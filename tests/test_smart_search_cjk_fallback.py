"""Contract tests for the provider-free smart-search path on CJK queries.

Audit 2026-08-24 (prod sessions 1139/1140): a Chinese query naming a real
catalog product plus operator professions deterministically died at
``needs_clarification/no_evidence_anchor`` because
1) the resolver dropped the ``pro`` token for compact models ("Z1 pro") and
   never applied its own series-length tie-break (R2),
2) the CJK keyword table discarded operator professions such as 赛车/厨师 (R4),
3) the evidence-anchor gate only looked at the (all-generic) English fallback
   ``search_query`` and never at the original query (R3), and
4) the designed provider-free plan self-labeled ``rule_v0/fallback_used=True``
   which reads as an LLM outage in ops surfaces (R1/F8).

These tests pin the fixed behavior.  The catalog stub mirrors the real
``vkpi_products`` rows verified on the prod clone (Vintage Z1 family and the
DC-550 monitor family).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import product_resolver, smart_query_planner
from app.domains.kol.profile_recall_match_evidence import query_evidence_terms


AUDIT_QUERY = "Z1 pro的一些不同行业的用户比如赛车,厨师餐饮等"

# Real catalog rows (prod clone, 2026-08-24) that participate in Z1 resolution.
_Z1_CATALOG: list[dict[str, Any]] = [
    {
        "sku": "VINTAGE-Z1-PRO-TTL-RETRO-ON-CAMERA-FLASH",
        "model_name": "Viltrox Vintage Z1 Pro TTL Retro On-Camera Flash",
        "marketing_name": "Vintage Z1 Pro TTL Retro On-Camera Flash",
        "series": "Pro",
        "category_main": "Lighting",
    },
    {
        "sku": "VINTAGE-Z1-RETRO-ON-CAMERA-FLASH",
        "model_name": "Viltrox Vintage Z1 Retro On-Camera Flash",
        "marketing_name": "Vintage Z1 Retro On-Camera Flash",
        "series": "",
        "category_main": "Lighting",
    },
    {"sku": "VL-LIT073", "model_name": "Vintage Z1+", "marketing_name": "", "series": "", "category_main": "Lighting/Flash"},
    {"sku": "VL-LIT092", "model_name": "Vintage Z1 PRO-N", "marketing_name": "", "series": "", "category_main": "Lighting/Flash"},
    {"sku": "VL-LIT093", "model_name": "Vintage Z1 PRO-F", "marketing_name": "", "series": "", "category_main": "Lighting/Flash"},
    {"sku": "VL-LIT094", "model_name": "Vintage Z1 PRO-C", "marketing_name": "", "series": "", "category_main": "Lighting/Flash"},
    {"sku": "VL-LIT095", "model_name": "Vintage Z1 PRO-S", "marketing_name": "", "series": "", "category_main": "Lighting/Flash"},
]


def _fake_catalog(rows: list[dict[str, Any]]):
    """Mirror list_product_catalog's LIKE semantics over an in-memory row set."""

    def _list(limit: int = 300, *, query: str = "", **_kwargs: Any) -> dict[str, Any]:
        needle = str(query or "").strip().lower()
        if not needle:
            return {"products": list(rows)[: int(limit)]}
        hits = [
            row
            for row in rows
            if needle
            in " ".join(
                str(row.get(field) or "").lower()
                for field in ("sku", "model_name", "marketing_name", "description")
            )
        ]
        return {"products": hits[: int(limit)]}

    return _list


@pytest.fixture()
def z1_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_resolver, "list_product_catalog", _fake_catalog(_Z1_CATALOG))


# ── F2 · CJK profession keywords survive into English search terms ────────────


@pytest.mark.parametrize(
    ("cjk_term", "expected_keyword"),
    [
        ("赛车", "motorsport content creator"),
        ("机车", "motorsport content creator"),
        ("摩托", "motorsport content creator"),
        ("厨师", "food content creator"),
        ("餐饮", "food content creator"),
        ("美食", "food content creator"),
        ("烹饪", "food content creator"),
        ("婚礼", "wedding content creator"),
        ("健身", "fitness content creator"),
        ("宠物", "pet content creator"),
        ("旅拍", "travel content creator"),
    ],
)
def test_fallback_plan_maps_operator_professions_to_english_terms(
    cjk_term: str, expected_keyword: str
) -> None:
    plan = smart_query_planner._fallback_plan(f"找一些{cjk_term}方向的创作者")
    assert plan["search_query"] == expected_keyword
    assert plan["query_cells"][0]["primary_query"] == expected_keyword
    # 问题A:中文绝不进 search_query。
    assert not any("一" <= ch <= "鿿" for ch in plan["search_query"])
    assert query_evidence_terms(plan["search_query"])


def test_fallback_plan_profession_terms_are_real_evidence_anchors() -> None:
    plan = smart_query_planner._fallback_plan("不同行业的用户比如赛车,厨师餐饮等")
    anchored = smart_query_planner._require_evidence_anchor(plan)
    assert anchored["status"] != "needs_clarification"
    assert "anchor_source" not in anchored  # anchors come from search_query itself


# ── F3 · anchor gate rescues plans whose original query carries evidence ─────


def test_anchor_gate_keeps_plan_when_original_query_has_evidence() -> None:
    # Professions deliberately absent from the keyword table: the English
    # fallback sentence is all-generic, but the raw query itself is not.
    plan = smart_query_planner._fallback_plan(
        "不同行业的用户比如滑雪和攀岩", reason="provider_free_initial"
    )
    assert query_evidence_terms(plan["search_query"]) == []
    anchored = smart_query_planner._require_evidence_anchor(plan)
    assert anchored["status"] != "needs_clarification"
    assert anchored["anchor_source"] == "original_query"
    # search_query is intentionally left as-is for the downstream evidence gate.
    assert anchored["search_query"] == plan["search_query"]


def test_anchor_gate_still_flips_when_both_sides_are_generic() -> None:
    plan = smart_query_planner._fallback_plan("找一些达人", reason="provider_free_initial")
    anchored = smart_query_planner._require_evidence_anchor(plan)
    assert anchored["status"] == "needs_clarification"
    assert anchored["reason"] == "no_evidence_anchor"
    assert anchored["clarification"]["message"] == (
        "没识别出要找的行业、场景、人物角色或内容形式，请补充其中一项；不需要输入 SKU。"
    )


def test_persona_only_english_junk_is_still_rejected(z1_catalog: None) -> None:
    plan = smart_query_planner.plan_text_query_provider_free("find good top creators", body={})
    assert plan["status"] == "needs_clarification"
    assert plan["reason"] == "no_evidence_anchor"


# ── F8 · provider-free plans stop self-labelling as a rule_v0 degradation ────


def test_provider_free_fallback_is_not_labelled_as_degradation() -> None:
    plan = smart_query_planner._fallback_plan("找一些人", reason="provider_free_initial")
    assert plan["provider"] == "provider_free"
    assert plan["model"] == "provider_free"
    assert plan["fallback_used"] is False
    assert plan["provider_calls_performed"] is False
    assert plan["reason"] == "provider_free_initial"


def test_true_rule_fallback_keeps_its_honest_degradation_label() -> None:
    plan = smart_query_planner._fallback_plan("lens review creators")
    assert plan["provider"] == "rule_v0"
    assert plan["model"] == "rule_v0"
    assert plan["fallback_used"] is True


# ── End-to-end contract for the exact audited query ──────────────────────────


def test_audited_cjk_query_resolves_z1_pro_and_yields_a_usable_plan(
    z1_catalog: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Persona KB is a separate data dependency; pin it off so the test asserts
    # the resolver + fallback path deterministically.
    monkeypatch.setattr(
        smart_query_planner, "_plan_from_product_persona", lambda *_args, **_kwargs: None
    )
    plan = smart_query_planner.plan_text_query_provider_free(AUDIT_QUERY, body={})
    # (a) the product resolves to the real Z1 Pro row (F1).
    assert plan["resolved_product"] is not None
    assert plan["resolved_product"]["sku"] == "VINTAGE-Z1-PRO-TTL-RETRO-ON-CAMERA-FLASH"
    # (b) the plan carries non-generic lexical anchors.
    assert query_evidence_terms(plan["search_query"])
    # (c) it never dies at the clarification wall any more.
    assert plan["status"] != "needs_clarification"
    assert plan["provider_calls_performed"] is False
    # (d) F8 also covers the no-persona product branch: a designed
    # provider-free plan must not self-label as an LLM degradation.
    assert plan["reason"] == "provider_free_product_fallback"
    assert plan["provider"] == "provider_free"
    assert plan["model"] == "provider_free"
    assert plan["fallback_used"] is False


def test_cjk_profession_query_without_product_gets_provider_free_plan(
    z1_catalog: None,
) -> None:
    plan = smart_query_planner.plan_text_query_provider_free(
        "不同行业的用户比如赛车,厨师餐饮等", body={}
    )
    assert plan["status"] != "needs_clarification"
    assert [cell["segment"] for cell in plan["query_cells"]] == ["motorsport", "food"]
    assert plan["search_queries"] == ["motorsport photographer", "food chef content creator"]
    assert plan["search_query"] == plan["search_queries"][0]
    assert plan["provider"] == "provider_free"
    assert plan["fallback_used"] is False


def test_compact_pro_guard_rejects_foreign_compact_code(monkeypatch):
    """复审 F-1:他牌紧凑码 + 品类词(a7 pro flash)不得被 Z1 Pro 凑赢——紧凑码必须命中赢家。"""
    from app.domains.kol import product_resolver as pr

    pool = {
        1: {"id": 1, "sku": "VINTAGE-Z1-PRO-TTL-RETRO-ON-CAMERA-FLASH", "model_name": "Vintage Z1 Pro", "marketing_name": "Z1 Pro TTL Retro Flash", "series": "Pro"},
        2: {"id": 2, "sku": "VINTAGE-Z1-RETRO-ON-CAMERA-FLASH", "model_name": "Vintage Z1", "marketing_name": "Z1 Retro Flash", "series": ""},
    }
    monkeypatch.setattr(pr, "list_product_catalog", lambda **_kwargs: {"products": list(pool.values())})
    assert pr._COMPACT_PRO_RE.findall("sony a7 pro flash shooters") == ["a7"]
    resolved = pr.resolve_product("sony a7 pro flash shooters")
    assert resolved is None
    # 正主不受影响:z1 pro 的紧凑码命中赢家词集。
    resolved_z1 = pr.resolve_product("z1 pro flash")
    assert resolved_z1 is not None and "Z1-PRO" in str(resolved_z1.get("sku") or "")
