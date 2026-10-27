import os
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

AUDIO_DIR = Path(os.environ.get("AUDIO_DIR", "/data/audio"))
MAX_TOTAL_BYTES = 100 * 1024 * 1024
# Relative path as stored in entries.audio_path; anything else is rejected before it can reach the disk.
AUDIO_PATH_RE = re.compile(r"^\d{4}/\d{2}/[0-9a-f]{32}\.opus$")


class AudioError(Exception):
    pass


def valid_audio_path(path) -> bool:
    return isinstance(path, str) and bool(AUDIO_PATH_RE.match(path))


def resolve(path: str) -> Path | None:
    """Absolute file for a stored audio_path, or None if it is malformed."""
    return AUDIO_DIR / path if valid_audio_path(path) else None


def store_audio(parts: list[tuple[bytes, str]]) -> str:
    """Join recorded segments into one Opus file under AUDIO_DIR; returns its relative path.

    parts: [(bytes, suffix)] in recording order. Segments may come from different recorders
    (a pause starts a new one), so they are decoded and concatenated, not byte-appended.
    """
    if not parts:
        raise AudioError("no audio")
    now = datetime.now(timezone.utc)
    rel = f"{now:%Y}/{now:%m}/{uuid.uuid4().hex}.opus"
    out = AUDIO_DIR / rel
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for i, (data, suffix) in enumerate(parts):
            src = Path(tmp) / f"in{i}{suffix}"
            src.write_bytes(data)
            cmd += ["-i", str(src)]
        norm = "".join(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=mono[a{i}];" for i in range(len(parts)))
        joined = "".join(f"[a{i}]" for i in range(len(parts)))
        cmd += ["-filter_complex", f"{norm}{joined}concat=n={len(parts)}:v=0:a=1[out]",
                "-map", "[out]", "-c:a", "libopus", "-b:a", "32k", str(out)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            out.unlink(missing_ok=True)
            raise AudioError(type(e).__name__) from e
    return rel


def delete_audio(path) -> None:
    f = resolve(path) if path else None
    if f is not None:
        f.unlink(missing_ok=True)
