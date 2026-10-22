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
from .integrations import sync_entry
from .storage import save_entry, get_entries

app = FastAPI(title="Diary Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

app.add_middleware(_PNAMiddleware)


class _ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        expected = os.environ.get("API_KEY", "")
        # No key configured = open (dev mode). Skip OPTIONS + /health.
        if expected and request.method != "OPTIONS" and request.url.path != "/health":
            if request.headers.get("x-api-key") != expected:
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

app.add_middleware(_ApiKeyMiddleware)

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
    sync = await run_in_threadpool(sync_entry, req.entry)
    return {**saved, "sync": sync} if sync else saved


@app.get("/entries")
async def entries(limit: int = 50):
    return get_entries(limit=limit)
