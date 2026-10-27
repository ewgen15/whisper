import hmac
import mimetypes
import os
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

from .audio import MAX_TOTAL_BYTES, AudioError, delete_audio, resolve, store_audio
from .enrichment import EDIT_LEVELS, PROVIDERS, MissingKeyError, enrich
from .transcription import transcribe
from .integrations import sync_entry, update_remote, delete_remote
from .ratelimit import RateLimiter
from .scales import SCALES, apply_scales, clean_scale, derive_state
from .stats import build_stats
from .storage import (
    delete_entry, get_entries, get_entry, get_stats_rows, save_entry, set_entry_links, update_entry,
)

app = FastAPI(title="Whisper Backend", version="0.1.0")

# Pure ASGI middleware — intercepts http.response.start at the wire level,
# so it sees OPTIONS preflight responses that CORSMiddleware emits directly
# (BaseHTTPMiddleware misses those).
# Chrome requires Access-Control-Allow-Private-Network: true to allow
# requests from public origins (github.io) to private IPs (Tailscale 100.x).
class _PNAMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_pna(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"access-control-allow-private-network", b"true"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_pna)


limiter = RateLimiter(limit=int(os.environ.get("RATE_LIMIT_PER_MIN", "120")), window=60)
# Wrong-key attempts are counted separately: behind the Funnel every caller may look like the
# same client, so a flood of bad requests must never use up the owner's budget.
fail_limiter = RateLimiter(limit=int(os.environ.get("FAILED_AUTH_PER_MIN", "10")), window=60)


def _client_key(request: Request) -> str:
    # Last X-Forwarded-For hop = the address the proxy saw (earlier hops are client-controlled).
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[-1].strip() or (request.client.host if request.client else "unknown")


def _too_many(retry_after: int) -> JSONResponse:
    return JSONResponse({"detail": "Too many requests"}, status_code=429,
                        headers={"Retry-After": str(retry_after)})


class _GuardMiddleware(BaseHTTPMiddleware):
    """API key check + rate limiting. OPTIONS and /health are never guarded."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path == "/health":
            return await call_next(request)
        client = _client_key(request)
        expected = os.environ.get("API_KEY", "")
        # No key configured = open (dev mode).
        if expected:
            given = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(given.encode(), expected.encode()):
                allowed, retry = fail_limiter.hit(client)
                return JSONResponse({"detail": "Unauthorized"}, status_code=401) if allowed else _too_many(retry)
        allowed, retry = limiter.hit("owner" if expected else client)
        if not allowed:
            return _too_many(retry)
        return await call_next(request)


# Last added = outermost. CORS wraps the guard so 401/429 still carry CORS headers
# (otherwise the browser reports an opaque network error instead of the status).
app.add_middleware(_GuardMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(_PNAMiddleware)

ALLOWED_AUDIO_TYPES = {
    "audio/webm", "audio/mp4", "audio/mpeg",
    "audio/ogg", "audio/wav", "audio/x-wav",
    "audio/m4a", "audio/x-m4a",
}


class EnrichRequest(BaseModel):
    text: str
    edit_level: str = "full"
    provider: str = "openai"


class SaveRequest(BaseModel):
    entry: dict


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...), language: str = Form("auto")):
    content_type = file.content_type or ""
    # browsers often send audio/webm;codecs=opus — strip the codecs part
    base_type = content_type.split(";")[0].strip()

    if base_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=415, detail=f"Unsupported audio type: {content_type}")

    lang = language.strip().lower()
    if lang in ("", "auto"):
        lang = None
    elif lang not in ("uk", "en"):
        raise HTTPException(status_code=422, detail="language must be auto, uk or en")

    suffix = mimetypes.guess_extension(base_type) or ".audio"
    audio_bytes = await file.read()
    text = transcribe(audio_bytes, suffix=suffix, language=lang)
    return {"text": text}


@app.post("/enrich")
async def enrich_text(req: EnrichRequest, request: Request):
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="Text cannot be empty")
    if req.edit_level not in EDIT_LEVELS:
        raise HTTPException(status_code=422, detail=f"edit_level must be one of {sorted(EDIT_LEVELS)}")
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail=f"provider must be one of {sorted(PROVIDERS)}")
    # A key from the app settings overrides the server's own; empty means "use the .env key".
    api_key = request.headers.get(f"x-{req.provider}-key", "").strip() or None
    try:
        return await run_in_threadpool(enrich, req.text, req.edit_level, req.provider, api_key)
    except MissingKeyError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        # Never echo the exception text: provider errors can contain parts of the key.
        raise HTTPException(status_code=502, detail=f"{req.provider}: {type(e).__name__}")


@app.post("/audio")
async def upload_audio(files: list[UploadFile] = File(...)):
    """Segments of one recording in order → one stored Opus file; the client puts the path into /save."""
    parts, total = [], 0
    for f in files:
        base_type = (f.content_type or "").split(";")[0].strip()
        if base_type not in ALLOWED_AUDIO_TYPES:
            raise HTTPException(status_code=415, detail=f"Unsupported audio type: {f.content_type}")
        data = await f.read()
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="Audio too large")
        if data:
            parts.append((data, mimetypes.guess_extension(base_type) or ".audio"))
    if not parts:
        raise HTTPException(status_code=422, detail="Audio is empty")
    try:
        return {"audio_path": await run_in_threadpool(store_audio, parts)}
    except AudioError:
        raise HTTPException(status_code=422, detail="Could not process audio")


@app.get("/entries/{entry_id}/audio")
async def entry_audio(entry_id: int):
    row = await run_in_threadpool(get_entry, entry_id)
    f = resolve(row.get("audio_path")) if row and row.get("audio_path") else None
    if f is None or not f.is_file():
        raise HTTPException(status_code=404, detail="No audio")
    return FileResponse(f, media_type="audio/ogg")


@app.post("/save")
async def save(req: SaveRequest):
    entry = apply_scales(req.entry)  # validates the scales and derives emotional_state from them
    saved = save_entry(entry)
    sync, links = await run_in_threadpool(sync_entry, entry)
    if links and saved.get("id") is not None:
        await run_in_threadpool(set_entry_links, saved["id"], links)
    result = {**saved, **links}
    return {**result, "sync": sync} if sync else result


@app.get("/entries")
async def entries(limit: int = 50):
    return get_entries(limit=limit)


@app.get("/stats")
async def stats():
    return build_stats(await run_in_threadpool(get_stats_rows))


# Columns the client may edit; ids, links and timestamps are never writable.
EDITABLE_FIELDS = {
    "title", "text", "mood", "emotional_state", "physical_state",
    "context", "project", "tags", "entry_type", *SCALES,
}


@app.patch("/entries/{entry_id}")
async def patch_entry(entry_id: int, changes: dict):
    if await run_in_threadpool(get_entry, entry_id) is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    changes = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
    if not changes:
        raise HTTPException(status_code=422, detail="No editable fields")
    for key in ("title", "text"):
        if key in changes and not str(changes[key] or "").strip():
            raise HTTPException(status_code=422, detail=f"{key} cannot be empty")
    if any(k in changes for k in SCALES):
        # The label always follows the numbers: merge with the stored scales, validate, re-derive.
        current = await run_in_threadpool(get_entry, entry_id)
        # user input is strict (no rounding of 1.5); stored values were already validated
        merged = {k: (clean_scale(changes[k]) if isinstance(changes[k], int) else None) if k in changes
                  else clean_scale(current.get(k)) for k in SCALES}
        bad = [k for k in SCALES if k in changes and merged[k] is None]
        if bad:
            raise HTTPException(status_code=422, detail=f"{', '.join(bad)} must be an integer from -2 to 2")
        changes.update({k: merged[k] for k in SCALES if k in changes})
        state = derive_state(*(merged[k] for k in SCALES))
        if state is not None:
            changes["emotional_state"] = state
    row = await run_in_threadpool(update_entry, entry_id, changes)
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    sync = await run_in_threadpool(update_remote, row)
    return {**row, "sync": sync} if sync else row


@app.delete("/entries/{entry_id}")
async def remove_entry(entry_id: int):
    row = await run_in_threadpool(get_entry, entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    # Remote first, then local: a failed remote delete is reported but must not trap the user
    # with an undeletable entry.
    sync = await run_in_threadpool(delete_remote, row)
    await run_in_threadpool(delete_entry, entry_id)
    await run_in_threadpool(delete_audio, row.get("audio_path"))
    return {"deleted": True, "sync": sync}
