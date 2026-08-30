"""行业对照(Industry Benchmark)—「Viltrox 在行业里站在哪」竞品全景对照(纯读聚合)。

回答:窗口内(默认 90 天)每个品牌的声量全貌 + Viltrox vs 每竞品的正面对照 +
「竞品有声量而我方 SKU 覆盖弱」的焦段格局。

口径复用(绝不重造,全部守卫 import):
- 品牌口径 = app.domains.market.brand_pulse 同源:证据扫描(_evidence_rows)、
  深析补充文本(_deep_texts,刻意排除 brand_exposure 点评字段)、竞品词表
  (_competitor_vocab → competitor_brands.json)、Viltrox 词表(_viltrox_terms)、
  词边界匹配(_matcher)、周分桶(_week_starts/_monday)、半窗动量归一(_classify_trend);
  单位 = 视频×品牌,一条视频同品牌只记 1 次 —— SoV/rank 与 brand_pulse 完全同口径。
- 焦段口径 = app.domains.kol.focal_matrix 同源:_extract_focals(词表正则 6–800mm)、
  _load_products + _build_catalog(369 SKU 目录三视图)、_focal_sort_mm。
- 互动率口径 = signature_profile 同式:(赞+评)/播放,播放缺失/为 0 诚实 None 不外推。

新增聚合(本模块只做加法,不改任何口径):
- 每品牌:声量(提及视频数/独立 KOL 数/总播放/均播放)、声量份额+排名、
  半窗环比动量(采集密度归一)、内容质量侧写(被提及视频的均互动率,带样本数)、
  焦段覆盖(该品牌被提及视频文本里命中的焦段);
- focal_grid:竞品声量 × 我方 SKU 目录 的焦段格子,标出
  sku_weak(竞品有声量、我方该焦段零官方 SKU)与 voice_weak(我方有货但零声量);
- head_to_head:Viltrox vs 每竞品三行对比(声量/覆盖 KOL/均播放)+
  一句话差距描述(规则模板拼接,非 LLM,数字全部来自上面的聚合)。

诚实约定:一切数字带 basis/method/confidence;播放缺失不比均播放;互动样本 <5 标低置信;
窗口没数据回 status,绝不编造市场数据(只用库内真数据,不联网不引外部行情)。
compat:SQL 占位符 ?;零字面 percent;BOOLEAN 宽容(is_active IS NOT FALSE 在 SQL 端判)。
红线:全程只读 SELECT,零写库、零迁移、零 LLM/provider 外调;
绝不写 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger("viltrox.domains.market.industry_benchmark")

SCHEMA_VERSION = "industry_benchmark_v1"
DEFAULT_WINDOW_DAYS = 90
EXAMPLES_PER_BRAND = 3
MAX_VIDEOS = 20000
# 互动率样本护栏:少于该数标 low(不删数,标置信)。
MIN_ENGAGEMENT_SAMPLE = 5
# 整体置信分档(纯规则,阈值写死可追溯)。
CONF_HIGH_HITS, CONF_HIGH_SCANNED = 150, 500
CONF_MEDIUM_HITS = 30


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_views(n: int | None) -> str:
    """均播放的人话写法(verdict 模板用):12.3K / 1.2M;缺数回 '—'。"""
    if n is None:
        return "—"
    v = float(n)
    if v >= 1_000_000:
        return f"{round(v / 100_000) / 10}M"
    if v >= 1_000:
        return f"{round(v / 100) / 10}K"
    return str(int(n))


# ── 口径复用(守卫 import,失败诚实降级) ─────────────────────────


def _brand_pulse_mod():
    """brand_pulse 是本面板的口径地基;import 失败整体回 error(不硬造替代口径)。"""
    from app.domains.market import brand_pulse

    return brand_pulse


def _focal_tools() -> dict[str, Any] | None:
    """焦段词表/目录工具:复用 focal_matrix;失败回 None → focal_grid 诚实缺席。"""
    try:
        from app.domains.kol.focal_matrix import (
            _build_catalog,
            _extract_focals,
            _focal_sort_mm,
            _load_products,
        )

        return {
            "extract": _extract_focals,
            "sort_mm": _focal_sort_mm,
            "load_products": _load_products,
            "build_catalog": _build_catalog,
        }
    except Exception:
        logger.debug("industry_benchmark.focal_tools_import_failed", exc_info=True)
        return None


def _engagement_rate(views: Any, likes: Any, comments: Any) -> float | None:
    """(赞+评)/播放,同 signature_profile 口径;播放缺失/为 0 诚实 None。"""
    try:
        v = int(views) if views is not None and views != "" else None
    except (TypeError, ValueError):
        v = None
    if not v or v <= 0:
        return None
    inter = 0
    for raw in (likes, comments):
        try:
            inter += int(raw) if raw is not None and raw != "" else 0
        except (TypeError, ValueError):
            continue
    return round(inter / v, 5)


# ── 补充读取:互动列(brand_pulse 扫描集之外唯一的新查询,同 WHERE 口径) ──


def _engagement_map(conn: Any, start_day: str) -> dict[int, tuple[Any, Any]]:
    """窗口内 active 证据的 (like_count, comment_count),按 evidence_id 索引。

    WHERE 与 brand_pulse._evidence_rows 完全一致(is_active IS NOT FALSE +
    COALESCE(posted_at, publish_date) >= 窗口起点),保证互动样本 ⊆ 扫描集。
    占位符全 ?,SQL 零字面 percent。
    """
    try:
        rows = conn.execute(
            """
            SELECT
                e.id AS evidence_id,
                e.like_count,
                e.comment_count
            FROM vkpi_kol_video_evidence e
            WHERE e.is_active IS NOT FALSE
              AND COALESCE(e.posted_at, CAST(to_jsonb(e.*)->>'publish_date' AS DATE)) >= ?
            LIMIT ?
            """,
            (start_day, int(MAX_VIDEOS)),
        ).fetchall()
    except Exception:
        logger.debug("industry_benchmark.engagement_read_failed", exc_info=True)
        return {}
    out: dict[int, tuple[Any, Any]] = {}
    for row in rows:
        item = dict(row)
        try:
            eid = int(item.get("evidence_id"))
        except (TypeError, ValueError):
            continue
        out[eid] = (item.get("like_count"), item.get("comment_count"))
    return out


# ── head_to_head 一句话差距(规则模板拼接,非 LLM) ────────────────


def _verdict_sentence(brand: str, v: dict[str, Any], c: dict[str, Any]) -> str:
    """三指标(声量/覆盖 KOL/均播放)逐项比 → 固定模板拼一句话;数字全可追溯。"""
    lead: list[str] = []
    behind: list[str] = []
    tie: list[str] = []

    vv, cv = int(v["videos"]), int(c["videos"])
    if vv > cv:
        lead.append(f"声量领先({vv} vs {cv})")
    elif vv < cv:
        behind.append(f"声量落后({vv} vs {cv})")
    else:
        tie.append(f"声量持平({vv})")

    vk, ck = int(v["kol_count"]), int(c["kol_count"])
    if vk > ck:
        lead.append(f"KOL 覆盖更广({vk} vs {ck} 人)")
    elif vk < ck:
        behind.append(f"KOL 覆盖更窄({vk} vs {ck} 人)")
    else:
        tie.append(f"KOL 覆盖持平({vk} 人)")

    va, ca = v.get("avg_views"), c.get("avg_views")
    views_unknown = va is None or ca is None
    if not views_unknown:
        if int(va) > int(ca):
            lead.append(f"均播放更高({_fmt_views(int(va))} vs {_fmt_views(int(ca))})")
        elif int(va) < int(ca):
            behind.append(f"均播放更低({_fmt_views(int(va))} vs {_fmt_views(int(ca))})")
        else:
            tie.append(f"均播放持平({_fmt_views(int(va))})")

    parts = "、".join(lead + tie + behind)
    if not behind and lead:
        suffix = "—— 对该品牌保持压制。"
    elif behind and not lead:
        suffix = f"—— 全面落后,{brand} 是重点对标对象。"
    elif any(s.startswith("声量落后") for s in behind) and not any(s.startswith("均播放更低") for s in behind):
        suffix = "—— 单条表现不弱,差在提及量,可扩 KOL 覆盖面。"
    elif any(s.startswith("均播放更低") for s in behind) and not any(s.startswith("声量落后") for s in behind):
        suffix = "—— 对方单条内容更能跑量,差距在内容质量/选题。"
    else:
        suffix = "—— 互有胜负,按短板逐项补。"
    if views_unknown:
        suffix += "(均播放一侧缺播放数,该项不比)"
    return f"vs {brand}:{parts}{suffix}"


# ── 聚合阶段:把扫描、投影和焦段格局拆成可独立验证的纯阶段 ──────────


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _new_brand_slot() -> dict[str, Any]:
    return {
        "videos": 0,
        "kols": set(),
        "views": [],
        "eng": [],
        "weekly": {},
        "focals": {},
        "examples": [],
    }


def _matched_brands(
    *,
    title_blob: str,
    deep_blob: str,
    vocab: dict[str, Any],
    viltrox_terms: Any,
    match: Any,
) -> dict[str, str]:
    hits: dict[str, str] = {}
    if any(match(title_blob, term) for term in viltrox_terms):
        hits["viltrox"] = "title"
    elif deep_blob and any(match(deep_blob, term) for term in viltrox_terms):
        hits["viltrox"] = "deep_analysis"
    for brand, meta in vocab.items():
        keywords = meta.get("keywords") or []
        if any(match(title_blob, keyword) for keyword in keywords):
            hits[brand] = "title"
        elif deep_blob and any(match(deep_blob, keyword) for keyword in keywords):
            hits[brand] = "deep_analysis"
    return hits


def _row_text_blobs(
    row: dict[str, Any],
    deep_texts: dict[int, str],
) -> tuple[int, str, str]:
    title_blob = " ".join(
        part
        for part in (
            str(row.get("video_title") or ""),
            str(row.get("title_alt") or ""),
        )
        if part.strip()
    )
    evidence_id = _safe_int(row.get("evidence_id"))
    return evidence_id, title_blob, deep_texts.get(evidence_id, "")


def _row_hit_payload(
    *,
    row: dict[str, Any],
    day: date,
    evidence_id: int,
    title_blob: str,
    deep_blob: str,
    eng_map: dict[int, tuple[Any, Any]],
    vocab: dict[str, Any],
    viltrox_terms: Any,
    match: Any,
    focal_tools: dict[str, Any] | None,
) -> tuple[dict[str, str], int, int | None, float | None, set[str], dict[str, Any]] | None:
    hits = _matched_brands(
        title_blob=title_blob,
        deep_blob=deep_blob,
        vocab=vocab,
        viltrox_terms=viltrox_terms,
        match=match,
    )
    if not hits:
        return None
    kol_id = _safe_int(row.get("kol_pool_id"))
    views = _optional_int(row.get("view_count"))
    likes, comments = eng_map.get(evidence_id, (None, None))
    engagement_rate = _engagement_rate(views, likes, comments)
    focals = (
        focal_tools["extract"]((title_blob + " " + deep_blob).lower())
        if focal_tools is not None
        else set()
    )
    example = {
        "evidence_id": evidence_id or None,
        "title": (
            str(row.get("video_title") or "")
            or str(row.get("title_alt") or "")
        )[:160],
        "content_url": str(row.get("content_url") or ""),
        "platform": str(row.get("platform") or ""),
        "posted_at": day.isoformat(),
        "view_count": views,
        "kol_name": str(row.get("kol_name") or ""),
    }
    return hits, kol_id, views, engagement_rate, focals, example


def _record_brand_hit(
    slot: dict[str, Any],
    *,
    week: date,
    via: str,
    kol_id: int,
    views: int | None,
    engagement_rate: float | None,
    focals: set[str],
    example: dict[str, Any],
) -> None:
    slot["videos"] += 1
    slot["weekly"][week] = slot["weekly"].get(week, 0) + 1
    if kol_id:
        slot["kols"].add(kol_id)
    if views is not None:
        slot["views"].append(views)
    if engagement_rate is not None:
        slot["eng"].append(engagement_rate)
    for focal in focals:
        slot["focals"][focal] = slot["focals"].get(focal, 0) + 1
    slot["examples"].append({**example, "matched_via": via})


def _scan_brand_stats(
    *,
    bp: Any,
    rows: list[dict[str, Any]],
    deep_texts: dict[int, str],
    eng_map: dict[int, tuple[Any, Any]],
    vocab: dict[str, Any],
    viltrox_terms: Any,
    match: Any,
    focal_tools: dict[str, Any] | None,
    start_day: date,
    today: date,
) -> tuple[dict[str, dict[str, Any]], dict[date, int], int, int]:
    stats: dict[str, dict[str, Any]] = {}
    week_totals: dict[date, int] = {}
    videos_scanned = 0
    videos_with_text = 0
    for row in rows:
        day = bp._parse_day(row.get("pub_day"))
        if day is None or day < start_day or day > today:
            continue
        week = bp._monday(day)
        videos_scanned += 1
        week_totals[week] = week_totals.get(week, 0) + 1
        evidence_id, title_blob, deep_blob = _row_text_blobs(row, deep_texts)
        if not title_blob.strip() and not deep_blob.strip():
            continue
        videos_with_text += 1
        payload = _row_hit_payload(
            row=row,
            day=day,
            evidence_id=evidence_id,
            title_blob=title_blob,
            deep_blob=deep_blob,
            eng_map=eng_map,
            vocab=vocab,
            viltrox_terms=viltrox_terms,
            match=match,
            focal_tools=focal_tools,
        )
        if payload is None:
            continue
        hits, kol_id, views, engagement_rate, focals, example = payload
        for brand, via in hits.items():
            slot = stats.setdefault(brand, _new_brand_slot())
            _record_brand_hit(
                slot,
                week=week,
                via=via,
                kol_id=kol_id,
                views=views,
                engagement_rate=engagement_rate,
                focals=focals,
                example=example,
            )
    return stats, week_totals, videos_scanned, videos_with_text


def _brand_payload(
    *,
    bp: Any,
    key: str,
    stats: dict[str, dict[str, Any]],
    vocab: dict[str, Any],
    weeks: list[date],
    mid: int,
    prev_scanned: int,
    recent_scanned: int,
    sort_mm: Any,
) -> dict[str, Any]:
    slot = stats.get(key) or _new_brand_slot()
    meta = (
        vocab.get(key, {})
        if key != "viltrox"
        else {"brand_type": "self", "category": "lens", "priority": "self"}
    )
    weekly = [int(slot["weekly"].get(week, 0)) for week in weeks]
    prev_sum, recent_sum = sum(weekly[:mid]), sum(weekly[mid:])
    trend, momentum_pct = bp._classify_trend(
        prev_sum,
        recent_sum,
        prev_scanned,
        recent_scanned,
    )
    views: list[int] = slot["views"]
    engagement: list[float] = slot["eng"]
    focal_items = sorted(
        (
            {"focal": focal, "mm": sort_mm(focal), "video_count": int(count)}
            for focal, count in slot["focals"].items()
        ),
        key=lambda item: (-item["video_count"], item["mm"]),
    )
    top_examples = sorted(
        slot["examples"],
        key=lambda item: (
            -(int(item.get("view_count") or 0)),
            str(item.get("posted_at") or ""),
        ),
    )[:EXAMPLES_PER_BRAND]
    return {
        "key": key,
        "brand": bp._display_brand(key),
        "brand_type": str(meta.get("brand_type") or ""),
        "category": str(meta.get("category") or ""),
        "videos": int(slot["videos"]),
        "kol_count": len(slot["kols"]),
        "total_views": int(sum(views)) if views else None,
        "avg_views": int(round(sum(views) / len(views))) if views else None,
        "views_known": len(views),
        "trend": trend,
        "momentum_pct": momentum_pct,
        "prev_sum": int(prev_sum),
        "recent_sum": int(recent_sum),
        "engagement": {
            "avg_rate": (
                round(sum(engagement) / len(engagement), 5)
                if engagement
                else None
            ),
            "sample": len(engagement),
            "confidence": (
                "ok"
                if len(engagement) >= MIN_ENGAGEMENT_SAMPLE
                else ("low" if engagement else "none")
            ),
            "method": "该品牌被提及视频逐条 (赞+评)/播放 后取均值;播放缺失的视频不入样本",
        },
        "focals": focal_items,
        "top_examples": top_examples,
    }


def _rank_brand_payloads(
    viltrox: dict[str, Any],
    competitors: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    voice_total = int(viltrox["videos"]) + sum(
        int(competitor["videos"]) for competitor in competitors
    )
    ranked = sorted(
        [viltrox, *competitors],
        key=lambda item: (-int(item["videos"]), str(item["key"])),
    )
    for index, item in enumerate(ranked):
        item["rank"] = (index + 1) if int(item["videos"]) > 0 else None
        item["share_of_voice"] = (
            round(int(item["videos"]) / voice_total, 3)
            if voice_total > 0
            else None
        )
    viltrox["brand_count_ranked"] = len(ranked)
    return voice_total, ranked


def _head_to_head_rows(
    viltrox: dict[str, Any],
    competitors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "key": competitor["key"],
            "brand": competitor["brand"],
            "rows": [
                {
                    "metric": "voice_videos",
                    "label": "声量(提及视频)",
                    "viltrox": int(viltrox["videos"]),
                    "competitor": int(competitor["videos"]),
                },
                {
                    "metric": "kol_count",
                    "label": "覆盖 KOL(独立人数)",
                    "viltrox": int(viltrox["kol_count"]),
                    "competitor": int(competitor["kol_count"]),
                },
                {
                    "metric": "avg_views",
                    "label": "均播放(有播放数的提及视频)",
                    "viltrox": viltrox["avg_views"],
                    "competitor": competitor["avg_views"],
                },
            ],
            "verdict": _verdict_sentence(competitor["brand"], viltrox, competitor),
            "verdict_method": "rule_template_v1(三指标逐项比 + 固定后缀,非 LLM)",
        }
        for competitor in competitors
    ]


def _focal_grid_payload(
    *,
    conn: Any,
    focal_tools: dict[str, Any] | None,
    competitors: list[dict[str, Any]],
    viltrox: dict[str, Any],
    sort_mm: Any,
) -> dict[str, Any]:
    if focal_tools is None:
        return {
            "status": "empty",
            "reason": "focal_matrix 词表/目录工具不可用,焦段格局诚实缺席。",
        }
    try:
        catalog = focal_tools["build_catalog"](
            focal_tools["load_products"](conn)
        )["focals"]
    except Exception:
        logger.debug("industry_benchmark.catalog_read_failed", exc_info=True)
        catalog = {}
    competitor_focal_videos: dict[str, int] = {}
    competitor_focal_brands: dict[str, set[str]] = {}
    for competitor in competitors:
        for item in competitor["focals"]:
            focal = item["focal"]
            competitor_focal_videos[focal] = (
                competitor_focal_videos.get(focal, 0) + int(item["video_count"])
            )
            competitor_focal_brands.setdefault(focal, set()).add(
                str(competitor["brand"])
            )
    viltrox_focal_videos = {
        item["focal"]: int(item["video_count"]) for item in viltrox["focals"]
    }
    cells: list[dict[str, Any]] = []
    all_focals = set(competitor_focal_videos) | set(viltrox_focal_videos)
    for focal in sorted(all_focals, key=sort_mm):
        catalog_item = catalog.get(focal)
        competitor_videos = int(competitor_focal_videos.get(focal, 0))
        own_videos = int(viltrox_focal_videos.get(focal, 0))
        official = int(catalog_item["official_sku_count"]) if catalog_item else 0
        cells.append(
            {
                "focal": focal,
                "mm": sort_mm(focal),
                "competitor_videos": competitor_videos,
                "competitor_brands": sorted(
                    competitor_focal_brands.get(focal, set())
                )[:6],
                "viltrox_videos": own_videos,
                "in_catalog": bool(catalog_item),
                "sku_count": int(catalog_item["sku_count"]) if catalog_item else 0,
                "official_sku_count": official,
                "flagship": (
                    (catalog_item["flagship"] or None) if catalog_item else None
                ),
                "sku_weak": competitor_videos > 0 and official == 0,
                "voice_weak": (
                    official > 0 and competitor_videos >= 2 and own_videos == 0
                ),
            }
        )
    opportunities = sorted(
        (cell for cell in cells if cell["sku_weak"]),
        key=lambda cell: (-cell["competitor_videos"], cell["mm"]),
    )
    return {
        "status": "ready" if cells else "empty",
        **(
            {}
            if cells
            else {"reason": "窗口内品牌命中视频的文本里未提到任何具体焦段。"}
        ),
        "cells": cells,
        "opportunities": opportunities,
        "voice_weak_cells": [cell for cell in cells if cell["voice_weak"]],
        "catalog_focal_count": len(catalog),
        "method": "lexicon_focal_v1(标题+深析文本正则提焦段)× vkpi_products 目录官方 SKU 数;sku_weak=竞品有声量且我方零官方 SKU,voice_weak=有官方 SKU、竞品≥2 条声量而我方 0 条",
    }


def _status_and_confidence(
    *,
    videos_scanned: int,
    brand_hit_videos: int,
) -> tuple[str, dict[str, str]]:
    if videos_scanned == 0:
        status = "no_data_in_window"
    elif brand_hit_videos == 0:
        status = "no_brand_signal"
    else:
        status = "ok"
    if (
        brand_hit_videos >= CONF_HIGH_HITS
        and videos_scanned >= CONF_HIGH_SCANNED
    ):
        confidence = {
            "level": "high",
            "reason": f"命中 {brand_hit_videos}≥{CONF_HIGH_HITS} 且扫描 {videos_scanned}≥{CONF_HIGH_SCANNED}",
        }
    elif brand_hit_videos >= CONF_MEDIUM_HITS:
        confidence = {
            "level": "medium",
            "reason": f"命中 {brand_hit_videos}≥{CONF_MEDIUM_HITS},样本可用但非全景",
        }
    else:
        confidence = {
            "level": "low",
            "reason": f"命中仅 {brand_hit_videos} 条(<{CONF_MEDIUM_HITS}),结论仅供方向参考",
        }
    return status, confidence


# ── 主入口 ───────────────────────────────────────────────────────


def benchmark(window_days: int = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """行业对照全景:每品牌声量/份额/动量/质量侧写/焦段覆盖 + head_to_head + 焦段格局。"""
    try:
        bp = _brand_pulse_mod()
    except Exception as exc:  # noqa: BLE001 — 口径地基缺失,诚实报错不硬造
        return {"schema_version": SCHEMA_VERSION, "status": "error", "reason": f"brand_pulse unavailable: {exc}"[:300]}

    from app.db.connection import get_conn

    days = bp._clamp_window(window_days)
    today = datetime.now(timezone.utc).date()
    start_day = today - timedelta(days=days - 1)
    start_str = start_day.isoformat()
    weeks = bp._week_starts(start_day, today)
    mid = len(weeks) // 2

    conn = get_conn()
    rows = bp._evidence_rows(conn, start_str)
    deep_texts = bp._deep_texts(conn, start_str)
    eng_map = _engagement_map(conn, start_str)
    vocab = bp._competitor_vocab()
    viltrox_terms = bp._viltrox_terms()
    match = bp._matcher()
    focal_tools = _focal_tools()

    stats, week_totals, videos_scanned, videos_with_text = _scan_brand_stats(
        bp=bp,
        rows=rows,
        deep_texts=deep_texts,
        eng_map=eng_map,
        vocab=vocab,
        viltrox_terms=viltrox_terms,
        match=match,
        focal_tools=focal_tools,
        start_day=start_day,
        today=today,
    )

    prev_scanned = sum(int(week_totals.get(week, 0)) for week in weeks[:mid])
    recent_scanned = sum(int(week_totals.get(week, 0)) for week in weeks[mid:])

    # ── 每品牌 payload(声量/份额/动量/质量侧写/焦段覆盖) ──
    sort_mm = focal_tools["sort_mm"] if focal_tools is not None else (lambda s: 0.0)

    def payload_for(key: str) -> dict[str, Any]:
        return _brand_payload(
            bp=bp,
            key=key,
            stats=stats,
            vocab=vocab,
            weeks=weeks,
            mid=mid,
            prev_scanned=prev_scanned,
            recent_scanned=recent_scanned,
            sort_mm=sort_mm,
        )

    viltrox = payload_for("viltrox")
    competitors = [payload_for(key) for key in stats if key != "viltrox"]
    competitors.sort(key=lambda item: (-int(item["videos"]), str(item["key"])))

    # ── 份额 + 排名(视频×品牌口径,SoV 与 brand_pulse 同式:V/(V+ΣC)) ──
    voice_total, ranked = _rank_brand_payloads(viltrox, competitors)

    # ── head_to_head:Viltrox vs 每竞品三行 + 规则模板一句话 ──
    head_to_head = _head_to_head_rows(viltrox, competitors)

    # ── focal_grid:竞品声量 × 我方 SKU 目录(sku_weak / voice_weak 格子) ──
    focal_grid = _focal_grid_payload(
        conn=conn,
        focal_tools=focal_tools,
        competitors=competitors,
        viltrox=viltrox,
        sort_mm=sort_mm,
    )

    # ── 状态 + 整体置信(纯规则分档,阈值可追溯) ──
    brand_hit_videos = voice_total
    status, confidence = _status_and_confidence(
        videos_scanned=videos_scanned,
        brand_hit_videos=brand_hit_videos,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "window_days": days,
        "window_start": start_str,
        "window_end": today.isoformat(),
        "viltrox": viltrox,
        "competitors": competitors,
        "brand_count_ranked": len(ranked),
        "head_to_head": head_to_head,
        "focal_grid": focal_grid,
        "basis": {
            "videos_scanned": videos_scanned,
            "videos_with_text": videos_with_text,
            "deep_analyzed_in_window": len(deep_texts),
            "brand_hit_videos": brand_hit_videos,
            "engagement_rows_in_window": len(eng_map),
            "prev_half_scanned": prev_scanned,
            "recent_half_scanned": recent_scanned,
            "vocab_brands": len(vocab),
            "unit": "视频×品牌(一条视频同品牌只记 1 次),与 brand_pulse 同口径",
        },
        "confidence": confidence,
        "method": "brand_pulse 词表口径 + (赞+评)/播放 + lexicon_focal_v1;纯 SQL/规则聚合,决定性",
        "generated_at": _utcnow(),
        "provider_calls": False,
        "llm_calls": False,
        "note": (
            "行业对照:只用库内真数据(证据标题 + final_v1 深析文本 + SKU 目录),不联网不引外部行情;"
            "verdict 为规则模板拼接非 LLM;纯只读聚合,零写库零迁移,绝不参与 viltrox_fit_score。"
        ),
    }
