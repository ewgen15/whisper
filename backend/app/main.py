import mimetypes
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .enrichment import enrich
from .transcription import transcribe
from .storage import save_entry, get_entries

app = FastAPI(title="Diary Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    return saved


@app.get("/entries")
async def entries(limit: int = 50):
    return get_entries(limit=limit)
