from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.via import session_generation as generation


def _bundle() -> dict[str, Any]:
    return {
        "session": {"state": {"lane": "creator"}},
        "persona": {
            "display_name": "Milo",
            "temperament": "calm",
            "talk_style": "warm",
            "outfit_code": "studio",
            "affinity_points": 7,
            "wardrobe_points": 3,
            "profile": {"language": "zh"},
        },
        "memory_refs": [{"summary": "first"}, {"summary": "second"}],
    }


def _disable_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generation, "get_via_business_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(generation, "get_via_product_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(generation, "_product_line_guide_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(generation, "_software_guide_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(generation, "_photography_guide_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(generation, "_casual_companion_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(generation, "compact_via_profile_context", lambda profile: dict(profile))


def test_business_template_preserves_payload_order_truncation_and_activity_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []
    long_text = "合" * 510

    monkeypatch.setattr(generation, "_memory_teaser", lambda _bundle: "remembered")
    monkeypatch.setattr(generation, "compact_via_profile_context", lambda profile: {"profile": profile})
    monkeypatch.setattr(
        generation,
        "get_via_business_reply",
        lambda *_args, **_kwargs: {
            "title": "  官方合作  ",
            "text": long_text,
            "quick_actions": [" A ", "", "B" * 45, "C", "D"],
            "business_subintent": "rental",
            "behavior_mode": "gear",
            "lock_ai_override": True,
            "session_state_patch": {"step": 2},
        },
    )
    monkeypatch.setattr(
        generation,
        "get_via_product_reply",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("product template must not run for business intent")
        ),
    )
    monkeypatch.setattr(
        generation,
        "build_business_context",
        lambda text, **kwargs: {"text": text, **kwargs},
    )

    def activity(**kwargs: Any) -> dict[str, Any]:
        calls.append(("activity", kwargs))
        return {"mode": "business"}

    monkeypatch.setattr(generation, "resolve_via_activity_state", activity)
    result = generation.compose_via_reply(
        _bundle(),
        "我想合作",
        current_surface="account",
        route_info={"intent": "business_support"},
    )

    assert list(result) == ["title", "text", "payload"]
    assert result["title"] == "官方合作"
    assert result["text"] == long_text[:500]
    assert list(result["payload"]) == [
        "persona",
        "memory_ref_count",
        "quick_actions",
        "surface",
        "business_mode",
        "business_subintent",
        "behavior_mode",
        "lock_ai_override",
        "business_state_patch",
        "business_context",
        "activity_state",
    ]
    assert result["payload"]["quick_actions"] == ["A", "B" * 40, "C"]
    assert result["payload"]["memory_ref_count"] == 2
    assert result["payload"]["business_context"] == {
        "text": "我想合作",
        "profile_context": {"profile": {"language": "zh"}},
        "session_state": {"lane": "creator"},
    }
    assert calls[0][1]["text"] == long_text
    assert calls[0][1]["business_subintent"] == "rental"


def test_helper_priority_and_keyword_fallback_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_helpers(monkeypatch)
    monkeypatch.setattr(generation, "_memory_teaser", lambda _bundle: "portrait light")
    monkeypatch.setattr(
        generation,
        "resolve_via_activity_state",
        lambda **kwargs: {"title": kwargs["title"], "surface": kwargs["current_surface"]},
    )
    helper = {
        "title": "  Guide  ",
        "text": "helper text",
        "quick_actions": ["One", "Two"],
        "helper_mode": "product_line_guide",
        "lock_ai_override": True,
        "software_context": ["DaVinci"],
        "product_line_context": ["EVO"],
        "product_line_records": [{"family": "EVO"}],
        "guide_draft": {"lead": "feel"},
    }
    monkeypatch.setattr(generation, "_product_line_guide_reply", lambda *_a, **_k: helper)
    monkeypatch.setattr(
        generation,
        "_software_guide_reply",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("helper priority drift")),
    )
    guided = generation.compose_via_reply(_bundle(), "guide", route_info={})
    assert guided == {
        "title": "Guide",
        "text": "helper text",
        "payload": {
            "persona": {
                "display_name": "Milo",
                "temperament": "calm",
                "talk_style": "warm",
                "outfit_code": "studio",
                "affinity_points": 7,
                "wardrobe_points": 3,
            },
            "memory_ref_count": 2,
            "quick_actions": ["One", "Two"],
            "surface": "upload",
            "helper_mode": "product_line_guide",
            "lock_ai_override": True,
            "software_context": ["DaVinci"],
            "product_line_context": ["EVO"],
            "product_line_records": [{"family": "EVO"}],
            "guide_draft": {"lead": "feel"},
            "activity_state": {"title": "Guide", "surface": "upload"},
        },
    }

    monkeypatch.setattr(generation, "_product_line_guide_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(generation, "_software_guide_reply", lambda *_a, **_k: None)
    cases = [
        ("VIP level", "Tier track", "Show VIP status"),
        ("我的佣金订单", "Affiliate lane", "Copy my link"),
        ("你记得上次吗", "Memory check", "Refresh memory"),
        ("换装", "Wardrobe", "Switch outfit"),
        ("镜头库存", "Stock watch", "Show stock watch"),
        ("分析我的投稿", "Upload coach", "Critique my last upload"),
        ("你好", "Via", "Upload critique"),
    ]
    for text, title, first_action in cases:
        reply = generation.compose_via_reply(_bundle(), text, current_surface="account")
        assert reply["title"] == title
        assert reply["payload"]["quick_actions"][0] == first_action
        assert reply["payload"]["activity_state"] == {
            "title": title,
            "surface": "account",
        }


def _stub_ai_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generation, "_memory_prompt_lines", lambda _bundle: ["memory-line"])
    monkeypatch.setattr(generation, "compact_via_profile_context", lambda profile: {"profile": profile})
    monkeypatch.setattr(generation, "_software_context_lines", lambda _text: ["software-line"])
    monkeypatch.setattr(generation, "_product_line_context_lines", lambda _text: ["product-line"])
    monkeypatch.setattr(generation, "_product_line_context_payload", lambda _text: [{"family": "EVO"}])
    monkeypatch.setattr(generation, "build_product_context", lambda text, **kwargs: {"text": text, **kwargs})
    monkeypatch.setattr(generation, "build_business_context", lambda text, **kwargs: {"text": text, **kwargs})
    monkeypatch.setattr(generation, "get_external_system_prompt_injection", lambda: "EXTERNAL-CONTEXT")


def test_ai_single_then_collab_preserves_context_routes_and_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ai_context(monkeypatch)
    calls: list[tuple[str, dict[str, Any]]] = []
    plan_route = {"provider": "first", "model": "model-a"}
    fallback_route = {"provider": "second", "model": "model-b"}
    monkeypatch.setattr(
        generation,
        "get_via_model_plan",
        lambda **_kwargs: {"dialogue": {"mode": "single", "routes": [plan_route]}},
    )
    monkeypatch.setattr(generation, "_should_use_dialogue_collab", lambda _route: False)
    monkeypatch.setattr(generation, "preview_via_routes", lambda *_a, **_k: [fallback_route])

    async def single(**kwargs: Any) -> None:
        calls.append(("single", kwargs))
        return None

    async def collab(**kwargs: Any) -> dict[str, Any]:
        calls.append(("collab", kwargs))
        return {
            "data": {
                "title": "  回复  ",
                "text": "中文结果",
                "quick_actions": [" A ", "", "B" * 45, "C", "D"],
            },
            "provider": "second",
            "model": "model-b",
            "providers": ["first", "second"],
            "models": ["model-a", "model-b"],
        }

    monkeypatch.setattr(generation, "generate_json_with_route", single)
    monkeypatch.setattr(generation, "generate_json_with_collab", collab)
    user_text = "中文问题" * 150
    result = asyncio.run(
        generation._generate_via_reply_with_ai(
            _bundle(),
            user_text,
            current_surface="account",
            route_info={"intent": "product", "brain": "dialogue"},
            reply_payload={
                "helper_mode": "product_line_guide",
                "product_line_context": ["supplied-context"],
                "product_line_records": [{"family": "supplied"}],
                "guide_draft": {"lead": "creative feel"},
            },
        )
    )

    assert result == {
        "title": "回复",
        "text": "中文结果",
        "quick_actions": ["A", "B" * 40, "C"],
        "provider": "second",
        "model": "model-b",
        "providers": ["first", "second"],
        "models": ["model-a", "model-b"],
        "strategy": "single_then_collab",
    }
    assert [name for name, _kwargs in calls] == ["single", "collab"]
    assert calls[0][1]["route_override"] == plan_route
    assert calls[1][1]["routes_override"] == [fallback_route]
    assert calls[0][1]["max_tokens"] == calls[1][1]["max_tokens"] == 180
    assert "EXTERNAL-CONTEXT" in calls[0][1]["system_prompt"]
    assert "photography advisor" in calls[0][1]["system_prompt"]
    payload = calls[0][1]["payload"]
    assert payload["user_text"] == user_text[:500]
    assert payload["memory_refs"] == ["memory-line"]
    assert payload["product_line_context"] == ["supplied-context"]
    assert payload["product_line_records"] == [{"family": "supplied"}]
    assert payload["guide_draft"] == {"lead": "creative feel"}


def test_ai_collab_mode_and_empty_text_failure_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_ai_context(monkeypatch)
    calls: list[dict[str, Any]] = []
    routes = [{"provider": "one", "model": "a"}, {"provider": "two", "model": "b"}]
    monkeypatch.setattr(
        generation,
        "get_via_model_plan",
        lambda **_kwargs: {"dialogue": {"mode": "collab", "routes": routes}},
    )

    async def forbidden_single(**_kwargs: Any) -> None:
        raise AssertionError("collab policy must not call the single route")

    async def collab(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "data": {"title": "Via", "text": "", "quick_actions": []},
            "provider": "one",
            "model": "a",
        }

    monkeypatch.setattr(generation, "generate_json_with_route", forbidden_single)
    monkeypatch.setattr(generation, "generate_json_with_collab", collab)
    result = asyncio.run(
        generation._generate_via_reply_with_ai(
            _bundle(),
            "hello",
            model_policy={"policy_key": "dialogue"},
        )
    )

    assert result is None
    assert len(calls) == 1
    assert calls[0]["routes_override"] == routes
    assert calls[0]["max_tokens"] == 220
