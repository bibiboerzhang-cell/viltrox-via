"""core/gemini_models leaf 契约 + 跨车道不变量(视频主力模型五处一字不差)。

不变量:DEFAULT_VIDEO_GEMINI_MODEL == worker WORKER_GEMINI_MODEL == enqueue
PRODUCTION_VIDEO_MODEL == model_registry.TASK_MODEL_BINDING['audit_video_analysis'] 后缀
== llm_local_evaluation.LOCAL_EVALUATION_MODEL。任何一处漂移,worker_runtime /
worker_gemini 就会以 model_binding_mismatch 拦下每一条视频 job。
"""
from __future__ import annotations

import asyncio
import importlib
import re
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "app"
_CONTRACT_VIDEO_MODEL = "gemini-3.6-flash"
_CONTRACT_JUDGE_MODEL = "gemini-3.5-flash-lite"
_RETIRED_PREVIEW_IDS = ("gemini-3-flash-preview", "gemini-3.1-pro-preview")


def _reload_leaf(monkeypatch, **env: str):
    for key in ("APIFY_WORKER_GEMINI_MODEL", "GEMINI_FINAL_V1_QA_MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("app.core.gemini_models", None)
    return importlib.import_module("app.core.gemini_models")


@pytest.fixture(autouse=True)
def _restore_leaf_module():
    yield
    sys.modules.pop("app.core.gemini_models", None)
    importlib.import_module("app.core.gemini_models")


def test_leaf_defaults_are_the_contract_literals(monkeypatch) -> None:
    leaf = _reload_leaf(monkeypatch)
    assert leaf.DEFAULT_VIDEO_GEMINI_MODEL == _CONTRACT_VIDEO_MODEL
    assert leaf.DEFAULT_GEMINI_JUDGE_MODEL == _CONTRACT_JUDGE_MODEL
    assert leaf.VISUAL_PASS_MODEL == leaf.DEFAULT_VIDEO_GEMINI_MODEL
    assert leaf.DEFAULT_FINAL_V1_CHAIN == (leaf.DEFAULT_VIDEO_GEMINI_MODEL, leaf.DEFAULT_GEMINI_JUDGE_MODEL)
    assert leaf.DEFAULT_FINAL_V1_CHAIN == (_CONTRACT_VIDEO_MODEL, _CONTRACT_JUDGE_MODEL)


def test_leaf_honours_env_overrides_and_ignores_blank(monkeypatch) -> None:
    leaf = _reload_leaf(
        monkeypatch,
        APIFY_WORKER_GEMINI_MODEL="  gemini-2.5-flash ",
        GEMINI_FINAL_V1_QA_MODEL="gemini-3.5-flash",
    )
    assert leaf.DEFAULT_VIDEO_GEMINI_MODEL == "gemini-2.5-flash"
    assert leaf.VISUAL_PASS_MODEL == "gemini-2.5-flash"
    assert leaf.DEFAULT_FINAL_V1_CHAIN == ("gemini-2.5-flash", "gemini-3.5-flash")
    assert leaf.DEFAULT_GEMINI_JUDGE_MODEL == "gemini-3.5-flash"
    blank = _reload_leaf(monkeypatch, APIFY_WORKER_GEMINI_MODEL="   ", GEMINI_FINAL_V1_QA_MODEL="")
    assert blank.DEFAULT_VIDEO_GEMINI_MODEL == _CONTRACT_VIDEO_MODEL
    assert blank.DEFAULT_GEMINI_JUDGE_MODEL == _CONTRACT_JUDGE_MODEL


def test_leaf_module_only_imports_os() -> None:
    source = (_BACKEND / "core" / "gemini_models.py").read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from\s+(\S+)\s+import|import\s+(\S+))", source, flags=re.MULTILINE)
    modules = {a or b for a, b in imports}
    assert modules == {"__future__", "os"}, modules


def test_gemini_3_family_detection() -> None:
    from app.core.gemini_models import is_gemini_3_family

    assert is_gemini_3_family("gemini-3.6-flash")
    assert is_gemini_3_family("gemini-3.5-flash-lite")
    assert is_gemini_3_family(" Gemini-3.5-Flash ")
    assert not is_gemini_3_family("gemini-2.5-flash")
    assert not is_gemini_3_family("gemini-2.5-pro")
    assert not is_gemini_3_family("")


def test_cross_lane_video_model_invariant() -> None:
    """A 车道(registry / local-eval)与 C 车道(leaf / worker / enqueue)必须同一字面。"""
    from app.core import gemini_models as leaf
    from app.core.model_registry import TASK_MODEL_BINDING, split_binding
    from app.domains.kol import video_analysis_enqueue as enqueue
    from app.platform import llm_local_evaluation as local_eval
    from app.workers import apify_jobs_worker as worker

    _, registry_model = split_binding(TASK_MODEL_BINDING["audit_video_analysis"])
    observed = {
        "core.gemini_models.DEFAULT_VIDEO_GEMINI_MODEL": leaf.DEFAULT_VIDEO_GEMINI_MODEL,
        "workers.apify_jobs_worker.WORKER_GEMINI_MODEL": worker.WORKER_GEMINI_MODEL,
        "kol.video_analysis_enqueue.PRODUCTION_VIDEO_MODEL": enqueue.PRODUCTION_VIDEO_MODEL,
        "model_registry.TASK_MODEL_BINDING[audit_video_analysis]": registry_model,
        "llm_local_evaluation.LOCAL_EVALUATION_MODEL": local_eval.LOCAL_EVALUATION_MODEL,
    }
    assert set(observed.values()) == {_CONTRACT_VIDEO_MODEL}, observed
    assert worker.FINAL_V1_GEMINI_MODELS == [_CONTRACT_VIDEO_MODEL]
    assert worker.FINAL_V1_KEYFRAME_QA_MODEL == leaf.DEFAULT_GEMINI_JUDGE_MODEL


def test_worker_models_are_registered_and_priced() -> None:
    from app.core.model_pricing import PRICING_USD_PER_1M_TOKENS
    from app.core.model_registry import is_selectable_model

    for model in (_CONTRACT_VIDEO_MODEL, _CONTRACT_JUDGE_MODEL):
        assert is_selectable_model(f"google/{model}"), model
        assert model in PRICING_USD_PER_1M_TOKENS, model


def test_judge_and_analyzer_modules_carry_no_retired_preview_ids() -> None:
    files = (
        _BACKEND / "workers" / "apify_jobs_worker_gemini_judges.py",
        _BACKEND / "workers" / "apify_jobs_worker.py",
        _BACKEND / "domains" / "kol" / "video_analysis_enqueue.py",
        _BACKEND / "services" / "ai" / "analyzers" / "gemini_video.py",
        _BACKEND / "services" / "ai" / "analyzers" / "gemini_video_youtube.py",
        _BACKEND / "services" / "ai" / "analyzers" / "gemini_video_keyframes.py",
    )
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for retired in _RETIRED_PREVIEW_IDS:
            if retired in text:
                offenders.append(f"{path.name}:{retired}")
        # 3.6-flash 已弃用 temperature;视频链任何地方都不得再传采样参数。
        for param in ("temperature=", "top_p=", "top_k="):
            if param in text:
                offenders.append(f"{path.name}:{param}")
    assert not offenders, offenders


def test_judge_labels_derive_from_visual_pass_model() -> None:
    from app.core.gemini_models import VISUAL_PASS_MODEL
    from app.core.model_registry import CLAUDE_OPUS_EXACT_MODEL
    from app.workers import apify_jobs_worker_gemini_judges as judges

    assert judges.VISUAL_PASS_MODEL == VISUAL_PASS_MODEL
    assert judges.FINAL_V1_KEYFRAME_QA_MODEL == _CONTRACT_JUDGE_MODEL
    source = (_BACKEND / "workers" / "apify_jobs_worker_gemini_judges.py").read_text(encoding="utf-8")
    assert 'f"gemini_direct_{VISUAL_PASS_MODEL}"' in source
    assert 'f"{VISUAL_PASS_MODEL}+{FINAL_V1_KEYFRAME_QA_MODEL}"' in source
    assert 'f"{VISUAL_PASS_MODEL}+{CLAUDE_OPUS_EXACT_MODEL}"' in source
    assert CLAUDE_OPUS_EXACT_MODEL


def _config_dump(config) -> dict:
    return config.model_dump(exclude_none=True, mode="json")


def test_keyframe_judge_config_thinking_by_family() -> None:
    from app.services.ai.analyzers import gemini_video_keyframes as kf

    lite = _config_dump(kf._keyframe_judge_generate_config(_CONTRACT_JUDGE_MODEL))
    assert lite["max_output_tokens"] == kf.KEYFRAME_JUDGE_MAX_OUTPUT_TOKENS >= 256
    assert str(lite["thinking_config"]["thinking_level"]).lower() == "minimal"
    assert "thinking_budget" not in lite["thinking_config"]
    assert not ({"temperature", "top_p", "top_k"} & set(lite))

    legacy = _config_dump(kf._keyframe_judge_generate_config("gemini-2.5-flash"))
    assert legacy["thinking_config"] == {"thinking_budget": 0}
    assert "thinking_level" not in legacy["thinking_config"]


def test_keyframe_qa_direct_call_passes_config_and_default_judge(monkeypatch, tmp_path) -> None:
    from app.services.ai.analyzers import gemini_video_keyframes as kf

    frame = tmp_path / "f1.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xd9")
    captured: dict = {}

    class _Resp:
        text = '{"qa_pass": true, "checks": []}'
        usage_metadata = None

    class _Models:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return _Resp()

    class _Client:
        models = _Models()

    # 2026-08-23 C3:SDK 调用经 llm_production.generate_google_content;这里用透传假边界
    # 记录收口参数,再按边界口径调 client.models.generate_content。
    strict: dict = {}

    def fake_generate_google_content(**kwargs):
        strict.update(kwargs)
        return kwargs["client"].models.generate_content(
            model=kwargs["model"], contents=kwargs["contents"], config=kwargs["config"]
        )

    monkeypatch.setattr(kf, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(kf, "gemini_client", _Client())
    monkeypatch.setattr(kf.llm_production, "generate_google_content", fake_generate_google_content)
    result = asyncio.run(
        kf.analyze_final_v1_keyframe_qa(
            final_v1_result={},
            keyframes=[{"image_path": str(frame)}],
            title="demo",
        )
    )
    assert captured["model"] == _CONTRACT_JUDGE_MODEL
    dumped = _config_dump(captured["config"])
    assert str(dumped["thinking_config"]["thinking_level"]).lower() == "minimal"
    assert dumped["max_output_tokens"] == kf.KEYFRAME_JUDGE_MAX_OUTPUT_TOKENS
    assert strict["max_output_tokens"] == kf.KEYFRAME_JUDGE_MAX_OUTPUT_TOKENS
    assert strict["purpose"] == "keyframe_qa"
    assert strict["metadata"]["task_binding"] == "keyframe_qa"
    assert strict["metadata"]["keyframe_count"] == 1
    assert strict["estimated_input_tokens"] >= kf.KEYFRAME_IMAGE_RESERVE_TOKENS
    assert result["model"] == _CONTRACT_JUDGE_MODEL
    assert result["method"] == f"gemini_final_v1_keyframe_qa_{_CONTRACT_JUDGE_MODEL}"


def test_anthropic_keyframe_judgment_disables_thinking(monkeypatch) -> None:
    import types as _types

    from app.core.model_registry import CLAUDE_OPUS_EXACT_MODEL
    from app.services.ai.analyzers import gemini_video_keyframes as kf

    captured: dict = {}

    class _Block:
        type = "text"
        text = '{"layer2": {}, "layer3": {}}'

    class _Resp:
        content = [_Block()]
        usage = None
        model = CLAUDE_OPUS_EXACT_MODEL

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _Resp()

    class _Anthropic:
        def __init__(self, **_kwargs):
            self.messages = _Messages()

    fake_sdk = _types.SimpleNamespace(Anthropic=_Anthropic)
    monkeypatch.setitem(sys.modules, "anthropic", fake_sdk)
    monkeypatch.setattr(kf.llm_gateway, "_get_api_key", lambda _provider: "test-key")
    monkeypatch.setattr(kf, "build_anthropic_multimodal_content", lambda text, frames: [{"type": "text", "text": text}])

    # 2026-08-23 C3:经 llm_production.generate_anthropic_messages;透传假边界按
    # anthropic_create_kwargs(thinking 默认 disabled、无 temperature)调 client.messages.create。
    from app.platform.llm_production_anthropic_helpers import anthropic_create_kwargs

    strict: dict = {}

    def fake_generate_anthropic_messages(**kwargs):
        strict.update(kwargs)
        return kwargs["client"].messages.create(
            **anthropic_create_kwargs(kwargs["model"], kwargs["max_output_tokens"], kwargs["messages"])
        )

    monkeypatch.delenv("VKPI_ANTHROPIC_THINKING", raising=False)
    monkeypatch.setattr(kf.llm_production, "generate_anthropic_messages", fake_generate_anthropic_messages)

    result = asyncio.run(
        kf.analyze_v2_judgment_with_anthropic_keyframes(
            layer1_visual_content={},
            keyframes=[],
            title="demo",
        )
    )
    assert captured["model"] == CLAUDE_OPUS_EXACT_MODEL
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["max_tokens"] == 4000
    assert "temperature" not in captured
    assert strict["purpose"] == "keyframe_claude_judge"
    assert strict["metadata"]["task_binding"] == "keyframe_claude_judge"
    assert result["method"] == f"anthropic_keyframe_judgment_{CLAUDE_OPUS_EXACT_MODEL}"
