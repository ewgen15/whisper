import os
import tempfile
from faster_whisper import WhisperModel

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        # int8 quantization — optimal for CPU inference
        _model = WhisperModel("small", device="cpu", compute_type="int8")
    return _model


def transcribe(audio_bytes: bytes, suffix: str = ".webm", language: str | None = None) -> str:
    model = get_model()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        segments, _ = model.transcribe(tmp_path, beam_size=5, language=language)
        return " ".join(seg.text.strip() for seg in segments)
    finally:
        os.unlink(tmp_path)
