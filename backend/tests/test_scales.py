import json
import re
import shutil
import subprocess
from itertools import product
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import enrichment
from app.scales import SCALES, apply_scales, clean_scale, derive_state
from app.storage import save_entry

client = TestClient(main.app)
INDEX = Path(__file__).resolve().parents[2] / "docs" / "index.html"


@pytest.fixture(autouse=True)
def _open_api(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    main.limiter.reset()
    main.fail_limiter.reset()


# --- derive_state ------------------------------------------------------------

@pytest.mark.parametrize("v,e,c,expected", [
    (2, 2, 1, "Позитивний"),     # joy and energy
    (1, 1, 0, "Позитивний"),
    (1, 0, 0, "Гарний"),         # calm contentment
    (2, -1, 2, "Гарний"),        # pleasant but drained
    (0, 0, 0, "Нейтральний"),    # plans, ideas, reflection
    (0, 2, 2, "Нейтральний"),    # energy alone does not make a mood
    (-1, 0, 0, "Поганий"),       # tension / illness while still in charge
    (-1, -2, 0, "Поганий"),
    (-1, 0, -1, "Пригнічений"),  # unpleasant and not in control
    (-2, 1, -2, "Пригнічений"),
    (0, 0, -2, "Пригнічений"),   # no control at all weighs down even a neutral tone
    (0, 0, -1, "Нейтральний"),
])
def test_derive_state(v, e, c, expected):
    assert derive_state(v, e, c) == expected


def test_derive_state_is_none_until_all_three_scales_are_known():
    assert derive_state(None, 0, 0) is None
    assert derive_state(1, None, 0) is None
    assert derive_state(1, 0, None) is None


def test_every_combination_gets_a_state_and_control_only_lowers_the_mood():
    rank = {"Пригнічений": 0, "Поганий": 1, "Нейтральний": 2, "Гарний": 3, "Позитивний": 4}
    for v, e, c in product(range(-2, 3), repeat=3):
        assert derive_state(v, e, c) in rank
    # more control never makes the same valence/energy worse, except the intended «Поганий» ↔ «Пригнічений» split
    for v, e in product(range(-2, 3), repeat=2):
        if v >= 0:
            states = [rank[derive_state(v, e, c)] for c in range(-2, 3)]
            assert states == sorted(states)


# --- clean_scale / apply_scales ----------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (0, 0), (-2, -2), (2, 2), (1.4, 1), (-1.6, -2),
    (3, None), (-3, None), (2.6, None), (True, None), ("1", None), (None, None), ([], None),
])
def test_clean_scale(raw, expected):
    assert clean_scale(raw) == expected


def test_apply_scales_derives_the_label_and_overrides_the_clients():
    out = apply_scales({"valence": -1, "energy": 0, "control": -2, "emotional_state": "Позитивний", "title": "t"})
    assert out["emotional_state"] == "Пригнічений"
    assert out["title"] == "t"


def test_apply_scales_keeps_the_label_when_scales_are_missing_or_invalid():
    assert apply_scales({"emotional_state": "Гарний"})["emotional_state"] == "Гарний"
    out = apply_scales({"valence": 1, "energy": "high", "control": 0, "emotional_state": "Гарний"})
    assert out["energy"] is None and out["emotional_state"] == "Гарний"


def test_apply_scales_cleans_the_predictions_too():
    out = apply_scales({"valence": 1, "energy": 1, "control": 1, "pred_valence": 9, "pred_energy": 0, "pred_control": -1})
    assert (out["pred_valence"], out["pred_energy"], out["pred_control"]) == (None, 0, -1)


# --- enrich ------------------------------------------------------------------

def _enrich(monkeypatch, extraction, scales):
    """`scales` is what the dedicated scales call answers (a dict, a raw string, or an exception to raise)."""
    def fake(provider, api_key, role, system, messages, temperature, json_mode=False):
        if system == enrichment._SCALES_PROMPT:
            if isinstance(scales, Exception):
                raise scales
            return scales if isinstance(scales, str) else json.dumps(scales)
        return json.dumps(extraction)

    monkeypatch.setattr(enrichment, "_complete", fake)
    return enrichment.enrich("текст", edit_level="none")


EXTRACTION = {"title": "t", "text": "x", "mood": []}


def test_enrich_derives_the_state_from_the_scales_call(monkeypatch):
    out = _enrich(monkeypatch, {**EXTRACTION, "emotional_state": "Позитивний"},
                  {"valence": -1, "energy": -1, "control": -2})
    assert (out["valence"], out["energy"], out["control"]) == (-1, -1, -2)
    assert out["emotional_state"] == "Пригнічений"  # a stray label from the extraction call is ignored


def test_enrich_without_usable_scales_gives_no_state(monkeypatch):
    out = _enrich(monkeypatch, {**EXTRACTION, "emotional_state": "Гарний"}, {"valence": 7, "energy": 0})
    assert out["valence"] is None and out["control"] is None
    assert out["emotional_state"] is None


@pytest.mark.parametrize("scales", [RuntimeError("openai down"), "не json", ""])
def test_a_failing_scales_call_does_not_break_the_rest_of_enrich(monkeypatch, scales):
    out = _enrich(monkeypatch, EXTRACTION, scales)
    assert out["title"] == "t" and out["text"] == "текст"  # edit_level "none" keeps the raw transcript
    assert out["emotional_state"] is None and out["energy"] is None


def test_the_scales_call_sees_the_corrected_text_and_a_cheap_model(monkeypatch):
    seen = []

    def fake(provider, api_key, role, system, messages, temperature, json_mode=False):
        seen.append((role, system, messages[0]["content"]))
        return json.dumps({"valence": 0, "energy": 0, "control": 0} if system == enrichment._SCALES_PROMPT else EXTRACTION)

    monkeypatch.setattr(enrichment, "_complete", fake)
    enrichment.enrich("сирий текст", edit_level="none")
    role, _, user = [c for c in seen if c[1] == enrichment._SCALES_PROMPT][0]
    assert role == "extract" and "сирий текст" in user


def test_the_extraction_prompt_leaves_the_scales_to_the_dedicated_call():
    body = enrichment._SYSTEM_PROMPT
    assert "- valence:" not in body and "- emotional_state:" not in body
    for key in SCALES:
        assert f"{key} —" in enrichment._SCALES_PROMPT


# --- /save and storage ---------------------------------------------------------

def _post_save(monkeypatch, entry):
    stored, synced = [], []
    monkeypatch.setattr(main, "save_entry", lambda e: stored.append(e) or {"id": 1})
    monkeypatch.setattr(main, "sync_entry", lambda e: synced.append(e) or ({}, {}))
    assert client.post("/save", json={"entry": entry}).status_code == 200
    return stored[0], synced[0]


def test_save_derives_the_state_from_the_scales_for_storage_and_for_notion(monkeypatch):
    stored, synced = _post_save(monkeypatch, {"title": "T", "valence": 1, "energy": 2, "control": 1,
                                              "emotional_state": "Поганий"})
    assert stored["emotional_state"] == synced["emotional_state"] == "Позитивний"


def test_save_drops_invalid_scales_and_keeps_the_clients_label(monkeypatch):
    stored, _ = _post_save(monkeypatch, {"title": "T", "valence": 1, "energy": 2, "control": 9,
                                         "pred_valence": -1, "emotional_state": "Поганий"})
    assert stored["control"] is None and stored["pred_valence"] == -1
    assert stored["emotional_state"] == "Поганий"


def test_save_entry_stores_the_scales_and_the_predictions(sb):
    save_entry(apply_scales({"title": "t", "valence": 1, "energy": 0, "control": -1,
                             "pred_valence": 2, "pred_energy": 0, "pred_control": 0}))
    row = [p for _, op, p, _ in sb.log if op == "insert"][0]
    assert (row["valence"], row["energy"], row["control"]) == (1, 0, -1)
    assert (row["pred_valence"], row["pred_energy"], row["pred_control"]) == (2, 0, 0)
    assert row["emotional_state"] == "Гарний"


def test_save_entry_without_scales_writes_nulls(sb):
    save_entry({"title": "t"})
    row = [p for _, op, p, _ in sb.log if op == "insert"][0]
    assert all(row[k] is None for k in ("valence", "energy", "control", "pred_valence", "pred_energy", "pred_control"))


# --- the browser keeps its own copy of derive_state for the live preview --------

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_frontend_derive_state_matches_the_backend_for_every_combination():
    html = INDEX.read_text()
    m = re.search(r"// @derive-state-start\n(.*?)// @derive-state-end", html, re.S)
    assert m, "markers around deriveState are missing in docs/index.html"
    script = m.group(1) + "\nconst out = {};\nfor (let v=-2; v<=2; v++) for (let e=-2; e<=2; e++) for (let c=-2; c<=2; c++)" \
                          " out[[v,e,c]] = deriveState(v, e, c);\nconsole.log(JSON.stringify(out));"
    js = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    for v, e, c in product(range(-2, 3), repeat=3):
        assert js[f"{v},{e},{c}"] == derive_state(v, e, c), (v, e, c)
