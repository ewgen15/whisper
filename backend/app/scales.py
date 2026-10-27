"""Emotional assessment on three scales — valence, energy, control, each an integer -2..2.

The model predicts the numbers, the user can correct them, and the label (`emotional_state`) is derived
from them here. docs/index.html mirrors `derive_state` for the live preview; a test keeps them identical.
"""
SCALES = ("valence", "energy", "control")
PRED_PREFIX = "pred_"


def clean_scale(value):
    """An int in -2..2, or None for anything else (missing, bool, text, out of range)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    n = round(value)
    return n if -2 <= n <= 2 else None


def derive_state(valence, energy, control):
    """Label from the scales. Pleasant: calm → «Гарний», energised → «Позитивний». Unpleasant: with a sense of
    helplessness → «Пригнічений», otherwise → «Поганий» (tension, exhaustion). Neutral valence is «Нейтральний»,
    unless control is at the bottom — then it is still «Пригнічений»."""
    if valence is None or energy is None or control is None:
        return None
    if valence >= 1:
        return "Позитивний" if energy >= 1 else "Гарний"
    if valence <= -1:
        return "Пригнічений" if control <= -1 else "Поганий"
    return "Пригнічений" if control <= -2 else "Нейтральний"


def apply_scales(entry: dict) -> dict:
    """Entry with validated scales (and the model's predictions under `pred_*`); when all three scales are
    present `emotional_state` is recomputed from them, so the label can never disagree with the numbers.
    Entries without scales keep whatever state they came with."""
    out = dict(entry)
    for key in SCALES:
        out[key] = clean_scale(entry.get(key))
        out[PRED_PREFIX + key] = clean_scale(entry.get(PRED_PREFIX + key))
    state = derive_state(*(out[k] for k in SCALES))
    if state is not None:
        out["emotional_state"] = state
    return out
