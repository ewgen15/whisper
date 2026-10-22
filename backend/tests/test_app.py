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


# ── language and edit level ─────────────────────────────────────────────

def _wav():
    return {"file": ("r.webm", b"x", "audio/webm")}


@pytest.mark.parametrize("sent,expected", [("uk", "uk"), ("en", "en"), ("auto", None), ("", None)])
def test_transcribe_passes_language(monkeypatch, sent, expected):
    seen = {}

    def fake(audio, suffix=".webm", language=None):
        seen["language"] = language
        return "текст"

    monkeypatch.setattr("app.main.transcribe", fake)
    r = client.post("/transcribe", files=_wav(), data={"language": sent})
    assert r.status_code == 200
    assert seen["language"] == expected


def test_transcribe_language_optional_and_validated(monkeypatch):
    monkeypatch.setattr("app.main.transcribe", lambda a, suffix=".webm", language=None: "ok")
    assert client.post("/transcribe", files=_wav()).status_code == 200
    assert client.post("/transcribe", files=_wav(), data={"language": "xx"}).status_code == 422


def test_enrich_rejects_unknown_edit_level():
    r = client.post("/enrich", json={"text": "привіт", "edit_level": "wild"})
    assert r.status_code == 422


def _fake_gpt(content):
    class Msg:
        pass

    class Choice:
        message = Msg()

    Choice.message.content = content

    class Resp:
        choices = [Choice()]

    seen = {}

    class Completions:
        def create(self, **kw):
            seen.update(kw)
            return Resp()

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()

    return Client(), seen


def test_edit_level_none_keeps_transcript_verbatim(monkeypatch):
    from app import enrichment

    fake, _ = _fake_gpt('{"title":"t","text":"редаговано","mood":[]}')
    monkeypatch.setattr(enrichment, "client", fake)
    out = enrichment.enrich("  як є,  ну  типу  ", edit_level="none")
    assert out["text"] == "як є,  ну  типу"
    assert out["title"] == "t"


def test_edit_level_full_uses_model_text_and_tells_it_the_level(monkeypatch):
    from app import enrichment

    fake, seen = _fake_gpt('{"title":"t","text":"чисто","mood":["спокійний"]}')
    monkeypatch.setattr(enrichment, "client", fake)
    out = enrichment.enrich("ну чисто", edit_level="light")
    assert out["text"] == "чисто"
    user = seen["messages"][1]["content"]
    assert "<edit_level>" in user and "light" in user
