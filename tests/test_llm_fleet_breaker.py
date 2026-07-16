from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.model_registry import (
    assert_production_task_bindings_are_pinned,
    floating_production_task_bindings,
)
from app.platform import llm_fleet_breaker
from app.platform.llm_fleet_breaker import (
    FleetBreakerOpen,
    FleetBreakerUnavailable,
    StaleFleetBreakerPermit,
)


def test_migration_266_has_exact_binding_key_and_durable_half_open_fence() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "266_vkpi_llm_fleet_breaker.sql"
    ).read_text(encoding="utf-8")

    assert "PRIMARY KEY (provider, model_name)" in migration
    assert "state IN ('closed', 'open', 'half_open')" in migration
    assert "half_open_owner" in migration
    assert "half_open_fence" in migration
    assert "half_open_lease_expires_at" in migration
    assert "generation BIGINT" in migration
    assert "version BIGINT" in migration
    assert "BEGIN;" not in migration
    assert "COMMIT;" not in migration


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ({"status": "provider_429"}, "provider_429"),
        ({"status": "provider_5xx"}, "provider_5xx"),
        ({"status": "provider_http_error", "error": "http_401"}, "provider_auth_error"),
        ({"status": "provider_http_error", "error": "http_400"}, None),
        ({"status": "timeout"}, "timeout"),
        ({"status": "success"}, None),
        (TimeoutError("redacted"), "timeout"),
    ],
)
def test_failure_classification_is_stable(outcome: Any, expected: str | None) -> None:
    assert llm_fleet_breaker.classify_fleet_breaker_failure(outcome) == expected


def test_production_task_binding_validator_rejects_latest_aliases() -> None:
    bindings = {
        "pinned": "google/gemini-2.5-flash",
        "floating": "google/gemini-flash-latest",
    }
    assert floating_production_task_bindings(bindings) == {
        "floating": "google/gemini-flash-latest"
    }
    with pytest.raises(RuntimeError, match="floating_latest_tasks=floating"):
        assert_production_task_bindings_are_pinned(bindings)


def test_production_breaker_enablement_is_independent_of_atomic_budget_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.platform import llm_gateway

    monkeypatch.setattr(llm_gateway, "IS_PRODUCTION", True)
    monkeypatch.delenv("VKPI_LLM_FLEET_BREAKER_ENABLED", raising=False)
    assert llm_gateway._strict_fleet_breaker_enabled(False) is True
    assert llm_gateway._strict_fleet_breaker_enabled(True) is True


def test_half_open_default_lease_exceeds_provider_http_timeout(monkeypatch) -> None:
    monkeypatch.delenv("VKPI_LLM_FLEET_BREAKER_HALF_OPEN_LEASE_SECONDS", raising=False)
    from app.platform import llm_gateway

    largest_timeout = max(
        int(config.get("timeout") or 0)
        for config in llm_gateway.PROVIDER_CONFIG.values()
    )
    assert llm_fleet_breaker._half_open_lease_seconds() >= largest_timeout * 2


class _Reservations:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def reserve_llm_budget(self, **kwargs: Any) -> Any:
        self.events.append(("reserve", kwargs))
        return SimpleNamespace(reservation_key="llmres-breaker-open")

    def mark_llm_provider_started(self, key: str) -> None:
        self.events.append(("started", key))

    def release_llm_reservation(self, key: str) -> bool:
        self.events.append(("released", key))
        return True

    def mark_llm_provider_unknown(self, key: str) -> bool:
        self.events.append(("unknown", key))
        return True


def _install_open_breaker(monkeypatch: pytest.MonkeyPatch):
    from app.platform import llm_gateway

    reservations = _Reservations()
    ledgers: list[dict[str, Any]] = []
    binding = f"openai/{llm_gateway.PROVIDER_CONFIG['openai']['model']}"
    monkeypatch.setattr(
        llm_gateway,
        "exact_binding_readiness_from_environment",
        lambda _binding: (
            {"binding": binding, "production_ready": True},
            {"source": "test_signed_fixture"},
        ),
    )
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider == "openai")
    monkeypatch.setattr(llm_gateway, "_budget_allows_provider", lambda *_a, **_k: (True, []))
    monkeypatch.setattr(llm_gateway, "_estimated_cost_usd", lambda *_a, **_k: 0.001)
    monkeypatch.setattr(llm_gateway, "_llm_budget_reservations", lambda: reservations)
    monkeypatch.setattr(llm_gateway, "record_call", lambda **kwargs: ledgers.append(kwargs) or {})

    def blocked(**_kwargs: Any) -> Any:
        raise FleetBreakerOpen(provider="openai", model=binding.split("/", 1)[1])

    monkeypatch.setattr(llm_gateway, "_acquire_strict_fleet_breaker", blocked)
    monkeypatch.setitem(
        llm_gateway._PROVIDER_CALLERS,
        "openai",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("open breaker must prevent provider HTTP")
        ),
    )
    return llm_gateway, reservations, ledgers, binding


def test_text_gateway_open_breaker_releases_reserved_budget_and_sends_no_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, reservations, ledgers, binding = _install_open_breaker(monkeypatch)
    provider, model = binding.split("/", 1)

    result = gateway.invoke(
        "hello",
        purpose="fleet-breaker-text-test",
        preferred_provider=provider,
        model_override=model,
        model_fallbacks=(),
        skip_budget_check=True,
        enforce_atomic_reservation=True,
    )

    assert [event[0] for event in reservations.events] == ["reserve", "released"]
    assert result["provider"] == "rule_v0"
    assert any(row["status"] == "fleet_breaker_open" for row in ledgers)


def test_json_gateway_open_breaker_releases_reserved_budget_and_sends_no_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, reservations, ledgers, binding = _install_open_breaker(monkeypatch)
    provider, model = binding.split("/", 1)

    result = gateway.invoke_json(
        "return json",
        purpose="fleet-breaker-json-test",
        preferred_provider=provider,
        model_override=model,
        model_fallbacks=(),
        required_keys=("ok",),
        skip_budget_check=True,
        enforce_atomic_reservation=True,
    )

    assert [event[0] for event in reservations.events] == ["reserve", "released"]
    assert result["provider"] == "rule_v0"
    assert result["json"] is None
    assert any(row["status"] == "fleet_breaker_open" for row in ledgers)


def test_legacy_text_gateway_still_checks_open_breaker_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, reservations, ledgers, binding = _install_open_breaker(monkeypatch)
    provider, model = binding.split("/", 1)

    result = gateway.invoke(
        "hello",
        purpose="fleet-breaker-legacy-text-test",
        preferred_provider=provider,
        model_override=model,
        model_fallbacks=(),
        skip_budget_check=True,
    )

    assert reservations.events == []
    assert result["provider"] == "rule_v0"
    blocked = next(row for row in ledgers if row["status"] == "fleet_breaker_open")
    assert blocked["update_budget_scopes"] is True
    assert blocked["force_cost_ledger"] is False


def test_legacy_json_gateway_still_checks_open_breaker_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, reservations, ledgers, binding = _install_open_breaker(monkeypatch)
    provider, model = binding.split("/", 1)

    result = gateway.invoke_json(
        "return json",
        purpose="fleet-breaker-legacy-json-test",
        preferred_provider=provider,
        model_override=model,
        model_fallbacks=(),
        required_keys=("ok",),
        skip_budget_check=True,
    )

    assert reservations.events == []
    assert result["provider"] == "rule_v0"
    assert result["json"] is None
    blocked = next(row for row in ledgers if row["status"] == "fleet_breaker_open")
    assert blocked["update_budget_scopes"] is True
    assert blocked["force_cost_ledger"] is False


def test_strict_gateway_store_unavailable_fails_closed_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, reservations, ledgers, binding = _install_open_breaker(monkeypatch)
    provider, model = binding.split("/", 1)
    monkeypatch.setattr(
        gateway,
        "_acquire_strict_fleet_breaker",
        lambda **_kwargs: (_ for _ in ()).throw(
            FleetBreakerUnavailable("fixture store unavailable")
        ),
    )

    result = gateway.invoke(
        "hello",
        purpose="fleet-breaker-store-test",
        preferred_provider=provider,
        model_override=model,
        model_fallbacks=(),
        skip_budget_check=True,
        enforce_atomic_reservation=True,
    )

    assert [event[0] for event in reservations.events] == ["reserve", "released"]
    assert result["provider"] == "rule_v0"
    assert any(
        row["status"] == "fleet_breaker_store_unavailable" for row in ledgers
    )


def test_strict_gateway_state_loss_after_provider_marks_unknown_and_stops_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, reservations, _ledgers, binding = _install_open_breaker(monkeypatch)
    provider, model = binding.split("/", 1)
    calls: list[str] = []
    monkeypatch.setattr(
        gateway,
        "_acquire_strict_fleet_breaker",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        gateway,
        "_complete_strict_fleet_breaker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FleetBreakerUnavailable("fixture completion unavailable")
        ),
    )

    def provider_call(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        called_model = str(kwargs.get("model_override") or model)
        calls.append(called_model)
        return {
            "status": "success",
            "provider": provider,
            "model": called_model,
            "text": "ok",
        }

    monkeypatch.setitem(gateway._PROVIDER_CALLERS, provider, provider_call)
    result = gateway.invoke(
        "hello",
        purpose="fleet-breaker-post-provider-test",
        preferred_provider=provider,
        model_override=model,
        model_fallbacks=((provider, "gpt-5.4"),),
        skip_budget_check=True,
        enforce_atomic_reservation=True,
    )

    assert calls == [model]
    assert [event[0] for event in reservations.events] == [
        "reserve",
        "started",
        "unknown",
    ]
    assert result["provider"] == "rule_v0"
    assert result["reason"] == "fleet_breaker_store_unavailable_after_provider"


def test_legacy_text_gateway_state_loss_after_provider_stops_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, reservations, _ledgers, binding = _install_open_breaker(monkeypatch)
    provider, model = binding.split("/", 1)
    calls: list[str] = []
    monkeypatch.setattr(
        gateway,
        "_acquire_strict_fleet_breaker",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        gateway,
        "_complete_strict_fleet_breaker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FleetBreakerUnavailable("fixture completion unavailable")
        ),
    )

    def provider_call(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        called_model = str(kwargs.get("model_override") or model)
        calls.append(called_model)
        return {
            "status": "success",
            "provider": provider,
            "model": called_model,
            "text": "ok",
        }

    monkeypatch.setitem(gateway._PROVIDER_CALLERS, provider, provider_call)
    result = gateway.invoke(
        "hello",
        purpose="fleet-breaker-legacy-post-provider-test",
        preferred_provider=provider,
        model_override=model,
        model_fallbacks=((provider, "gpt-5.4"),),
        skip_budget_check=True,
    )

    assert calls == [model]
    assert reservations.events == []
    assert result["provider"] == "rule_v0"
    assert result["reason"] == "fleet_breaker_store_unavailable_after_provider"


@pytest.mark.pg
def test_real_postgres_allows_one_half_open_probe_and_rejects_stale_fence(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg
    from psycopg import sql

    from app.db.connection import PostgresCompatConnection

    schema = f"vkpi_llm_breaker_test_{uuid.uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "266_vkpi_llm_fleet_breaker.sql"
    ).read_text(encoding="utf-8")
    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        admin.execute(migration)

        def build_connection() -> PostgresCompatConnection:
            raw = psycopg.connect(pg_dsn, connect_timeout=5)
            raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            raw.commit()
            return PostgresCompatConnection(raw, pool=None)

        monkeypatch.setattr(llm_fleet_breaker, "is_postgres_runtime", lambda: True)
        monkeypatch.setattr(llm_fleet_breaker, "open_standalone_conn", build_connection)
        monkeypatch.setattr(
            llm_fleet_breaker,
            "close_standalone_conn",
            lambda conn: conn.close(),
        )
        monkeypatch.setenv("VKPI_LLM_FLEET_BREAKER_FAILURE_THRESHOLD", "1")
        monkeypatch.setenv("VKPI_LLM_FLEET_BREAKER_RECOVERY_SECONDS", "1")
        monkeypatch.setenv("VKPI_LLM_FLEET_BREAKER_HALF_OPEN_LEASE_SECONDS", "5")

        started = datetime.now(timezone.utc)
        closed = llm_fleet_breaker.acquire_fleet_breaker_permit(
            "openai", "gpt-test-exact", owner="closed", now=started
        )
        opened = llm_fleet_breaker.record_fleet_breaker_failure(
            closed, "provider_5xx", now=started
        )
        assert opened.state == "open"

        def claim_probe(owner: str) -> Any:
            try:
                return llm_fleet_breaker.acquire_fleet_breaker_permit(
                    "openai",
                    "gpt-test-exact",
                    owner=owner,
                    now=started + timedelta(seconds=2),
                )
            except FleetBreakerOpen as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(claim_probe, ("probe-one", "probe-two-racing"))
            )
        permits = [claim for claim in claims if not isinstance(claim, FleetBreakerOpen)]
        blocked = [claim for claim in claims if isinstance(claim, FleetBreakerOpen)]
        assert len(permits) == 1
        assert len(blocked) == 1
        first_probe = permits[0]
        assert first_probe.state == "half_open"
        with pytest.raises(FleetBreakerOpen):
            llm_fleet_breaker.acquire_fleet_breaker_permit(
                "openai",
                "gpt-test-exact",
                owner="probe-two-early",
                now=started + timedelta(seconds=3),
            )

        second_probe = llm_fleet_breaker.acquire_fleet_breaker_permit(
            "openai",
            "gpt-test-exact",
            owner="probe-two",
            now=started + timedelta(seconds=8),
        )
        assert second_probe.fence > first_probe.fence
        with pytest.raises(StaleFleetBreakerPermit):
            llm_fleet_breaker.record_fleet_breaker_success(first_probe)
        closed_again = llm_fleet_breaker.record_fleet_breaker_success(second_probe)
        assert closed_again.state == "closed"
    finally:
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()
