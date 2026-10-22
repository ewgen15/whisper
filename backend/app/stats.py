from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Europe/Kyiv")


def _kyiv_day(value) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # documents.created_at is stored as naive UTC
    return dt.astimezone(_TZ).date().isoformat()


def build_stats(rows: list[dict]) -> dict:
    """Per-day aggregate of mood and emotional_state; the client colours and charts it."""
    days: dict[str, dict] = {}
    total = 0
    for row in rows:
        day = _kyiv_day(row.get("created_at"))
        if day is None:
            continue  # undated rows can't be placed on a chart
        total += 1
        bucket = days.setdefault(day, {"d": day, "n": 0, "mood": [], "emo": []})
        bucket["n"] += 1
        mood = row.get("mood")
        if isinstance(mood, list):
            bucket["mood"].extend(str(m) for m in mood if m)
        if row.get("emotional_state"):
            bucket["emo"].append(row["emotional_state"])
    return {"total": total, "days": [days[k] for k in sorted(days)]}
