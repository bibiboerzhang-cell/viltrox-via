"""波 D·D2「新证据即登记」:证据写口钩子 + 日任务兜底的合同(假库,零 PG)。

- 非收藏 KOL:零写、理由 not_favorited,不查月闸;
- 收藏 KOL:过月闸 → enroll_my_kol_evidence(kol_pool_ids=[id], apply=True) 恰好一次 + commit;
- 月闸关:不登记,理由原样回传;登记炸:只 warning + rollback,理由 enroll_failed;
- 证据写口:status=created 才挂钩子;钩子炸也不影响写口返回;
- 日任务:幂等(第二次 inserted=0 / already_active),月闸关 → blocked,异常 → failed 不冒泡。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.kol import evidence_side_effects as se  # noqa: E402
from app.domains.kol import video_evidence, video_tracking_budget, video_tracking_enroll  # noqa: E402


class _FakeConn:
    def __init__(self, *, favorited: bool = True) -> None:
        self.favorited = favorited
        self.commits = 0
        self.rollbacks = 0
        self.sql: list[str] = []

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def execute(self, sql, *_args, **_kwargs):
        self.sql.append(" ".join(str(sql).split()))
        return self

    def fetchone(self):
        return {"hit": 1} if self.favorited else None

    def fetchall(self):
        return []


def test_hook_skips_unfavorited_without_touching_gate(monkeypatch) -> None:
    conn = _FakeConn(favorited=False)
    monkeypatch.setattr(video_tracking_budget, "budget_gate", lambda *a, **k: pytest.fail("gate must not run"))
    monkeypatch.setattr(video_tracking_enroll, "enroll_my_kol_evidence", lambda *a, **k: pytest.fail("must not enroll"))
    out = se.enroll_tracking_after_new_evidence(77, evidence_id=5, conn=conn)
    assert out["tracking_enroll_reason"] == "not_favorited" and out["tracking_enrolled"] == 0
    assert conn.commits == 0 and any("vkpi_kol_pool_favorites" in s for s in conn.sql)
    assert se.enroll_tracking_after_new_evidence(0, conn=conn)["tracking_enroll_reason"] == "kol_pool_id_required"


def test_hook_enrolls_favorited_kol_once_and_is_idempotent(monkeypatch) -> None:
    conn = _FakeConn()
    calls: list[dict] = []
    state = {"active": 0}

    def fake_enroll(db, **kwargs):
        calls.append(kwargs)
        inserted = 1 if state["active"] == 0 else 0
        state["active"] = 1
        return {"candidates": 1, "inserted": inserted, "already_active": 1 - inserted, "skipped": {}}

    monkeypatch.setattr(video_tracking_budget, "budget_gate", lambda c, **kw: {"allowed": True, "reason": "within_cap"})
    monkeypatch.setattr(video_tracking_enroll, "enroll_my_kol_evidence", fake_enroll)
    first = se.enroll_tracking_after_new_evidence(77, evidence_id=5, conn=conn)
    assert first["tracking_enrolled"] == 1 and first["tracking_enroll_reason"] is None and conn.commits == 1
    assert calls == [{"apply": True, "kol_pool_ids": [77]}]
    second = se.enroll_tracking_after_new_evidence(77, evidence_id=6, conn=conn)
    assert second["tracking_enrolled"] == 0 and second["tracking_enroll_reason"] == "already_enrolled" and conn.commits == 2


def test_hook_respects_budget_gate_and_logs_failures(monkeypatch, caplog) -> None:
    conn = _FakeConn()
    monkeypatch.setattr(video_tracking_budget, "budget_gate", lambda c, **kw: {"allowed": False, "reason": "hard_stop_or_projected_cap:metric_tracking"})
    monkeypatch.setattr(video_tracking_enroll, "enroll_my_kol_evidence", lambda *a, **k: pytest.fail("must not enroll"))
    with caplog.at_level(logging.WARNING):
        out = se.enroll_tracking_after_new_evidence(77, conn=conn)
    assert out["tracking_enroll_reason"] == "hard_stop_or_projected_cap:metric_tracking" and conn.commits == 0
    assert any("tracking_enroll_skipped" in r.getMessage() for r in caplog.records)

    monkeypatch.setattr(video_tracking_budget, "budget_gate", lambda c, **kw: {"allowed": True})

    def boom(*_a, **_k):
        raise RuntimeError("pg down")

    monkeypatch.setattr(video_tracking_enroll, "enroll_my_kol_evidence", boom)
    with caplog.at_level(logging.WARNING):
        out = se.enroll_tracking_after_new_evidence(77, conn=conn)
    assert out["tracking_enroll_reason"] == "enroll_failed" and conn.rollbacks == 1 and conn.commits == 0
    assert any("tracking_enroll_failed" in r.getMessage() for r in caplog.records)


def test_evidence_writer_calls_hook_only_on_created(monkeypatch) -> None:
    calls: list[tuple[int, int | None]] = []
    monkeypatch.setattr(video_evidence, "_load_kol", lambda db, kid: {"id": kid})
    monkeypatch.setattr(video_evidence, "_table_columns", lambda db, table: {"id", "kol_pool_id", "content_url", "platform", "title", "video_title", "created_at", "updated_at", "source", "source_ref"})
    existing: dict = {}
    monkeypatch.setattr(video_evidence, "_load_existing_evidence", lambda db, url, *, kol_pool_id: existing.get(url))
    monkeypatch.setattr(video_evidence, "_fetch_video_metadata", lambda url: {"content_url": url, "platform": "youtube", "title": "t"})
    monkeypatch.setattr(video_evidence, "_score_snapshot", lambda db, ids: {})
    monkeypatch.setattr(video_evidence, "_insert_evidence", lambda db, values: 901)
    monkeypatch.setattr(video_evidence, "_update_evidence", lambda db, eid, values: None)
    monkeypatch.setattr(video_evidence, "_commit", lambda db: None)
    monkeypatch.setattr(video_evidence, "_rollback", lambda db: None)
    monkeypatch.setattr(se, "enroll_tracking_after_new_evidence", lambda kid, *, evidence_id=None, conn=None: calls.append((kid, evidence_id)) or {"tracking_enrolled": 1, "tracking_enroll_reason": None})

    created = video_evidence.ensure_video_evidence_from_url(9, "https://www.youtube.com/watch?v=abc", dry_run=False, conn=object())
    assert created["status"] == "created" and created["evidence_id"] == 901
    assert created["tracking_enroll"]["tracking_enrolled"] == 1 and calls == [(9, 901)]

    existing["https://www.youtube.com/watch?v=abc"] = {"id": 901, "kol_pool_id": 9}
    reused = video_evidence.ensure_video_evidence_from_url(9, "https://www.youtube.com/watch?v=abc", dry_run=False, conn=object())
    assert reused["status"] == "reused" and "tracking_enroll" not in reused and calls == [(9, 901)]

    dry = video_evidence.ensure_video_evidence_from_url(9, "https://www.youtube.com/watch?v=xyz", dry_run=True, conn=object())
    assert dry["status"] == "would_create" and "tracking_enroll" not in dry and calls == [(9, 901)]


def test_daily_auto_enroll_is_idempotent_gated_and_never_raises(monkeypatch, caplog) -> None:
    conn = _FakeConn()
    state = {"runs": 0}

    def fake_enroll(db, **kwargs):
        state["runs"] += 1
        inserted = 3 if state["runs"] == 1 else 0
        return {"candidates": 3, "to_register": inserted, "inserted": inserted, "already_active": 3 - inserted, "skipped": {}, "tiers": {"hot": 1, "warm": 2, "cold": 0}}

    monkeypatch.setattr(video_tracking_budget, "budget_gate", lambda c, **kw: {"allowed": True})
    monkeypatch.setattr(video_tracking_enroll, "enroll_my_kol_evidence", fake_enroll)
    first = se.run_tracking_auto_enroll(conn=conn)
    assert first["status"] == "ok" and first["inserted"] == 3 and first["provider_calls_performed"] is False and conn.commits == 1
    second = se.run_tracking_auto_enroll(conn=conn)
    assert second["status"] == "ok" and second["inserted"] == 0 and second["already_active"] == 3 and conn.commits == 2

    monkeypatch.setattr(video_tracking_budget, "budget_gate", lambda c, **kw: {"allowed": False, "reason": "budget_scope_not_configured"})
    blocked = se.run_tracking_auto_enroll(conn=conn)
    assert blocked["status"] == "blocked" and blocked["reason"] == "budget_scope_not_configured" and state["runs"] == 2

    monkeypatch.setattr(video_tracking_budget, "budget_gate", lambda c, **kw: {"allowed": True})
    monkeypatch.setattr(video_tracking_enroll, "enroll_my_kol_evidence", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pg down")))
    with caplog.at_level(logging.WARNING):
        failed = se.run_tracking_auto_enroll(conn=conn)
    assert failed["status"] == "failed" and failed["error_code"] == "runtimeerror" and conn.rollbacks == 1
    assert any("tracking_auto_enroll.failed" in r.getMessage() for r in caplog.records)
