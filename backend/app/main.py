import hmac
import mimetypes
import os
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

from .enrichment import enrich
from .transcription import transcribe
from .integrations import sync_entry, update_remote, delete_remote
from .ratelimit import RateLimiter
from .storage import save_entry, get_entries, get_entry, update_entry, delete_entry, set_entry_links

app = FastAPI(title="Diary Backend", version="0.1.0")

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


limiter = RateLimiter(limit=int(os.environ.get("RATE_LIMIT_PER_MIN", "60")), window=60)


def _client_key(request: Request) -> str:
    # Behind Tailscale Funnel the TCP peer is always the proxy; the last X-Forwarded-For
    # hop is the address the proxy saw (earlier hops are client-controlled).
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[-1].strip() or (request.client.host if request.client else "unknown")


class _RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path == "/health":
            return await call_next(request)
        allowed, retry_after = limiter.hit(_client_key(request))
        if not allowed:
            return JSONResponse({"detail": "Too many requests"}, status_code=429,
                                headers={"Retry-After": str(retry_after)})
        return await call_next(request)


class _ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        expected = os.environ.get("API_KEY", "")
        # No key configured = open (dev mode). Skip OPTIONS + /health.
        if expected and request.method != "OPTIONS" and request.url.path != "/health":
            given = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(given.encode(), expected.encode()):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)


# Last added = outermost. Order matters: CORS wraps the guards so 401/429 still carry
# CORS headers (otherwise the browser reports an opaque network error instead of the status),
# and the limiter sits before the key check so wrong-key guessing is throttled.
app.add_middleware(_ApiKeyMiddleware)
app.add_middleware(_RateLimitMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(_PNAMiddleware)

ALLOWED_AUDIO_TYPES = {
    "audio/webm", "audio/mp4", "audio/mpeg",
    "audio/ogg", "audio/wav", "audio/x-wav",
    "audio/m4a", "audio/x-m4a",
}


class EnrichRequest(BaseModel):
    text: str


class SaveRequest(BaseModel):
    entry: dict


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    content_type = file.content_type or ""
    # browsers often send audio/webm;codecs=opus — strip the codecs part
    base_type = content_type.split(";")[0].strip()

    if base_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=415, detail=f"Unsupported audio type: {content_type}")

    suffix = mimetypes.guess_extension(base_type) or ".audio"
    audio_bytes = await file.read()
    text = transcribe(audio_bytes, suffix=suffix)
    return {"text": text}


@app.post("/enrich")
async def enrich_text(req: EnrichRequest):
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="Text cannot be empty")
    result = enrich(req.text)
    return result


@app.post("/save")
async def save(req: SaveRequest):
    saved = save_entry(req.entry)
    sync, links = await run_in_threadpool(sync_entry, req.entry)
    if links and saved.get("id") is not None:
        await run_in_threadpool(set_entry_links, saved["id"], links)
    result = {**saved, **links}
    return {**result, "sync": sync} if sync else result


@app.get("/entries")
async def entries(limit: int = 50):
    return get_entries(limit=limit)


# Columns the client may edit; ids, links and timestamps are never writable.
EDITABLE_FIELDS = {
    "title", "text", "mood", "emotional_state", "physical_state",
    "context", "project", "tags", "entry_type",
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
    return {"deleted": True, "sync": sync}
