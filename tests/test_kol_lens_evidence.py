from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.routers import vkpi_lens_insights as router_mod  # noqa: E402
from app.domains.kol import lens_evidence as le  # noqa: E402
from app.domains.kol import lens_evidence_store as store  # noqa: E402


CATALOG = [
    {"sku": "AF-75MM-F18-EVO-FE", "model_name": "Viltrox AF 75mm F1.8 EVO Full-Frame Lens for Sony E-Mount", "marketing_name": "", "category_main": "Lens", "series": "EVO", "mount": "FE-mount"},
    {"sku": "AF-75MM-F18-EVO-Z", "model_name": "Viltrox AF 75mm F1.8 EVO Full-Frame Lens for Nikon Z-Mount", "marketing_name": "", "category_main": "Lens", "series": "EVO", "mount": "Z-mount"},
    {"sku": "AF-135MM-F18-LAB-FE", "model_name": "Viltrox AF 135mm F1.8 LAB Full-Frame Lens for Sony E-Mount", "marketing_name": "", "category_main": "Lens", "series": "LAB", "mount": "FE-mount"},
    {"sku": "AF-135MM-F18-LAB-Z", "model_name": "Viltrox AF 135mm F1.8 LAB Full-Frame Lens for Nikon Z-mount", "marketing_name": "", "category_main": "Lens", "series": "LAB", "mount": "Z-mount"},
    {"sku": "AF-28MM-F4-5-FE", "model_name": "Viltrox AF 28mm F4.5 Full-Frame Lens for Sony E-Mount", "marketing_name": "", "category_main": "Lens", "series": "", "mount": "FE-mount"},
    {"sku": "AF-28MM-F4-5-CHIP-L", "model_name": "Viltrox AF 28mm F4.5 Chip Full-Frame Lens for Leica L-Mount", "marketing_name": "", "category_main": "Lens", "series": "", "mount": "L-mount"},
    {"sku": "AF-35MM-F12-LAB-FE", "model_name": "Viltrox AF 35mm F1.2 LAB Full-Frame Lens for Sony E-Mount", "marketing_name": "", "category_main": "Lens", "series": "LAB", "mount": "FE-mount"},
    {"sku": "VL-LEN072", "model_name": "AF 35/1.2 FE", "marketing_name": "", "category_main": "Lens", "series": "", "mount": ""},
    {"sku": "DC-A1-2800-NITS-7-INCH-CAMERA-MONITOR", "model_name": "Viltrox DC-A1 2800 Nits 7-Inch Camera Monitor", "marketing_name": "", "category_main": "Monitor", "series": "", "mount": ""},
    {"sku": "VL-MON015", "model_name": "DC-A1", "marketing_name": "", "category_main": "Monitor", "series": "", "mount": ""},
    {"sku": "VINTAGE-Z2-MINI-TTL-ON-CAMERA-FLASH", "model_name": "Vintage Z2 Mini TTL On-Camera Flash", "marketing_name": "", "category_main": "Lighting", "series": "", "mount": ""},
    {"sku": "VL-LIT078", "model_name": "Vintage Z2 S", "marketing_name": "", "category_main": "Lighting/Flash", "series": "", "mount": ""},
    {"sku": "EPIC-50MM-T2-0-1-33X-PL-ANAMORPHIC-CINE-L", "model_name": "Viltrox EPIC 50mm T2.0 1.33X PL Full-Frame Anamorphic Cine Lens", "marketing_name": "", "category_main": "Lens", "series": "EPIC", "mount": "L-mount"},
    {"sku": "AF-50MM-F20-AIR-FE", "model_name": "Viltrox AF 50mm F2.0 Air Full-Frame Lens for Sony E-Mount", "marketing_name": "", "category_main": "Lens", "series": "Air", "mount": "FE-mount"},
]


def _index() -> le.CatalogIndex:
    return le.CatalogIndex(CATALOG, aliases=None)


def _result(**layer1: object) -> dict:
    return {
        "schema_version": "video_analysis_final_v1",
        "layer1_visual_content": {
            "content_summary": "博主用 Viltrox AF 75mm F1.8 EVO 拍人像。",
            "product_presence": "Viltrox AF 75mm F1.8 EVO 镜头在视频开头有明确的特写。标题中也明确提及了该镜头型号。",
            "brand_exposure": "无其他露出。",
            "scene_timeline": [{"timestamp": "00:10", "what": "口播介绍 Viltrox 135mm F1.8 LAB FE 的对焦。"}],
            **layer1,
        },
        "layer4_attribution": {"product_contribution": "Viltrox 75mm F1.8 EVO 贡献了焦外。"},
        "layer6_flags_and_scores": {"scores": {"product_proof_score": {"evidence": "Sony 85mm GM II 作对比。"}}},
        "raw_gemini_video": {"content_topic": "唯卓仕 DC-A1 监视器开箱。", "viltrox_products_all": []},
    }


@pytest.mark.parametrize(
    ("mention", "resolution", "sku", "display"),
    [
        ("AF 75mm F1.8 EVO", "family", None, "AF 75mm F1.8 EVO"),
        ("AF 135mm F1.8 LAB FE", "sku", "AF-135MM-F18-LAB-FE", "AF 135mm F1.8 LAB"),
        ("135mm f/1.8 LAB for Nikon Z-mount", "sku", "AF-135MM-F18-LAB-Z", "AF 135mm F1.8 LAB"),
        ("28mm F4.5", "sku", "AF-28MM-F4-5-FE", "AF 28mm F4.5"),
        ("AF 35/1.2", "sku", "AF-35MM-F12-LAB-FE", "AF 35mm F1.2 LAB"),
        ("Vintage Z2", "sku", "VINTAGE-Z2-MINI-TTL-ON-CAMERA-FLASH", "Vintage Z2"),
        ("DC-A1", "sku", "DC-A1-2800-NITS-7-INCH-CAMERA-MONITOR", "DC-A1"),
        ("AF 50mm F2", "sku", "AF-50MM-F20-AIR-FE", "AF 50mm F2.0 Air"),
        ("50mm T2.0", "sku", "EPIC-50MM-T2-0-1-33X-PL-ANAMORPHIC-CINE-L", "EPIC 50mm T2.0 1.33X PL Anamorphic Cine"),
        ("AF 24mm F1.8 E", "unresolved", None, "AF 24mm F1.8 E"),
        ("85mm", "unresolved", None, "85mm"),
    ],
)
def test_catalog_resolution_never_invents_a_sku(mention, resolution, sku, display) -> None:
    outcome = _index().resolve(le.canonical_text(mention))
    assert outcome["resolution"] == resolution
    assert outcome["product_sku"] == sku
    assert outcome["display_name"] == display


def test_extraction_clips_model_tokens_and_merges_modalities() -> None:
    rows = le.extract_resolved(_result(), _index())
    by_name = {row["display_name"]: row for row in rows}

    evo = by_name["AF 75mm F1.8 EVO"]
    assert evo["resolution"] == "family"
    assert set(evo["modalities"]) == {"visual", "text"}
    assert {"product_presence", "content_summary", "product_contribution"} <= set(evo["source_fields"])
    assert evo["mention_count"] == 3
    lab = by_name["AF 135mm F1.8 LAB"]
    assert lab["resolution"] == "sku" and lab["product_sku"] == "AF-135MM-F18-LAB-FE"
    assert lab["modalities"] == ["voice"]
    assert by_name["DC-A1"]["resolution"] == "sku"
    # 竞品(Sony 85mm GM II)绝不被当成 Viltrox 提及
    assert not any("85mm" in row["mention_text"] for row in rows)


def test_extraction_accepts_json_string_and_skips_brand_only_text() -> None:
    payload = json.dumps(_result(product_presence="无Viltrox产品出现。", content_summary="Viltrox 没有出现。"), ensure_ascii=False)
    rows = le.extract_resolved(payload, _index())
    names = {row["display_name"] for row in rows}
    assert "AF 75mm F1.8 EVO" in names  # product_contribution 仍点名
    assert le.extract_resolved({"layer1_visual_content": {"product_presence": "无 Viltrox 产品。"}}, _index()) == []


def test_partial_mentions_merge_into_resolved_rows_in_same_result() -> None:
    result = _result(product_presence="Viltrox AF 75mm F1.8 EVO 出镜;Viltrox 75mm 字幕标注。")
    rows = le.extract_resolved(result, _index())
    assert [row["display_name"] for row in rows if row["resolution"] == "unresolved"] == []
    evo = next(row for row in rows if row["display_name"] == "AF 75mm F1.8 EVO")
    assert "text" in evo["modalities"]


# ── 落表 + 聚合(sqlite 镜像) ──────────────────────────────────────────────


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, duplicate_of_id INTEGER, display_name TEXT, handle TEXT);
        CREATE TABLE vkpi_kol_pool_favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, kol_pool_id INTEGER, staff_id INTEGER);
        CREATE TABLE vkpi_kol_pool_members (id INTEGER PRIMARY KEY AUTOINCREMENT, kol_pool_id INTEGER, staff_id INTEGER);
        CREATE TABLE vkpi_products (sku TEXT PRIMARY KEY, model_name TEXT, marketing_name TEXT, category_main TEXT, series TEXT, mount TEXT, specs_json TEXT, fit_tags_json TEXT, product_url TEXT);
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY, kol_pool_id INTEGER NOT NULL, content_url TEXT NOT NULL, platform TEXT,
            title TEXT, video_title TEXT, view_count INTEGER, is_active INTEGER DEFAULT 1
        );
        CREATE TABLE vkpi_analysis_cache (
            id INTEGER PRIMARY KEY, target_type TEXT, target_id TEXT, derive_method TEXT, result TEXT,
            status TEXT DEFAULT 'ready', updated_at TEXT
        );
        CREATE TABLE vkpi_kol_lens_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT, cache_id INTEGER NOT NULL, evidence_id INTEGER, kol_pool_id INTEGER,
            mention_text TEXT NOT NULL, mention_norm TEXT NOT NULL, resolution TEXT NOT NULL, product_sku TEXT,
            lens_key TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL DEFAULT '', category_main TEXT NOT NULL DEFAULT '',
            candidate_skus TEXT NOT NULL DEFAULT '[]', modalities TEXT NOT NULL DEFAULT '[]', source_fields TEXT NOT NULL DEFAULT '[]',
            mention_count INTEGER NOT NULL DEFAULT 1, extractor_version TEXT NOT NULL DEFAULT '',
            created_at TEXT, updated_at TEXT, UNIQUE (cache_id, mention_norm)
        );
        CREATE TABLE vkpi_kol_lens_evidence_scan (
            cache_id INTEGER PRIMARY KEY, evidence_id INTEGER, kol_pool_id INTEGER, extractor_version TEXT NOT NULL DEFAULT '',
            cache_updated_at TEXT, mention_rows INTEGER NOT NULL DEFAULT 0, scan_status TEXT NOT NULL DEFAULT 'scanned', scanned_at TEXT
        );
        INSERT INTO vkpi_kol_pool VALUES (9, NULL, 'Creator Nine', 'nine'), (10, NULL, 'Creator Ten', 'ten');
        INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id) VALUES (9, 10), (10, 20);
        INSERT INTO vkpi_kol_video_evidence VALUES
            (41, 9, 'https://www.youtube.com/watch?v=a', 'youtube', 'Portrait with EVO', 'Portrait with EVO', 1000, 1),
            (42, 9, 'https://www.youtube.com/watch?v=b', 'youtube', 'No products', 'No products', NULL, 1),
            (43, 10, 'https://www.youtube.com/watch?v=c', 'youtube', 'Other staff LAB', 'Other staff LAB', 5000, 1);
        """
    )
    for product in CATALOG:
        conn.execute(
            "INSERT INTO vkpi_products (sku, model_name, marketing_name, category_main, series, mount, specs_json, fit_tags_json, product_url) VALUES (?, ?, ?, ?, ?, ?, '{}', '[]', '')",
            (product["sku"], product["model_name"], product["marketing_name"], product["category_main"], product["series"], product["mount"]),
        )
    results = {
        41: _result(),
        42: _result(product_presence="无 Viltrox 产品。", content_summary="猫咪视频。", scene_timeline=[]) | {"layer4_attribution": {}, "raw_gemini_video": {}},
        43: _result(product_presence="Viltrox AF 135mm F1.8 LAB FE 字幕标注。", content_summary="", scene_timeline=[]) | {"layer4_attribution": {}, "raw_gemini_video": {}},
    }
    for cache_id, (evidence_id, result) in enumerate(results.items(), start=500):
        conn.execute(
            "INSERT INTO vkpi_analysis_cache (id, target_type, target_id, derive_method, result, status, updated_at) VALUES (?, 'video', ?, 'video_analysis_final_v1', ?, 'ready', '2026-08-20T00:00:00Z')",
            (cache_id, str(evidence_id), json.dumps(result, ensure_ascii=False)),
        )
    conn.execute(
        "INSERT INTO vkpi_analysis_cache (id, target_type, target_id, derive_method, result, status, updated_at) VALUES (600, 'cn_platform_video', 'bilibili:BV1', 'video_analysis_final_v1', ?, 'ready', '2026-08-20T00:00:00Z')",
        (json.dumps(_result(), ensure_ascii=False),),
    )
    return conn


def test_backfill_is_dry_run_by_default_and_idempotent_when_applied() -> None:
    conn = _conn()

    dry = store.backfill_lens_evidence(conn, apply=False)
    assert dry["dry_run"] is True and dry["written_rows"] == 0
    assert conn.execute("SELECT COUNT(*) FROM vkpi_kol_lens_evidence").fetchone()[0] == 0
    assert dry["cache_rows_considered"] == 4 and dry["cache_rows_with_evidence"] == 3
    assert dry["by_resolution"]["unresolved"] == 0 and dry["unresolved_pct"] == 0.0

    applied = store.backfill_lens_evidence(conn, apply=True)
    rows = conn.execute("SELECT cache_id, evidence_id, kol_pool_id, resolution, display_name FROM vkpi_kol_lens_evidence ORDER BY id").fetchall()
    assert applied["written_rows"] == len(rows) > 0
    assert {r["scan_status"] for r in conn.execute("SELECT scan_status FROM vkpi_kol_lens_evidence_scan").fetchall()} == {"scanned", "empty_result", "no_evidence"}
    # no_evidence 行(B 站缓存无 evidence 归属)抽出的提及 kol_pool_id 为空,不会混进收藏集聚合
    assert all(r["kol_pool_id"] is None for r in rows if r["cache_id"] == 600)

    again = store.backfill_lens_evidence(conn, apply=True)
    assert again["cache_rows_considered"] == 0 and again["written_rows"] == 0
    assert conn.execute("SELECT COUNT(*) FROM vkpi_kol_lens_evidence").fetchone()[0] == len(rows)

    forced = store.backfill_lens_evidence(conn, apply=True, force=True)
    assert forced["cache_rows_considered"] == 4
    assert conn.execute("SELECT COUNT(*) FROM vkpi_kol_lens_evidence").fetchone()[0] == len(rows)


def test_summary_groups_by_lens_with_scope_and_honest_views() -> None:
    conn = _conn()
    store.backfill_lens_evidence(conn, apply=True)

    own = store.lens_summary(conn, staff_scope_id=10)
    assert own["scope"]["mode"] == "staff_collection"
    assert own["coverage"] == {"analysed_videos": 2, "scanned_videos": 2, "videos_with_products": 1, "videos_without_products": 1, "unscanned_videos": 0}
    names = {lens["display_name"]: lens for lens in own["lenses"]}
    assert set(names) == {"AF 75mm F1.8 EVO", "AF 135mm F1.8 LAB", "DC-A1"}
    evo = names["AF 75mm F1.8 EVO"]
    assert evo["videos"] == 1 and evo["kols"] == 1 and evo["views_total"] == 1000 and evo["views_measured_videos"] == 1
    assert evo["modalities"]["visual"] == 1 and evo["modalities"]["text"] == 1
    assert evo["samples"][0]["evidence_id"] == 41 and evo["samples"][0]["kol_name"] == "Creator Nine"
    assert evo["samples"][0]["cache_id"] == 500 and evo["samples"][0]["v_relevance"] == "confirmed"
    assert evo["v_relevance"] == "confirmed" and evo["v_relevance_rows"] == {"confirmed": 1, "likely": 0}
    assert own["summary"]["kols_with_products"] == 1
    # v_relevance 三态投影:41 号视频 confirmed;42 号扫过零提及 = none
    assert own["summary"]["v_relevance_videos"] == {"confirmed": 1, "likely": 0, "none": 1}
    assert own["v_relevance_labels"]["confirmed"] == "确认出镜"

    team = store.lens_summary(conn, staff_scope_id=None)
    lab = {lens["display_name"]: lens for lens in team["lenses"]}["AF 135mm F1.8 LAB"]
    assert lab["videos"] == 2 and lab["kols"] == 2 and lab["views_total"] == 6000
    assert lab["resolution"] == "sku" and lab["skus"] == ["AF-135MM-F18-LAB-FE"]
    assert team["coverage"]["analysed_videos"] == 3

    everything = store.lens_summary(conn, staff_scope_id=None, scope_all=True)
    assert everything["scope"]["mode"] == "all_analysed"
    assert everything["coverage"]["analysed_videos"] == 4 and everything["coverage"]["scanned_videos"] == 4


def test_kol_lenses_and_empty_states() -> None:
    conn = _conn()
    store.backfill_lens_evidence(conn, apply=True)

    nine = store.kol_lenses(conn, kol_pool_id=9)
    assert [lens["display_name"] for lens in nine["lenses"]][:1] == ["AF 75mm F1.8 EVO"] or len(nine["lenses"]) == 3
    assert nine["coverage"] == {"analysed_videos": 2, "scanned_videos": 2, "videos_with_products": 1, "videos_without_products": 1, "unscanned_videos": 0}
    assert nine["empty_reason"] is None
    assert [v["evidence_id"] for v in nine["videos"]] == [41]
    assert nine["videos"][0]["v_relevance"] == "confirmed" and nine["videos"][0]["cache_id"] == 500
    assert "AF 75mm F1.8 EVO" in nine["videos"][0]["lenses"]
    assert nine["v_relevance_videos"] == {"confirmed": 1, "likely": 0, "none": 1}
    relevance = store.evidence_relevance(conn, evidence_ids=[41, 42, 43, 999])
    assert relevance[41]["v_relevance"] == "confirmed" and relevance[42]["v_relevance"] == "none"
    assert relevance[43]["v_relevance"] == "confirmed" and 999 not in relevance

    conn.execute("DELETE FROM vkpi_kol_lens_evidence WHERE kol_pool_id=10")
    ten = store.kol_lenses(conn, kol_pool_id=10)
    assert ten["lenses"] == [] and ten["empty_reason"] == "no_lens_evidence"
    conn.execute("DELETE FROM vkpi_analysis_cache WHERE target_id='43'")
    assert store.kol_lenses(conn, kol_pool_id=10)["empty_reason"] == "no_analysed_videos"


def test_router_scope_gates(monkeypatch) -> None:
    conn = _conn()
    store.backfill_lens_evidence(conn, apply=True)
    monkeypatch.setattr(router_mod, "get_conn", lambda: conn)
    monkeypatch.setattr(router_mod, "table_exists", lambda name: True)

    body = router_mod.lens_insights_summary_endpoint(scope_mode="collection", staff_id=20, limit=10, staff={"id": 10, "role": "member"})
    assert body["scope"]["staff_scope_id"] == 10
    with pytest.raises(HTTPException) as error:
        router_mod.lens_insights_summary_endpoint(scope_mode="all", staff_id=None, limit=10, staff={"id": 10, "role": "member"})
    assert error.value.status_code == 403
    manager = router_mod.lens_insights_summary_endpoint(scope_mode="all", staff_id=None, limit=10, staff={"id": 1, "role": "owner", "is_owner": 1})
    assert manager["scope"]["mode"] == "all_analysed"

    assert router_mod.lens_insights_kol_endpoint(kol_pool_id=9, staff={"id": 10, "role": "member"})["kol_pool_id"] == 9
    with pytest.raises(HTTPException) as forbidden:
        router_mod.lens_insights_kol_endpoint(kol_pool_id=10, staff={"id": 10, "role": "member"})
    assert forbidden.value.status_code == 403

    monkeypatch.setattr(router_mod, "table_exists", lambda name: False)
    with pytest.raises(HTTPException) as missing:
        router_mod.lens_insights_kol_endpoint(kol_pool_id=9, staff={"id": 10, "role": "member"})
    assert missing.value.status_code == 503


def test_explain_and_trace_replay_roundtrip() -> None:
    conn = _conn()
    traces = store.explain_cache_rows(conn, cache_ids=[500, 501])
    by_id = {t["cache_id"]: t for t in traces}
    assert by_id[500]["video_v_relevance"] == "confirmed" and by_id[501]["video_v_relevance"] == "none"
    assert by_id[500]["ledger"]["scan_status"] is None
    assert any(a["body"] == "AF 75mm F1.8 EVO" for a in by_id[500]["anchors"])
    records = store.export_trace(conn, limit=10)
    assert len(records) == 4 and all(r["texts"] for r in records)
    replay = store.replay_trace(records, _index())
    direct = store.backfill_lens_evidence(conn, apply=False)
    assert replay["mode"] == "replay" and replay["would_write_rows"] == direct["mention_rows"]
    assert replay["by_resolution"] == direct["by_resolution"]
    assert replay["scan_transitions"] == {"unscanned->empty_result": 1, "unscanned->scanned": 3}
    assert replay["videos_by_v_relevance"] == direct["videos_by_v_relevance"] == {"confirmed": 3, "likely": 0, "none": 1}
    diff = store.diff_traces(traces, traces)
    assert diff["same_content"] == 2 and diff["same_rows"] == 2 and diff["differing"] == []


def test_migration_287_pair_and_router_registry() -> None:
    up = (ROOT / "migrations/287_vkpi_kol_lens_evidence.sql").read_text()
    down = (ROOT / "migrations/287_vkpi_kol_lens_evidence_down.sql").read_text()
    assert "vkpi_kol_lens_evidence" in up and "vkpi_kol_lens_evidence_scan" in up
    assert "?" not in up and "?" not in down
    assert "DROP TABLE IF EXISTS vkpi_kol_lens_evidence" in down
    from app.api.routers import ADMIN_ROUTER_MODULES

    assert "vkpi_lens_insights" in ADMIN_ROUTER_MODULES
