"""Unit tests for V-KPI audit/firewall decorators.

These tests intentionally avoid the database. They validate the decorator
contracts with monkeypatched dependencies so Agent test work can stay inside the
allowed test boundary.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.domains.audit import decorator as audit_decorator
from app.domains.access import firewall as firewall_decorator


# ---------------------------------------------------------------------------
# audit_action
# ---------------------------------------------------------------------------


def test_audit_action_logs_success_with_default_target(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(audit_decorator.audit, "log_business_event", lambda **kwargs: events.append(kwargs))

    @audit_decorator.audit_action(action_type="kol_import", target_type="kol_pool")
    def handler(*, kol_id: int, staff: dict):
        return {"ok": True}

    assert handler(kol_id=123, staff={"id": 77}) == {"ok": True}
    assert len(events) == 1
    assert events[0]["staff_id"] == 77
    assert events[0]["action_type"] == "kol_import"
    assert events[0]["target_type"] == "kol_pool"
    assert events[0]["target_id"] == "123"
    assert events[0]["metadata"]["action_status"] == "success"
    assert events[0]["metadata"]["kwargs_keys"] == ["kol_id"]


def test_audit_action_logs_failure_then_reraises(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(audit_decorator.audit, "log_business_event", lambda **kwargs: events.append(kwargs))

    @audit_decorator.audit_action(action_type="project_update", target_type="project")
    def handler(*, project_id: int, staff: dict):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        handler(project_id=456, staff={"staff_id": 88})

    assert len(events) == 1
    assert events[0]["target_id"] == "456"
    assert events[0]["metadata"]["action_status"] == "failed"
    assert events[0]["metadata"]["error"] == "boom"


def test_audit_action_custom_extractors(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(audit_decorator.audit, "log_business_event", lambda **kwargs: events.append(kwargs))

    @audit_decorator.audit_action(
        action_type="feature_toggle",
        target_type="feature_flag",
        target_id_extractor=lambda result, kwargs: result["flag_key"],
        detail_extractor=lambda result, kwargs: f"enabled={result['enabled']}",
        metadata_extractor=lambda result, kwargs: {"source": kwargs["source"]},
    )
    def handler(*, source: str, staff: dict):
        return {"flag_key": "vkpi.test", "enabled": True}

    handler(source="unit", staff={"id": 91})
    assert events[0]["target_id"] == "vkpi.test"
    assert events[0]["detail"] == "enabled=True"
    assert events[0]["metadata"]["source"] == "unit"
    assert events[0]["metadata"]["action_status"] == "success"


def test_audit_action_without_staff_does_not_log(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(audit_decorator.audit, "log_business_event", lambda **kwargs: events.append(kwargs))

    @audit_decorator.audit_action(action_type="noop", target_type="none")
    def handler():
        return "ok"

    assert handler() == "ok"
    assert events == []


def test_audit_action_audit_failure_is_best_effort(monkeypatch):
    def fail_audit(**kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(audit_decorator.audit, "log_business_event", fail_audit)

    @audit_decorator.audit_action(action_type="safe", target_type="unit")
    def handler(*, staff: dict):
        return "business-ok"

    assert handler(staff={"id": 101}) == "business-ok"


def test_audit_action_supports_async_handlers(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(audit_decorator.audit, "log_business_event", lambda **kwargs: events.append(kwargs))

    @audit_decorator.audit_action(action_type="async_action", target_type="unit")
    async def handler(*, id: str, staff: dict):
        return {"ok": True, "id": id}

    result = asyncio.run(handler(id="async-1", staff={"id": 111}))
    assert result == {"ok": True, "id": "async-1"}
    assert events[0]["target_id"] == "async-1"


# ---------------------------------------------------------------------------
# firewall_check
# ---------------------------------------------------------------------------


def _patch_platform_state(
    monkeypatch,
    *,
    flag_enabled: bool = True,
    crawl_enabled: int = 1,
    monthly_budget_usd: float = 10.0,
):
    monkeypatch.setattr(
        firewall_decorator.platform_crawl_settings,
        "feature_flags",
        lambda: {"flags": [{"flag_key": "vkpi.test_flag", "enabled": flag_enabled}]},
    )
    monkeypatch.setattr(
        firewall_decorator.platform_crawl_settings,
        "platform_settings",
        lambda: {
            "platforms": [
                {
                    "platform": "instagram",
                    "crawl_enabled": crawl_enabled,
                    "monthly_budget_usd": monthly_budget_usd,
                }
            ]
        },
    )


def test_check_firewall_allows_configured_platform(monkeypatch):
    _patch_platform_state(monkeypatch)

    result = firewall_decorator.check_firewall(
        platform="instagram",
        action="crawl",
        feature_flag="vkpi.test_flag",
        require_budget=True,
    )

    assert result == {"allowed": True, "reason": "passed", "action": "crawl"}


def test_check_firewall_blocks_disabled_feature_flag(monkeypatch):
    _patch_platform_state(monkeypatch, flag_enabled=False)

    result = firewall_decorator.check_firewall(
        platform="instagram",
        action="crawl",
        feature_flag="vkpi.test_flag",
    )

    assert result["allowed"] is False
    assert result["reason"] == "feature_flag_disabled"


def test_check_firewall_blocks_disabled_platform(monkeypatch):
    _patch_platform_state(monkeypatch, crawl_enabled=0)

    result = firewall_decorator.check_firewall(platform="instagram", action="crawl")

    assert result["allowed"] is False
    assert result["reason"] == "platform_crawl_disabled"


def test_check_firewall_blocks_zero_budget_when_required(monkeypatch):
    _patch_platform_state(monkeypatch, monthly_budget_usd=0)

    result = firewall_decorator.check_firewall(platform="instagram", action="crawl", require_budget=True)

    assert result["allowed"] is False
    assert result["reason"] == "platform_budget_zero"


def test_firewall_decorator_blocks_before_business_call(monkeypatch):
    _patch_platform_state(monkeypatch, monthly_budget_usd=0)
    calls: list[str] = []

    @firewall_decorator.firewall_check(platform="instagram", action="crawl", require_budget=True)
    def handler(*, staff: dict):
        calls.append("called")
        return "ok"

    with pytest.raises(HTTPException) as exc:
        handler(staff={"id": 1})

    assert calls == []
    assert exc.value.status_code == 503
    assert exc.value.detail["reason"] == "platform_budget_zero"


def test_firewall_decorator_owner_force_bypass(monkeypatch):
    _patch_platform_state(monkeypatch, crawl_enabled=0, monthly_budget_usd=0)

    @firewall_decorator.firewall_check(platform="instagram", action="crawl", require_budget=True)
    def handler(*, body: dict, staff: dict):
        return "ok"

    assert handler(body={"force": True}, staff={"id": 1, "is_owner": True}) == "ok"


def test_firewall_decorator_non_owner_force_does_not_bypass(monkeypatch):
    _patch_platform_state(monkeypatch, crawl_enabled=0, monthly_budget_usd=0)

    @firewall_decorator.firewall_check(platform="instagram", action="crawl", require_budget=True)
    def handler(*, body: dict, staff: dict):
        return "ok"

    with pytest.raises(HTTPException) as exc:
        handler(body={"force": True}, staff={"id": 2, "is_owner": False})

    assert exc.value.detail["reason"] == "platform_crawl_disabled"


def test_firewall_decorator_supports_async_handlers(monkeypatch):
    _patch_platform_state(monkeypatch)

    @firewall_decorator.firewall_check(platform="instagram", action="crawl", require_budget=True)
    async def handler(*, staff: dict):
        return {"ok": True}

    assert asyncio.run(handler(staff={"id": 3})) == {"ok": True}
