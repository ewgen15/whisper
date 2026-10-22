from fastapi.testclient import TestClient

import app.main as main
from app.ratelimit import RateLimiter


def test_allows_up_to_limit_then_blocks():
    t = [0.0]
    lim = RateLimiter(limit=3, window=60, clock=lambda: t[0])
    assert [lim.hit("a")[0] for _ in range(4)] == [True, True, True, False]


def test_keys_are_independent_and_window_slides():
    t = [0.0]
    lim = RateLimiter(limit=1, window=60, clock=lambda: t[0])
    assert lim.hit("a")[0] and lim.hit("b")[0]
    assert not lim.hit("a")[0]
    t[0] = 61
    assert lim.hit("a")[0]


def test_retry_after_is_positive_when_blocked():
    t = [0.0]
    lim = RateLimiter(limit=1, window=60, clock=lambda: t[0])
    lim.hit("a")
    t[0] = 10
    ok, retry = lim.hit("a")
    assert not ok and 49 <= retry <= 50


def test_memory_is_bounded_by_pruning_idle_keys():
    t = [0.0]
    lim = RateLimiter(limit=1, window=60, clock=lambda: t[0])
    for i in range(50):
        lim.hit(f"k{i}")
    t[0] = 1000
    lim.hit("fresh")
    assert len(lim._hits) == 1


# --- wired into the app -------------------------------------------------------

def _client(monkeypatch, limit):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(main, "limiter", RateLimiter(limit=limit, window=60))
    return TestClient(main.app)


def test_429_after_limit_with_retry_after_and_cors(monkeypatch):
    c = _client(monkeypatch, 2)
    h = {"Origin": "https://ewgen15.github.io"}
    assert c.post("/enrich", json={"text": ""}, headers=h).status_code == 422
    assert c.post("/enrich", json={"text": ""}, headers=h).status_code == 422
    r = c.post("/enrich", json={"text": ""}, headers=h)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert r.headers["access-control-allow-origin"] == "*"  # browser can read the error


def test_health_and_preflight_are_never_limited(monkeypatch):
    c = _client(monkeypatch, 1)
    for _ in range(5):
        assert c.get("/health").status_code == 200
        assert c.options("/save", headers={"Origin": "https://x", "Access-Control-Request-Method": "POST"}).status_code == 200


def test_wrong_keys_are_limited_too_and_401_carries_cors(monkeypatch):
    c = _client(monkeypatch, 2)
    monkeypatch.setenv("API_KEY", "secret")
    h = {"Origin": "https://x", "X-API-Key": "wrong"}
    r = c.get("/entries", headers=h)
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == "*"
    c.get("/entries", headers=h)
    assert c.get("/entries", headers=h).status_code == 429  # key guessing is throttled
