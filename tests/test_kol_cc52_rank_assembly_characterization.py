"""Behavior locks for the CC-52 ranking/assembly refactors (focal_recommendations + roster_optimizer).

Captured verbatim from the pre-refactor implementations on fixed inputs; the
refactor must keep every ordering, tie-break, score, reason string and payload
key byte-identical.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from app.domains.kol import roster_optimizer
from app.domains.kol.focal_recommendations import product_opportunities
from scripts.vkpi_engineering_health_collect import collect_complexity

ROOT = Path(__file__).resolve().parents[1]


# ── product_opportunities fixtures ─────────────────────────────────────────

LINE_LABELS = {"af_lens": "AF", "mf_lens": "MF", "cine": "Cine"}
GAPS = {"24mm", "35mm", "50mm", "85mm"}
CONTEXT = {
    "mount": "FE-mount",
    "content_lane": "cinema",
    "catalog_price_ceiling_proxy_usd": 1000.0,
    "camera_body": "Sony FX3",
    "lens_brands": ["sigma", "viltrox"],
}

PRODUCTS = [
    {"status": "official", "line": "af_lens", "focals": ["35mm"], "series": "PRO",
     "sku": "AF-35-P1", "model_name": "AF 35 F1.8 FE", "mount": "FE-mount", "price_usd": 500,
     "marketing_name": "AF 35mm Pro One", "product_url": "https://x/p1", "official_catalog_product_id": "cat-1"},
    {"status": "official", "line": "af_lens", "focals": ["50mm"], "series": "EVO",
     "sku": "AF-50-E1", "model_name": "AF 50 F1.8 FE", "mount": "FE-mount", "price_usd": 300,
     "marketing_name": "AF 50mm Evo", "product_url": "https://x/p2", "official_catalog_product_id": "cat-2"},
    {"status": "official", "line": "cine", "focals": ["85mm"], "series": "",
     "sku": "CINE-85-T1", "model_name": "Cine 85 T1.5 FE", "mount": "FE-mount", "price_usd": 1400,
     "marketing_name": "Cine 85 T1.5", "product_url": "https://x/p3", "official_catalog_product_id": "cat-3"},
    {"status": "official", "line": "af_lens", "focals": ["35mm"], "series": "PRO",
     "sku": "AF-35-P2", "model_name": "AF 35 F1.4 FE II", "mount": "FE-mount", "price_usd": 550,
     "marketing_name": "AF 35mm Pro Two", "product_url": "https://x/p4", "official_catalog_product_id": "cat-4"},
    {"status": "official", "line": "af_lens", "focals": ["24mm"], "series": "EVO",
     "sku": "AF-24-E1", "model_name": "AF 24 F1.8 FE", "mount": "FE-mount", "price_usd": 900,
     "marketing_name": "AF 24mm Evo", "product_url": "https://x/p5", "official_catalog_product_id": "cat-5"},
    {"status": "official", "line": "af_lens", "focals": ["24mm"], "series": "AIR",
     "sku": "AF-24-A1", "model_name": "AF 24 F2.8 FE", "mount": "FE-mount", "price_usd": None,
     "marketing_name": "", "product_url": "", "official_catalog_product_id": ""},
    # ── rows the filter chain must drop ──
    {"status": "draft", "line": "af_lens", "focals": ["35mm"], "series": "PRO",
     "sku": "F1", "model_name": "Draft", "mount": "FE-mount", "price_usd": 100},
    {"status": "official", "line": "accessory", "focals": ["35mm"], "series": "",
     "sku": "F2", "model_name": "Hood", "mount": "FE-mount", "price_usd": 20},
    {"status": "official", "line": "af_lens", "focals": ["35mm", "50mm"], "series": "PRO",
     "sku": "F3", "model_name": "Zoomish", "mount": "FE-mount", "price_usd": 700},
    {"status": "official", "line": "af_lens", "focals": ["135mm"], "series": "PRO",
     "sku": "F4", "model_name": "AF 135", "mount": "FE-mount", "price_usd": 800},
    {"status": "official", "line": "af_lens", "focals": ["35mm"], "series": "PRO",
     "sku": "F5", "model_name": "AF 35 Z", "mount": "Z-mount", "price_usd": 500},
    {"status": "official", "line": "af_lens", "focals": ["35mm"], "series": "PRO",
     "sku": "F6", "model_name": "AF 35 nomount", "mount": "", "price_usd": 500},
    {"status": "official", "line": "cine", "focals": ["85mm"], "series": "",
     "sku": "F8-CINE-OUT", "model_name": "Cine 85 XL", "mount": "FE-mount", "price_usd": 3500,
     "marketing_name": "Cine Outlier"},
]


def _rank(context: dict[str, Any], **kwargs: Any) -> list[dict[str, Any]]:
    return product_opportunities(
        PRODUCTS, GAPS, context,
        product_line_of=lambda row: row["line"],
        product_focals=lambda row, line: set(row["focals"]),
        focal_sort_mm=lambda focal: float(focal.rstrip("m")),
        line_labels=LINE_LABELS,
        **kwargs,
    )


def test_product_opportunities_full_payload_order_and_tiebreaks() -> None:
    """Filters, scoring, sort tie-break (price_distance), family dedup and the
    two-pass series diversification must reproduce the captured list verbatim.
    """
    assert _rank(CONTEXT) == [
        {"focal": "35mm", "mm": 35.0, "sku": "AF-35-P2", "product_name": "AF 35mm Pro Two",
         "flagship": "AF 35mm Pro Two", "series": ["PRO"], "line": "AF", "lines": ["AF"],
         "mount": "FE-mount", "price_usd": 550.0, "max_price_usd": 550.0,
         "product_url": "https://x/p4", "official_catalog_product_id": "cat-4",
         "sku_count": 1, "official_sku_count": 1, "value_usd": 550.0,
         "recommendation_score": 107,
         "score_breakdown": {"base": 40, "mount": 30, "content": 8, "series": 12, "price": 12, "evidence": 5},
         "compatibility_status": "compatible", "confidence": "high",
         "price_fit": "within_band", "price_distance": 100.0,
         "reasons": ["与推断卡口 FE-mount 匹配", "自动对焦单品适合视频/混合创作", "PRO 系列参与多样化候选",
                     "目录价 USD 550 · 价格带 within_band", "已识别常用镜头品牌: sigma/viltrox"]},
        {"focal": "24mm", "mm": 24.0, "sku": "AF-24-E1", "product_name": "AF 24mm Evo",
         "flagship": "AF 24mm Evo", "series": ["EVO"], "line": "AF", "lines": ["AF"],
         "mount": "FE-mount", "price_usd": 900.0, "max_price_usd": 900.0,
         "product_url": "https://x/p5", "official_catalog_product_id": "cat-5",
         "sku_count": 1, "official_sku_count": 1, "value_usd": 900.0,
         "recommendation_score": 106,
         "score_breakdown": {"base": 40, "mount": 30, "content": 8, "series": 11, "price": 12, "evidence": 5},
         "compatibility_status": "compatible", "confidence": "high",
         "price_fit": "within_band", "price_distance": 250.0,
         "reasons": ["与推断卡口 FE-mount 匹配", "自动对焦单品适合视频/混合创作", "EVO 系列参与多样化候选",
                     "目录价 USD 900 · 价格带 within_band", "已识别常用镜头品牌: sigma/viltrox"]},
        {"focal": "85mm", "mm": 85.0, "sku": "CINE-85-T1", "product_name": "Cine 85 T1.5",
         "flagship": "Cine 85 T1.5", "series": ["Cine"], "line": "Cine", "lines": ["Cine"],
         "mount": "FE-mount", "price_usd": 1400.0, "max_price_usd": 1400.0,
         "product_url": "https://x/p3", "official_catalog_product_id": "cat-3",
         "sku_count": 1, "official_sku_count": 1, "value_usd": 1400.0,
         "recommendation_score": 97,
         "score_breakdown": {"base": 40, "mount": 30, "content": 12, "series": 5, "price": 5, "evidence": 5},
         "compatibility_status": "compatible", "confidence": "high",
         "price_fit": "stretch", "price_distance": 750.0,
         "reasons": ["与推断卡口 FE-mount 匹配", "视频/电影创作语境与 Cine 产品线匹配", "Cine 系列参与多样化候选",
                     "目录价 USD 1,400 · 价格带 stretch", "已识别常用镜头品牌: sigma/viltrox"]},
        {"focal": "24mm", "mm": 24.0, "sku": "AF-24-A1", "product_name": "AF 24 F2.8 FE",
         "flagship": "AF 24 F2.8 FE", "series": ["AIR"], "line": "AF", "lines": ["AF"],
         "mount": "FE-mount", "price_usd": None, "max_price_usd": None,
         "product_url": None, "official_catalog_product_id": None,
         "sku_count": 1, "official_sku_count": 1, "value_usd": 0.0,
         "recommendation_score": 90,
         "score_breakdown": {"base": 40, "mount": 30, "content": 8, "series": 7, "price": 0, "evidence": 5},
         "compatibility_status": "compatible", "confidence": "high",
         "price_fit": "price_unknown", "price_distance": 350.0,
         "reasons": ["与推断卡口 FE-mount 匹配", "自动对焦单品适合视频/混合创作", "AIR 系列参与多样化候选",
                     "已识别常用镜头品牌: sigma/viltrox"]},
        {"focal": "50mm", "mm": 50.0, "sku": "AF-50-E1", "product_name": "AF 50mm Evo",
         "flagship": "AF 50mm Evo", "series": ["EVO"], "line": "AF", "lines": ["AF"],
         "mount": "FE-mount", "price_usd": 300.0, "max_price_usd": 300.0,
         "product_url": "https://x/p2", "official_catalog_product_id": "cat-2",
         "sku_count": 1, "official_sku_count": 1, "value_usd": 300.0,
         "recommendation_score": 99,
         "score_breakdown": {"base": 40, "mount": 30, "content": 8, "series": 11, "price": 5, "evidence": 5},
         "compatibility_status": "compatible", "confidence": "high",
         "price_fit": "entry", "price_distance": 350.0,
         "reasons": ["与推断卡口 FE-mount 匹配", "自动对焦单品适合视频/混合创作", "EVO 系列参与多样化候选",
                     "目录价 USD 300 · 价格带 entry", "已识别常用镜头品牌: sigma/viltrox"]},
    ]


def test_product_opportunities_limit_short_circuits_inside_first_pass() -> None:
    assert [item["sku"] for item in _rank(CONTEXT, limit=2)] == ["AF-35-P2", "AF-24-E1"]


def test_product_opportunities_lane_branches_and_cine_gate() -> None:
    photo = _rank(dict(CONTEXT, content_lane="photography"))
    assert [(i["sku"], i["score_breakdown"]["content"], i["recommendation_score"]) for i in photo] == [
        ("AF-35-P2", 12, 111), ("AF-24-E1", 12, 110), ("AF-24-A1", 12, 94), ("AF-50-E1", 12, 103),
    ]
    unknown = _rank(dict(CONTEXT, content_lane="unknown"))
    assert [(i["sku"], i["score_breakdown"]["content"], i["recommendation_score"]) for i in unknown] == [
        ("AF-35-P2", 5, 104), ("AF-24-E1", 5, 103), ("AF-24-A1", 5, 87), ("AF-50-E1", 5, 96),
    ]
    hybrid = _rank(dict(CONTEXT, content_lane="hybrid"))
    assert [(i["sku"], i["score_breakdown"]["content"], i["recommendation_score"]) for i in hybrid] == [
        ("AF-35-P2", 8, 107), ("AF-24-E1", 8, 106), ("CINE-85-T1", 12, 97),
        ("AF-24-A1", 8, 90), ("AF-50-E1", 8, 99),
    ]


def test_product_opportunities_ceiling_tiers_flip_series_scores_and_tiebreak() -> None:
    high = _rank(dict(CONTEXT, catalog_price_ceiling_proxy_usd=1500.0))
    assert [(i["sku"], i["score_breakdown"]["series"], i["price_fit"], i["recommendation_score"]) for i in high] == [
        ("AF-35-P2", 11, "within_band", 106), ("CINE-85-T1", 6, "within_band", 105),
        ("AF-24-E1", 9, "within_band", 104), ("AF-24-A1", 5, "price_unknown", 88),
        ("AF-50-E1", 9, "entry", 97),
    ]
    low = _rank(dict(CONTEXT, catalog_price_ceiling_proxy_usd=800.0))
    # tie-break flips at this ceiling: AF-35-P1 (distance 20) now beats AF-35-P2 (30)
    assert [(i["sku"], i["score_breakdown"]["series"], i["price_fit"], i["recommendation_score"]) for i in low] == [
        ("AF-50-E1", 12, "within_band", 107), ("AF-35-P1", 8, "within_band", 103),
        ("CINE-85-T1", 4, "stretch", 96), ("AF-24-A1", 11, "price_unknown", 94),
        ("AF-24-E1", 12, "stretch", 100),
    ]


def test_product_opportunities_evidence_and_confidence_degrade_without_gear() -> None:
    items = _rank(dict(CONTEXT, camera_body=None, lens_brands=[]))
    top = items[0]
    assert (top["sku"], top["confidence"], top["score_breakdown"]["evidence"]) == ("AF-35-P2", "medium", 0)
    assert top["reasons"] == [
        "与推断卡口 FE-mount 匹配", "自动对焦单品适合视频/混合创作", "PRO 系列参与多样化候选",
        "目录价 USD 550 · 价格带 within_band",
    ]


def test_product_opportunities_requires_mount_and_price_ceiling() -> None:
    assert _rank(dict(CONTEXT, mount=None)) == []
    assert _rank(dict(CONTEXT, catalog_price_ceiling_proxy_usd=None)) == []


# ── optimize_roster fixtures ───────────────────────────────────────────────

class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class FakeDb:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> _Rows:
        self.calls.append((sql, params))
        return _Rows(self.rows)


ROSTER_NOTE = (
    "去重触达为估算:折减用实测评论者 jaccard(commenter_jaccard_v0)优先、"
    "地理×平台相似度(geo_proxy_v0)兜底;创作者国别代理 ≠ 实测受众地理,诚实降档;"
    "受众地理缺失的候选按 KOL 独立 unknown 桶计,彼此不折减(缺数据不等于同受众),"
    "展示层统一折回 UNKNOWN。"
)
CALIBRATION = {
    "jaccard_to_dup_factor": 2.5, "overlap_discount_cap": 0.95,
    "proxy_same_platform": 0.3, "proxy_cross_platform": 0.1,
}


def _wire(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]],
          live: tuple[dict[tuple[int, int], float], str] = ({}, "")) -> tuple[FakeDb, list[list[int]]]:
    db = FakeDb(rows)
    budget_calls: list[list[int]] = []

    def stub_budget(entries: list[dict[str, Any]]) -> dict[str, Any]:
        budget_calls.append([e["kol_pool_id"] for e in entries])
        return {"status": "stubbed", "n": len(entries)}

    monkeypatch.setattr("app.db.connection.get_conn", lambda: db)
    monkeypatch.setattr(roster_optimizer, "_attach_rate_estimates", stub_budget)
    monkeypatch.setattr(roster_optimizer, "_live_pair_jaccard", lambda _db, _ids: live)
    return db, budget_calls


def test_optimize_roster_greedy_overlap_and_full_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live jaccard beats stored (max-merge), the discounted candidate loses round 2
    to a disjoint-unit candidate, and the payload reproduces the capture verbatim.
    """
    rows = [
        {"id": 1, "platform": "YouTube", "handle": "alpha", "display_name": "Alpha", "followers": 1000,
         "avg_views": 200, "country": "", "audience_estimated_json": json.dumps({
             "top_countries": [{"code": "US", "pct": 60}, {"code": "GB", "pct": 30}],
             "sample_size": 50,
             "overlap": {"items": [{"kind": "kol", "peer_id": 2, "jaccard": 0.1}]},
         })},
        {"id": 2, "platform": "youtube", "handle": "bravo", "display_name": "", "followers": 800,
         "avg_views": 0, "country": "美国", "audience_estimated_json": None},
        {"id": 3, "platform": "instagram", "handle": "charlie", "display_name": "Charlie", "followers": 0,
         "avg_views": 500, "country": "", "audience_estimated_json": "not-json"},
        {"id": 4, "platform": "youtube", "handle": "delta", "display_name": "Delta", "followers": 0,
         "avg_views": 0, "country": "德国", "audience_estimated_json": None},
    ]
    db, budget_calls = _wire(monkeypatch, rows, live=({(1, 2): 0.2}, "live-note-test"))

    out = roster_optimizer.optimize_roster([1, 2, 3, 4], max_size=2)

    assert db.calls[0] == (
        "SELECT id, platform, handle, display_name, followers, avg_views, country, "
        "audience_estimated_json FROM vkpi_kol_pool WHERE id IN (?,?,?,?)",
        (1, 2, 3, 4),
    )
    assert budget_calls == [[1, 3]]
    assert out == {
        "status": "ok",
        "method": "greedy_setcover_v0",
        "max_size": 2,
        "selected": [
            {"rank": 1, "kol_pool_id": 1, "handle": "alpha", "display_name": "Alpha",
             "platform": "youtube", "marginal_reach": 1000.0, "base_reach": 1000.0,
             "reach_basis": "followers", "geo_source": "audience_ensemble_v1",
             "geo_top": [("US", 60.0), ("GB", 30.0), ("OTHER", 10.0)],
             "reason": "首选:边际触达最大;新开覆盖单元 youtube×US / youtube×GB / youtube×OTHER"},
            {"rank": 2, "kol_pool_id": 3, "handle": "charlie", "display_name": "Charlie",
             "platform": "instagram", "marginal_reach": 500.0, "base_reach": 500.0,
             "reach_basis": "avg_views", "geo_source": "none",
             "geo_top": [("UNKNOWN", 100.0)],
             "reason": "新开覆盖单元 instagram×UNKNOWN;受众地理缺失,归 UNKNOWN 桶;触达基数用 avg_views 降级口径"},
        ],
        "dropped_overlap": [
            {"kol_pool_id": 2, "handle": "bravo", "platform": "youtube",
             "marginal_reach_if_added": 400.0,
             "top_overlaps": [{"with_kol_id": 1, "with_handle": "alpha",
                               "overlap_score": 0.5, "method": "commenter_jaccard_v0"}],
             "reason": "roster 已满,且边际增益低于已选成员"},
            {"kol_pool_id": 4, "handle": "delta", "platform": "youtube",
             "marginal_reach_if_added": 0.0, "top_overlaps": [],
             "reason": "无粉丝/播放触达基数,边际增益为 0(补数据后再评)"},
        ],
        "coverage": {
            "total_dedup_reach": 1500.0, "raw_reach_sum": 1500.0, "dedup_saved_pct": 0.0,
            "by_geo": [
                {"bucket": "US", "reach": 600.0, "pct": 40.0},
                {"bucket": "UNKNOWN", "reach": 500.0, "pct": 33.33},
                {"bucket": "GB", "reach": 300.0, "pct": 20.0},
                {"bucket": "OTHER", "reach": 100.0, "pct": 6.67},
            ],
            "by_platform": [
                {"platform": "youtube", "reach": 1000.0, "pct": 66.67},
                {"platform": "instagram", "reach": 500.0, "pct": 33.33},
            ],
            "unknown_geo_pct": 33.33,
        },
        "basis": {
            "candidates_in": 4, "candidates_found": 4, "missing_ids": [], "invalid_ids": [],
            "geo_source_counts": {"audience_ensemble_v1": 1, "creator_country_proxy": 2, "unknown": 1},
            "reach_basis_counts": {"followers": 2, "avg_views": 1, "none": 1},
            "overlap_pairs_measured": 1, "overlap_pairs_stored": 1, "overlap_pairs_live": 1,
            "live_jaccard_note": "live-note-test",
            "calibration": CALIBRATION,
        },
        "confidence": "low",
        "note": ROSTER_NOTE,
        "budget": {"status": "stubbed", "n": 2},
    }


def test_optimize_roster_tiebreak_prefers_smaller_id_and_proxy_discount(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"id": 10, "platform": "youtube", "handle": "t10", "display_name": "T10", "followers": 500,
         "avg_views": 0, "country": "美国", "audience_estimated_json": None},
        {"id": 11, "platform": "youtube", "handle": "t11", "display_name": "T11", "followers": 500,
         "avg_views": 0, "country": "美国", "audience_estimated_json": None},
    ]
    _wire(monkeypatch, rows)
    out = roster_optimizer.optimize_roster([11, 10], max_size=2)
    assert [(e["rank"], e["kol_pool_id"], e["marginal_reach"], e["reason"]) for e in out["selected"]] == [
        (1, 10, 500.0, "首选:边际触达最大;新开覆盖单元 youtube×US;地理为创作者国别代理(非实测受众)"),
        (2, 11, 350.0, "与已选 #10 重叠折减 30.0 个点(geo_proxy_v0);地理为创作者国别代理(非实测受众)"),
    ]
    assert out["coverage"] == {
        "total_dedup_reach": 850.0, "raw_reach_sum": 1000.0, "dedup_saved_pct": 15.0,
        "by_geo": [{"bucket": "US", "reach": 850.0, "pct": 100.0}],
        "by_platform": [{"platform": "youtube", "reach": 850.0, "pct": 100.0}],
        "unknown_geo_pct": 0.0,
    }
    assert out["dropped_overlap"] == []
    assert out["basis"]["overlap_pairs_measured"] == 0


def test_optimize_roster_no_reach_basis_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"id": 5, "platform": "tiktok", "handle": "echo", "display_name": "Echo", "followers": 0,
         "avg_views": 0, "country": "", "audience_estimated_json": None},
    ]
    _wire(monkeypatch, rows)
    out = roster_optimizer.optimize_roster([5])
    assert out == {
        "status": "no_reach_basis",
        "method": "greedy_setcover_v0",
        "max_size": 8,
        "selected": [],
        "dropped_overlap": [
            {"kol_pool_id": 5, "handle": "echo", "platform": "tiktok",
             "marginal_reach_if_added": 0.0, "top_overlaps": [],
             "reason": "无粉丝/播放触达基数,边际增益为 0(补数据后再评)"},
        ],
        "coverage": {
            "total_dedup_reach": 0, "raw_reach_sum": 0, "dedup_saved_pct": 0.0,
            "by_geo": [], "by_platform": [], "unknown_geo_pct": 0.0,
        },
        "basis": {
            "candidates_in": 1, "candidates_found": 1, "missing_ids": [], "invalid_ids": [],
            "geo_source_counts": {"audience_ensemble_v1": 0, "creator_country_proxy": 0, "unknown": 1},
            "reach_basis_counts": {"followers": 0, "avg_views": 0, "none": 1},
            "overlap_pairs_measured": 0, "overlap_pairs_stored": 0, "overlap_pairs_live": 0,
            "live_jaccard_note": "",
            "calibration": CALIBRATION,
        },
        "confidence": "low",
        "note": ROSTER_NOTE,
        "reason": "所有候选均无触达基数(followers/avg_views 皆缺),无法优化;补数据后再跑",
        "budget": {"status": "stubbed", "n": 0},
    }


def test_optimize_roster_degraded_paths_never_touch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_db() -> Any:
        raise AssertionError("degraded paths must not open a connection")

    monkeypatch.setattr("app.db.connection.get_conn", _no_db)
    assert roster_optimizer.optimize_roster([], max_size=3) == {
        "status": "empty", "method": "greedy_setcover_v0",
        "reason": "候选列表为空(或全部非法 id),无从优化",
        "invalid_ids": [], "selected": [], "dropped_overlap": [],
        "coverage": {"total_dedup_reach": 0, "by_geo": [], "by_platform": []},
        "confidence": "low",
    }
    # non-int values are reported; non-positive ints are silently dropped
    assert roster_optimizer.optimize_roster(["x", None, -3, 0], max_size=3) == {
        "status": "empty", "method": "greedy_setcover_v0",
        "reason": "候选列表为空(或全部非法 id),无从优化",
        "invalid_ids": ["x", None], "selected": [], "dropped_overlap": [],
        "coverage": {"total_dedup_reach": 0, "by_geo": [], "by_platform": []},
        "confidence": "low",
    }
    assert roster_optimizer.optimize_roster(list(range(1, 62))) == {
        "status": "too_many_candidates", "method": "greedy_setcover_v0",
        "reason": "候选 61 个超过上限 60,请先粗筛再进组合优化",
        "selected": [], "dropped_overlap": [],
        "coverage": {"total_dedup_reach": 0, "by_geo": [], "by_platform": []},
        "confidence": "low",
    }


def test_optimize_roster_missing_candidates_and_db_error_propagation(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, [])
    assert roster_optimizer.optimize_roster(["7", "x", 7, -3, None]) == {
        "status": "no_candidates", "method": "greedy_setcover_v0",
        "reason": "候选 id 在 vkpi_kol_pool 全部查无此人",
        "missing_ids": [7], "selected": [], "dropped_overlap": [],
        "coverage": {"total_dedup_reach": 0, "by_geo": [], "by_platform": []},
        "confidence": "low",
    }

    class BoomDb:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("boom")

    monkeypatch.setattr("app.db.connection.get_conn", lambda: BoomDb())
    with pytest.raises(RuntimeError, match="boom"):
        roster_optimizer.optimize_roster([7])


def test_cc52_shells_stay_simple_and_new_helpers_stay_bounded() -> None:
    """Refactor ratchet: shells <= 10, every helper in both files touched by this
    refactor <= 12 (pre-existing out-of-scope functions are pinned at their
    current ceilings so they cannot silently grow past them).
    """
    legacy_ceilings = {
        "focal_recommendations.py": {
            "creator_context": 18, "creator_price_profile": 16, "infer_creator_mount": 14,
        },
        "roster_optimizer.py": {
            "_attach_rate_estimates": 22, "_geo_distribution": 18, "_load_profiles": 15,
            "_live_pair_jaccard": 15, "_stored_pair_jaccard": 13,
        },
    }
    shells = {"focal_recommendations.py": "product_opportunities", "roster_optimizer.py": "optimize_roster"}
    for filename, shell_name in shells.items():
        path = ROOT / "backend/app/domains/kol" / filename
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) < 800
        rows = collect_complexity({str(path): ast.parse(source)})
        by_name = {row.qualified_name: row.cc for row in rows}
        assert by_name[shell_name] <= 10
        for name, cc in by_name.items():
            if "<lambda" in name:
                continue
            assert cc <= legacy_ceilings[filename].get(name, 12), f"{filename}:{name} cc={cc}"
