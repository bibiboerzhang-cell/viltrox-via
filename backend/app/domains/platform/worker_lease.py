"""I2 · Worker Lease(分布式任务租约:云发本地领)。

中心侧 acquire_lease 登记一条 leased 行(发任务),本地 worker 凭 lease_token
renew_lease 续租 / complete_lease 完成 / release_lease 释放;expire_stale 清扫把
到期仍 leased 的行置 expired 供重派。lease_status 给各状态计数(只读)。

lease_token = sha256(worker_id|task_kind|id) 前 32 位,可复现非随机(避免不可复现)。
全部 best-effort:缺表/异常 → 诚实降级返回,不抛。零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_TABLE = "vkpi_worker_leases"
_STATUSES = ("leased", "completed", "expired", "released")


def _lease_token(worker_id: str, task_kind: str, lease_id: int) -> str:
    """据 worker_id+task_kind+id 拼可复现 hash 作为租约凭据(非随机)。"""
    raw = f"{worker_id}|{task_kind}|{int(lease_id)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def acquire_lease(
    worker_id: str,
    task_kind: str,
    ttl_seconds: int = 300,
    payload_ref: str = "",
) -> dict[str, Any]:
    """登记一条 leased 租约,回填可复现 lease_token,返回 {available, lease_id, lease_token, ...}。"""
    if not table_exists(_TABLE):
        return {"available": False, "reason": "table_missing"}
    worker_id = str(worker_id or "").strip()
    task_kind = str(task_kind or "").strip()
    if not worker_id or not task_kind:
        return {"available": False, "reason": "worker_id_and_task_kind_required"}
    try:
        ttl = max(1, int(ttl_seconds))
    except (TypeError, ValueError):
        ttl = 300
    conn = get_conn()
    try:
        row = conn.execute(
            f"""
            INSERT INTO {_TABLE}
              (worker_id, task_kind, status, payload_ref, leased_at, expires_at, updated_at)
            VALUES (?, ?, 'leased', ?, NOW(), NOW() + (? || ' seconds')::interval, NOW())
            RETURNING id
            """,
            (worker_id, task_kind, str(payload_ref or "") or None, str(ttl)),
        ).fetchone()
        if row is None:
            conn.rollback()
            return {"available": False, "reason": "insert_failed"}
        lease_id = int(row["id"])
        token = _lease_token(worker_id, task_kind, lease_id)
        conn.execute(
            f"UPDATE {_TABLE} SET lease_token = ?, updated_at = NOW() WHERE id = ?",
            (token, lease_id),
        )
        conn.commit()
        return {
            "available": True,
            "lease_id": lease_id,
            "lease_token": token,
            "worker_id": worker_id,
            "task_kind": task_kind,
            "ttl_seconds": ttl,
            "status": "leased",
        }
    except Exception as exc:  # best-effort:失败诚实降级,不抛
        logger.warning("acquire_lease failed (%s/%s): %s", worker_id, task_kind, exc)
        try:
            conn.rollback()
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
        return {"available": False, "reason": "error"}


def renew_lease(lease_token: str, ttl_seconds: int = 300) -> dict[str, Any]:
    """续租:把 leased 行 expires_at 顺延 ttl_seconds。仅对仍 leased 的行生效。"""
    if not table_exists(_TABLE):
        return {"renewed": False, "reason": "table_missing"}
    token = str(lease_token or "").strip()
    if not token:
        return {"renewed": False, "reason": "lease_token_required"}
    try:
        ttl = max(1, int(ttl_seconds))
    except (TypeError, ValueError):
        ttl = 300
    conn = get_conn()
    try:
        cur = conn.execute(
            f"""
            UPDATE {_TABLE}
               SET expires_at = NOW() + (? || ' seconds')::interval, updated_at = NOW()
             WHERE lease_token = ? AND status = 'leased'
            """,
            (str(ttl), token),
        )
        conn.commit()
        ok = int(getattr(cur, "rowcount", 0) or 0) > 0
        return {"renewed": ok, "ttl_seconds": ttl} if ok else {"renewed": False, "reason": "not_leased"}
    except Exception as exc:
        logger.warning("renew_lease failed (%s): %s", token, exc)
        try:
            conn.rollback()
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
        return {"renewed": False, "reason": "error"}


def complete_lease(lease_token: str, result_ref: str = "") -> dict[str, Any]:
    """完成:把 leased 行置 completed 并回填 result_ref。仅对仍 leased 的行生效。"""
    if not table_exists(_TABLE):
        return {"completed": False, "reason": "table_missing"}
    token = str(lease_token or "").strip()
    if not token:
        return {"completed": False, "reason": "lease_token_required"}
    conn = get_conn()
    try:
        cur = conn.execute(
            f"""
            UPDATE {_TABLE}
               SET status = 'completed', result_ref = ?, updated_at = NOW()
             WHERE lease_token = ? AND status = 'leased'
            """,
            (str(result_ref or "") or None, token),
        )
        conn.commit()
        ok = int(getattr(cur, "rowcount", 0) or 0) > 0
        return {"completed": ok} if ok else {"completed": False, "reason": "not_leased"}
    except Exception as exc:
        logger.warning("complete_lease failed (%s): %s", token, exc)
        try:
            conn.rollback()
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
        return {"completed": False, "reason": "error"}


def release_lease(lease_token: str) -> dict[str, Any]:
    """释放:把 leased 行置 released(主动放弃,供重派)。仅对仍 leased 的行生效。"""
    if not table_exists(_TABLE):
        return {"released": False, "reason": "table_missing"}
    token = str(lease_token or "").strip()
    if not token:
        return {"released": False, "reason": "lease_token_required"}
    conn = get_conn()
    try:
        cur = conn.execute(
            f"""
            UPDATE {_TABLE}
               SET status = 'released', updated_at = NOW()
             WHERE lease_token = ? AND status = 'leased'
            """,
            (token,),
        )
        conn.commit()
        ok = int(getattr(cur, "rowcount", 0) or 0) > 0
        return {"released": ok} if ok else {"released": False, "reason": "not_leased"}
    except Exception as exc:
        logger.warning("release_lease failed (%s): %s", token, exc)
        try:
            conn.rollback()
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
        return {"released": False, "reason": "error"}


def expire_stale() -> dict[str, Any]:
    """过期清扫:把 expires_at 早于当前时刻且仍 leased 的行置 expired,返回处理数量。"""
    if not table_exists(_TABLE):
        return {"expired": 0, "reason": "table_missing"}
    conn = get_conn()
    try:
        cur = conn.execute(
            f"""
            UPDATE {_TABLE}
               SET status = 'expired', updated_at = NOW()
             WHERE status = 'leased' AND expires_at IS NOT NULL AND expires_at < NOW()
            """,
        )
        conn.commit()
        n = int(getattr(cur, "rowcount", 0) or 0)
        return {"expired": n}
    except Exception as exc:
        logger.warning("expire_stale failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
        return {"expired": 0, "reason": "error"}


def lease_status() -> dict[str, Any]:
    """各状态计数(只读聚合):{available, counts:{leased,completed,expired,released}, total}。"""
    if not table_exists(_TABLE):
        return {"available": False, "counts": {s: 0 for s in _STATUSES}, "total": 0}
    counts = {s: 0 for s in _STATUSES}
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT status, COUNT(*) AS n FROM {_TABLE} GROUP BY status"
        ).fetchall()
        total = 0
        for r in rows or []:
            st = str(r["status"] or "")
            n = int(r["n"] or 0)
            total += n
            if st in counts:
                counts[st] = n
        return {"available": True, "counts": counts, "total": total}
    except Exception as exc:
        logger.warning("lease_status failed: %s", exc)
        return {"available": False, "counts": counts, "total": 0}


__all__ = [
    "acquire_lease",
    "renew_lease",
    "complete_lease",
    "release_lease",
    "expire_stale",
    "lease_status",
]
