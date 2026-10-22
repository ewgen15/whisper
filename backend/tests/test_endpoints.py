import pytest
from fastapi.testclient import TestClient

import app.main as main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _open_api(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    main.limiter.reset()
    main.fail_limiter.reset()


# --- POST /save ------------------------------------------------------------

def test_save_stores_remote_links_and_returns_sync(monkeypatch):
    linked = []
    monkeypatch.setattr(main, "save_entry", lambda e: {"id": 5, "title": e["title"]})
    monkeypatch.setattr(main, "sync_entry", lambda e: ({"vector": "ok", "notion": "ok"},
                                                      {"document_id": "d", "notion_page_id": "p"}))
    monkeypatch.setattr(main, "set_entry_links", lambda i, links: linked.append((i, links)))
    r = client.post("/save", json={"entry": {"title": "T"}})
    assert r.status_code == 200
    assert linked == [(5, {"document_id": "d", "notion_page_id": "p"})]
    assert r.json()["sync"] == {"vector": "ok", "notion": "ok"}
    assert r.json()["notion_page_id"] == "p"


def test_save_without_sync_has_no_sync_field_and_no_link_write(monkeypatch):
    monkeypatch.setattr(main, "save_entry", lambda e: {"id": 1})
    monkeypatch.setattr(main, "sync_entry", lambda e: ({}, {}))
    monkeypatch.setattr(main, "set_entry_links", lambda *a: pytest.fail("must not be called"))
    assert client.post("/save", json={"entry": {}}).json() == {"id": 1}


# --- PATCH /entries/{id} -----------------------------------------------------

def test_patch_updates_only_allowed_fields_and_syncs(monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "get_entry", lambda i: {"id": i, "title": "old", "notion_page_id": "p"})

    def fake_update(i, changes):
        seen["changes"] = changes
        return {"id": i, "title": changes["title"], "notion_page_id": "p"}

    monkeypatch.setattr(main, "update_entry", fake_update)
    monkeypatch.setattr(main, "update_remote", lambda row: {"notion": "ok"})
    r = client.patch("/entries/3", json={"title": "new", "id": 99, "notion_page_id": "evil", "created_at": "x"})
    assert r.status_code == 200
    assert seen["changes"] == {"title": "new"}  # protected columns are dropped
    assert r.json()["title"] == "new" and r.json()["sync"] == {"notion": "ok"}


def test_patch_404_for_unknown_entry(monkeypatch):
    monkeypatch.setattr(main, "get_entry", lambda i: None)
    assert client.patch("/entries/3", json={"title": "x"}).status_code == 404


def test_patch_422_for_empty_changes(monkeypatch):
    monkeypatch.setattr(main, "get_entry", lambda i: {"id": i})
    assert client.patch("/entries/3", json={}).status_code == 422
    assert client.patch("/entries/3", json={"title": "   "}).status_code == 422


# --- DELETE /entries/{id} ----------------------------------------------------

def test_delete_removes_remote_then_local(monkeypatch):
    order = []
    row = {"id": 3, "notion_page_id": "p", "document_id": "d"}
    monkeypatch.setattr(main, "get_entry", lambda i: row)
    monkeypatch.setattr(main, "delete_remote", lambda r: order.append("remote") or {"notion": "ok", "vector": "ok"})
    monkeypatch.setattr(main, "delete_entry", lambda i: order.append("local"))
    r = client.delete("/entries/3")
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "sync": {"notion": "ok", "vector": "ok"}}
    assert order == ["remote", "local"]


def test_delete_404_for_unknown_entry(monkeypatch):
    monkeypatch.setattr(main, "get_entry", lambda i: None)
    assert client.delete("/entries/3").status_code == 404


def test_delete_still_removes_local_row_when_remote_fails(monkeypatch):
    removed = []
    monkeypatch.setattr(main, "get_entry", lambda i: {"id": i})
    monkeypatch.setattr(main, "delete_remote", lambda r: {"notion": "error: RuntimeError"})
    monkeypatch.setattr(main, "delete_entry", lambda i: removed.append(i))
    r = client.delete("/entries/3")
    assert removed == [3] and r.json()["sync"] == {"notion": "error: RuntimeError"}  # failure is reported, not hidden


# --- storage helpers -------------------------------------------------------

def test_storage_update_and_links_target_the_right_row(sb):
    from app import storage
    sb.responses[("entries", "update")] = [{"id": 3, "title": "n"}]
    assert storage.update_entry(3, {"title": "n"}) == {"id": 3, "title": "n"}
    storage.set_entry_links(3, {"notion_page_id": "p"})
    storage.delete_entry(3)
    assert [(op, f) for _, op, _, f in sb.log] == [("update", [("id", 3)]), ("update", [("id", 3)]), ("delete", [("id", 3)])]
