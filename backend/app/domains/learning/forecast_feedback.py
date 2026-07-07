"""预测流水对答案(forecast_feedback)—— B3 学习闭环通电 · 刀1 的「回查实际」半边。

链路:performance_forecast 每次 ready 预测落一行 vkpi_forecast_log(迁移215,
outcome='pending')→ 本模块 refresh_forecast_outcomes() 对满 30 天仍 pending 的行
回查该 KOL 预测时点之后的有效视频证据实际播放中位数,填 actual_views/actual_at
并判 outcome → forecast_log_summary() 出带内率(预测区间到底准不准的唯一真数)。

判定口径(决定性,零 LLM):
  actual = 该 KOL 在 [created_at, now] 窗口内新发(posted_at 落窗)、is_active、
           view_count>0 的 evidence 播放中位数(statistics.median,可手工复算);
  行内已有 actual_views(人工手录)则直接用,不重算不覆写;
  actual < p10 → below;actual > p90 → above;否则 hit_in_band;
  窗口内无新发有播放证据 → 保持 pending(诚实等料,绝不硬判);
  p10/p90 缺失(理论上 ready 流水不该有)→ 保持 pending 并计数 missing_band。

调用方式:先提供手动函数 + 路由(vkpi_forecast_feedback);签名对每日 job 友好,
后续 scheduler 直接调 refresh_forecast_outcomes() 即可,无需改本模块。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(不用 LIKE);BOOLEAN 读回
可能是 int 1/0,判断走 _truthy;函数内懒 import get_conn。
红线:唯一写入是 vkpi_forecast_log 的 actual_views/actual_at/outcome 三列(明确目标列);
零触 fit 评分存储列、不碰 rule_v0;零 LLM 调用;表未建(迁移215未 apply)诚实降级不炸。
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 满多少天才对答案(给 KOL 留发片窗口);路由可传参覆盖,冒烟可传 0。
DEFAULT_MIN_AGE_DAYS = 30
# 单次 refresh 最多处理的 pending 行数(防超长请求;每日 job 多跑几轮自然清空)。
DEFAULT_REFRESH_LIMIT = 200

OUTCOME_PENDING = "pending"
OUTCOME_HIT = "hit_in_band"
OUTCOME_BELOW = "below"
OUTCOME_ABOVE = "above"


# ── 小工具(与 performance_forecast 同款容错约定)──────────────────────


def _text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _truthy(value: Any) -> bool:
    """compat 层 BOOLEAN 读回可能是 int 1/0 / 't' / 'true',统一容错。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "t", "true", "yes", "y")


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "T", 1).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ── 回查实际:窗口内新发视频的播放中位数 ───────────────────────────────


def _actual_views_in_window(
    conn: Any, kol_pool_id: int, window_from: datetime, window_to: datetime
) -> tuple[int | None, int]:
    """该 KOL 在 [window_from, window_to] 内新发有播放证据的播放中位数与样本数。

    posted_at 判窗与 is_active 判真都拉回 Python 做(compat 层时间/布尔双态容错);
    无样本诚实 (None, 0),绝不外推。
    """
    rows = conn.execute(
        """
        SELECT view_count, posted_at, is_active
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id = ? AND posted_at IS NOT NULL
        """,
        (int(kol_pool_id),),
    ).fetchall()
    views: list[float] = []
    for raw in rows:
        row = dict(raw)
        if not _truthy(row.get("is_active")):
            continue
        count = _int_or_none(row.get("view_count"))
        if count is None or count <= 0:
            continue
        posted = _parse_ts(row.get("posted_at"))
        if posted is None or posted < window_from or posted > window_to:
            continue
        views.append(float(count))
    if not views:
        return None, 0
    return int(round(statistics.median(views))), len(views)


def _judge(actual: int, p10: int | None, p90: int | None) -> str | None:
    """带内判定;p10/p90 缺失给 None(保持 pending,不硬判)。"""
    if p10 is None or p90 is None:
        return None
    if actual < int(p10):
        return OUTCOME_BELOW
    if actual > int(p90):
        return OUTCOME_ABOVE
    return OUTCOME_HIT


# ── 主入口①:refresh_forecast_outcomes(手动函数,每日 job 可直调)────


def refresh_forecast_outcomes(
    min_age_days: int = DEFAULT_MIN_AGE_DAYS,
    limit: int = DEFAULT_REFRESH_LIMIT,
    *,
    conn: Any = None,
) -> dict[str, Any]:
    """对满窗仍 pending 的预测流水行回查实际、判 outcome;永不抛异常。

    返回 {status, scanned, updated, outcomes, kept_pending_no_evidence,
    kept_pending_missing_band, cutoff, min_age_days};表未建诚实 empty。
    """
    min_age_days = max(0, min(int(min_age_days), 365))
    limit = max(1, min(int(limit), 1000))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=min_age_days)
    result: dict[str, Any] = {
        "status": "ok",
        "scanned": 0,
        "updated": 0,
        "outcomes": {OUTCOME_HIT: 0, OUTCOME_BELOW: 0, OUTCOME_ABOVE: 0},
        "kept_pending_no_evidence": 0,
        "kept_pending_missing_band": 0,
        "min_age_days": min_age_days,
        "cutoff": cutoff.isoformat(),
        "generated_at": now.isoformat(),
    }
    try:
        from app.db.connection import get_conn

        db = conn or get_conn()
        try:
            rows = db.execute(
                """
                SELECT id, kol_pool_id, p10, p50, p90, created_at, actual_views
                FROM vkpi_forecast_log
                WHERE outcome = 'pending' AND created_at < ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 — 表未建(迁移215未 apply)诚实空态
            logger.warning("vkpi_forecast_log unavailable for refresh: %s", exc)
            result["status"] = "empty"
            result["reason"] = "预测流水表未建(迁移 215 未 apply),无从对答案。"
            return result

        updates: list[tuple[int, datetime, str, int]] = []  # (actual, at, outcome, id)
        for raw in rows:
            row = dict(raw)
            result["scanned"] += 1
            row_id = _int_or_none(row.get("id"))
            kol_id = _int_or_none(row.get("kol_pool_id"))
            created = _parse_ts(row.get("created_at"))
            if row_id is None or kol_id is None or created is None:
                result["kept_pending_missing_band"] += 1
                continue
            # 人工手录的 actual_views 优先,只补判 outcome,不重算不覆写。
            actual = _int_or_none(row.get("actual_views"))
            if actual is None or actual <= 0:
                actual, _sample = _actual_views_in_window(db, kol_id, created, now)
            if actual is None or actual <= 0:
                result["kept_pending_no_evidence"] += 1
                continue
            outcome = _judge(actual, _int_or_none(row.get("p10")), _int_or_none(row.get("p90")))
            if outcome is None:
                result["kept_pending_missing_band"] += 1
                continue
            updates.append((int(actual), now, outcome, row_id))

        for actual, at, outcome, row_id in updates:
            db.execute(
                """
                UPDATE vkpi_forecast_log
                SET actual_views = ?, actual_at = ?, outcome = ?
                WHERE id = ? AND outcome = 'pending'
                """,
                (actual, at, outcome, row_id),
            )
            result["updated"] += 1
            result["outcomes"][outcome] = result["outcomes"].get(outcome, 0) + 1
        if updates:
            db.commit()
        return result
    except Exception as exc:  # noqa: BLE001 — 学习闭环增益件,永不炸调用方
        logger.warning("refresh_forecast_outcomes failed: %s", exc)
        try:
            if conn is None:
                from app.db.connection import get_conn

                get_conn().rollback()
        except Exception:  # noqa: BLE001 — 回滚失败不掩盖原始错误
            pass
        result["status"] = "error"
        result["reason"] = str(exc)[:300]
        return result


# ── 主入口②:forecast_log_summary(带内率统计读数)─────────────────────


def _rate(hit: int, resolved: int) -> float | None:
    """带内率;无已判样本诚实 None,不用 0 假装。"""
    if resolved <= 0:
        return None
    return round(hit / resolved, 4)


def forecast_log_summary(*, recent_limit: int = 10, conn: Any = None) -> dict[str, Any]:
    """预测流水总览:按 outcome 分布 + 已判行带内率 + 按 context/method 分桶 + 最近样本。

    永不抛异常;表未建(迁移215未 apply)诚实 empty。
    """
    recent_limit = max(0, min(int(recent_limit), 50))
    base: dict[str, Any] = {
        "status": "empty",
        "total": 0,
        "outcomes": {},
        "resolved": 0,
        "in_band_rate": None,
        "by_context": [],
        "by_method": [],
        "recent": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "带内率 = hit_in_band 除以已判行(hit+below+above);pending 是还没到窗或"
            "窗口内无新发证据的行,不参与带内率。数据全部来自 vkpi_forecast_log 对答案结果。"
        ),
    }
    try:
        from app.db.connection import get_conn

        db = conn or get_conn()
        try:
            rows = db.execute(
                """
                SELECT outcome, context, method, COUNT(*) AS n
                FROM vkpi_forecast_log
                GROUP BY outcome, context, method
                """,
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 — 表未建诚实空态
            logger.warning("vkpi_forecast_log unavailable for summary: %s", exc)
            base["reason"] = "预测流水表未建(迁移 215 未 apply),暂无流水可统计。"
            return base

        outcome_counts: dict[str, int] = {}
        ctx_stats: dict[str, dict[str, int]] = {}
        method_stats: dict[str, dict[str, int]] = {}
        for raw in rows:
            row = dict(raw)
            outcome = _text(row.get("outcome"), 20) or OUTCOME_PENDING
            n = _int_or_none(row.get("n")) or 0
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + n
            for bucket, key in (
                (ctx_stats, _text(row.get("context"), 20) or "unknown"),
                (method_stats, _text(row.get("method"), 60) or "unknown"),
            ):
                slot = bucket.setdefault(key, {"total": 0, "hit": 0, "resolved": 0})
                slot["total"] += n
                if outcome in (OUTCOME_HIT, OUTCOME_BELOW, OUTCOME_ABOVE):
                    slot["resolved"] += n
                if outcome == OUTCOME_HIT:
                    slot["hit"] += n

        total = sum(outcome_counts.values())
        hit = outcome_counts.get(OUTCOME_HIT, 0)
        resolved = hit + outcome_counts.get(OUTCOME_BELOW, 0) + outcome_counts.get(OUTCOME_ABOVE, 0)
        base.update(
            status="ready" if total else "empty",
            total=total,
            outcomes=outcome_counts,
            resolved=resolved,
            in_band_rate=_rate(hit, resolved),
            by_context=[
                {"context": k, "total": v["total"], "resolved": v["resolved"],
                 "in_band_rate": _rate(v["hit"], v["resolved"])}
                for k, v in sorted(ctx_stats.items())
            ],
            by_method=[
                {"method": k, "total": v["total"], "resolved": v["resolved"],
                 "in_band_rate": _rate(v["hit"], v["resolved"])}
                for k, v in sorted(method_stats.items())
            ],
        )
        if not total:
            base["reason"] = "预测流水表还没有任何行 — 跑一次 KOL 预测(档案/发射台)即开始积累。"
            return base

        if recent_limit:
            recent_rows = db.execute(
                """
                SELECT id, kol_pool_id, sku, p10, p50, p90, confidence, context,
                       created_at, actual_views, actual_at, outcome
                FROM vkpi_forecast_log
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (recent_limit,),
            ).fetchall()
            base["recent"] = [
                {
                    "id": _int_or_none(r.get("id")),
                    "kol_pool_id": _int_or_none(r.get("kol_pool_id")),
                    "sku": _text(r.get("sku"), 120),
                    "p10": _int_or_none(r.get("p10")),
                    "p50": _int_or_none(r.get("p50")),
                    "p90": _int_or_none(r.get("p90")),
                    "confidence": _text(r.get("confidence"), 20),
                    "context": _text(r.get("context"), 20),
                    "created_at": _iso(r.get("created_at")),
                    "actual_views": _int_or_none(r.get("actual_views")),
                    "actual_at": _iso(r.get("actual_at")),
                    "outcome": _text(r.get("outcome"), 20),
                }
                for r in (dict(x) for x in recent_rows)
            ]
        return base
    except Exception as exc:  # noqa: BLE001 — 读数增益件,永不炸调用方
        logger.warning("forecast_log_summary failed: %s", exc)
        base["status"] = "error"
        base["reason"] = str(exc)[:300]
        return base
