"""段级创意资产库 v0(creative_segments)——「哪个开头/哪种画面最灵」可检索。

把每条已深析(final_v1)视频拆成可检索的段级条目(纯已有分析文本的索引,
零切片文件、零外部调用、零 LLM、零写库):
  opening           开头段:scene_timeline 首镜 + layer2 前3秒感受 → 开头类型词表分类;
  scene             分镜段:scene_timeline 每镜(what/timestamp/why_it_matters)→ 拍法词表标签;
  product_exposure  产品露出段:layer1 product_presence + brand_exposure 描述文本。

检索三路(词表过滤式,非搜索引擎):
  query  自由词:小写子串匹配 段描述+视频标题+标签文案;
  style  拍法:命中段级 styles 或全片 video_styles(键/中文标签/子串均可);
  focal  焦段:'85mm'/'85' → 段描述+视频标题正则提焦段后精确对齐。
排序:按所属视频播放数降序(再 evidence_id/段序稳定排序)。

可追溯:每条带 source(evidence_id + 层名路径 + derive_method),回查
vkpi_analysis_cache 即得原文。词表复用第2轮 signature_profile 的
SHOOTING_MODES / OPENING_TYPES(只 import 不复制,口径同源)。

数据落点侦察结论(2026-07 本地真库):段级素材真实落点是
vkpi_analysis_cache(derive_method='video_analysis_final_v1', status='ready')
result JSON 的 layer1_visual_content.scene_timeline(what/timestamp/why_it_matters)
+ product_presence/brand_exposure(文本)+ layer2_viewer_emotion.first_three_seconds_feeling;
vkpi_kol_llm_deep_analysis_results.llm_dimensions_11 仅存 layer1_summary/scores 等
汇总键,无分镜粒度,不作数据源。

诚实态:深析库为空 → {status:"empty", reason};有库但过滤零命中 →
status:"ready" + matched=0 + 空 items(绝不硬凑)。分类是词表规则
(method=lexicon_segments_v1),识别不了就不贴标签。

compat 约定:SQL 占位符 ?;SQL 字符串零字面 percent(不用 LIKE);BOOLEAN 判真
走 _truthy;JSONB 双态(dict/str)走 _loads。函数内懒 import get_conn。
红线:纯读聚合,绝不写库;零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.kol.signature_profile import OPENING_TYPES, SHOOTING_MODES

logger = get_logger(__name__)

FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"

_SCAN_LIMIT = 800          # 全库扫描上限(当前 final_v1 ready 约 481 条,留余量)
_MAX_SCENES_PER_VIDEO = 12  # 与 signature_profile 同口径,防个别超长 timeline 撑爆
_MAX_LIMIT = 200

# 焦段提取:与第3轮 focal_matrix 同口径('85mm'/'85 mm'/'85.5mm';负回顾防 '85.5mm' 抠出 '5mm')
_FOCAL_MM_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d)?)\s*mm", re.IGNORECASE)
_FOCAL_MIN, _FOCAL_MAX = 6.0, 800.0

_STYLE_LABEL = {key: label for key, label, _ in SHOOTING_MODES}
_OPENING_LABEL = {key: label for key, label, _ in OPENING_TYPES}


# ── 小工具(容错约定与 signature_profile / focal_matrix 同款)─────────


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


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _focal_display(raw: str) -> str | None:
    """'85' / '85.0' / '85.5' → '85mm' / '85.5mm';合理范围外返回 None(不算焦段)。"""
    value = _float_or_none(raw)
    if value is None or not (_FOCAL_MIN <= value <= _FOCAL_MAX):
        return None
    return (str(int(value)) if value == int(value) else str(value)) + "mm"


def _extract_focals(blob: str) -> set[str]:
    return {d for m in _FOCAL_MM_RE.findall(blob) if (d := _focal_display(m))}


def _normalize_focal(raw: str) -> str:
    """筛选输入 '85mm' / '85 mm' / '85' → 统一显示值 '85mm';解不出回空(= 不过滤)。"""
    token = str(raw or "").strip().lower()
    if not token:
        return ""
    if token.endswith("mm"):
        token = token[:-2].strip()
    return _focal_display(token) or ""


def _final_layers(root: dict[str, Any]) -> dict[str, Any]:
    """六层双态解包:平铺在根 / 嵌在 video_analysis_final_v1 / 藏在 raw_gemini_video 下。"""
    if _as_dict(root.get("layer1_visual_content")):
        return root
    nested = _as_dict(root.get(FINAL_V1_DERIVE_METHOD))
    if _as_dict(nested.get("layer1_visual_content")):
        return nested
    raw_nested = _as_dict(_as_dict(root.get("raw_gemini_video")).get(FINAL_V1_DERIVE_METHOD))
    return raw_nested if _as_dict(raw_nested.get("layer1_visual_content")) else {}


def _classify(blob: str, lexicon: tuple[tuple[str, str, tuple[str, ...]], ...]) -> list[str]:
    """词表分类:返回命中的 key 列表(顺序按词表定义);识别不了就空,不硬贴。"""
    if not blob:
        return []
    return [key for key, _label, terms in lexicon if any(term in blob for term in terms)]


def _tags(keys: list[str], label_map: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": k, "label": label_map.get(k, k)} for k in keys]


# ── 数据装载(全只读;JOIN 口径抄 signature_profile._load_final_v1_rows)──


def _load_final_v1_rows(
    conn: Any,
    scan_limit: int = _SCAN_LIMIT,
    *,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """全库 ready 的 final_v1 深析 + evidence 表现数据 + KOL 归属,按播放数降序。"""
    rows = conn.execute(
        """
        SELECT
          e.id AS evidence_id,
          COALESCE(e.video_title, e.title, '') AS title,
          e.content_url,
          e.platform,
          e.view_count,
          e.posted_at,
          e.thumbnail_url,
          e.kol_pool_id,
          k.handle AS kol_handle,
          k.display_name AS kol_display_name,
          ac.result AS result
        FROM vkpi_analysis_cache ac
        JOIN vkpi_kol_video_evidence e ON e.id::text = ac.target_id
        JOIN vkpi_kol_pool k ON k.id = e.kol_pool_id
        WHERE ac.target_type = 'video'
          AND ac.derive_method = ?
          AND ac.status = 'ready'
        ORDER BY COALESCE(e.view_count, 0) DESC, e.id DESC
        LIMIT ? OFFSET ?
        """,
        (FINAL_V1_DERIVE_METHOD, int(scan_limit), max(0, int(offset))),
    ).fetchall()
    return [dict(r) for r in rows]


def _count_final_v1_rows(conn: Any) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM vkpi_analysis_cache ac
        JOIN vkpi_kol_video_evidence e ON e.id::text = ac.target_id
        JOIN vkpi_kol_pool k ON k.id = e.kol_pool_id
        WHERE ac.target_type = 'video'
          AND ac.derive_method = ?
          AND ac.status = 'ready'
        """,
        (FINAL_V1_DERIVE_METHOD,),
    ).fetchone()
    return min(_SCAN_LIMIT, _int_or_none(dict(row).get("n") if row else 0) or 0)


# ── 缩略图链(pool_detail 同款语义;毒缓存自愈:cached_image_url 已把
#    「上游失败写进磁盘的 1x1 透明 SVG 占位」判为无效,不再当真图上报)──────


def _thumbnail_fields(row: dict[str, Any]) -> dict[str, Any]:
    """看板缩略图三件套(纯读本地文件缓存 + 派生,零网络零写库):
    cached_thumbnail_url = 本地图缓存(失败占位自愈后才算);youtube 从 content_url
    派生官方缩略图;best_thumbnail 链序与 pool_detail 一致:cached → raw → youtube。
    拿不到就 None / 空串,前端诚实占位,绝不编图。"""
    from app.domains.kol.pool_detail import _youtube_thumbnail_url, _youtube_video_id
    from app.domains.media.cache import cached_image_url

    raw = _text(row.get("thumbnail_url"), 500)
    cached = ""
    if raw:
        try:
            cached = cached_image_url(raw) or ""
        except Exception:  # noqa: BLE001 — 缓存读失败按无缓存处理,不炸检索
            cached = ""
    youtube_thumb = ""
    if _text(row.get("platform"), 30).lower() == "youtube":
        try:
            youtube_thumb = _youtube_thumbnail_url(_youtube_video_id(row.get("content_url")))
        except Exception:  # noqa: BLE001
            youtube_thumb = ""
    return {
        "thumbnail_url": raw or None,
        "cached_thumbnail_url": cached or None,
        "youtube_thumbnail_url": youtube_thumb or None,
        "best_thumbnail": cached or raw or youtube_thumb or None,
    }


# ── 单视频 → 段级条目拆解 ────────────────────────────────────────────


def _video_style_blob(layers: dict[str, Any]) -> str:
    """全片拍法底料(小写):layer1 摘要/制作观察/分镜 + layer2 前3秒;口径同 signature_profile。"""
    layer1 = _as_dict(layers.get("layer1_visual_content"))
    layer2 = _as_dict(layers.get("layer2_viewer_emotion"))
    parts: list[str] = [
        _text(layer1.get("content_summary"), 800),
        _text(layer1.get("production_observations"), 400),
        _text(layer2.get("first_three_seconds_feeling"), 200),
    ]
    for scene in _as_list(layer1.get("scene_timeline"))[:_MAX_SCENES_PER_VIDEO]:
        if isinstance(scene, dict):
            parts.append(_text(scene.get("what"), 150))
    return " ".join(p for p in parts if p).lower()


def _decompose_video(
    row: dict[str, Any],
    *,
    include_thumbnails: bool = True,
) -> list[dict[str, Any]]:
    """一条深析视频 → 段级条目列表;解不开 payload 诚实返回空(该视频不进索引)。"""
    root = _as_dict(_loads(row.get("result")))
    layers = _final_layers(root)
    layer1 = _as_dict(layers.get("layer1_visual_content"))
    layer2 = _as_dict(layers.get("layer2_viewer_emotion"))
    if not layer1:
        return []

    eid = _int_or_none(row.get("evidence_id"))
    title = _text(row.get("title"), 160)
    title_lower = title.lower()
    video: dict[str, Any] = {
        "evidence_id": eid,
        "title": title,
        "content_url": _text(row.get("content_url"), 500),
        "platform": _text(row.get("platform"), 30),
        "view_count": _int_or_none(row.get("view_count")),
        "posted_at": _iso(row.get("posted_at")),
    }
    if include_thumbnails:
        # 看板缩略图(cached → raw → youtube 派生;毒缓存自愈链,失败=None 前端诚实占位)
        video.update(_thumbnail_fields(row))
    kol = {
        "kol_pool_id": _int_or_none(row.get("kol_pool_id")),
        "handle": _text(row.get("kol_handle"), 100),
        "display_name": _text(row.get("kol_display_name"), 100),
    }
    video_style_keys = _classify(_video_style_blob(layers), SHOOTING_MODES)
    video_styles = _tags(video_style_keys, _STYLE_LABEL)

    def _entry(segment_type: str, layer_path: str, description: str, *, timestamp: str | None = None,
               opening_keys: list[str] | None = None, style_keys: list[str] | None = None) -> dict[str, Any]:
        blob = description.lower()
        return {
            "segment_id": f"{eid}:{layer_path}",
            "segment_type": segment_type,
            "description": description,
            "timestamp": timestamp,
            "opening_types": _tags(opening_keys or [], _OPENING_LABEL),
            "styles": _tags(style_keys or [], _STYLE_LABEL),
            "video_styles": video_styles,
            "focals": sorted(_extract_focals(blob + " " + title_lower)),
            "source": {
                "evidence_id": eid,
                "layer": layer_path,
                "derive_method": FINAL_V1_DERIVE_METHOD,
            },
            "video": video,
            "kol": kol,
            "_blob": blob,  # 检索底料,出参前剥掉
        }

    segments: list[dict[str, Any]] = []
    scenes = [s for s in _as_list(layer1.get("scene_timeline"))[:_MAX_SCENES_PER_VIDEO] if isinstance(s, dict)]

    # ① 开头段:首镜 + 前3秒感受 → 开头类型词表
    opening_parts = []
    if scenes:
        opening_parts.append(_text(scenes[0].get("what"), 300))
        opening_parts.append(_text(scenes[0].get("why_it_matters"), 200))
    first3 = _text(layer2.get("first_three_seconds_feeling"), 200)
    if first3:
        opening_parts.append(first3)
    opening_desc = " ".join(p for p in opening_parts if p)
    if opening_desc:
        segments.append(
            _entry(
                "opening",
                "layer1_visual_content.scene_timeline[0]+layer2_viewer_emotion.first_three_seconds_feeling",
                opening_desc,
                timestamp=_text(scenes[0].get("timestamp"), 30) or None if scenes else None,
                opening_keys=_classify(opening_desc.lower(), OPENING_TYPES),
                style_keys=_classify(opening_desc.lower(), SHOOTING_MODES),
            )
        )

    # ② 分镜段:timeline 每镜(含首镜——首镜同时是 opening 与 scene,两种检索意图都要能命中)
    for idx, scene in enumerate(scenes):
        what = _text(scene.get("what"), 300)
        why = _text(scene.get("why_it_matters"), 200)
        desc = " ".join(p for p in (what, why) if p)
        if not desc:
            continue
        segments.append(
            _entry(
                "scene",
                f"layer1_visual_content.scene_timeline[{idx}]",
                desc,
                timestamp=_text(scene.get("timestamp"), 30) or None,
                style_keys=_classify(desc.lower(), SHOOTING_MODES),
            )
        )

    # ③ 产品露出段:product_presence + brand_exposure 描述文本(双态:str 或 dict)
    presence = _text(layer1.get("product_presence"), 400)
    exposure = _text(layer1.get("brand_exposure"), 400)
    exposure_desc = " ".join(p for p in (presence, exposure) if p)
    if exposure_desc:
        segments.append(
            _entry(
                "product_exposure",
                "layer1_visual_content.product_presence+brand_exposure",
                exposure_desc,
                style_keys=_classify(exposure_desc.lower(), SHOOTING_MODES),
            )
        )

    return segments


# ── 过滤三路 ────────────────────────────────────────────────────────


def _style_match(segment: dict[str, Any], style_token: str) -> bool:
    """style 过滤:键/中文标签/子串,命中段级 styles 或全片 video_styles 任一即过。"""
    for tag in list(segment.get("styles") or []) + list(segment.get("video_styles") or []):
        key = str(tag.get("key") or "").lower()
        label = str(tag.get("label") or "").lower()
        if style_token == key or style_token == label or style_token in label or style_token in key:
            return True
    return False


def _matches(segment: dict[str, Any], query_token: str, style_token: str, focal_token: str) -> bool:
    if focal_token and focal_token not in (segment.get("focals") or []):
        return False
    if style_token and not _style_match(segment, style_token):
        return False
    if query_token:
        haystack = " ".join(
            [
                segment.get("_blob") or "",
                str(_as_dict(segment.get("video")).get("title") or "").lower(),
                " ".join(str(t.get("label") or "") for t in _as_list(segment.get("opening_types"))).lower(),
                " ".join(str(t.get("label") or "") for t in _as_list(segment.get("styles"))).lower(),
                " ".join(str(t.get("label") or "") for t in _as_list(segment.get("video_styles"))).lower(),
            ]
        )
        if query_token not in haystack:
            return False
    return True


_SEGMENT_TYPE_LABEL = {"opening": "开头段", "scene": "分镜段", "product_exposure": "产品露出段"}


def _facets(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """全索引(过滤前)可选项计数,供前端下拉/图形;只列真实存在的值。"""
    style_counts: dict[str, int] = {}
    opening_counts: dict[str, int] = {}
    focal_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for seg in segments:
        type_key = str(seg.get("segment_type") or "")
        if type_key:
            type_counts[type_key] = type_counts.get(type_key, 0) + 1
        seen_styles = {str(t.get("key")) for t in list(seg.get("styles") or []) + list(seg.get("video_styles") or [])}
        for key in seen_styles:
            style_counts[key] = style_counts.get(key, 0) + 1
        for tag in _as_list(seg.get("opening_types")):
            key = str(tag.get("key"))
            opening_counts[key] = opening_counts.get(key, 0) + 1
        for focal in _as_list(seg.get("focals")):
            focal_counts[str(focal)] = focal_counts.get(str(focal), 0) + 1
    return {
        "styles": sorted(
            ({"key": k, "label": _STYLE_LABEL.get(k, k), "segment_count": v} for k, v in style_counts.items()),
            key=lambda item: item["segment_count"],
            reverse=True,
        ),
        "openings": sorted(
            ({"key": k, "label": _OPENING_LABEL.get(k, k), "segment_count": v} for k, v in opening_counts.items()),
            key=lambda item: item["segment_count"],
            reverse=True,
        ),
        "focals": sorted(
            ({"focal": k, "segment_count": v} for k, v in focal_counts.items()),
            key=lambda item: item["segment_count"],
            reverse=True,
        )[:24],
        # 段型构成(看板环图口径:全索引过滤前计数,与三路 facets 同窗)
        "segment_types": sorted(
            (
                {"key": k, "label": _SEGMENT_TYPE_LABEL.get(k, k), "segment_count": v}
                for k, v in type_counts.items()
            ),
            key=lambda item: item["segment_count"],
            reverse=True,
        ),
    }


# ── 主入口 ──────────────────────────────────────────────────────────


def segment_top_items(
    query: str = "",
    style: str = "",
    focal: str = "",
    limit: int = 30,
) -> dict[str, Any]:
    """Return the exact top-N planning projection without building all facets.

    GTM summary/preview only consume description, tags, views and platform from
    the leading items.  The source query is already ordered by the same keys as
    ``segment_search``; paging until N matches therefore returns the identical
    leading slice while avoiding full-result JSON decoding, thumbnail filesystem
    probes and facet construction.  No total-match claim is made by this view.
    """
    from app.db.connection import get_conn

    conn = get_conn()
    bounded_limit = max(1, min(_int_or_none(limit) or 30, _MAX_LIMIT))
    query_token = _text(query, 120).lower()
    style_token = _text(style, 60).lower()
    focal_input = _text(focal, 20)
    focal_token = _normalize_focal(focal_input)
    available_videos = _count_final_v1_rows(conn)
    if available_videos <= 0:
        return {
            "status": "empty",
            "reason": "深析库为空(vkpi_analysis_cache 无 ready 的 final_v1 视频),段级资产无从索引 — 先跑「KOL深度分析理解」。",
            "filters": {"query": query_token, "style": style_token, "focal": focal_token or focal_input},
            "scanned_videos": 0,
            "returned": 0,
            "items": [],
        }

    page_size = max(16, min(64, bounded_limit * 2))
    selected: list[dict[str, Any]] = []
    offset = 0
    while offset < available_videos and len(selected) < bounded_limit:
        rows = _load_final_v1_rows(
            conn,
            min(page_size, available_videos - offset),
            offset=offset,
        )
        if not rows:
            break
        for row in rows:
            for segment in _decompose_video(row, include_thumbnails=False):
                if _matches(segment, query_token, style_token, focal_token):
                    selected.append(segment)
                    if len(selected) >= bounded_limit:
                        break
            if len(selected) >= bounded_limit:
                break
        offset += len(rows)

    items = [
        {k: v for k, v in segment.items() if k != "_blob"}
        for segment in selected[:bounded_limit]
    ]
    return {
        "status": "ready",
        "method": "lexicon_segments_v1",
        "projection": "planning_core_v1",
        "selection": "exact_top_n",
        "filters": {"query": query_token, "style": style_token, "focal": focal_token or focal_input},
        "scanned_videos": available_videos,
        "returned": len(items),
        "items": items,
        "note": (
            "与 segment_search 相同排序和词表过滤的精确 top-N;本投影不构建全量 facets,"
            "不声称过滤后总命中数。"
        ),
    }


def segment_search(query: str = "", style: str = "", focal: str = "", limit: int = 30) -> dict:
    """段级创意资产检索:三路词表过滤 + 按所属视频播放数降序;全只读。"""
    from app.db.connection import get_conn

    conn = get_conn()
    limit = max(1, min(_int_or_none(limit) or 30, _MAX_LIMIT))
    query_token = _text(query, 120).lower()
    style_token = _text(style, 60).lower()
    focal_input = _text(focal, 20)
    focal_token = _normalize_focal(focal_input)

    rows = _load_final_v1_rows(conn)
    if not rows:
        return {
            "status": "empty",
            "reason": "深析库为空(vkpi_analysis_cache 无 ready 的 final_v1 视频),段级资产无从索引 — 先跑「KOL深度分析理解」。",
            "filters": {"query": query_token, "style": style_token, "focal": focal_token or focal_input},
            "scanned_videos": 0,
            "segment_count": 0,
            "matched": 0,
            "items": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    all_segments: list[dict[str, Any]] = []
    for row in rows:
        all_segments.extend(_decompose_video(row))

    matched = [s for s in all_segments if _matches(s, query_token, style_token, focal_token)]
    # 稳定排序:所属视频播放数降序 → evidence_id 降序 → 段落原始顺序(decompose 顺序已保序)
    matched.sort(
        key=lambda s: (
            -(_int_or_none(_as_dict(s.get("video")).get("view_count")) or 0),
            -(_int_or_none(_as_dict(s.get("source")).get("evidence_id")) or 0),
        )
    )

    items = [{k: v for k, v in seg.items() if k != "_blob"} for seg in matched[:limit]]
    note_bits = ["段级条目=纯已有 final_v1 分析文本的索引(零切片文件零外部调用零 LLM)"]
    if focal_input and not focal_token:
        note_bits.append(f"focal 输入「{focal_input}」解析不出合理焦段(6-800mm),已忽略该路过滤")
    if not matched:
        note_bits.append("三路过滤零命中 — 词表规则不硬凑,换词或看 facets 里真实存在的选项")

    # 覆盖 KOL:扫描窗口内 distinct kol_pool_id(KPI 带真值;零推断,直读 join 行)
    kol_count = len({kid for row in rows if (kid := _int_or_none(row.get("kol_pool_id"))) is not None})

    return {
        "status": "ready",
        "method": "lexicon_segments_v1",
        "filters": {"query": query_token, "style": style_token, "focal": focal_token or focal_input},
        "scanned_videos": len(rows),
        "segment_count": len(all_segments),
        "kol_count": kol_count,
        "matched": len(matched),
        "returned": len(items),
        "items": items,
        "facets": _facets(all_segments),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": ";".join(note_bits) + "。独立展示信号,不参与 V6 Fit 评分。",
    }
