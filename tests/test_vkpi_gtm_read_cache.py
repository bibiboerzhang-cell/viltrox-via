from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import math
import threading
import time
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.api.routers import vkpi_market_brain, vkpi_market_brain_summary
from app.api.dependencies.perms import require_tab
from app.core import permissions as core_permissions
from app.domains.market_brain import gtm_plan_preview, summary
from app.domains.market_brain import read_cache
from app.services.cache import memory_cache


@pytest.fixture(autouse=True)
def _isolated_memory_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    monkeypatch.setattr(
        vkpi_market_brain,
        "freshness_version",
        lambda _method, *, ttl_seconds: f"test:{int(ttl_seconds)}",
    )
    monkeypatch.setattr(
        vkpi_market_brain_summary,
        "freshness_version",
        lambda _method, *, ttl_seconds: f"test:{int(ttl_seconds)}",
    )
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()
    yield
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()


def _staff(
    staff_id: int = 7,
    *,
    organization_id: int = 1,
    role: str = "member",
    is_owner: int = 0,
) -> dict:
    return {
        "id": staff_id,
        "organization_id": organization_id,
        "organization_scope_status": "resolved",
        "role": role,
        "is_owner": is_owner,
    }


def test_summary_cache_key_partitions_tenant_actor_and_role() -> None:
    base = vkpi_market_brain_summary._summary_cache_key(_staff())

    assert base == vkpi_market_brain_summary._summary_cache_key(_staff())
    assert base != vkpi_market_brain_summary._summary_cache_key(
        _staff(8)
    )
    assert base != vkpi_market_brain_summary._summary_cache_key(
        _staff(organization_id=4)
    )
    assert base != vkpi_market_brain_summary._summary_cache_key(
        _staff(role="manager")
    )
    assert base.startswith("vkpi_gtm:summary:v3:data:")
    assert ":org:1:auth:" in base


def test_preview_key_canonicalizes_equivalent_inputs_and_hides_raw_sku() -> None:
    first = vkpi_market_brain._preview_cache_key(
        staff=_staff(),
        sku="  AF 85  ",
        country="us",
        budget_usd=3000,
        goal="CONVERSION",
        window_days=30,
    )
    second = vkpi_market_brain._preview_cache_key(
        staff=_staff(),
        sku="AF 85",
        country="US",
        budget_usd=3000.0,
        goal="conversion",
        window_days=30,
    )
    changed_budget = vkpi_market_brain._preview_cache_key(
        staff=_staff(),
        sku="AF 85",
        country="US",
        budget_usd=3001,
        goal="conversion",
        window_days=30,
    )

    assert first == second
    assert first != changed_budget
    assert "AF 85" not in first
    assert first.startswith("vkpi_gtm:plan_preview:v3:data:")
    assert ":org:1:auth:" in first


def test_preview_key_uses_exact_bounded_builder_inputs_without_collision() -> None:
    base_budget = 3000.0
    adjacent_budget = math.nextafter(base_budget, math.inf)

    base = vkpi_market_brain._preview_cache_key(
        staff=_staff(),
        sku="AF 85",
        country="US",
        budget_usd=base_budget,
        goal="conversion",
        window_days=30,
    )
    adjacent = vkpi_market_brain._preview_cache_key(
        staff=_staff(),
        sku="AF 85",
        country="US",
        budget_usd=adjacent_budget,
        goal="conversion",
        window_days=30,
    )

    assert base != adjacent
    with pytest.raises(ValueError, match="1-120"):
        vkpi_market_brain._preview_cache_key(
            staff=_staff(),
            sku="x" * 121,
            country="US",
            budget_usd=base_budget,
            goal="conversion",
            window_days=30,
        )


def test_summary_route_reuses_result_for_same_authorization_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def build(staff: dict) -> dict:
        calls.append(staff)
        return {"method": "summary-test", "items": [1]}

    monkeypatch.setattr(summary, "build_summary", build)
    first = vkpi_market_brain_summary.get_market_brain_summary(staff=_staff())
    second = vkpi_market_brain_summary.get_market_brain_summary(staff=_staff())

    assert first == second == {"method": "summary-test", "items": [1]}
    assert len(calls) == 1
    entry = next(iter(memory_cache._cache.values()))
    remaining = entry["expires"] - time.time()
    assert 0 < remaining <= vkpi_market_brain_summary._GTM_READ_CACHE_TTL_SEC


def test_summary_cache_never_crosses_staff_or_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []

    def build(staff: dict) -> dict:
        identity = (int(staff["organization_id"]), int(staff["id"]))
        calls.append(identity)
        return {"identity": list(identity)}

    monkeypatch.setattr(summary, "build_summary", build)
    outputs = [
        vkpi_market_brain_summary.get_market_brain_summary(staff=_staff(7, organization_id=1)),
        vkpi_market_brain_summary.get_market_brain_summary(staff=_staff(8, organization_id=1)),
        vkpi_market_brain_summary.get_market_brain_summary(staff=_staff(7, organization_id=4)),
    ]

    assert calls == [(1, 7), (1, 8)]
    assert outputs == [
        {"identity": [1, 7]},
        {"identity": [1, 8]},
        {
            "status": "scope_unavailable",
            "claim_status": "descriptive_only",
            "organization_id": 4,
            "organization_scope_status": "resolved",
            "reason": (
                "GTM Summary 底层市场、产品和学习聚合尚未完成 organization_id 收窄；"
                "为防止返回默认工作区数据，本租户暂不执行聚合。"
            ),
            "writes": False,
        },
    ]


class _Rows:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = list(many or [])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class _StaffMembershipConnection:
    def __init__(self, organization_ids: list[int]):
        self.organization_ids = organization_ids

    def execute(self, sql: str, _params=()):
        normalized = " ".join(sql.split())
        if "FROM staff s" in normalized:
            unique_ids = sorted(set(self.organization_ids))
            return _Rows(
                one={
                    "id": 7,
                    "user_id": 99,
                    "role": "admin",
                    "active": 1,
                    "permissions_json": "{}",
                    "email": "admin@example.test",
                    "name": "Admin",
                    "resolved_organization_id": unique_ids[0] if unique_ids else None,
                    "organization_membership_count": len(unique_ids),
                }
            )
        raise AssertionError(f"unexpected SQL: {normalized}")


def test_real_auth_dependency_resolves_membership_before_gtm_builders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        core_permissions,
        "get_conn",
        lambda: _StaffMembershipConnection([1]),
    )
    monkeypatch.setattr(summary, "build_summary", lambda staff: {"status": "ok", "staff": staff["id"]})
    monkeypatch.setattr(
        gtm_plan_preview,
        "build_preview",
        lambda **kwargs: {"status": "ok", "sku": kwargs["sku"]},
    )

    staff = asyncio.run(
        require_tab("vkpi", "read")(
            request=SimpleNamespace(state=SimpleNamespace()),
            user={"id": 99, "email": "admin@example.test", "role": "admin"}
        )
    )
    assert staff["organization_id"] == 1
    assert staff["organization_scope_status"] == "resolved"

    summary_result = vkpi_market_brain_summary.get_market_brain_summary(staff=staff)
    preview_result = vkpi_market_brain.get_gtm_plan_preview(
        sku="AF 85",
        country="US",
        budget_usd=3000,
        goal="conversion",
        window_days=30,
        staff=staff,
    )

    assert summary_result == {"status": "ok", "staff": 7}
    assert preview_result == {"status": "ok", "sku": "AF 85"}


def test_gtm_routes_fail_closed_for_resolved_non_default_tenant_before_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vkpi_market_brain_summary,
        "cache_get_or_build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("summary cache touched")),
    )
    monkeypatch.setattr(
        vkpi_market_brain,
        "cache_get_or_build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("preview cache touched")),
    )

    staff = {"id": 7, "organization_id": 4, "organization_scope_status": "resolved"}
    summary_result = vkpi_market_brain_summary.get_market_brain_summary(staff=staff)
    preview_result = vkpi_market_brain.get_gtm_plan_preview(
        sku="AF 85",
        country="US",
        budget_usd=3000,
        goal="conversion",
        window_days=30,
        staff=staff,
    )

    assert summary_result["status"] == "scope_unavailable"
    assert preview_result["status"] == "scope_unavailable"
    assert summary_result["organization_id"] == 4
    assert preview_result["organization_id"] == 4


@pytest.mark.parametrize(
    ("organization_ids", "expected_scope_status"),
    [([], "membership_missing"), ([1, 4], "ambiguous")],
)
def test_real_auth_dependency_fails_closed_on_missing_or_ambiguous_membership(
    monkeypatch: pytest.MonkeyPatch,
    organization_ids: list[int],
    expected_scope_status: str,
) -> None:
    monkeypatch.setattr(
        core_permissions,
        "get_conn",
        lambda: _StaffMembershipConnection(organization_ids),
    )
    staff = asyncio.run(
        require_tab("vkpi", "read")(
            request=SimpleNamespace(state=SimpleNamespace()),
            user={"id": 99, "email": "admin@example.test", "role": "admin"}
        )
    )

    result = vkpi_market_brain_summary.get_market_brain_summary(staff=staff)

    assert "organization_id" not in staff
    assert staff["organization_scope_status"] == expected_scope_status
    assert result["status"] == "scope_unavailable"
    assert result["organization_id"] is None
    assert result["organization_scope_status"] == expected_scope_status


def test_preview_route_reuses_canonical_equivalent_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def build_preview(**kwargs) -> dict:
        calls.append(kwargs)
        return {"public_plan": {"sku": str(kwargs["sku"]).strip()}, "meta": {"writes": False}}

    monkeypatch.setattr(gtm_plan_preview, "build_preview", build_preview)
    first = vkpi_market_brain.get_gtm_plan_preview(
        sku=" AF 85 ",
        country="us",
        budget_usd=3000,
        goal="CONVERSION",
        window_days=30,
        staff=_staff(),
    )
    second = vkpi_market_brain.get_gtm_plan_preview(
        sku="AF 85",
        country="US",
        budget_usd=3000.0,
        goal="conversion",
        window_days=30,
        staff=_staff(),
    )

    assert first == second
    assert len(calls) == 1
    assert calls[0]["sku"] == "AF 85"
    assert calls[0]["country"] == "US"
    assert calls[0]["goal"] == "conversion"


def test_invalid_preview_goal_does_not_touch_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vkpi_market_brain,
        "cache_get_or_build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache touched")),
    )

    with pytest.raises(HTTPException) as error:
        vkpi_market_brain.get_gtm_plan_preview(
            sku="AF 85",
            country="US",
            budget_usd=3000,
            goal="not-a-goal",
            window_days=30,
            staff=_staff(),
        )
    assert error.value.status_code == 422


def test_builder_exception_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def broken(_staff_value: dict) -> dict:
        nonlocal calls
        calls += 1
        raise RuntimeError("temporary read failure")

    monkeypatch.setattr(summary, "build_summary", broken)
    first = vkpi_market_brain_summary.get_market_brain_summary(staff=_staff())
    second = vkpi_market_brain_summary.get_market_brain_summary(staff=_staff())

    assert calls == 2
    assert first["status"] == second["status"] == "error"
    assert memory_cache._cache == {}


def test_honest_degraded_payload_is_returned_but_never_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def degraded(_staff_value: dict) -> dict:
        nonlocal calls
        calls += 1
        return {
            "method": "summary-test",
            "weekly_signals": {"status": "degraded", "reason": "source stale"},
        }

    monkeypatch.setattr(summary, "build_summary", degraded)
    first = vkpi_market_brain_summary.get_market_brain_summary(staff=_staff())
    second = vkpi_market_brain_summary.get_market_brain_summary(staff=_staff())

    assert first == second
    assert calls == 2
    assert memory_cache._cache == {}


def test_freshness_version_rotates_by_method_and_bounded_data_window() -> None:
    first = read_cache.freshness_version("method-v1", ttl_seconds=30, now=59.9)
    same_window = read_cache.freshness_version("method-v1", ttl_seconds=30, now=30.0)
    next_window = read_cache.freshness_version("method-v1", ttl_seconds=30, now=60.0)
    changed_method = read_cache.freshness_version("method-v2", ttl_seconds=30, now=59.9)

    assert first == same_window
    assert first != next_window
    assert first != changed_method


def test_concurrent_summary_requests_collapse_to_one_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    guard = threading.Lock()

    def slow_build(_staff_value: dict) -> dict:
        nonlocal calls
        with guard:
            calls += 1
        time.sleep(0.03)
        return {"method": "summary-test", "items": [1, 2, 3]}

    monkeypatch.setattr(summary, "build_summary", slow_build)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(
                lambda _index: vkpi_market_brain_summary.get_market_brain_summary(
                    staff=_staff()
                ),
                range(32),
            )
        )

    assert calls == 1
    assert all(result == {"method": "summary-test", "items": [1, 2, 3]} for result in results)
