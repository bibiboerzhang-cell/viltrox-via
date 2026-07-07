"""品牌安全扫描(brand_safety v0)—— 全库内信号、零外网、零 LLM、零写库。

brand_safety_scan(kol_pool_id) -> dict:四路库内信号 + 12 类风险框架(对齐同行
Later 式口径,诚实标「库内信号 v0,外网扫描待接」;risk_level 仅 none/info/warn
提示,绝不下结论):
  ① disclosure            FTC 披露习惯:守卫 import quality_compliance.ftc_scan
                          (词表法否定感知),转述其 summary/risks,不重造;
  ② comment_negativity    评论区负面聚类:该 KOL 视频已入库评论的负面词表密度
                          (词表守卫 import signature_profile.NEGATIVE_TERMS)
                          + 按命中词聚类出 top 负面主题;
  ③ content_controversy   深析文本+标题争议词表:政治/仇恨/脏话/暴力/武器/成人/
                          烟酒药物/争议议题 8 类词表扫 final_v1 深析文本(分析师
                          口吻,仅作迹象)与视频标题(创作者文本);
  ④ competitor_binding    竞品独占迹象:标题+深析文本按 competitor_brands.json
                          词表(守卫 import competitor_text)算该 KOL 的竞品声量
                          占比(视频×品牌单位,与 brand_pulse 同口径),
                          >80% = 深度绑定预警(warn)。

12 类风险框架:8 类争议词表 + 虚假信息(external_pending,库内无信号源诚实
待接)+ 披露合规 + 评论负面 + 竞品绑定;每类 coverage 标 in_library_v0 或
external_pending,绝不假装扫过外网。

compat 约定:SQL 占位符 ?;SQL 零 percent 字面(不用 LIKE);BOOLEAN 读回可能是
int 1/0 走 _truthy;JSONB 双态走 _loads(经 quality_compliance 复用)。

红线:纯读信号,绝不写任何表;绝不读写 viltrox_fit_score、不碰 rule_v0;
零 LLM/provider 调用;词表启发式仅提示需人工核看,绝不下法律/道德结论。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = "brand_safety_v0"

# 评论负面密度:样本低于此数不参与升级(诚实低样本)。
MIN_COMMENT_SAMPLE = 10
# 竞品绑定:至少这么多条「提及任一品牌」的视频才给占比结论。
MIN_BINDING_SAMPLE = 5
# 竞品声量占比阈值:>0.8 深度绑定预警(warn),>0.5 提示(info)。
BINDING_WARN_SHARE = 0.8
BINDING_INFO_SHARE = 0.5
# 争议词表:同类命中 ≥ 这么多条视频升 warn,否则 info。
CONTROVERSY_WARN_VIDEOS = 3

# ── 争议词表(8 类;英文词走词边界、中文走子串,与 signature_profile 同款口径)──
# 刻意避开影像圈常用词(shoot/shooting/shotgun mic/killer deal 等不入表)。
CONTROVERSY_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("politics", "政治敏感", (
        "politics", "political", "election", "protest", "propaganda",
        "政治", "选举", "抗议", "示威", "制裁",
    )),
    ("hate_speech", "仇恨/歧视言论", (
        "racist", "racism", "sexist", "homophobic", "nazi", "hate speech",
        "种族歧视", "歧视言论", "仇恨言论", "辱华",
    )),
    ("profanity", "脏话/冒犯语言", (
        "fuck", "fucking", "shit", "bitch", "asshole",
        "傻逼", "妈的", "操你", "去你的",
    )),
    ("violence", "暴力/血腥", (
        "violence", "violent", "gore", "murder", "assault", "massacre",
        "暴力", "血腥", "斗殴", "枪杀", "凶杀",
    )),
    ("weapons", "武器涉枪", (
        "firearm", "firearms", "rifle", "pistol", "ammunition", "ammo",
        "枪支", "弹药", "持枪",
    )),
    ("adult_content", "成人内容", (
        "nsfw", "porn", "nude", "nudity", "onlyfans",
        "色情", "裸露", "软色情",
    )),
    ("drugs_alcohol", "烟酒药物", (
        "marijuana", "cannabis", "cocaine", "heroin", "drunk", "drug abuse",
        "大麻", "毒品", "吸毒", "酗酒", "醉酒",
    )),
    ("social_controversy", "争议社会议题", (
        "controversy", "controversial", "scandal", "lawsuit", "boycott",
        "争议", "丑闻", "诉讼", "抵制", "翻车",
    )),
)

# ── 12 类风险框架(对齐 Later 式品牌安全口径;source 指到本模块信号路)──────────
# (key, 中文标签, 信号来源:controversy=③词表 / disclosure=① / comments=② /
#  competitor=④ / external=库内无信号源,外网扫描待接)
RISK_FRAMEWORK: tuple[tuple[str, str, str], ...] = (
    ("adult_content", "成人内容", "controversy"),
    ("hate_speech", "仇恨/歧视言论", "controversy"),
    ("profanity", "脏话/冒犯语言", "controversy"),
    ("violence", "暴力/血腥", "controversy"),
    ("weapons", "武器涉枪", "controversy"),
    ("drugs_alcohol", "烟酒药物", "controversy"),
    ("politics", "政治敏感", "controversy"),
    ("social_controversy", "争议社会议题", "controversy"),
    ("misinformation", "虚假信息", "external"),
    ("disclosure_compliance", "广告披露合规(FTC)", "disclosure"),
    ("audience_negativity", "评论区负面舆情", "comments"),
    ("competitor_binding", "竞品深度绑定", "competitor"),
)

_LEVEL_ORDER = {"none": 0, "info": 1, "warn": 2}


# ── 小工具 ──────────────────────────────────────────────────────────────────


def _text(value: Any, limit: int = 300) -> str:
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _max_level(levels: list[str]) -> str:
    best = "none"
    for level in levels:
        if _LEVEL_ORDER.get(level, 0) > _LEVEL_ORDER.get(best, 0):
            best = level
    return best


# 词表命中(返回命中词,供聚类展示):英文词走词边界正则(shotgun 不命中 gun 类词、
# previews 不命中 review 类词),中文/符号词走子串 —— 与 signature_profile._term_pattern
# 同款口径(那边只回 bool,这里要命中词列表,故本地小实现)。
_TERM_PATTERN_CACHE: dict[str, re.Pattern[str] | None] = {}


def _term_pattern(term: str) -> re.Pattern[str] | None:
    cached = _TERM_PATTERN_CACHE.get(term, False)
    if cached is not False:
        return cached  # type: ignore[return-value]
    pattern: re.Pattern[str] | None = None
    if term and term.isascii() and any(ch.isalnum() for ch in term):
        prefix = r"(?<![a-z0-9])" if term[0].isalnum() else ""
        suffix = r"(?![a-z0-9])" if term[-1].isalnum() else ""
        pattern = re.compile(prefix + re.escape(term) + suffix)
    _TERM_PATTERN_CACHE[term] = pattern
    return pattern


def _matched_terms(blob: str, terms: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for term in terms:
        pattern = _term_pattern(term)
        if pattern is not None:
            if pattern.search(blob):
                hits.append(term)
        elif term in blob:
            hits.append(term)
    return hits


# ── 数据装载(全只读)────────────────────────────────────────────────────────


def _load_pool_row(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, handle, display_name, platform FROM vkpi_kol_pool WHERE id = ?",
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_evidence_rows(conn: Any, kol_pool_id: int, limit: int = 500) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id AS evidence_id, COALESCE(video_title, title, '') AS title,
               content_url, platform, view_count, is_active
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id = ?
        ORDER BY COALESCE(view_count, 0) DESC, id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows if _truthy(dict(r).get("is_active"))]


def _load_deep_blobs(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    """final_v1 深析文本底料(守卫 import quality_compliance 的装载/blob 口径,不重造)。

    返回 [{evidence_id, title, blob(小写)}];import/解析失败回空列表(诚实降级)。
    """
    try:
        from app.domains.kol import quality_compliance as qc
    except ImportError as exc:  # pragma: no cover — 同包件,理论不该发生
        logger.warning("brand_safety deep blobs unavailable: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    try:
        for row in qc._load_deep_rows(conn, int(kol_pool_id)):
            dims = qc._as_dict(qc._loads(row.get("dims")))
            if not dims:
                continue
            blob = qc._deep_text_blob(dims)
            if not blob.strip():
                continue
            out.append(
                {
                    "evidence_id": _int_or_none(row.get("evidence_id")),
                    "title": _text(row.get("title"), 140),
                    "blob": blob,
                }
            )
    except Exception as exc:  # noqa: BLE001 — 深析底料失败不拖垮其余信号
        logger.warning("brand_safety deep blobs failed for %s: %s", kol_pool_id, exc)
        return []
    return out


def _load_comments(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    """评论装载:守卫 import audience.authenticity 的双桥口径(同一真源,不另造桥)。"""
    try:
        from app.domains.audience.authenticity import load_kol_comments

        return load_kol_comments(conn, int(kol_pool_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("brand_safety comments load failed for %s: %s", kol_pool_id, exc)
        return []


# ── ① FTC 披露习惯(守卫 import quality_compliance.ftc_scan)─────────────────


def _disclosure_block(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    try:
        from app.domains.kol import quality_compliance as qc

        scan = qc.ftc_scan(int(kol_pool_id), conn=conn)
    except Exception as exc:  # noqa: BLE001 — 守卫失败诚实降级,不拖垮整案
        return {"status": "error", "level": "none", "reason": _text(str(exc), 300),
                "source": "quality_compliance.ftc_scan(守卫 import)"}
    if scan.get("status") != "ready":
        return {"status": "empty", "level": "none",
                "reason": _text(scan.get("reason"), 300),
                "source": "quality_compliance.ftc_scan(守卫 import)"}
    summary = scan.get("summary") or {}
    level = _text(summary.get("risk_level"), 10)
    if level not in ("info", "warn"):
        level = "none"
    return {
        "status": "ready",
        "level": level,
        "disclosed": summary.get("disclosed"),
        "undisclosed_suspect": summary.get("undisclosed_suspect"),
        "clean": summary.get("clean"),
        "warn_count": summary.get("warn_count"),
        "top_risks": (scan.get("risks") or [])[:5],
        "coverage": scan.get("coverage") or {},
        "source": "quality_compliance.ftc_scan(词表法否定感知,守卫 import 转述)",
        "note": "clean ≠ 确认无风险(描述覆盖有限);词表启发式,非法律结论。",
    }


# ── ② 评论区负面聚类 ─────────────────────────────────────────────────────────


def _comment_negativity_block(comments: list[dict[str, Any]]) -> dict[str, Any]:
    texts = [_text(c.get("comment_text"), 500) for c in comments]
    texts = [t for t in texts if t]
    if not texts:
        return {"status": "empty", "level": "none",
                "reason": "该 KOL 视频暂无已入库评论(vkpi_comments 双桥均空),负面聚类无从做。"}
    try:
        from app.domains.kol.signature_profile import NEGATIVE_TERMS
    except ImportError as exc:  # pragma: no cover — 同包件
        return {"status": "error", "level": "none", "reason": f"负面词表 import 失败:{exc}"}

    total = len(texts)
    negative = 0
    clusters: dict[str, dict[str, Any]] = {}
    for text in texts:
        low = text.lower()
        hits = _matched_terms(low, NEGATIVE_TERMS)
        if not hits:
            continue
        negative += 1
        for term in hits:
            entry = clusters.setdefault(term, {"term": term, "count": 0, "samples": []})
            entry["count"] += 1
            if len(entry["samples"]) < 2:
                entry["samples"].append(text[:120])
    density = negative / total
    top_clusters = sorted(clusters.values(), key=lambda c: c["count"], reverse=True)[:6]

    level = "none"
    note = ""
    if total < MIN_COMMENT_SAMPLE:
        note = f"样本仅 {total} 条(<{MIN_COMMENT_SAMPLE}),密度不升级 risk(诚实低样本)。"
    elif density >= 0.25 and negative >= 5:
        level = "warn"
    elif density >= 0.10 and negative >= 2:
        level = "info"
    return {
        "status": "ready",
        "level": level,
        "comments_scanned": total,
        "negative_count": negative,
        "negative_density": round(density, 3),
        "clusters": top_clusters,
        "note": note or "负面评论可能针对产品也可能针对博主,词表密度仅信号,需人工核看。",
        "basis": {
            "source": "vkpi_comments(双桥)+ signature_profile.NEGATIVE_TERMS(守卫 import 词表)",
            "method": "负面词表命中密度 + 按命中词聚类",
        },
    }


# ── ③ 深析文本+标题争议词表 ──────────────────────────────────────────────────


def _controversy_block(
    evidence_rows: list[dict[str, Any]], deep_blobs: list[dict[str, Any]]
) -> dict[str, Any]:
    if not evidence_rows and not deep_blobs:
        return {"status": "empty", "level": "none",
                "reason": "无视频标题也无深析文本可扫,争议词表扫描无从做。", "categories": []}
    # 每类:{evidence 维度命中} —— 同一视频标题+深析都命中只记一次。
    per_cat: dict[str, dict[Any, dict[str, Any]]] = {}
    for row in evidence_rows:
        blob = _text(row.get("title"), 300).lower()
        if not blob:
            continue
        for key, _label, terms in CONTROVERSY_CATEGORIES:
            hits = _matched_terms(blob, terms)
            if hits:
                bucket = per_cat.setdefault(key, {})
                eid = _int_or_none(row.get("evidence_id"))
                entry = bucket.setdefault(eid, {
                    "evidence_id": eid, "title": _text(row.get("title"), 140),
                    "sources": [], "terms": []})
                entry["sources"] = sorted(set(entry["sources"] + ["title"]))
                entry["terms"] = sorted(set(entry["terms"] + hits))
    for deep in deep_blobs:
        blob = deep.get("blob") or ""
        for key, _label, terms in CONTROVERSY_CATEGORIES:
            hits = _matched_terms(blob, terms)
            if hits:
                bucket = per_cat.setdefault(key, {})
                eid = deep.get("evidence_id")
                entry = bucket.setdefault(eid, {
                    "evidence_id": eid, "title": _text(deep.get("title"), 140),
                    "sources": [], "terms": []})
                entry["sources"] = sorted(set(entry["sources"] + ["deep_analysis"]))
                entry["terms"] = sorted(set(entry["terms"] + hits))

    categories: list[dict[str, Any]] = []
    for key, label, _terms in CONTROVERSY_CATEGORIES:
        videos = list((per_cat.get(key) or {}).values())
        level = "none"
        if videos:
            level = "warn" if len(videos) >= CONTROVERSY_WARN_VIDEOS else "info"
        categories.append(
            {
                "key": key,
                "label": label,
                "level": level,
                "video_count": len(videos),
                "examples": videos[:3],
            }
        )
    overall = _max_level([c["level"] for c in categories])
    return {
        "status": "ready",
        "level": overall,
        "scanned": {"titles": len(evidence_rows), "deep_texts": len(deep_blobs)},
        "categories": categories,
        "note": (
            "词表启发式:标题=创作者文本,深析=分析师口吻仅作迹象;命中≠违规,"
            "需人工核看原视频;影像圈常用词(shoot/shotgun mic 等)已刻意避开。"
        ),
        "basis": {"method": f"8 类争议词表;同类命中≥{CONTROVERSY_WARN_VIDEOS} 条视频升 warn,否则 info"},
    }


# ── ④ 竞品独占迹象(brand_pulse 同口径:视频×品牌单位)────────────────────────


def _competitor_binding_block(
    evidence_rows: list[dict[str, Any]], deep_blobs: list[dict[str, Any]]
) -> dict[str, Any]:
    try:
        from app.domains.kol.competitor_text import _keyword_match, load_competitor_brands
        from app.domains.market.brand_signal_detector import VILTROX_TERMS
    except ImportError as exc:  # pragma: no cover
        return {"status": "error", "level": "none", "reason": f"竞品词表件 import 失败:{exc}"}

    brands = load_competitor_brands()
    deep_by_eid = {d.get("evidence_id"): d.get("blob") or "" for d in deep_blobs if d.get("evidence_id") is not None}
    if not evidence_rows and not deep_blobs:
        return {"status": "empty", "level": "none",
                "reason": "无视频标题也无深析文本,竞品声量占比无从算。"}

    viltrox_videos = 0
    brand_videos: dict[str, int] = {}
    scanned = 0
    for row in evidence_rows:
        eid = _int_or_none(row.get("evidence_id"))
        blob = (_text(row.get("title"), 300) + " " + (deep_by_eid.get(eid) or "")).lower().strip()
        if not blob:
            continue
        scanned += 1
        if any(_keyword_match(blob, term) for term in VILTROX_TERMS):
            viltrox_videos += 1
        for brand, config in brands.items():
            if any(_keyword_match(blob, kw) for kw in config.get("keywords") or []):
                brand_videos[brand] = brand_videos.get(brand, 0) + 1

    comp_units = sum(brand_videos.values())
    units = viltrox_videos + comp_units
    if units == 0:
        return {"status": "empty", "level": "none",
                "reason": f"扫描 {scanned} 条视频文本,未命中 Viltrox 或任何竞品词表(声量占比无从算)。"}
    share = comp_units / units
    top_brands = sorted(brand_videos.items(), key=lambda kv: kv[1], reverse=True)
    top_list = [{"brand": b, "video_count": n} for b, n in top_brands[:6]]

    level = "none"
    message = ""
    if units < MIN_BINDING_SAMPLE:
        message = f"品牌提及仅 {units} 个视频×品牌单位(<{MIN_BINDING_SAMPLE}),占比不升级 risk(诚实低样本)。"
    elif share > BINDING_WARN_SHARE:
        level = "warn"
        message = f"竞品声量占比 {round(share * 100)}%(>80%)= 深度绑定预警:该 KOL 内容重心在竞品生态。"
        if top_brands and comp_units > 0 and top_brands[0][1] / comp_units >= 0.6 and top_brands[0][1] >= 4:
            message += f"且集中在单一品牌 {top_brands[0][0]}({top_brands[0][1]} 条),存在独占合作可能(仅迹象)。"
    elif share > BINDING_INFO_SHARE:
        level = "info"
        message = f"竞品声量占比 {round(share * 100)}%(>50%),竞品内容偏多,合作前留意排他条款。"
    return {
        "status": "ready",
        "level": level,
        "videos_scanned": scanned,
        "viltrox_videos": viltrox_videos,
        "competitor_units": comp_units,
        "competitor_share": round(share, 3),
        "top_competitor_brands": top_list,
        "message": message or f"竞品声量占比 {round(share * 100)}%,未达提示阈值。",
        "basis": {
            "source": "视频标题(创作者文本)+ final_v1 深析文本;competitor_brands.json + VILTROX_TERMS(守卫 import)",
            "method": "视频×品牌单位(与 brand_pulse 同口径);share = 竞品单位/(Viltrox+竞品)",
            "thresholds": {"warn": BINDING_WARN_SHARE, "info": BINDING_INFO_SHARE, "min_sample": MIN_BINDING_SAMPLE},
        },
    }


# ── 12 类框架组装 ────────────────────────────────────────────────────────────


def _framework(
    controversy: dict[str, Any],
    disclosure: dict[str, Any],
    negativity: dict[str, Any],
    competitor: dict[str, Any],
) -> list[dict[str, Any]]:
    contro_by_key = {c.get("key"): c for c in controversy.get("categories") or []}
    entries: list[dict[str, Any]] = []
    for key, label, source in RISK_FRAMEWORK:
        if source == "controversy":
            cat = contro_by_key.get(key) or {}
            entries.append(
                {
                    "key": key, "label": label,
                    "coverage": "in_library_v0",
                    "risk_level": cat.get("level", "none") if controversy.get("status") == "ready" else "none",
                    "signal_count": cat.get("video_count", 0),
                    "detail_ref": "signals.content_controversy",
                }
            )
        elif source == "external":
            entries.append(
                {
                    "key": key, "label": label,
                    "coverage": "external_pending",
                    "risk_level": "none",
                    "signal_count": 0,
                    "note": "库内无该类信号源,外网扫描待接(诚实待接,非 clean)。",
                }
            )
        else:
            block = {"disclosure": disclosure, "comments": negativity, "competitor": competitor}[source]
            entries.append(
                {
                    "key": key, "label": label,
                    "coverage": "in_library_v0",
                    "risk_level": block.get("level", "none") if block.get("status") == "ready" else "none",
                    "signal_count": 1 if block.get("status") == "ready" and block.get("level") in ("info", "warn") else 0,
                    "detail_ref": {
                        "disclosure": "signals.disclosure",
                        "comments": "signals.comment_negativity",
                        "competitor": "signals.competitor_binding",
                    }[source],
                }
            )
    return entries


# ── 主入口 ──────────────────────────────────────────────────────────────────


def brand_safety_scan(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
    """品牌安全 v0:四路库内信号 + 12 类框架;KOL 不存在抛 LookupError(路由转 404)。

    红线:独立展示信号,绝不写回任何表、不参与任何选人/推荐评分;仅 info/warn 提示。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    pool = _load_pool_row(db, int(kol_pool_id))
    if not pool:
        raise LookupError(f"kol_pool {kol_pool_id} not found")

    evidence_rows = _load_evidence_rows(db, int(kol_pool_id))
    deep_blobs = _load_deep_blobs(db, int(kol_pool_id))
    comments = _load_comments(db, int(kol_pool_id))

    disclosure = _disclosure_block(db, int(kol_pool_id))
    negativity = _comment_negativity_block(comments)
    controversy = _controversy_block(evidence_rows, deep_blobs)
    competitor = _competitor_binding_block(evidence_rows, deep_blobs)

    framework = _framework(controversy, disclosure, negativity, competitor)
    overall = _max_level([str(entry.get("risk_level") or "none") for entry in framework])

    base = {
        "kol_pool_id": int(kol_pool_id),
        "kol": {
            "handle": _text(pool.get("handle"), 100),
            "display_name": _text(pool.get("display_name"), 100),
            "platform": _text(pool.get("platform"), 30),
        },
        "version": SCHEMA_VERSION,
        "scope": "in_library_signals_only",
        "risk_level": overall,
        "framework": framework,
        "signals": {
            "disclosure": disclosure,
            "comment_negativity": negativity,
            "content_controversy": controversy,
            "competitor_binding": competitor,
        },
        "coverage": {
            "evidence_count": len(evidence_rows),
            "deep_analyzed_count": len(deep_blobs),
            "comments_scanned": len(comments),
        },
        "generated_at": _now_iso(),
        "note": (
            "品牌安全 v0:12 类风险框架对齐同行(Later)口径,但只扫库内信号"
            "(标题/深析文本/已入库评论/披露词表),外网扫描待接;"
            "risk_level 仅 none/info/warn 提示,词表启发式需人工核看,绝不下结论。"
        ),
    }
    if not evidence_rows and not deep_blobs and not comments:
        return {
            **base,
            "status": "empty",
            "reason": "该 KOL 库内无视频证据、无深析文本、无已入库评论 —— 四路信号全部无料,框架仅列待接项。",
        }
    return {**base, "status": "ready"}
