"""KOL 焦段矩阵(focal_matrix v1)—— 「这个 KOL 拍过哪些焦段/产品线,哪块是空白」。

三块输出(全部纯聚合已有数据,零新采集、零 LLM、零写库):
  covered           焦段覆盖:从 evidence 标题 + final_v1 深析文本里用正则/词表提焦段
                    (9mm…135mm)→ 每焦段视频数 + 均播放 + 最佳例子;
  gaps              空白焦段:vkpi_products 目录里有 SKU 但该 KOL 零覆盖的焦段;
                    适配产品按机身/卡口、创作类型、价格带与系列多样性排序,
                    高价多焦段套装不再重复灌入每个焦段;
  matched_products  命中的我方 SKU 家族:焦段 + 系列词(LAB/Pro/Air/EVO)/光圈 + viltrox
                    语境三重词表匹配,跨卡口去重成家族。

产品线口径:按 vkpi_products 的 category/SKU 前缀聚类成 9 条线(AF 镜头/手动镜头/
电影镜/灯具/转接/滤镜/监视器/电池/配件);视频侧按词表判「内容覆盖」——
覆盖≠用的是我方产品(matched_products 才是我方命中),注释与响应 note 里都明说。

诚实态:每块缺数据返回 {status:"empty", reason:...},识别是词表/正则规则
(method=lexicon_focal_v1),识别不了就不算,绝不杜撰;价格带只是目录分层代理,注明口径。
负语境剔除:焦段词命中但邻近是胶片/等效语境("shot on 35mm film"、"35mm 胶片"、
"50mm equivalent"、"等效50mm")时不算覆盖 —— 那说的是底片规格或换算视角,不是这支镜头。

数据源(全只读):vkpi_kol_pool / vkpi_kol_video_evidence / vkpi_analysis_cache
(derive_method='video_analysis_final_v1', status='ready') / vkpi_products。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(不用 LIKE);BOOLEAN 读回
可能是 int 1/0,判断走 _truthy;JSONB 双态(dict/str)走 _loads。函数内懒 import get_conn。
红线:纯读聚合,绝不写库;零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"

# 焦段提取:'85mm' / '85 mm' / '85.5mm';前置负回顾避免 '85.5mm' 里再抠出 '5mm'。
_FOCAL_MM_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d)?)\s*mm", re.IGNORECASE)
# 负语境剔除:焦段数字命中但邻近是胶片规格/等效换算语境时,说的不是这支镜头的焦段,
# 不算覆盖 —— "shot on 35mm film" / "35mm 胶片" 是底片规格,"50mm equivalent" /
# "等效50mm" 是换算视角。film/胶片 只认紧跟焦段词之后的规格式写法(避免误伤
# "short film with 85mm" 这类前文提 film 的正常镜头内容);equivalent/等效 前后都查。
_FILM_AFTER_RE = re.compile(r"^[\s\-]*(?:film(?![a-z0-9])|胶片)", re.IGNORECASE)
_EQUIV_RE = re.compile(r"(?<![a-z0-9])equiv(?:alents?)?(?![a-z0-9])|等效", re.IGNORECASE)
_NEG_BEFORE, _NEG_AFTER = 16, 24
# 变焦段:'24-70mm'(目录暂无变焦 SKU,只如实记录提及,不进焦段格子)。
_ZOOM_RE = re.compile(r"(?<![\d.])(\d{1,3})\s*-\s*(\d{1,3})\s*mm", re.IGNORECASE)
# 型号斜杠写法:'AF 135/1.8 FE' → 135(只用于产品目录解析,不用于视频文本)。
_MODEL_SLASH_RE = re.compile(r"(?<![\d.])(\d{2,3})/(?=[\d.])")
# SKU 光圈 token:AF-85MM-F14-PRO-FE → '14';AF-28MM-F4-5-FE → '4-5'。
_SKU_APERTURE_RE = re.compile(r"-[FT](\d+(?:-\d+)?)(?:-|$)")

_FOCAL_MIN, _FOCAL_MAX = 6.0, 800.0  # 合理焦段范围外的数字一律不算(如 0.5mm 滤镜厚度)

# ── 产品线聚类(key, 中文标签, 视频文本词表;词表判「内容覆盖」非「用我方产品」)──
PRODUCT_LINES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("af_lens", "AF 自动对焦镜头", (
        "lens", "prime", "bokeh", "autofocus", "定焦", "镜头", "自动对焦", "虚化",
    )),
    ("mf_lens", "手动/特种镜头", (
        "manual focus", "manual lens", "手动对焦", "手动镜头", "移轴", "鱼眼",
    )),
    ("cine", "电影镜头", (
        "cine lens", "cinema lens", "anamorphic", "变形宽银幕", "电影镜", "epic",
    )),
    ("lighting", "灯具/闪光", (
        "light", "lighting", "flash", "strobe", "softbox", "led", "rgb",
        "灯", "布光", "闪光", "补光",
    )),
    ("adapter", "转接环/增距", (
        "adapter", "speed booster", "speedbooster", "teleconverter", "转接", "增距",
    )),
    ("filter", "滤镜", ("filter", "滤镜",)),
    ("monitor", "监视器", ("monitor", "监视器",)),
    ("battery", "电池/供电", ("battery", "v-mount", "电池",)),
    ("accessory", "配件", ("hdmi cable", "lens hood", "extension tube", "遮光罩", "近摄接圈",)),
)
_LINE_LABEL = {key: label for key, label, _ in PRODUCT_LINES}

# 我方镜头系列词(matched_products 用;系列缺失的家族退回光圈词匹配)
_SERIES_TOKENS = ("lab", "pro", "air", "evo", "epic")


# ── 小工具(与 signature_profile 同款容错约定)───────────────────────


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _loads(value: Any) -> Any:
    """JSONB 经 compat 层可能回 dict 也可能回 str,双态容错。"""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _text(value: Any, limit: int = 300) -> str:
    if isinstance(value, dict):
        value = " ".join(str(v) for v in value.values() if isinstance(v, (str, int, float)))
    elif isinstance(value, list):
        value = " ".join(str(v) for v in value if isinstance(v, (str, int, float)))
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


from app.core.coerce import _truthy


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _avg(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def _focal_display(raw: str) -> str | None:
    """'85' / '85.0' / '85.5' → '85mm' / '85.5mm';范围外返回 None(不算焦段)。"""
    value = _float_or_none(raw)
    if value is None or not (_FOCAL_MIN <= value <= _FOCAL_MAX):
        return None
    return (str(int(value)) if value == int(value) else str(value)) + "mm"


def _focal_sort_mm(display: str) -> float:
    return _float_or_none(display[:-2]) or 0.0


def _film_context(blob: str, start: int, end: int) -> bool:
    """该处焦段命中是否落在胶片/等效负语境:紧跟 film/胶片,或邻近窗口有 equivalent/等效。"""
    after = blob[end:end + _NEG_AFTER]
    if _FILM_AFTER_RE.search(after):
        return True
    before = blob[max(0, start - _NEG_BEFORE):start]
    return bool(_EQUIV_RE.search(before + " " + after))


def _extract_focals(blob: str) -> set[str]:
    """焦段集合(负语境感知):同一焦段只要有一处非胶片/等效语境的命中即算。"""
    out: set[str] = set()
    for m in _FOCAL_MM_RE.finditer(blob):
        display = _focal_display(m.group(1))
        if display and not _film_context(blob, m.start(), m.end()):
            out.add(display)
    return out


def _extract_zooms(blob: str) -> set[str]:
    out: set[str] = set()
    for lo, hi in _ZOOM_RE.findall(blob):
        a, b = _focal_display(lo), _focal_display(hi)
        if a and b and _focal_sort_mm(a) < _focal_sort_mm(b):
            out.add(a[:-2] + "-" + b)
    return out


# ── 数据装载(全只读;查询风格抄 signature_profile)───────────────────


def _load_pool_row(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, handle, display_name, platform, bio, raw_platform_data,
               recommended_product_lines_json, primary_topic, content_style,
               followers, avg_views
        FROM vkpi_kol_pool
        WHERE id = ?
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_evidence_rows(conn: Any, kol_pool_id: int, limit: int = 500) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          id AS evidence_id,
          COALESCE(video_title, title, '') AS title,
          view_count,
          is_active
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id = ?
        ORDER BY COALESCE(view_count, 0) DESC, id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows if _truthy(dict(r).get("is_active"))]


def _load_final_v1_rows(conn: Any, kol_pool_id: int, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          e.id AS evidence_id,
          COALESCE(e.video_title, e.title, '') AS title,
          e.view_count,
          ac.result AS result
        FROM vkpi_analysis_cache ac
        JOIN vkpi_kol_video_evidence e ON e.id::text = ac.target_id
        WHERE ac.target_type = 'video'
          AND ac.derive_method = ?
          AND ac.status = 'ready'
          AND e.kol_pool_id = ?
        ORDER BY COALESCE(e.view_count, 0) DESC, e.id DESC
        LIMIT ?
        """,
        (FINAL_V1_DERIVE_METHOD, int(kol_pool_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_products(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sku, series, category_main, category_detail,
               model_name, marketing_name, price_usd, status,
               mount, product_url, description, specs_json, fit_tags_json,
               source_confidence, official_catalog_product_id
        FROM vkpi_products
        """,
        (),
    ).fetchall()
    return [dict(r) for r in rows]


# ── final_v1 深析文本底料 ────────────────────────────────────────────


def _final_layers(root: dict[str, Any]) -> dict[str, Any]:
    """六层双态解包:平铺在根 / 嵌在 video_analysis_final_v1 / 藏在 raw_gemini_video 下。"""
    if _as_dict(root.get("layer1_visual_content")):
        return root
    nested = _as_dict(root.get(FINAL_V1_DERIVE_METHOD))
    if _as_dict(nested.get("layer1_visual_content")):
        return nested
    raw_nested = _as_dict(_as_dict(root.get("raw_gemini_video")).get(FINAL_V1_DERIVE_METHOD))
    return raw_nested if _as_dict(raw_nested.get("layer1_visual_content")) else {}


def _deep_blob_and_flag(result: Any) -> tuple[str, bool]:
    """深析文本底料(小写)+ 「深析确认出现 Viltrox」旗标;解不开诚实回空。"""
    root = _as_dict(_loads(result))
    if not root:
        return "", False
    raw = _as_dict(root.get("raw_gemini_video"))
    layers = _final_layers(root)
    layer1 = _as_dict(layers.get("layer1_visual_content"))

    parts: list[str] = [
        _text(layer1.get("content_summary"), 800),
        _text(layer1.get("product_presence"), 400),
        _text(layer1.get("brand_exposure"), 400),
        _text(layer1.get("production_observations"), 400),
        _text(raw.get("content_topic"), 400),
        _text(raw.get("viltrox_lens"), 200),
        _text(raw.get("other_lens"), 200),
        _text(raw.get("viltrox_products_all"), 400),
    ]
    for scene in _as_list(layer1.get("scene_timeline"))[:12]:
        if isinstance(scene, dict):
            parts.append(_text(scene.get("what"), 150))
    blob = " ".join(p for p in parts if p).lower()

    viltrox_flag = (
        _truthy(raw.get("viltrox_detected"))
        or bool(_text(raw.get("viltrox_lens"), 50))
        or bool(_as_list(raw.get("viltrox_products_all")))
    )
    return blob, viltrox_flag


# ── 产品目录聚类(焦段目录 + 产品线目录 + 镜头家族)───────────────────


def _product_line_of(row: dict[str, Any]) -> str:
    sku = _text(row.get("sku"), 120).upper()
    cat = (_text(row.get("category_main"), 60) + " " + _text(row.get("category_detail"), 60)).lower()
    name = _text(row.get("model_name"), 200).lower()
    padded = " " + name
    if "cine" in cat or "CINE" in sku or sku.startswith("EPIC") or "anamorphic" in name:
        return "cine"
    if "light" in cat or "flash" in cat:
        return "lighting"
    if "adapter" in cat or "booster" in cat or "speed booster" in name:
        return "adapter"
    if "filter" in cat or "FILTER" in sku:
        return "filter"
    if "monitor" in cat:
        return "monitor"
    if "battery" in cat or "BATTERY" in sku:
        return "battery"
    if "hood" in name or "cable" in name or "extension tube" in name or "tube" in cat or "accessor" in cat:
        return "accessory"
    if sku.startswith("MF") or padded.startswith(" mf ") or "manual" in name:
        return "mf_lens"
    if sku.startswith("AF") or " af " in padded or "lens" in cat:
        return "af_lens"
    return "accessory"


def _product_focals(row: dict[str, Any], line: str) -> set[str]:
    """镜头线产品的焦段(SKU 的 NNMM token + 型号 '135/1.8' 写法);非镜头线不算焦段。"""
    if line not in ("af_lens", "mf_lens", "cine"):
        return set()
    sku_blob = _text(row.get("sku"), 160).lower()
    name_blob = _text(row.get("model_name"), 200).lower()
    focals = _extract_focals(sku_blob) | _extract_focals(name_blob)
    focals |= {d for m in _MODEL_SLASH_RE.findall(name_blob) if (d := _focal_display(m))}
    return focals


def _product_aperture(row: dict[str, Any]) -> str:
    """光圈显示值:SKU 'F14'→'1.4' / 'F4-5'→'4.5';退回型号 '/1.8' 写法;解不出回空。"""
    m = _SKU_APERTURE_RE.search(_text(row.get("sku"), 160).upper())
    if m:
        token = m.group(1)
        if "-" in token:
            return token.replace("-", ".")
        if len(token) >= 2:
            return token[0] + "." + token[1:]
        return token + ".0"
    m2 = re.search(r"/([\d.]+)", _text(row.get("model_name"), 200))
    return m2.group(1) if m2 else ""


def _build_catalog(products: list[dict[str, Any]]) -> dict[str, Any]:
    """目录三视图:focal→SKU 聚合 / 产品线→SKU 数 / 镜头家族(焦段+系列+光圈,跨卡口去重)。"""
    focal_cat: dict[str, dict[str, Any]] = {}
    line_cat: dict[str, dict[str, Any]] = {}
    families: dict[tuple[str, str, str], dict[str, Any]] = {}

    for row in products:
        sku = _text(row.get("sku"), 120)
        if not sku:
            continue
        line = _product_line_of(row)
        price = _float_or_none(row.get("price_usd"))
        official = _text(row.get("status"), 30).lower() == "official"
        name = _text(row.get("marketing_name"), 160) or _text(row.get("model_name"), 160)

        entry = line_cat.setdefault(line, {"sku_count": 0, "example": ""})
        entry["sku_count"] += 1
        if not entry["example"] and name:
            entry["example"] = name

        for focal in _product_focals(row, line):
            slot = focal_cat.setdefault(focal, {
                "sku_count": 0, "official_sku_count": 0, "value_usd": 0.0,
                "max_price_usd": None, "flagship": "", "series": set(), "lines": set(),
            })
            slot["sku_count"] += 1
            slot["lines"].add(line)
            series = _text(row.get("series"), 30)
            if series:
                slot["series"].add(series)
            if official:
                slot["official_sku_count"] += 1
                if price is not None:
                    slot["value_usd"] += price
                    if slot["max_price_usd"] is None or price > slot["max_price_usd"]:
                        slot["max_price_usd"] = price
                        slot["flagship"] = name

        if line in ("af_lens", "mf_lens", "cine"):
            focals = _product_focals(row, line)
            if len(focals) == 1:  # 单焦段才建家族;EPIC 套装多焦段不硬拆
                focal = next(iter(focals))
                series = _text(row.get("series"), 30).lower()
                aperture = _product_aperture(row)
                key = (focal, series, aperture)
                fam = families.setdefault(key, {
                    "focal": focal, "series": series, "aperture": aperture, "line": line,
                    "skus": set(), "name": name, "max_price_usd": None,
                })
                fam["skus"].add(sku)
                if price is not None and (fam["max_price_usd"] is None or price > fam["max_price_usd"]):
                    fam["max_price_usd"] = price
                if name and (not fam["name"] or len(name) < len(fam["name"])):
                    fam["name"] = name

    return {"focals": focal_cat, "lines": line_cat, "families": families}


# ── 个性化产品机会(机身/卡口 × 创作类型 × 价格带 × 系列多样性)─────────

from app.domains.kol.focal_recommendations import (
    creator_context as _creator_context,
    creator_price_profile as _creator_price_profile,
    effective_product_mount as _effective_product_mount,
    infer_creator_mount as _infer_creator_mount,
    price_fit as _price_fit,
    product_opportunities as _rank_product_opportunities,
    product_series as _product_series,
)


def _product_opportunities(
    products: list[dict[str, Any]],
    gap_focals: set[str],
    context: dict[str, Any],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    return _rank_product_opportunities(
        products,
        gap_focals,
        context,
        product_line_of=_product_line_of,
        product_focals=_product_focals,
        focal_sort_mm=_focal_sort_mm,
        line_labels=_LINE_LABEL,
        limit=limit,
    )


# ── 视频侧检测 ──────────────────────────────────────────────────────


def _merge_videos(
    evidence_rows: list[dict[str, Any]],
    final_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 evidence_id 合并:标题底料人人有,深析底料只有 final_v1 ready 的才有。"""
    by_id: dict[int, dict[str, Any]] = {}
    for row in evidence_rows:
        eid = _int_or_none(row.get("evidence_id"))
        if eid is None:
            continue
        by_id[eid] = {
            "evidence_id": eid,
            "title": _text(row.get("title"), 200),
            "view_count": _int_or_none(row.get("view_count")),
            "title_blob": _text(row.get("title"), 300).lower(),
            "deep_blob": "",
            "viltrox_flag": False,
            "has_deep": False,
        }
    for row in final_rows:
        eid = _int_or_none(row.get("evidence_id"))
        if eid is None:
            continue
        blob, flag = _deep_blob_and_flag(row.get("result"))
        video = by_id.setdefault(eid, {
            "evidence_id": eid,
            "title": _text(row.get("title"), 200),
            "view_count": _int_or_none(row.get("view_count")),
            "title_blob": _text(row.get("title"), 300).lower(),
            "deep_blob": "",
            "viltrox_flag": False,
            "has_deep": False,
        })
        video["deep_blob"] = blob
        video["viltrox_flag"] = flag
        video["has_deep"] = True
    return list(by_id.values())


def _detect_coverage(videos: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    """逐视频跑焦段正则 + 产品线词表 → (焦段命中, 产品线命中, 变焦段提及计数)。"""
    focal_hits: dict[str, dict[str, Any]] = {}
    line_hits: dict[str, dict[str, Any]] = {}
    zoom_mentions: dict[str, int] = {}

    for video in videos:
        blob = (video["title_blob"] + " " + video["deep_blob"]).strip()
        if not blob:
            continue
        views = video.get("view_count")
        # 每视频焦段集合各算一次并缓存(标题 / 全文各一),消掉逐焦段重复跑正则。
        title_focals = _extract_focals(video["title_blob"])
        blob_focals = _extract_focals(blob)

        for focal in blob_focals:
            slot = focal_hits.setdefault(focal, {
                "video_count": 0, "views": [], "title_hits": 0, "deep_hits": 0, "top": None,
            })
            slot["video_count"] += 1
            if isinstance(views, int):
                slot["views"].append(views)
            if focal in title_focals:
                slot["title_hits"] += 1
            elif video["has_deep"]:
                slot["deep_hits"] += 1
            if slot["top"] is None or (views or 0) > (slot["top"].get("view_count") or 0):
                slot["top"] = {"title": video["title"][:120], "view_count": views}

        for zoom in _extract_zooms(blob):
            zoom_mentions[zoom] = zoom_mentions.get(zoom, 0) + 1

        for key, _label, terms in PRODUCT_LINES:
            hit = any(term in blob for term in terms)
            if key == "af_lens" and not hit:
                hit = bool(blob_focals)  # 提到具体焦段即视作镜头内容
            if not hit:
                continue
            slot = line_hits.setdefault(key, {"video_count": 0, "views": [], "example": ""})
            slot["video_count"] += 1
            if isinstance(views, int):
                slot["views"].append(views)
            if not slot["example"]:
                slot["example"] = video["title"][:120]

    return focal_hits, line_hits, zoom_mentions


def _aperture_terms(aperture: str) -> tuple[str, ...]:
    return ("f/" + aperture, "f" + aperture, "t" + aperture, aperture) if aperture else ()


def _match_families(
    videos: list[dict[str, Any]],
    families: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """我方 SKU 家族命中:焦段 + (系列词|光圈词) + viltrox 语境,三条都要,宁缺毋滥。"""
    # 每视频焦段集合只算一次(消掉逐家族×逐视频的重复正则)。
    prepared: list[tuple[dict[str, Any], str, set[str]]] = []
    for video in videos:
        blob = (video["title_blob"] + " " + video["deep_blob"]).strip()
        if blob:
            prepared.append((video, blob, _extract_focals(blob)))
    items: list[dict[str, Any]] = []
    for fam in families.values():
        matched: list[dict[str, Any]] = []
        for video, blob, blob_focals in prepared:
            if fam["focal"] not in blob_focals:
                continue
            if "viltrox" not in blob and not video["viltrox_flag"]:
                continue
            if fam["series"]:
                if fam["series"] not in blob:
                    continue
            elif fam["aperture"]:
                if not any(term in blob for term in _aperture_terms(fam["aperture"])):
                    continue
            matched.append(video)
        if not matched:
            continue
        best = max(matched, key=lambda v: v.get("view_count") or 0)
        items.append({
            "family": fam["name"] or (fam["focal"] + (" " + fam["series"].upper() if fam["series"] else "")),
            "focal": fam["focal"],
            "series": fam["series"].upper() if fam["series"] else None,
            "aperture": fam["aperture"] or None,
            "skus": sorted(fam["skus"])[:8],
            "matched_video_count": len(matched),
            "example_title": best["title"][:120],
            "example_view_count": best.get("view_count"),
        })
    # 同一物理产品在目录里有两套编码(零售 AF-85MM-F14-PRO 与内部 VL-LEN073),
    # 按 (焦段, 光圈) 合并成一个家族:SKU 取并集,名字偏好带系列词的零售名。
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (item["focal"], item["aperture"] or "")
        slot = merged.get(key)
        if slot is None:
            merged[key] = item
            continue
        slot["skus"] = sorted(set(slot["skus"]) | set(item["skus"]))[:8]
        slot["matched_video_count"] = max(slot["matched_video_count"], item["matched_video_count"])
        if item["series"] and not slot["series"]:
            slot["family"], slot["series"] = item["family"], item["series"]
            slot["example_title"] = item["example_title"]
            slot["example_view_count"] = item["example_view_count"]
    out = list(merged.values())
    out.sort(key=lambda it: (it["matched_video_count"], it["example_view_count"] or 0), reverse=True)
    return out[:20]


# ── 主入口 ──────────────────────────────────────────────────────────


def focal_matrix(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
    """KOL 焦段/产品线覆盖矩阵;KOL 不存在抛 LookupError(路由转 404)。"""
    from app.db.connection import get_conn

    db = conn or get_conn()
    pool = _load_pool_row(db, int(kol_pool_id))
    if not pool:
        raise LookupError(f"kol_pool {kol_pool_id} not found")

    evidence_rows = _load_evidence_rows(db, int(kol_pool_id))
    final_rows = _load_final_v1_rows(db, int(kol_pool_id))
    products = _load_products(db)
    catalog = _build_catalog(products)
    videos = _merge_videos(evidence_rows, final_rows)
    focal_hits, line_hits, zoom_mentions = _detect_coverage(videos)
    creator_context = _creator_context(pool, videos)

    # ── 矩阵格子:目录焦段(covered / gap)+ 目录外但 KOL 拍过的焦段 ──
    focal_cells: list[dict[str, Any]] = []
    all_focals = sorted(set(catalog["focals"]) | set(focal_hits), key=_focal_sort_mm)
    for focal in all_focals:
        cat = catalog["focals"].get(focal)
        hit = focal_hits.get(focal)
        cell: dict[str, Any] = {
            "focal": focal,
            "mm": _focal_sort_mm(focal),
            "in_catalog": bool(cat),
            "covered": bool(hit),
            "video_count": hit["video_count"] if hit else 0,
            "avg_views": _avg(hit["views"]) if hit else None,
            "title_hits": hit["title_hits"] if hit else 0,
            "deep_hits": hit["deep_hits"] if hit else 0,
            "top_example": hit["top"] if hit else None,
        }
        if cat:
            cell["catalog"] = {
                "sku_count": cat["sku_count"],
                "official_sku_count": cat["official_sku_count"],
                "value_usd": round(cat["value_usd"], 2),
                "max_price_usd": cat["max_price_usd"],
                "flagship": cat["flagship"] or None,
                "series": sorted(cat["series"]),
                "lines": sorted(_LINE_LABEL.get(ln, ln) for ln in cat["lines"]),
            }
        focal_cells.append(cell)

    # ── 产品线格子:目录有的线 covered / gap ──
    line_cells: list[dict[str, Any]] = []
    for key, label, _terms in PRODUCT_LINES:
        cat_entry = catalog["lines"].get(key)
        hit = line_hits.get(key)
        if not cat_entry and not hit:
            continue  # 目录没有、KOL 也没拍:不渲染幽灵线
        line_cells.append({
            "key": key,
            "label": label,
            "in_catalog": bool(cat_entry),
            "catalog_sku_count": cat_entry["sku_count"] if cat_entry else 0,
            "covered": bool(hit),
            "video_count": hit["video_count"] if hit else 0,
            "avg_views": _avg(hit["views"]) if hit else None,
            "example_title": (hit["example"] or None) if hit else None,
        })

    # ── gaps:目录焦段空白 + 机身/卡口/创作类型/价格带/系列多样性后的具体产品 ──
    gap_focals = {
        cell["focal"]
        for cell in focal_cells
        if cell["in_catalog"] and not cell["covered"]
    }
    catalog_gap_items = [
        {
            "focal": cell["focal"],
            "mm": cell["mm"],
            "sku_count": cell["catalog"]["sku_count"],
            "official_sku_count": cell["catalog"]["official_sku_count"],
            "value_usd": cell["catalog"]["value_usd"],
            "max_price_usd": cell["catalog"]["max_price_usd"],
            "flagship": cell["catalog"]["flagship"],
            "series": cell["catalog"]["series"],
            "lines": cell["catalog"]["lines"],
        }
        for cell in focal_cells
        if cell["focal"] in gap_focals
    ]
    catalog_gap_items.sort(key=lambda item: item["mm"])
    recommendations = _product_opportunities(products, gap_focals, creator_context)
    line_gaps = [c for c in line_cells if c["in_catalog"] and not c["covered"]]

    covered_focals = [c for c in focal_cells if c["covered"]]
    if not videos:
        covered_block: dict[str, Any] = {
            "status": "empty",
            "reason": "该 KOL 暂无有效视频证据(vkpi_kol_video_evidence 空/全 inactive),焦段覆盖无从统计。",
        }
        gaps_block: dict[str, Any] = {
            "status": "empty",
            "reason": "没有任何视频证据时全目录皆是空白,排序无意义 — 先补充 evidence 再看可切入焦段。",
            "items": [],
            "recommendations": [],
            "creator_context": creator_context,
            "catalog_gap_count": len(gap_focals),
            "recommendation_status": "insufficient_evidence",
        }
    elif not covered_focals:
        covered_block = {
            "status": "empty",
            "reason": f"扫了 {len(videos)} 条视频(标题+深析文本),没提到任何具体焦段 — 词表/正则不硬猜。",
        }
        gaps_block = {
            "status": "ready",
            "items": catalog_gap_items,
            "recommendations": recommendations,
            "product_lines": line_gaps,
            "creator_context": creator_context,
            "catalog_gap_count": len(gap_focals),
            "ranking_method": "mount_content_price_series_v2",
            "recommendation_status": "ready" if recommendations else "insufficient_evidence" if gap_focals else "not_applicable",
            **({
                "recommendation_reason": "存在目录焦段空白,但机身/卡口/常用镜头证据不足或冲突;不生成伪 Top1。"
            } if gap_focals and not recommendations else {}),
        }
    else:
        covered_block = {
            "status": "ready",
            "focal_count": len(covered_focals),
            "zoom_mentions": [
                {"range_mm": k, "video_count": v}
                for k, v in sorted(zoom_mentions.items(), key=lambda kv: kv[1], reverse=True)
            ][:6],
        }
        gaps_block = {
            "status": "ready",
            "items": catalog_gap_items,
            "recommendations": recommendations,
            "product_lines": line_gaps,
            "creator_context": creator_context,
            "catalog_gap_count": len(gap_focals),
            "ranking_method": "mount_content_price_series_v2",
            "recommendation_status": "ready" if recommendations else "insufficient_evidence" if gap_focals else "not_applicable",
            **({
                "recommendation_reason": "存在目录焦段空白,但机身/卡口/常用镜头证据不足或冲突;不生成伪 Top1。"
            } if gap_focals and not recommendations else {}),
        }

    matched_items = _match_families(videos, catalog["families"]) if videos else []
    matched_block: dict[str, Any] = (
        {"status": "ready", "items": matched_items}
        if matched_items
        else {
            "status": "empty",
            "reason": "视频文本里没同时命中「焦段+系列/光圈+Viltrox 语境」,不硬贴我方 SKU。",
        }
    )

    return {
        "status": "ready",
        "kol_pool_id": int(kol_pool_id),
        "kol": {
            "handle": _text(pool.get("handle"), 100),
            "display_name": _text(pool.get("display_name"), 100),
            "platform": _text(pool.get("platform"), 30),
        },
        "basis": {
            "evidence_count": len(evidence_rows),
            "deep_analyzed_count": len(final_rows),
            "catalog_focal_count": len(catalog["focals"]),
            "method": "lexicon_focal_v1",
            "opportunity_method": "mount_content_price_series_v2",
        },
        "matrix": {"focals": focal_cells, "product_lines": line_cells},
        "covered": covered_block,
        "gaps": gaps_block,
        "matched_products": matched_block,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "口径:焦段/产品线为词表+正则提取(evidence 标题 + final_v1 深析文本,零 LLM);"
            "胶片/等效负语境剔除:35mm film、35mm 胶片、50mm equivalent、等效50mm 这类"
            "底片规格/换算视角写法不算焦段覆盖;"
            "覆盖=拍过该类内容,不代表用的是我方产品(我方命中看 matched_products);"
            "产品机会=官方单品经机身/卡口硬闸,再按创作类型、装备价格带代理与系列多样性排序;"
            "多焦段套装不重复计价,卡口未知明确待核验;价格带不是购买力/合作预算。"
            "独立展示信号,不参与 V6 Fit 评分。"
        ),
    }
