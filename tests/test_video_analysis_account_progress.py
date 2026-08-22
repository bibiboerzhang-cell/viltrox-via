"""account_deep 账号级进度只读汇总(已完成/进行中/失败/预计剩余)。全 fake 连接,零写零 LLM。"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import video_analysis_enqueue as enqueue


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows]

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.rows[0]) if self.rows else None


class _Conn:
    """按 SQL 片段路由的假连接;记录每条 SQL,断言只读。"""

    def __init__(
        self,
        *,
        evidence_ids: list[int],
        caches: dict[str, str],
        jobs: dict[str, dict[str, Any]],
        p50_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.evidence_ids = evidence_ids
        self.caches = caches
        self.jobs = jobs
        self.p50_rows = p50_rows if p50_rows is not None else [{"p50_ms": None, "n": 0}]
        self.sql: list[str] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        compact = " ".join(str(sql).split())
        self.sql.append(compact)
        assert not compact.upper().startswith(("INSERT", "UPDATE", "DELETE")), compact
        if "SELECT e.id AS evidence_id FROM vkpi_kol_video_evidence" in compact:
            return _Rows([{"evidence_id": eid} for eid in self.evidence_ids])
        if "FROM vkpi_kol_video_evidence WHERE id IN" in compact:
            return _Rows(
                [
                    {"id": eid, "content_url": f"https://www.youtube.com/watch?v=id{eid:08d}", "platform": "youtube", "title": f"t{eid}"}
                    for eid in params
                ]
            )
        if "FROM vkpi_analysis_cache" in compact:
            wanted = set(params[1:])
            return _Rows([{"target_id": t, "updated_at": ts} for t, ts in self.caches.items() if t in wanted])
        if "DISTINCT ON (payload->>'target_id')" in compact:
            wanted = set(params[1:])
            return _Rows([{**job, "target_id": t} for t, job in self.jobs.items() if t in wanted])
        if "percentile_cont(0.5)" in compact:
            return _Rows(self.p50_rows)
        raise AssertionError(compact)


def test_progress_counts_states_and_eta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY", "4")
    conn = _Conn(
        evidence_ids=[10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
        caches={"10": "2026-08-22T00:00:00Z", "9": "2026-08-21T00:00:00Z"},
        jobs={
            "10": {"id": 100, "status": "done", "attempts": 0, "last_error_category": None, "updated_at": "x"},
            "8": {"id": 98, "status": "running", "attempts": 0, "last_error_category": None, "updated_at": "x"},
            "7": {"id": 97, "status": "queued", "attempts": 1, "last_error_category": "download", "updated_at": "x"},
            "6": {"id": 96, "status": "queued", "attempts": 0, "last_error_category": None, "updated_at": "x"},
            "5": {"id": 95, "status": "queued", "attempts": 0, "last_error_category": None, "updated_at": "x"},
            "4": {"id": 94, "status": "queued", "attempts": 0, "last_error_category": None, "updated_at": "x"},
            "3": {"id": 93, "status": "failed", "attempts": 2, "last_error_category": "download", "updated_at": "x"},
            "2": {"id": 92, "status": "blocked", "attempts": 0, "last_error_category": "blocked", "updated_at": "x"},
        },
        p50_rows=[{"p50_ms": 40000.0, "n": 12}],
    )
    out = enqueue.account_video_analysis_progress(conn, 88, limit=20)
    assert out["kol_pool_id"] == 88 and out["derive_method"] == "video_analysis_final_v1"
    assert out["scope"] == {"limit": 20, "evidence_total": 10, "scope_total": 10}
    assert out["counts"] == {"ready": 2, "running": 1, "queued": 4, "failed": 1, "blocked": 1, "triage": 0, "not_requested": 1}
    assert (out["completed"], out["in_progress"], out["failed"], out["not_requested"]) == (2, 5, 2, 1)
    assert out["percent"] == 20 and out["state"] == "running"
    # 5 条进行中 / 4 槽位 = 2 波 × 40s
    assert out["eta"] == {
        "remaining": 5,
        "recent_p50_ms": 40000,
        "basis": "done_jobs_24h_p50",
        "effective_parallelism": 4,
        "estimated_remaining_seconds": 80,
    }
    by_id = {item["evidence_id"]: item for item in out["items"]}
    assert by_id[10]["state"] == "ready" and by_id[10]["cache_updated_at"] == "2026-08-22T00:00:00Z"
    assert by_id[7]["state"] == "queued" and by_id[7]["attempts"] == 1 and by_id[7]["last_error_category"] == "download"
    assert by_id[3]["state"] == "failed" and by_id[2]["state"] == "blocked"
    assert by_id[1]["state"] == "not_requested" and by_id[1]["job_id"] is None
    assert by_id[8]["content_url"].endswith("id00000008")
    assert out["write_db"] is False and out["provider_calls"] is False


def test_progress_scope_is_capped_like_enqueue_all_kol_videos() -> None:
    conn = _Conn(evidence_ids=list(range(30, 0, -1)), caches={str(i): "ts" for i in range(11, 31)}, jobs={})
    out = enqueue.account_video_analysis_progress(conn, 5, limit=20, include_items=False)
    assert out["scope"] == {"limit": 20, "evidence_total": 30, "scope_total": 20}
    assert out["completed"] == 20 and out["state"] == "done" and out["percent"] == 100
    assert out["items"] == []
    assert out["eta"]["basis"] == "not_needed" and out["eta"]["estimated_remaining_seconds"] is None


def test_progress_states_idle_partial_failed_and_no_evidence() -> None:
    idle = enqueue.account_video_analysis_progress(_Conn(evidence_ids=[1, 2], caches={}, jobs={}), 1)
    assert idle["state"] == "idle" and idle["not_requested"] == 2
    partial_failed = enqueue.account_video_analysis_progress(
        _Conn(
            evidence_ids=[1, 2],
            caches={"1": "ts"},
            jobs={"2": {"id": 2, "status": "triage", "attempts": 2, "last_error_category": "unknown", "updated_at": "x"}},
        ),
        1,
    )
    assert partial_failed["state"] == "partial_failed" and partial_failed["failed"] == 1
    empty = enqueue.account_video_analysis_progress(_Conn(evidence_ids=[], caches={}, jobs={}), 1)
    assert empty["state"] == "no_evidence" and empty["scope"]["scope_total"] == 0 and empty["percent"] == 0


def test_progress_eta_without_samples_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY", "garbage")
    conn = _Conn(
        evidence_ids=[1],
        caches={},
        jobs={"1": {"id": 1, "status": "queued", "attempts": 0, "last_error_category": None, "updated_at": "x"}},
        p50_rows=[{"p50_ms": None, "n": 0}],
    )
    out = enqueue.account_video_analysis_progress(conn, 1)
    assert out["state"] == "running"
    assert out["eta"]["basis"] == "no_sample" and out["eta"]["estimated_remaining_seconds"] is None
    assert out["eta"]["effective_parallelism"] == 1


def test_progress_requires_kol_pool_id() -> None:
    with pytest.raises(ValueError):
        enqueue.account_video_analysis_progress(_Conn(evidence_ids=[], caches={}, jobs={}), 0)
