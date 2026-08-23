"""搜索页反馈写口(L→F 契约):闭集校验 / 幂等去重 / 路由挂载 / 只读白名单。

hermetic:假连接按 SQL 路由;live PG 段(pg marker)在 pg_compat 事务内真插真改后回滚。
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.domains.recommendations import search_feedback


def test_validate_payload_closed_sets() -> None:
    ok = search_feedback.validate_payload({"source": "discovery_wall", "kol_pool_id": 7, "verdict": "up", "reason": "other"})
    assert ok["reason"] == ""  # up 不带 reason
    down = search_feedback.validate_payload({"source": "kol_detail", "kol_pool_id": "7", "verdict": "down", "reason": "too_small"})
    assert down["kol_pool_id"] == 7 and down["reason"] == "too_small"
    with pytest.raises(ValueError):
        search_feedback.validate_payload({"source": "elsewhere", "kol_pool_id": 7, "verdict": "up"})
    with pytest.raises(ValueError):
        search_feedback.validate_payload({"source": "discovery_wall", "kol_pool_id": 0, "verdict": "up"})
    with pytest.raises(ValueError):
        search_feedback.validate_payload({"source": "discovery_wall", "kol_pool_id": 7, "verdict": "maybe"})
    with pytest.raises(ValueError):
        search_feedback.validate_payload({"source": "discovery_wall", "kol_pool_id": 7, "verdict": "down"})  # down 必填 reason
    with pytest.raises(ValueError):
        search_feedback.validate_payload({"source": "discovery_wall", "kol_pool_id": 7, "verdict": "down", "reason": "bad"})
    assert set(r["key"] for r in search_feedback.reason_options()) == set(search_feedback.REASONS)
    assert all(r["label_zh"] for r in search_feedback.reason_options())


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, *, existing: list[dict[str, Any]] | None = None, rec_id: int = 0):
        self.sql: list[tuple[str, tuple]] = []
        self.existing = existing or []
        self.rec_id = rec_id
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()):
        self.sql.append((" ".join(sql.split()), params))
        if "FROM vkpi_kol_recommendations" in sql:
            return _Cursor([{"id": self.rec_id}] if self.rec_id else [])
        if sql.lstrip().startswith("INSERT INTO vkpi_recommendation_feedback"):
            return _Cursor([{"id": 901}])
        if "SELECT id, feedback_type, reason, metadata_json" in sql:
            return _Cursor(self.existing)
        if "GROUP BY source" in sql:
            return _Cursor([
                {"source": "discovery_wall", "feedback_type": "reject", "reason": "too_small", "created_by_staff_id": 5, "n": 2},
                {"source": "kol_detail", "feedback_type": "shortlist", "reason": "", "created_by_staff_id": 6, "n": 1},
            ])
        return _Cursor([])

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None


def _wire(monkeypatch, conn: _Conn):
    monkeypatch.setattr(search_feedback, "get_conn", lambda: conn)
    monkeypatch.setattr(search_feedback, "table_exists", lambda name: True)
    monkeypatch.setattr(search_feedback, "_COLUMNS_READY", True)


def test_record_inserts_with_mapping_and_outcome_bridge(monkeypatch) -> None:
    conn = _Conn(rec_id=42)
    _wire(monkeypatch, conn)
    captured: dict[str, Any] = {}

    from app.domains.recommendations import outcomes

    def _fake_record_if_missing(rec_id, node, **kw):
        captured["rec"] = rec_id
        captured["node"] = node
        return True

    monkeypatch.setattr(outcomes, "record_if_missing", _fake_record_if_missing)
    out = search_feedback.record_search_feedback(
        {"source": "discovery_wall", "kol_pool_id": 7, "verdict": "down", "reason": "wrong_region", "session_item_id": "s1"},
        staff={"id": 5},
    )
    assert out["ok"] is True and out["feedback_id"] == 901 and out["deduped"] is False
    assert out["feedback_type"] == "reject" and out["recommendation_id"] == 42
    assert captured == {"rec": 42, "node": "rejected"} and out["outcome_changed"] is True
    insert = next(s for s, _ in conn.sql if s.startswith("INSERT INTO vkpi_recommendation_feedback"))
    assert "source, kol_pool_id, reason" in insert
    params = next(p for s, p in conn.sql if s.startswith("INSERT INTO vkpi_recommendation_feedback"))
    assert params[0] == 42 and params[1] == "reject" and params[6] == "discovery_wall" and params[7] == 7 and params[8] == "wrong_region"
    assert conn.commits == 1


def test_record_dedupes_and_updates_on_verdict_change(monkeypatch) -> None:
    conn = _Conn(existing=[{"id": 77, "feedback_type": "shortlist", "reason": "", "metadata_json": '{"verdict":"up"}'}])
    _wire(monkeypatch, conn)
    same = search_feedback.record_search_feedback({"source": "kol_detail", "kol_pool_id": 9, "verdict": "up"}, staff={"id": 5})
    assert same["ok"] and same["feedback_id"] == 77 and same["deduped"] and not same["updated"]
    assert not any(s.startswith("UPDATE") or s.startswith("INSERT") for s, _ in conn.sql)
    flipped = search_feedback.record_search_feedback(
        {"source": "kol_detail", "kol_pool_id": 9, "verdict": "down", "reason": "duplicate"}, staff={"id": 5},
    )
    assert flipped["feedback_id"] == 77 and flipped["updated"] is True and flipped["feedback_type"] == "reject"
    update = next((s, p) for s, p in conn.sql if s.startswith("UPDATE vkpi_recommendation_feedback"))
    assert update[1][0] == "reject" and update[1][1] == "duplicate" and update[1][-1] == 77
    assert not any(s.startswith("INSERT") for s, _ in conn.sql)


def test_migration_missing_is_honest(monkeypatch) -> None:
    conn = _Conn()
    monkeypatch.setattr(search_feedback, "get_conn", lambda: conn)
    monkeypatch.setattr(search_feedback, "table_exists", lambda name: True)
    monkeypatch.setattr(search_feedback, "_COLUMNS_READY", False)
    out = search_feedback.record_search_feedback({"source": "discovery_wall", "kol_pool_id": 3, "verdict": "up"})
    assert out == {"ok": False, "reason": "migration_290_missing", "feedback_id": None}
    assert search_feedback.count_search_feedback()["ok"] is False


def test_count_groups_by_reason_source_and_mine(monkeypatch) -> None:
    conn = _Conn()
    _wire(monkeypatch, conn)
    out = search_feedback.count_search_feedback(staff={"id": 5})
    assert out["total"] == 3 and out["down"] == 2 and out["up"] == 1 and out["mine"] == 2
    assert out["by_reason"] == {"too_small": 2} and out["by_source"] == {"discovery_wall": 2, "kol_detail": 1}
    with pytest.raises(ValueError):
        search_feedback.count_search_feedback(source="nope")


def test_router_mounts_contract_and_admin_paths() -> None:
    from app.api.routers import ADMIN_ROUTER_MODULES, vkpi_recommendations
    from app.core.release_validation import release_validation_request_allowed

    assert "vkpi_recommendations" in ADMIN_ROUTER_MODULES
    paths = {(tuple(sorted(r.methods)), r.path) for r in vkpi_recommendations.router.routes}
    assert (("POST",), "/api/admin/vkpi/recommendations/search-feedback") in paths
    assert (("POST",), "/api/vkpi/recommendations/search-feedback") in paths
    assert (("GET",), "/api/admin/vkpi/recommendations/search-feedback/count") in paths
    assert (("GET",), "/api/admin/vkpi/recommendations/search-feedback/reasons") in paths
    assert release_validation_request_allowed("GET", "/api/admin/vkpi/recommendations/search-feedback/count")
    assert release_validation_request_allowed("GET", "/api/admin/vkpi/recommendations/search-feedback/reasons")
    assert not release_validation_request_allowed("POST", "/api/admin/vkpi/recommendations/search-feedback")
    assert not release_validation_request_allowed("POST", "/api/vkpi/recommendations/search-feedback")


def test_migration_290_text_contract() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    up = (root / "migrations/290_vkpi_recommendation_feedback_search.sql").read_text(encoding="utf-8")
    down = (root / "migrations/290_vkpi_recommendation_feedback_search_down.sql").read_text(encoding="utf-8")
    assert "?" not in up and "%" not in up  # compat 适配器陷阱
    assert "DROP NOT NULL" in up and "uq_vkpi_reco_feedback_search_dedupe" in up
    for key in ("vkpi_forecast_batch_issue", "vkpi_weekly_offline_eval", "vkpi_anomaly_sentinel"):
        assert f"'{key}'" in up and f"'{key}'" in down
    assert "FALSE" in up and "ON CONFLICT (task_key) DO NOTHING" in up
    assert "SET NOT NULL" in down and "290_vkpi_recommendation_feedback_search.sql" in down
    import re

    assert not re.search(r"(?mi)^\s*(BEGIN|COMMIT)\s*;", up)


@pytest.mark.pg
def test_live_pg_search_feedback_roundtrip(pg_compat, monkeypatch) -> None:
    """真 PG:插 → 同键改判 UPDATE(不堆行)→ 计数;事务回滚不留痕。"""
    conn = pg_compat
    monkeypatch.setattr(conn, "commit", lambda: None)
    monkeypatch.setattr(search_feedback, "get_conn", lambda: conn)
    monkeypatch.setattr(search_feedback, "_COLUMNS_READY", None)
    if not search_feedback.columns_ready(force=True):
        pytest.skip("migration 290 not applied on test database")
    tag = uuid.uuid4().hex[:8]
    pool = conn.execute(
        "INSERT INTO vkpi_kol_pool (pool_uid, platform, handle) VALUES (?, 'youtube', ?) RETURNING id",
        (f"sf-{tag}", f"sf_{tag}"),
    ).fetchone()
    pool_id = int(dict(pool)["id"])
    first = search_feedback.record_search_feedback({"source": "discovery_wall", "kol_pool_id": pool_id, "verdict": "up"})
    assert first["ok"] and first["deduped"] is False
    second = search_feedback.record_search_feedback(
        {"source": "discovery_wall", "kol_pool_id": pool_id, "verdict": "down", "reason": "brand_official"},
    )
    assert second["feedback_id"] == first["feedback_id"] and second["updated"] is True
    rows = conn.execute(
        "SELECT feedback_type, reason, recommendation_id FROM vkpi_recommendation_feedback WHERE kol_pool_id=?", (pool_id,),
    ).fetchall()
    assert len(rows) == 1 and dict(rows[0])["feedback_type"] == "reject" and dict(rows[0])["reason"] == "brand_official"
    assert dict(rows[0])["recommendation_id"] is None
    counts = search_feedback.count_search_feedback(source="discovery_wall")
    assert counts["ok"] and counts["total"] >= 1 and counts["by_reason"].get("brand_official", 0) >= 1
