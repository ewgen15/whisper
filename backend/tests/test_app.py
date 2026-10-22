import pytest
from fastapi.testclient import TestClient

from app import integrations
from app.enrichment import _time_of_day
from app.main import app

client = TestClient(app)


def test_health_is_open_even_with_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    assert client.get("/health").status_code == 200


def test_api_key_required(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    assert client.get("/entries").status_code == 401
    assert client.get("/entries", headers={"X-API-Key": "wrong"}).status_code == 401


def test_preflight_passes_without_key_and_allows_private_network(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    r = client.options(
        "/entries",
        headers={
            "Origin": "https://ewgen15.github.io",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-api-key",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-private-network"] == "true"


def test_enrich_rejects_empty_text():
    assert client.post("/enrich", json={"text": "   "}).status_code == 422


@pytest.mark.parametrize("hour,expected", [
    (4, "Ніч"), (5, "Ранок"), (11, "Ранок"), (12, "День"),
    (16, "День"), (17, "Вечір"), (22, "Вечір"), (23, "Ніч"), (0, "Ніч"),
])
def test_time_of_day(hour, expected):
    assert _time_of_day(hour) == expected


def test_embedding_input_matches_n8n_layout():
    s = integrations._embedding_input({
        "title": "Тема", "tags": ["а", "б"], "entry_type": "Рефлексія", "time_of_day": "Ранок",
        "mood": ["сум"], "project": None, "context": "Дім", "physical_state": "Гарний",
        "emotional_state": "Поганий", "text": "Текст", "points": ["раз", "два"],
    })
    assert s == (
        "Назва теми: Тема. Теги: а, б. Тип запису: Рефлексія. Час доби: Ранок. Настрій: сум. "
        "Проєкт: не вказано. Контекст: Дім. Фізичний стан: Гарний. Емоційний стан: Поганий. "
        "Текст: Текст. Ключові пункти: • раз • два."
    )


def test_notion_payload(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "t")
    sent = {}

    class Resp:
        def raise_for_status(self):
            pass

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, json=json)
        return Resp()

    monkeypatch.setattr(integrations.httpx, "post", fake_post)
    integrations.save_notion({
        "title": "Тема", "text": "Абзац 1\nАбзац 2", "mood": ["а,б"], "tags": ["т"],
        "entry_type": "Рефлексія", "emotional_state": None, "people": ["Мар'ян"],
        "points": ["теза"],
    })

    props = sent["json"]["properties"]
    assert sent["headers"]["Authorization"] == "Bearer t"
    assert props["Настрій"]["multi_select"] == [{"name": "а б"}]  # commas stripped
    assert props["Тип запису"] == {"select": {"name": "Рефлексія"}}
    assert "Емоційний стан" not in props  # empty select omitted
    assert props["Ім'я контакта"]["rich_text"][0]["text"]["content"] == "Мар'ян"
    types = [b["type"] for b in sent["json"]["children"]]
    assert types == ["paragraph", "paragraph", "heading_3", "bulleted_list_item"]


def test_sync_entry_isolates_failures(monkeypatch):
    monkeypatch.setenv("VECTOR_ENABLED", "true")
    monkeypatch.setenv("NOTION_ENABLED", "true")
    calls = []

    def boom(entry):
        raise RuntimeError("down")

    monkeypatch.setattr(integrations, "save_vector", boom)
    monkeypatch.setattr(integrations, "save_notion", lambda e: calls.append("notion"))
    status = integrations.sync_entry({"title": "x"})
    assert status == {"vector": "error: RuntimeError", "notion": "ok"}
    assert calls == ["notion"]


def test_sync_entry_noop_when_flags_off(monkeypatch):
    monkeypatch.delenv("VECTOR_ENABLED", raising=False)
    monkeypatch.delenv("NOTION_ENABLED", raising=False)
    assert integrations.sync_entry({}) == {}


# --- vector write (documents -> embeddings) -------------------------------

class _FakeTable:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def insert(self, row):
        self.store.append((self.name, row))
        self._row = row
        return self

    def execute(self):
        class R:
            data = [{"id": "doc-1"}]
        return R()


class _FakeSupabase:
    def __init__(self):
        self.inserts = []

    def table(self, name):
        return _FakeTable(self.inserts, name)


class _FakeOpenAI:
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


def test_save_vector_writes_document_then_linked_embedding(monkeypatch):
    sb, oa = _FakeSupabase(), _FakeOpenAI()
    monkeypatch.setattr(integrations, "get_client", lambda: sb)
    monkeypatch.setattr(integrations, "_openai", oa)

    integrations.save_vector({
        "title": "Тема", "text": "Текст запису", "tags": ["т"], "mood": ["м"],
        "entry_type": "Подія", "points": ["а", "б"],
    })

    (t1, doc), (t2, emb) = sb.inserts
    assert (t1, t2) == ("documents", "embeddings")
    assert doc["topic"] == "Тема" and doc["points"] == "• а • б"
    assert emb["document_id"] == "doc-1"
    assert emb["content"] == "Текст запису"  # content stays plain text, like n8n
    assert emb["embedding"] == [0.1, 0.2, 0.3]
    assert emb["metadata"] == {"topic": "Тема", "tags": ["т"], "type": "Подія", "mood": ["м"]}
    model, sent = oa.calls[0]
    assert model == "text-embedding-ada-002"
    assert sent.startswith("Назва теми: Тема.") and "Текст: Текст запису." in sent


def test_save_vector_skips_empty_text(monkeypatch):
    sb = _FakeSupabase()
    monkeypatch.setattr(integrations, "get_client", lambda: sb)
    integrations.save_vector({"title": "x", "text": "  "})
    assert sb.inserts == []


# --- /save endpoint --------------------------------------------------------

def test_save_returns_sync_status(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    import app.main as main
    monkeypatch.setattr(main, "save_entry", lambda e: {"id": 1, "title": e["title"]})
    monkeypatch.setattr(main, "sync_entry", lambda e: {"vector": "ok", "notion": "error: HTTPStatusError"})
    r = client.post("/save", json={"entry": {"title": "T"}})
    assert r.status_code == 200
    assert r.json() == {"id": 1, "title": "T", "sync": {"vector": "ok", "notion": "error: HTTPStatusError"}}


def test_save_has_no_sync_field_when_flags_off(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    import app.main as main
    monkeypatch.setattr(main, "save_entry", lambda e: {"id": 1})
    monkeypatch.setattr(main, "sync_entry", lambda e: {})
    assert client.post("/save", json={"entry": {}}).json() == {"id": 1}


# --- enrich post-processing ------------------------------------------------

def test_enrich_overrides_time_of_day_and_defaults_mood(monkeypatch):
    from app import enrichment

    class Msg:
        content = '{"title": "T", "time_of_day": "Ніч", "mood": null}'

    class Choice:
        message = Msg()

    class Resp:
        choices = [Choice()]

    class Fake:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    assert "<transcript>" in kwargs["messages"][1]["content"]
                    return Resp()

    class FakeDT:
        @staticmethod
        def now(tz):
            import datetime
            return datetime.datetime(2026, 10, 19, 7, 40, tzinfo=tz)

    monkeypatch.setattr(enrichment, "client", Fake)
    monkeypatch.setattr(enrichment, "datetime", FakeDT)
    out = enrichment.enrich("щось")
    assert out["time_of_day"] == "Ранок"  # code decides, not the model
    assert out["mood"] == []
