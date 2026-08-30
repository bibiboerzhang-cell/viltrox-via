from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.platform import llm_gateway
from app.platform import llm_gateway_json
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
JSON_GATEWAY_FAMILY = (
    ROOT / "backend/app/platform/llm_gateway_json.py",
    ROOT / "backend/app/platform/llm_gateway_json_runtime.py",
    ROOT / "backend/app/platform/llm_gateway_json_attempt_runtime.py",
)


def test_invoke_json_facade_forwards_every_public_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    fallbacks = iter((("google", "gemini-2.5-flash"),))
    validator = lambda value: bool(value)  # noqa: E731
    metadata = {"surface": "test"}
    staff = {"id": 7}
    injected_hooks: dict[str, Any] = {}
    hook_names = {
        "preflight_candidate",
        "run_candidate",
        "build_cache_plan",
        "cache_model_label",
        "serve_cached_result",
        "deferred_or_none",
        "store_cached_result",
    }

    def runtime(gateway: Any, prompt: str, **kwargs: Any) -> dict[str, Any]:
        for name in hook_names:
            injected_hooks[name] = kwargs.pop(name)
        captured.update({"gateway": gateway, "prompt": prompt, **kwargs})
        return {"status": "captured"}

    monkeypatch.setattr(llm_gateway_json, "invoke_json_runtime", runtime)
    result = llm_gateway.invoke_json(
        "prompt",
        purpose="purpose",
        max_output_tokens=321,
        preferred_provider="openai",
        model_override="gpt-test",
        model_fallbacks=fallbacks,
        require_runtime_verified=False,
        skip_budget_check=True,
        require_configured_budget=True,
        cost_tag="scope",
        triggered_by=19,
        metadata=metadata,
        staff=staff,
        required_keys=("ok",),
        validator=validator,
        deadline_seconds=12.5,
        max_provider_attempts=2,
        enforce_atomic_reservation=True,
    )

    assert result == {"status": "captured"}
    assert set(injected_hooks) == hook_names
    assert all(callable(hook) for hook in injected_hooks.values())
    assert captured == {
        "gateway": llm_gateway,
        "prompt": "prompt",
        "purpose": "purpose",
        "max_output_tokens": 321,
        "preferred_provider": "openai",
        "model_override": "gpt-test",
        "model_fallbacks": fallbacks,
        "require_runtime_verified": False,
        "skip_budget_check": True,
        "require_configured_budget": True,
        "cost_tag": "scope",
        "triggered_by": 19,
        "metadata": metadata,
        "staff": staff,
        "required_keys": ("ok",),
        "validator": validator,
        "deadline_seconds": 12.5,
        "max_provider_attempts": 2,
        "enforce_atomic_reservation": True,
    }


def test_json_gateway_family_stays_bounded_and_acyclic_by_direction() -> None:
    trees = {
        str(path): ast.parse(path.read_text(encoding="utf-8"))
        for path in JSON_GATEWAY_FAMILY
    }
    rows = collect_complexity(trees)
    facade = next(
        row
        for row in rows
        if row.path.endswith("llm_gateway_json.py")
        and row.qualified_name == "invoke_json"
    )

    assert facade.cc <= 10
    assert max(row.cc for row in rows) <= 30
    assert all(
        len(path.read_text(encoding="utf-8").splitlines()) <= 800
        for path in JSON_GATEWAY_FAMILY
    )
    runtime_source = JSON_GATEWAY_FAMILY[1].read_text(encoding="utf-8")
    attempt_source = JSON_GATEWAY_FAMILY[2].read_text(encoding="utf-8")
    assert "from app." not in runtime_source
    assert "from app." not in attempt_source
