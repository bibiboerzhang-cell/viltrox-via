"""V-KPI 本地算力节点看板路由(W4 · 只读聚合)。

- GET /api/admin/vkpi/local-workers/board
  → 一次请求喂面板:节点列表(在线态[last_seen_at 5min 窗]/当前任务/最近错误/租约计数)
    + 最近租约(默认 10 条)+ 按 task_type 分组计数 + 总览。

数据源:vkpi_worker_devices / vkpi_local_task_leases(W1 迁移 213 建表)。
诚实态:表未建 → {status:"empty", reason}(面板展示空态,不编数字);
聚合内部异常不 500,回 {status:"error", reason}(前端安静降级)。
红线:纯读展示,零写库;不触 evidence/kol_pool/viltrox_fit_score/rule_v0;
本模块不签发也不校验任务 token(那是 registry/validation 的事),不持有任何长期 key。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi/local-workers", tags=["vkpi-local-workers"])

# 在线判定窗口:last_seen_at 距今 5 分钟内算在线(与前端小点口径一致)。
ONLINE_WINDOW_MINUTES = 5
# 看板读数上限(纯展示,防大表拖垮单请求)。
MAX_DEVICES = 200
MAX_RECENT_LEASES = 50
ACTIVE_SCAN_LIMIT = 200
ERROR_SCAN_LIMIT = 200


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_dt(value: Any) -> datetime | None:
    """timestamptz 读回 → aware datetime(naive 按 UTC 补齐;字符串防御解析)。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _ts(value: Any) -> str | None:
    dt = _as_dt(value)
    return dt.isoformat() if dt else None


def _loads(raw: Any) -> Any:
    """jsonb 读回:已是 dict/list 直接用;字符串防御解析;失败回 None(不编造)。"""
    if raw in (None, "", b""):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lease_effective_status(status: Any, expires_at: Any, result_validated: Any, now: datetime) -> str:
    """展示口径(看板自算,不改库):
    - submitted 且 result_validated=1 → validated(W1 库层词汇无 validated 状态,校验成功走 int 标志);
    - leased 且已过期 → expired(W1 不一定回写状态)。
    """
    raw = _text(status).lower() or "leased"
    try:
        validated = int(result_validated or 0) == 1
    except (TypeError, ValueError):
        validated = False
    if validated and raw in ("submitted", "validated"):
        return "validated"
    if raw == "leased":
        exp = _as_dt(expires_at)
        if exp is not None and exp < now:
            return "expired"
    return raw


def _table_exists(conn: Any, table_name: str) -> bool:
    try:
        row = conn.execute("SELECT to_regclass(?) AS reg", ("public." + table_name,)).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    return bool(dict(row).get("reg"))


@router.get("/board")
def local_workers_board(
    leases_limit: int = 10,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """本地算力节点看板聚合(全只读,不写库)。"""
    del staff
    from app.db.connection import get_conn

    try:
        return _build_board(get_conn(), leases_limit=leases_limit)
    except Exception as exc:  # noqa: BLE001 — 看板失败不炸接口,诚实回原因
        logger.warning("local_workers_board failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300]}


def _build_board(conn: Any, leases_limit: int = 10) -> dict:
    now = _utc_now()
    limit = max(1, min(int(leases_limit or 10), MAX_RECENT_LEASES))

    if not _table_exists(conn, "vkpi_worker_devices") or not _table_exists(conn, "vkpi_local_task_leases"):
        return {
            "status": "empty",
            "reason": "本地节点表未建(W1 迁移 213_vkpi_worker_devices 未应用),尚无本地节点",
            "generated_at": now.isoformat(),
            "online_window_minutes": ONLINE_WINDOW_MINUTES,
            "devices": [],
            "recent_leases": [],
            "task_type_counts": [],
            "totals": {"devices": 0, "online": 0, "active_leases": 0},
        }

    device_rows = conn.execute(
        """
        SELECT id, device_id, staff_id, device_name, platform, capabilities,
               last_seen_at, status, trust_level, created_at
        FROM vkpi_worker_devices
        ORDER BY last_seen_at DESC NULLS LAST, id DESC
        LIMIT ?
        """,
        (MAX_DEVICES,),
    ).fetchall()

    lease_rows = conn.execute(
        """
        SELECT id, job_id, device_id, task_type, status, issued_at, expires_at,
               submitted_at, result_validated, validation_notes, error_code
        FROM vkpi_local_task_leases
        ORDER BY issued_at DESC NULLS LAST, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    # 当前任务:status='leased' 且未过期,按 device 取最新一条(看板口径,不回写)。
    active_rows = conn.execute(
        """
        SELECT id, job_id, device_id, task_type, issued_at, expires_at
        FROM vkpi_local_task_leases
        WHERE status = ?
        ORDER BY issued_at DESC NULLS LAST, id DESC
        LIMIT ?
        """,
        ("leased", ACTIVE_SCAN_LIMIT),
    ).fetchall()

    error_rows = conn.execute(
        """
        SELECT id, device_id, task_type, error_code, validation_notes,
               issued_at, submitted_at
        FROM vkpi_local_task_leases
        WHERE error_code IS NOT NULL AND error_code <> ''
        ORDER BY COALESCE(submitted_at, issued_at) DESC NULLS LAST, id DESC
        LIMIT ?
        """,
        (ERROR_SCAN_LIMIT,),
    ).fetchall()

    type_count_rows = conn.execute(
        """
        SELECT task_type, status, result_validated, COUNT(*) AS n
        FROM vkpi_local_task_leases
        GROUP BY task_type, status, result_validated
        """
    ).fetchall()

    device_count_rows = conn.execute(
        """
        SELECT device_id, status, result_validated, COUNT(*) AS n
        FROM vkpi_local_task_leases
        GROUP BY device_id, status, result_validated
        """
    ).fetchall()

    # ── 派生:当前任务(每设备最新未过期 leased)/ 最近错误(每设备最新一条)──
    current_by_device: dict[str, dict[str, Any]] = {}
    active_lease_count = 0
    for row in active_rows:
        data = dict(row)
        exp = _as_dt(data.get("expires_at"))
        if exp is not None and exp < now:
            continue  # 已过期不算在跑
        active_lease_count += 1
        dev = _text(data.get("device_id"))
        if dev and dev not in current_by_device:  # 行序=最新优先
            current_by_device[dev] = {
                "lease_id": data.get("id"),
                "job_id": data.get("job_id"),
                "task_type": _text(data.get("task_type")),
                "issued_at": _ts(data.get("issued_at")),
                "expires_at": _ts(data.get("expires_at")),
            }

    last_error_by_device: dict[str, dict[str, Any]] = {}
    for row in error_rows:
        data = dict(row)
        dev = _text(data.get("device_id"))
        if dev and dev not in last_error_by_device:  # 行序=最新优先
            last_error_by_device[dev] = {
                "lease_id": data.get("id"),
                "task_type": _text(data.get("task_type")),
                "error_code": _text(data.get("error_code")),
                "notes": _text(data.get("validation_notes"))[:200],
                "at": _ts(data.get("submitted_at") or data.get("issued_at")),
            }

    stats_by_device: dict[str, dict[str, int]] = {}
    for row in device_count_rows:
        data = dict(row)
        dev = _text(data.get("device_id"))
        status_key = _lease_effective_status(data.get("status"), None, data.get("result_validated"), now)
        if not dev:
            continue
        bucket = stats_by_device.setdefault(dev, {})
        bucket[status_key] = bucket.get(status_key, 0) + int(data.get("n") or 0)

    window = timedelta(minutes=ONLINE_WINDOW_MINUTES)
    devices: list[dict[str, Any]] = []
    online_count = 0
    for row in device_rows:
        data = dict(row)
        dev = _text(data.get("device_id"))
        seen = _as_dt(data.get("last_seen_at"))
        online = bool(seen is not None and (now - seen) <= window)
        if online:
            online_count += 1
        devices.append(
            {
                "id": data.get("id"),
                "device_id": dev,
                "device_name": _text(data.get("device_name")) or dev or "(未命名节点)",
                "platform": _text(data.get("platform")),
                "staff_id": data.get("staff_id"),
                "status": _text(data.get("status")) or "offline",
                "trust_level": int(data.get("trust_level") or 0),
                "capabilities": _loads(data.get("capabilities")),
                "last_seen_at": _ts(data.get("last_seen_at")),
                "created_at": _ts(data.get("created_at")),
                "online": online,
                "current_task": current_by_device.get(dev),
                "last_error": last_error_by_device.get(dev),
                "lease_stats": stats_by_device.get(dev, {}),
            }
        )

    recent_leases: list[dict[str, Any]] = []
    device_names = {d["device_id"]: d["device_name"] for d in devices if d.get("device_id")}
    for row in lease_rows:
        data = dict(row)
        dev = _text(data.get("device_id"))
        recent_leases.append(
            {
                "id": data.get("id"),
                "job_id": data.get("job_id"),
                "device_id": dev,
                "device_name": device_names.get(dev, dev),
                "task_type": _text(data.get("task_type")),
                "status": _text(data.get("status")).lower() or "leased",
                "effective_status": _lease_effective_status(
                    data.get("status"), data.get("expires_at"), data.get("result_validated"), now
                ),
                "issued_at": _ts(data.get("issued_at")),
                "expires_at": _ts(data.get("expires_at")),
                "submitted_at": _ts(data.get("submitted_at")),
                "result_validated": int(data.get("result_validated") or 0),
                "error_code": _text(data.get("error_code")) or None,
            }
        )

    type_buckets: dict[str, dict[str, Any]] = {}
    for row in type_count_rows:
        data = dict(row)
        task_type = _text(data.get("task_type")) or "(未标类型)"
        status_key = _lease_effective_status(data.get("status"), None, data.get("result_validated"), now)
        n = int(data.get("n") or 0)
        bucket = type_buckets.setdefault(task_type, {"task_type": task_type, "total": 0, "by_status": {}})
        bucket["total"] += n
        bucket["by_status"][status_key] = bucket["by_status"].get(status_key, 0) + n
    task_type_counts = sorted(type_buckets.values(), key=lambda item: -int(item["total"]))

    return {
        "status": "ready",
        "generated_at": now.isoformat(),
        "online_window_minutes": ONLINE_WINDOW_MINUTES,
        "devices": devices,
        "recent_leases": recent_leases,
        "task_type_counts": task_type_counts,
        "totals": {
            "devices": len(devices),
            "online": online_count,
            "active_leases": active_lease_count,
        },
    }
