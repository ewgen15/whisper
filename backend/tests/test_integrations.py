from app import integrations

ENTRY = {
    "title": "Тема", "text": "Абзац 1\nАбзац 2", "tags": ["т"], "mood": ["а,б"],
    "entry_type": "Рефлексія", "emotional_state": None, "people": ["Мар'ян"],
    "points": ["теза 1", "теза 2"], "time_of_day": "Ранок",
}


# --- create ----------------------------------------------------------------

def test_save_vector_writes_document_then_linked_embedding_and_returns_id(sb, oai):
    doc_id = integrations.save_vector({
        "title": "Тема", "text": "Текст запису", "tags": ["т"], "mood": ["м"],
        "entry_type": "Подія", "points": ["а", "б"],
    })
    assert doc_id == "doc-1"
    (t1, op1, doc, _), (t2, op2, emb, _) = sb.log
    assert (t1, op1, t2, op2) == ("documents", "insert", "embeddings", "insert")
    assert doc["topic"] == "Тема" and doc["points"] == "• а • б"
    assert emb["document_id"] == "doc-1"
    assert emb["content"] == "Текст запису"  # plain text, like n8n
    assert emb["embedding"] == [0.1, 0.2, 0.3]
    assert emb["metadata"] == {"topic": "Тема", "tags": ["т"], "type": "Подія", "mood": ["м"]}
    model, sent = oai.calls[0]
    assert model == "text-embedding-ada-002"
    assert sent.startswith("Назва теми: Тема.") and "Текст: Текст запису." in sent


def test_save_vector_skips_empty_text(sb, oai):
    assert integrations.save_vector({"title": "x", "text": "  "}) is None
    assert sb.log == []


def test_notion_payload_and_page_id(notion):
    page_id = integrations.save_notion(ENTRY)
    assert page_id == "page-1"
    (method, url, body), = notion.calls
    assert (method, url) == ("POST", "https://api.notion.com/v1/pages")
    props = body["properties"]
    assert props["Настрій"]["multi_select"] == [{"name": "а б"}]  # commas stripped
    assert props["Тип запису"] == {"select": {"name": "Рефлексія"}}
    assert "Емоційний стан" not in props  # empty select omitted
    assert props["Ім'я контакта"]["rich_text"][0]["text"]["content"] == "Мар'ян"
    assert [b["type"] for b in body["children"]] == ["paragraph", "paragraph", "heading_3", "bulleted_list_item", "bulleted_list_item"]


def test_sync_entry_returns_status_and_links(monkeypatch):
    monkeypatch.setenv("VECTOR_ENABLED", "true")
    monkeypatch.setenv("NOTION_ENABLED", "true")
    monkeypatch.setattr(integrations, "save_vector", lambda e: "doc-9")
    monkeypatch.setattr(integrations, "save_notion", lambda e: "page-9")
    status, links = integrations.sync_entry({"title": "x"})
    assert status == {"vector": "ok", "notion": "ok"}
    assert links == {"document_id": "doc-9", "notion_page_id": "page-9"}


def test_sync_entry_isolates_failures_and_omits_failed_links(monkeypatch):
    monkeypatch.setenv("VECTOR_ENABLED", "true")
    monkeypatch.setenv("NOTION_ENABLED", "true")

    def boom(entry):
        raise RuntimeError("down")

    monkeypatch.setattr(integrations, "save_vector", boom)
    monkeypatch.setattr(integrations, "save_notion", lambda e: "page-9")
    status, links = integrations.sync_entry({"title": "x"})
    assert status == {"vector": "error: RuntimeError", "notion": "ok"}
    assert links == {"notion_page_id": "page-9"}


def test_sync_entry_noop_when_flags_off(monkeypatch):
    monkeypatch.delenv("VECTOR_ENABLED", raising=False)
    monkeypatch.delenv("NOTION_ENABLED", raising=False)
    assert integrations.sync_entry({}) == ({}, {})


# --- update ----------------------------------------------------------------

ROW = {
    "id": 7, "title": "Нова назва", "text": "Новий текст", "tags": ["т"], "mood": ["м"],
    "entry_type": "Подія", "time_of_day": "День", "emotional_state": "Гарний",
    "notion_page_id": "page-1", "document_id": "doc-1",
}


def test_update_remote_syncs_notion_properties_body_and_keeps_points(monkeypatch, notion):
    monkeypatch.setenv("NOTION_ENABLED", "true")
    monkeypatch.delenv("VECTOR_ENABLED", raising=False)
    notion.children = [
        {"id": "b1", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "старий"}]}},
        {"id": "b2", "type": "heading_3", "heading_3": {"rich_text": [{"plain_text": "Тези"}]}},
        {"id": "b3", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "стара теза"}]}},
    ]
    status = integrations.update_remote(ROW)
    assert status == {"notion": "ok"}
    assert notion.methods_urls() == [
        ("PATCH", "/pages/page-1"),
        ("GET", "/blocks/page-1/children"),
        ("DELETE", "/blocks/b1"), ("DELETE", "/blocks/b2"), ("DELETE", "/blocks/b3"),
        ("PATCH", "/blocks/page-1/children"),
    ]
    props = notion.calls[0][2]["properties"]
    assert props["Name"]["title"][0]["text"]["content"] == "Нова назва"
    appended = notion.calls[-1][2]["children"]
    texts = [b[b["type"]]["rich_text"][0]["text"]["content"] for b in appended]
    assert texts == ["Новий текст", "Тези", "стара теза"]  # existing theses survive an edit


def test_update_remote_refreshes_document_and_embedding(monkeypatch, sb, oai):
    monkeypatch.setenv("VECTOR_ENABLED", "true")
    monkeypatch.delenv("NOTION_ENABLED", raising=False)
    sb.responses[("documents", "select")] = [{"id": "doc-1", "points": "• стара теза", "time_of_day": "Ранок"}]
    status = integrations.update_remote(ROW)
    assert status == {"vector": "ok"}
    doc_upd, = sb.ops("documents", "update")
    assert doc_upd[2]["topic"] == "Нова назва" and doc_upd[2]["text"] == "Новий текст"
    assert doc_upd[3] == [("id", "doc-1")]
    emb_upd, = sb.ops("embeddings", "update")
    assert emb_upd[2]["content"] == "Новий текст" and emb_upd[2]["embedding"] == [0.1, 0.2, 0.3]
    assert emb_upd[3] == [("document_id", "doc-1")]
    assert "Ключові пункти: • стара теза." in oai.calls[0][1]  # points come from documents


def test_update_remote_skips_unlinked_entries(monkeypatch, sb, oai, notion):
    monkeypatch.setenv("VECTOR_ENABLED", "true")
    monkeypatch.setenv("NOTION_ENABLED", "true")
    row = {k: v for k, v in ROW.items() if k not in ("notion_page_id", "document_id")}
    assert integrations.update_remote(row) == {}
    assert sb.log == [] and notion.calls == []


def test_update_remote_isolates_failures(monkeypatch, sb, oai, notion):
    monkeypatch.setenv("VECTOR_ENABLED", "true")
    monkeypatch.setenv("NOTION_ENABLED", "true")
    sb.responses[("documents", "select")] = [{"id": "doc-1", "points": ""}]
    notion.fail_on = "/pages/"
    status = integrations.update_remote(ROW)
    assert status == {"vector": "ok", "notion": "error: RuntimeError"}


# --- delete ----------------------------------------------------------------

def test_delete_remote_archives_notion_and_removes_vector_rows(monkeypatch, sb, notion):
    monkeypatch.setenv("VECTOR_ENABLED", "true")
    monkeypatch.setenv("NOTION_ENABLED", "true")
    status = integrations.delete_remote(ROW)
    assert status == {"vector": "ok", "notion": "ok"}
    (method, url, body), = notion.calls
    assert (method, url.endswith("/pages/page-1"), body) == ("PATCH", True, {"archived": True})  # recoverable
    assert [(t, f) for t, op, _, f in sb.log if op == "delete"] == [
        ("embeddings", [("document_id", "doc-1")]),   # child first, then parent
        ("documents", [("id", "doc-1")]),
    ]


def test_delete_remote_skips_unlinked_and_isolates_failures(monkeypatch, sb, notion):
    monkeypatch.setenv("VECTOR_ENABLED", "true")
    monkeypatch.setenv("NOTION_ENABLED", "true")
    assert integrations.delete_remote({"id": 1}) == {}
    notion.fail_on = "/pages/"
    assert integrations.delete_remote(ROW) == {"vector": "ok", "notion": "error: RuntimeError"}
