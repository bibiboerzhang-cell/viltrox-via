"""O 车道单测:blocked 原因 → last_error_category 分类;进度端点门禁与契约外壳;C1 异常摘要脱敏。"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.domains.costs.budget_guard_errors import note_cost_ledger_failure, redact_secrets, summarize_exception
from app.workers.apify_jobs_worker_paid_scope import block_reason_category


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("video_analysis_authorization_fence_required", "authorization"),
        ("my_kol_paid_action_fence_required", "authorization"),
        ("content_fit_authorization_fence_required", "authorization"),
        ("my_kol_paid_action_actor_inactive", "authorization"),
        ("my_kol_paid_action_permission_revoked", "authorization"),
        ("my_kol_paid_action_evidence_target_drifted", "authorization"),
        ("project_scope_denied", "authorization"),
        ("local_evaluation_capability_required", "authorization"),
        ("release_validation_fenced", "authorization"),
        ("budget_guard_blocked", "budget"),
        ("model_binding_mismatch", "model"),
        ("execution_class_mismatch", "model"),
        ("readiness_not_production_ready", "model"),
        ("unsupported_llm_derive_method", "model"),
        ("provider_replay_blocked", "provider"),
        ("image_post_no_video", "blocked"),
        ("unsupported_platform", "blocked"),
        ("unknown_job_type", "blocked"),
        ("", "blocked"),
        (None, "blocked"),
    ],
)
def test_block_reason_category(reason: Any, expected: str) -> None:
    assert block_reason_category(reason) == expected


def test_block_job_sql_writes_categorized_last_error_category() -> None:
    """源码口径守卫:_block_job 不再把 last_error_category 钉死成 'blocked'(任务 5)。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "backend/app/workers/apify_jobs_worker.py").read_text(encoding="utf-8")
    block_src = src.split("def _block_job(")[1].split("\ndef ")[0]
    assert "last_error_category='blocked'" not in block_src
    assert "block_reason_category(reason)" in block_src


def test_summarize_exception_keeps_root_cause_and_masks_secrets() -> None:
    class ForeignKeyViolation(Exception):
        pass

    exc = ForeignKeyViolation(
        'insert or update on table "vkpi_ai_cost_ledger" violates foreign key constraint "x_fkey"\n'
        'DETAIL:  Key (staff_id)=(1) is not present in table "staff".\n'
        "HINT: token=abc123 https://user:pw@host/path Authorization: Bearer zzz"
    )
    summary = summarize_exception(exc)
    assert summary.startswith("ForeignKeyViolation: insert or update")
    assert "(staff_id)=(1)" in summary
    # 只保留前两行,第三行(含密钥)不进摘要
    assert "abc123" not in summary and "HINT" not in summary
    assert redact_secrets("token=abc123 https://user:pw@host/p Authorization: Bearer zzz api_key: sk-1") == (
        "token=*** https://***@host/p Authorization:*** api_key:***"
    )
    assert summarize_exception(ValueError("")) == "ValueError"
    assert len(summarize_exception(RuntimeError("x" * 5000))) <= len("RuntimeError: ") + 240
    noted = note_cost_ledger_failure(exc, scope="cron:x", staff_id=None, unresolved_staff_id=1)
    assert noted == summary
    assert any("cost_ledger_write_failed: ForeignKeyViolation" in note for note in exc.__notes__)


def test_progress_route_applies_target_gate_and_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routers import vkpi_kol_pool_progress as route
    from app.domains.kol.my_kol_paid_action_access import MyKolPaidActionError

    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(route, "get_conn", lambda: object())

    def fake_readable(_conn: Any, *, kol_pool_id: int, staff: Any) -> int:
        if kol_pool_id == 404:
            raise MyKolPaidActionError("kol_pool_not_found", 404)
        if kol_pool_id == 403:
            raise MyKolPaidActionError("my_kol_target_read_forbidden", 403)
        return 1

    monkeypatch.setattr(route, "assert_target_readable", fake_readable)

    def fake_progress(_conn: Any, pid: int, *, limit: int, include_items: bool) -> dict[str, Any]:
        calls.append((pid, limit, include_items))
        return {"kol_pool_id": pid, "state": "partial_failed", "eta_seconds": None, "items": [{"failure_category": "authorization"}]}

    monkeypatch.setattr(route.kol_video_analysis_enqueue, "account_video_analysis_progress", fake_progress)

    out = route.get_pool_item_video_analysis_progress(88, limit=5, include_items=False, staff={"id": 1})
    assert calls == [(88, 5, False)]
    assert out["contract"] == route.PROGRESS_CONTRACT and out["read_only"] is True
    assert out["failure_categories"] == ["download", "authorization", "budget", "model", "provider", "unknown"]
    assert out["items"][0]["failure_category"] == "authorization"
    for pid, status in ((404, 404), (403, 403)):
        with pytest.raises(HTTPException) as caught:
            route.get_pool_item_video_analysis_progress(pid, limit=5, include_items=True, staff={"id": 1})
        assert caught.value.status_code == status
    with pytest.raises(HTTPException) as bad:
        route.get_pool_item_video_analysis_progress(0, limit=5, include_items=True, staff={"id": 1})
    assert bad.value.status_code == 400


def test_progress_route_is_mounted_under_kol_pool_router() -> None:
    from app.api.routers import vkpi_kol_pool

    paths = {getattr(r, "path", "") for r in vkpi_kol_pool.router.routes}
    assert "/api/admin/vkpi/kol-pool/{kol_pool_id}/video-analysis-progress" in paths
