"""镜头出镜解析器:口语别名表 / 卡口短语 / 仅系列 / 斜杠列表 / v_relevance 投影。

目录夹具取自 2026-08 vkpi_products 真实快照的家族子集(SKU / model_name / series / mount 原样),
别名表每一条都要能经解析器落到目录家族——表里写错只会落 unresolved,测试就会红。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.kol import lens_evidence as le  # noqa: E402
from app.domains.products import product_aliases_lens as al  # noqa: E402


def _lens(sku: str, name: str, series: str, mount: str, category: str = "Lens") -> dict:
    return {"sku": sku, "model_name": name, "marketing_name": "", "category_main": category, "series": series, "mount": mount}


CATALOG = [
    _lens("AF-85MM-F14-PRO-FE", "Viltrox AF 85mm F1.4 Pro Full-Frame Lens for Sony E-Mount", "Pro", "FE-mount"),
    _lens("AF-85MM-F14-PRO-Z", "Viltrox AF 85mm F1.4 Pro Full-Frame Lens for Nikon Z-Mount", "Pro", "Z-mount"),
    _lens("VL-LEN085", "AF 85/1.4 FE", "", ""),
    _lens("AF-75MM-F12-PRO-FE", "Viltrox AF 75mm F1.2 Pro APS-C Lens for Sony E-Mount", "Pro", "FE-mount"),
    _lens("AF-75MM-F12-PRO-X", "Viltrox AF 75mm F1.2 Pro APS-C Lens for Fujifilm X-Mount", "Pro", "X-mount"),
    _lens("AF-27MM-F12-PRO-X", "Viltrox AF 27mm F1.2 Pro APS-C Lens for Fujifilm X-Mount", "Pro", "X-mount"),
    _lens("AF-27MM-F12-PRO-Z", "Viltrox AF 27mm F1.2 Pro APS-C Lens for Nikon Z-Mount", "Pro", "Z-mount"),
    _lens("AF-56MM-F12-PRO-X", "Viltrox AF 56mm F1.2 Pro APS-C Lens for Fujifilm X-Mount", "Pro", "X-mount"),
    _lens("AF-50MM-F14-PRO-FE", "Viltrox AF 50mm F1.4 Pro Full-Frame Lens for Sony E-Mount", "Pro", "FE-mount"),
    _lens("AF-135MM-F18-LAB-FE", "Viltrox AF 135mm F1.8 LAB Full-Frame Lens for Sony E-Mount", "LAB", "FE-mount"),
    _lens("AF-135MM-F18-LAB-Z", "Viltrox AF 135mm F1.8 LAB Full-Frame Lens for Nikon Z-mount", "LAB", "Z-mount"),
    _lens("AF-35MM-F12-LAB-FE", "Viltrox AF 35mm F1.2 LAB Full-Frame Lens for Sony E-Mount", "LAB", "FE-mount"),
    _lens("AF-75MM-F18-EVO-FE", "Viltrox AF 75mm F1.8 EVO Full-Frame Lens for Sony E-Mount", "EVO", "FE-mount"),
    _lens("AF-85MM-F20-EVO-FE", "Viltrox AF 85mm F2.0 EVO Full-Frame Lens for Sony E-Mount", "EVO", "FE-mount"),
    _lens("AF-85MM-F20-EVO-Z", "Viltrox AF 85mm F2.0 EVO Full-Frame Lens for Nikon Z-Mount", "EVO", "Z-mount"),
    _lens("AF-90MM-F22-EVO-X", "Viltrox AF 90mm F2.2 EVO Full-Frame Lens for Fujifilm X-Mount", "EVO", "X-mount"),
    _lens("AF-55MM-F18-EVO-FE", "Viltrox AF 55mm F1.8 EVO Full-Frame Lens for Sony E-Mount", "EVO", "FE-mount"),
    _lens("AF-35MM-F18-EVO-FE", "Viltrox AF 35mm F1.8 EVO Full-Frame Lens for Sony E-Mount", "EVO", "FE-mount"),
    _lens("AF-26MM-F28-EVO-FE", "Viltrox AF 26mm F2.8 EVO Full-Frame Lens for Sony E-Mount", "EVO", "FE-mount"),
    _lens("AF-9MM-F28-AIR-FE", "Viltrox AF 9mm F2.8 Air APS-C Lens for Sony E-Mount", "Air", "FE-mount"),
    _lens("AF-14MM-F4-AIR-FE", "Viltrox AF 14mm F4.0 Air Full-Frame Lens for Sony E-Mount", "Air", "FE-mount"),
    _lens("AF-15MM-F17-AIR-Z", "Viltrox AF 15mm F1.7 Air APS-C Lens for Nikon Z-Mount", "Air", "Z-mount"),
    _lens("AF-20MM-F28-AIR-FE", "Viltrox AF 20mm F2.8 Air Full-Frame Lens for Sony E-Mount", "Air", "FE-mount"),
    _lens("AF-25MM-F17-AIR-X", "Viltrox AF 25mm F1.7 Air APS-C Lens for Fujifilm X-Mount", "Air", "X-mount"),
    _lens("AF-35MM-F17-AIR-FE", "Viltrox AF 35mm F1.7 Air APS-C Lens for Sony E-Mount", "Air", "FE-mount"),
    _lens("AF-40MM-F25-AIR-FE", "Viltrox AF 40mm F2.5 Air Full-Frame Lens for Sony E-Mount", "Air", "FE-mount"),
    _lens("AF-50MM-F20-AIR-FE", "Viltrox AF 50mm F2.0 Air Full-Frame Lens for Sony E-Mount", "Air", "FE-mount"),
    _lens("AF-50MM-F20-AIR-Z", "Viltrox AF 50mm F2.0 Air Full-Frame Lens for Nikon Z-Mount", "Air", "Z-mount"),
    _lens("AF-56MM-F17-AIR-X", "Viltrox AF 56mm F1.7 Air APS-C Lens for Fujifilm X-Mount", "Air", "X-mount"),
    _lens("AF-13MM-F14-X", "Viltrox AF 13mm F1.4 APS-C Lens for Fujifilm X-Mount", "", "X-mount"),
    _lens("AF-23MM-F14-X", "Viltrox AF 23mm F1.4 APS-C Lens for Fujifilm X-Mount", "", "X-mount"),
    _lens("AF-23MM-F14-Z", "Viltrox AF 23mm F1.4 APS-C Lens for Nikon Z-Mount", "", "Z-mount"),
    _lens("AF-33MM-F14-X", "Viltrox AF 33mm F1.4 APS-C Lens for Fujifilm X-Mount", "", "X-mount"),
    _lens("AF-56MM-F14-X", "Viltrox AF 56mm F1.4 APS-C Lens for Fujifilm X-Mount", "", "X-mount"),
    _lens("AF-16MM-F18-FE", "Viltrox AF 16mm F1.8 Full-Frame Lens for Sony E-Mount", "", "FE-mount"),
    _lens("AF-16MM-F18-Z", "Viltrox AF 16mm F1.8 Full-Frame Lens for Nikon Z-Mount", "", "Z-mount"),
    _lens("AF-24MM-F18-Z", "Viltrox AF 24mm F1.8 Full-Frame Lens for Nikon Z-Mount", "", "Z-mount"),
    _lens("AF-28MM-F18-FE", "Viltrox AF 28mm F1.8 Full-Frame Lens for Sony E-Mount", "", "FE-mount"),
    _lens("AF-28MM-F4-5-FE", "Viltrox AF 28mm F4.5 Full-Frame Lens for Sony E-Mount", "", "FE-mount"),
    _lens("AF-28MM-F4-5-Z", "Viltrox AF 28mm F4.5 Full-Frame Lens for Nikon Z-Mount", "", "Z-mount"),
    _lens("AF-28MM-F4-5-CHIP-L", "Viltrox AF 28mm F4.5 Chip Full-Frame Lens for Leica L-Mount", "", "L-mount"),
    _lens("AF-85MM-F18-II-FE", "Viltrox AF 85mm F1.8 II Full-Frame Lens for Sony E-Mount", "", "FE-mount"),
    _lens("AF-85MM-F18-II-X", "Viltrox AF 85mm F1.8 II Full-Frame Lens for Fujifilm X-Mount", "", "X-mount"),
    _lens("AF-35MM-F18-II-FE", "Viltrox AF 35mm F1.8 II Full-Frame Lens for Sony E-Mount", "", "FE-mount"),
    _lens("MF-20MM-F18-FE", "Viltrox MF 20mm F1.8 Full-Frame Lens for Sony E-Mount", "", "FE-mount"),
    _lens("PFU-RBMH-20MM-F18-ASPH-FE", "Viltrox PFU RBMH 20mm F1.8 ASPH Lens for Sony E-Mount", "", ""),
    _lens("EPIC-50MM-T2-0-1-33X-PL-ANAMORPHIC-CINE-L", "Viltrox EPIC 50mm T2.0 1.33X PL Full-Frame Anamorphic Cine Lens", "Cine", "L-mount"),
    _lens("EPIC-35MM-T2-0-1-33X-PL-ANAMORPHIC-CINE-L", "Viltrox EPIC 35mm T2.0 1.33X PL Full-Frame Anamorphic Cine Lens", "Cine", "L-mount"),
    _lens("EPIC-65MM-T2-8-MACRO-1-33X-PL-ANAMORPHIC-CINE-L", "Viltrox EPIC 65mm T2.8 Macro 1.33X PL Full-Frame Anamorphic Cine Lens", "Cine", "L-mount"),
    _lens("EPIC-75MM-T2-0-1-33X-PL-ANAMORPHIC-CINE-L", "Viltrox EPIC 75mm T2.0 1.33X PL Full-Frame Anamorphic Cine Lens", "Cine", "L-mount"),
    _lens("EPIC-100MM-T2-0-1-33X-PL-ANAMORPHIC-CINE-L", "Viltrox EPIC 100mm T2.0 1.33X PL Full-Frame Anamorphic Cine Lens", "Cine", "L-mount"),
    _lens("EPIC-135MM-T2-4-1-33X-PL-ANAMORPHIC-CINE-L", "Viltrox EPIC 135mm T2.4 1.33X PL Full-Frame Anamorphic Cine Lens", "Cine", "L-mount"),
    _lens("MF-33MM-T1-5-CINE-M4-3-MOUNT-M43", "Viltrox MF 33mm T1.5 Cine Lens M4/3 Mount", "Cine", "M43"),
    _lens("VINTAGE-Z1-PRO-TTL-RETRO-ON-CAMERA-FLASH", "Viltrox Vintage Z1 Pro TTL Retro On-Camera Flash", "Pro", "", "Lighting"),
    _lens("VINTAGE-Z1-RETRO-ON-CAMERA-FLASH", "Viltrox Vintage Z1 Retro On-Camera Flash", "", "", "Lighting"),
    _lens("VL-LIT073", "Vintage Z1+", "", "", "Lighting/Flash"),
    _lens("VINTAGE-Z2-MINI-TTL-ON-CAMERA-FLASH", "Vintage Z2 Mini TTL On-Camera Flash", "", "", "Lighting"),
    _lens("VL-LIT078", "Vintage Z2-S", "", "", "Lighting/Flash"),
    _lens("SPARK-Z3-TTL-ON-CAMERA-FLASH", "Viltrox Spark Z3 TTL On-Camera Flash", "", "", "Lighting"),
    _lens("K60-RGB-LIGHT-STICK", "Viltrox K60 RGB Light Stick", "", "", "Lighting"),
    _lens("S05-POCKET-RGB-LED-VIDEO-LIGHT", "Viltrox S05 Pocket RGB LED Video Light", "", "", "Lighting"),
    _lens("NEXUSFOCUS-F1-PL-E-AUTOFOCUS-CONTROL-SYSTEM-NEXUS", "Viltrox NexusFocus F1 PL-E Autofocus Control System", "Nexus", "", "Accessories"),
    _lens("VL-ADP056", "NexusFocus F1", "", "", "Adapter"),
    _lens("TC-2-0X-TELECONVERTER-FE", "Viltrox TC-2.0X Teleconverter for Sony", "", "FE-mount", "Product"),
    _lens("TC-2-0X-TELECONVERTER-Z", "Viltrox TC-2.0X Teleconverter for Nikon", "", "Z-mount", "Product"),
    _lens("EF-E-II-AF-BOOSTER-ADAPTER", "Viltrox EF-E II AF Booster Lens Adapter", "", "", "Lens"),
    _lens("VL-ADP005", "EF-E II", "", "", "Adapter"),
    _lens("EF-Z2-ADAPTER", "Viltrox EF-Z2 Lens Adapter", "", "", "Adapter"),
    _lens("VL-MON006", "DC-X2", "", "", "Monitor"),
    _lens("VL-MON007", "DC-X3", "", "", "Monitor"),
    _lens("DC-X-FHD-2000-NITS-6-INCH-CAMERA-MONITOR", "Viltrox DC-X FHD 2000 Nits 6-Inch Camera Monitor", "", "", "Monitor"),
    _lens("DC-A1-2800-NITS-7-INCH-CAMERA-MONITOR", "Viltrox DC-A1 2800 Nits 7-Inch Camera Monitor", "", "", "Monitor"),
]


@pytest.fixture(scope="module")
def index() -> le.CatalogIndex:
    return le.CatalogIndex(CATALOG, aliases=None)


def _resolve(index: le.CatalogIndex, mention: str) -> dict:
    return index.resolve(le.canonical_text(mention))


# ── 口语别名 ≥ 20 条:型号 / 焦段 / 卡口 / 系列 ─────────────────────────────


@pytest.mark.parametrize(
    ("mention", "resolution", "sku", "display"),
    [
        ("85 1.4", "family", None, "AF 85mm F1.4 Pro"),
        ("85mm f1.4 Pro", "family", None, "AF 85mm F1.4 Pro"),
        ("AF85 F1.4", "family", None, "AF 85mm F1.4 Pro"),
        ("85/1.4", "family", None, "AF 85mm F1.4 Pro"),
        ("Z-mount 85mm F1.4", "sku", "AF-85MM-F14-PRO-Z", "AF 85mm F1.4 Pro"),
        ("85 1.4 Pro FE", "sku", "AF-85MM-F14-PRO-FE", "AF 85mm F1.4 Pro"),
        ("75 1.2", "family", None, "AF 75mm F1.2 Pro"),
        ("27 1.2 Pro", "family", None, "AF 27mm F1.2 Pro"),
        ("56 1.2", "sku", "AF-56MM-F12-PRO-X", "AF 56mm F1.2 Pro"),
        ("135 LAB", "family", None, "AF 135mm F1.8 LAB"),
        ("135 1.8", "family", None, "AF 135mm F1.8 LAB"),
        ("35 LAB", "sku", "AF-35MM-F12-LAB-FE", "AF 35mm F1.2 LAB"),
        ("AF 35/1.2", "sku", "AF-35MM-F12-LAB-FE", "AF 35mm F1.2 LAB"),
        ("75 EVO", "sku", "AF-75MM-F18-EVO-FE", "AF 75mm F1.8 EVO"),
        ("85 F2", "family", None, "AF 85mm F2.0 EVO"),
        ("85 2.0 Z-mount", "sku", "AF-85MM-F20-EVO-Z", "AF 85mm F2.0 EVO"),
        ("26mm f2.8", "sku", "AF-26MM-F28-EVO-FE", "AF 26mm F2.8 EVO"),
        ("9 Air", "sku", "AF-9MM-F28-AIR-FE", "AF 9mm F2.8 Air"),
        ("14mm F4", "sku", "AF-14MM-F4-AIR-FE", "AF 14mm F4.0 Air"),
        ("50 F2", "family", None, "AF 50mm F2.0 Air"),
        ("50 Air Z-mount", "sku", "AF-50MM-F20-AIR-Z", "AF 50mm F2.0 Air"),
        ("13 1.4", "sku", "AF-13MM-F14-X", "AF 13mm F1.4"),
        ("23 1.4", "family", None, "AF 23mm F1.4"),
        ("16 1.8", "family", None, "AF 16mm F1.8"),
        ("28 4.5", "family", None, "AF 28mm F4.5"),
        ("28mm pancake", "family", None, "AF 28mm F4.5"),
        ("28 Chip", "sku", "AF-28MM-F4-5-CHIP-L", "AF 28mm F4.5 Chip"),
        ("85 1.8 II", "family", None, "AF 85mm F1.8 II"),
        ("AF 85mm F1.8 XF", "sku", "AF-85MM-F18-II-X", "AF 85mm F1.8 II"),
        ("MF 20 1.8", "sku", "MF-20MM-F18-FE", "MF 20mm F1.8"),
        ("Epic 50", "sku", "EPIC-50MM-T2-0-1-33X-PL-ANAMORPHIC-CINE-L", "EPIC 50mm T2.0 1.33X PL Anamorphic Cine"),
        ("Epic 65 Macro", "sku", "EPIC-65MM-T2-8-MACRO-1-33X-PL-ANAMORPHIC-CINE-L", "EPIC 65mm T2.8 Macro 1.33X PL Anamorphic Cine"),
        ("M43 33mm T1.5", "sku", "MF-33MM-T1-5-CINE-M4-3-MOUNT-M43", "MF 33mm T1.5 Cine M43 Mount"),
        ("Z1", "sku", "VINTAGE-Z1-RETRO-ON-CAMERA-FLASH", "Vintage Z1"),
        ("Z1 Pro", "sku", "VINTAGE-Z1-PRO-TTL-RETRO-ON-CAMERA-FLASH", "Vintage Z1 Pro"),
        ("Z2", "sku", "VINTAGE-Z2-MINI-TTL-ON-CAMERA-FLASH", "Vintage Z2"),
        ("Z3", "sku", "SPARK-Z3-TTL-ON-CAMERA-FLASH", "Spark Z3"),
        ("Nexus Focus", "sku", "NEXUSFOCUS-F1-PL-E-AUTOFOCUS-CONTROL-SYSTEM-NEXUS", "NexusFocus F1.0"),
        ("NexusFocus F1.0 PL-E", "sku", "NEXUSFOCUS-F1-PL-E-AUTOFOCUS-CONTROL-SYSTEM-NEXUS", "NexusFocus F1.0"),
        ("TC 2X", "family", None, "TC-2.0X"),
        ("EF-E2", "sku", "VL-ADP005", "EF-E II"),  # 官方行家族名带后缀,旧目录行是唯一精确家族命中
        ("DC-X3", "sku", "VL-MON007", "DC-X3"),
    ],
)
def test_alias_table_resolves_colloquial_mentions(index, mention, resolution, sku, display) -> None:
    outcome = _resolve(index, mention)
    assert outcome["resolution"] == resolution
    assert outcome["product_sku"] == sku
    assert outcome["display_name"] == display


@pytest.mark.parametrize(
    ("mention", "display"),
    [
        ("25mm F1.8", "25mm F1.8"),          # 目录只有 25mm F1.7 Air,光圈对不上不猜
        ("45mm T1.5", "45mm T1.5"),          # 目录无此电影头
        ("16mm F1.8 Pro", "16mm F1.8 Pro"),  # 目录 16mm F1.8 没有 Pro 版,系列是硬约束
        ("24-70mm f2.8", "24-70mm f2.8"),    # 变焦不在目录
        ("DC-K6", "DC-K6"),
        ("AF 50mm F1.8", "AF 50mm F1.8"),
    ],
)
def test_unknown_models_stay_unresolved_with_original_text(index, mention, display) -> None:
    outcome = _resolve(index, mention)
    assert outcome["resolution"] == "unresolved" and outcome["product_sku"] is None
    assert outcome["display_name"] == display


def test_focal_only_mention_with_many_families_is_unresolved_with_candidates(index) -> None:
    outcome = _resolve(index, "Z-mount 85mm")
    assert outcome["resolution"] == "unresolved"
    assert set(outcome["candidate_skus"]) == {"AF-85MM-F14-PRO-Z", "AF-85MM-F20-EVO-Z"}


def test_mount_missing_in_catalog_falls_back_to_family_never_sku(index) -> None:
    outcome = _resolve(index, "AF 23mm F1.4 E")
    assert outcome["resolution"] == "family" and outcome["product_sku"] is None
    assert outcome["display_name"] == "AF 23mm F1.4" and "mount_unmatched" in outcome["note"]


def test_alias_table_every_row_lands_on_catalog_family(index) -> None:
    rows = list(al.alias_rows())
    assert len(rows) >= 40
    misses = [row["alias"] for row in rows if _resolve(index, row["alias"])["resolution"] == "unresolved"]
    assert misses == []
    # 拼写变体允许落同一个键(「85 1.4」「AF85 F1.4」「85/1.4」),但同键必须指向同一 canonical
    by_key: dict[str, set[str]] = {}
    for row in rows:
        by_key.setdefault(row["alias_key"], set()).add(row["canonical"])
    assert len(by_key) >= 40
    assert [key for key, canon in by_key.items() if len(canon) > 1] == []


def test_alias_key_normalizes_spelling_variants() -> None:
    assert al.alias_key("85mm F1.4 Pro") == al.alias_key("85 1.4 pro") == al.alias_key("AF85 f/1.4 Pro") == al.alias_key("85/1.4 Pro")
    assert al.alias_key("50 F2") == al.alias_key("50mm F2.0")
    assert al.alias_key("35mm T1.5") != al.alias_key("35mm F1.5")
    assert al.lookup_lens_alias("不存在的型号") == ""


# ── 卡口短语 / 仅系列 / 斜杠列表 ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "token"),
    [
        ("Z 卡口 85 1.4", "Z-mount"),
        ("尼康口 85mm", "Z-mount"),
        ("索尼口 85 1.4", "FE-mount"),
        ("E卡口 85mm", "FE-mount"),
        ("富士口 27 1.2", "X-mount"),
        ("XF 卡口 27mm", "X-mount"),
        ("L 卡口 16mm", "L-mount"),
        ("85mm F1.4 for Sony E", "FE-mount"),
        ("M43 卡口 33mm", "M43"),
    ],
)
def test_mount_phrases_rewrite_to_tokens(text, token) -> None:
    assert token in al.rewrite_mount_phrases(text)


def test_mount_phrase_does_not_touch_plain_camera_brand_words() -> None:
    assert al.rewrite_mount_phrases("装在索尼 A7IV 上") == "装在索尼 A7IV 上"


def test_chinese_mount_phrase_resolves_to_sku(index) -> None:
    rows = le.extract_resolved({"layer1_visual_content": {"product_presence": "Viltrox Z 卡口 85 1.4 特写出镜。"}}, index)
    assert [(r["resolution"], r["product_sku"]) for r in rows] == [("sku", "AF-85MM-F14-PRO-Z")]
    assert rows[0]["v_relevance"] == "confirmed"


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("Pro 系列", ["Pro"]),
        ("Pro series", ["Pro"]),
        ("Epics", ["EPIC"]),
        ("LAB 高端线", ["LAB"]),
        ("Pro LAB", ["Pro", "LAB"]),
        ("AF 系列", []),
        ("85mm Pro", []),
    ],
)
def test_series_only_detection(text, codes) -> None:
    assert al.series_only_codes(text) == codes


def test_series_only_mentions_project_to_likely_and_never_pick_a_model(index) -> None:
    result = {
        "layer1_visual_content": {
            "product_presence": "画面里多次出现 Viltrox Pro 系列标识;Viltrox Epics 装在机身上;Viltrox Pro/LAB 系列特征吻合。",
        },
        "layer6_flags_and_scores": {"final_verdict": "建议推 Viltrox LAB 系列给他。"},
    }
    rows = le.extract_resolved(result, index)
    by_key = {row["lens_key"]: row for row in rows}
    assert set(by_key) == {"series:pro", "series:epic", "series:lab"}
    for row in rows:
        assert row["resolution"] == "unresolved" and row["product_sku"] is None
        assert row["v_relevance"] == "likely" and row["v_reason"] == "series_only"
    assert by_key["series:pro"]["display_name"] == "Pro 系列"
    # Pro 跨镜头与闪光灯(Vintage Z1 Pro),品类不唯一就留空,不硬标 Lens
    assert by_key["series:pro"]["category_main"] == "" and by_key["series:lab"]["category_main"] == "Lens"
    assert "AF-85MM-F14-PRO-FE" in by_key["series:pro"]["candidate_skus"]
    # final_verdict 里的「推 LAB 系列」是建议,不算仅系列证据(LAB 行只来自 product_presence)
    assert by_key["series:lab"]["source_fields"] == ["product_presence"]


def test_series_only_from_advisory_field_alone_is_dropped(index) -> None:
    rows = le.extract_resolved({"layer6_flags_and_scores": {"final_verdict": "建议推 Viltrox Pro 系列给他。"}}, index)
    assert rows == []


def test_leading_series_word_is_kept_as_hard_constraint(index) -> None:
    rows = le.extract_resolved({"layer1_visual_content": {"product_presence": "Viltrox Pro 75mm F1.2 出镜。"}}, index)
    assert [row["display_name"] for row in rows] == ["AF 75mm F1.2 Pro"]


@pytest.mark.parametrize(
    ("body", "pieces"),
    [
        ("13mm/23mm/27mm/75mm Pro", ["13mm Pro", "23mm Pro", "27mm Pro", "75mm Pro"]),
        ("DC-X2/X3", ["DC-X2", "DC-X3"]),
        ("75mm/27mm F1.2", ["75mm F1.2", "27mm F1.2"]),
        ("AF 85mm F1.4 Pro", ["AF 85mm F1.4 Pro"]),
    ],
)
def test_slash_lists_split(body, pieces) -> None:
    assert le.split_slash_list(body) == pieces


def test_slash_list_pieces_retry_without_list_suffix(index) -> None:
    rows = le.extract_resolved({"layer1_visual_content": {"product_presence": "Viltrox 13mm/27mm/75mm Pro 出镜;Viltrox DC-X2/X3 监看。"}}, index)
    names = {row["display_name"]: row for row in rows}
    assert set(names) == {"AF 13mm F1.4", "AF 27mm F1.2 Pro", "AF 75mm F1.2 Pro", "DC-X2", "DC-X3"}
    assert "list_suffix_dropped" in names["AF 13mm F1.4"]["note"]
    assert names["AF 27mm F1.2 Pro"]["note"] == ""


def test_canonical_text_keeps_m43_mount_intact() -> None:
    assert le.canonical_text("MF 33mm T1.5 Cine Lens M4/3 Mount") == "MF 33mm T1.5 Cine Lens M43 Mount"
    assert le.family_name("Viltrox MF 33mm T1.5 Cine Lens M4/3 Mount") == "MF 33mm T1.5 Cine M43 Mount"


# ── v_relevance 三态投影 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"resolution": "sku", "lens_key": "af85mmf14pro", "modalities": ["visual"], "source_fields": ["product_presence"]}, ("confirmed", "catalog_match_explicit_modality")),
        ({"resolution": "family", "lens_key": "af85mmf14pro", "modalities": ["voice"], "source_fields": ["scene_timeline"]}, ("confirmed", "catalog_match_explicit_modality")),
        ({"resolution": "family", "lens_key": "af85mmf14pro", "modalities": ["text"], "source_fields": ["brand_exposure"]}, ("confirmed", "catalog_match_explicit_modality")),
        ({"resolution": "sku", "lens_key": "af85mmf14pro", "modalities": ["unspecified"], "source_fields": ["product_contribution"]}, ("likely", "catalog_match_modality_unspecified")),
        ({"resolution": "sku", "lens_key": "af85mmf14pro", "modalities": ["visual"], "source_fields": ["final_verdict"]}, ("likely", "advisory_field_only")),
        ({"resolution": "sku", "lens_key": "af85mmf14pro", "modalities": ["visual"], "source_fields": ["final_verdict", "product_presence"]}, ("confirmed", "catalog_match_explicit_modality")),
        ({"resolution": "unresolved", "lens_key": "series:pro", "modalities": ["visual"], "source_fields": ["product_presence"]}, ("likely", "series_only")),
        ({"resolution": "unresolved", "lens_key": "", "modalities": ["visual"], "source_fields": ["product_presence"]}, ("likely", "unresolved_mention")),
    ],
)
def test_v_relevance_projection(row, expected) -> None:
    assert le.v_relevance_for(row) == expected


def test_explain_trace_carries_anchor_and_projection(index) -> None:
    trace = le.explain({"layer1_visual_content": {"product_presence": "Viltrox AF 85mm F1.4 Pro 特写;Viltrox 没有别的产品。"}}, index)
    assert trace["extractor_version"] == le.EXTRACTOR_VERSION == "lens_evidence_v2"
    assert trace["alias_table_version"] == al.ALIAS_TABLE_VERSION
    bodies = [anchor["body"] for anchor in trace["anchors"]]
    assert bodies == ["AF 85mm F1.4 Pro", ""]
    assert trace["rows"][0]["v_relevance"] == "confirmed" and trace["rows"][0]["product_sku"] is None
