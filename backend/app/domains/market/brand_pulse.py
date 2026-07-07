"""品牌脉搏(Brand Pulse)— 全池 KOL 内容里品牌声量的周度趋势(纯读聚合)。

回答:「最近 N 天(默认 90)哪些品牌在 KOL 内容里升温/降温,Viltrox 声量相对位置」。

数据源(零新采集 / 零 LLM / 零写库,读端聚合):
- vkpi_kol_video_evidence:全池视频证据(标题 + 发布日期 + 播放数),标题词表扫描;
  发布日期主走真列 published_at_norm(迁移 216 把 to_jsonb 挖 publish_date 的口径转正:
  COALESCE(posted_at, publish_date) 回填 + 索引),列空的新行保底 COALESCE 落回旧路径;
- vkpi_analysis_cache 已 ready 的 video_analysis_final_v1:layer1 的 competitor_presence /
  content_summary 文本作为深析补充信号(brand_exposure 字段刻意排除 —— 该字段是分析员
  对 Viltrox 露出的点评,不管有没有露出都会写到 Viltrox,拿来匹配会系统性虚增自家声量)。

品牌识别口径(绝不重造词表,全部复用 #51 同源配置):
- 竞品词表:app.domains.kol.competitor_text.load_competitor_brands()
  (competitor_brands.json,含 keywords / priority / category / brand_type);
- Viltrox 自家词表:app.domains.market.brand_signal_detector.VILTROX_TERMS;
- 关键词匹配:app.domains.kol.competitor_text._keyword_match(词边界正则,同 #51)。

聚合口径:
- 单位 = 视频×品牌:一条视频同一品牌只记 1 次(标题命中或深析文本命中任一即算);
- 按周分桶(周一为周起点),窗口内没数据的周输出空桶并标 sparse=true,诚实不填数;
- 趋势 = 窗口前半 vs 后半对比,并按各半窗「实际扫描视频数」归一(采集密度校正:
  证据入库有波峰,不校正会把采集变密冒充全员升温):
  expected = prev_sum × (recent_scanned/prev_scanned),ratio = (recent_sum+1)/(expected+1);
  ratio≥1.5 且 recent≥2 → rising,ratio≤0.67 且 prev≥2 → falling,
  其余 stable(样本 <2 一律 stable,小样本不硬判);
- Viltrox 自身曲线单列,share_of_voice = V/(V+ΣC)(视频×品牌单位,与 #51 同口径),
  rank = 全部品牌按窗口内提及视频数排名。

红线(强约束):绝不写 viltrox_fit_score、绝不动 rule_v0;全程只读 SELECT,零写库、
零迁移、零 LLM/provider 外调;数据不足如实回 status,绝不杜撰。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger("viltrox.domains.market.brand_pulse")

SCHEMA_VERSION = "brand_pulse_v1"
# 窗口天数护栏:太短没有趋势意义,太长扫描面失控。
MIN_WINDOW_DAYS = 14
MAX_WINDOW_DAYS = 365
DEFAULT_WINDOW_DAYS = 90
# 全池窗口内最多扫描的证据行数(本地全表也就 ~3k,2 万留足余量)。
MAX_VIDEOS = 20000
# 输出品牌上限与每品牌例证视频数。
TOP_BRANDS = 20
EXAMPLES_PER_BRAND = 3
# 趋势判定的最小样本:前/后半窗命中视频数低于此值不给升降结论。
MIN_TREND_SAMPLE = 2

# 已知品牌的特殊显示写法(与 #51 competitor_exposure 保持一致);其余 .title()。
_BRAND_DISPLAY = {
    "ttartisan": "TTArtisan",
    "7artisans": "7Artisans",
    "smallhd": "SmallHD",
    "dji": "DJI",
    "smallrig": "SmallRig",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _display_brand(key: str) -> str:
    return _BRAND_DISPLAY.get(key, key.title() if key else key)


def _parse_day(value: Any) -> date | None:
    """'2026-06-28' / '2026-06-28T10:29:15+00:00' → date;解析失败回 None(该行跳过)。"""
    raw = str(value or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _clamp_window(window_days: Any) -> int:
    try:
        parsed = int(window_days or DEFAULT_WINDOW_DAYS)
    except (TypeError, ValueError):
        parsed = DEFAULT_WINDOW_DAYS
    return max(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, parsed))


# ── 词表复用(绝不重造) ─────────────────────────────────────────


def _competitor_vocab() -> dict[str, dict[str, Any]]:
    """竞品词表:复用 #51 同源 load_competitor_brands();读失败回空表(诚实少测)。"""
    try:
        from app.domains.kol.competitor_text import load_competitor_brands

        vocab: dict[str, dict[str, Any]] = {}
        for brand, cfg in load_competitor_brands().items():
            keywords = [kw for kw in [brand, *(cfg.get("keywords") or [])] if str(kw or "").strip()]
            vocab[brand] = {
                "keywords": keywords,
                "priority": str(cfg.get("priority") or ""),
                "category": str(cfg.get("category") or ""),
                "brand_type": str(cfg.get("brand_type") or ""),
            }
        return vocab
    except Exception:
        logger.debug("brand_pulse.competitor_vocab_read_failed", exc_info=True)
        return {}


def _viltrox_terms() -> list[str]:
    """Viltrox 自家词表:复用 brand_signal_detector.VILTROX_TERMS;失败回最小核心词。"""
    try:
        from app.domains.market.brand_signal_detector import VILTROX_TERMS

        return [str(term) for term in VILTROX_TERMS if str(term or "").strip()]
    except Exception:
        return ["viltrox"]


def _matcher():
    """词边界匹配函数:复用 competitor_text._keyword_match;失败回子串包含(宽容降级)。"""
    try:
        from app.domains.kol.competitor_text import _keyword_match

        return _keyword_match
    except Exception:
        return lambda text, kw: str(kw or "").lower() in str(text or "").lower()


# ── 数据读取(纯只读 SELECT) ────────────────────────────────────


def _evidence_rows(conn: Any, start_day: str) -> list[dict[str, Any]]:
    """窗口内全池 active 证据行:标题 + 日期 + 例证所需字段。

    日期主走真列 published_at_norm(迁移 216 转正:COALESCE(posted_at, publish_date)
    回填 + 索引);列为空的新行保底 COALESCE 落回旧路径
    (posted_at → to_jsonb 挖 publish_date),绝不漏行。占位符全 ?,SQL 里零字面 %。
    """
    rows = conn.execute(
        """
        SELECT
            e.id AS evidence_id,
            e.kol_pool_id,
            e.platform,
            e.content_url,
            e.view_count,
            COALESCE(e.video_title, '') AS video_title,
            to_jsonb(e.*)->>'title' AS title_alt,
            CAST(COALESCE(e.published_at_norm, e.posted_at, CAST(to_jsonb(e.*)->>'publish_date' AS DATE)) AS TEXT) AS pub_day,
            COALESCE(p.display_name, '') AS kol_name
        FROM vkpi_kol_video_evidence e
        LEFT JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.is_active IS NOT FALSE
          AND COALESCE(e.published_at_norm, e.posted_at, CAST(to_jsonb(e.*)->>'publish_date' AS DATE)) >= ?
        ORDER BY e.id DESC
        LIMIT ?
        """,
        (start_day, int(MAX_VIDEOS)),
    ).fetchall()
    return [dict(row) for row in rows]


def _deep_texts(conn: Any, start_day: str) -> dict[int, str]:
    """窗口内已深析(final_v1 ready)视频的补充文本:competitor_presence + content_summary。

    刻意不取 layer1 的 brand_exposure:该字段永远在点评 Viltrox 露出与否,
    拿来做词表匹配会把「画面内零露出」也算成 Viltrox 声量,系统性虚增自家。
    """
    try:
        rows = conn.execute(
            """
            SELECT
                CAST(ac.target_id AS BIGINT) AS evidence_id,
                CAST(ac.result #> '{layer1_visual_content,competitor_presence}' AS TEXT) AS competitor_presence,
                CAST(ac.result #> '{raw_gemini_video,content_summary}' AS TEXT) AS content_summary
            FROM vkpi_analysis_cache ac
            JOIN vkpi_kol_video_evidence e ON e.id = CAST(ac.target_id AS BIGINT)
            WHERE ac.target_type = 'video'
              AND ac.derive_method = 'video_analysis_final_v1'
              AND ac.status = 'ready'
              AND e.is_active IS NOT FALSE
              AND COALESCE(e.published_at_norm, e.posted_at, CAST(to_jsonb(e.*)->>'publish_date' AS DATE)) >= ?
            LIMIT ?
            """,
            (start_day, int(MAX_VIDEOS)),
        ).fetchall()
    except Exception:
        logger.debug("brand_pulse.deep_texts_read_failed", exc_info=True)
        return {}
    texts: dict[int, str] = {}
    for row in rows:
        item = dict(row)
        try:
            eid = int(item.get("evidence_id"))
        except (TypeError, ValueError):
            continue
        blob = " ".join(
            str(item.get(key) or "")
            for key in ("competitor_presence", "content_summary")
            if str(item.get(key) or "").strip() not in {"", "null", '""'}
        )
        if blob.strip():
            texts[eid] = blob
    return texts


# ── 周分桶 + 趋势 ────────────────────────────────────────────────


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _week_starts(start_day: date, end_day: date) -> list[date]:
    weeks: list[date] = []
    cursor = _monday(start_day)
    last = _monday(end_day)
    while cursor <= last:
        weeks.append(cursor)
        cursor += timedelta(days=7)
    return weeks


def _classify_trend(prev_sum: int, recent_sum: int, prev_scanned: int, recent_scanned: int) -> tuple[str, int]:
    """前半窗 vs 后半窗 → (trend, momentum_pct),按各半窗扫描量归一。

    expected = prev_sum × (recent_scanned/prev_scanned):校正采集密度差
    (证据入库有波峰,不校正会把「采集变密」冒充全员升温);
    ratio = (recent+1)/(expected+1) 平滑防零除。样本不够(两半命中都 <MIN_TREND_SAMPLE)
    一律 stable —— 小样本不硬判升降。
    """
    prev_scanned = max(1, int(prev_scanned))
    recent_scanned = max(1, int(recent_scanned))
    expected = prev_sum * (recent_scanned / prev_scanned)
    ratio = (recent_sum + 1.0) / (expected + 1.0)
    momentum_pct = int(round((recent_sum - expected) / max(expected, 1.0) * 100))
    if ratio >= 1.5 and recent_sum >= MIN_TREND_SAMPLE:
        return "rising", momentum_pct
    if ratio <= 0.67 and prev_sum >= MIN_TREND_SAMPLE:
        return "falling", momentum_pct
    return "stable", momentum_pct


def _brand_payload(
    key: str,
    meta: dict[str, Any],
    weekly_counts: dict[date, int],
    kol_ids: set[int],
    examples: list[dict[str, Any]],
    weeks: list[date],
    mid: int,
    prev_scanned: int,
    recent_scanned: int,
) -> dict[str, Any]:
    weekly = [int(weekly_counts.get(week, 0)) for week in weeks]
    prev_sum = sum(weekly[:mid])
    recent_sum = sum(weekly[mid:])
    trend, momentum_pct = _classify_trend(prev_sum, recent_sum, prev_scanned, recent_scanned)
    top_examples = sorted(
        examples,
        key=lambda item: (-(int(item.get("view_count") or 0)), str(item.get("posted_at") or "")),
    )[:EXAMPLES_PER_BRAND]
    return {
        "key": key,
        "brand": _display_brand(key),
        "brand_type": str(meta.get("brand_type") or ""),
        "category": str(meta.get("category") or ""),
        "priority": str(meta.get("priority") or ""),
        "total_videos": int(sum(weekly)),
        "kol_count": len(kol_ids),
        "weekly": weekly,
        "prev_sum": int(prev_sum),
        "recent_sum": int(recent_sum),
        "trend": trend,
        "momentum_pct": momentum_pct,
        "top_examples": top_examples,
    }


# ── 对外入口 ─────────────────────────────────────────────────────


def get_brand_pulse(window_days: int = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """全池品牌脉搏:窗口内按周分桶的品牌提及趋势 + Viltrox 相对位置(纯只读)。"""
    days = _clamp_window(window_days)
    today = datetime.now(timezone.utc).date()
    start_day = today - timedelta(days=days - 1)
    start_str = start_day.isoformat()
    weeks = _week_starts(start_day, today)
    mid = len(weeks) // 2

    conn = get_conn()
    rows = _evidence_rows(conn, start_str)
    deep_texts = _deep_texts(conn, start_str)
    vocab = _competitor_vocab()
    viltrox_terms = _viltrox_terms()
    match = _matcher()

    videos_scanned = 0
    videos_with_text = 0
    week_totals: dict[date, int] = {}
    week_brand_videos: dict[date, int] = {}
    brand_weekly: dict[str, dict[date, int]] = {}
    brand_kols: dict[str, set[int]] = {}
    brand_examples: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        day = _parse_day(row.get("pub_day"))
        if day is None or day < start_day or day > today:
            continue
        week = _monday(day)
        videos_scanned += 1
        week_totals[week] = week_totals.get(week, 0) + 1

        title_blob = " ".join(
            part for part in (str(row.get("video_title") or ""), str(row.get("title_alt") or "")) if part.strip()
        )
        deep_blob = deep_texts.get(int(row.get("evidence_id") or 0), "")
        if not title_blob.strip() and not deep_blob.strip():
            continue
        videos_with_text += 1

        hits: dict[str, str] = {}  # brand key → matched_via
        if any(match(title_blob, term) for term in viltrox_terms):
            hits["viltrox"] = "title"
        elif deep_blob and any(match(deep_blob, term) for term in viltrox_terms):
            hits["viltrox"] = "deep_analysis"
        for brand, meta in vocab.items():
            keywords = meta.get("keywords") or []
            if any(match(title_blob, kw) for kw in keywords):
                hits[brand] = "title"
            elif deep_blob and any(match(deep_blob, kw) for kw in keywords):
                hits[brand] = "deep_analysis"

        if not hits:
            continue
        week_brand_videos[week] = week_brand_videos.get(week, 0) + 1
        try:
            kol_id = int(row.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            kol_id = 0
        example = {
            "evidence_id": row.get("evidence_id"),
            "title": (str(row.get("video_title") or "") or str(row.get("title_alt") or ""))[:160],
            "content_url": str(row.get("content_url") or ""),
            "platform": str(row.get("platform") or ""),
            "posted_at": day.isoformat(),
            "view_count": row.get("view_count"),
            "kol_pool_id": kol_id or None,
            "kol_name": str(row.get("kol_name") or ""),
        }
        for brand, via in hits.items():
            counts = brand_weekly.setdefault(brand, {})
            counts[week] = counts.get(week, 0) + 1
            if kol_id:
                brand_kols.setdefault(brand, set()).add(kol_id)
            brand_examples.setdefault(brand, []).append({**example, "matched_via": via})

    # ── Viltrox 单列 + 竞品榜(趋势按半窗扫描量归一,校正采集密度差) ──
    prev_scanned = sum(int(week_totals.get(week, 0)) for week in weeks[:mid])
    recent_scanned = sum(int(week_totals.get(week, 0)) for week in weeks[mid:])
    viltrox_meta = {"brand_type": "self", "category": "lens", "priority": "self"}
    viltrox = _brand_payload(
        "viltrox",
        viltrox_meta,
        brand_weekly.get("viltrox", {}),
        brand_kols.get("viltrox", set()),
        brand_examples.get("viltrox", []),
        weeks,
        mid,
        prev_scanned,
        recent_scanned,
    )

    competitor_payloads = [
        _brand_payload(
            key,
            vocab.get(key, {}),
            brand_weekly[key],
            brand_kols.get(key, set()),
            brand_examples.get(key, []),
            weeks,
            mid,
            prev_scanned,
            recent_scanned,
        )
        for key in brand_weekly
        if key != "viltrox"
    ]
    competitor_payloads.sort(key=lambda item: (-int(item["total_videos"]), str(item["key"])))
    competitor_payloads = competitor_payloads[:TOP_BRANDS]

    # Viltrox 相对位置:rank 按窗口内提及视频数(含自家)排;SoV 与 #51 视频×品牌口径同源。
    all_totals = sorted(
        [int(viltrox["total_videos"]), *(int(item["total_videos"]) for item in competitor_payloads)],
        reverse=True,
    )
    viltrox_rank = (all_totals.index(int(viltrox["total_videos"])) + 1) if int(viltrox["total_videos"]) > 0 else None
    competitor_total = sum(int(item["total_videos"]) for item in competitor_payloads)
    voice_total = int(viltrox["total_videos"]) + competitor_total
    share_of_voice = round(int(viltrox["total_videos"]) / voice_total, 3) if voice_total > 0 else None
    viltrox["share_of_voice"] = share_of_voice
    viltrox["rank"] = viltrox_rank
    viltrox["brand_count_ranked"] = len(all_totals)

    groups = {
        "rising": [item["key"] for item in competitor_payloads if item["trend"] == "rising"],
        "falling": [item["key"] for item in competitor_payloads if item["trend"] == "falling"],
        "stable": [item["key"] for item in competitor_payloads if item["trend"] == "stable"],
    }

    week_buckets = []
    sparse_weeks = 0
    for week in weeks:
        scanned = int(week_totals.get(week, 0))
        sparse = scanned == 0
        if sparse:
            sparse_weeks += 1
        week_buckets.append(
            {
                "week_start": week.isoformat(),
                "videos_scanned": scanned,
                "brand_videos": int(week_brand_videos.get(week, 0)),
                "sparse": sparse,
            }
        )

    if videos_scanned == 0:
        status = "no_data_in_window"
    elif voice_total == 0:
        status = "no_brand_signal"
    else:
        status = "ok"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "window_days": days,
        "window_start": start_str,
        "window_end": today.isoformat(),
        "weeks": week_buckets,
        "viltrox": viltrox,
        "brands": competitor_payloads,
        "groups": groups,
        "coverage": {
            "videos_scanned": videos_scanned,
            "prev_half_scanned": prev_scanned,
            "recent_half_scanned": recent_scanned,
            "videos_with_text": videos_with_text,
            "deep_analyzed_in_window": len(deep_texts),
            "brand_hit_videos": sum(week_brand_videos.values()),
            "sparse_weeks": sparse_weeks,
            "vocab_brands": len(vocab),
        },
        "generated_at": _utcnow(),
        "provider_calls": False,
        "llm_calls": False,
        "note": "品牌脉搏:视频×品牌口径,标题词表 + final_v1 深析文本(排除 brand_exposure 点评字段);纯只读聚合,零写库零 LLM,绝不参与 viltrox_fit_score。",
    }
