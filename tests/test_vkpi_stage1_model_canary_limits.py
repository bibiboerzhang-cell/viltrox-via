"""Hermetic attempt boundaries; no provider, real reservation or business DB."""
from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.ops import vkpi_stage1_model_canary as canary
from tests.test_vkpi_stage1_model_canary import (
    _FakeReservations, _authorized_environment, _ledger_collector, _success_invoker,
)


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _TimedReservations(_FakeReservations):
    def __init__(self, clock, *, reserve_delay=0, start_delay=0, settle_delay=0,
                 release_outcome=True, fail_after_start=False):
        super().__init__()
        self.clock = clock
        self.reserve_delay = reserve_delay
        self.start_delay = start_delay
        self.settle_delay = settle_delay
        self.release_outcome = release_outcome
        self.fail_after_start = fail_after_start
        self.release_attempts = []

    def reserve_llm_budget(self, **kwargs):
        receipt = super().reserve_llm_budget(**kwargs)
        self.clock.advance(self.reserve_delay)
        return receipt

    def mark_llm_provider_started(self, key):
        super().mark_llm_provider_started(key)
        self.clock.advance(self.start_delay)
        if self.fail_after_start:
            raise RuntimeError("synthetic committed start failure")

    def settle_llm_reservation(self, key, amount):
        receipt = super().settle_llm_reservation(key, amount)
        self.clock.advance(self.settle_delay)
        return receipt

    def release_llm_reservation(self, key):
        self.release_attempts.append(key)
        if self.release_outcome == "raises":
            raise RuntimeError("synthetic release failure")
        # Match the real API: only state=reserved can be released.  This fake
        # must not incorrectly release a provider_started reservation.
        if key in self.started or self.release_outcome is False:
            return False
        return super().release_llm_reservation(key)


_PLAN = {
    "max_calls": 2,
    "only_bindings": ("openai/gpt-5.6-luna", "google/gemini-3.6-flash"),
    "max_output_tokens": 16,
    "per_call_timeout_seconds": 10,
    "total_timeout_seconds": 10,
    "max_cost_usd": Decimal("0.01"),
}


def _run(*, clock=None, reservations=None, invoker=_success_invoker,
         configured=None, budget=None, record=None, plan_options=None):
    clock = clock or _Clock()
    reservations = reservations or _TimedReservations(clock)
    ledger, calls = [], []
    options = {**_PLAN, **(plan_options or {})}
    plan = canary.build_plan(**options)

    def invoke(*args):
        calls.append(args)
        return invoker(*args)

    report = canary.run_canary(
        live=True, **options, environment=_authorized_environment(plan),
        live_invoker=invoke, provider_configured=configured or (lambda _: True),
        budget_checker=budget or (lambda _: True), reservation_manager=reservations,
        ledger_recorder=record or _ledger_collector(ledger), is_production=False,
        monotonic=clock,
    )
    selected = [row for row in report["results"] if row["status"] != "not_selected"]
    return report, selected, reservations, ledger, calls


def test_actual_spend_leaves_insufficient_headroom_before_next_reservation():
    def invoke(*args):
        return {**_success_invoker(*args), "cost_micro_usd": 999}

    report, rows, reservations, ledger, calls = _run(
        invoker=invoke, plan_options={"max_cost_usd": Decimal("0.001")},
    )
    assert len(calls) == len(reservations.reserved) == len(ledger) == 1
    assert [row["status"] for row in rows] == ["success", "cost_plan_blocked"]
    assert report["accounting"]["observed_cost_micro_usd"] == 999
    assert len(reservations.settled) == 1
    assert report["all_selected_bindings_succeeded"] is False


def test_exact_remaining_estimate_is_allowed_without_erasing_actual_cost():
    options = {**_PLAN, "max_cost_usd": Decimal("0.001")}
    plan = canary.build_plan(**options)
    last_estimate_micro = int(plan.selected[1].estimated_cost_usd * 1_000_000)
    amounts = iter((1000 - last_estimate_micro, last_estimate_micro))

    def invoke(*args):
        return {**_success_invoker(*args), "cost_micro_usd": next(amounts)}

    report, _, reservations, _, calls = _run(invoker=invoke, plan_options=options)
    assert len(calls) == len(reservations.settled) == 2
    assert report["accounting"]["observed_cost_micro_usd"] == 1000
    assert report["all_selected_bindings_succeeded"] is True


def test_actual_cost_over_ceiling_is_settled_and_reported_not_clamped():
    def invoke(*args):
        return {**_success_invoker(*args), "cost_micro_usd": 1200}

    report, rows, reservations, ledger, calls = _run(
        invoker=invoke, plan_options={"max_cost_usd": Decimal("0.001")},
    )
    assert len(calls) == len(reservations.settled) == 1
    assert ledger[0]["cost_micro_usd"] == 1200
    assert report["accounting"]["observed_cost_micro_usd"] == 1200
    assert [row["status"] for row in rows] == ["cost_ceiling_exceeded", "not_attempted_after_fail_closed"]


@pytest.mark.parametrize("stage", ["configured", "budget"])
def test_preflight_time_is_part_of_deadline(stage):
    clock = _Clock()

    def slow(_):
        clock.advance(10)
        return True

    kwargs = {stage: slow}
    report, rows, reservations, ledger, calls = _run(clock=clock, **kwargs)
    assert calls == reservations.reserved == ledger == []
    assert report["provider_calls_performed"] == 0
    assert rows[0]["status"] == "total_timeout"


@pytest.mark.parametrize("release_outcome", [True, False, "raises"])
def test_reservation_time_expiry_never_starts_provider_and_reports_release(release_outcome):
    clock = _Clock()
    reservations = _TimedReservations(clock, reserve_delay=10, release_outcome=release_outcome)
    report, rows, reservations, ledger, calls = _run(clock=clock, reservations=reservations)
    assert calls == reservations.started == reservations.unknown == reservations.settled == ledger == []
    assert report["provider_calls_performed"] == 0
    assert reservations.release_attempts == ["reservation-1"]
    expected = "total_timeout" if release_outcome is True else "total_timeout_reservation_retained"
    assert rows[0]["status"] == expected
    assert reservations.released == (["reservation-1"] if release_outcome is True else [])


@pytest.mark.parametrize("fail_after_start", [False, True])
def test_started_but_not_called_reservation_is_not_falsely_released(fail_after_start):
    clock = _Clock()
    reservations = _TimedReservations(clock, start_delay=10, fail_after_start=fail_after_start)
    report, rows, reservations, ledger, calls = _run(clock=clock, reservations=reservations)
    assert calls == reservations.released == reservations.unknown == reservations.settled == ledger == []
    assert reservations.started == reservations.release_attempts == ["reservation-1"]
    assert report["provider_calls_performed"] == 0
    reason = "reservation_start_failed" if fail_after_start else "total_timeout"
    assert rows[0]["status"] == f"{reason}_reservation_retained"


def test_transport_timeout_uses_remaining_after_reserve_and_start():
    clock = _Clock()
    reservations = _TimedReservations(clock, reserve_delay=2.2, start_delay=1.1)
    report, rows, _, _, calls = _run(
        clock=clock, reservations=reservations,
        plan_options={"max_calls": 1, "only_bindings": ("openai/gpt-5.6-luna",)},
    )
    assert len(calls) == 1
    assert calls[0][3] == 6
    assert rows[0]["latency_ms"] == 0  # preparation belongs to deadline, not provider latency
    assert report["all_selected_bindings_succeeded"] is True


@pytest.mark.parametrize("stage", ["provider", "ledger", "settle"])
def test_late_success_is_accounted_before_timeout_and_stops_following_call(stage):
    clock = _Clock()
    reservations = _TimedReservations(clock, settle_delay=11 if stage == "settle" else 0)
    ledger = []
    collect = _ledger_collector(ledger)

    def invoke(*args):
        if stage == "provider":
            clock.advance(11)
        return _success_invoker(*args)

    def record(**kwargs):
        if stage == "ledger":
            clock.advance(11)
        return collect(**kwargs)

    report, rows, reservations, _, calls = _run(
        clock=clock, reservations=reservations, invoker=invoke, record=record,
    )
    assert len(calls) == len(ledger) == len(reservations.settled) == 1
    assert reservations.unknown == reservations.released == []
    assert report["accounting"]["verified_calls"] == 1
    assert report["accounting"]["observed_cost_micro_usd"] == 100
    assert [row["status"] for row in rows] == ["timeout", "not_attempted_after_fail_closed"]
    assert report["all_selected_bindings_succeeded"] is False


@pytest.mark.parametrize("text", [canary.CANARY_EXPECTED_RESPONSE, ""])
@pytest.mark.parametrize("usage", [{}, {"input_tokens": 0, "output_tokens": 0}, {"input_tokens": 12}])
@pytest.mark.parametrize("reported_cost", [0, 100])
def test_missing_or_zero_usage_retains_unknown_reservation_even_with_reported_cost(text, usage, reported_cost):
    def invoke(binding, *_args):
        return {"status": "success", "model": binding.split("/", 1)[1],
                "text": text, "cost_micro_usd": reported_cost, **usage}

    report, rows, reservations, ledger, calls = _run(invoker=invoke)
    assert len(calls) == len(reservations.reserved) == len(ledger) == 1
    assert reservations.unknown == ["reservation-1"]
    assert reservations.settled == reservations.released == []
    assert report["accounting"]["verified_calls"] == 0
    assert report["accounting"]["observed_cost_micro_usd"] == 0
    assert rows[0]["status"] == ledger[0]["status"] == "cost_accounting_failed"
    assert ledger[0]["metadata"]["cost_accounting_status"] == "unknown"
    assert ledger[0]["metadata"]["cost_zero_is_placeholder"] is True
    assert rows[1]["status"] == "not_attempted_after_fail_closed"


def test_unknown_mark_failure_is_reported_without_settling_or_releasing():
    reservations = _FakeReservations(fail_unknown=True)

    def invoke(binding, *_args):
        return {"status": "success", "model": binding.split("/", 1)[1],
                "text": canary.CANARY_EXPECTED_RESPONSE, "cost_micro_usd": 0}

    report, rows, reservations, _, calls = _run(reservations=reservations, invoker=invoke)
    assert len(calls) == 1
    assert reservations.started == ["reservation-1"]
    assert reservations.settled == reservations.released == []
    assert rows[0]["status"] == "reservation_unknown_mark_failed"
    assert report["all_selected_bindings_succeeded"] is False


@pytest.mark.parametrize("text", [canary.CANARY_EXPECTED_RESPONSE, ""])
def test_nonzero_usage_without_reported_cost_still_uses_exact_model_pricing(text):
    def invoke(*args):
        raw = _success_invoker(*args)
        raw.pop("cost_micro_usd")
        raw["text"] = text
        return raw

    report, rows, reservations, ledger, calls = _run(invoker=invoke)
    assert len(calls) == len(reservations.settled) == len(ledger) == 2
    assert reservations.unknown == []
    assert report["accounting"]["verified_calls"] == 2
    assert report["accounting"]["observed_cost_micro_usd"] > 0
    assert all(item["metadata"]["cost_accounting_status"] == "known" for item in ledger)
    assert all(row["status"] == ("success" if text else "empty_response") for row in rows)


@pytest.mark.parametrize("canary_scope_configured", [False, True])
def test_default_budget_checker_requires_existing_scope_before_reserve_or_provider(
    monkeypatch, canary_scope_configured,
):
    from app.domains.costs import budget_guard, budget_readonly

    inspected_scopes = []

    def inspect(scope, *, estimated_cost):
        inspected_scopes.append(scope)
        configured = scope != canary.CANARY_COST_SCOPE or canary_scope_configured
        return {"configured": configured, "allowed": configured, "read_only": True,
                "estimated_cost_usd": estimated_cost}

    monkeypatch.setattr(budget_readonly, "get_budget_status_readonly", inspect)
    monkeypatch.setattr(canary.llm_gateway, "_budget_guard", lambda: budget_guard)
    monkeypatch.setattr(canary.llm_gateway, "_monthly_budget_cents", lambda: 10)
    monkeypatch.setattr(canary.llm_gateway, "_budget_remaining_cents", lambda: 10)
    options = {**_PLAN, "max_calls": 1, "only_bindings": ("openai/gpt-5.6-luna",)}
    plan = canary.build_plan(**options)
    reservations = _FakeReservations()
    calls, ledger = [], []

    def invoke(*args):
        calls.append(args)
        return _success_invoker(*args)

    report = canary.run_canary(
        live=True, **options, environment=_authorized_environment(plan),
        live_invoker=invoke, provider_configured=lambda _: True,
        reservation_manager=reservations, ledger_recorder=_ledger_collector(ledger),
        is_production=False,
        # Deliberately omit budget_checker: exercise the CLI's real default.
    )
    assert canary.CANARY_COST_SCOPE in inspected_scopes
    expected_calls = 1 if canary_scope_configured else 0
    assert report["provider_calls_performed"] == expected_calls
    assert len(calls) == len(reservations.reserved) == len(ledger) == expected_calls
    assert len(reservations.settled) == expected_calls
    selected = next(row for row in report["results"] if row["status"] != "not_selected")
    assert selected["status"] == ("success" if canary_scope_configured else "budget_blocked")
    assert report["all_selected_bindings_succeeded"] is canary_scope_configured
