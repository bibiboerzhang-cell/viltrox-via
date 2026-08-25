from __future__ import annotations

import asyncio
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import logging
import math
import threading
import time
from types import SimpleNamespace

from fastapi import HTTPException, Response
import pytest

from app.api.routers import vkpi_market_brain, vkpi_market_brain_summary
from app.api.dependencies.perms import require_tab
from app.core.config import VKPI_GTM_READ_CACHE_TTL_SEC
from app.core import permissions as core_permissions
from app.domains.market_brain import gtm_plan_preview, summary
from app.domains.market_brain import read_cache
from app.services.cache import memory_cache


@pytest.fixture(autouse=True)
def _isolated_memory_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
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
    assert base.startswith("vkpi_gtm:summary:v4:")
    assert ":org:1:auth:" in base


def test_gtm_read_cache_ttl_is_shared_and_bounded() -> None:
    assert 30 <= VKPI_GTM_READ_CACHE_TTL_SEC <= 600
    assert vkpi_market_brain._GTM_READ_CACHE_TTL_SEC == VKPI_GTM_READ_CACHE_TTL_SEC
    assert vkpi_market_brain_summary._GTM_READ_CACHE_TTL_SEC == VKPI_GTM_READ_CACHE_TTL_SEC


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
    assert first.startswith("vkpi_gtm:plan_preview:v4:")
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


def test_cache_contract_version_is_stable_until_the_builder_contract_changes() -> None:
    first = read_cache.cache_contract_version("method-v1")
    same_method = read_cache.cache_contract_version("method-v1")
    changed_method = read_cache.cache_contract_version("method-v2")

    assert first == same_method
    assert first != changed_method
    assert first.startswith("v4:")


def test_summary_three_identical_requests_expose_builder_then_real_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def build(_staff_value: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"method": "summary-test", "items": [1]}

    monkeypatch.setattr(summary, "build_summary", build)
    responses = [Response(), Response(), Response()]
    results = [
        vkpi_market_brain_summary.get_market_brain_summary(
            staff=_staff(),
            response=response,
        )
        for response in responses
    ]

    assert results == [{"method": "summary-test", "items": [1]}] * 3
    assert calls == 1
    assert [response.headers["x-vkpi-cache"] for response in responses] == [
        "miss_builder",
        "hit",
        "hit",
    ]
    assert [response.headers["x-vkpi-cache-builder"] for response in responses] == [
        "1",
        "0",
        "0",
    ]
    assert all(response.headers["x-vkpi-cache-key-version"] == "v4" for response in responses)
    assert "gtm-builder" in responses[0].headers["server-timing"]
    assert "gtm-builder" not in responses[1].headers["server-timing"]


def test_preview_three_canonical_requests_expose_builder_then_real_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def build_preview(**kwargs) -> dict:
        nonlocal calls
        calls += 1
        return {"public_plan": {"sku": kwargs["sku"]}, "meta": {"writes": False}}

    monkeypatch.setattr(gtm_plan_preview, "build_preview", build_preview)
    responses = [Response(), Response(), Response()]
    results = [
        vkpi_market_brain.get_gtm_plan_preview(
            sku=" AF 85 " if index == 0 else "AF 85",
            country="us" if index == 0 else "US",
            budget_usd=3000.0,
            goal="CONVERSION" if index == 0 else "conversion",
            window_days=30,
            staff=_staff(),
            response=response,
        )
        for index, response in enumerate(responses)
    ]

    assert results == [{"public_plan": {"sku": "AF 85"}, "meta": {"writes": False}}] * 3
    assert calls == 1
    assert [response.headers["x-vkpi-cache"] for response in responses] == [
        "miss_builder",
        "hit",
        "hit",
    ]


def test_cache_observer_failure_never_breaks_a_healthy_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    calls = 0

    def build() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "ready"}

    def broken_observer(_observation: dict) -> None:
        raise RuntimeError("telemetry sink unavailable")

    first = memory_cache.cache_get_or_build(
        "vkpi_gtm:test:observer-failure",
        build,
        ttl=30,
        observe=broken_observer,
    )
    second = memory_cache.cache_get_or_build(
        "vkpi_gtm:test:observer-failure",
        build,
        ttl=30,
        observe=broken_observer,
    )

    assert first == second == {"status": "ready"}
    assert calls == 1


def test_force_refresh_rebuilds_and_replaces_an_existing_cache_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    observations: list[dict] = []
    calls = 0

    def build() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "ready", "revision": calls}

    first = memory_cache.cache_get_or_build(
        "vkpi_gtm:test:manual-refresh",
        build,
        ttl=30,
        observe=observations.append,
    )
    refreshed = memory_cache.cache_get_or_build(
        "vkpi_gtm:test:manual-refresh",
        build,
        ttl=30,
        observe=observations.append,
        force_refresh=True,
    )
    final_hit = memory_cache.cache_get_or_build(
        "vkpi_gtm:test:manual-refresh",
        build,
        ttl=30,
        observe=observations.append,
    )

    assert first == {"status": "ready", "revision": 1}
    assert refreshed == final_hit == {"status": "ready", "revision": 2}
    assert calls == 2
    assert [item["outcome"] for item in observations] == [
        "miss_builder",
        "refresh_builder",
        "hit",
    ]


def test_concurrent_force_refreshes_collapse_to_one_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    calls = 0
    calls_guard = threading.Lock()
    release_builder = threading.Event()
    build_started = threading.Event()

    def build() -> dict:
        nonlocal calls
        with calls_guard:
            calls += 1
        build_started.set()
        assert release_builder.wait(timeout=2)
        return {"status": "ready", "revision": 1}

    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = [
            pool.submit(
                memory_cache.cache_get_or_build,
                "vkpi_gtm:test:manual-refresh-burst",
                build,
                30,
                force_refresh=True,
            )
            for _index in range(32)
        ]
        assert build_started.wait(timeout=1)
        release_builder.set()
        results = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert results == [{"status": "ready", "revision": 1}] * 32


def test_concurrent_uncacheable_force_refreshes_share_one_short_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    calls = 0
    calls_guard = threading.Lock()
    release_builder = threading.Event()
    build_started = threading.Event()

    def build() -> dict:
        nonlocal calls
        with calls_guard:
            calls += 1
        build_started.set()
        assert release_builder.wait(timeout=2)
        return {"status": "degraded", "reason": "source unavailable"}

    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = [
            pool.submit(
                memory_cache.cache_get_or_build,
                "vkpi_gtm:test:manual-refresh-uncacheable",
                build,
                30,
                cache_if=read_cache.cacheable_payload,
                force_refresh=True,
            )
            for _index in range(32)
        ]
        assert build_started.wait(timeout=1)
        release_builder.set()
        results = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert results == [
        {"status": "degraded", "reason": "source unavailable"}
    ] * 32
    assert memory_cache.cache_get("vkpi_gtm:test:manual-refresh-uncacheable") is None


def test_concurrent_failed_force_refreshes_do_not_repeat_the_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    calls = 0
    calls_guard = threading.Lock()
    release_builder = threading.Event()
    build_started = threading.Event()

    def build() -> dict:
        nonlocal calls
        with calls_guard:
            calls += 1
        build_started.set()
        assert release_builder.wait(timeout=2)
        raise RuntimeError("source failed")

    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = [
            pool.submit(
                memory_cache.cache_get_or_build,
                "vkpi_gtm:test:manual-refresh-error",
                build,
                30,
                force_refresh=True,
            )
            for _index in range(32)
        ]
        assert build_started.wait(timeout=1)
        release_builder.set()
        errors = []
        for future in futures:
            with pytest.raises(RuntimeError) as error:
                future.result(timeout=2)
            errors.append(str(error.value))

    assert calls == 1
    assert "source failed" in errors
    assert all(
        message == "source failed" or "retry after 5 seconds" in message
        for message in errors
    )


def test_force_refresh_fails_closed_without_building_during_validation_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_cache, "_cache_mutations_fenced", lambda: True)
    calls = 0

    def build() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "ready"}

    with pytest.raises(RuntimeError, match="mutations are fenced"):
        memory_cache.cache_get_or_build(
            "vkpi_gtm:test:manual-refresh-fenced",
            build,
            ttl=30,
            force_refresh=True,
        )

    assert calls == 0


def test_immediately_repeated_force_refresh_uses_recent_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    observations: list[dict] = []
    calls = 0

    def build() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "ready", "revision": calls}

    first = memory_cache.cache_get_or_build(
        "vkpi_gtm:test:manual-refresh-cooldown",
        build,
        ttl=30,
        observe=observations.append,
        force_refresh=True,
    )
    second = memory_cache.cache_get_or_build(
        "vkpi_gtm:test:manual-refresh-cooldown",
        build,
        ttl=30,
        observe=observations.append,
        force_refresh=True,
    )

    assert first == second == {"status": "ready", "revision": 1}
    assert calls == 1
    assert [item["outcome"] for item in observations] == [
        "refresh_builder",
        "refresh_recent_hit",
    ]


def test_gtm_routes_explicit_refresh_bypasses_then_replaces_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_calls = 0
    preview_calls = 0

    def build_summary(_staff_value: dict) -> dict:
        nonlocal summary_calls
        summary_calls += 1
        return {"status": "ready", "revision": summary_calls}

    def build_preview(**kwargs) -> dict:
        nonlocal preview_calls
        preview_calls += 1
        return {
            "status": "ready",
            "revision": preview_calls,
            "sku": kwargs["sku"],
        }

    monkeypatch.setattr(summary, "build_summary", build_summary)
    monkeypatch.setattr(gtm_plan_preview, "build_preview", build_preview)

    summary_first_response = Response()
    summary_refresh_response = Response()
    summary_hit_response = Response()
    summary_first = vkpi_market_brain_summary.get_market_brain_summary(
        staff=_staff(), response=summary_first_response
    )
    summary_refreshed = vkpi_market_brain_summary.get_market_brain_summary(
        refresh=True, staff=_staff(), response=summary_refresh_response
    )
    summary_hit = vkpi_market_brain_summary.get_market_brain_summary(
        staff=_staff(), response=summary_hit_response
    )

    preview_first_response = Response()
    preview_refresh_response = Response()
    preview_hit_response = Response()
    preview_kwargs = {
        "sku": "AF 85",
        "country": "US",
        "budget_usd": 3000,
        "goal": "conversion",
        "window_days": 30,
        "staff": _staff(),
    }
    preview_first = vkpi_market_brain.get_gtm_plan_preview(
        **preview_kwargs, response=preview_first_response
    )
    preview_refreshed = vkpi_market_brain.get_gtm_plan_preview(
        **preview_kwargs, refresh=True, response=preview_refresh_response
    )
    preview_hit = vkpi_market_brain.get_gtm_plan_preview(
        **preview_kwargs, response=preview_hit_response
    )

    assert [summary_first["revision"], summary_refreshed["revision"], summary_hit["revision"]] == [1, 2, 2]
    assert [preview_first["revision"], preview_refreshed["revision"], preview_hit["revision"]] == [1, 2, 2]
    assert [
        summary_first_response.headers["x-vkpi-cache"],
        summary_refresh_response.headers["x-vkpi-cache"],
        summary_hit_response.headers["x-vkpi-cache"],
    ] == ["miss_builder", "refresh_builder", "hit"]
    assert [
        preview_first_response.headers["x-vkpi-cache"],
        preview_refresh_response.headers["x-vkpi-cache"],
        preview_hit_response.headers["x-vkpi-cache"],
    ] == ["miss_builder", "refresh_builder", "hit"]


def test_gtm_observer_allowlists_surface_and_never_accepts_identity_or_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = Response()
    with caplog.at_level(logging.INFO, logger=read_cache.logger.name):
        read_cache.gtm_cache_observer(
            "secret-sku-or-actor",
            response=response,
        )(
            {
                "outcome": "hit",
                "elapsed_ms": 0.5,
                "builder_ms": None,
                "cache_candidate": None,
            }
        )

    record = next(item for item in caplog.records if "gtm.read_cache" in item.getMessage())
    assert "surface=unknown" in record.getMessage()
    assert "secret-sku-or-actor" not in record.getMessage()
    assert response.headers["x-vkpi-cache"] == "hit"
    assert "secret-sku-or-actor" not in str(dict(response.headers)).lower()


def test_cache_observer_records_builder_error_without_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    observations: list[dict] = []

    def broken_builder() -> dict:
        raise RuntimeError("temporary builder failure")

    with pytest.raises(RuntimeError, match="temporary builder failure"):
        memory_cache.cache_get_or_build(
            "vkpi_gtm:test:builder-error",
            broken_builder,
            ttl=30,
            observe=observations.append,
        )

    assert [item["outcome"] for item in observations] == ["builder_error"]
    assert memory_cache._cache == {}


def test_cache_observer_reports_fenced_builder_without_a_cache_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: list[dict] = []
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    monkeypatch.setattr(memory_cache, "_cache_mutations_fenced", lambda: True)
    monkeypatch.setattr(
        memory_cache,
        "cache_set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache write attempted")),
    )

    result = memory_cache.cache_get_or_build(
        "vkpi_gtm:test:fenced",
        lambda: {"status": "ready"},
        ttl=30,
        observe=observations.append,
    )

    assert result == {"status": "ready"}
    assert [item["outcome"] for item in observations] == ["fenced_builder"]
    assert observations[0]["cache_candidate"] is None


def test_cache_observer_distinguishes_process_wait_hit_from_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    observations: list[dict] = []
    observation_lock = threading.Lock()
    build_started = threading.Event()
    release_builder = threading.Event()

    def observe(item: dict) -> None:
        with observation_lock:
            observations.append(item)

    def build() -> dict:
        build_started.set()
        assert release_builder.wait(timeout=1)
        return {"status": "ready"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            memory_cache.cache_get_or_build,
            "vkpi_gtm:test:wait-hit",
            build,
            30,
            observe=observe,
        )
        assert build_started.wait(timeout=1)
        second = pool.submit(
            memory_cache.cache_get_or_build,
            "vkpi_gtm:test:wait-hit",
            build,
            30,
            observe=observe,
        )
        release_builder.set()
        results = [first.result(timeout=1), second.result(timeout=1)]

    assert results == [{"status": "ready"}, {"status": "ready"}]
    assert sorted(item["outcome"] for item in observations) == [
        "miss_builder",
        "miss_wait_hit",
    ]


def test_cache_observer_distinguishes_distributed_wait_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: list[dict] = []
    cache_reads = iter([None, None, {"status": "ready", "source": "peer"}])
    monkeypatch.setattr(memory_cache, "cache_get", lambda _key: next(cache_reads))
    monkeypatch.setattr(memory_cache, "_cache_mutations_fenced", lambda: False)

    @contextmanager
    def peer_build_lock(_key: str):
        yield True

    monkeypatch.setattr(memory_cache, "_distributed_build_lock", peer_build_lock)
    result = memory_cache.cache_get_or_build(
        "vkpi_gtm:test:distributed-hit",
        lambda: (_ for _ in ()).throw(AssertionError("local builder ran")),
        ttl=30,
        observe=observations.append,
    )

    assert result == {"status": "ready", "source": "peer"}
    assert [item["outcome"] for item in observations] == ["miss_distributed_hit"]
