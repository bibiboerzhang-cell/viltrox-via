from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from pathlib import Path

import pytest

from app.platform.llm_budget_reservations import LlmBudgetBlocked


def _authorize(monkeypatch, gateway, allowed: bool = True) -> None:
    monkeypatch.setattr(
        gateway,
        "exact_binding_readiness_from_environment",
        lambda binding: (
            {"binding": binding, "production_ready": allowed},
            {"source": "test_dual_signed_fixture"},
        ),
    )


class _Reservations:
    def __init__(self, *, block: bool = False) -> None:
        self.block = block
        self.events: list[tuple[str, Any]] = []

    def reserve_llm_budget(self, **kwargs):
        self.events.append(("reserve", kwargs))
        if self.block:
            raise LlmBudgetBlocked("hard_stop_or_projected_cap", scope="monthly_total")
        return SimpleNamespace(reservation_key="llmres-test")

    def mark_llm_provider_started(self, key: str) -> None:
        self.events.append(("started", key))

    def settle_llm_reservation(self, key: str, actual: float) -> dict[str, Any]:
        self.events.append(("settled", (key, actual)))
        return {"settled": True, "actual_cost_usd": actual}

    def mark_llm_provider_unknown(self, key: str) -> bool:
        self.events.append(("unknown", key))
        return True

    def release_llm_reservation(self, key: str) -> bool:
        self.events.append(("released", key))
        return True


def _install_gateway(monkeypatch, *, readiness: bool = True, blocked: bool = False):
    from app.platform import llm_gateway as gateway

    reservations = _Reservations(block=blocked)
    ledgers: list[dict[str, Any]] = []
    _authorize(monkeypatch, gateway, readiness)
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda provider: provider == "anthropic")
    monkeypatch.setattr(gateway, "_budget_allows_provider", lambda *_a, **_k: (True, []))
    monkeypatch.setattr(gateway, "_llm_budget_reservations", lambda: reservations)
    monkeypatch.setattr(gateway, "record_call", lambda **kwargs: ledgers.append(kwargs) or {})
    return gateway, reservations, ledgers


def _invoke(gateway):
    return gateway.invoke(
        "hello",
        purpose="strict-unit",
        preferred_provider="anthropic",
        model_override="claude-opus-4-7",
        model_fallbacks=(),
        skip_budget_check=True,
        enforce_atomic_reservation=True,
    )


def test_readiness_block_prevents_reservation_and_provider(monkeypatch) -> None:
    gateway, reservations, _ledgers = _install_gateway(monkeypatch, readiness=False)
    monkeypatch.setitem(
        gateway._PROVIDER_CALLERS,
        "anthropic",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider must not run")),
    )

    result = _invoke(gateway)

    assert result["provider"] == "rule_v0"
    assert reservations.events == []
    assert result["errors"][0]["error"] == "readiness_not_production_ready"


def test_budget_reservation_block_prevents_provider_and_records_both_ledgers(monkeypatch) -> None:
    gateway, reservations, ledgers = _install_gateway(monkeypatch, blocked=True)
    monkeypatch.setitem(
        gateway._PROVIDER_CALLERS,
        "anthropic",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider must not run")),
    )

    result = _invoke(gateway)

    assert result["provider"] == "rule_v0"
    assert [event[0] for event in reservations.events] == ["reserve"]
    provider_attempt = next(row for row in ledgers if row["provider"] == "anthropic")
    assert provider_attempt["status"] == "budget_blocked"
    assert provider_attempt["update_budget_scopes"] is False
    assert provider_attempt["force_cost_ledger"] is True


def test_success_reserves_starts_records_then_settles_without_double_budget(monkeypatch) -> None:
    gateway, reservations, ledgers = _install_gateway(monkeypatch)

    def record_call(**kwargs):
        reservations.events.append(("ledger", kwargs["status"]))
        ledgers.append(kwargs)
        return {}

    monkeypatch.setattr(gateway, "record_call", record_call)

    def provider(*_args, **_kwargs):
        reservations.events.append(("provider", None))
        return {
            "status": "success",
            "provider": "anthropic",
            "model": "claude-opus-4-7",
            "text": "ok",
            "input_tokens": 100,
            "output_tokens": 20,
        }

    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "anthropic", provider)

    result = _invoke(gateway)

    assert [event[0] for event in reservations.events] == [
        "reserve",
        "started",
        "provider",
        "ledger",
        "settled",
    ]
    assert result["status"] == "success"
    assert result["budget_reservation_key"] == "llmres-test"
    success = next(row for row in ledgers if row["status"] == "success")
    assert success["update_budget_scopes"] is False
    assert success["force_cost_ledger"] is True


def test_text_atomic_ledger_failure_keeps_reservation_open_and_stops_fallback(
    monkeypatch,
) -> None:
    gateway, reservations, _ledgers = _install_gateway(monkeypatch)

    def provider(*_args, **_kwargs):
        reservations.events.append(("provider", None))
        return {
            "status": "success",
            "provider": "anthropic",
            "model": "claude-opus-4-7",
            "text": "ok",
            "input_tokens": 100,
            "output_tokens": 20,
        }

    def ledger_down(**_kwargs):
        reservations.events.append(("ledger", None))
        raise RuntimeError("secret ledger outage")

    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "anthropic", provider)
    monkeypatch.setattr(gateway, "record_call", ledger_down)

    result = _invoke(gateway)

    assert [event[0] for event in reservations.events] == [
        "reserve",
        "started",
        "provider",
        "ledger",
        "unknown",
    ]
    assert result["provider"] == "rule_v0"
    assert result["reason"] == "audit_ledger_unavailable"
    assert result["budget_reservation_key"] == "llmres-test"
    assert "secret ledger outage" not in str(result)


def test_text_atomic_settlement_failure_hard_stops_two_candidate_chain(
    monkeypatch,
) -> None:
    gateway, reservations, ledgers = _install_gateway(monkeypatch)
    provider_models: list[str] = []

    def provider(*_args, **kwargs):
        model = str(kwargs.get("model_override") or "")
        provider_models.append(model)
        reservations.events.append(("provider", model))
        return {
            "status": "success",
            "provider": "anthropic",
            "model": model,
            "text": "ok",
            "input_tokens": 100,
            "output_tokens": 20,
        }

    def settlement_down(key: str, actual: float):
        reservations.events.append(("settled", (key, actual)))
        raise RuntimeError("secret settlement outage")

    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "anthropic", provider)
    reservations.settle_llm_reservation = settlement_down

    result = gateway.invoke(
        "hello",
        purpose="strict-unit",
        preferred_provider="anthropic",
        model_override="claude-opus-4-7",
        model_fallbacks=(("anthropic", "claude-sonnet-4-6"),),
        skip_budget_check=True,
        enforce_atomic_reservation=True,
    )

    assert provider_models == ["claude-opus-4-7"]
    assert [event[0] for event in reservations.events] == [
        "reserve",
        "started",
        "provider",
        "settled",
        "unknown",
    ]
    assert [row["status"] for row in ledgers] == ["success"]
    assert result["provider"] == "rule_v0"
    assert result["reason"] == "reservation_settlement_failed"
    assert result["budget_reservation_key"] == "llmres-test"
    assert "secret settlement outage" not in str(result)


def test_provider_exception_marks_unknown_and_records_attempt(monkeypatch) -> None:
    gateway, reservations, ledgers = _install_gateway(monkeypatch)

    def provider(*_args, **_kwargs):
        reservations.events.append(("provider", None))
        raise RuntimeError("fixture")

    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "anthropic", provider)

    result = _invoke(gateway)

    assert result["provider"] == "rule_v0"
    assert [event[0] for event in reservations.events] == [
        "reserve",
        "started",
        "provider",
        "unknown",
    ]
    attempt = next(row for row in ledgers if row["provider"] == "anthropic")
    assert attempt["status"] == "provider_exception"
    assert attempt["metadata"]["provider_error_type"] == "RuntimeError"


def test_exact_model_mismatch_is_settled_and_rejected(monkeypatch) -> None:
    gateway, reservations, ledgers = _install_gateway(monkeypatch)
    monkeypatch.setitem(
        gateway._PROVIDER_CALLERS,
        "anthropic",
        lambda *_a, **_k: {
            "status": "success",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "text": "wrong model",
            "input_tokens": 10,
            "output_tokens": 5,
        },
    )

    result = _invoke(gateway)

    assert result["provider"] == "rule_v0"
    assert "settled" in [event[0] for event in reservations.events]
    mismatch = next(row for row in ledgers if row["status"] == "model_mismatch")
    assert mismatch["update_budget_scopes"] is False
    assert mismatch["metadata"]["requested_model"] == "claude-opus-4-7"


def test_production_wrapper_forces_exact_authoritative_chain(monkeypatch) -> None:
    from app.platform import llm_production

    captured: dict[str, Any] = {}

    def invoke(prompt: str, **kwargs):
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "anthropic",
            "model": "claude-opus-4-7",
            "text": "ok",
        }

    monkeypatch.setattr(llm_production.llm_gateway, "invoke", invoke)
    assert llm_production.generate_text(
        "hello",
        provider="anthropic",
        model="claude-opus-4-7",
        purpose="unit",
        metadata={"phase": "evaluation"},
    )["text"] == "ok"
    assert captured["model_fallbacks"] == ()
    assert captured["enforce_atomic_reservation"] is True
    assert captured["require_runtime_verified"] is True
    assert captured["metadata"]["phase"] == "evaluation"
    assert captured["metadata"]["entrypoint"] == "llm_production_text_v1"


def test_production_json_wrapper_forces_exact_atomic_single_attempt(monkeypatch) -> None:
    from app.platform import llm_production

    captured: dict[str, Any] = {}

    def invoke_json(prompt: str, **kwargs):
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "json": {"answer": "ok"},
        }

    monkeypatch.setattr(llm_production.llm_gateway, "invoke_json", invoke_json)
    result = llm_production.generate_json(
        "hello",
        provider="openai",
        model="gpt-5.4-mini",
        purpose="unit-json",
        required_keys=("answer",),
        metadata={"surface": "unit"},
    )
    assert result["json"] == {"answer": "ok"}
    assert captured["model_fallbacks"] == ()
    assert captured["max_provider_attempts"] == 1
    assert captured["enforce_atomic_reservation"] is True
    assert captured["require_runtime_verified"] is True
    assert captured["metadata"]["entrypoint"] == "llm_production_json_v1"


def test_migrated_high_risk_text_paths_have_no_direct_provider_generation() -> None:
    root = Path(__file__).resolve().parents[1]
    targets = (
        "backend/app/api/routers/brand_analysis.py",
        "backend/app/services/kol/content_scorer.py",
        "backend/app/services/kol/account_dossier.py",
        "backend/app/services/intelligence/brand.py",
        "backend/app/services/intelligence/market.py",
        "backend/app/services/ai/analyzers/gpt_prefilter.py",
        "backend/app/services/via/model_router.py",
    )
    forbidden = (
        ".messages.create(",
        ".responses.create(",
        ".models.generate_content(",
    )
    for target in targets:
        source = (root / target).read_text(encoding="utf-8")
        assert "llm_production.generate_" in source or "generate_text(" in source
        assert not any(token in source for token in forbidden), target
    brand_route = (root / targets[0]).read_text(encoding="utf-8")
    assert "admin = await get_admin(request)" in brand_route
