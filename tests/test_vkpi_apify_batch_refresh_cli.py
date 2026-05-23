from __future__ import annotations

import asyncio
import json

from scripts import vkpi_apify_batch_refresh


def test_cli_run_blocks_provider_calls_by_default(monkeypatch) -> None:
    calls: dict[str, object] = {}
    monkeypatch.delenv("APIFY_TOKEN", raising=False)

    def fake_plan(**kwargs):
        calls["plan"] = kwargs
        return {
            "strategy": "apify_batch_first",
            "max_concurrent_runs": 2,
            "selector_ready": True,
            "source_total": 3,
            "total_targets": 1,
            "batch_count": 1,
            "platforms": {"instagram": 1},
            "skipped": [{"reason": "unsupported_platform"}],
            "batches": [{"batch_key": "instagram-1", "platform": "instagram", "targets": [{"kol_pool_id": 1}]}],
        }

    async def fake_execute(plan, **kwargs):
        calls["execute"] = kwargs
        return {
            "executed": False,
            "reason": "provider_calls_not_allowed",
            "batch_count": len(plan["batches"]),
            "summary": {"retry_count": 0, "failed_batches": 0},
        }

    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "qualified_apify_batch_plan", fake_plan)
    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "execute_apify_batch_plan", fake_execute)
    args = vkpi_apify_batch_refresh.parse_args(["--limit", "10", "--platforms", "instagram", "--compact"])

    result = asyncio.run(vkpi_apify_batch_refresh.run_from_args(args))

    assert result["mode"] == "plan_with_blocked_executor"
    assert result["provider_calls_allowed"] is False
    assert result["execution"]["executed"] is False
    assert calls["execute"]["allow_provider_calls"] is False
    assert calls["plan"]["limit"] == 10
    assert calls["plan"]["platforms"] == {"instagram"}
    assert result["plan"]["batches"][0] == {"batch_key": "instagram-1", "platform": "instagram", "target_count": None, "actor_id": None, "kol_pool_ids": None}
    assert result["operator_summary"] == {
        "readiness": "blocked_provider_calls",
        "mode": "plan_with_blocked_executor",
        "provider_calls_requested": False,
        "provider_calls_allowed": False,
        "provider_gate_reason": "provider_calls_not_requested",
        "provider_configured": False,
        "missing_provider_platforms": ["instagram"],
        "execution_preflight_status": "provider_not_configured",
        "can_execute_if_authorized": False,
        "live_target_cap": 25,
        "selector_ready": True,
        "source_total": 3,
        "target_count": 1,
        "batch_count": 1,
        "platforms": {"instagram": 1},
        "skipped_count": 1,
        "skipped_reasons": {"unsupported_platform": 1},
        "executed": False,
        "execution_reason": "provider_calls_not_allowed",
        "retry_count": 0,
        "failed_batches": 0,
    }


def test_cli_requires_both_execute_and_allow_provider_calls(monkeypatch) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setenv("APIFY_TOKEN", "test-token")

    def fake_plan(**_kwargs):
        return {"strategy": "apify_batch_first", "max_concurrent_runs": 2, "selector_ready": True, "total_targets": 1, "batch_count": 1, "platforms": {"youtube": 1}, "batches": []}

    async def fake_execute(_plan, **kwargs):
        calls["execute"] = kwargs
        return {"executed": bool(kwargs.get("allow_provider_calls"))}

    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "qualified_apify_batch_plan", fake_plan)
    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "execute_apify_batch_plan", fake_execute)

    execute_only = asyncio.run(vkpi_apify_batch_refresh.run_from_args(vkpi_apify_batch_refresh.parse_args(["--execute"])))
    execute_allowed = asyncio.run(vkpi_apify_batch_refresh.run_from_args(vkpi_apify_batch_refresh.parse_args(["--execute", "--allow-provider-calls"])))

    assert execute_only["provider_calls_allowed"] is False
    assert execute_only["execution"]["executed"] is False
    assert execute_allowed["provider_calls_allowed"] is True
    assert execute_allowed["execution"]["executed"] is True
    assert execute_allowed["provider_gate"]["reason"] == "allowed"
    assert execute_allowed["execution_preflight"]["status"] == "ready"
    assert execute_allowed["operator_summary"]["can_execute_if_authorized"] is True


def test_cli_writes_operator_artifact(monkeypatch, tmp_path, capsys) -> None:
    artifact = tmp_path / "batch-plan.json"
    calls: dict[str, object] = {}

    def fake_plan(**_kwargs):
        return {"strategy": "apify_batch_first", "max_concurrent_runs": 2, "batches": []}

    async def fake_execute(_plan, **kwargs):
        calls["execute"] = kwargs
        return {"executed": False, "reason": "provider_calls_not_allowed"}

    async def fake_close():
        calls["closed"] = True

    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "qualified_apify_batch_plan", fake_plan)
    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "execute_apify_batch_plan", fake_execute)
    monkeypatch.setattr(vkpi_apify_batch_refresh, "close_db_runtime", fake_close)

    code = asyncio.run(vkpi_apify_batch_refresh.async_main(["--compact", "--json-out", str(artifact)]))

    assert code == 0
    assert calls["execute"]["allow_provider_calls"] is False
    assert calls["closed"] is True
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert payload["artifact"]["path"] == str(artifact)
    assert payload["provider_calls_allowed"] is False
    assert payload["operator_summary"]["readiness"] == "blocked_provider_calls"
    assert printed["artifact"]["path"] == str(artifact)


def test_cli_blocks_live_execution_above_target_cap(monkeypatch) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setenv("APIFY_TOKEN", "test-token")

    def fake_plan(**_kwargs):
        return {
            "strategy": "apify_batch_first",
            "max_concurrent_runs": 2,
            "selector_ready": True,
            "source_total": 26,
            "total_targets": 26,
            "batch_count": 1,
            "platforms": {"youtube": 26},
            "batches": [{"batch_key": "youtube-1", "platform": "youtube", "targets": [{"kol_pool_id": item} for item in range(1, 27)]}],
        }

    async def fake_execute(_plan, **kwargs):
        calls["execute"] = kwargs
        return {"executed": False, "reason": "provider_calls_not_allowed", "summary": {"retry_count": 0, "failed_batches": 0}}

    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "qualified_apify_batch_plan", fake_plan)
    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "execute_apify_batch_plan", fake_execute)

    result = asyncio.run(vkpi_apify_batch_refresh.run_from_args(vkpi_apify_batch_refresh.parse_args(["--execute", "--allow-provider-calls"])))

    assert calls["execute"]["allow_provider_calls"] is False
    assert result["provider_gate"]["requested"] is True
    assert result["provider_gate"]["allowed"] is False
    assert result["provider_gate"]["reason"] == "live_target_cap_exceeded"
    assert result["provider_calls_allowed"] is False
    assert result["execution"]["reason"] == "live_target_cap_exceeded"
    assert result["operator_summary"]["readiness"] == "live_target_cap_exceeded"


def test_cli_blocks_live_execution_without_provider_config(monkeypatch) -> None:
    calls: dict[str, object] = {}
    monkeypatch.delenv("APIFY_TOKEN", raising=False)

    def fake_plan(**_kwargs):
        return {
            "strategy": "apify_batch_first",
            "max_concurrent_runs": 2,
            "selector_ready": True,
            "source_total": 1,
            "total_targets": 1,
            "batch_count": 1,
            "platforms": {"youtube": 1},
            "batches": [{"batch_key": "youtube-1", "platform": "youtube", "targets": [{"kol_pool_id": 1}]}],
        }

    async def fake_execute(_plan, **kwargs):
        calls["execute"] = kwargs
        return {"executed": False, "reason": "provider_calls_not_allowed", "summary": {"retry_count": 0, "failed_batches": 0}}

    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "qualified_apify_batch_plan", fake_plan)
    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "execute_apify_batch_plan", fake_execute)

    result = asyncio.run(vkpi_apify_batch_refresh.run_from_args(vkpi_apify_batch_refresh.parse_args(["--execute", "--allow-provider-calls"])))

    assert calls["execute"]["allow_provider_calls"] is False
    assert result["provider_gate"]["reason"] == "provider_not_configured"
    assert result["provider_config"]["token_configured"] is False
    assert result["provider_config"]["missing_platforms"] == ["youtube"]
    assert result["execution_preflight"]["status"] == "provider_not_configured"
    assert result["operator_summary"]["readiness"] == "provider_not_configured"


def test_cli_blocks_live_execution_when_plan_has_no_targets(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_plan(**_kwargs):
        return {"strategy": "apify_batch_first", "max_concurrent_runs": 2, "total_targets": 0, "batch_count": 0, "batches": []}

    async def fake_execute(_plan, **kwargs):
        calls["execute"] = kwargs
        return {"executed": False, "reason": "provider_calls_not_allowed", "summary": {"retry_count": 0, "failed_batches": 0}}

    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "qualified_apify_batch_plan", fake_plan)
    monkeypatch.setattr(vkpi_apify_batch_refresh.apify_batch_refresh, "execute_apify_batch_plan", fake_execute)

    result = asyncio.run(vkpi_apify_batch_refresh.run_from_args(vkpi_apify_batch_refresh.parse_args(["--execute", "--allow-provider-calls"])))

    assert calls["execute"]["allow_provider_calls"] is False
    assert result["provider_calls_allowed"] is False
    assert result["provider_gate"]["reason"] == "no_targets_to_execute"
    assert result["execution_preflight"]["status"] == "selector_not_ready"
    assert result["operator_summary"]["readiness"] == "no_targets_to_execute"


def test_operator_summary_requires_review_for_retryable_execution() -> None:
    result = {
        "mode": "execute",
        "provider_calls_allowed": True,
        "provider_gate": {"requested": True, "allowed": True, "reason": "allowed", "live_target_cap": 25},
        "provider_config": {"configured": True, "missing_platforms": []},
        "execution_preflight": {"status": "ready", "can_execute_if_authorized": True},
        "plan": {"selector_ready": True, "source_total": 2, "total_targets": 2, "batch_count": 1, "platforms": {"youtube": 2}, "skipped": []},
        "execution": {"executed": True, "summary": {"retry_count": 1, "failed_batches": 0}},
    }

    summary = vkpi_apify_batch_refresh.operator_summary(result)

    assert summary["readiness"] == "review_required"
    assert summary["retry_count"] == 1
    assert summary["provider_calls_allowed"] is True
    assert summary["execution_preflight_status"] == "ready"
    assert summary["can_execute_if_authorized"] is True


def test_execution_preflight_reports_ready_without_request(monkeypatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    args = vkpi_apify_batch_refresh.parse_args(["--limit", "10"])
    plan = {
        "selector_ready": True,
        "total_targets": 10,
        "batch_count": 1,
        "platforms": {"youtube": 10},
        "batches": [{"batch_key": "youtube-1", "platform": "youtube", "targets": [{"kol_pool_id": 1}]}],
        "skipped": [],
    }
    provider_config = vkpi_apify_batch_refresh.provider_config_summary(plan)

    preflight = vkpi_apify_batch_refresh.execution_preflight(args, plan, provider_config)

    assert preflight["status"] == "ready"
    assert preflight["can_execute_if_authorized"] is True
    assert preflight["checks"]["provider_configured"] is True
