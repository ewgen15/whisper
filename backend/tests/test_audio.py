import subprocess

import pytest
from fastapi.testclient import TestClient

import app.audio as audio
import app.main as main

client = TestClient(main.app)

GOOD = "2026/10/" + "a" * 32 + ".opus"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(audio, "AUDIO_DIR", tmp_path)
    main.limiter.reset()
    main.fail_limiter.reset()


def _fake_ffmpeg(monkeypatch, calls=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        open(cmd[-1], "wb").write(b"opus")
    monkeypatch.setattr(audio.subprocess, "run", run)


def test_valid_audio_path_rejects_traversal_and_junk():
    assert audio.valid_audio_path(GOOD)
    for bad in ("../etc/passwd", "2026/10/../../x.opus", "2026/10/abc.opus", "/abs.opus", None, 5, GOOD + "x"):
        assert not audio.valid_audio_path(bad)


def test_store_audio_concatenates_segments_into_one_file(monkeypatch, tmp_path):
    calls = []
    _fake_ffmpeg(monkeypatch, calls)
    rel = audio.store_audio([(b"1", ".webm"), (b"2", ".mp4")])
    assert audio.valid_audio_path(rel) and (tmp_path / rel).read_bytes() == b"opus"
    cmd = calls[0]
    assert cmd.count("-i") == 2 and "concat=n=2:v=0:a=1[out]" in " ".join(cmd) and "libopus" in cmd


def test_store_audio_cleans_up_on_ffmpeg_failure(monkeypatch, tmp_path):
    def boom(cmd, **kw):
        open(cmd[-1], "wb").write(b"partial")
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(audio.subprocess, "run", boom)
    with pytest.raises(audio.AudioError):
        audio.store_audio([(b"1", ".webm")])
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


def test_upload_audio_returns_path(monkeypatch):
    _fake_ffmpeg(monkeypatch)
    files = [("files", ("a.webm", b"aa", "audio/webm;codecs=opus")), ("files", ("b.webm", b"bb", "audio/webm"))]
    r = client.post("/audio", files=files)
    assert r.status_code == 200 and audio.valid_audio_path(r.json()["audio_path"])


def test_upload_audio_rejects_wrong_type_and_empty():
    assert client.post("/audio", files=[("files", ("a.txt", b"x", "text/plain"))]).status_code == 415
    assert client.post("/audio", files=[("files", ("a.webm", b"", "audio/webm"))]).status_code == 422


def test_upload_audio_requires_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    assert client.post("/audio", files=[("files", ("a.webm", b"x", "audio/webm"))]).status_code == 401


def test_entry_audio_serves_file_and_404s(monkeypatch, tmp_path):
    (tmp_path / "2026" / "10").mkdir(parents=True)
    (tmp_path / GOOD).write_bytes(b"opus")
    monkeypatch.setattr(main, "get_entry", lambda i: {"id": i, "audio_path": GOOD} if i == 1 else {"id": i})
    r = client.get("/entries/1/audio")
    assert r.status_code == 200 and r.content == b"opus"
    assert client.get("/entries/2/audio").status_code == 404


def test_entry_audio_ignores_malformed_stored_path(monkeypatch):
    monkeypatch.setattr(main, "get_entry", lambda i: {"id": i, "audio_path": "../../etc/passwd"})
    assert client.get("/entries/1/audio").status_code == 404


def test_save_entry_keeps_only_valid_audio_path(sb):
    from app.storage import save_entry
    save_entry({"title": "t", "audio_path": GOOD})
    save_entry({"title": "t", "audio_path": "../../x"})
    rows = [p for _, op, p, _ in sb.log if op == "insert"]
    assert rows[0]["audio_path"] == GOOD and rows[1]["audio_path"] is None


def test_delete_entry_removes_audio_file(monkeypatch, tmp_path):
    (tmp_path / "2026" / "10").mkdir(parents=True)
    f = tmp_path / GOOD
    f.write_bytes(b"opus")
    monkeypatch.setattr(main, "get_entry", lambda i: {"id": i, "audio_path": GOOD})
    monkeypatch.setattr(main, "delete_remote", lambda row: {})
    monkeypatch.setattr(main, "delete_entry", lambda i: None)
    assert client.delete("/entries/1").status_code == 200
    assert not f.exists()
