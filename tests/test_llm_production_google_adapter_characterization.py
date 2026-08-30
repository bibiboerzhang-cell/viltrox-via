"""generate_google_content 五段拆刀前的 characterization 锁(2026-08-30 CC 车道)。

锁五条路径的逐字行为(拆分前后必须同绿):
1. 成功路径:record_call payload 逐键相等 + attempt_log settled + 结算恰好一次;
   顺序锁死 preflight → reserve → breaker → started → provider → breaker(success)
   → 台账 → settle(预算围栏先于任何出网、台账恰好记一次);
2. 预算拒绝:reserve 抛 → budget_blocked 台账(force_cost_ledger)+ attempt
   budget_blocked + ProductionLlmUnavailable(reason 透传),不碰 provider;
3. 任务绑定不匹配:preflight 之前拒(零预检、零 provider I/O);
4. provider 异常:原异常原样上抛(非 ProductionLlmUnavailable),台账
   provider_exception + 预留标 unknown + attempt unknown;
5. usage 缺失:台账 usage_missing 恰好一次、不结算、预留标 unknown、抛
   usage_missing(思考 token 陷阱防护:output = candidates + thoughts,
   input = prompt + tool_use;2.5 系注入 thinking_budget=0 关思考)。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import model_registry
from app.platform import llm_production
from app.platform.llm_production_common import ProductionLlmUnavailable


_MODEL = "gemini-2.5-flash"
_BINDING = f"google/{_MODEL}"
_CONTENTS = [{"role": "user", "parts": [{"text": "analyze this video"}]}]


class _FakeBinding:
    def matches_response_model(self, response_model: str) -> bool:
        return str(response_model or "").startswith(_MODEL)


def _wire_common(monkeypatch, events: list) -> None:
    monkeypatch.setattr(
        llm_production, "current_task_model_binding", lambda: {"local_file_video": _BINDING}
    )
    monkeypatch.setattr(model_registry, "task_model_fallback_bindings", lambda _name: ())
    monkeypatch.setattr(
        llm_production.llm_gateway, "_resolve_gateway_binding", lambda *_a, **_k: _FakeBinding()
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_estimate_cost_micro_usd",
        lambda *_a, **_k: 2_000_000,
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_cost_scope_for_purpose",
        lambda purpose, cost_tag: str(cost_tag or purpose),
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "budget_preflight",
        lambda *_a, **_k: events.append("preflight")
        or {"providers": [{"binding": _BINDING, "provider_calls_allowed": True}]},
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_acquire_strict_fleet_breaker",
        lambda **_k: events.append("breaker_acquire") or object(),
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_complete_strict_fleet_breaker",
        lambda _guard, outcome: events.append(("breaker_complete", outcome)),
    )


def _good_response() -> SimpleNamespace:
    return SimpleNamespace(
        model_version=_MODEL,
        usage_metadata={
            "prompt_token_count": 100,
            "tool_use_prompt_token_count": 7,
            "candidates_token_count": 50,
            "thoughts_token_count": 5,
        },
        text="ok",
    )


def _call(client, *, attempt_log, metadata=None, **overrides):
    kwargs = dict(
        client=client,
        contents=_CONTENTS,
        config=None,
        model=_MODEL,
        purpose="video_final_v1",
        max_output_tokens=8192,
        estimated_input_tokens=120_000,
        cost_tag="cron:vkpi_analysis_worker",
        triggered_by=40,
        staff={"id": 3},
        metadata=metadata if metadata is not None else {"task_binding": "local_file_video"},
        attempt_log=attempt_log,
    )
    kwargs.update(overrides)
    return llm_production.generate_google_content(**kwargs)


def test_success_path_ledgers_exactly_once_and_settles(monkeypatch):
    events: list = []
    call_rows: list[dict] = []
    attempt_log: list[dict] = []
    provider_kwargs: dict = {}
    _wire_common(monkeypatch, events)

    class Reservations:
        def reserve_llm_budget(self, **kwargs):
            events.append(("reserve", kwargs["provider"], kwargs["model"], kwargs["cost_scope"]))
            assert kwargs["estimated_cost_usd"] == 2.0
            assert kwargs["metadata"]["estimated_input_tokens"] == 120_000
            assert kwargs["metadata"]["max_output_tokens"] == 8192
            assert kwargs["metadata"]["request_content_recorded"] is False
            assert (
                kwargs["metadata"]["entrypoint"]
                == "llm_production_google_generate_content_v1"
            )
            return SimpleNamespace(reservation_key="llmres-google")

        def mark_llm_provider_started(self, key):
            events.append(("started", key))

        def release_llm_reservation(self, _key):
            raise AssertionError("success path must not release")

        def settle_llm_reservation(self, key, actual_cost):
            events.append(("settled", key, actual_cost))
            return {"settled": True}

    monkeypatch.setattr(
        llm_production.llm_gateway, "_llm_budget_reservations", lambda: Reservations()
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "record_call",
        lambda **kwargs: call_rows.append(kwargs) or {"call": {}},
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_mark_reserved_attempt_unknown",
        lambda _key: pytest.fail("success path must not mark unknown"),
    )

    def generate_content(**kwargs):
        events.append("provider")
        provider_kwargs.update(kwargs)
        return _good_response()

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    response = _call(client, attempt_log=attempt_log)
    assert response.text == "ok"

    # 预算围栏先于任何出网;台账恰好一次;结算恰好一次。
    assert events[0] == "preflight"
    assert events[1] == ("reserve", "google", _MODEL, "cron:vkpi_analysis_worker")
    assert events[2] == "breaker_acquire"
    assert events[3] == ("started", "llmres-google")
    assert events[4] == "provider"
    assert events[5] == ("breaker_complete", {"status": "success"})
    assert events[6] == ("settled", "llmres-google", 2.0)
    assert len(call_rows) == 1

    # provider 请求:2.5 系注入 thinking_budget=0 关思考 + 输出钳位(一个字不动)。
    assert provider_kwargs["model"] == _MODEL
    assert provider_kwargs["contents"] is _CONTENTS
    cfg = provider_kwargs["config"]
    assert cfg["max_output_tokens"] == 8192
    thinking = cfg["thinking_config"]
    budget = (
        thinking.get("thinking_budget")
        if isinstance(thinking, dict)
        else getattr(thinking, "thinking_budget", None)
    )
    assert budget == 0

    row = call_rows[0]
    assert row["provider"] == "google"
    assert row["model"] == _MODEL
    assert row["purpose"] == "video_final_v1"
    assert row["prompt"].startswith("google_contents_sha256:")
    # 思考 token 陷阱防护:input=prompt+tool_use,output=candidates+thoughts。
    assert row["input_tokens"] == 107
    assert row["output_tokens"] == 55
    assert row["cost_micro_usd"] == 2_000_000
    assert row["status"] == "success"
    assert row["fallback_used"] is False
    assert row["cost_tag"] == "cron:vkpi_analysis_worker"
    assert row["triggered_by"] == 40
    assert row["staff"] == {"id": 3}
    assert row["update_budget_scopes"] is False
    assert row["force_cost_ledger"] is True
    meta = row["metadata"]
    assert meta["entrypoint"] == "llm_production_google_generate_content_v1"
    assert meta["request_content_recorded"] is False
    assert meta["reservation_key"] == "llmres-google"
    assert meta["reservation_estimated_cost_usd"] == 2.0
    assert meta["latency_ms"] >= 0
    assert meta["response_model"] == _MODEL
    assert meta["max_output_tokens"] == 8192
    assert meta["usage_metadata"]["prompt_token_count"] == 100
    assert meta["task_binding"] == "local_file_video"
    assert meta["task_binding_actual"] == _BINDING
    assert meta["task_binding_primary"] == _BINDING
    assert meta["task_binding_role"] == "primary"
    assert meta["fallback_semantics"] == "task_binding_role_v1"
    assert meta["execution_class"] == llm_production.llm_gateway.PRODUCTION_EXECUTION_CLASS
    assert meta["phase"] == "video_analysis"
    assert meta["subphase"] == "provider_generation"

    assert len(attempt_log) == 1
    attempt = attempt_log[0]
    assert attempt["authority"] == "llm_production_google_generate_content_v1"
    assert attempt["state"] == "settled"
    assert attempt["model"] == _MODEL
    assert attempt["estimated_cost_usd"] == 2.0
    assert attempt["actual_cost_usd"] == 2.0
    assert attempt["input_tokens"] == 107
    assert attempt["output_tokens"] == 55
    assert attempt["response_model"] == _MODEL
    assert attempt["fallback_used"] is False


def test_budget_rejection_ledgers_budget_blocked_without_provider_io(monkeypatch):
    events: list = []
    call_rows: list[dict] = []
    attempt_log: list[dict] = []
    _wire_common(monkeypatch, events)

    class BudgetExhausted(Exception):
        reason = "llm_budget_exhausted"

    class Reservations:
        def reserve_llm_budget(self, **_kwargs):
            raise BudgetExhausted()

        def release_llm_reservation(self, _key):
            raise AssertionError("no reservation key to release before reserve succeeds")

    monkeypatch.setattr(
        llm_production.llm_gateway, "_llm_budget_reservations", lambda: Reservations()
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "record_call",
        lambda **kwargs: call_rows.append(kwargs) or {"call": {}},
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_acquire_strict_fleet_breaker",
        lambda **_k: pytest.fail("breaker must not be acquired after reserve failure"),
    )
    client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **_k: pytest.fail("no provider I/O on budget block")
        )
    )

    with pytest.raises(ProductionLlmUnavailable) as info:
        _call(client, attempt_log=attempt_log)
    assert info.value.code == "llm_budget_exhausted"
    assert info.value.result["status"] == "blocked"
    assert info.value.result["provider"] == "google"
    assert info.value.result["model"] == _MODEL

    assert len(call_rows) == 1
    row = call_rows[0]
    assert row["status"] == "budget_blocked"
    assert row["cost_tag"] == "cron:vkpi_analysis_worker"
    assert row["update_budget_scopes"] is False
    assert row["force_cost_ledger"] is True
    assert row["metadata"]["reservation_reason"] == "llm_budget_exhausted"
    assert row["metadata"]["estimated_cost_usd"] == 2.0
    assert row["metadata"]["request_content_recorded"] is False
    assert len(attempt_log) == 1
    assert attempt_log[0]["state"] == "budget_blocked"
    assert attempt_log[0]["estimated_cost_usd"] == 2.0


def test_binding_mismatch_rejects_before_preflight_and_provider(monkeypatch):
    monkeypatch.setattr(
        llm_production, "current_task_model_binding", lambda: {"local_file_video": _BINDING}
    )
    monkeypatch.setattr(model_registry, "task_model_fallback_bindings", lambda _name: ())
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "budget_preflight",
        lambda *_a, **_k: pytest.fail("no preflight on binding mismatch"),
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "record_call",
        lambda **_k: pytest.fail("no ledger row on binding mismatch"),
    )
    attempt_log: list[dict] = []
    client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **_k: pytest.fail("no provider I/O on mismatch")
        )
    )

    with pytest.raises(ProductionLlmUnavailable) as info:
        _call(client, attempt_log=attempt_log, model="gemini-3.6-flash")
    assert info.value.code == "task_binding_model_mismatch"
    assert info.value.result["task_binding"] == "local_file_video"
    assert info.value.result["expected_binding"] == _BINDING
    assert info.value.result["allowed_bindings"] == [_BINDING]
    assert info.value.result["actual_binding"] == "google/gemini-3.6-flash"
    assert attempt_log == []


def test_provider_exception_reraises_original_and_marks_unknown(monkeypatch):
    events: list = []
    call_rows: list[dict] = []
    attempt_log: list[dict] = []
    unknown_keys: list[str] = []
    _wire_common(monkeypatch, events)

    class Reservations:
        def reserve_llm_budget(self, **_kwargs):
            return SimpleNamespace(reservation_key="llmres-google")

        def mark_llm_provider_started(self, key):
            events.append(("started", key))

        def release_llm_reservation(self, _key):
            raise AssertionError("started reservation must not be released")

        def settle_llm_reservation(self, _key, _cost):
            raise AssertionError("provider exception must not settle")

    monkeypatch.setattr(
        llm_production.llm_gateway, "_llm_budget_reservations", lambda: Reservations()
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "record_call",
        lambda **kwargs: call_rows.append(kwargs) or {"call": {}},
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_mark_reserved_attempt_unknown",
        lambda key: unknown_keys.append(key),
    )

    boom = RuntimeError("google explodes")

    def generate_content(**_kwargs):
        raise boom

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    with pytest.raises(RuntimeError) as info:
        _call(client, attempt_log=attempt_log)
    assert info.value is boom

    assert ("breaker_complete", boom) in events
    assert unknown_keys == ["llmres-google"]
    assert len(call_rows) == 1
    row = call_rows[0]
    assert row["status"] == "provider_exception"
    assert row["update_budget_scopes"] is False
    assert "force_cost_ledger" not in row
    assert row["metadata"]["reservation_key"] == "llmres-google"
    assert row["metadata"]["reservation_estimated_cost_usd"] == 2.0
    assert len(attempt_log) == 1
    assert attempt_log[0]["state"] == "unknown"
    assert attempt_log[0]["actual_cost_usd"] is None


def test_usage_missing_ledgers_once_marks_unknown_and_never_settles(monkeypatch):
    events: list = []
    call_rows: list[dict] = []
    attempt_log: list[dict] = []
    unknown_keys: list[str] = []
    _wire_common(monkeypatch, events)

    class Reservations:
        def reserve_llm_budget(self, **_kwargs):
            return SimpleNamespace(reservation_key="llmres-google")

        def mark_llm_provider_started(self, key):
            events.append(("started", key))

        def release_llm_reservation(self, _key):
            raise AssertionError("started reservation must not be released")

        def settle_llm_reservation(self, _key, _cost):
            raise AssertionError("usage_missing must not settle")

    monkeypatch.setattr(
        llm_production.llm_gateway, "_llm_budget_reservations", lambda: Reservations()
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "record_call",
        lambda **kwargs: call_rows.append(kwargs) or {"call": {}},
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_mark_reserved_attempt_unknown",
        lambda key: unknown_keys.append(key),
    )

    def generate_content(**_kwargs):
        return SimpleNamespace(model_version=_MODEL, usage_metadata={}, text="body")

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    with pytest.raises(ProductionLlmUnavailable) as info:
        _call(client, attempt_log=attempt_log)
    assert info.value.code == "usage_missing"

    assert ("breaker_complete", {"status": "usage_missing"}) in events
    assert unknown_keys == ["llmres-google"]
    assert len(call_rows) == 1
    row = call_rows[0]
    assert row["status"] == "usage_missing"
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0
    assert row["cost_micro_usd"] == 0
    assert row["force_cost_ledger"] is True
    assert len(attempt_log) == 1
    assert attempt_log[0]["state"] == "unknown"
    assert attempt_log[0]["input_tokens"] == 0
    assert attempt_log[0]["output_tokens"] == 0


def test_provider_gate_blocked_ledgers_provider_blocked_without_reservation(monkeypatch):
    events: list = []
    call_rows: list[dict] = []
    attempt_log: list[dict] = []
    _wire_common(monkeypatch, events)
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "budget_preflight",
        lambda *_a, **_k: {
            "providers": [
                {
                    "binding": _BINDING,
                    "provider_calls_allowed": False,
                    "binding_gate_reason": "provider_env_missing",
                }
            ]
        },
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_llm_budget_reservations",
        lambda: pytest.fail("no reservation when provider gate blocks"),
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "record_call",
        lambda **kwargs: call_rows.append(kwargs) or {"call": {}},
    )
    client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **_k: pytest.fail("no provider I/O when gate blocks")
        )
    )

    with pytest.raises(ProductionLlmUnavailable) as info:
        _call(client, attempt_log=attempt_log)
    assert info.value.code == "provider_env_missing"
    assert len(call_rows) == 1
    row = call_rows[0]
    assert row["status"] == "provider_blocked"
    assert row["update_budget_scopes"] is False
    assert "force_cost_ledger" not in row
    assert "cost_tag" not in row
    assert row["metadata"]["provider_gate_reason"] == "provider_env_missing"
    assert row["metadata"]["max_output_tokens"] == 8192
    assert row["metadata"]["estimated_input_tokens"] == 120_000
    assert len(attempt_log) == 1
    assert attempt_log[0]["state"] == "provider_blocked"
    assert attempt_log[0]["estimated_cost_usd"] == 0.0
