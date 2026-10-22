from app.stats import build_stats


def _row(ts, mood=None, emo=None):
    return {"created_at": ts, "mood": mood, "emotional_state": emo}


def test_groups_rows_by_kyiv_day():
    rows = [
        _row("2026-10-22T08:00:00", ["радісний"], "Позитивний"),
        _row("2026-10-22T15:41:08.670663", ["спокійний", "радісний"], "Гарний"),
    ]
    out = build_stats(rows)
    assert out["total"] == 2
    assert out["days"] == [
        {"d": "2026-10-22", "n": 2, "mood": ["радісний", "спокійний", "радісний"], "emo": ["Позитивний", "Гарний"]}
    ]


def test_utc_late_evening_belongs_to_next_kyiv_day():
    # 22:30 UTC in October is 01:30 next day in Kyiv (UTC+3)
    out = build_stats([_row("2026-10-22T22:30:00+00:00", ["сумний"], "Поганий")])
    assert out["days"][0]["d"] == "2026-10-23"


def test_days_sorted_ascending_and_tolerates_missing_fields():
    rows = [
        _row("2026-03-02T10:00:00", None, None),
        _row("2026-03-01T10:00:00", "не список", ""),
        {"created_at": None},
    ]
    out = build_stats(rows)
    assert [d["d"] for d in out["days"]] == ["2026-03-01", "2026-03-02"]
    assert out["days"][0] == {"d": "2026-03-01", "n": 1, "mood": [], "emo": []}
    assert out["total"] == 2


def test_stats_endpoint_reads_documents_table(sb):
    sb.responses[("documents", "select")] = [_row("2026-10-22T08:00:00", ["радісний"], "Позитивний")]
    from fastapi.testclient import TestClient
    from app.main import app

    r = TestClient(app).get("/stats")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert sb.ops("documents", "select")
