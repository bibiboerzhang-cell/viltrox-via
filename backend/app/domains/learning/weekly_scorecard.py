"""周度记分卡(weekly_scorecard_v1)—— L 轨道验收头条:「周度命中率自动出」。

在 prediction_ledger(九组预测台账)的裁决口径之上,把每组「预测→结果」按 ISO 周
分桶,输出周度命中率曲线(每组每周:判定数/命中数/命中率),另附:
  - 本周 vs 上周动量(delta 百分点 + 方向;任一周样本<5 诚实 low_sample);
  - 样本荒周诚实 sparse(0<判定数<5 标 sparse=True,绝不假装曲线可信);
  - pending 积压计数(776 条推荐待对答案就是头号信号,顶层显著呈现);
  - 谁该被催对答案:pending 最老 top5 条目(带类型/引用/天龄)。

裁决时间列(2026-07-06 真库侦察,真列名,全部按事件真实发生时刻分桶):
  kol_recommend  vkpi_recommendation_outcomes:outcome_finalized_at 全空、first_action_at
                 仅 2 行非空 → 采用「正向节点各自的 *_at」(shortlisted_at/claimed_at/
                 outreach_sent_at/reply_at/agreement_at/content_published_at/first_order_at)
                 取最早者;负向用 rejected_at;链式回落 outcome_finalized_at →
                 first_action_at → 反馈 created_at → recommended_at。
  market_bet     vkpi_bet_ledger.updated_at(复盘写回时刻)→ review_at → created_at。
  alert_signal   vkpi_alerts:resolved 用 resolved_at → updated_at → created_at;
                 仍 open 的保守计未命中,按 created_at 分桶(与台账同款保守口径)。
  brand_signal   vkpi_brand_signal.reviewed_at(未 review = pending,按 detected_at 计龄)。
  performance_forecast  预测在线计算不落库 → 永远诚实 pending,周曲线全零。
  <auto_ops 类别>  vkpi_action_execution_ledger.created_at(mode=executed 才算裁决)。

诚实态:某周 0 判定 → hit_rate=None;组内 8 周全无判定 → status=pending/empty;
守卫 import prediction_ledger,炸了本模块自带保守回落词表并在 payload 里如实标注。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent;BOOLEAN 读回可能是 int 1/0,
判断走 _truthy;timestamp 读回可能是字符串(尾缀 Z),统一 _parse_dt;函数内懒 import get_conn。

红线(永久):只读聚合、绝不写库;绝不回写 fit 评分存储列、绝不碰 rule_v0;
零 LLM;命中率只用于展示与催办,不构成改评分的许可。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "weekly_scorecard_v1"

DEFAULT_WEEKS = 8
MAX_WEEKS = 52
SCAN_LIMIT = 5000  # 每组扫描行数上限(判决在 Python 侧,相关表都很小)

# ── 守卫 import prediction_ledger:口径唯一真源;炸了用本地保守回落并如实标注 ──
try:
    from app.domains.agents import prediction_ledger as _pl

    _PL_IMPORT_ERROR: str | None = None
except Exception as _exc:  # noqa: BLE001 — 守卫:台账模块炸了记分卡仍要能出
    _pl = None  # type: ignore[assignment]
    _PL_IMPORT_ERROR = str(_exc)[:200]


def _local_truthy(value: Any) -> bool:
    """回落版 _truthy(与 prediction_ledger 同款):BOOLEAN 读回可能是 int 1/0 / 't'。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "t", "true", "yes", "y")


def _pl_attr(name: str, fallback: Any) -> Any:
    if _pl is None:
        return fallback
    return getattr(_pl, name, fallback)


_truthy = _pl_attr("_truthy", _local_truthy)
MIN_SAMPLE = int(_pl_attr("MIN_SAMPLE_FOR_CONFIDENCE", 5))
_POSITIVE_FEEDBACK_TYPES = _pl_attr(
    "_POSITIVE_FEEDBACK_TYPES",
    frozenset({"shortlist", "accept", "accepted", "adopt", "like", "positive", "promote", "claim"}),
)
_NEGATIVE_FEEDBACK_TYPES = _pl_attr(
    "_NEGATIVE_FEEDBACK_TYPES",
    frozenset({"reject", "rejected", "dismiss", "dismissed", "negative", "exclude", "downvote"}),
)
_SIGNAL_DISMISSED = _pl_attr(
    "_SIGNAL_DISMISSED", frozenset({"dismissed", "ignored", "none", "no_action", "false_positive"})
)
_EXEC_HIT = _pl_attr("_EXEC_HIT", frozenset({"success", "succeeded", "ok", "done"}))
_EXEC_MISS = _pl_attr("_EXEC_MISS", frozenset({"failed", "failure", "error", "timeout"}))

# kol_recommend 正向节点 → 该节点的真实裁决时间列(2026-07-06 侦察真列名)
_POSITIVE_EVENT_TS: tuple[tuple[str, str], ...] = (
    ("was_shortlisted", "shortlisted_at"),
    ("was_claimed", "claimed_at"),
    ("outreach_sent", "outreach_sent_at"),
    ("reply_received", "reply_at"),
    ("agreement_reached", "agreement_at"),
    ("content_published", "content_published_at"),
    ("order_attributed", "first_order_at"),
)


# ── 时间小工具 ────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_dt(value: Any) -> datetime | None:
    """compat 层 timestamp 读回可能是 datetime 也可能是 '2026-07-03T11:11:44Z' 字符串。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:19])
        except Exception:  # noqa: BLE001 — 解析不动的脏时间戳按缺失处理,不炸整组
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    parsed = _parse_dt(value)
    return parsed.isoformat() if parsed is not None else None


def _first_dt(*values: Any) -> datetime | None:
    """链式回落:按顺序取第一个能解析出来的时间。"""
    for value in values:
        parsed = _parse_dt(value)
        if parsed is not None:
            return parsed
    return None


def _week_key(dt: datetime) -> str:
    iso = dt.isocalendar()
    return str(iso[0]) + "-W" + str(int(iso[1])).zfill(2)


def _age_days(since: datetime | None) -> int | None:
    if since is None:
        return None
    return max(0, int((_utcnow() - since).total_seconds() // 86400))


def _clamp_weeks(weeks: Any) -> int:
    try:
        return max(1, min(int(weeks), MAX_WEEKS))
    except Exception:
        return DEFAULT_WEEKS


def _week_axis(weeks: int) -> list[dict[str, str]]:
    """近 weeks 个 ISO 周(旧→新,含本周),每项 {week, week_start(周一日期)}。"""
    today: date = _utcnow().date()
    monday = today - timedelta(days=today.isocalendar()[2] - 1)
    axis: list[dict[str, str]] = []
    for i in range(weeks - 1, -1, -1):
        start = monday - timedelta(days=7 * i)
        iso = start.isocalendar()
        axis.append({"week": str(iso[0]) + "-W" + str(int(iso[1])).zfill(2), "week_start": start.isoformat()})
    return axis


# ── 分桶与动量 ────────────────────────────────────────────────────────


def _bucket_weekly(judged: list[dict[str, Any]], axis: list[dict[str, str]]) -> dict[str, Any]:
    """judged 每条 {"hit": bool, "at": datetime|None} → 周桶曲线 + 范围外/无时间计数。"""
    per: dict[str, dict[str, int]] = {item["week"]: {"judged": 0, "hits": 0} for item in axis}
    in_range = 0
    out_of_range = 0
    undated = 0
    for item in judged:
        at = item.get("at")
        if at is None:
            undated += 1
            continue
        key = _week_key(at)
        bucket = per.get(key)
        if bucket is None:
            out_of_range += 1
            continue
        bucket["judged"] += 1
        in_range += 1
        if item.get("hit"):
            bucket["hits"] += 1
    weekly: list[dict[str, Any]] = []
    in_range_hits = 0
    sparse_weeks = 0
    for slot in axis:
        bucket = per[slot["week"]]
        judged_n = bucket["judged"]
        hits_n = bucket["hits"]
        in_range_hits += hits_n
        sparse = 0 < judged_n < MIN_SAMPLE
        if sparse:
            sparse_weeks += 1
        weekly.append({
            "week": slot["week"],
            "week_start": slot["week_start"],
            "judged": judged_n,
            "hits": hits_n,
            "hit_rate": round(hits_n / judged_n, 4) if judged_n > 0 else None,
            "sparse": sparse,
        })
    return {
        "weekly": weekly,
        "in_range_judged": in_range,
        "in_range_hits": in_range_hits,
        "in_range_hit_rate": round(in_range_hits / in_range, 4) if in_range > 0 else None,
        "out_of_range_judged": out_of_range,
        "undated_judged": undated,
        "sparse_weeks": sparse_weeks,
    }


def _momentum(weekly: list[dict[str, Any]]) -> dict[str, Any]:
    """本周 vs 上周动量;任一周样本<MIN_SAMPLE 诚实 low_sample,判不出方向绝不硬判。"""
    cur = weekly[-1] if weekly else None
    prev = weekly[-2] if len(weekly) >= 2 else None
    cur_j = int(cur.get("judged") or 0) if cur else 0
    prev_j = int(prev.get("judged") or 0) if prev else 0
    if cur_j == 0 and prev_j == 0:
        direction = "no_data"        # 两周都没有裁决,动量无从谈起
    elif cur_j == 0:
        direction = "stalled"        # 本周还没有任何裁决(对答案停摆)
    elif prev_j == 0:
        direction = "new"            # 上周无样本,本周才开张
    else:
        delta = float(cur.get("hit_rate") or 0.0) - float(prev.get("hit_rate") or 0.0)
        if delta > 0.0001:
            direction = "up"
        elif delta < -0.0001:
            direction = "down"
        else:
            direction = "flat"
    delta_pp: float | None = None
    if cur_j > 0 and prev_j > 0:
        delta_pp = round((float(cur.get("hit_rate") or 0.0) - float(prev.get("hit_rate") or 0.0)) * 100, 1)
    return {
        "this_week": cur,
        "last_week": prev,
        "delta_pp": delta_pp,
        "direction": direction,
        "low_sample": (0 < cur_j < MIN_SAMPLE) or (0 < prev_j < MIN_SAMPLE),
    }


def _group_result(
    *,
    action_type: str,
    label: str,
    axis: list[dict[str, str]],
    judged: list[dict[str, Any]],
    pending_count: int,
    judged_time_basis: str,
    scanned: int,
    note: str | None = None,
) -> dict[str, Any]:
    buckets = _bucket_weekly(judged, axis)
    if buckets["in_range_judged"] > 0:
        status = "ok"
    elif judged:
        status = "stale"    # 有历史裁决但都在窗口之外(曲线全空是窗口问题,不是没裁决过)
    elif pending_count > 0:
        status = "pending"
    else:
        status = "empty"
    result = {
        "action_type": action_type,
        "label": label,
        "status": status,
        "judged_time_basis": judged_time_basis,
        "judged_total_all_time": len(judged),
        "pending_count": pending_count,
        "scanned": scanned,
        "momentum": _momentum(buckets["weekly"]),
    }
    result.update(buckets)
    if note:
        result["note"] = note
    return result


def _error_group(action_type: str, label: str, axis: list[dict[str, str]], exc: Exception) -> dict[str, Any]:
    logger.warning("weekly_scorecard group failed action_type=%s: %s", action_type, exc)
    empty = _bucket_weekly([], axis)
    result = {
        "action_type": action_type,
        "label": label,
        "status": "error",
        "reason": str(exc)[:300],
        "judged_time_basis": "",
        "judged_total_all_time": 0,
        "pending_count": 0,
        "scanned": 0,
        "momentum": _momentum(empty["weekly"]),
    }
    result.update(empty)
    return result


def _pending_item(
    *,
    action_type: str,
    label: str,
    ref: str,
    detail: str,
    since: datetime | None,
) -> dict[str, Any]:
    return {
        "action_type": action_type,
        "label": label,
        "ref": ref,
        "detail": detail,
        "pending_since": since.isoformat() if since else None,
        "age_days": _age_days(since),
    }


# ── 各组周度收集器(全只读;每组独立 try/except,单组塌了不拖垮记分卡)──


def _weekly_kol_recommend(conn: Any, axis: list[dict[str, str]], pending_out: list[dict[str, Any]]) -> dict[str, Any]:
    label = "KOL 推荐"
    basis = (
        "正向=各节点真实时间列(shortlisted_at/claimed_at/outreach_sent_at/reply_at/agreement_at/"
        "content_published_at/first_order_at 取最早);负向=rejected_at;"
        "回落 outcome_finalized_at → first_action_at → 反馈 created_at → recommended_at"
        "(侦察:outcome_finalized_at 全空、first_action_at 仅 2 行)"
    )
    try:
        fb_rows = conn.execute(
            "SELECT recommendation_id, feedback_type, created_at FROM vkpi_recommendation_feedback ORDER BY id DESC LIMIT ?",
            (SCAN_LIMIT,),
        ).fetchall()
        fb_types: dict[int, set[str]] = {}
        fb_at: dict[int, datetime | None] = {}
        for raw in fb_rows:
            row = dict(raw)
            rec_id = int(row.get("recommendation_id") or 0)
            if rec_id <= 0:
                continue
            fb_types.setdefault(rec_id, set()).add(str(row.get("feedback_type") or "").strip().lower())
            if rec_id not in fb_at:
                fb_at[rec_id] = _parse_dt(row.get("created_at"))
        rows = conn.execute(
            """
            SELECT id, recommendation_id, kol_pool_id, recommended_at, first_action_at, outcome_finalized_at,
                   was_shortlisted, shortlisted_at, was_rejected, rejected_at,
                   was_claimed, claimed_at, outreach_sent, outreach_sent_at,
                   reply_received, reply_at, agreement_reached, agreement_at,
                   content_published, content_published_at, order_attributed, first_order_at
            FROM vkpi_recommendation_outcomes
            ORDER BY id DESC
            LIMIT ?
            """,
            (SCAN_LIMIT,),
        ).fetchall()
        judged: list[dict[str, Any]] = []
        pending = 0
        for raw in rows:
            row = dict(raw)
            rec_id = int(row.get("recommendation_id") or 0)
            types = fb_types.get(rec_id, set())
            positive_event_times = [
                _parse_dt(row.get(ts_col))
                for flag_col, ts_col in _POSITIVE_EVENT_TS
                if _truthy(row.get(flag_col)) and _parse_dt(row.get(ts_col)) is not None
            ]
            positive = any(_truthy(row.get(flag_col)) for flag_col, _ts in _POSITIVE_EVENT_TS) or bool(
                types & _POSITIVE_FEEDBACK_TYPES
            )
            negative = _truthy(row.get("was_rejected")) or bool(types & _NEGATIVE_FEEDBACK_TYPES)
            fallback = _first_dt(
                row.get("outcome_finalized_at"), row.get("first_action_at"), fb_at.get(rec_id), row.get("recommended_at")
            )
            if positive:
                at = min(positive_event_times) if positive_event_times else fallback
                judged.append({"hit": True, "at": at})
            elif negative:
                judged.append({"hit": False, "at": _first_dt(row.get("rejected_at")) or fallback})
            else:
                pending += 1
                pending_out.append(_pending_item(
                    action_type="kol_recommend", label=label,
                    ref="outcome#" + str(row.get("id")),
                    detail="kol_pool_id=" + str(row.get("kol_pool_id") if row.get("kol_pool_id") is not None else "-")
                           + " 推荐已展示,无任何业务节点/反馈对答案",
                    since=_parse_dt(row.get("recommended_at")),
                ))
        return _group_result(
            action_type="kol_recommend", label=label, axis=axis, judged=judged,
            pending_count=pending, judged_time_basis=basis, scanned=len(rows),
        )
    except Exception as exc:  # noqa: BLE001 — 单组失败诚实降级
        return _error_group("kol_recommend", label, axis, exc)


def _weekly_market_bet(conn: Any, axis: list[dict[str, str]], pending_out: list[dict[str, Any]]) -> dict[str, Any]:
    label = "市场押注"
    basis = "updated_at(复盘写回时刻)→ review_at → created_at;open=pending 按 created_at 计龄;void 剔除"
    try:
        rows = conn.execute(
            "SELECT id, hypothesis, outcome, review_at, created_at, updated_at FROM vkpi_bet_ledger ORDER BY id DESC LIMIT ?",
            (SCAN_LIMIT,),
        ).fetchall()
        judged: list[dict[str, Any]] = []
        pending = 0
        for raw in rows:
            row = dict(raw)
            outcome = str(row.get("outcome") or "").strip().lower()
            at = _first_dt(row.get("updated_at"), row.get("review_at"), row.get("created_at"))
            if outcome == "won":
                judged.append({"hit": True, "at": at})
            elif outcome == "lost":
                judged.append({"hit": False, "at": at})
            elif outcome == "void":
                continue
            else:
                pending += 1
                hypothesis = str(row.get("hypothesis") or "").strip()
                pending_out.append(_pending_item(
                    action_type="market_bet", label=label,
                    ref="bet#" + str(row.get("id")),
                    detail=("押注待复盘:" + hypothesis[:60]) if hypothesis else "押注待复盘(无 hypothesis)",
                    since=_parse_dt(row.get("created_at")),
                ))
        return _group_result(
            action_type="market_bet", label=label, axis=axis, judged=judged,
            pending_count=pending, judged_time_basis=basis, scanned=len(rows),
        )
    except Exception as exc:  # noqa: BLE001
        return _error_group("market_bet", label, axis, exc)


def _weekly_alert_signal(conn: Any, axis: list[dict[str, str]]) -> dict[str, Any]:
    label = "规则告警信号"
    basis = "resolved 用 resolved_at → updated_at → created_at;open 保守计未命中按 created_at 分桶(处理闭环率代理口径)"
    try:
        rows = conn.execute(
            "SELECT id, status, resolved_at, updated_at, created_at FROM vkpi_alerts ORDER BY id DESC LIMIT ?",
            (SCAN_LIMIT,),
        ).fetchall()
        judged: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            resolved = str(row.get("status") or "").strip().lower() == "resolved" or _parse_dt(row.get("resolved_at")) is not None
            if resolved:
                judged.append({"hit": True, "at": _first_dt(row.get("resolved_at"), row.get("updated_at"), row.get("created_at"))})
            else:
                judged.append({"hit": False, "at": _parse_dt(row.get("created_at"))})
        return _group_result(
            action_type="alert_signal", label=label, axis=axis, judged=judged,
            pending_count=0, judged_time_basis=basis, scanned=len(rows),
            note="代理口径=处理闭环率(该表无误报标签),宁压低不虚高;open 告警全部进分母",
        )
    except Exception as exc:  # noqa: BLE001
        return _error_group("alert_signal", label, axis, exc)


def _weekly_brand_signal(conn: Any, axis: list[dict[str, str]], pending_out: list[dict[str, Any]]) -> dict[str, Any]:
    label = "品牌信号"
    basis = "reviewed_at(review 时刻);未 review = pending 按 detected_at 计龄"
    try:
        rows = conn.execute(
            "SELECT id, reviewed_at, action_taken, detected_at, brand_name FROM vkpi_brand_signal ORDER BY id DESC LIMIT ?",
            (SCAN_LIMIT,),
        ).fetchall()
        judged: list[dict[str, Any]] = []
        pending = 0
        for raw in rows:
            row = dict(raw)
            reviewed_at = _parse_dt(row.get("reviewed_at"))
            action = str(row.get("action_taken") or "").strip().lower()
            if reviewed_at is None:
                pending += 1
                pending_out.append(_pending_item(
                    action_type="brand_signal", label=label,
                    ref="signal#" + str(row.get("id")),
                    detail="品牌信号检出未 review:" + str(row.get("brand_name") or "-"),
                    since=_parse_dt(row.get("detected_at")),
                ))
            else:
                judged.append({"hit": bool(action) and action not in _SIGNAL_DISMISSED, "at": reviewed_at})
        return _group_result(
            action_type="brand_signal", label=label, axis=axis, judged=judged,
            pending_count=pending, judged_time_basis=basis, scanned=len(rows),
        )
    except Exception as exc:  # noqa: BLE001
        return _error_group("brand_signal", label, axis, exc)


def _weekly_performance_forecast(axis: list[dict[str, str]]) -> dict[str, Any]:
    """预测在线计算不落库(与台账同款侦察结论)→ 周曲线全零,永远诚实 pending。"""
    return _group_result(
        action_type="performance_forecast", label="KOL 表现预测", axis=axis, judged=[],
        pending_count=0,
        judged_time_basis="(无落库表)预测在线计算于 performance_forecast,无 prediction 行、无 actual 可对答案",
        scanned=0,
        note="有预测能力、无对答案链路;整组诚实 pending,绝不编周曲线",
    )


def _weekly_auto_ops(conn: Any, category: str, axis: list[dict[str, str]]) -> dict[str, Any] | None:
    """auto_ops 动态类别:mode=executed 的 success/failed 按 created_at 分桶;无行回 None。"""
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
        if mode != "executed" or outcome in ("skipped", "skip"):
            excluded += 1
            continue
        if outcome in _EXEC_HIT:
            judged.append({"hit": True, "at": _parse_dt(row.get("created_at"))})
        elif outcome in _EXEC_MISS:
            judged.append({"hit": False, "at": _parse_dt(row.get("created_at"))})
        else:
            excluded += 1
    return _group_result(
        action_type=str(category), label="自动执行 · " + str(category), axis=axis, judged=judged,
        pending_count=0,
        judged_time_basis="vkpi_action_execution_ledger.created_at(执行落账时刻);dry_run/skipped 剔除",
        scanned=len(rows),
        note="执行可靠率口径;本次剔除 " + str(excluded) + " 行演练/闸拒",
    )


def _auto_ops_categories(conn: Any) -> list[str]:
    rows = conn.execute(
        "SELECT category FROM vkpi_action_execution_ledger GROUP BY category ORDER BY category",
    ).fetchall()
    return [str(dict(r).get("category") or "") for r in rows if str(dict(r).get("category") or "")]


# ── 对外契约(路由按此调用;永不 raise,永远回 dict)─────────────────────


def weekly_scorecard(weeks: int = DEFAULT_WEEKS) -> dict[str, Any]:
    """九组预测台账的周度命中率曲线 + 动量 + pending 积压(全只读聚合,零 LLM)。

    顶层键:status / weeks / week_axis / groups / overall / pending_backlog / momentum(=overall.momentum)。
    诚实态:样本荒周 sparse=True;整组无裁决 status=pending/empty;pending 积压
    (776 条推荐待对答案)是头号信号,pending_backlog 顶层显著呈现 + 最老 top5 催办名单。
    """
    win = _clamp_weeks(weeks)
    axis = _week_axis(win)
    try:
        from app.db.connection import get_conn

        conn = get_conn()
        pending_items: list[dict[str, Any]] = []
        groups: list[dict[str, Any]] = [
            _weekly_kol_recommend(conn, axis, pending_items),
            _weekly_market_bet(conn, axis, pending_items),
            _weekly_alert_signal(conn, axis),
            _weekly_brand_signal(conn, axis, pending_items),
            _weekly_performance_forecast(axis),
        ]
        named_keys = {g["action_type"] for g in groups}
        try:
            for category in _auto_ops_categories(conn):
                if category.strip().lower() in named_keys:
                    continue
                try:
                    dynamic = _weekly_auto_ops(conn, category, axis)
                except Exception as exc:  # noqa: BLE001 — 单类别失败不拖垮其余
                    dynamic = _error_group(category, "自动执行 · " + category, axis, exc)
                if dynamic is not None:
                    groups.append(dynamic)
        except Exception as exc:  # noqa: BLE001 — 动态组整体失败不拖垮具名组
            logger.warning("weekly_scorecard auto_ops categories failed: %s", exc)

        # ── overall:各组周桶逐周相加(error 组全零,天然不污染)──
        overall_weekly: list[dict[str, Any]] = []
        for idx, slot in enumerate(axis):
            judged_n = sum(int(g["weekly"][idx]["judged"]) for g in groups)
            hits_n = sum(int(g["weekly"][idx]["hits"]) for g in groups)
            overall_weekly.append({
                "week": slot["week"],
                "week_start": slot["week_start"],
                "judged": judged_n,
                "hits": hits_n,
                "hit_rate": round(hits_n / judged_n, 4) if judged_n > 0 else None,
                "sparse": 0 < judged_n < MIN_SAMPLE,
            })
        in_range_judged = sum(int(w["judged"]) for w in overall_weekly)
        in_range_hits = sum(int(w["hits"]) for w in overall_weekly)
        overall = {
            "action_type": "_overall",
            "label": "全组合计",
            "weekly": overall_weekly,
            "in_range_judged": in_range_judged,
            "in_range_hits": in_range_hits,
            "in_range_hit_rate": round(in_range_hits / in_range_judged, 4) if in_range_judged > 0 else None,
            "sparse_weeks": sum(1 for w in overall_weekly if w["sparse"]),
            "momentum": _momentum(overall_weekly),
        }

        # ── pending 积压:头号信号显著呈现 + 最老 top5 催办名单 ──
        pending_total = sum(int(g.get("pending_count") or 0) for g in groups)
        judged_all_time = sum(int(g.get("judged_total_all_time") or 0) for g in groups)
        denominator = pending_total + judged_all_time
        pending_items.sort(key=lambda item: (item.get("pending_since") is None, item.get("pending_since") or ""))
        rec_pending = next((int(g.get("pending_count") or 0) for g in groups if g["action_type"] == "kol_recommend"), 0)
        pending_backlog = {
            "pending_total": pending_total,
            "judged_total_all_time": judged_all_time,
            "pending_share": round(pending_total / denominator, 4) if denominator > 0 else None,
            "by_group": [
                {"action_type": g["action_type"], "label": g["label"], "pending": int(g.get("pending_count") or 0)}
                for g in groups
                if int(g.get("pending_count") or 0) > 0
            ],
            "headline": (
                str(pending_total) + " 条预测待对答案(其中 KOL 推荐 " + str(rec_pending)
                + " 条)—— 学习闭环头号信号:曲线不缺算法,缺的是有人对答案"
            ),
            "chase_top5": pending_items[:5],
        }

        return {
            "status": "ok",
            "method": METHOD,
            "generated_at": _utcnow_iso(),
            "weeks": win,
            "week_axis": axis,
            "min_sample_per_week": MIN_SAMPLE,
            "ledger_import": "ok" if _PL_IMPORT_ERROR is None else "fallback: " + _PL_IMPORT_ERROR,
            "groups": groups,
            "overall": overall,
            "momentum": overall["momentum"],
            "pending_backlog": pending_backlog,
            "note": (
                "周度记分卡:prediction_ledger 同款裁决口径按 ISO 周分桶(只读聚合真实表,零 LLM);"
                "每周判定<" + str(MIN_SAMPLE) + " 诚实 sparse;pending 不计分母;"
                "台账永不影响任何评分(影响评分=NO)。"
            ),
        }
    except Exception as exc:  # noqa: BLE001 — 契约:永不 raise
        logger.warning("weekly_scorecard failed: %s", exc)
        return {
            "status": "error",
            "reason": str(exc)[:300],
            "weeks": win,
            "week_axis": axis,
            "groups": [],
            "generated_at": _utcnow_iso(),
        }
