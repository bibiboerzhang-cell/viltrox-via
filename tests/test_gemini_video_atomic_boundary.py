from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


class _Config:
    def __init__(self, **values: Any) -> None:
        self.values = dict(values)

    def model_copy(self, *, update: dict[str, Any]):
        return _Config(**{**self.values, **update})


class _Reservations:
    def __init__(self, *, start_error: bool = False) -> None:
        self.start_error = start_error
        self.events: list[tuple[str, Any]] = []
        self.counter = 0

    def reserve_llm_budget(self, **kwargs):
        self.counter += 1
        key = f"llmres-{self.counter}"
        self.events.append(("reserve", (key, kwargs)))
        return SimpleNamespace(reservation_key=key)

    def mark_llm_provider_started(self, key: str) -> None:
        self.events.append(("started", key))
        if self.start_error:
            raise RuntimeError("start fence failed")

    def settle_llm_reservation(self, key: str, actual: float):
        self.events.append(("settled", (key, actual)))
        return {"settled": True}

    def mark_llm_provider_unknown(self, key: str) -> bool:
        self.events.append(("unknown", key))
        return True

    def release_llm_reservation(self, key: str) -> bool:
        self.events.append(("released", key))
        return True


def _install_boundary(monkeypatch, *, allowed: bool = True, start_error: bool = False):
    from app.platform import llm_production

    reservations = _Reservations(start_error=start_error)
    ledgers: list[dict[str, Any]] = []
    monkeypatch.setattr(
        llm_production,
        "current_task_model_binding",
        lambda: {"audit_video_analysis": "google/gemini-3.6-flash"},
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "budget_preflight",
        lambda *_args, **_kwargs: {
            "provider_gate_reason": (
                "provider_calls_allowed" if allowed else "readiness_not_production_ready"
            ),
            "providers": [
                {
                    "binding": "google/gemini-3.6-flash",
                    "provider_calls_allowed": allowed,
                    "binding_gate_reason": (
                        "" if allowed else "readiness_not_production_ready"
                    ),
                }
            ],
        },
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_llm_budget_reservations",
        lambda: reservations,
    )

    def record_call(**kwargs):
        reservations.events.append(("ledger", kwargs["status"]))
        ledgers.append(kwargs)
        return {"call": {"call_uid": "unit"}}

    monkeypatch.setattr(llm_production.llm_gateway, "record_call", record_call)
    return llm_production, reservations, ledgers


def _call(
    llm_production,
    *,
    client: Any,
    attempt_log: list[dict[str, Any]],
    attempt_index: int,
    subphase: str,
):
    return llm_production.generate_google_content(
        client=client,
        contents=[SimpleNamespace(uri="https://example.invalid/video"), "prompt"],
        config=_Config(media_resolution="LOW"),
        model="gemini-3.6-flash",
        purpose="audit_video_analysis",
        max_output_tokens=999_999,
        estimated_input_tokens=25_000,
        cost_tag="cron:vkpi_analysis_worker",
        metadata={
            "task_binding": "audit_video_analysis",
            "phase": "video_analysis",
            "subphase": subphase,
            "attempt_index": attempt_index,
            "attempt_total": 2,
            "target_id": "42",
        },
        attempt_log=attempt_log,
    )


def test_uri_and_file_attempts_each_reserve_settle_once_without_double_budget(
    monkeypatch,
) -> None:
    llm_production, reservations, ledgers = _install_boundary(monkeypatch)
    provider_kwargs: list[dict[str, Any]] = []

    class Models:
        @staticmethod
        def generate_content(**kwargs):
            reservations.events.append(("provider", kwargs["model"]))
            provider_kwargs.append(kwargs)
            return SimpleNamespace(
                model_version="gemini-3.6-flash",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=10_000,
                    candidates_token_count=500,
                    thoughts_token_count=100,
                ),
                text="{}",
            )

    client = SimpleNamespace(models=Models())
    attempt_log: list[dict[str, Any]] = []
    _call(
        llm_production,
        client=client,
        attempt_log=attempt_log,
        attempt_index=1,
        subphase="youtube_uri_fast_generation",
    )
    _call(
        llm_production,
        client=client,
        attempt_log=attempt_log,
        attempt_index=2,
        subphase="youtube_file_fallback_generation",
    )

    assert [event[0] for event in reservations.events] == [
        "reserve",
        "started",
        "provider",
        "ledger",
        "settled",
        "reserve",
        "started",
        "provider",
        "ledger",
        "settled",
    ]
    assert [row["status"] for row in ledgers] == ["success", "success"]
    assert all(row["update_budget_scopes"] is False for row in ledgers)
    assert all(row["force_cost_ledger"] is True for row in ledgers)
    assert [row["metadata"]["attempt_index"] for row in ledgers] == [1, 2]
    assert [row["metadata"]["subphase"] for row in ledgers] == [
        "youtube_uri_fast_generation",
        "youtube_file_fallback_generation",
    ]
    assert all(
        kwargs["config"].values["max_output_tokens"]
        == llm_production.GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP
        for kwargs in provider_kwargs
    )
    assert [row["state"] for row in attempt_log] == ["settled", "settled"]


def test_google_multimodal_attempt_is_covered_by_fleet_breaker(monkeypatch) -> None:
    llm_production, reservations, _ledgers = _install_boundary(monkeypatch)
    guard = object()

    def acquire(**kwargs):
        reservations.events.append(("breaker_acquire", (kwargs["provider"], kwargs["model"])))
        return guard

    def complete(actual_guard, outcome):
        assert actual_guard is guard
        reservations.events.append(("breaker_complete", outcome["status"]))

    monkeypatch.setattr(llm_production.llm_gateway, "_acquire_strict_fleet_breaker", acquire)
    monkeypatch.setattr(llm_production.llm_gateway, "_complete_strict_fleet_breaker", complete)

    class Models:
        @staticmethod
        def generate_content(**kwargs):
            reservations.events.append(("provider", kwargs["model"]))
            return SimpleNamespace(
                model_version="gemini-3.6-flash",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=100,
                    candidates_token_count=20,
                    thoughts_token_count=1,
                ),
                text="{}",
            )

    _call(
        llm_production,
        client=SimpleNamespace(models=Models()),
        attempt_log=[],
        attempt_index=1,
        subphase="breaker-coverage",
    )
    names = [event[0] for event in reservations.events]
    assert names.index("breaker_acquire") < names.index("provider")
    assert names.index("provider") < names.index("breaker_complete")


def test_provider_exception_is_unknown_and_never_released(monkeypatch) -> None:
    llm_production, reservations, ledgers = _install_boundary(monkeypatch)

    class Models:
        @staticmethod
        def generate_content(**_kwargs):
            reservations.events.append(("provider", None))
            raise TimeoutError("provider outcome uncertain")

    attempt_log: list[dict[str, Any]] = []
    with pytest.raises(TimeoutError):
        _call(
            llm_production,
            client=SimpleNamespace(models=Models()),
            attempt_log=attempt_log,
            attempt_index=1,
            subphase="youtube_uri_fast_generation",
        )

    assert [event[0] for event in reservations.events] == [
        "reserve",
        "started",
        "provider",
        "ledger",
        "unknown",
    ]
    assert ledgers[-1]["status"] == "provider_exception"
    assert attempt_log[-1]["state"] == "unknown"


def test_start_fence_failure_releases_before_any_provider_call(monkeypatch) -> None:
    llm_production, reservations, _ledgers = _install_boundary(
        monkeypatch,
        start_error=True,
    )

    class Models:
        @staticmethod
        def generate_content(**_kwargs):  # pragma: no cover - tripwire
            raise AssertionError("provider must not run")

    attempt_log: list[dict[str, Any]] = []
    with pytest.raises(llm_production.ProductionLlmUnavailable):
        _call(
            llm_production,
            client=SimpleNamespace(models=Models()),
            attempt_log=attempt_log,
            attempt_index=1,
            subphase="local_file_generation",
        )

    assert [event[0] for event in reservations.events] == [
        "reserve",
        "started",
        "released",
    ]
    assert attempt_log[-1]["state"] == "released"


def test_readiness_block_runs_neither_reservation_nor_provider(monkeypatch) -> None:
    llm_production, reservations, ledgers = _install_boundary(
        monkeypatch,
        allowed=False,
    )

    class Models:
        @staticmethod
        def generate_content(**_kwargs):  # pragma: no cover - tripwire
            raise AssertionError("provider must not run")

    with pytest.raises(llm_production.ProductionLlmUnavailable):
        _call(
            llm_production,
            client=SimpleNamespace(models=Models()),
            attempt_log=[],
            attempt_index=1,
            subphase="youtube_uri_fast_generation",
        )

    assert [event[0] for event in reservations.events] == ["ledger"]
    assert ledgers[-1]["status"] == "provider_blocked"


def test_worker_outer_cost_path_does_not_write_a_second_ledger(monkeypatch) -> None:
    from app.workers import apify_jobs_worker as worker_gemini

    monkeypatch.setattr(
        worker_gemini.budget_guard,
        "record_cost",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("outer Gemini ledger must not write")
        ),
    )
    raw = {
        "analyzed": True,
        "model": "gemini-3.6-flash",
        "cost_authority": "llm_production_google_generate_content_v1",
        "llm_attempts": [
            {
                "state": "settled",
                "actual_cost_usd": 0.012,
                "estimated_cost_usd": 0.02,
                "input_tokens": 1000,
                "output_tokens": 100,
            },
            {
                "state": "unknown",
                "actual_cost_usd": None,
                "estimated_cost_usd": 0.03,
                "input_tokens": 0,
                "output_tokens": 0,
            },
        ],
    }
    cost, basis, tokens_in, tokens_out = worker_gemini._authoritative_gemini_cost(
        raw,
        9.0,
    )
    ledger = worker_gemini._record_gemini_cost(
        job={"id": 1},
        payload={"target_type": "video", "target_id": "42"},
        raw=raw,
        cost=cost,
        cost_basis=basis,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=10,
        preflight_cost=9.0,
    )

    assert (cost, basis, tokens_in, tokens_out) == (
        0.042,
        "llm_production_atomic_attempt_ledger",
        1000,
        100,
    )
    assert ledger["outer_ledger_write"] is False
    assert ledger["scopes_updated"] == []
