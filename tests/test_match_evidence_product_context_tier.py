"""产品证据分两档:型号级(精确型号/系列+焦段)与品牌/卡口级语境;新品无人提过型号时卡口级放行,
仅焦段属性或仅人设仍拒(2026-08-23 严格 30 搜索 496/500 被 no_match_evidence 刷掉)。"""
from __future__ import annotations

from app.domains.kol import profile_recall_match_evidence as me

QUERY = "photographer videographer portrait photographer filmmaker"
PRODUCT_TERMS = ["af-26mm-f28-evo-fe", "viltrox", "26mm", "f2.8", "evo", "full-frame", "sony", "e-mount"]


def _row(bio: str) -> dict:
    return {"handle": "x", "display_name": "Street Shooter", "bio": bio, "primary_topic": "portrait photographer",
            "content_style": "travel filmmaker", "secondary_topics_json": "[]", "profile_text": bio, "type_reason": ""}


def test_new_lens_with_mount_context_passes_with_context_level():
    ev = me.build_match_evidence(_row("Sony E-mount street photographer based in Lisbon"), {}, QUERY, required_product_terms=PRODUCT_TERMS)
    assert ev
    assert ev[0]["term"] in {"sony", "e-mount"}  # 语境证明排在最前


def test_exact_model_mention_is_identity_level():
    ev = me.build_match_evidence(_row("Shot on Viltrox AF 26mm F2.8 EVO FE"), {}, QUERY, required_product_terms=PRODUCT_TERMS)
    assert ev and ev[0]["term"] in {"af-26mm-f28-evo-fe", "evo", "26mm"}  # 型号级(系列+焦段)证明优先


def test_persona_only_or_attribute_only_still_rejected():
    assert me.build_match_evidence(_row("Professional wedding filmmaker and photographer"), {}, QUERY, required_product_terms=PRODUCT_TERMS) == []
    assert me.build_match_evidence(_row("Photographer who loves 26mm and f2.8 primes"), {}, QUERY, required_product_terms=PRODUCT_TERMS) == []


def test_intent_still_required_even_with_context():
    assert me.build_match_evidence(_row("Sony E-mount gear deals and unboxings"), {}, "wedding videographer", required_product_terms=PRODUCT_TERMS) == []


def test_recall_evidence_gate_uses_persona_terms():
    """profile_recall 证据闸必须用 检索词∪人群词(evidence_query_text),否则泛角色词被剔光整池判无证据。"""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("backend/app/domains/kol/profile_recall.py").read_text(encoding="utf-8")
    assert 'evidence_query_text = f"{resolved_text} {persona_text}"' in src
    assert "build_match_evidence(row, evidence, evidence_query_text" in src
