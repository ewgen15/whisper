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


def _fake_gpt(*contents):
    """Each create() call returns the next content; `seen` holds the last call, `calls` all of them."""
    seen = {}
    calls = []
    queue = list(contents)

    class Completions:
        def create(self, **kw):
            seen.clear()
            seen.update(kw)
            calls.append(kw)
            seen["calls"] = calls

            class Msg:
                content = queue.pop(0) if len(queue) > 1 else queue[0]

            class Choice:
                message = Msg()

            class Resp:
                choices = [Choice()]

            return Resp()

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()

    seen["calls"] = calls
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


def test_full_level_corrects_transcript_first_with_stronger_model(monkeypatch):
    from app import enrichment

    fake, seen = _fake_gpt("нова модель їсть токени", '{"title":"t","text":"нова модель їсть токени","mood":[]}')
    monkeypatch.setattr(enrichment, "client", fake)
    out = enrichment.enrich("нова мандель їздить токенів", edit_level="full")
    first, second = seen["calls"]
    assert first["model"] != "gpt-4o-mini"
    assert "мандель" in first["messages"][1]["content"]
    assert "нова модель їсть токени" in second["messages"][1]["content"]
    assert out["text"] == "нова модель їсть токени"


def test_correction_failure_falls_back_to_raw_text(monkeypatch):
    from app import enrichment

    fake, seen = _fake_gpt('{"title":"t","text":"x","mood":[]}')

    def boom(**kw):
        if kw["model"] == enrichment.CORRECTION_MODEL:
            raise RuntimeError("openai down")
        return orig(**kw)

    orig = fake.chat.completions.create
    fake.chat.completions.create = boom
    monkeypatch.setattr(enrichment, "client", fake)
    enrichment.enrich("сирий текст", edit_level="full")
    assert "сирий текст" in seen["messages"][1]["content"]


def test_light_and_none_skip_the_correction_step(monkeypatch):
    from app import enrichment

    for level in ("light", "none"):
        fake, seen = _fake_gpt('{"title":"t","text":"x","mood":[]}')
        monkeypatch.setattr(enrichment, "client", fake)
        enrichment.enrich("текст", edit_level=level)
        assert len(seen["calls"]) == 1


def _fake_claude(monkeypatch, *texts):
    """Replaces anthropic.Anthropic; returns the list of (api_key, create kwargs) it saw."""
    import anthropic

    calls = []
    queue = list(texts)

    class Block:
        type = "text"

        def __init__(self, text):
            self.text = text

    class Messages:
        def __init__(self, key):
            self.key = key

        def create(self, **kw):
            calls.append((self.key, kw))

            class Resp:
                content = [Block(queue.pop(0) if len(queue) > 1 else queue[0])]
            return Resp()

    class FakeAnthropic:
        def __init__(self, api_key):
            self.messages = Messages(api_key)

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    return calls


def test_anthropic_provider_uses_claude_models_and_parses_fenced_json(monkeypatch):
    from app import enrichment

    calls = _fake_claude(monkeypatch, "нова модель їсть токени", '```json\n{"title":"t","text":"x","mood":["спокійний"]}\n```')
    out = enrichment.enrich("нова мандель їздить токенів", edit_level="full", provider="anthropic", api_key="sk-ant-x")
    (key1, first), (key2, second) = calls
    assert key1 == key2 == "sk-ant-x"
    assert first["model"] == enrichment.PROVIDERS["anthropic"]["correct"]
    assert second["model"] == enrichment.PROVIDERS["anthropic"]["extract"]
    assert first["system"] and second["messages"][0]["role"] == "user"
    assert out["title"] == "t" and out["mood"] == ["спокійний"]


def test_anthropic_without_any_key_is_a_422(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/enrich", json={"text": "привіт", "provider": "anthropic"})
    assert r.status_code == 422 and "Anthropic" in r.json()["detail"]


def test_enrich_passes_key_header_to_the_chosen_provider(monkeypatch):
    calls = _fake_claude(monkeypatch, '{"title":"t","text":"x","mood":[]}')
    r = client.post("/enrich", json={"text": "привіт", "provider": "anthropic", "edit_level": "light"},
                    headers={"X-Anthropic-Key": "sk-ant-from-app"})
    assert r.status_code == 200
    assert calls[0][0] == "sk-ant-from-app"


def test_enrich_rejects_unknown_provider():
    r = client.post("/enrich", json={"text": "привіт", "provider": "gemini"})
    assert r.status_code == 422


def test_provider_failure_is_502_without_leaking_the_key(monkeypatch):
    import anthropic

    class Boom:
        def __init__(self, api_key):
            raise RuntimeError(f"bad key {api_key}")

    monkeypatch.setattr(anthropic, "Anthropic", Boom)
    r = client.post("/enrich", json={"text": "привіт", "provider": "anthropic", "edit_level": "light"},
                    headers={"X-Anthropic-Key": "sk-ant-secret"})
    assert r.status_code == 502
    assert "sk-ant-secret" not in r.text
