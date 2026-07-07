"""G3 评论区营销机会雷达(comment_opportunities v0)—— 反向用评论管线:
「官号值得去哪些高热视频下评论」的 Top5 机会清单(纯读聚合,零 LLM 零采集零写库)。

回答:最近 N 天(默认 7)库内哪些视频最值得官号去评论区互动 ——
视频热度(播放/互动)× 主题相关(焦段/摄影词表命中)× 竞品语境(brand_pulse
同源词表命中=可借势)× 新鲜度,四因子加权出分,Top5 带「为何值得去」规则模板
与「建议评论角度」词表模板(非 LLM 生成)。

数据源(全只读 SELECT):
- vkpi_kol_video_evidence:窗口内 active 证据(标题/播放/点赞/评论数);
  发布日期主走真列 published_at_norm(迁移 216),空行保底 COALESCE 落回
  posted_at → to_jsonb 挖 publish_date(与 brand_pulse 同口径,绝不漏行);
- vkpi_kol_pool:KOL 展示名(LEFT JOIN,缺失如实空)。

词表复用(绝不重造):
- 焦段提取:app.domains.kol.focal_matrix._extract_focals(负语境感知,同 #焦段矩阵);
- 竞品词表:app.domains.market.brand_pulse._competitor_vocab()(#51 同源配置);
- Viltrox 自家词表:brand_pulse._viltrox_terms();
- 词边界匹配:brand_pulse._matcher()(competitor_text._keyword_match 同款);
- 摄影主题词表:本模块常量 TOPIC_TERMS(主题 key → 关键词 → 评论角度模板)。

评分口径(决定性,同输入同输出;规则原文进 basis 可追溯):
- heat 热度(权重 0.35)= 0.7×播放对数归一(log10(views+1)/log10(max+1))
  + 0.3×互动率归一(min(1, er/0.10),er=(赞+评)/播放);
- relevance 主题相关(0.25)= 焦段命中 0.6 + 每个主题类命中 0.2(封顶 0.4);
- competitor 竞品语境(0.20)= 命中即 0.5,每多一个品牌 +0.25(封顶 1.0);
- freshness 新鲜度(0.20)= max(0, 1 - 距今天数/窗口天数);
- 总分 = 100×加权和,tie-break evidence_id 升序。

红线(强约束):只出机会清单,绝!不!自动发评论 —— 一切评论由人工撰写、人工发布;
本模块无任何写路径/外调;零触 viltrox_fit_score / rule_v0;数据不足诚实空态绝不编。
compat 约定:SQL 占位符 ?;SQL 零字面 percent(词表匹配全在 Python 侧);
BOOLEAN 宽容判真(is_active IS NOT FALSE);函数内懒 import get_conn。
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "comment_opportunities_v0"

# 窗口护栏:默认近 7 天;上限 180 天(本地库有断更期,放宽给人工调窗)。
MIN_DAYS = 1
MAX_DAYS = 180
DEFAULT_DAYS = 7
# 窗口内最多扫描的证据行数(与 brand_pulse.MAX_VIDEOS 同量级护栏)。
MAX_VIDEOS = 20000
# 输出机会条数。
TOP_N = 5
# 「评论区活跃」阈值:已有评论数 >= 此值,提示提问式互动易获曝光。
ACTIVE_COMMENTS_MIN = 50
# 自家官号在 pool 里的标记(vkpi_kol_pool.source_type)—— 官号自己的视频不算机会。
OWN_BRAND_SOURCE_TYPE = "own_brand_excluded"

# 四因子权重(和为 1;规则原文进 basis)。
W_HEAT, W_RELEVANCE, W_COMPETITOR, W_FRESHNESS = 0.35, 0.25, 0.20, 0.20
# 互动率归一分母:er >= 10% 记满分(镜头类内容 10% 已属爆款互动)。
ER_FULL = 0.10

# ── 摄影主题词表(key, 中文标签, 关键词, 评论角度模板)——词表模板非 LLM ──
TOPIC_TERMS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("portrait", "人像", ("portrait", "portraits", "headshot", "人像", "肖像"),
     "该视频讨论人像拍摄——可评肤色还原/焦外过渡,分享同焦段人像参数互动"),
    ("bokeh", "虚化/焦外", ("bokeh", "虚化", "焦外"),
     "该视频聊焦外虚化——可评光圈全开的焦外形态,邀作者聊光斑口味"),
    ("review", "评测/对比", ("review", "reviews", "comparison", "vs", "评测", "测评", "对比", "横评"),
     "评测/对比语境——可补充规格视角(重量/对焦/价格比),中立参与讨论"),
    ("filmmaking", "视频/电影感", ("cinematic", "filmmaking", "b-roll", "broll", "vlog", "短片", "电影感", "运镜"),
     "视频创作语境——可评运镜与呼吸效应控制,聊视频拍摄镜头选择"),
    ("lowlight", "弱光/夜拍", ("low light", "lowlight", "night", "astro", "夜拍", "弱光", "星空"),
     "弱光/夜拍语境——可评大光圈进光量与高感控噪的实拍体验"),
    ("autofocus", "自动对焦", ("autofocus", "eye af", "af speed", "自动对焦", "追焦", "对焦"),
     "对焦话题——可评人眼追焦/呼吸补偿表现,提问作者实拍成功率"),
    ("budget", "性价比", ("budget", "affordable", "cheap", "性价比", "预算", "平价"),
     "性价比语境——可评「同规格更亲民」的选购视角,不贬踩他牌"),
)

# 兜底评论角度(词表零命中时;泛互动,不硬贴产品)。
FALLBACK_ANGLE = "高播放泛摄影内容——轻互动:赞赏作品 + 专业视角提问,不硬贴产品"

REDLINE_NOTE = (
    "本接口只输出「值得去评论」的机会清单与人工参考角度,绝不自动发评论;"
    "一切评论由人工撰写、人工发布(官号口径需人审)。"
)

SCORE_RULE_TEXT = (
    "score = 100 × (0.35×heat + 0.25×relevance + 0.20×competitor + 0.20×freshness);"
    "heat = 0.7×log10(views+1)/log10(max_views+1) + 0.3×min(1, engagement_rate/0.10);"
    "relevance = 焦段命中0.6 + 每主题类0.2(封顶0.4);"
    "competitor = 命中0.5 + 每多一品牌0.25(封顶1.0);"
    "freshness = max(0, 1 - age_days/window_days);tie-break evidence_id 升序。"
)


# ── 小工具 ───────────────────────────────────────────────────────────


def _text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_day(value: Any) -> date | None:
    raw = str(value or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _clamp_days(days: Any) -> int:
    try:
        parsed = int(days or DEFAULT_DAYS)
    except (TypeError, ValueError):
        parsed = DEFAULT_DAYS
    return max(MIN_DAYS, min(MAX_DAYS, parsed))


# ── 词表装载(全部守卫 import 复用兄弟件,失败诚实降级) ───────────────


def _focal_extractor():
    """焦段提取:复用 focal_matrix._extract_focals(负语境感知);失败回空提取。"""
    try:
        from app.domains.kol.focal_matrix import _extract_focals

        return _extract_focals
    except Exception:
        logger.debug("comment_opportunities.focal_extractor_load_failed", exc_info=True)
        return lambda blob: set()


def _competitor_vocab() -> dict[str, dict[str, Any]]:
    """竞品词表:复用 brand_pulse._competitor_vocab(#51 同源);失败回空表。"""
    try:
        from app.domains.market.brand_pulse import _competitor_vocab

        return _competitor_vocab()
    except Exception:
        logger.debug("comment_opportunities.competitor_vocab_load_failed", exc_info=True)
        return {}


def _viltrox_terms() -> list[str]:
    try:
        from app.domains.market.brand_pulse import _viltrox_terms

        return _viltrox_terms()
    except Exception:
        return ["viltrox"]


def _matcher():
    """词边界匹配:复用 brand_pulse._matcher(competitor_text 同款);失败回子串包含。"""
    try:
        from app.domains.market.brand_pulse import _matcher

        return _matcher()
    except Exception:
        return lambda text, kw: str(kw or "").lower() in str(text or "").lower()


# ── 数据读取(纯只读 SELECT;日期口径与 brand_pulse 同款) ─────────────


def _evidence_rows(db: Any, start_day: str) -> list[dict[str, Any]]:
    """窗口内全池 active 证据行:标题 + 播放/互动 + 发布日 + KOL 名。

    发布日期主走真列 published_at_norm(迁移 216);空行保底 COALESCE 落回
    posted_at → to_jsonb 挖 publish_date。占位符全 ?,SQL 零字面 percent。
    """
    rows = db.execute(
        """
        SELECT
            e.id AS evidence_id,
            e.kol_pool_id,
            e.platform,
            e.content_url,
            e.view_count,
            e.like_count,
            e.comment_count,
            COALESCE(e.video_title, '') AS video_title,
            to_jsonb(e.*)->>'title' AS title_alt,
            CAST(COALESCE(e.published_at_norm, e.posted_at, CAST(to_jsonb(e.*)->>'publish_date' AS DATE)) AS TEXT) AS pub_day,
            COALESCE(p.display_name, '') AS kol_name,
            COALESCE(p.handle, '') AS kol_handle,
            COALESCE(p.source_type, '') AS kol_source_type
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


def _newest_pub_day(db: Any) -> str | None:
    """全库最新发布日(空窗口时如实告诉人「数据只到哪天」,不留悬念)。"""
    try:
        row = db.execute(
            """
            SELECT CAST(MAX(COALESCE(e.published_at_norm, e.posted_at)) AS TEXT) AS max_day
            FROM vkpi_kol_video_evidence e
            WHERE e.is_active IS NOT FALSE
            """
        ).fetchone()
        return _text((dict(row) or {}).get("max_day"), 30) or None if row else None
    except Exception:
        return None


# ── 单视频信号提取 + 打分(决定性规则) ───────────────────────────────


def _signals_for(
    blob: str, extract_focals: Any, match: Any,
    comp_vocab: dict[str, dict[str, Any]], viltrox_terms: list[str],
) -> dict[str, Any]:
    """标题词表信号:焦段 / 主题类 / 竞品品牌 / 自家提及。全 Python 侧匹配。"""
    focals = sorted(extract_focals(blob))
    topics: list[dict[str, str]] = []
    for key, label, keywords, angle in TOPIC_TERMS:
        if any(match(blob, kw) for kw in keywords):
            topics.append({"key": key, "label": label, "angle": angle})
    brands = sorted(
        brand for brand, cfg in comp_vocab.items()
        if any(match(blob, kw) for kw in (cfg.get("keywords") or []))
    )
    viltrox_mentioned = any(match(blob, term) for term in viltrox_terms)
    return {
        "focal_hits": focals,
        "topic_hits": topics,
        "competitor_hits": brands,
        "viltrox_mentioned": bool(viltrox_mentioned),
    }


def _score_parts(
    views: int, likes: int | None, comments: int | None,
    signals: dict[str, Any], age_days: int, window_days: int, max_views: int,
) -> dict[str, Any]:
    """四因子拆分(每个分量 0..1,原始输入一并回带,可复算可追溯)。"""
    views_norm = (
        math.log10(views + 1) / math.log10(max_views + 1) if max_views > 0 else 0.0
    )
    interactions = (likes or 0) + (comments or 0)
    er = (interactions / views) if views > 0 else 0.0
    heat = 0.7 * views_norm + 0.3 * min(1.0, er / ER_FULL)
    relevance = (0.6 if signals["focal_hits"] else 0.0) + min(0.4, 0.2 * len(signals["topic_hits"]))
    n_brands = len(signals["competitor_hits"])
    competitor = min(1.0, 0.5 + 0.25 * (n_brands - 1)) if n_brands > 0 else 0.0
    freshness = max(0.0, 1.0 - (age_days / window_days)) if window_days > 0 else 0.0
    total = 100.0 * (W_HEAT * heat + W_RELEVANCE * relevance + W_COMPETITOR * competitor + W_FRESHNESS * freshness)
    return {
        "total": round(total, 1),
        "heat": round(heat, 4),
        "relevance": round(min(1.0, relevance), 4),
        "competitor": round(competitor, 4),
        "freshness": round(freshness, 4),
        "engagement_rate": round(er, 4) if views > 0 else None,
        "views_norm": round(views_norm, 4),
        "age_days": int(age_days),
    }


def _why_text(parts: dict[str, Any], signals: dict[str, Any], views: int, window_days: int) -> str:
    """「为何值得去」规则模板:只陈述命中的事实,没命中的因子不凑字。"""
    reasons: list[str] = [
        f"近{window_days}天高热度:播放 {views:,}"
        + (f",互动率 {parts['engagement_rate'] * 100:.1f}%" if parts.get("engagement_rate") else "")
    ]
    if signals["focal_hits"]:
        reasons.append("命中焦段词表:" + " / ".join(signals["focal_hits"][:4]))
    if signals["topic_hits"]:
        reasons.append("命中摄影主题:" + " / ".join(t["label"] for t in signals["topic_hits"][:3]))
    if signals["competitor_hits"]:
        reasons.append(
            "竞品语境可借势:" + " / ".join(signals["competitor_hits"][:3])
            + "(该视频受众正在看同类产品)"
        )
    if signals["viltrox_mentioned"]:
        reasons.append("视频已提到 Viltrox,官号现身承接顺理成章")
    if parts["age_days"] <= max(2, window_days // 3):
        reasons.append(f"发布仅 {parts['age_days']} 天,评论区仍在长尾曝光期")
    return ";".join(reasons)


def _comment_angles(signals: dict[str, Any], comments: int | None) -> list[str]:
    """建议评论角度(词表模板拼装,非 LLM):按命中信号逐条给人工参考。"""
    angles: list[str] = []
    if signals["focal_hits"]:
        focal = signals["focal_hits"][0]
        topic = signals["topic_hits"][0]["label"] if signals["topic_hits"] else "实拍"
        angles.append(f"该视频讨论 {focal} {topic}——可评镜头参数互动(光圈/对焦/焦外表现)")
    for t in signals["topic_hits"][:3]:
        angles.append(t["angle"])
    if signals["competitor_hits"]:
        angles.append(
            "视频语境提到 " + " / ".join(signals["competitor_hits"][:3])
            + "——可礼貌补充 Viltrox 同规格选项供对比参考(借势不贬踩)"
        )
    if signals["viltrox_mentioned"]:
        angles.append("视频已提到 Viltrox——官号致谢 + 补充产品信息,顺手承接评论区提问")
    if (comments or 0) >= ACTIVE_COMMENTS_MIN:
        angles.append(f"评论区活跃({comments} 条)——提问式互动更易获回复与曝光")
    if not angles:
        angles.append(FALLBACK_ANGLE)
    # 去重保序(不同信号可能拼出同句)。
    seen: set[str] = set()
    return [a for a in angles if not (a in seen or seen.add(a))]


# ── 主入口 ──────────────────────────────────────────────────────────


def opportunities(days: int = DEFAULT_DAYS) -> dict[str, Any]:
    """近 N 天「官号值得去评论」Top5 机会清单(全只读;决定性:同输入同输出)。"""
    window_days = _clamp_days(days)
    today = datetime.now(timezone.utc).date()
    start_day = (today - timedelta(days=window_days)).isoformat()

    from app.db.connection import get_conn

    db = get_conn()
    rows = _evidence_rows(db, start_day)

    base = {
        "status": "ready",
        "method": METHOD,
        "days": window_days,
        "window_start": start_day,
        "window_end": today.isoformat(),
        "generated_at": _utcnow_iso(),
        "llm_calls": False,
        "provider_calls": False,
        "redline": REDLINE_NOTE,
    }

    if not rows:
        newest = _newest_pub_day(db)
        return {
            **base,
            "status": "empty",
            "reason": (
                f"近 {window_days} 天窗口内无入库视频证据"
                + (f"(库内最新发布日 {newest[:10]},可加大 days 调窗)" if newest else "")
                + " — 诚实空态,不编机会。"
            ),
            "coverage": {"videos_scanned": 0, "scored": 0, "skipped_no_views": 0, "skipped_no_url": 0, "skipped_own_brand": 0},
            "items": [],
        }

    extract_focals = _focal_extractor()
    match = _matcher()
    comp_vocab = _competitor_vocab()
    viltrox_terms = _viltrox_terms()

    # 第一遍:取可打分行(有 URL 且有播放数)+ 窗口内最大播放(对数归一分母)。
    scorable: list[dict[str, Any]] = []
    skipped_no_views = skipped_no_url = skipped_own_brand = 0
    for row in rows:
        # 自家官号的视频不进机会清单:官号没道理去自己视频下「借势评论」。
        if _text(row.get("kol_source_type"), 60) == OWN_BRAND_SOURCE_TYPE:
            skipped_own_brand += 1
            continue
        url = _text(row.get("content_url"), 500)
        views = _int_or_none(row.get("view_count"))
        if not url:
            skipped_no_url += 1
            continue
        if views is None or views <= 0:
            skipped_no_views += 1
            continue
        pub = _parse_day(row.get("pub_day"))
        if pub is None:
            continue
        scorable.append({**row, "_url": url, "_views": int(views), "_pub": pub})
    max_views = max((r["_views"] for r in scorable), default=0)

    items: list[dict[str, Any]] = []
    for row in scorable:
        title = _text(row.get("video_title"), 300) or _text(row.get("title_alt"), 300)
        blob = title
        likes = _int_or_none(row.get("like_count"))
        comments = _int_or_none(row.get("comment_count"))
        age_days = max(0, (today - row["_pub"]).days)
        signals = _signals_for(blob, extract_focals, match, comp_vocab, viltrox_terms)
        parts = _score_parts(row["_views"], likes, comments, signals, age_days, window_days, max_views)
        items.append(
            {
                "evidence_id": _int_or_none(row.get("evidence_id")),
                "title": title or "(无标题)",
                "content_url": row["_url"],
                "platform": _text(row.get("platform"), 30),
                "kol_pool_id": _int_or_none(row.get("kol_pool_id")),
                "kol_name": _text(row.get("kol_name"), 120),
                "kol_handle": _text(row.get("kol_handle"), 100),
                "published_day": row["_pub"].isoformat(),
                "view_count": row["_views"],
                "like_count": likes,
                "comment_count": comments,
                "score": parts["total"],
                "score_parts": {
                    "heat": parts["heat"],
                    "relevance": parts["relevance"],
                    "competitor": parts["competitor"],
                    "freshness": parts["freshness"],
                    "engagement_rate": parts["engagement_rate"],
                    "views_norm": parts["views_norm"],
                    "age_days": parts["age_days"],
                },
                "signals": signals,
                "why": _why_text(parts, signals, row["_views"], window_days),
                "comment_angles": _comment_angles(signals, comments),
            }
        )

    # Top5:总分降序,tie-break evidence_id 升序(决定性)。
    items.sort(key=lambda it: (-float(it["score"]), int(it["evidence_id"] or 0)))
    top = items[:TOP_N]

    coverage = {
        "videos_scanned": len(rows),
        "scored": len(items),
        "skipped_no_views": skipped_no_views,
        "skipped_no_url": skipped_no_url,
        "skipped_own_brand": skipped_own_brand,
        "max_views_in_window": max_views,
        "competitor_vocab_brands": len(comp_vocab),
    }
    if not top:
        return {
            **base,
            "status": "empty",
            "reason": (
                f"窗口内扫到 {len(rows)} 条证据,但无一条同时具备真实 URL 与播放数,无从打分 — 诚实空态。"
            ),
            "coverage": coverage,
            "items": [],
        }
    return {
        **base,
        "coverage": coverage,
        "items": top,
        "basis": {
            "score_rule": SCORE_RULE_TEXT,
            "weights": {
                "heat": W_HEAT, "relevance": W_RELEVANCE,
                "competitor": W_COMPETITOR, "freshness": W_FRESHNESS,
            },
            "vocab_sources": {
                "focal": "kol.focal_matrix._extract_focals(负语境感知正则)",
                "topics": "本模块 TOPIC_TERMS 摄影主题词表(评论角度模板同源)",
                "competitor": "market.brand_pulse._competitor_vocab(#51 competitor_brands.json 同源)",
                "viltrox": "market.brand_signal_detector.VILTROX_TERMS(brand_pulse 同款装载)",
            },
            "date_column": "published_at_norm(迁移216)主走,COALESCE posted_at → to_jsonb publish_date 兜底",
            "own_brand_rule": "vkpi_kol_pool.source_type=own_brand_excluded 的自家官号视频不进清单(官号不评自己),剔除数见 coverage.skipped_own_brand。",
            "note": "为何值得去=规则模板拼装;建议评论角度=词表模板,均非 LLM 生成。",
        },
    }
