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
