import os

import pytest

# Modules read these at import time; real calls are always mocked in tests.
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_KEY", "test-key")


class FakeSupabase:
    """Records every query. `responses[(table, op)]` overrides the returned rows."""

    def __init__(self):
        self.log = []          # (table, op, payload, filters)
        self.responses = {}

    def table(self, name):
        db = self

        class Query:
            def __init__(self):
                self.op, self.payload, self.filters = None, None, []

            def insert(self, row):
                self.op, self.payload = "insert", row
                return self

            def update(self, vals):
                self.op, self.payload = "update", vals
                return self

            def delete(self):
                self.op = "delete"
                return self

            def select(self, cols="*"):
                self.op, self.payload = "select", cols
                return self

            def order(self, *a, **kw):
                return self

            def range(self, *a):
                return self

            def eq(self, col, val):
                self.filters.append((col, val))
                return self

            def execute(self):
                db.log.append((name, self.op, self.payload, self.filters))

                class R:
                    data = db.responses.get((name, self.op), [{"id": "doc-1"}])
                return R()

        return Query()

    def ops(self, table=None, op=None):
        return [e for e in self.log if (table is None or e[0] == table) and (op is None or e[1] == op)]


class FakeOpenAI:
    def __init__(self):
        self.calls = []
        outer = self

        class Embeddings:
            def create(self, model, input):
                outer.calls.append((model, input))

                class D:
                    embedding = [0.1, 0.2, 0.3]

                class R:
                    data = [D()]
                return R()

        self.embeddings = Embeddings()


class FakeNotion:
    """Replaces httpx.request. `children` is what GET /blocks/<id>/children returns."""

    def __init__(self):
        self.calls = []        # (method, url, json)
        self.children = []
        self.fail_on = None    # substring of url that should raise

    def __call__(self, method, url, headers=None, json=None, timeout=None, params=None):
        self.calls.append((method, url, json))
        assert headers["Authorization"].startswith("Bearer ")
        outer = self

        class Resp:
            def raise_for_status(self):
                if outer.fail_on and outer.fail_on in url:
                    raise RuntimeError("notion down")

            def json(self):
                if method == "GET" and url.endswith("/children"):
                    return {"results": outer.children, "has_more": False}
                return {"id": "page-1", "url": "https://notion.so/page-1"}
        return Resp()

    def methods_urls(self):
        return [(m, u.replace("https://api.notion.com/v1", "")) for m, u, _ in self.calls]


@pytest.fixture
def sb(monkeypatch):
    from app import integrations, storage
    fake = FakeSupabase()
    monkeypatch.setattr(integrations, "get_client", lambda: fake)
    monkeypatch.setattr(storage, "get_client", lambda: fake)
    return fake


@pytest.fixture
def oai(monkeypatch):
    from app import integrations
    fake = FakeOpenAI()
    monkeypatch.setattr(integrations, "_openai", fake)
    return fake


@pytest.fixture
def notion(monkeypatch):
    from app import integrations
    monkeypatch.setenv("NOTION_TOKEN", "t")
    fake = FakeNotion()
    monkeypatch.setattr(integrations.httpx, "request", fake)
    return fake
