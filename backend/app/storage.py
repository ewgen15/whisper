import os
from supabase import create_client, Client

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _client = create_client(url, key)
    return _client


def save_entry(data: dict) -> dict:
    row = {
        "title":           data.get("title"),
        "text":            data.get("text"),
        "raw_text":        data.get("rawText"),
        "mood":            data.get("mood", []),
        "emotional_state": data.get("emotional_state"),
        "physical_state":  data.get("physical_state"),
        "context":         data.get("context"),
        "project":         data.get("project"),
        "tags":            data.get("tags", []),
        "entry_type":      data.get("entry_type"),
        "time_of_day":     data.get("time_of_day"),
        "duration":        data.get("duration"),
        "date_label":      data.get("date_label"),
        "month_label":     data.get("month_label"),
    }
    result = get_client().table("entries").insert(row).execute()
    return result.data[0] if result.data else row


def get_entries(limit: int = 50) -> list[dict]:
    result = (
        get_client()
        .table("entries")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []
