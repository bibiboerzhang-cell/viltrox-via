from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from scripts.ops import vkpi_stage1_model_canary as canary


class _FakeReservations:
    def __init__(
        self,
        *,
        fail_reserve: bool = False,
        fail_start: bool = False,
        fail_settle: bool = False,
        fail_unknown: bool = False,
    ) -> None:
        self.fail_reserve = fail_reserve
        self.fail_start = fail_start
        self.fail_settle = fail_settle
        self.fail_unknown = fail_unknown
        self.reserved: list[dict[str, object]] = []
        self.started: list[str] = []
        self.settled: list[tuple[str, Decimal | float | str]] = []
        self.unknown: list[str] = []
        self.released: list[str] = []
        self.scopes: dict[str, tuple[str, ...]] = {}

    def reserve_llm_budget(self, **kwargs):
        if self.fail_reserve:
            raise RuntimeError("secret budget failure")
        self.reserved.append(kwargs)
        provider = str(kwargs.get("provider") or "")
        provider_scope = {
            "google": "provider:gemini",
            "anthropic": "provider:claude",
        }.get(provider, f"provider:{provider}")
        reservation_key = f"reservation-{len(self.reserved)}"
        cumulative_scopes = (
            "monthly_total",
            provider_scope,
            canary.CANARY_COST_SCOPE,
        )
        self.scopes[reservation_key] = cumulative_scopes
        return SimpleNamespace(
            reservation_key=reservation_key,
            cumulative_scopes=cumulative_scopes,
        )

    def mark_llm_provider_started(self, reservation_key: str) -> None:
        if self.fail_start:
            raise RuntimeError("secret start failure")
        self.started.append(reservation_key)

    def settle_llm_reservation(
        self, reservation_key: str, actual_cost_usd: float
    ) -> dict[str, object]:
        if self.fail_settle:
            raise RuntimeError("secret settle failure")
        self.settled.append((reservation_key, actual_cost_usd))
        actual_micro = int(round(float(actual_cost_usd) * 1_000_000))
        scopes = list(self.scopes[reservation_key])
        return {
            "settled": True,
            "actual_cost_usd": actual_cost_usd,
            "actual_cost_micro_usd": actual_micro,
            "scopes_updated": scopes,
            "scope_deltas_micro_usd": {
                scope: actual_micro for scope in scopes
            },
            "readback_verified": True,
        }

    def mark_llm_provider_unknown(self, reservation_key: str) -> bool:
        if self.fail_unknown:
            raise RuntimeError("secret unknown failure")
        self.unknown.append(reservation_key)
        return True

    def release_llm_reservation(self, reservation_key: str) -> bool:
        self.released.append(reservation_key)
        return True


def _ledger_collector(target: list[dict[str, object]]):
    def record(**kwargs):
        target.append(kwargs)
        cost_micro = int(kwargs.get("cost_micro_usd") or 0)
        return {
            "call": {
                "call_uid": f"test-{len(target)}",
                "cost_micro_usd": cost_micro,
            },
            "cost_ledger": {
                "recorded": True,
                "ledger_id": len(target),
                "cost_micro_usd": cost_micro,
            },
        }

    return record


def _authorized_environment(plan: canary.CanaryPlan) -> dict[str, str]:
    return {canary.AUTHORIZATION_ENV: plan.authorization_value}


def _success_invoker(
    binding: str,
    prompt: str,
    max_output_tokens: int,
    timeout_seconds: int,
) -> dict[str, object]:
    _provider, model = binding.split("/", 1)
    assert prompt == canary.CANARY_PROMPT
    assert 16 <= max_output_tokens <= canary.MAX_OUTPUT_TOKENS_HARD_LIMIT
    assert 1 <= timeout_seconds <= canary.MAX_PER_CALL_TIMEOUT_SECONDS
    return {
        "status": "success",
        "model": model,
        "text": "VKPI_STAGE1_CANARY_OK",
        "input_tokens": 12,
        "output_tokens": 7,
        "cost_micro_usd": 100,
        "latency_ms": 1,
        "secret_like_field": "must-never-appear",
    }


def test_default_is_repeatable_zero_call_dry_run() -> None:
    calls: list[str] = []

    def forbidden(*args, **kwargs):
        calls.append("called")
        raise AssertionError((args, kwargs))

    first = canary.run_canary(live_invoker=forbidden)
    second = canary.run_canary(live_invoker=forbidden)

    assert calls == []
    assert first == second
    assert first["mode"] == "dry_run"
    assert first["provider_calls_performed"] == 0
    assert first["claim_status"] == "descriptive_only"
    assert first["production_authorized"] is False
    assert first["attestation_status"] == "unsigned_not_readiness_evidence"
    assert first["safety_limits"]["unique_task_bindings"] == canary.EXPECTED_UNIQUE_BINDINGS == 6
    assert len(first["results"]) == 6
    assert {row["status"] for row in first["results"]} == {"dry_run"}
    expected_row_keys = {
        "binding",
        "requested_model",
        "response_model",
        "status",
        "latency_ms",
        "response_sha256",
        "claim_status",
    }
    assert all(set(row) == expected_row_keys for row in first["results"])


def test_live_requires_plan_bound_authorization_before_any_preflight_or_call() -> None:
    touched: list[str] = []

    report = canary.run_canary(
        live=True,
        environment={},
        live_invoker=lambda *_args: touched.append("invoke"),
        provider_configured=lambda _provider: touched.append("configured") or True,
        budget_checker=lambda _row: touched.append("budget") or True,
        is_production=False,
    )

    assert touched == []
    assert report["provider_calls_performed"] == 0
    assert report["authorization"]["authorized"] is False
    assert {row["status"] for row in report["results"]} == {
        "authorization_blocked"
    }


def test_authorization_is_invalidated_when_any_plan_limit_changes() -> None:
    original = canary.build_plan()
    changed = canary.build_plan(max_output_tokens=64)
    assert original.manifest_sha256 != changed.manifest_sha256

    report = canary.run_canary(
        live=True,
        max_output_tokens=64,
        environment=_authorized_environment(original),
        live_invoker=lambda *_args: pytest.fail("must not invoke"),
        provider_configured=lambda _provider: True,
        budget_checker=lambda _row: True,
        is_production=False,
    )
    assert report["provider_calls_performed"] == 0
    assert {row["status"] for row in report["results"]} == {
        "authorization_blocked"
    }


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"max_calls": 9}, "max_calls"),
        ({"max_output_tokens": 129}, "max_output_tokens"),
        ({"per_call_timeout_seconds": 31}, "per_call_timeout"),
        ({"total_timeout_seconds": 241}, "total_timeout"),
        ({"max_cost_usd": Decimal("0.11")}, "max_cost_usd"),
    ],
)
def test_hard_limits_fail_before_provider_io(kwargs: dict, reason: str) -> None:
    with pytest.raises(canary.CanarySafetyError, match=reason):
        canary.run_canary(
            live=True,
            live_invoker=lambda *_args: pytest.fail("must not invoke"),
            **kwargs,
        )


def test_cost_plan_ceiling_blocks_before_authorization_or_provider_io() -> None:
    report = canary.run_canary(
        live=True,
        max_cost_usd=Decimal("0.000001"),
        environment={canary.AUTHORIZATION_ENV: "irrelevant"},
        live_invoker=lambda *_args: pytest.fail("must not invoke"),
        provider_configured=lambda _provider: pytest.fail("must not preflight"),
        budget_checker=lambda _row: pytest.fail("must not preflight"),
        is_production=False,
    )
    assert report["provider_calls_performed"] == 0
    assert {row["status"] for row in report["results"]} == {
        "cost_plan_blocked"
    }


def test_exact_binding_selection_is_plan_bound_and_skips_other_bindings() -> None:
    target = "google/gemini-2.5-pro"
    plan = canary.build_plan(max_calls=1, only_bindings=(target,))

    assert [row.binding for row in plan.selected] == [target]
    report = canary.run_canary(
        max_calls=1,
        only_bindings=(target,),
        live_invoker=lambda *_args: pytest.fail("dry run must not invoke"),
    )
    status_by_binding = {row["binding"]: row["status"] for row in report["results"]}
    assert status_by_binding[target] == "dry_run"
    assert set(status_by_binding.values()) == {"dry_run", "not_selected"}


@pytest.mark.parametrize(
    "bindings",
    [
        ("google/not-in-task-inventory",),
        ("google/gemini-2.5-pro", "google/gemini-2.5-pro"),
    ],
)
def test_invalid_exact_binding_selection_fails_before_provider_io(
    bindings: tuple[str, ...],
) -> None:
    with pytest.raises(canary.CanarySafetyError):
        canary.run_canary(
            live=True,
            max_calls=2,
            only_bindings=bindings,
            live_invoker=lambda *_args: pytest.fail("must not invoke"),
        )


def test_authorized_live_run_calls_each_exact_binding_once_and_redacts_content() -> None:
    plan = canary.build_plan()
    calls: list[str] = []
    reservations = _FakeReservations()
    ledger: list[dict[str, object]] = []

    def invoker(*args):
        binding = str(args[0])
        calls.append(binding)
        return _success_invoker(*args)

    report = canary.run_canary(
        live=True,
        environment=_authorized_environment(plan),
        live_invoker=invoker,
        provider_configured=lambda _provider: True,
        budget_checker=lambda _row: True,
        reservation_manager=reservations,
        ledger_recorder=_ledger_collector(ledger),
        is_production=False,
    )

    assert calls == [row.binding for row in plan.selected]
    assert len(calls) == 6
    assert report["provider_calls_performed"] == 6
    assert report["all_selected_bindings_succeeded"] is True
    assert {row["status"] for row in report["results"]} == {"success"}
    assert all(len(row["response_sha256"]) == 64 for row in report["results"])
    assert len(reservations.reserved) == 6
    assert len(reservations.started) == 6
    assert len(reservations.settled) == 6
    assert reservations.unknown == []
    assert reservations.released == []
    assert len(ledger) == 6
    assert report["accounting"] == {
        "precision": "micro_usd",
        "required_for_live_success": True,
        "verified_calls": 6,
        "observed_cost_micro_usd": 600,
    }
    assert all(item["prompt"] == "" for item in ledger)
    assert all(item["update_budget_scopes"] is False for item in ledger)
    assert all(item["force_cost_ledger"] is True for item in ledger)
    assert all(
        item["metadata"]["request_content_recorded"] is False
        and item["metadata"]["response_content_recorded"] is False
        for item in ledger
    )
    rendered = json.dumps(report, ensure_ascii=False)
    assert canary.CANARY_PROMPT not in rendered
    assert "VKPI_STAGE1_CANARY_OK" not in rendered
    assert "must-never-appear" not in rendered
    assert plan.authorization_value not in rendered


def test_response_model_mismatch_is_descriptive_and_never_promotes_readiness() -> None:
    plan = canary.build_plan(max_calls=1)

    def mismatched(*_args):
        return {
            "status": "success",
            "model": "definitely-another-model",
            "text": "secret raw response",
            "input_tokens": 12,
            "output_tokens": 7,
            "cost_micro_usd": 100,
        }

    reservations = _FakeReservations()
    ledger: list[dict[str, object]] = []

    report = canary.run_canary(
        live=True,
        max_calls=1,
        environment=_authorized_environment(plan),
        live_invoker=mismatched,
        provider_configured=lambda _provider: True,
        budget_checker=lambda _row: True,
        reservation_manager=reservations,
        ledger_recorder=_ledger_collector(ledger),
        is_production=False,
    )
    selected = report["results"][0]
    assert selected["status"] == "model_mismatch"
    assert selected["claim_status"] == "descriptive_only"
    assert report["production_authorized"] is False
    assert report["attestation_status"] == "unsigned_not_readiness_evidence"
    assert "secret raw response" not in json.dumps(report)
    assert len(reservations.settled) == 1
    assert len(ledger) == 1


def test_nonempty_but_wrong_canary_text_is_invalid_without_content_leak() -> None:
    plan = canary.build_plan(max_calls=1)
    reservations = _FakeReservations()
    ledger: list[dict[str, object]] = []

    def wrong_text(binding: str, *_args):
        _provider, model = binding.split("/", 1)
        return {
            "status": "success",
            "model": model,
            "text": "not the canary contract and must not leak",
            "input_tokens": 12,
            "output_tokens": 7,
            "cost_micro_usd": 100,
        }

    report = canary.run_canary(
        live=True,
        max_calls=1,
        environment=_authorized_environment(plan),
        live_invoker=wrong_text,
        provider_configured=lambda _provider: True,
        budget_checker=lambda _row: True,
        reservation_manager=reservations,
        ledger_recorder=_ledger_collector(ledger),
        is_production=False,
    )

    assert report["results"][0]["status"] == "invalid_response"
    assert report["all_selected_bindings_succeeded"] is False
    assert report["response_contract_sha256"] == canary._sha256_text(
        canary.CANARY_EXPECTED_RESPONSE
    )
    assert "not the canary contract" not in json.dumps(report)


@pytest.mark.parametrize(
    ("is_production", "environment", "expected"),
    [
        (True, {}, "production_forbidden"),
        (
            False,
            {"VKPI_LLM_GATEWAY_FORCE_OFFLINE": "1"},
            "force_offline",
        ),
    ],
)
def test_production_and_force_offline_remain_fail_closed(
    is_production: bool,
    environment: dict[str, str],
    expected: str,
) -> None:
    plan = canary.build_plan()
    environment[canary.AUTHORIZATION_ENV] = plan.authorization_value
    report = canary.run_canary(
        live=True,
        environment=environment,
        live_invoker=lambda *_args: pytest.fail("must not invoke"),
        provider_configured=lambda _provider: True,
        budget_checker=lambda _row: True,
        is_production=is_production,
    )
    assert report["provider_calls_performed"] == 0
    assert {row["status"] for row in report["results"]} == {expected}


def test_provider_and_budget_preflight_fail_closed_before_first_call() -> None:
    plan = canary.build_plan()
    for provider_configured, budget_checker, expected in (
        (lambda _provider: False, lambda _row: True, "not_configured"),
        (lambda _provider: True, lambda _row: False, "budget_blocked"),
    ):
        reservations = _FakeReservations()
        report = canary.run_canary(
            live=True,
            environment=_authorized_environment(plan),
            live_invoker=lambda *_args: pytest.fail("must not invoke"),
            provider_configured=provider_configured,
            budget_checker=budget_checker,
            reservation_manager=reservations,
            ledger_recorder=lambda **_kwargs: pytest.fail("must not ledger"),
            is_production=False,
        )
        assert report["provider_calls_performed"] == 0
        assert {row["status"] for row in report["results"]} == {expected}
        assert reservations.reserved == []


def test_atomic_reservation_failure_is_zero_call_and_stops_all_models() -> None:
    plan = canary.build_plan()
    reservations = _FakeReservations(fail_reserve=True)
    ledger: list[dict[str, object]] = []
    calls: list[str] = []

    report = canary.run_canary(
        live=True,
        environment=_authorized_environment(plan),
        live_invoker=lambda binding, *_args: calls.append(binding),
        provider_configured=lambda _provider: True,
        budget_checker=lambda _row: True,
        reservation_manager=reservations,
        ledger_recorder=_ledger_collector(ledger),
        is_production=False,
    )

    assert calls == []
    assert ledger == []
    assert report["provider_calls_performed"] == 0
    assert report["results"][0]["status"] == "reservation_failed"
    assert {
        row["status"] for row in report["results"][1:]
    } == {"not_attempted_after_fail_closed"}


def test_provider_exception_is_ledgered_unknown_and_stops_next_model() -> None:
    plan = canary.build_plan()
    reservations = _FakeReservations()
    ledger: list[dict[str, object]] = []
    calls: list[str] = []

    def explode(binding: str, *_args):
        calls.append(binding)
        raise RuntimeError("provider secret must not escape")

    report = canary.run_canary(
        live=True,
        environment=_authorized_environment(plan),
        live_invoker=explode,
        provider_configured=lambda _provider: True,
        budget_checker=lambda _row: True,
        reservation_manager=reservations,
        ledger_recorder=_ledger_collector(ledger),
        is_production=False,
    )

    assert len(calls) == 1
    assert report["provider_calls_performed"] == 1
    assert len(reservations.reserved) == 1
    assert len(reservations.started) == 1
    assert reservations.unknown == ["reservation-1"]
    assert reservations.settled == []
    assert len(ledger) == 1
    assert ledger[0]["status"] == "invoker_exception"
    assert report["results"][0]["status"] == "invoker_exception"
    assert {
        row["status"] for row in report["results"][1:]
    } == {"not_attempted_after_fail_closed"}
    assert "provider secret" not in json.dumps(report)


def test_ledger_failure_marks_unknown_and_stops_before_second_model() -> None:
    plan = canary.build_plan()
    reservations = _FakeReservations()
    calls: list[str] = []

    def invoker(*args):
        calls.append(str(args[0]))
        return _success_invoker(*args)

    def broken_ledger(**_kwargs):
        raise RuntimeError("ledger secret must not escape")

    report = canary.run_canary(
        live=True,
        environment=_authorized_environment(plan),
        live_invoker=invoker,
        provider_configured=lambda _provider: True,
        budget_checker=lambda _row: True,
        reservation_manager=reservations,
        ledger_recorder=broken_ledger,
        is_production=False,
    )

    assert len(calls) == 1
    assert reservations.unknown == ["reservation-1"]
    assert reservations.settled == []
    assert report["results"][0]["status"] == "ledger_failed"
    assert {
        row["status"] for row in report["results"][1:]
    } == {"not_attempted_after_fail_closed"}
    assert "ledger secret" not in json.dumps(report)


def test_settlement_failure_keeps_reservation_open_and_stops() -> None:
    plan = canary.build_plan()
    reservations = _FakeReservations(fail_settle=True)
    ledger: list[dict[str, object]] = []
    calls: list[str] = []

    def invoker(*args):
        calls.append(str(args[0]))
        return _success_invoker(*args)

    report = canary.run_canary(
        live=True,
        environment=_authorized_environment(plan),
        live_invoker=invoker,
        provider_configured=lambda _provider: True,
        budget_checker=lambda _row: True,
        reservation_manager=reservations,
        ledger_recorder=_ledger_collector(ledger),
        is_production=False,
    )

    assert len(calls) == 1
    assert len(ledger) == 1
    assert reservations.unknown == ["reservation-1"]
    assert reservations.settled == []
    assert report["results"][0]["status"] == "settlement_failed"
    assert {
        row["status"] for row in report["results"][1:]
    } == {"not_attempted_after_fail_closed"}


def test_four_ledger_mismatch_stops_before_second_model() -> None:
    plan = canary.build_plan()
    reservations = _FakeReservations()
    ledger: list[dict[str, object]] = []
    calls: list[str] = []
    original_settle = reservations.settle_llm_reservation

    def mismatched_settlement(
        reservation_key: str, actual_cost_usd: float
    ) -> dict[str, object]:
        receipt = original_settle(reservation_key, actual_cost_usd)
        deltas = dict(receipt["scope_deltas_micro_usd"])
        deltas["monthly_total"] = int(deltas["monthly_total"]) + 1
        receipt["scope_deltas_micro_usd"] = deltas
        return receipt

    reservations.settle_llm_reservation = mismatched_settlement

    def invoker(*args):
        calls.append(str(args[0]))
        return _success_invoker(*args)

    report = canary.run_canary(
        live=True,
        environment=_authorized_environment(plan),
        live_invoker=invoker,
        provider_configured=lambda _provider: True,
        budget_checker=lambda _row: True,
        reservation_manager=reservations,
        ledger_recorder=_ledger_collector(ledger),
        is_production=False,
    )

    assert len(calls) == 1
    assert len(ledger) == 1
    assert len(reservations.settled) == 1
    assert reservations.unknown == []
    assert report["results"][0]["status"] == "accounting_mismatch"
    assert {
        row["status"] for row in report["results"][1:]
    } == {"not_attempted_after_fail_closed"}
    assert report["accounting"]["verified_calls"] == 0


def test_cli_defaults_to_dry_run() -> None:
    args = canary.parse_args([])
    assert args.live is False
    assert args.max_calls == canary.EXPECTED_UNIQUE_BINDINGS == 6
    assert args.max_output_tokens <= 128
