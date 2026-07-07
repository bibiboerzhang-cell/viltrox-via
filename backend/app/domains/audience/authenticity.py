"""受众真实性信号(authenticity v0)—— 全库内信号、零外网、零 LLM、零写库。

authenticity_signal(kol_pool_id) -> dict:四路库内信号 → authenticity_score 0-100:
  ① commenter_repeat   评论者重复率:同 commenter(author_id,B1 回填列;缺则 handle
                       兜底)多条评论的占比 —— 高重复可能=铁粉也可能=互推群,仅信号;
  ② template_comments  评论模板化率:纯 emoji/超短评/同文重复(≥3 次同文)占比 ——
                       真实观众也常发短评,阈值刻意保守;
  ③ engagement_outlier 互动播放比离群:该 KOL 每视频 (赞+评)/播放 的个人中位数,
                       对照全池各 KOL 中位数分布的分位 —— 极端高=互推/买互动可能,
                       极端低=买播放可能,两头都只提示不下结论;
  ④ inflation_flag     既有列守卫读:vkpi_kol_pool.suspect_inflation(P0-3 假粉离群
                       已落库),TRUE=warn;从未跑过检测诚实 empty,不当 clean。

综合分:100 − Σ各信号扣分(每路封顶,见常量);只有可用信号参与,缺数路记
signals_used 之外并如实标 reason。评论样本 <10 → confidence=low(诚实降级)。

数据源(全只读):vkpi_comments(account_id / evidence 双桥,与 audience_stats
同款口径)/ vkpi_kol_video_evidence / vkpi_kol_pool(inflation 列)。

compat 约定:SQL 占位符 ?;SQL 零 percent 字面(不用 LIKE);BOOLEAN 读回可能是
int 1/0 走 _truthy;JSONB 双态走 _loads。

红线:纯读信号,绝不写任何表;绝不读写 viltrox_fit_score、不碰 rule_v0;
零 LLM/provider 调用;样本不足诚实降级,绝不杜撰。
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = "authenticity_v0"

# 评论样本阈值:低于此数 confidence=low(诚实降级,分数仍出但明说不可靠)。
MIN_COMMENT_SAMPLE = 10
# 单 KOL 最多读取的评论条数(双桥合并去重后)。
MAX_COMMENTS = 2000
# 池分位对照:KOL 至少要有这么多条有播放数的视频才进分布/参评。
POOL_MIN_VIDEOS_PER_KOL = 3
# 池分位对照:分布里至少要有这么多个 KOL 才有对照意义。
POOL_MIN_KOLS = 30
# 全池 evidence 扫描上限(本地全表 ~3k,2 万留足余量)。
POOL_MAX_EVIDENCE = 20000
# 各信号扣分封顶(0-100 综合分的构成;阈值见各信号函数,刻意保守)。
DEDUCT_CAP_REPEAT = 25
DEDUCT_CAP_TEMPLATE = 25
DEDUCT_CAP_ENGAGEMENT = 20
DEDUCT_CAP_INFLATION = 30

# 「有词性内容」判定:任意 Unicode 字母/数字(阿拉伯语/西里尔/假名等全算,
# str.isalnum() 对 emoji/符号返回 False)—— 全都没有 = 纯 emoji/符号评论。
# 真库负例:3648 的评论是阿拉伯语,ASCII+CJK 正则会把它们误判成纯 emoji。


def _has_wordish(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


# ── 小工具(compat 容错)────────────────────────────────────────────────────


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


def _loads(value: Any) -> Any:
    """JSONB 经 compat 层可能回 dict/list 也可能回 str,双态容错。"""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


# ── 数据装载(全只读;? 占位;零 percent 字面)───────────────────────────────


def _load_pool_row(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, handle, display_name, platform,
               suspect_inflation, inflation_reason, inflation_signals_json, inflation_checked_at
        FROM vkpi_kol_pool WHERE id = ?
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else None


def load_kol_comments(conn: Any, kol_pool_id: int, limit: int = MAX_COMMENTS) -> list[dict[str, Any]]:
    """该 KOL 已入库评论(双桥合并去重):account_id=kol_pool_id 或 evidence 桥
    (post_table 属 evidence 口径 + post_id 落在该 KOL 的 video evidence 上),
    与 audience_stats.sample_local_commenters 同款口径;按评论 id 去重。
    公共函数:kol.brand_safety 守卫 import 复用,别再另造评论桥。
    """
    merged: dict[int, dict[str, Any]] = {}
    rows = conn.execute(
        """
        SELECT id, comment_text, author_id, author_handle, likes_count, created_at, post_id
        FROM vkpi_comments WHERE account_id = ? LIMIT ?
        """,
        (int(kol_pool_id), int(limit)),
    ).fetchall()
    for raw in rows:
        item = dict(raw)
        cid = _int_or_none(item.get("id"))
        if cid is not None:
            merged[cid] = item

    ev_rows = conn.execute(
        "SELECT id FROM vkpi_kol_video_evidence WHERE kol_pool_id = ? LIMIT 300",
        (int(kol_pool_id),),
    ).fetchall()
    eids = [int(dict(e)["id"]) for e in ev_rows]
    if eids and len(merged) < limit:
        placeholders = ",".join(["?"] * len(eids))
        rows = conn.execute(
            "SELECT id, comment_text, author_id, author_handle, likes_count, created_at, post_id "
            "FROM vkpi_comments "
            f"WHERE post_table IN ('evidence','vkpi_kol_video_evidence') AND post_id IN ({placeholders}) "
            "LIMIT ?",
            (*eids, int(limit)),
        ).fetchall()
        for raw in rows:
            item = dict(raw)
            cid = _int_or_none(item.get("id"))
            if cid is not None and cid not in merged:
                merged[cid] = item
    return list(merged.values())[: int(limit)]


# ── ① 评论者重复率 ──────────────────────────────────────────────────────────


def _author_key(comment: dict[str, Any]) -> str:
    """commenter 身份键:优先 B1 回填的 author_id 列,缺则 author_handle 兜底。"""
    key = _text(comment.get("author_id"), 200)
    if not key:
        key = _text(comment.get("author_handle"), 200).lower()
    return key


def _commenter_repeat_signal(comments: list[dict[str, Any]]) -> dict[str, Any]:
    keys = [_author_key(c) for c in comments]
    keys = [k for k in keys if k]
    total = len(keys)
    if total == 0:
        return {"status": "empty", "reason": "已入库评论里没有可识别的评论者身份(author_id/handle 均空)。",
                "deduction": 0, "level": "none"}
    counter = Counter(keys)
    repeat_comments = sum(n for n in counter.values() if n >= 2)
    share = repeat_comments / total
    top = [{"author": k, "comments": n} for k, n in counter.most_common(3) if n >= 2]

    level, deduction = "none", 0
    note = ""
    if total < MIN_COMMENT_SAMPLE:
        note = f"样本仅 {total} 条(<{MIN_COMMENT_SAMPLE}),重复率不参与扣分(诚实低样本)。"
    elif share >= 0.5:
        level, deduction = "warn", DEDUCT_CAP_REPEAT
    elif share >= 0.35:
        level, deduction = "info", 15
    elif share >= 0.25:
        level, deduction = "info", 8
    return {
        "status": "ready",
        "level": level,
        "deduction": deduction,
        "comments_with_author": total,
        "unique_commenters": len(counter),
        "repeat_comment_share": round(share, 3),
        "top_repeat_commenters": top,
        "note": note or "高重复可能=铁粉也可能=互推群,仅信号不下结论。",
        "basis": {"source": "vkpi_comments.author_id(B1 回填)+ handle 兜底", "method": "同 commenter ≥2 条评论的占比"},
    }


# ── ② 评论模板化率 ──────────────────────────────────────────────────────────


def _template_signal(comments: list[dict[str, Any]]) -> dict[str, Any]:
    texts = [_text(c.get("comment_text"), 500) for c in comments]
    texts = [t for t in texts if t]
    total = len(texts)
    if total == 0:
        return {"status": "empty", "reason": "已入库评论均无文本内容,模板化率无从计算。",
                "deduction": 0, "level": "none"}
    normalized = [" ".join(t.lower().split()) for t in texts]
    dup_counter = Counter(normalized)
    emoji_only = 0
    very_short = 0
    duplicated = 0
    flagged = 0
    for norm in normalized:
        is_emoji = not _has_wordish(norm)
        is_short = (not is_emoji) and len(norm) <= 5
        is_dup = dup_counter[norm] >= 3
        emoji_only += 1 if is_emoji else 0
        very_short += 1 if is_short else 0
        duplicated += 1 if is_dup else 0
        flagged += 1 if (is_emoji or is_short or is_dup) else 0
    share = flagged / total
    dup_examples = [{"text": t[:80], "count": n} for t, n in dup_counter.most_common(3) if n >= 3]

    level, deduction = "none", 0
    note = ""
    if total < MIN_COMMENT_SAMPLE:
        note = f"样本仅 {total} 条(<{MIN_COMMENT_SAMPLE}),模板化率不参与扣分(诚实低样本)。"
    elif share >= 0.7:
        level, deduction = "warn", DEDUCT_CAP_TEMPLATE
    elif share >= 0.5:
        level, deduction = "info", 15
    elif share >= 0.35:
        level, deduction = "info", 8
    return {
        "status": "ready",
        "level": level,
        "deduction": deduction,
        "comments_with_text": total,
        "templated_share": round(share, 3),
        "breakdown": {"emoji_only": emoji_only, "very_short": very_short, "duplicated_text": duplicated},
        "duplicated_examples": dup_examples,
        "note": note or "真实观众也常发短评/emoji,阈值刻意保守;仅信号不下结论。",
        "basis": {"source": "vkpi_comments.comment_text", "method": "纯emoji ∪ ≤5字符短评 ∪ ≥3次同文重复 的占比"},
    }


# ── ③ 互动播放比离群(池分位对照)────────────────────────────────────────────


def _load_pool_engagement_medians(conn: Any) -> dict[int, float]:
    """全池每 KOL 的 (赞+评)/播放 个人中位数(≥POOL_MIN_VIDEOS_PER_KOL 条有播放视频才入)。"""
    rows = conn.execute(
        """
        SELECT kol_pool_id, view_count, like_count, comment_count, is_active
        FROM vkpi_kol_video_evidence
        LIMIT ?
        """,
        (POOL_MAX_EVIDENCE,),
    ).fetchall()
    rates: dict[int, list[float]] = {}
    for raw in rows:
        item = dict(raw)
        if not _truthy(item.get("is_active")):
            continue
        kid = _int_or_none(item.get("kol_pool_id"))
        views = _int_or_none(item.get("view_count"))
        if kid is None or not views or views <= 0:
            continue
        inter = (_int_or_none(item.get("like_count")) or 0) + (_int_or_none(item.get("comment_count")) or 0)
        rates.setdefault(kid, []).append(inter / views)
    medians: dict[int, float] = {}
    for kid, values in rates.items():
        if len(values) >= POOL_MIN_VIDEOS_PER_KOL:
            med = _median(values)
            if med is not None:
                medians[kid] = med
    return medians


def _engagement_outlier_signal(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    medians = _load_pool_engagement_medians(conn)
    own = medians.get(int(kol_pool_id))
    if own is None:
        return {
            "status": "empty",
            "reason": f"该 KOL 有播放数的视频不足 {POOL_MIN_VIDEOS_PER_KOL} 条,互动播放比离群对照不做(不硬算)。",
            "deduction": 0, "level": "none",
        }
    others = sorted(medians.values())
    if len(others) < POOL_MIN_KOLS:
        return {
            "status": "empty",
            "reason": f"全池可对照 KOL 仅 {len(others)} 个(<{POOL_MIN_KOLS}),分位对照无意义。",
            "deduction": 0, "level": "none",
        }
    below = sum(1 for v in others if v < own)
    percentile = below / len(others)
    p50 = _median(others)

    level, deduction, direction = "none", 0, ""
    if percentile >= 0.98:
        level, deduction, direction = "warn", DEDUCT_CAP_ENGAGEMENT, "极端偏高(互推/买互动可能,也可能真互动强)"
    elif percentile >= 0.95:
        level, deduction, direction = "info", 10, "偏高(top5%)"
    elif percentile <= 0.02:
        level, deduction, direction = "info", 10, "极端偏低(买播放/僵尸受众可能,也可能内容形态使然)"
    elif percentile <= 0.05:
        level, deduction, direction = "info", 5, "偏低(bottom5%)"
    return {
        "status": "ready",
        "level": level,
        "deduction": deduction,
        "own_median_engagement_rate": round(own, 5),
        "pool_median": round(p50, 5) if p50 is not None else None,
        "pool_percentile": round(percentile, 3),
        "pool_kols_compared": len(others),
        "direction": direction or "在池内正常带(p5–p95)",
        "note": "两头离群都只提示不下结论;分位是全池各 KOL 个人中位数的横向对照。",
        "basis": {
            "source": "vkpi_kol_video_evidence 全池(is_active 视频,(赞+评)/播放)",
            "method": f"每 KOL 个人中位数(≥{POOL_MIN_VIDEOS_PER_KOL} 条有播放视频)→ 池分位",
        },
    }


# ── ④ 既有假粉离群列(P0-3 守卫读)───────────────────────────────────────────


def _inflation_signal(pool: dict[str, Any]) -> dict[str, Any]:
    checked_at = _text(pool.get("inflation_checked_at"), 60)
    if not checked_at:
        return {
            "status": "empty",
            "reason": "假粉离群检测(P0-3)从未跑过该 KOL,不当 clean 也不猜(诚实缺口)。",
            "deduction": 0, "level": "none",
        }
    flagged = _truthy(pool.get("suspect_inflation"))
    signals = _loads(pool.get("inflation_signals_json"))
    if flagged:
        return {
            "status": "ready",
            "level": "warn",
            "deduction": DEDUCT_CAP_INFLATION,
            "suspect_inflation": True,
            "reason": _text(pool.get("inflation_reason"), 400),
            "signals": signals if isinstance(signals, (dict, list)) else None,
            "checked_at": checked_at,
            "basis": {"source": "vkpi_kol_pool.suspect_inflation(P0-3 假粉离群,既有列守卫读)"},
        }
    return {
        "status": "ready",
        "level": "none",
        "deduction": 0,
        "suspect_inflation": False,
        "checked_at": checked_at,
        "note": "已跑过假粉离群检测,未命中规则(规则法非背书)。",
        "basis": {"source": "vkpi_kol_pool.suspect_inflation(P0-3 假粉离群,既有列守卫读)"},
    }


# ── 主入口 ──────────────────────────────────────────────────────────────────


def authenticity_signal(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
    """受众真实性 v0:四路库内信号 → 0-100 综合分;KOL 不存在抛 LookupError(路由转 404)。

    红线:独立展示信号,绝不写回任何表、不参与任何选人/推荐评分。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    pool = _load_pool_row(db, int(kol_pool_id))
    if not pool:
        raise LookupError(f"kol_pool {kol_pool_id} not found")

    try:
        comments = load_kol_comments(db, int(kol_pool_id))
    except Exception as exc:  # noqa: BLE001 — 评论桥挂了不拖垮其余信号
        logger.warning("authenticity comments load failed for %s: %s", kol_pool_id, exc)
        comments = []

    signals = {
        "commenter_repeat": _commenter_repeat_signal(comments),
        "template_comments": _template_signal(comments),
        "engagement_outlier": _engagement_outlier_signal(db, int(kol_pool_id)),
        "inflation_flag": _inflation_signal(pool),
    }

    signals_used = [key for key, sig in signals.items() if sig.get("status") == "ready"]
    deductions = [
        {"signal": key, "points": int(sig.get("deduction") or 0), "level": sig.get("level", "none")}
        for key, sig in signals.items()
        if sig.get("status") == "ready" and int(sig.get("deduction") or 0) > 0
    ]
    comment_sample = len([c for c in comments if _text(c.get("comment_text"), 10)])
    confidence = "low" if comment_sample < MIN_COMMENT_SAMPLE else ("medium" if comment_sample < 50 else "high")

    base = {
        "kol_pool_id": int(kol_pool_id),
        "kol": {
            "handle": _text(pool.get("handle"), 100),
            "display_name": _text(pool.get("display_name"), 100),
            "platform": _text(pool.get("platform"), 30),
        },
        "version": SCHEMA_VERSION,
        "signals": signals,
        "signals_used": signals_used,
        "generated_at": _now_iso(),
        "note": (
            "受众真实性 v0:全库内信号(评论重复/模板化/互动离群/既有假粉列),零外网、零 LLM、零写库;"
            "各信号只提示 info/warn,综合分是保守启发式,绝不下「买粉」结论。"
        ),
    }
    if not signals_used:
        return {
            **base,
            "status": "empty",
            "authenticity_score": None,
            "confidence": {"label": "low", "comment_sample": comment_sample},
            "reason": "四路信号全部无数据(无评论、有播放视频不足、假粉检测未跑),综合分无从计算。",
        }

    score = max(0, min(100, 100 - sum(d["points"] for d in deductions)))
    return {
        **base,
        "status": "ready",
        "authenticity_score": score,
        "deductions": deductions,
        "confidence": {
            "label": confidence,
            "comment_sample": comment_sample,
            "note": f"评论样本 <{MIN_COMMENT_SAMPLE} 条时 confidence=low(诚实降级);低样本信号不参与扣分。",
        },
        "basis": {
            "method": "100 − Σ各可用信号扣分(每路封顶:重复25/模板25/离群20/假粉列30)",
            "signals_used": signals_used,
        },
    }
