import functools
import http.server
import json
import threading
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
]


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

    def _open(entries=ENTRIES, transcribe=None):
        """`transcribe`: optional callable(route) replacing the default successful /transcribe + /enrich."""

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
                return route.fulfill(json={"title": "Відновлена думка", "text": "Привіт", "mood": [], "tags": []}, headers=cors)
            if url.endswith("/stats"):
                return route.fulfill(json={"days": []}, headers=cors)
            return route.fulfill(status=503, body="down", headers=cors)

        page.route(f"{BACKEND}/**", handle)
        page.add_init_script(
            f"localStorage.setItem('diary_backend', {json.dumps(BACKEND)});"
            "localStorage.setItem('diary_api_key', 'test-key');"
        )
        page.goto(site + "/index.html")
        return page

    return _open
