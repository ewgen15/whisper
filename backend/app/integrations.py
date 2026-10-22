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


def save_vector(entry: dict) -> None:
    """Mirrors the n8n flow: documents row + embeddings row (ada-002 over the text)."""
    global _openai
    text = entry.get("text") or ""
    if not text.strip():
        return
    if _openai is None:
        _openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    sb = get_client()
    doc = sb.table("documents").insert({
        "topic":           entry.get("title"),
        "tags":            entry.get("tags", []),
        "type":            entry.get("entry_type"),
        "time_of_day":     entry.get("time_of_day"),
        "mood":            entry.get("mood", []),
        "project":         entry.get("project"),
        "context":         entry.get("context"),
        "physical_state":  entry.get("physical_state"),
        "emotional_state": entry.get("emotional_state"),
        "text":            text,
        "points":          _points_text(entry.get("points")),
    }).execute()
    doc_id = doc.data[0]["id"]

    vector = _openai.embeddings.create(model=EMBEDDING_MODEL, input=_embedding_input(entry)).data[0].embedding
    sb.table("embeddings").insert({
        "content":     text,
        "metadata":    {
            "topic": entry.get("title"),
            "tags":  entry.get("tags", []),
            "type":  entry.get("entry_type"),
            "mood":  entry.get("mood", []),
        },
        "embedding":   vector,
        "document_id": doc_id,
    }).execute()


def _rich(text: str) -> list[dict]:
    # Notion caps a single rich_text object at 2000 chars
    return [{"type": "text", "text": {"content": text[i:i + 2000]}} for i in range(0, len(text), 2000)]


def _tags(values) -> list[dict]:
    # multi_select names can't contain commas
    return [{"name": str(v).replace(",", " ")[:100]} for v in values or [] if v]


def _select(value) -> dict | None:
    return {"select": {"name": str(value).replace(",", " ")}} if value else None


def save_notion(entry: dict) -> None:
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
        sel = _select(entry.get(key))
        if sel:
            props[notion_name] = sel
    for notion_name, key in (("Контекст", "context"), ("Проект", "project")):
        if entry.get(key):
            props[notion_name] = {"rich_text": _rich(entry[key])[:1]}
    if entry.get("people"):
        props["Ім'я контакта"] = {"rich_text": _rich(", ".join(entry["people"]))[:1]}

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

    r = httpx.post(
        "https://api.notion.com/v1/pages",
        headers={
            "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
            "Notion-Version": "2022-06-28",
        },
        json={"parent": {"database_id": NOTION_DB_ID}, "properties": props, "children": children[:100]},
        timeout=20,
    )
    r.raise_for_status()


def sync_entry(entry: dict) -> dict:
    """Runs the enabled side effects; one failing never blocks the other or the save."""
    status = {}
    for name, flag, fn in (
        ("vector", "VECTOR_ENABLED", save_vector),
        ("notion", "NOTION_ENABLED", save_notion),
    ):
        if not _flag(flag):
            continue
        try:
            fn(entry)
            status[name] = "ok"
        except Exception as exc:  # noqa: BLE001 — surfaced to the client, not fatal
            log.exception("%s sync failed", name)
            status[name] = f"error: {type(exc).__name__}"
    return status
