"""Fail CI if production LLM boundary debt grows.

This is a ratchet, not an allow-list claiming the remaining paths are safe.
Every listed call must still be migrated to llm_production (or a future
multimodal/embed/batch sibling). Reductions pass automatically; new files or
additional calls fail review.
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"

DIRECT_PROVIDER_MAX = {
    "domains/kol/audience_avatar_llm.py": 1,  # relocated from audience_stats.py (avatar-age Gemini call); debt unchanged
    "domains/kol/profile_recall.py": 1,
    "domains/market/ai_today.py": 2,
    "domains/projects/contract_assist.py": 1,
    "platform/llm_batch.py": 1,
    "services/ai/analyzers/claude_contract_extract.py": 1,
    "services/ai/analyzers/claude_vision.py": 1,
    "services/ai/analyzers/gemini_video.py": 1,
    "services/ai/analyzers/gemini_video_keyframes.py": 4,
    "services/ai/analyzers/gemini_video_youtube.py": 2,
    "services/intelligence/lens_compare.py": 1,
    "services/intelligence/lens_monitor.py": 1,
    "services/scraping/ytdlp.py": 1,
    "services/verification/comment_generator.py": 1,
    "services/via/vector_memory_embeddings.py": 1,
}

LEGACY_GATEWAY_MAX = {
    "api/routers/vkpi_kol_pool_intel.py": 1,
    "api/routers/vkpi_kol_pool_jobs.py": 1,
    "domains/kol/memory.py": 1,
    "domains/kol/outreach_draft.py": 1,
    "domains/kol/profile_discovery_localize.py": 1,
    "domains/kol/profile_recall.py": 1,
    "domains/kol/recall_pipeline.py": 1,
    "domains/kol/smart_query_planner.py": 1,
    "domains/reports/report_helpers.py": 1,
    "platform/models/router.py": 1,
}

DIRECT_SUFFIXES = (
    "chat.completions.create",
    "messages.create",
    "responses.create",
    "models.generate_content",
    "embeddings.create",
    "batches.create",
)


def _attribute_chain(value: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _inventory() -> tuple[dict[str, int], dict[str, int]]:
    direct: dict[str, int] = {}
    legacy: dict[str, int] = {}
    for path in APP.rglob("*.py"):
        relative = str(path.relative_to(APP))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = _attribute_chain(node.func)
            if any(call.endswith(suffix) for suffix in DIRECT_SUFFIXES):
                if relative == "platform/llm_production.py":
                    # This is the reviewed strict provider boundary: exact
                    # task/model match, dual-signed readiness, conservative
                    # media estimate, atomic reservation and settlement.
                    continue
                direct[relative] = direct.get(relative, 0) + 1
            if call in {"llm_gateway.invoke", "llm_gateway.invoke_json"}:
                if relative == "platform/llm_production.py":
                    continue
                legacy[relative] = legacy.get(relative, 0) + 1
    return direct, legacy


def _assert_ratchet(actual: dict[str, int], allowed: dict[str, int], label: str) -> None:
    unexpected = {path: count for path, count in actual.items() if path not in allowed}
    increased = {
        path: {"actual": count, "maximum": allowed[path]}
        for path, count in actual.items()
        if path in allowed and count > allowed[path]
    }
    assert not unexpected, f"new {label} paths must use llm_production: {unexpected}"
    assert not increased, f"{label} debt increased: {increased}"


def test_direct_provider_sdk_debt_can_only_decrease() -> None:
    direct, _legacy = _inventory()
    _assert_ratchet(direct, DIRECT_PROVIDER_MAX, "direct provider SDK")
    assert sum(direct.values()) <= 20


def test_legacy_non_atomic_gateway_debt_can_only_decrease() -> None:
    _direct, legacy = _inventory()
    _assert_ratchet(legacy, LEGACY_GATEWAY_MAX, "legacy gateway")
    assert sum(legacy.values()) <= 10


def test_user_facing_via_and_caption_prefilter_are_not_in_debt_inventory() -> None:
    direct, legacy = _inventory()
    for path in (
        "services/via/model_router.py",
        "services/ai/analyzers/gpt_prefilter.py",
    ):
        assert path not in direct
        assert path not in legacy
