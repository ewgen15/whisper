import functools
import http.server
import io
import json
import re
import threading
import wave
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"
BACKEND = "https://backend.test"

ENTRIES = [
    {
        "id": 1,
        "title": "Ранкова прогулянка",
        "text": "Погуляв у парку, голова прояснилась.",
        "mood": ["Спокійний"],
        "tags": ["прогулянка"],
        "created_at": "2026-10-20T07:30:00Z",
    },
    {
        "id": 2,
        "title": "Робочий день",
        "text": "Багато зустрічей, але встиг закрити задачу.",
        "mood": ["Втомлений"],
        "tags": ["робота"],
        "created_at": "2026-10-21T16:00:00Z",
    },
    {
        "id": 3,
        "title": "Розмова з адвокатом",
        "text": "Суддя пішов на лікарняний.\n\nЗасідання перенесли на два тижні.",
        "mood": ["тривожний"],
        "tags": ["суд"],
        "entry_type": "Подія",
        "time_of_day": "День",
        "physical_state": "Нейтральний",
        "context": "Дзвінок адвоката",
        "project": "Квартира",
        "duration": "0:03",
        "emotional_state": "Гарний",
        "valence": 1, "energy": 0, "control": 1,
        "pred_valence": 0, "pred_energy": 0, "pred_control": 1,
        "audio_path": "2026/10/" + "a" * 32 + ".opus",
        "created_at": "2026-10-22T09:00:00Z",
    },
]


def silent_wav(seconds=3):
    """A valid, quiet WAV: the stand-in for the saved recording the app downloads and plays."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(8000)
        w.writeframes(b"\x80" * 8000 * seconds)
    return buf.getvalue()

ENRICHED = {
    "title": "Відновлена думка",
    "text": "Привіт, світе.",
    "mood": ["Спокійний"],
    "valence": 1,
    "energy": 0,
    "control": 1,
    "emotional_state": "Гарний",
    "physical_state": "Бадьорий",
    "entry_type": "Роздум",
    "time_of_day": "Вечір",
    "tags": ["тест"],
}


@pytest.fixture(scope="session")
def site():
    """Serves docs/ the way GitHub Pages does."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DOCS))
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def browser_context_args(browser_context_args, playwright):
    # iPhone viewport; no service worker so each test sees fresh code
    return {**playwright.devices["iPhone 14"], "service_workers": "block"}


@pytest.fixture
def open_app(page, site):
    """Opens the app with a mocked backend. `entries=None` makes /entries fail."""

    def _open(entries=ENTRIES, transcribe=None, init_script=None, enriched=None, audio_status=200):
        """`transcribe`: optional callable(route) replacing the default successful /transcribe + /enrich.
        `enriched`: replaces the default /enrich response. Bodies POSTed to /save are collected in `page.saves`."""
        page.saves = []
        page.patches = []          # bodies PATCHed to /entries/<id>
        page.audio_requests = []   # headers of every /entries/<id>/audio request

        def handle(route):
            url = route.request.url
            if route.request.method == "OPTIONS":
                return route.fulfill(status=204, headers={"access-control-allow-origin": "*"})
            cors = {"access-control-allow-origin": "*"}
            if url.endswith("/entries") and entries is not None:
                return route.fulfill(json=entries, headers=cors)
            if url.endswith("/transcribe"):
                return transcribe(route) if transcribe else route.fulfill(json={"text": "Привіт"}, headers=cors)
            if url.endswith("/enrich"):
                return route.fulfill(json=ENRICHED if enriched is None else enriched, headers=cors)
            if url.endswith("/save"):
                entry = json.loads(route.request.post_data)["entry"]
                page.saves.append(entry)
                return route.fulfill(json={**entry, "id": 99, "created_at": "2026-10-26T18:00:00Z"}, headers=cors)
            m = re.search(r"/entries/(\d+)(/audio)?$", url)
            if m and m.group(2):
                page.audio_requests.append(route.request.headers)
                if audio_status != 200:
                    return route.fulfill(status=audio_status, body="no", headers=cors)
                return route.fulfill(body=silent_wav(), headers={**cors, "content-type": "audio/wav"})
            if m and route.request.method == "PATCH":
                body = json.loads(route.request.post_data)
                page.patches.append(body)
                row = next(e for e in ENTRIES if e["id"] == int(m.group(1)))
                return route.fulfill(json={**row, **body, "emotional_state": "Пригнічений"}, headers=cors)  # the server derives the label
            if url.endswith("/stats"):
                return route.fulfill(json={"days": []}, headers=cors)
            return route.fulfill(status=503, body="down", headers=cors)

        page.route(f"{BACKEND}/**", handle)
        if init_script:
            page.add_init_script(init_script)
        page.add_init_script(
            f"localStorage.setItem('diary_backend', {json.dumps(BACKEND)});"
            "localStorage.setItem('diary_api_key', 'test-key');"
        )
        page.goto(site + "/index.html")
        return page

    return _open
