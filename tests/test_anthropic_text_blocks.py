"""Anthropic 回包文本块拼接:思考块在前时各消费者仍能拿到文本,且旁路直连不带 temperature。"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.services.ai.analyzers.anthropic_response_text import text_blocks_joined


def _thinking_first(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="internal reasoning", signature="sig"),
            SimpleNamespace(type="text", text=text),
        ]
    )


# ── helper unit behaviour ────────────────────────────────────────────────────

def test_text_blocks_joined_skips_leading_thinking_block() -> None:
    assert text_blocks_joined(_thinking_first("hello")) == "hello"


def test_text_blocks_joined_concatenates_multiple_text_blocks_in_order() -> None:
    resp = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="a"),
            SimpleNamespace(type="tool_use", id="t1", name="x", input={}),
            SimpleNamespace(type="text", text="b"),
        ]
    )
    assert text_blocks_joined(resp) == "a\nb"
    assert text_blocks_joined(resp, separator="") == "ab"


def test_text_blocks_joined_accepts_dict_blocks_and_typeless_stubs() -> None:
    assert text_blocks_joined({"content": [{"type": "thinking", "thinking": "x"}, {"type": "text", "text": "ok"}]}) == "ok"
    # Fake SDK objects without a ``type`` attribute are still treated as text.
    assert text_blocks_joined(SimpleNamespace(content=[SimpleNamespace(text="stub")])) == "stub"


def test_text_blocks_joined_never_raises_on_empty_or_thinking_only() -> None:
    assert text_blocks_joined(None) == ""
    assert text_blocks_joined(SimpleNamespace(content=[])) == ""
    assert text_blocks_joined(SimpleNamespace(content=None)) == ""
    assert text_blocks_joined(SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="only")])) == ""
    assert text_blocks_joined(SimpleNamespace(content="plain")) == "plain"


# ── lens_monitor / lens_compare(2026-08-23 C3 起走 llm_production 严格边界)──

class _FakeClient:
    """只剩占位 client:SDK 调用已收口到 llm_production.generate_anthropic_messages。"""

    def __init__(self) -> None:
        self.messages = SimpleNamespace()


def _capture_strict_anthropic(monkeypatch, module, text: str) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def fake_messages(**kwargs: Any) -> SimpleNamespace:
        captured.append(kwargs)
        return _thinking_first(text)

    monkeypatch.setattr(module.llm_production, "generate_anthropic_messages", fake_messages)
    return captured


def test_lens_monitor_classify_reads_text_after_thinking_block(monkeypatch) -> None:
    from app.services.intelligence import lens_monitor

    payload = {"categories": {"review": [0]}, "insights": ["ok"]}
    client = _FakeClient()
    monkeypatch.setattr(lens_monitor, "_claude_available", True)
    monkeypatch.setattr(lens_monitor, "get_claude_client", lambda: client)
    captured = _capture_strict_anthropic(monkeypatch, lens_monitor, "```json\n" + json.dumps(payload) + "\n```")

    result = lens_monitor.classify_videos_with_claude(
        "viltrox 50mm",
        [{"idx": 0, "title": "t", "channel": "c", "views": 10, "published": "2026-01-01", "description": "d"}],
    )

    assert result == payload
    assert len(captured) == 1
    kwargs = captured[0]
    assert kwargs["client"] is client
    assert kwargs["model"] == lens_monitor.CLAUDE_MODEL
    assert kwargs["purpose"] == "lens_monitor"
    assert kwargs["metadata"]["task_binding"] == "lens_monitor"
    assert kwargs["max_output_tokens"] == 4000
    assert "temperature" not in kwargs and "thinking" not in kwargs  # 思考/采样口径归边界


def test_lens_compare_analyze_reads_text_after_thinking_block(monkeypatch) -> None:
    from app.services.intelligence import lens_compare

    payload = {"topics": ["bokeh"], "recommendation": "go"}
    client = _FakeClient()
    monkeypatch.setattr(lens_compare, "_claude_available", True)
    monkeypatch.setattr(lens_compare, "get_claude_client", lambda: client)
    captured = _capture_strict_anthropic(monkeypatch, lens_compare, json.dumps(payload))

    stats = {"video_count": 0, "total_views": 0, "avg_views": 0, "avg_engagement_pct": 0.0}
    monkeypatch.setattr(lens_compare, "build_compare_prompt", lambda *_a, **_k: "compare prompt")
    result = lens_compare.analyze_with_claude("a", "b", stats, stats, [], [])

    assert result == payload
    kwargs = captured[0]
    assert kwargs["model"] == lens_compare.CLAUDE_MODEL
    assert kwargs["purpose"] == "lens_compare"
    assert kwargs["metadata"]["task_binding"] == "lens_compare"
    assert kwargs["messages"] == [{"role": "user", "content": "compare prompt"}]
    assert "temperature" not in kwargs


def test_lens_paths_are_registered_task_bindings() -> None:
    from app.core import model_registry

    assert model_registry.TASK_MODEL_BINDING["lens_monitor"] == "anthropic/claude-sonnet-5"
    assert model_registry.TASK_MODEL_BINDING["lens_compare"] == "anthropic/claude-sonnet-5"
    assert model_registry.TASK_MODEL_ENV_KEYS["lens_monitor"] == ("CLAUDE_MODEL", None)


# ── analyzers that consume generate_anthropic_messages ───────────────────────

def test_claude_vision_images_batch_reads_text_after_thinking_block(monkeypatch) -> None:
    from app.services.ai.analyzers import claude_vision_images

    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(claude_vision_images, "ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(claude_vision_images, "ANTHROPIC_API_KEY", "configured")
    monkeypatch.setattr(claude_vision_images, "_build_anthropic_client", lambda: SimpleNamespace(messages=SimpleNamespace()))

    def fake_messages(**kwargs: Any) -> SimpleNamespace:
        captured.append(kwargs)
        return _thinking_first('{"viltrox_detected":true,"confidence":"high","viltrox_lens":"AF 50mm"}')

    monkeypatch.setattr(claude_vision_images.llm_production, "generate_anthropic_messages", fake_messages)

    result = claude_vision_images._analyze_images_batch(["YWJj"], "Field test", "instagram")

    assert result["viltrox_detected"] is True
    assert result["viltrox_lens"] == "AF 50mm"
    assert captured[0]["model"] == claude_vision_images.CLAUDE_MODEL


def test_claude_text_thumbnail_pass_reads_text_after_thinking_block(monkeypatch) -> None:
    from app.services.ai import retry
    from app.services.ai.analyzers import claude_text

    monkeypatch.setattr(claude_text, "ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(claude_text, "get_claude_client", lambda: SimpleNamespace(messages=SimpleNamespace()))
    monkeypatch.setattr(retry.time, "sleep", lambda _seconds: None)

    class _Img:
        headers = {"Content-Type": "image/jpeg"}

        def __enter__(self) -> "_Img":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        @staticmethod
        def read() -> bytes:
            return b"x" * 2048

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Img())

    def fake_generate_text(_prompt: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "success",
            "provider": "anthropic",
            "model": kwargs["model"],
            "text": '{"content_genre":"review","quality_scores":{"exposure":7}}',
        }

    vision_calls: list[dict[str, Any]] = []

    def fake_messages(**kwargs: Any) -> SimpleNamespace:
        vision_calls.append(kwargs)
        return _thinking_first('{"viltrox_detected":true,"viltrox_lens":"AF 27mm","thumbnail_summary":"lens on desk"}')

    monkeypatch.setattr(claude_text.llm_production, "generate_text", fake_generate_text)
    monkeypatch.setattr(claude_text.llm_production, "generate_anthropic_messages", fake_messages)

    result = claude_text.analyze_text_content(
        "Lens review",
        "A detailed Viltrox lens review for creators.",
        "https://example.com/video/42",
        "youtube",
        "Independent field test.",
        og_image="https://example.com/thumb.jpg",
    )

    assert result["analyzed"] is True
    assert len(vision_calls) == 1
    assert vision_calls[0]["metadata"]["subphase"] == "thumbnail_vision"
    # 缩略图视觉结果被合并进分析(思考块在前也不丢文本)
    assert result.get("viltrox_detected") is True or result.get("viltrox_lens") == "AF 27mm" or result.get("thumbnail_summary") == "lens on desk"


def test_claude_vision_video_frames_reads_text_after_thinking_block(monkeypatch) -> None:
    from app.services.ai import retry
    from app.services.ai.analyzers import claude_vision

    monkeypatch.setattr(claude_vision, "ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(claude_vision, "ANTHROPIC_API_KEY", "configured")
    monkeypatch.setattr(claude_vision.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(claude_vision, "extract_video_frames_with_ts", lambda _p, max_frames=6: [("YWJj", 0.0), ("YWJj", 5.0)])
    monkeypatch.setattr(claude_vision, "_build_anthropic_client", lambda: SimpleNamespace(messages=SimpleNamespace()))
    monkeypatch.setattr(claude_vision, "save_best_frame", lambda *_a, **_k: "")
    monkeypatch.setattr(retry.time, "sleep", lambda _seconds: None)

    captured: list[dict[str, Any]] = []

    def fake_messages(**kwargs: Any) -> SimpleNamespace:
        captured.append(kwargs)
        return _thinking_first('{"viltrox_detected":true,"confidence":"high","viltrox_lens":"AF 85mm","quality_scores":{"exposure":8}}')

    monkeypatch.setattr(claude_vision.llm_production, "generate_anthropic_messages", fake_messages)

    result = claude_vision.analyze_video_with_claude("/tmp/fake.mp4", "viltrox_test.mp4")

    assert len(captured) == 1
    assert captured[0]["metadata"]["task_binding"] == "audit_vision_fallback"
    assert result.get("viltrox_detected") is True
    assert result.get("viltrox_lens") == "AF 85mm"
