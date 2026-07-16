"""U1 · 顶栏全局任务进度中心端点冒烟(admin 身份,真库只读)。

配方同 test_w5_route_smoke.py:middleware + require_tab 两道 seam 伪造 admin,
TestClient 打真 Postgres(纯读路径)。断言:
  - GET /api/admin/vkpi/progress/center 200;
  - 契约字段齐(counts/running/queued/recent_done/recent_llm/stage_flow);
  - 纯读诊断位(write_db/llm_calls/worker_touched 全 False);
  - recent_done/recent_llm 各 ≤ 5;
红线:零写库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


_ADMIN_USER = {"id": 1, "email": "admin@u1.test", "role": "admin"}
_ADMIN_STAFF = {
    "id": 1,
    "staff_id": 1,
    "user_id": 1,
    "role": "admin",
    "is_owner": 1,
    "permissions": {"vkpi": "admin"},
    "email": "admin@u1.test",
}


def test_running_progress_is_explicitly_estimated_and_overdue_is_indeterminate():
    from app.api.routers.vkpi_progress_center import _running_progress

    now = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)
    item = {"id": "7", "job_type": "kol_profile_deep_crawl"}

    pct, eta, overdue = _running_progress(
        item,
        {"7": now - timedelta(seconds=30)},
        {"kol_profile_deep_crawl": 120.0},
        now,
    )
    assert pct == 25
    assert eta == 90
    assert overdue is False

    pct, eta, overdue = _running_progress(
        item,
        {"7": now - timedelta(seconds=121)},
        {"kol_profile_deep_crawl": 120.0},
        now,
    )
    assert pct is None
    assert eta is None
    assert overdue is True


def test_llm_reason_code_is_bounded_and_prioritizes_production_readiness():
    from app.domains.tasks.queue_view import (
        _authoritative_llm_status,
        _llm_reason_code,
        _runtime_reason_contract,
    )

    reason = _llm_reason_code(
        "all_providers_failed",
        {
            "reason": "readiness_not_production_ready",
            "attempt_errors": [{"error": "provider_exception: raw secret-like body"}],
        },
    )
    assert reason == "readiness_not_production_ready"
    assert "secret" not in reason
    assert _llm_reason_code("budget_disabled", {}) == "budget_blocked"
    assert _llm_reason_code(
        "all_providers_failed",
        {"errors": [{"status": "provider_exception", "error": "raw provider body"}]},
    ) == "provider_unavailable"
    not_configured = _runtime_reason_contract(
        "all_providers_failed",
        {"errors": [{"status": "not_configured"}]},
    )
    assert _authoritative_llm_status("all_providers_failed", not_configured) == "blocked"
    provider_failed = _runtime_reason_contract(
        "all_providers_failed",
        {"errors": [{"status": "provider_exception"}]},
    )
    assert _authoritative_llm_status("all_providers_failed", provider_failed) == "failed"
    assert _llm_reason_code("disabled", {"reason": "VKPI_WEEKLY_SUMMARY_AI_DISABLED"}) == "operator_disabled"
    assert _llm_reason_code("unknown", {"error": "do not expose me"}) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("success", "done"),
        ("completed", "done"),
        ("in_progress", "running"),
        ("started", "running"),
        ("retrying", "retrying"),
        ("ai_budget_hard_stop", "blocked"),
        ("budget_disabled", "blocked"),
        ("not_configured", "blocked"),
        ("readiness_not_production_ready", "blocked"),
        ("model_binding_blocked", "blocked"),
        ("all_providers_failed", "failed"),
        ("provider_error", "failed"),
        ("canceled", "cancelled"),
        ("triage", "triage"),
    ],
)
def test_task_status_contract_keeps_terminal_states_out_of_running(raw, expected):
    from app.domains.tasks.queue_view import _infer_stage, _normal_status

    status = _normal_status(raw)
    assert status == expected
    if status in {"blocked", "failed", "cancelled", "timeout", "triage"}:
        assert status != "running"
    if status == "retrying":
        assert _infer_stage(status, "账号分析", job_type="kol_profile_deep_crawl") == "queued"


def test_retrying_rolls_up_as_queued_not_running():
    from app.domains.tasks.queue_view import _rollup_active_counts

    assert _rollup_active_counts(
        Counter({"queued": 2, "retrying": 3, "running": 4, "processing": 2, "started": 3})
    ) == (5, 9, 14)


def test_llm_reservations_expose_real_inflight_and_unknown_is_triage(monkeypatch):
    import app.domains.tasks.queue_view as queue_view

    now = datetime.now(timezone.utc)

    class FakeConn:
        def execute(self, _sql, _params=()):
            return self

        def fetchall(self):
            return [
                {
                    "reservation_key": "llmres-queued",
                    "provider": "openai",
                    "model_name": "gpt-exact",
                    "purpose": "vkpi_query_plan",
                    "state": "reserved",
                    "metadata_json": '{"request_content_recorded":false}',
                    "reserved_at": now,
                    "provider_started_at": None,
                    "updated_at": now,
                },
                {
                    "reservation_key": "llmres-running",
                    "provider": "google",
                    "model_name": "gemini-exact",
                    "purpose": "video_analysis_final_v1",
                    "state": "provider_started",
                    "metadata_json": '{"task_binding":"audit_video_analysis","target_label":"video:42","parent_job_id":42,"phase":"evaluation","subphase":"provider_generation","attempt_index":1,"attempt_total":2,"prompt":"must-not-leak"}',
                    "reserved_at": now,
                    "provider_started_at": now,
                    "updated_at": now,
                },
                {
                    "reservation_key": "llmres-unknown",
                    "provider": "anthropic",
                    "model_name": "claude-exact",
                    "purpose": "marketing_advisor",
                    "state": "unknown",
                    "metadata_json": "{}",
                    "reserved_at": now,
                    "provider_started_at": now,
                    "updated_at": now,
                },
            ]

    monkeypatch.setattr(queue_view, "table_exists", lambda name: name == "vkpi_llm_budget_reservations")
    monkeypatch.setattr(queue_view, "get_conn", lambda: FakeConn())
    active, recent = queue_view._query_llm_reservations(
        now - timedelta(minutes=5), 20
    )

    assert [item["status"] for item in active] == ["queued", "running"]
    assert active[1]["source"] == "llm_reservations"
    assert active[1]["provider"] == "google"
    assert active[1]["model"] == "gemini-exact"
    assert active[1]["task_binding"] == "audit_video_analysis"
    assert active[1]["target"]["label"] == "video:42"
    assert active[1]["stage"] == "thinking"
    assert active[1]["parent_job_id"] == 42
    assert active[1]["phase"] == "evaluation"
    assert active[1]["subphase"] == "provider_generation"
    assert active[1]["attempt_index"] == 1
    assert active[1]["attempt_total"] == 2
    assert [item["status"] for item in recent] == ["triage"]
    assert recent[0]["reason_code"] == "reservation_outcome_unknown"
    for item in [*active, *recent]:
        assert "prompt" not in item
        assert "request_hash" not in item


def test_completed_llm_call_keeps_safe_task_and_phase_mapping(monkeypatch):
    import app.domains.tasks.queue_view as queue_view

    now = datetime.now(timezone.utc)

    class FakeConn:
        def execute(self, _sql, _params=()):
            return self

        def fetchall(self):
            return [
                {
                    "id": 91,
                    "call_uid": "llm-call-91",
                    "provider": "openai",
                    "model": "gpt-exact",
                    "purpose": "vkpi_kol_content_fit",
                    "status": "success",
                    "fallback_used": False,
                    "created_at": now,
                    "metadata_json": '{"task_binding":"kol_content_fit_analysis","target_label":"creator:77","parent_job_id":77,"phase":"evaluation","subphase":"provider_generation","attempt_index":2,"total":3,"prompt":"must-not-leak"}',
                    "latency_ms": 900,
                    "cost_cents": 2,
                }
            ]

    monkeypatch.setattr(queue_view, "get_conn", lambda: FakeConn())
    active, recent = queue_view._query_llm_calls(
        now - timedelta(minutes=5), 20, 200
    )

    assert active == []
    assert len(recent) == 1
    item = recent[0]
    assert item["status"] == "done"
    assert item["task_binding"] == "kol_content_fit_analysis"
    assert item["target"]["label"] == "creator:77"
    assert item["parent_job_id"] == 77
    assert item["phase"] == "evaluation"
    assert item["subphase"] == "provider_generation"
    assert item["attempt_index"] == 2
    assert item["attempt_total"] == 3
    assert "prompt" not in item


def test_progress_center_preserves_safe_llm_task_binding():
    from app.api.routers.vkpi_progress_center import _project_recent, _project_task

    item = {
        "id": "llmres-1",
        "source": "llm_reservations",
        "kind": "LLM分析",
        "job_type": "vkpi_kol_content_fit",
        "target": {"label": "creator:77"},
        "status": "running",
        "task_binding": "kol_content_fit_analysis",
    }
    assert _project_task(item)["task_binding"] == "kol_content_fit_analysis"
    assert _project_recent(item)["task_binding"] == "kol_content_fit_analysis"


def test_persisted_gate_wrapper_projects_exact_readiness_reason():
    from app.domains.tasks.queue_view import _reason_projection

    projected = _reason_projection(
        "blocked",
        '{"reason":"budget_guard_blocked","reason_detail":"readiness_not_production_ready"}',
        "blocked",
    )
    assert projected == {
        "reason_code": "readiness_not_production_ready",
        "reason_category": "readiness",
        "reason_retryable": False,
    }


def test_progress_center_never_projects_terminal_or_retrying_as_running(monkeypatch):
    import app.api.routers.vkpi_progress_center as progress_center

    def task(status: str, row_id: str) -> dict:
        return {
            "id": row_id,
            "source": "apify_jobs",
            "kind": "账号分析",
            "job_type": "kol_profile_deep_crawl",
            "target": {"label": row_id},
            "status": status,
            "stage": "queued" if status in {"queued", "retrying"} else "thinking",
            "stage_label": "排队" if status in {"queued", "retrying"} else "思考",
            "created_at": "2026-07-15T00:00:00+00:00",
            "updated_at": "2026-07-15T00:01:00+00:00",
        }

    snapshot = {
        "active": [
            task("running", "running"),
            task("processing", "processing"),
            task("queued", "queued"),
            task("retrying", "retrying"),
            # Defensive projection: even if an upstream source accidentally
            # returns terminal rows in active, the center must not show them.
            task("blocked", "blocked"),
            task("failed", "failed"),
            task("cancelled", "cancelled"),
        ],
        "recent": [],
        "counts": {"running": 2, "queued": 2, "active_total": 4, "recent_total": 0},
        "diagnostics": {"worker_online": True},
    }
    monkeypatch.setattr(progress_center.task_queue_view, "get_task_queue", lambda **_kwargs: snapshot)
    monkeypatch.setattr(progress_center, "get_conn", lambda: object())
    monkeypatch.setattr(progress_center.task_queue_view, "_avg_duration_by_job_type", lambda _conn: {})
    monkeypatch.setattr(progress_center, "_started_at_by_apify_id", lambda _conn: {})

    payload = progress_center._build_center_payload(None, 20, 120)
    assert [row["status"] for row in payload["running"]] == ["running", "processing"]
    assert [row["status"] for row in payload["queued"]] == ["queued", "retrying"]


@pytest.mark.parametrize(
    ("status", "has_error"),
    [
        ("done", False),
        ("blocked", False),
        ("cancelled", False),
        ("partial_done", False),
        ("prefilter_rejected", False),
        ("failed", True),
        ("timeout", True),
        ("triage", True),
    ],
)
def test_recent_projection_distinguishes_non_error_terminal_states(status, has_error):
    from app.api.routers.vkpi_progress_center import _project_recent

    row = _project_recent({"id": status, "status": status, "error": "persisted detail"})
    assert row["has_error"] is has_error


@pytest.fixture(scope="module")
def admin_client():
    import app.main as main_mod
    from app.main import app
    import app.api.dependencies.perms as perms_mod
    from app.api.dependencies.auth import get_user_required
    from fastapi.testclient import TestClient

    saved = {
        "main_gcu": main_mod.get_current_user,
        "main_scfu": main_mod.staff_context_for_user,
        "perms_scfu": perms_mod.staff_context_for_user,
        "overrides": dict(app.dependency_overrides),
    }
    main_mod.get_current_user = lambda request: _ADMIN_USER
    main_mod.staff_context_for_user = lambda user: _ADMIN_STAFF
    perms_mod.staff_context_for_user = lambda user: _ADMIN_STAFF
    app.dependency_overrides[get_user_required] = lambda: _ADMIN_USER

    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        main_mod.get_current_user = saved["main_gcu"]
        main_mod.staff_context_for_user = saved["main_scfu"]
        perms_mod.staff_context_for_user = saved["perms_scfu"]
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved["overrides"])


def test_progress_center_route_mounted():
    """注册表制挂载生效:路由存在于 app.routes(ADMIN_ROUTER_MODULES append)。"""
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/admin/vkpi/progress/center" in paths


def test_progress_center_stream_snapshot_uses_short_lived_db_scope(monkeypatch):
    from contextlib import contextmanager

    import app.api.routers.vkpi_progress_center as progress_center

    lifecycle: list[str] = []

    @contextmanager
    def bounded_scope():
        lifecycle.append("enter")
        yield None
        lifecycle.append("exit")

    monkeypatch.setattr(progress_center, "db_connection_sync_scope", bounded_scope)
    monkeypatch.setattr(
        progress_center,
        "_build_center_payload",
        lambda viewer, limit, recent_minutes: {
            "viewer": viewer,
            "limit": limit,
            "recent_minutes": recent_minutes,
        },
    )

    payload = progress_center._build_center_payload_bounded({"id": 7}, 20, 120)

    assert payload == {"viewer": {"id": 7}, "limit": 20, "recent_minutes": 120}
    assert lifecycle == ["enter", "exit"]


@pytest.mark.pg
def test_progress_center_contract(admin_client):
    resp = admin_client.get("/api/admin/vkpi/progress/center")
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()

    assert body.get("status") == "ready"
    counts = body.get("counts") or {}
    for key in ("running", "queued", "active_total", "recent_total"):
        assert isinstance(counts.get(key), int), f"counts.{key} 应为 int: {counts}"

    for key in ("running", "queued", "recent_done", "recent_llm", "stage_flow"):
        assert isinstance(body.get(key), list), f"{key} 应为 list"
    assert len(body["recent_done"]) <= 5
    assert len(body["recent_llm"]) <= 5

    # 阶段流契约(前端 4 步文案同源):队列中→抓取→分析→落库
    stages = [s.get("stage") for s in body["stage_flow"]]
    assert stages == ["queued", "search", "thinking", "summarizing"]

    diagnostics = body.get("diagnostics") or {}
    assert isinstance(diagnostics.get("worker_online"), bool)
    assert diagnostics.get("write_db") is False
    assert diagnostics.get("llm_calls") is False
    assert diagnostics.get("worker_touched") is False
    assert diagnostics.get("llm_visibility") in {
        "gateway_outcomes_plus_strict_reservations",
        "gateway_outcomes_only_reservation_schema_unavailable",
    }
    assert isinstance(diagnostics.get("llm_reservation_schema_available"), bool)


@pytest.mark.pg
def test_progress_center_task_shapes(admin_client):
    """真库出真任务数:跑中/排队行字段形状(有数据才逐行断言,空库不误伤)。"""
    resp = admin_client.get("/api/admin/vkpi/progress/center")
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()

    for task in body.get("running") or []:
        assert task.get("status") in ("running", "processing")
        pct = task.get("progress_pct")
        assert pct is None or (isinstance(pct, int) and 0 <= pct <= 94)
        assert isinstance(task.get("progress_overdue"), bool)
        if task.get("progress_overdue"):
            assert pct is None
            assert task.get("eta_seconds") is None
            assert task.get("progress_label") == "已超历史均时"
        assert task.get("stage") in ("queued", "search", "thinking", "summarizing")
    for task in body.get("queued") or []:
        assert task.get("status") in ("queued", "retrying")
        assert task.get("progress_pct") == 0
        eta = task.get("eta_seconds")
        assert eta is None or isinstance(eta, (int, float))
    for item in body.get("recent_done") or []:
        assert isinstance(item.get("id"), str)
        assert isinstance(item.get("has_error"), bool)
    for item in body.get("recent_llm") or []:
        assert item.get("source") == "llm_calls"
        assert isinstance(item.get("id"), str)
        assert isinstance(item.get("has_error"), bool)
        # 展示级投影不允许透传 prompt/原始 provider exception。
        assert "prompt" not in item
        assert "error" not in item


@pytest.mark.pg
def test_progress_center_respects_limit(admin_client):
    resp = admin_client.get("/api/admin/vkpi/progress/center?limit=1&recent_minutes=120")
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert len(body.get("running") or []) <= 1
    assert len(body.get("queued") or []) <= 1
    # limit 越界被 FastAPI 校验拦下(ge=1/le=50)
    resp_bad = admin_client.get("/api/admin/vkpi/progress/center?limit=0")
    assert resp_bad.status_code == 422
