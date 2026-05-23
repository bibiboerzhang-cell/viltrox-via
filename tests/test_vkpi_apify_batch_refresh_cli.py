from __future__ import annotations

import asyncio
import json

from scripts import vkpi_apify_batch_refresh


def test_cli_run_blocks_provider_calls_by_default(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_plan(**kwargs):
        calls["plan"] = kwargs
        return {"strategy": "apify_batch_first", "max_concurrent_runs": 2, "batches": [{"batch_key": "instagram-1", "platform": "instagram", "targets": [{"kol_pool_id": 1}]}]}

    async def fake_execute(plan, **kwargs):
        calls["execute"] = kwargs
        return {"executed": False, "reason": "provider_calls_not_allowed", "batch_count": len(plan["batches"])}

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


def test_cli_requires_both_execute_and_allow_provider_calls(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_plan(**_kwargs):
        return {"strategy": "apify_batch_first", "max_concurrent_runs": 2, "batches": []}

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
    assert printed["artifact"]["path"] == str(artifact)
