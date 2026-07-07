"""闭环波 L3 · 周对答案报告(规格第五章前半:哪些判断对了/错了/下次怎么改)。

weekly_report() 纯读 vkpi_gtm_outcomes:
  - 分组胜率:按 market / channel / content_angle / action_type 四维,decision 口径
    (win=validated/escalate,loss=failed/retreat,partial/retry 计中性);
  - 样本纪律:分组样本 <5 一律标 insufficient(统计功效闸复用),小样本胜率只展示不背书;
  - 三段输出:what_worked(对了什么)/ what_failed(错了什么)/ what_to_change
    (下次怎么改 = decided 行 next_weight_change 结构化条目汇总)+ headline 一句话。

next_weight_change 只做**汇总展示**:权重真回流走既有 recommendation_feedback 生效链
(L4 桥接,带样本闸——样本<5 绝不改权重),本模块零写库、零 LLM、零采集。
与 gtm_windows / L2 裁决流零 import 依赖:三方只共读写 vkpi_gtm_outcomes 一张表。

compat 约定:SQL 占位符 ?;零字面 percent;jsonb/时间读回可能是 str(json.loads/
fromisoformat 宽容);BOOLEAN 读回宽容。表未建(L2 迁移 217 并行在建)诚实空态。
红线:纯读;绝不写 viltrox_fit_score、不碰 rule_v0;胜率是"裁决的统计"不是承诺。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

TABLE = "vkpi_gtm_outcomes"

WIN_DECISIONS = ("validated", "escalate")
LOSS_DECISIONS = ("failed", "retreat")
NEUTRAL_DECISIONS = ("partial", "retry")
DECIDED_DECISIONS = WIN_DECISIONS + LOSS_DECISIONS + NEUTRAL_DECISIONS

MIN_SAMPLE = 5  # 样本纪律:<5 标 insufficient,绝不小样本下结论/改权重
GROUP_DIMS = ("market", "channel", "content_angle", "action_type")

_DEFAULT_DAYS = 7
_ITEM_CAP = 30

_METHOD_NOTE = (
    "decision 口径:win=validated/escalate,loss=failed/retreat,partial/retry 计中性;"
    "win_rate=wins/n。样本<5 的组标 insufficient——只展示不背书,绝不据此改权重。"
)


# ── 小工具 ──────────────────────────────────────────────────────────


from app.core.coerce import _int0


def _text(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _loads_safe(value: Any) -> Any:
    """jsonb 读回双态(dict/list 或 json 字符串)宽容解析;坏 json 诚实 None。"""
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _bet_brief(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "outcome_id": _int0(row.get("id")),
        "product_sku": _text(row.get("product_sku"), 80) or None,
        "market": _text(row.get("market"), 40) or None,
        "channel": _text(row.get("channel"), 40) or None,
        "content_angle": _text(row.get("content_angle"), 80) or None,
        "action_type": _text(row.get("action_type"), 40) or None,
        "decision": _text(row.get("decision"), 20),
        "lesson": _text(row.get("lesson"), 200) or None,
        "decided_at": _text(row.get("decided_at"), 40) or None,
    }


# ── 分组胜率(decision 口径 + 样本纪律) ─────────────────────────────


def _group_stats(decided: list[dict[str, Any]], dim: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = {}
    for row in decided:
        key = _text(row.get(dim), 80) or "(未标)"
        b = buckets.setdefault(key, {"n": 0, "wins": 0, "losses": 0, "neutral": 0})
        b["n"] += 1
        decision = _text(row.get("decision"), 20)
        if decision in WIN_DECISIONS:
            b["wins"] += 1
        elif decision in LOSS_DECISIONS:
            b["losses"] += 1
        else:
            b["neutral"] += 1
    out = []
    for key, b in buckets.items():
        insufficient = b["n"] < MIN_SAMPLE
        out.append({
            "value": key,
            "n": b["n"],
            "wins": b["wins"],
            "losses": b["losses"],
            "neutral": b["neutral"],
            "win_rate": round(b["wins"] / b["n"], 4) if b["n"] > 0 else None,
            "insufficient": insufficient,
            "note": f"样本 {b['n']} < {MIN_SAMPLE},胜率仅展示不背书" if insufficient else "",
        })
    out.sort(key=lambda g: (-g["n"], -(g["win_rate"] or 0)))
    return out


def _sufficient(groups: dict[str, list[dict[str, Any]]], *, min_rate: float | None = None,
                max_rate: float | None = None) -> list[dict[str, Any]]:
    hits = []
    for dim, entries in groups.items():
        for g in entries:
            if g["insufficient"] or g["win_rate"] is None:
                continue
            if min_rate is not None and g["win_rate"] < min_rate:
                continue
            if max_rate is not None and g["win_rate"] > max_rate:
                continue
            hits.append({"dim": dim, **{k: g[k] for k in ("value", "n", "wins", "losses", "win_rate")}})
    hits.sort(key=lambda g: -(g["win_rate"] or 0))
    return hits


# ── 三段构建 ─────────────────────────────────────────────────────────


def _build_what_to_change(period_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """下次怎么改 = decided 行 next_weight_change 结构化条目汇总(只汇总,不生效)。"""
    entries: list[dict[str, Any]] = []
    for row in period_rows:
        change = _loads_safe(row.get("next_weight_change"))
        if not change:
            continue
        entries.append({
            "outcome_id": _int0(row.get("id")),
            "decision": _text(row.get("decision"), 20),
            "action_type": _text(row.get("action_type"), 40) or None,
            "market": _text(row.get("market"), 40) or None,
            "channel": _text(row.get("channel"), 40) or None,
            "content_angle": _text(row.get("content_angle"), 80) or None,
            "change": change,
        })
    return {
        "status": "ready" if entries else "empty",
        "count": len(entries),
        "items": entries[:_ITEM_CAP],
        "effect_chain_note": (
            "本段只做结构化汇总;权重真回流走既有 recommendation_feedback 生效链(L4 桥接),"
            f"且带样本闸——样本 <{MIN_SAMPLE} 绝不改权重。"
        ),
    }


def _build_headline(*, period_rows: list[dict[str, Any]], decided_total: int,
                    open_total: int, groups: dict[str, list[dict[str, Any]]],
                    days: int) -> str:
    if not period_rows:
        return (
            f"近 {days} 天 0 条裁决(累计已裁决 {decided_total}、未裁决 {open_total})——"
            "三窗素材照常回填,等人工裁决进账后本报告才有对错可讲。"
        )
    by_decision: dict[str, int] = {}
    for row in period_rows:
        d = _text(row.get("decision"), 20)
        by_decision[d] = by_decision.get(d, 0) + 1
    wins = sum(by_decision.get(d, 0) for d in WIN_DECISIONS)
    losses = sum(by_decision.get(d, 0) for d in LOSS_DECISIONS)
    parts = ", ".join(f"{k} {v}" for k, v in sorted(by_decision.items(), key=lambda kv: -kv[1]))
    credible = _sufficient(groups, min_rate=0.0)
    return (
        f"近 {days} 天裁决 {len(period_rows)} 条(对 {wins} / 错 {losses};{parts});"
        f"累计已裁决 {decided_total}、未裁决 {open_total};样本≥{MIN_SAMPLE} 的可信组合 {len(credible)} 个。"
    )


# ── 主入口(纯读) ───────────────────────────────────────────────────


def weekly_report(days: int = _DEFAULT_DAYS) -> dict[str, Any]:
    """周对答案报告:分组胜率 + 对了什么/错了什么/下次怎么改 三段 + headline。

    days 为「本期」窗口(按 decided_at,默认 7 天;days<=0 = 全量);分组胜率吃
    **全部已裁决行**(累计口径,样本更足),三段吃本期行。全只读。
    """
    from app.db.connection import get_conn, table_exists

    now = datetime.now(timezone.utc)
    period_days = _int0(days)
    if period_days <= 0:
        period_days = 0
    empty_sections = {
        "groups": {dim: [] for dim in GROUP_DIMS},
        "what_worked": {"status": "empty", "items": [], "group_highlights": []},
        "what_failed": {"status": "empty", "items": [], "group_highlights": []},
        "what_to_change": {"status": "empty", "count": 0, "items": [], "effect_chain_note": ""},
    }
    if not table_exists(TABLE):
        return {
            "status": "empty",
            "reason": f"{TABLE} 未建(L2 迁移 217 并行在建);建表并有裁决后本报告自动出数。",
            "generated_at": now.isoformat(),
            "period": {"days": period_days or _DEFAULT_DAYS, "since": None},
            "totals": {"decided_total": 0, "open_total": 0, "decided_in_period": 0},
            "headline": "GTM 结果账本未建表,周对答案待 L2 迁移落地。",
            "method": _METHOD_NOTE,
            **empty_sections,
        }

    conn = get_conn()
    decided_placeholders = ",".join("?" for _ in DECIDED_DECISIONS)
    try:
        decided = [dict(r) for r in conn.execute(
            f"""
            SELECT id, gtm_plan_id, product_sku, market, segment, channel,
                   action_type, content_angle, decision, lesson,
                   next_weight_change, created_at, decided_at, decided_by
            FROM {TABLE}
            WHERE decision IN ({decided_placeholders})
            ORDER BY id DESC
            """,
            tuple(DECIDED_DECISIONS),
        ).fetchall()]
        open_row = conn.execute(
            f"""
            SELECT COUNT(*) AS n FROM {TABLE}
            WHERE decision IS NULL OR decision NOT IN ({decided_placeholders})
            """,
            tuple(DECIDED_DECISIONS),
        ).fetchone()
        open_total = _int0(dict(open_row).get("n")) if open_row else 0
    except Exception as exc:  # noqa: BLE001 — 报告失败诚实回原因,不 500
        logger.warning("weekly_answers.load_failed: %s", exc)
        return {
            "status": "error", "reason": _text(str(exc), 300),
            "generated_at": now.isoformat(), "method": _METHOD_NOTE, **empty_sections,
            "headline": "周对答案读取失败(见 reason)。",
        }

    since = (now - timedelta(days=period_days)) if period_days > 0 else None
    if since is None:
        period_rows = list(decided)
    else:
        period_rows = []
        for row in decided:
            decided_at = _parse_dt(row.get("decided_at"))
            if decided_at is not None and decided_at >= since:
                period_rows.append(row)

    groups = {dim: _group_stats(decided, dim) for dim in GROUP_DIMS}

    worked_items = [_bet_brief(r) for r in period_rows if _text(r.get("decision"), 20) in WIN_DECISIONS]
    failed_items = [_bet_brief(r) for r in period_rows if _text(r.get("decision"), 20) in LOSS_DECISIONS]
    what_worked = {
        "status": "ready" if worked_items else "empty",
        "items": worked_items[:_ITEM_CAP],
        "group_highlights": _sufficient(groups, min_rate=0.6),
        "note": "对了什么=本期 validated/escalate 裁决 + 样本≥5 且胜率≥60% 的组合。",
    }
    what_failed = {
        "status": "ready" if failed_items else "empty",
        "items": failed_items[:_ITEM_CAP],
        "group_highlights": _sufficient(groups, max_rate=0.4),
        "note": "错了什么=本期 failed/retreat 裁决 + 样本≥5 且胜率≤40% 的组合;lesson 原话随行。",
    }
    what_to_change = _build_what_to_change(period_rows)
    headline = _build_headline(
        period_rows=period_rows, decided_total=len(decided), open_total=open_total,
        groups=groups, days=period_days or 0)

    return {
        "status": "ready" if decided else "empty",
        "reason": "" if decided else "账本尚无任何已裁决行(decision 全 open)——先裁决,后对答案。",
        "generated_at": now.isoformat(),
        "period": {
            "days": period_days,
            "since": since.isoformat() if since else None,
            "scope_note": "分组胜率=累计全部已裁决行;三段=本期(decided_at 窗口)行。",
        },
        "totals": {
            "decided_total": len(decided),
            "open_total": open_total,
            "decided_in_period": len(period_rows),
        },
        "method": _METHOD_NOTE,
        "groups": groups,
        "what_worked": what_worked,
        "what_failed": what_failed,
        "what_to_change": what_to_change,
        "headline": headline,
        "note": (
            "纯读报告:胜率是裁决的统计不是承诺;样本不足纪律照旧(<5 标 insufficient);"
            "接晨报/记分卡由收口刀接线,本模块只出结构。"
        ),
    }


__all__ = ["weekly_report", "WIN_DECISIONS", "LOSS_DECISIONS", "NEUTRAL_DECISIONS",
           "DECIDED_DECISIONS", "MIN_SAMPLE", "GROUP_DIMS"]
