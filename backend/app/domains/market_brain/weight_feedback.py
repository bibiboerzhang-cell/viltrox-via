"""闭环波 L4 权重回流桥 — next_weight_change 结构化条目 → recommendation_feedback 既有生效链。

apply_weight_change(outcome_row, dry_run=True):把一条 vkpi_gtm_outcomes 行(dict)里的
next_weight_change(jsonb,单条 dict 或条目 list)逐条桥接到学习闭环真链:

  真链 = vkpi_recommendation_feedback 落行(actions._record_action_feedback_once,幂等)
       → recommendation_use_case.FEEDBACK_SCORE / product_analysis.FEEDBACK_SCORE_ADJUSTMENTS
         打分时按 feedback_type 计入权重调整(既有生效链,本模块零新评分逻辑)。

条目形状(容错,全字段可缺):
  {"target": "kol", "kol_pool_id": 123, "direction": "down",
   "reason": "该类 KOL 回复率低于目标", "sample_count": 12}
  - direction: up=升权 / down=降权;v1 保守只映射两个温和档:
      up   → positive_signal(生效链 +5/+8)
      down → snooze(生效链 -8/-10)
    绝不自动写 reject(-20/-25)级重罚——重罚只能人工在推荐卡上点。
  - sample_count: 条目自带(L3 周报分组算好)优先;缺则按本行 channel+action_type
    统计 vkpi_gtm_outcomes 已裁决行数;表缺=0。
  - 非 KOL 目标(region/channel/content_style 等)v1 只结构化记录(recorded_only),
    不硬塞进 KOL 推荐链——诚实缺席,复杂权重引擎不抢跑。

人审红线(用户点名「保证审」):
  1. 未裁决的 outcome(decided_at 空且 decision 非终态)全部 hold_undecided,绝不回流 ——
     权重只从人工裁决过的结果流出;裁决本身只能人工 POST(L2 端点),本模块无任何裁决路径。
  2. 样本闸:sample_count < MIN_SAMPLE(复用 prediction_ledger 统计功效纪律 =5)
     强制 hold_insufficient_sample,绝不小样本改权重(dry_run 也如实标 hold)。
  3. dry_run=True(默认)纯预览零写库;dry_run=False 也只写 vkpi_recommendation_feedback
     一张表(既有生效链自己的台账),绝不写 viltrox_fit_score、不碰 rule_v0。

compat 约定:SQL 占位符 ?、全文零 percent 字符;jsonb 读回可能是 dict 或 str,_loads 容错。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 样本闸:复用预测台账的统计功效纪律(样本<5 = insufficient,绝不改权重)。
try:
    from app.domains.agents.prediction_ledger import MIN_SAMPLE_FOR_CONFIDENCE as MIN_SAMPLE
except Exception:  # pragma: no cover — 兄弟件缺席时兜底同一纪律值
    MIN_SAMPLE = 5

# 方向 → 生效链词表(仅温和档;词表必须是 FEEDBACK_SCORE 已认识的 key)。
_DIRECTION_FEEDBACK: dict[str, str] = {"up": "positive_signal", "down": "snooze"}

# 已裁决终态(decision 文本);open/pending/空 = 未裁决。
_DECIDED_DECISIONS = {"validated", "failed", "partial", "retry", "escalate", "retreat"}

_OUTCOME_TABLE = "vkpi_gtm_outcomes"


# ── 小工具(容错解析) ────────────────────────────────────────────────


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _int0(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _text(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _entries(raw: Any) -> list[dict[str, Any]]:
    """next_weight_change 归一化:单条 dict → [dict];list 过滤非 dict;其余 → []。"""
    parsed = _loads(raw, default=None)
    if isinstance(parsed, dict):
        return [parsed] if parsed else []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict) and item]
    return []


def _is_decided(outcome_row: dict[str, Any]) -> bool:
    """人审红线:只有人工裁决过的行才允许回流(decided_at 或终态 decision 二证其一)。"""
    if outcome_row.get("decided_at") not in (None, ""):
        return True
    return _text(outcome_row.get("decision"), 30).lower() in _DECIDED_DECISIONS


def _computed_sample(outcome_row: dict[str, Any]) -> int:
    """按本行 channel+action_type 统计已裁决样本数(表缺/列漂移诚实 0 → 触发 hold)。"""
    channel = _text(outcome_row.get("channel"), 60)
    action_type = _text(outcome_row.get("action_type"), 60)
    if not channel and not action_type:
        return 0
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists(_OUTCOME_TABLE):
            return 0
        row = get_conn().execute(
            """
            SELECT COUNT(*) AS c FROM vkpi_gtm_outcomes
            WHERE decided_at IS NOT NULL AND channel = ? AND action_type = ?
            """,
            (channel, action_type),
        ).fetchone()
        return _int0(dict(row).get("c")) if row else 0
    except Exception:
        logger.warning("weight_feedback sample count failed", exc_info=True)
        return 0


def _entry_sample(entry: dict[str, Any], outcome_row: dict[str, Any]) -> tuple[int, str]:
    """样本数:条目自带优先(L3 分组算好),缺则按 channel+action_type 现算。"""
    for key in ("sample_count", "sample", "n"):
        if key in entry:
            return max(0, _int0(entry.get(key))), "entry"
    return _computed_sample(outcome_row), "computed"


# ── 主入口 ────────────────────────────────────────────────────────────


def apply_weight_change(
    outcome_row: dict[str, Any],
    dry_run: bool = True,
    *,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 outcome_row.next_weight_change 逐条桥到 recommendation_feedback 既有生效链。

    dry_run=True(默认)纯预览:每条给出 would_write/hold 判定,零写库。
    dry_run=False 只对「已裁决 + 样本达标 + 能落到真实推荐」的条目写一行
    vkpi_recommendation_feedback(复用 actions._record_action_feedback_once,幂等);
    其余条目诚实 hold/recorded_only,绝不硬写。
    """
    outcome_id = _int0(outcome_row.get("id"))
    entries = _entries(outcome_row.get("next_weight_change"))
    decided = _is_decided(outcome_row)
    results: list[dict[str, Any]] = []
    counts = {"total": len(entries), "actionable": 0, "held": 0, "recorded_only": 0, "skipped": 0}

    # 生效链真实写入函数(grep 定位:app/domains/recommendations/actions.py)。
    # 惰性 import:避免模块级拖入 actions 的重依赖(workflow/shopify 等)。
    rec_actions = None
    try:
        from app.domains.recommendations import actions as rec_actions  # noqa: PLC0415
    except Exception:  # pragma: no cover — 链模块缺席时全员 recorded_only,不炸
        logger.warning("weight_feedback: recommendations.actions import failed", exc_info=True)

    for index, entry in enumerate(entries):
        direction = _text(entry.get("direction"), 10).lower()
        feedback_type = _DIRECTION_FEEDBACK.get(direction, "")
        kol_pool_id = _int0(entry.get("kol_pool_id")) or _int0(outcome_row.get("kol_pool_id"))
        reason = _text(entry.get("reason") or entry.get("why"), 300)
        sample_count, sample_source = _entry_sample(entry, outcome_row)
        item: dict[str, Any] = {
            "index": index,
            "target": _text(entry.get("target"), 40) or ("kol" if kol_pool_id else "unspecified"),
            "kol_pool_id": kol_pool_id or None,
            "direction": direction or None,
            "feedback_type": feedback_type or None,
            "reason": reason,
            "sample_count": sample_count,
            "sample_source": sample_source,
            "min_sample": MIN_SAMPLE,
        }

        # 红线1:未裁决 outcome 一律 hold(权重只从人工裁决过的结果流出)。
        if not decided:
            item.update({"status": "hold_undecided", "note": "outcome 未人工裁决,绝不回流"})
            counts["held"] += 1
            results.append(item)
            continue
        # 红线2:样本闸——样本 < MIN_SAMPLE 强制 hold,不改权重(dry_run 也如实标)。
        if sample_count < MIN_SAMPLE:
            item.update({
                "status": "hold_insufficient_sample",
                "note": f"样本 {sample_count} < {MIN_SAMPLE},统计功效不足,不改权重",
            })
            counts["held"] += 1
            results.append(item)
            continue
        # 方向词表外(或缺 direction)= 不可自动映射,只记录。
        if not feedback_type:
            item.update({"status": "recorded_only", "note": "direction 不在 up/down 词表,结构化记录不回流"})
            counts["recorded_only"] += 1
            results.append(item)
            continue
        # 非 KOL 目标 v1 只记录(region/channel/content_style 的承接由各自模块吃)。
        if not kol_pool_id:
            item.update({"status": "recorded_only", "note": "无 kol_pool_id,非 KOL 权重 v1 只结构化记录"})
            counts["recorded_only"] += 1
            results.append(item)
            continue
        if rec_actions is None:
            item.update({"status": "recorded_only", "note": "生效链模块不可用,本条只记录"})
            counts["recorded_only"] += 1
            results.append(item)
            continue

        # 落点解析:该 pool 项最近一条推荐(生效链按 recommendation_id 记账)。
        try:
            recommendation_id = rec_actions._latest_recommendation_id_for_pool(kol_pool_id)
        except Exception:
            logger.warning("weight_feedback: recommendation lookup failed", exc_info=True)
            recommendation_id = 0
        if not recommendation_id:
            item.update({"status": "recorded_only", "note": "该 KOL 无 backing 推荐,生效链无落点"})
            counts["recorded_only"] += 1
            results.append(item)
            continue
        item["recommendation_id"] = recommendation_id
        counts["actionable"] += 1

        if dry_run:
            item.update({
                "status": "preview",
                "would_write": {
                    "table": "vkpi_recommendation_feedback",
                    "recommendation_id": recommendation_id,
                    "feedback_type": feedback_type,
                    "via": "actions._record_action_feedback_once",
                },
                "note": "dry_run 预览,未写库",
            })
            results.append(item)
            continue

        # 真回流:复用既有生效链写入函数(幂等:同推荐同类型只落一次)。
        try:
            inserted = rec_actions._record_action_feedback_once(
                recommendation_id,
                feedback_type,
                {
                    "source": "gtm_weight_feedback",
                    "gtm_outcome_id": outcome_id,
                    "weight_change": entry,
                    "sample_count": sample_count,
                },
                staff=staff,
                note=reason or f"GTM outcome {outcome_id} 权重回流",
            )
            if inserted:
                from app.db.connection import get_conn  # noqa: PLC0415

                get_conn().commit()
                item.update({"status": "applied", "note": "已写入生效链(vkpi_recommendation_feedback)"})
            else:
                item.update({"status": "skipped_duplicate", "note": "生效链已有同类型反馈,幂等跳过(防重复计权)"})
                counts["skipped"] += 1
        except Exception as exc:
            logger.warning("weight_feedback: chain write failed", exc_info=True)
            item.update({"status": "error", "note": _text(str(exc), 200)})
            counts["skipped"] += 1
        results.append(item)

    applied = sum(1 for r in results if r.get("status") == "applied")
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "gtm_outcome_id": outcome_id or None,
        "decided": decided,
        "min_sample": MIN_SAMPLE,
        "entries": results,
        "counts": {**counts, "applied": applied},
        "note": (
            "权重回流只走既有 recommendation_feedback 生效链;未裁决/样本<"
            f"{MIN_SAMPLE} 强制 hold;非 KOL 目标 v1 只结构化记录。零触 viltrox_fit_score。"
        ),
    }
