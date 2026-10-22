import logging
import os

import httpx
from openai import OpenAI

from .storage import get_client

log = logging.getLogger("diary.integrations")

NOTION_DB_ID = os.environ.get("NOTION_DATABASE_ID", "1c745674-5b78-80af-aca8-c828db2feb60")
EMBEDDING_MODEL = "text-embedding-ada-002"

_openai: OpenAI | None = None


def _flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _points_text(points) -> str:
    return " ".join(f"• {p}" for p in points or [])


def _embedding_input(entry: dict) -> str:
    """Same string layout as the n8n embedding node, so old and new vectors stay comparable."""
    def val(v):
        return v or "не вказано"

    def joined(v):
        return ", ".join(v) if v else "не вказано"

    return (
        f"Назва теми: {val(entry.get('title'))}. "
        f"Теги: {joined(entry.get('tags'))}. "
        f"Тип запису: {val(entry.get('entry_type'))}. "
        f"Час доби: {val(entry.get('time_of_day'))}. "
        f"Настрій: {joined(entry.get('mood'))}. "
        f"Проєкт: {val(entry.get('project'))}. "
        f"Контекст: {val(entry.get('context'))}. "
        f"Фізичний стан: {val(entry.get('physical_state'))}. "
        f"Емоційний стан: {val(entry.get('emotional_state'))}. "
        f"Текст: {val(entry.get('text'))}. "
        f"Ключові пункти: {val(_points_text(entry.get('points')))}."
    )


def _ensure_openai():
    global _openai
    if _openai is None:
        _openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai


def _metadata(entry: dict) -> dict:
    return {
        "topic": entry.get("title"),
        "tags":  entry.get("tags", []),
        "type":  entry.get("entry_type"),
        "mood":  entry.get("mood", []),
    }


def _document_row(entry: dict) -> dict:
    return {
        "topic":           entry.get("title"),
        "tags":            entry.get("tags", []),
        "type":            entry.get("entry_type"),
        "time_of_day":     entry.get("time_of_day"),
        "mood":            entry.get("mood", []),
        "project":         entry.get("project"),
        "context":         entry.get("context"),
        "physical_state":  entry.get("physical_state"),
        "emotional_state": entry.get("emotional_state"),
        "text":            entry.get("text") or "",
    }


def _embed(entry: dict) -> list[float]:
    return _ensure_openai().embeddings.create(model=EMBEDDING_MODEL, input=_embedding_input(entry)).data[0].embedding


def save_vector(entry: dict) -> str | None:
    """Mirrors the n8n flow: documents row + embeddings row (ada-002). Returns the document id."""
    text = entry.get("text") or ""
    if not text.strip():
        return None
    sb = get_client()
    doc = sb.table("documents").insert({**_document_row(entry), "points": _points_text(entry.get("points"))}).execute()
    doc_id = doc.data[0]["id"]
    sb.table("embeddings").insert({
        "content":     text,
        "metadata":    _metadata(entry),
        "embedding":   _embed(entry),
        "document_id": doc_id,
    }).execute()
    return doc_id


def update_vector(row: dict) -> None:
    sb = get_client()
    doc_id = row["document_id"]
    docs = sb.table("documents").select("points").eq("id", doc_id).execute().data
    if not docs:
        return
    # points aren't stored in `entries`; reuse the ones already written to documents
    points = [p.strip() for p in (docs[0].get("points") or "").split("•") if p.strip()]
    entry = {**row, "points": points}
    sb.table("documents").update(_document_row(entry)).eq("id", doc_id).execute()
    sb.table("embeddings").update({
        "content":   entry.get("text") or "",
        "metadata":  _metadata(entry),
        "embedding": _embed(entry),
    }).eq("document_id", doc_id).execute()


def delete_vector(row: dict) -> None:
    sb = get_client()
    sb.table("embeddings").delete().eq("document_id", row["document_id"]).execute()
    sb.table("documents").delete().eq("id", row["document_id"]).execute()


def _rich(text: str) -> list[dict]:
    # Notion caps a single rich_text object at 2000 chars
    return [{"type": "text", "text": {"content": text[i:i + 2000]}} for i in range(0, len(text), 2000)]


def _tags(values) -> list[dict]:
    # multi_select names can't contain commas
    return [{"name": str(v).replace(",", " ")[:100]} for v in values or [] if v]


def _select(value) -> dict:
    return {"select": {"name": str(value).replace(",", " ")} if value else None}


def _notion(method: str, path: str, json=None) -> dict:
    r = httpx.request(
        method,
        f"https://api.notion.com/v1{path}",
        headers={
            "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
            "Notion-Version": "2022-06-28",
        },
        json=json,
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def _notion_props(entry: dict, clear: bool = False) -> dict:
    """clear=True also blanks properties that are empty (needed when editing)."""
    props = {
        "Name":    {"title": _rich(entry.get("title") or "Без назви")[:1]},
        "Настрій": {"multi_select": _tags(entry.get("mood"))},
        "Теги":    {"multi_select": _tags(entry.get("tags"))},
    }
    for notion_name, key in (
        ("Тип запису", "entry_type"),
        ("Емоційний стан", "emotional_state"),
        ("Фізичний стан", "physical_state"),
        ("Час доби", "time_of_day"),
    ):
        if entry.get(key) or clear:
            props[notion_name] = _select(entry.get(key))
    for notion_name, key in (("Контекст", "context"), ("Проект", "project")):
        if entry.get(key) or clear:
            props[notion_name] = {"rich_text": _rich(entry.get(key) or "")[:1]}
    if entry.get("people"):
        props["Ім'я контакта"] = {"rich_text": _rich(", ".join(entry["people"]))[:1]}
    return props


def _notion_blocks(entry: dict) -> list[dict]:
    children = [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich(chunk)}}
        for chunk in (entry.get("text") or "").split("\n") if chunk.strip()
    ]
    points = entry.get("points") or []
    if points:
        children.append({"object": "block", "type": "heading_3",
                         "heading_3": {"rich_text": _rich("Тези")}})
        children += [
            {"object": "block", "type": "bulleted_list_item",
             "bulleted_list_item": {"rich_text": _rich(str(p))}}
            for p in points
        ]
    return children[:100]


def save_notion(entry: dict) -> str:
    page = _notion("POST", "/pages", {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": _notion_props(entry),
        "children": _notion_blocks(entry),
    })
    return page["id"]


def _plain(block: dict) -> str:
    return "".join(t.get("plain_text", "") for t in block.get(block["type"], {}).get("rich_text", []))


def update_notion(row: dict) -> None:
    page_id = row["notion_page_id"]
    _notion("PATCH", f"/pages/{page_id}", {"properties": _notion_props(row, clear=True)})
    existing = _notion("GET", f"/blocks/{page_id}/children")["results"]
    # points live only in the page body; keep whatever is listed under the "Тези" heading
    points, in_points = [], False
    for b in existing:
        if b["type"] == "heading_3":
            in_points = True
        elif in_points and b["type"] == "bulleted_list_item":
            points.append(_plain(b))
    for b in existing:
        _notion("DELETE", f"/blocks/{b['id']}")
    children = _notion_blocks({**row, "points": points})
    if children:
        _notion("PATCH", f"/blocks/{page_id}/children", {"children": children})


def delete_notion(row: dict) -> None:
    _notion("PATCH", f"/pages/{row['notion_page_id']}", {"archived": True})  # stays recoverable in Notion trash


# (status key, env flag, entries column holding the remote id, create/update/delete function names —
# resolved at call time so tests can patch them)
_TARGETS = (
    ("vector", "VECTOR_ENABLED", "document_id",    "save_vector", "update_vector", "delete_vector"),
    ("notion", "NOTION_ENABLED", "notion_page_id", "save_notion", "update_notion", "delete_notion"),
)


def _run(name: str, fn, arg) -> str:
    try:
        fn(arg)
        return "ok"
    except Exception as exc:  # noqa: BLE001 — surfaced to the client, not fatal
        log.exception("%s sync failed", name)
        return f"error: {type(exc).__name__}"


def sync_entry(entry: dict) -> tuple[dict, dict]:
    """Runs the enabled side effects; one failing never blocks the other or the save.

    Returns (statuses, links) where links maps `entries` columns to the remote ids created."""
    status, links = {}, {}
    for name, flag, column, create, _update, _delete in _TARGETS:
        if not _flag(flag):
            continue
        try:
            remote_id = globals()[create](entry)
            status[name] = "ok"
            if remote_id:
                links[column] = remote_id
        except Exception as exc:  # noqa: BLE001
            log.exception("%s sync failed", name)
            status[name] = f"error: {type(exc).__name__}"
    return status, links


def _apply(row: dict, op_index: int) -> dict:
    status = {}
    for target in _TARGETS:
        name, flag, column = target[:3]
        if _flag(flag) and row.get(column):
            status[name] = _run(name, globals()[target[op_index]], row)
    return status


def update_remote(row: dict) -> dict:
    """Pushes an edited `entries` row to Notion/Supabase vectors; entries without a link are skipped."""
    return _apply(row, 4)


def delete_remote(row: dict) -> dict:
    return _apply(row, 5)
