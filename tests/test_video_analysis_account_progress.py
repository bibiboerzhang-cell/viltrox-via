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
        heartbeats: int = 0,
        lease_owners: int = 0,
        queue_ahead: int = 0,
    ) -> None:
        self.evidence_ids = evidence_ids
        self.caches = caches
        self.jobs = jobs
        self.p50_rows = p50_rows if p50_rows is not None else [{"p50_ms": None, "n": 0}]
        self.heartbeats = heartbeats
        self.lease_owners = lease_owners
        self.queue_ahead = queue_ahead
        self.sql: list[str] = []
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1

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
        if "FROM vkpi_worker_heartbeat" in compact:
            return _Rows([{"n": self.heartbeats}])
        if "COUNT(DISTINCT lease_owner)" in compact:
            return _Rows([{"n": self.lease_owners}])
        if "created_at < ?" in compact:
            assert params[0] == "video_analysis_final_v1"
            return _Rows([{"n": self.queue_ahead}])
        raise AssertionError(compact)


def test_progress_counts_states_and_eta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY", "1")
    fence_blocked = '{"reason": "video_analysis_authorization_fence_required", "provider_calls_performed": false}'
    conn = _Conn(
        evidence_ids=[10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
        caches={"10": "2026-08-22T00:00:00Z", "9": "2026-08-21T00:00:00Z"},
        jobs={
            "10": {"id": 100, "status": "done", "attempts": 0, "last_error_category": None, "updated_at": "x"},
            "8": {"id": 98, "status": "running", "attempts": 0, "last_error_category": None, "updated_at": "x"},
            "7": {"id": 97, "status": "queued", "attempts": 1, "last_error_category": "download", "updated_at": "x", "created_at": "2026-08-22T01:00:00Z"},
            "6": {"id": 96, "status": "queued", "attempts": 0, "last_error_category": None, "updated_at": "x", "created_at": "2026-08-22T00:30:00Z"},
            "5": {"id": 95, "status": "queued", "attempts": 0, "last_error_category": None, "updated_at": "x", "created_at": "2026-08-22T00:40:00Z"},
            "4": {"id": 94, "status": "queued", "attempts": 0, "last_error_category": None, "updated_at": "x", "created_at": "2026-08-22T00:50:00Z"},
            "3": {"id": 93, "status": "failed", "attempts": 2, "last_error_category": "download", "updated_at": "x", "last_error": "RuntimeError: direct_video_download_failed: proxy 522"},
            "2": {"id": 92, "status": "blocked", "attempts": 0, "last_error_category": "blocked", "updated_at": "x", "last_error": fence_blocked},
        },
        p50_rows=[{"p50_ms": 40000.0, "n": 12}],
        heartbeats=4,
        queue_ahead=3,
    )
    out = enqueue.account_video_analysis_progress(conn, 88, limit=20)
    assert out["kol_pool_id"] == 88 and out["derive_method"] == "video_analysis_final_v1"
    assert out["scope"] == {"limit": 20, "evidence_total": 10, "scope_total": 10}
    assert out["counts"] == {"ready": 2, "running": 1, "queued": 4, "failed": 1, "blocked": 1, "triage": 0, "not_requested": 1}
    assert (out["completed"], out["in_progress"], out["failed"], out["not_requested"]) == (2, 5, 2, 1)
    assert out["percent"] == 20 and out["state"] == "running"
    # F7:(前方 3 + 本账号 5 条进行中) / 4 条活跃车道(心跳)= 2 波 × 40s;env 槽位提示(1)不再是口径
    assert out["eta_seconds"] == 80
    assert out["eta"] == {
        "remaining": 5,
        "queue_ahead": 3,
        "recent_p50_ms": 40000,
        "basis": "done_jobs_24h_p50",
        "active_lanes": 4,
        "lanes_basis": "worker_heartbeat",
        "effective_parallelism": 4,
        "estimated_remaining_seconds": 80,
    }
    # 队列位置按本账号最早 queued 任务的 created_at 取
    ahead_sql = [sql for sql in conn.sql if "created_at < ?" in sql]
    assert len(ahead_sql) == 1
    by_id = {item["evidence_id"]: item for item in out["items"]}
    assert by_id[10]["state"] == "ready" and by_id[10]["cache_updated_at"] == "2026-08-22T00:00:00Z"
    assert by_id[7]["state"] == "queued" and by_id[7]["attempts"] == 1 and by_id[7]["last_error_category"] == "download"
    assert by_id[3]["state"] == "failed" and by_id[2]["state"] == "blocked"
    # F3:失败项可读化(O→F 契约字段)
    assert by_id[3]["failure_category"] == "download"
    assert by_id[3]["failure_reason_human"] == "视频下载失败:平台限制或代理不稳"
    assert by_id[2]["failure_category"] == "authorization"
    assert by_id[2]["failure_reason_human"] == "授权围栏缺失:请从 MY KOL 页重新发起"
    assert by_id[2]["failure_code"] == "video_analysis_authorization_fence_required"
    assert by_id[8]["failure_category"] is None and by_id[8]["failure_reason_human"] is None
    # 重试中的 queued 项也带上一次失败原因(不再只显示"排队中")
    assert by_id[7]["failure_category"] == "download"
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
    assert out["eta_seconds"] is None and out["eta"]["lanes_basis"] == "not_needed"


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
    assert out["eta_seconds"] is None
    # 无心跳、无 running 租约 → 退 env 提示(解析失败按 1),口径诚实写明
    assert out["eta"]["active_lanes"] == 1 and out["eta"]["lanes_basis"] == "env_concurrency_hint"
    assert out["eta"]["effective_parallelism"] == 1


def test_progress_lanes_fall_back_to_running_lease_owners() -> None:
    conn = _Conn(
        evidence_ids=[1, 2],
        caches={},
        jobs={
            "1": {"id": 1, "status": "queued", "attempts": 0, "last_error_category": None, "updated_at": "x", "created_at": "2026-08-22T00:00:00Z"},
            "2": {"id": 2, "status": "running", "attempts": 0, "last_error_category": None, "updated_at": "x"},
        },
        p50_rows=[{"p50_ms": 30000.0, "n": 3}],
        heartbeats=0,
        lease_owners=2,
        queue_ahead=4,
    )
    out = enqueue.account_video_analysis_progress(conn, 1)
    # (4 前方 + 2 本账号) / 2 车道 = 3 波 × 30s
    assert out["eta"]["active_lanes"] == 2 and out["eta"]["lanes_basis"] == "running_lease_owners"
    assert out["eta_seconds"] == 90 and out["eta"]["queue_ahead"] == 4


def test_progress_failure_fields_cover_every_category() -> None:
    from app.domains.kol import video_analysis_progress_reasons as reasons

    cases = {
        ("blocked", "budget", '{"reason": "budget_guard_blocked", "reason_detail": "budget_exhausted"}'): ("budget", "预算已达上限"),
        ("blocked", "model", '{"reason": "model_binding_mismatch"}'): ("model", "分析模型暂不可用:请稍后重试"),
        ("triage", "provider_pressure", "RuntimeError: 429 resource_exhausted"): ("provider", "分析服务繁忙:多次重试仍失败,请稍后重新发起"),
        ("queued", "timeout", "gemini_call_timeout"): ("provider", "分析服务响应超时:会自动重试"),
        ("triage", "content_restricted", "private video"): ("download", "视频需登录或为私密内容,无法获取"),
        ("triage", "content_unavailable", "video unavailable 404"): ("download", "视频已删除或不存在"),
        ("blocked", "blocked", '{"reason": "image_post_no_video"}'): ("download", "该链接不是可分析的视频"),
        ("failed", "code_error", "TypeError: bad"): ("unknown", "分析程序出错:已记录,请联系管理员"),
        ("failed", None, "forced_ai_cost_ledger_write_failed: ForeignKeyViolation: staff_id"): ("unknown", "记账校验失败:已记录,请联系管理员"),
        ("failed", "unknown", "something odd"): ("unknown", "分析未完成:原因待排查"),
        ("blocked", "authorization", '{"reason": "my_kol_paid_action_actor_inactive"}'): ("authorization", "发起人权限已变更:请用有权限的账号重新发起"),
        ("blocked", "blocked", '{"reason": "release_validation_fenced"}'): ("authorization", "系统正在发布验证,付费分析暂停:请稍后重新发起"),
    }
    for (status, category, text), expected in cases.items():
        fields = reasons.failure_fields(status=status, last_error_category=category, last_error=text)
        assert (fields["failure_category"], fields["failure_reason_human"]) == expected, (status, category, text, fields)
        assert fields["failure_category"] in reasons.FAILURE_CATEGORIES
        # 门面零内部术语:中文句子里不出现 LLM/gemini/fence/json 等词
        assert not any(term in fields["failure_reason_human"].lower() for term in ("llm", "gemini", "fence", "json", "apify"))
    # 子进程 stderr 尾巴(V→O 契约 child_stderr_tail)可参与兜底分类
    stderr_fields = reasons.failure_fields(status="failed", last_error_category="unknown", last_error="Gemini video analysis failed: not_analyzed", stderr_tail="psycopg.errors.ForeignKeyViolation: ... staff_id")
    assert stderr_fields["failure_category"] == "unknown" and "记账" in stderr_fields["failure_reason_human"]
    assert reasons.failure_fields(status="running", last_error_category=None, last_error=None) == {"failure_category": None, "failure_reason_human": None, "failure_code": None}


def test_progress_requires_kol_pool_id() -> None:
    with pytest.raises(ValueError):
        enqueue.account_video_analysis_progress(_Conn(evidence_ids=[], caches={}, jobs={}), 0)
