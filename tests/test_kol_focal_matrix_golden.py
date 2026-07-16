"""F1 件② golden:focal_matrix 胶片/等效负语境剔除 + 每视频焦段集合缓存。

fake conn 喂固定行,断言:
- "shot on 35mm film" / "35mm 胶片" / "50mm equivalent" / "等效50mm" 不算焦段覆盖;
- 正常镜头写法("viltrox 85mm f1.4")照常覆盖;同焦段有一处干净命中即算;
- 覆盖统计与家族匹配结果不因缓存重构而变(payload 结构键不变)。
"""
import json

from app.domains.kol import focal_matrix as fm


# ── 纯函数级:负语境剔除 ─────────────────────────────────────────────


def test_extract_focals_film_context_excluded():
    assert fm._extract_focals("shot on 35mm film - vintage look") == set()
    assert fm._extract_focals("经典35mm胶片质感") == set()
    assert fm._extract_focals("35 mm film photography basics") == set()


def test_extract_focals_equivalent_context_excluded():
    assert fm._extract_focals("gives a 40mm equivalent field of view") == set()
    assert fm._extract_focals("equivalent to 50mm on full frame") == set()
    assert fm._extract_focals("等效50mm 视角") == set()
    assert fm._extract_focals("50mm等效视角") == set()


def test_extract_focals_normal_lens_kept():
    assert fm._extract_focals("viltrox 85mm f1.4 lab portrait test") == {"85mm"}
    assert fm._extract_focals("viltrox af 35mm f1.8 review") == {"35mm"}
    # filmmaking 是一个词,不触发 film 负语境
    assert fm._extract_focals("35mm lens for filmmaking") == {"35mm"}
    # 前文正常提 film(非紧跟规格式写法)不误杀
    assert fm._extract_focals("my short film with the 85mm lens") == {"85mm"}


def test_extract_focals_one_clean_mention_wins():
    # 同焦段:一处胶片语境 + 一处干净命中 → 仍算覆盖
    blob = "35mm film emulation test, shot with viltrox 35mm f1.8"
    assert fm._extract_focals(blob) == {"35mm"}


# ── golden fixture:fake conn ────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for key, rows in self.routes.items():
            if key in sql:
                return _Result(rows)
        return _Result([])


def _product(sku, model_name, price, series="", mount=""):
    return {
        "sku": sku, "series": series, "category_main": "Lens",
        "category_detail": "AF Lens", "model_name": model_name,
        "marketing_name": model_name, "price_usd": price, "status": "official",
        "mount": mount, "product_url": f"https://example.com/{sku.lower()}",
    }


def _conn():
    return _Conn({
        "FROM vkpi_kol_pool": [{
            "id": 5, "handle": "focal_tester", "display_name": "Focal Tester", "platform": "youtube",
        }],
        "FROM vkpi_analysis_cache": [],
        "FROM vkpi_kol_video_evidence": [
            # 只提 35mm film(胶片语境)→ 不算 35mm 覆盖
            {"evidence_id": 11, "title": "Shot on 35mm FILM - vintage vibes", "view_count": 900, "is_active": 1},
            # 干净的 85mm LAB + viltrox → 覆盖 + 家族命中
            {"evidence_id": 12, "title": "Viltrox 85mm f1.4 LAB portrait test", "view_count": 500, "is_active": 1},
        ],
        "FROM vkpi_products": [
            _product("AF-35MM-F18-FE", "AF 35/1.8 FE", 380.0),
            _product("AF-85MM-F14-LAB-FE", "AF 85/1.4 LAB FE", 999.0, series="LAB"),
        ],
    })


def test_focal_matrix_film_context_not_covered_golden():
    payload = fm.focal_matrix(5, conn=_conn())

    assert payload["status"] == "ready"
    cells = {c["focal"]: c for c in payload["matrix"]["focals"]}

    # 85mm:干净命中 → covered
    assert cells["85mm"]["covered"] is True
    assert cells["85mm"]["video_count"] == 1
    assert cells["85mm"]["in_catalog"] is True

    # 35mm:只有胶片语境命中 → 不算覆盖,反而进 gaps(目录有 SKU 零覆盖)
    assert cells["35mm"]["covered"] is False
    assert cells["35mm"]["video_count"] == 0
    gap_focals = [g["focal"] for g in payload["gaps"]["items"]]
    assert "35mm" in gap_focals
    assert "85mm" not in gap_focals

    # 家族命中:85mm + LAB 系列词 + viltrox 语境三条齐
    matched = payload["matched_products"]
    assert matched["status"] == "ready"
    assert any(item["focal"] == "85mm" for item in matched["items"])
    assert not any(item["focal"] == "35mm" for item in matched["items"])

    # 结构键不变(industry_benchmark / category_tracks / launch_assembly 在吃)
    for key in ("kol", "basis", "matrix", "covered", "gaps", "matched_products", "generated_at", "note"):
        assert key in payload
    assert payload["basis"]["method"] == "lexicon_focal_v1"
    cell = cells["85mm"]
    for key in ("focal", "mm", "in_catalog", "covered", "video_count", "avg_views",
                "title_hits", "deep_hits", "top_example", "catalog"):
        assert key in cell


def test_product_opportunity_uses_mount_price_and_series_diversity_not_epic_total():
    conn = _Conn({
        "FROM vkpi_kol_pool": [{
            "id": 9,
            "handle": "sony_filmmaker",
            "display_name": "Sony Filmmaker",
            "platform": "tiktok",
            "bio": "Sony FX3 filmmaker and camera rig breakdowns",
            "followers": 728_000,
            "avg_views": 349_900,
        }],
        "FROM vkpi_analysis_cache": [],
        "FROM vkpi_kol_video_evidence": [{
            "evidence_id": 91,
            "title": "Sony FX3 camera rig breakdown for cinematic color grading",
            "view_count": 349_900,
            "is_active": 1,
        }],
        "FROM vkpi_products": [
            # 多焦段高价套装:旧算法会把 25.8K 重复灌到每个焦段;v2 必须排除。
            {
                **_product(
                    "EPIC-25MM-35MM-50MM-75MM-100MM-PL-SET",
                    "EPIC 25mm/35mm/50mm/75mm/100mm PL Cine Lens Set",
                    25_800,
                    series="Cine",
                    mount="L-mount",
                ),
                "category_detail": "Cine Lens",
            },
            # 同焦段错误卡口:即使价格/系列一样也必须硬排除。
            _product("AF-35MM-F18-EVO-Z", "AF 35mm F1.8 EVO Nikon Z", 395, series="EVO", mount="Z-mount"),
            _product("AF-35MM-F18-EVO-FE", "AF 35mm F1.8 EVO Sony E", 395, series="EVO", mount="FE-mount"),
            _product("AF-50MM-F14-PRO-FE", "AF 50mm F1.4 Pro Sony E", 549, series="Pro", mount="FE-mount"),
            _product("AF-135MM-F18-LAB-FE", "AF 135mm F1.8 LAB Sony E", 899, series="LAB", mount="FE-mount"),
            _product("AF-15MM-F17-AIR-FE", "AF 15mm F1.7 Air Sony E", 239, series="Air", mount="FE-mount"),
            {
                **_product("MF-20MM-T20-CINE-FE", "MF 20mm T2.0 Cine Sony E", 499, series="Cine", mount="FE-mount"),
                "category_detail": "Cine Lens",
            },
        ],
    })

    payload = fm.focal_matrix(9, conn=conn)
    context = payload["gaps"]["creator_context"]
    items = payload["gaps"]["recommendations"][:5]
    skus = [item["sku"] for item in items]
    series = [item["series"][0] for item in items]

    assert context["camera_body"].lower() == "sony fx3"
    assert context["mount"] == "FE-mount"
    assert context["content_lane"] == "cinema"
    assert "EPIC-25MM-35MM-50MM-75MM-100MM-PL-SET" not in skus
    assert "AF-35MM-F18-EVO-Z" not in skus
    assert all(item["mount"] == "FE-mount" for item in items)
    assert {"EVO", "Pro", "LAB", "Air", "Cine"} <= set(series)
    assert len(series) == len(set(series))
    assert all(item["compatibility_status"] == "compatible" for item in items)
    assert all(item["reasons"] and item["score_breakdown"] for item in items)
    assert payload["basis"]["opportunity_method"] == "mount_content_price_series_v2"


def test_product_opportunity_unknown_mount_does_not_emit_fake_top1():
    products = [
        _product("AF-35MM-F18-EVO-FE", "AF 35mm F1.8 EVO Sony E", 395, series="EVO", mount="FE-mount"),
    ]
    context = fm._creator_context({"followers": 20_000}, [{"title_blob": "lens review", "deep_blob": ""}])
    items = fm._product_opportunities(products, {"35mm"}, context)

    assert context["mount_status"] == "unknown"
    assert context["recommendation_status"] == "insufficient_evidence"
    assert items == []


def test_explicit_camera_body_wins_over_competitor_mentions():
    mount, evidence, status = fm._infer_creator_mount(
        "comparison with nikon z8 and fujifilm x-h2",
        "Sony A7 IV",
    )
    assert mount == "FE-mount"
    assert status == "inferred_from_camera"
    assert "Sony" in evidence


def test_conflicting_text_mounts_stop_recommendation_when_camera_unknown():
    mount, evidence, status = fm._infer_creator_mount(
        "Sony versus Nikon versus Fujifilm camera comparison",
        "",
    )
    assert mount == ""
    assert status == "conflict"
    assert "多个卡口" in evidence


def test_compact_camera_models_and_red_komodo_mounts_are_not_misclassified():
    assert fm._infer_creator_mount("mentions PL adapter", "sonyfx9")[0] == "FE-mount"
    assert fm._infer_creator_mount("compares Nikon", "fujifilmxh2")[0] == "X-mount"
    assert fm._infer_creator_mount("cine camera", "RED Komodo")[0] == "RF-mount"
