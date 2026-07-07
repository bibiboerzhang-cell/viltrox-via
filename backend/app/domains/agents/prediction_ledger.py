"""预测台账(prediction_ledger v1)—— 「系统的预测有多准」,驾照升降级的数据引擎。

把散落在真实表里的「预测→结果」对齐成台账条目,按 action_type 分组算近 window 次命中率。
纯 SQL 聚合已有数据,零 LLM、零新采集、零写库、零外部动作。

分组口径(2026-07-06 真库侦察,每组命中定义写进 basis,可追溯):
  kol_recommend        vkpi_recommendation_outcomes(778 行)+ vkpi_recommendation_feedback(1 行)。
                       预测=系统推荐该 KOL 值得推进;命中=任一正向业务节点为真
                       (shortlisted/claimed/outreach/reply/agreement/published/order,
                       由 refresh_open_outcomes 每日从真实业务行回流)或正向人工反馈;
                       未命中=was_rejected 为真或负向反馈;无任何裁决=pending 不计分母。
  market_bet           vkpi_bet_ledger(Market Brain 押注,2 行)。预测=hypothesis+probability;
                       命中=outcome won;未命中=lost;void 作废剔除;open=pending。
  alert_signal         vkpi_alerts(86 行)。预测=规则判定「此事需要关注」;无误报标签,
                       采用保守「处理闭环率」代理口径:resolved=命中,仍 open=未命中计入分母
                       (宁可压低命中率,防止驾照虚高)。
  brand_signal         vkpi_brand_signal(侦察时 0 行,诚实空组)。命中=已 review 且采取了行动;
                       未命中=review 后明确弃置;未 review=pending。
  performance_forecast 第4轮预测战绩(evidence_quantile_v1):预测在 /kol-pool/{id}/forecast
                       在线计算、不落库,连 prediction 行都不存在 → 永远诚实 pending
                       (有预测能力、无 actual 可对答案),绝不编命中率。
  <auto_ops 类别>       vkpi_action_execution_ledger(33 行)动态兜底:action_type 匹配 category
                       时,mode=executed 的行 success=命中、failed/error=未命中;
                       skipped 与 dry_run 剔除(闸门拒绝/演练不是预测裁决)。

诚实态:样本<5 → confidence="insufficient"(驾照引擎据此绝不升级);判不出的组
status="empty"/"pending" + sample_count=0,绝不编数。数据荒是常态,空得起。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(不用 LIKE,用 POSITION);
BOOLEAN 读回可能是 int 1/0,判断走 _truthy;函数内懒 import get_conn。

红线(永久):本台账只读聚合、绝不写库;台账结果只用于自主驾照判定与展示,
「影响评分」永久 NO —— 绝不回写 fit 评分存储列、绝不改 rule_v0 打分逻辑,
命中率再高也不构成改评分的许可;零 LLM 调用;不执行任何外部动作。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "prediction_ledger_v1"

# 判定阈值(全部进 basis,可追溯)
MIN_SAMPLE_FOR_CONFIDENCE = 5   # 样本<5 = insufficient(驾照绝不升级)
LOW_CONFIDENCE_CEILING = 20     # 5~19 = low
MEDIUM_CONFIDENCE_CEILING = 50  # 20~49 = medium;>=50 = high
DEFAULT_WINDOW = 20
MAX_WINDOW = 200
SCAN_LIMIT = 5000               # 每组扫描行数上限(判决在 Python 侧,表都很小)

# kol_recommend 正向业务节点(refresh_open_outcomes 从真实业务行回流的 BOOLEAN 列)
_POSITIVE_OUTCOME_COLS: tuple[str, ...] = (
    "was_shortlisted", "was_claimed", "outreach_sent", "reply_received",
    "agreement_reached", "content_published", "order_attributed",
)
# 人工反馈词表(vkpi_recommendation_feedback.feedback_type;侦察实见 shortlist)
_POSITIVE_FEEDBACK_TYPES = frozenset({"shortlist", "accept", "accepted", "adopt", "like", "positive", "promote", "claim"})
_NEGATIVE_FEEDBACK_TYPES = frozenset({"reject", "rejected", "dismiss", "dismissed", "negative", "exclude", "downvote"})

# brand_signal 里视为「弃置」的 action_taken(review 过但明确不行动 = 信号未命中)
_SIGNAL_DISMISSED = frozenset({"dismissed", "ignored", "none", "no_action", "false_positive"})

# auto_ops 执行台账 outcome 词表
_EXEC_HIT = frozenset({"success", "succeeded", "ok", "done"})
_EXEC_MISS = frozenset({"failed", "failure", "error", "timeout"})


# ── 小工具(与 signature_profile / performance_forecast 同款容错约定)──


from app.core.coerce import _truthy


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _confidence(sample_count: int) -> str:
    """置信度阶梯:<5 insufficient(驾照绝不升级)/ <20 low / <50 medium / >=50 high。"""
    if sample_count <= 0:
        return "none"
    if sample_count < MIN_SAMPLE_FOR_CONFIDENCE:
        return "insufficient"
    if sample_count < LOW_CONFIDENCE_CEILING:
        return "low"
    if sample_count < MEDIUM_CONFIDENCE_CEILING:
        return "medium"
    return "high"


def _clamp_window(window: Any) -> int:
    try:
        return max(1, min(int(window), MAX_WINDOW))
    except Exception:
        return DEFAULT_WINDOW


def _finalize(
    *,
    action_type: str,
    label: str,
    window: int,
    judged: list[dict[str, Any]],
    pending_count: int,
    basis_extra: dict[str, Any],
    scanned: int,
) -> dict[str, Any]:
    """把「已裁决条目列表(新→旧)+ pending 计数」收敛成契约 dict(status/hit_rate/sample_count/confidence/basis)。

    judged 每条:{"hit": bool, "at": str|None}。取最近 window 条计命中率;0 条裁决时诚实
    pending/empty,hit_rate=None 绝不编数。
    """
    recent = judged[:window]
    sample_count = len(recent)
    hits = sum(1 for item in recent if item.get("hit"))
    hit_rate = round(hits / sample_count, 4) if sample_count > 0 else None
    if sample_count > 0:
        status = "ok"
    elif pending_count > 0:
        status = "pending"
    else:
        status = "empty"
    stamps = [str(item.get("at") or "") for item in recent if item.get("at")]
    basis: dict[str, Any] = {
        "method": METHOD,
        "window": window,
        "hits": hits,
        "misses": sample_count - hits,
        "judged_total": len(judged),
        "pending_count": pending_count,
        "scanned": scanned,
        "window_span": {"oldest": min(stamps) if stamps else None, "newest": max(stamps) if stamps else None},
        "min_sample_for_confidence": MIN_SAMPLE_FOR_CONFIDENCE,
        "generated_at": _utcnow_iso(),
    }
    basis.update(basis_extra)
    return {
        "action_type": action_type,
        "label": label,
        "status": status,
        "hit_rate": hit_rate,
        "sample_count": sample_count,
        "confidence": _confidence(sample_count),
        "basis": basis,
    }


def _error_group(action_type: str, label: str, exc: Exception) -> dict[str, Any]:
    """聚合内部异常不外抛:诚实 error 组(hit_rate=None),驾照引擎见非 ok 一律不升级。"""
    logger.warning("prediction_ledger group failed action_type=%s: %s", action_type, exc)
    return {
        "action_type": action_type,
        "label": label,
        "status": "error",
        "hit_rate": None,
        "sample_count": 0,
        "confidence": "none",
        "basis": {"method": METHOD, "reason": str(exc)[:300], "generated_at": _utcnow_iso()},
    }


# ── 各组收集器(全只读;每组独立 try/except,单组塌了不拖垮台账)──────


def _collect_kol_recommend(conn: Any, window: int) -> dict[str, Any]:
    """kol_recommend:推荐 outcome 业务节点 + 人工反馈 → 裁决;两者皆无 = pending。"""
    label = "KOL 推荐"
    try:
        fb_rows = conn.execute(
            "SELECT recommendation_id, feedback_type FROM vkpi_recommendation_feedback ORDER BY id DESC LIMIT ?",
            (SCAN_LIMIT,),
        ).fetchall()
        fb_map: dict[int, set[str]] = {}
        for raw in fb_rows:
            row = dict(raw)
            rec_id = int(row.get("recommendation_id") or 0)
            if rec_id > 0:
                fb_map.setdefault(rec_id, set()).add(str(row.get("feedback_type") or "").strip().lower())
        out_rows = conn.execute(
            """
            SELECT recommendation_id, recommended_at,
                   was_shortlisted, was_rejected, was_claimed, outreach_sent, reply_received,
                   agreement_reached, content_published, order_attributed
            FROM vkpi_recommendation_outcomes
            ORDER BY id DESC
            LIMIT ?
            """,
            (SCAN_LIMIT,),
        ).fetchall()
        judged: list[dict[str, Any]] = []
        pending = 0
        for raw in out_rows:
            row = dict(raw)
            rec_id = int(row.get("recommendation_id") or 0)
            fb_types = fb_map.get(rec_id, set())
            positive = any(_truthy(row.get(col)) for col in _POSITIVE_OUTCOME_COLS) or bool(fb_types & _POSITIVE_FEEDBACK_TYPES)
            negative = _truthy(row.get("was_rejected")) or bool(fb_types & _NEGATIVE_FEEDBACK_TYPES)
            if positive:
                # 正负并存时按正向计(业务已推进说明推荐有效;负反馈另可在 note 层追查)
                judged.append({"hit": True, "at": _iso(row.get("recommended_at"))})
            elif negative:
                judged.append({"hit": False, "at": _iso(row.get("recommended_at"))})
            else:
                pending += 1
        return _finalize(
            action_type="kol_recommend", label=label, window=window,
            judged=judged, pending_count=pending, scanned=len(out_rows),
            basis_extra={
                "source": ["vkpi_recommendation_outcomes", "vkpi_recommendation_feedback"],
                "hit_definition": "任一正向业务节点为真(shortlisted/claimed/outreach/reply/agreement/published/order,来自 refresh_open_outcomes 真实业务行回流)或正向人工反馈(shortlist/accept 等)",
                "miss_definition": "was_rejected 为真或负向人工反馈(reject/dismiss 等)",
                "pending_definition": "既无正向节点也无裁决反馈的推荐(展示过但没人对答案),不计分母",
            },
        )
    except Exception as exc:  # noqa: BLE001 — 单组失败诚实降级
        return _error_group("kol_recommend", label, exc)


def _collect_market_bet(conn: Any, window: int) -> dict[str, Any]:
    """market_bet:Market Brain 押注台账,outcome won/lost 裁决,open 待复盘,void 作废剔除。"""
    label = "市场押注"
    try:
        rows = conn.execute(
            "SELECT outcome, review_at, created_at, updated_at FROM vkpi_bet_ledger ORDER BY id DESC LIMIT ?",
            (SCAN_LIMIT,),
        ).fetchall()
        judged: list[dict[str, Any]] = []
        pending = 0
        voided = 0
        for raw in rows:
            row = dict(raw)
            outcome = str(row.get("outcome") or "").strip().lower()
            at = _iso(row.get("updated_at")) or _iso(row.get("review_at")) or _iso(row.get("created_at"))
            if outcome == "won":
                judged.append({"hit": True, "at": at})
            elif outcome == "lost":
                judged.append({"hit": False, "at": at})
            elif outcome == "void":
                voided += 1
            else:  # open 或未知值一律按待复盘
                pending += 1
        return _finalize(
            action_type="market_bet", label=label, window=window,
            judged=judged, pending_count=pending, scanned=len(rows),
            basis_extra={
                "source": ["vkpi_bet_ledger"],
                "hit_definition": "outcome=won(到期复盘 scan_due_bets 逼出的裁决)",
                "miss_definition": "outcome=lost",
                "pending_definition": "outcome=open(未到 review_at 或未复盘)",
                "voided": voided,
                "note": "void 作废注剔除,不进分母",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _error_group("market_bet", label, exc)


def _collect_alert_signal(conn: Any, window: int) -> dict[str, Any]:
    """alert_signal:规则告警。无误报标签 → 保守「处理闭环率」代理口径(open 计未命中)。"""
    label = "规则告警信号"
    try:
        rows = conn.execute(
            "SELECT status, resolved_at, created_at FROM vkpi_alerts ORDER BY id DESC LIMIT ?",
            (SCAN_LIMIT,),
        ).fetchall()
        judged: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            resolved = str(row.get("status") or "").strip().lower() == "resolved" or bool(_iso(row.get("resolved_at")))
            judged.append({"hit": resolved, "at": _iso(row.get("created_at"))})
        return _finalize(
            action_type="alert_signal", label=label, window=window,
            judged=judged, pending_count=0, scanned=len(rows),
            basis_extra={
                "source": ["vkpi_alerts"],
                "hit_definition": "status=resolved 或 resolved_at 非空(告警被确认处理=信号有效)",
                "miss_definition": "仍 open 的告警保守计未命中(该表无误报标签,宁压低命中率防驾照虚高)",
                "pending_definition": "无(保守口径下所有告警都进分母)",
                "note": "代理口径=处理闭环率,非严格预测命中率;仅可用于保守判定,不可作为升级唯一依据",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _error_group("alert_signal", label, exc)


def _collect_brand_signal(conn: Any, window: int) -> dict[str, Any]:
    """brand_signal:品牌信号检出。review 后行动=命中,review 后弃置=未命中,未 review=pending。"""
    label = "品牌信号"
    try:
        rows = conn.execute(
            "SELECT reviewed_at, action_taken, detected_at FROM vkpi_brand_signal ORDER BY id DESC LIMIT ?",
            (SCAN_LIMIT,),
        ).fetchall()
        judged: list[dict[str, Any]] = []
        pending = 0
        for raw in rows:
            row = dict(raw)
            reviewed = bool(_iso(row.get("reviewed_at")))
            action = str(row.get("action_taken") or "").strip().lower()
            at = _iso(row.get("reviewed_at")) or _iso(row.get("detected_at"))
            if not reviewed:
                pending += 1
            elif action and action not in _SIGNAL_DISMISSED:
                judged.append({"hit": True, "at": at})
            else:
                judged.append({"hit": False, "at": at})
        return _finalize(
            action_type="brand_signal", label=label, window=window,
            judged=judged, pending_count=pending, scanned=len(rows),
            basis_extra={
                "source": ["vkpi_brand_signal"],
                "hit_definition": "reviewed_at 非空且 action_taken 为实质行动(非 dismissed/ignored/none)",
                "miss_definition": "review 后明确弃置(action_taken 空或 dismissed/ignored/false_positive)",
                "pending_definition": "检出但尚未 review",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _error_group("brand_signal", label, exc)


def _collect_performance_forecast(_conn: Any, window: int) -> dict[str, Any]:
    """performance_forecast:预测能力存在但预测不落库 → 永远诚实「待对答案」。

    第4轮预测战绩(evidence_quantile_v1)在 /api/admin/vkpi/kol-pool/{id}/forecast 在线计算,
    既不写 vkpi_analysis_cache 也无专表(2026-07-06 真库侦察确认零 forecast 行),
    所以连「预测」侧都没有留痕,更没有 actual 侧可对齐 —— 有预测无 actual,sample_count=0,
    绝不编命中率。待预测落库+结果回流后本组自动转正(收集器已预留)。
    """
    return {
        "action_type": "performance_forecast",
        "label": "KOL 表现预测",
        "status": "pending",
        "hit_rate": None,
        "sample_count": 0,
        "confidence": "none",
        "basis": {
            "method": METHOD,
            "window": window,
            "hits": 0,
            "misses": 0,
            "judged_total": 0,
            "pending_count": 0,
            "scanned": 0,
            "source": ["(无落库表)在线计算于 app.domains.kol.performance_forecast(evidence_quantile_v1)"],
            "hit_definition": "待定义:预测 p10/p50/p90 落库后,以实际播放落入区间为命中",
            "miss_definition": "待定义:实际播放落在预测区间外",
            "pending_definition": "预测在线计算不落库,无 prediction 行、无 actual 可对答案 → 整组待对答案",
            "note": "有预测能力、无对答案链路;诚实 pending,绝不编数",
            "min_sample_for_confidence": MIN_SAMPLE_FOR_CONFIDENCE,
            "generated_at": _utcnow_iso(),
        },
    }


def _collect_auto_ops_category(conn: Any, category: str, window: int) -> dict[str, Any] | None:
    """auto_ops 动态兜底:action_type 匹配 vkpi_action_execution_ledger.category。

    只认 mode=executed 的真实执行(dry_run 是演练、skipped 是闸门拒绝,都不是预测裁决,剔除);
    outcome success=命中,failed/error=未命中。该表 0 行匹配 → 返回 None(调用方给 unknown)。
    """
    rows = conn.execute(
        "SELECT mode, outcome, created_at FROM vkpi_action_execution_ledger WHERE category = ? ORDER BY id DESC LIMIT ?",
        (str(category), SCAN_LIMIT),
    ).fetchall()
    if not rows:
        return None
    judged: list[dict[str, Any]] = []
    excluded = 0
    for raw in rows:
        row = dict(raw)
        mode = str(row.get("mode") or "").strip().lower()
        outcome = str(row.get("outcome") or "").strip().lower()
        at = _iso(row.get("created_at"))
        if mode != "executed" or outcome in ("skipped", "skip"):
            excluded += 1
            continue
        if outcome in _EXEC_HIT:
            judged.append({"hit": True, "at": at})
        elif outcome in _EXEC_MISS:
            judged.append({"hit": False, "at": at})
        else:
            excluded += 1
    return _finalize(
        action_type=str(category), label=f"自动执行 · {category}", window=window,
        judged=judged, pending_count=0, scanned=len(rows),
        basis_extra={
            "source": ["vkpi_action_execution_ledger"],
            "hit_definition": "mode=executed 且 outcome=success(真实执行成功)",
            "miss_definition": "mode=executed 且 outcome=failed/error",
            "pending_definition": "dry_run 演练与 skipped 闸门拒绝剔除,不进分母",
            "excluded": excluded,
            "note": "执行可靠率口径(该 category 无预测-结果对,退而用真实执行成败)",
        },
    )


def _auto_ops_categories(conn: Any) -> list[str]:
    rows = conn.execute(
        "SELECT category, COUNT(*) AS n FROM vkpi_action_execution_ledger GROUP BY category ORDER BY category",
    ).fetchall()
    return [str(dict(r).get("category") or "") for r in rows if str(dict(r).get("category") or "")]


# 具名组注册表(顺序即摘要展示顺序)
_NAMED_COLLECTORS: tuple[tuple[str, Any], ...] = (
    ("kol_recommend", _collect_kol_recommend),
    ("market_bet", _collect_market_bet),
    ("alert_signal", _collect_alert_signal),
    ("brand_signal", _collect_brand_signal),
    ("performance_forecast", _collect_performance_forecast),
)
_NAMED_MAP = dict(_NAMED_COLLECTORS)


# ── 对外契约(兄弟件 A 驾照引擎按此调用;永不 raise,永远回 dict)──────


def hit_rate_for(action_type: str, window: int = 20) -> dict[str, Any]:
    """某 action_type 近 window 次预测命中率(契约键:status/hit_rate/sample_count/confidence/basis)。

    解析顺序:具名组(kol_recommend/market_bet/alert_signal/brand_signal/performance_forecast)
    → vkpi_action_execution_ledger 按 category 动态兜底 → unknown_action_type(诚实未知)。
    驾照引擎约定:status != "ok" 或 confidence in ("none","insufficient") 一律不得作为升级依据。
    红线:只读;结果永不回写任何评分列、不碰 rule_v0(「影响评分」永久 NO)。
    """
    key = str(action_type or "").strip().lower()
    win = _clamp_window(window)
    if not key:
        return {
            "action_type": "", "label": "", "status": "unknown_action_type",
            "hit_rate": None, "sample_count": 0, "confidence": "none",
            "basis": {"method": METHOD, "reason": "action_type 为空", "generated_at": _utcnow_iso()},
        }
    try:
        from app.db.connection import get_conn

        conn = get_conn()
        collector = _NAMED_MAP.get(key)
        if collector is not None:
            return collector(conn, win)
        dynamic = _collect_auto_ops_category(conn, key, win)
        if dynamic is not None:
            return dynamic
        return {
            "action_type": key, "label": key, "status": "unknown_action_type",
            "hit_rate": None, "sample_count": 0, "confidence": "none",
            "basis": {
                "method": METHOD, "window": win,
                "reason": "该 action_type 既非具名台账组,执行台账也无此 category 的行(诚实未知,不编数)",
                "known_groups": [name for name, _fn in _NAMED_COLLECTORS],
                "generated_at": _utcnow_iso(),
            },
        }
    except Exception as exc:  # noqa: BLE001 — 契约:永不 raise
        return _error_group(key, key, exc)


def ledger_summary() -> dict[str, Any]:
    """全部分组的台账摘要(具名组 + 执行台账动态 category 组),供面板与驾照引擎总览。

    每组即 hit_rate_for 同款 dict;空组诚实列出(sample_count=0),绝不编数、绝不隐藏数据荒。
    永不 raise:整体失败回 {status:"error"}。
    """
    try:
        from app.db.connection import get_conn

        conn = get_conn()
        groups: list[dict[str, Any]] = []
        for name, collector in _NAMED_COLLECTORS:
            groups.append(collector(conn, DEFAULT_WINDOW))
        named_keys = {name for name, _fn in _NAMED_COLLECTORS}
        try:
            for category in _auto_ops_categories(conn):
                if category.strip().lower() in named_keys:
                    continue
                dynamic = _collect_auto_ops_category(conn, category, DEFAULT_WINDOW)
                if dynamic is not None:
                    groups.append(dynamic)
        except Exception as exc:  # noqa: BLE001 — 动态组失败不拖垮具名组
            logger.warning("prediction_ledger auto_ops categories failed: %s", exc)
        judged_total = sum(int(g.get("basis", {}).get("judged_total") or 0) for g in groups)
        pending_total = sum(int(g.get("basis", {}).get("pending_count") or 0) for g in groups)
        return {
            "status": "ok",
            "method": METHOD,
            "generated_at": _utcnow_iso(),
            "window": DEFAULT_WINDOW,
            "groups": groups,
            "totals": {
                "groups": len(groups),
                "judged_total": judged_total,
                "pending_total": pending_total,
                "groups_with_sample": sum(1 for g in groups if int(g.get("sample_count") or 0) > 0),
            },
            "note": "预测→结果台账(只读聚合真实表,零 LLM);样本<5 = insufficient,驾照引擎不得据此升级;台账永不影响任何评分(影响评分=NO)。",
        }
    except Exception as exc:  # noqa: BLE001 — 契约:永不 raise
        logger.warning("prediction_ledger summary failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "groups": [], "generated_at": _utcnow_iso()}
