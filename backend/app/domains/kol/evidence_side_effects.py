"""新证据即登记(波 D·D2):视频证据新行落库后,把收藏 KOL 的新视频幂等登记进指标追踪。

波 C·C5 做了「收藏即登记」(favorite_side_effects):收藏那一刻把该 KOL 当时已有的视频全部登记。
缺的是另一半:收藏之后再抓到的新视频(深爬 / 历史补抓 / 项目手录)不会自动续登记,指标追踪
就此断更。本模块补两条路:

* enroll_tracking_after_new_evidence(kol_pool_id, evidence_id=..., conn=...)
  挂在 video_evidence.ensure_video_evidence_from_url 的 status=created 之后(主写已 commit);
  非收藏 KOL 直接 not_favorited 零写;受 metric_tracking 月闸(默认 $30/月,fail-closed);
  任何失败只 logger.warning,绝不冒泡进证据写口。
* run_tracking_auto_enroll(limit=None)
  零成本日任务兜底:其它写口(projects/workflow_evidence、observation_windows 的 ON CONFLICT
  写法)落的新证据,以及钩子失败漏掉的行,由它每天按收藏集全量幂等补登记
  (ON CONFLICT (evidence_id) DO NOTHING;已 active / paused 一律不动)。
  供主会话注册为 ``vkpi_tracking_auto_enroll``。

两条路都零 provider 调用、零入队:登记只写订阅行,真正花钱的刷新入队在 scheduler 那边还有同一道闸。
红线:绝不触 viltrox_fit_score;SQL 全 ? 占位。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol import video_tracking_budget, video_tracking_enroll


logger = get_logger("viltrox.domains.kol.evidence_side_effects")

TASK_KEY = "vkpi_tracking_auto_enroll"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_favorited(conn: Any, kol_pool_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 AS hit FROM vkpi_kol_pool_favorites WHERE kol_pool_id = ? LIMIT 1",
        (int(kol_pool_id),),
    ).fetchone()
    return row is not None


def _gate(conn: Any) -> tuple[bool, str]:
    gate = video_tracking_budget.budget_gate(conn, sync_spend=False)
    if gate.get("allowed"):
        return True, "within_cap"
    return False, str(gate.get("reason") or "budget_blocked")


def _summary(summary: dict[str, Any], *, reason: str | None) -> dict[str, Any]:
    return {
        "tracking_enrolled": _int(summary.get("inserted")),
        "tracking_candidates": _int(summary.get("candidates")),
        "tracking_already_active": _int(summary.get("already_active")),
        "tracking_skipped": dict(summary.get("skipped") or {}),
        "tracking_enroll_reason": reason,
    }


def _empty(reason: str) -> dict[str, Any]:
    return _summary({}, reason=reason)


def enroll_tracking_after_new_evidence(
    kol_pool_id: int,
    *,
    evidence_id: int | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """证据新行落库后调用(主写已 commit)。永不抛异常;返回与收藏即登记同形的统计。"""

    pool_id = _int(kol_pool_id)
    if pool_id <= 0:
        return _empty("kol_pool_id_required")
    db = conn or get_conn()
    try:
        if not _is_favorited(db, pool_id):
            return _empty("not_favorited")
        allowed, reason = _gate(db)
        if not allowed:
            logger.warning(
                "evidence.tracking_enroll_skipped | kol_pool_id=%s evidence_id=%s reason=%s",
                pool_id, evidence_id, reason,
            )
            return _empty(reason)
        summary = video_tracking_enroll.enroll_my_kol_evidence(db, apply=True, kol_pool_ids=[pool_id])
        db.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort 副作用,证据主写已落库,只记日志
        logger.warning(
            "evidence.tracking_enroll_failed | kol_pool_id=%s evidence_id=%s error=%s",
            pool_id, evidence_id, f"{type(exc).__name__}: {exc}",
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("evidence.tracking_enroll_rollback_failed", exc_info=True)
        return _empty("enroll_failed")
    inserted = _int(summary.get("inserted"))
    if _int(summary.get("candidates")) == 0:
        reason = "no_video_evidence"
    elif inserted == 0 and _int(summary.get("already_active")) > 0:
        reason = "already_enrolled"
    elif inserted == 0:
        reason = "nothing_eligible"
    else:
        reason = None
    return _summary(summary, reason=reason)


def run_tracking_auto_enroll(limit: int | None = None, *, conn: Any | None = None) -> dict[str, Any]:
    """日任务兜底:收藏集全量幂等续登记(同步;scheduler 用 asyncio.to_thread 包)。永不抛异常。"""

    db = conn or get_conn()
    result: dict[str, Any] = {
        "status": "ok",
        "task": TASK_KEY,
        "provider_calls_performed": False,
        "candidates": 0,
        "to_register": 0,
        "inserted": 0,
        "already_active": 0,
        "skipped": {},
    }
    try:
        allowed, reason = _gate(db)
        if not allowed:
            logger.warning("tracking_auto_enroll.skipped | reason=%s", reason)
            result.update({"status": "blocked", "reason": reason})
            return result
        summary = video_tracking_enroll.enroll_my_kol_evidence(db, apply=True, limit=limit)
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 调度层只看 status,不让异常逃出日任务
        logger.warning("tracking_auto_enroll.failed | error=%s", f"{type(exc).__name__}: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("tracking_auto_enroll.rollback_failed", exc_info=True)
        result.update({"status": "failed", "error_code": type(exc).__name__.lower()[:80]})
        return result
    result.update(
        {
            "candidates": _int(summary.get("candidates")),
            "to_register": _int(summary.get("to_register")),
            "inserted": _int(summary.get("inserted")),
            "already_active": _int(summary.get("already_active")),
            "skipped": dict(summary.get("skipped") or {}),
            "tiers": dict(summary.get("tiers") or {}),
        }
    )
    if result["candidates"] == 0:
        result["status"] = "empty"
    logger.info(
        "tracking_auto_enroll.done | status=%s candidates=%s inserted=%s already_active=%s",
        result["status"], result["candidates"], result["inserted"], result["already_active"],
    )
    return result


__all__ = ["TASK_KEY", "enroll_tracking_after_new_evidence", "run_tracking_auto_enroll"]
