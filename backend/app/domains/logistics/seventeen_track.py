"""17track 物流同步(2026-06-12 裁令"17track 早点做")。

免费注册制:token 走 env VKPI_17TRACK_TOKEN,未配置时诚实降级(enqueue 即 blocked,
UI 仍显阶段推断)。队列铁律:同步经 apify_jobs(泳道「物流同步」),不做同步内联。
写点:vkpi_project_kol_assignments.metadata_json.shipping(status/latest_event/events/
provider/synced_at)——前端 trackingStatus 读的就是 shipping.status,零前端改造直达 UI。
红线:零触 fit_score;只动 shipping 元数据。
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.core.permissions import check_tab_permission
from app.core.security import user_status_allows_auth
from app.db.connection import get_conn
from app.domains.access import scope
from app.domains.projects.workflow_evidence import record_delivered_signal
from app.domains.staff import is_manager_staff

logger = get_logger("viltrox.domains.logistics.seventeen_track")

JOB_TYPE = "logistics_track_sync"
API_BASE = "https://api.17track.net/track/v2.2"
FENCE_KEY = "logistics_access_fence"
FENCE_VERSION = 1


class LogisticsAccessError(RuntimeError):
    """Stable, provider-free authorization failure for enqueue/worker paths."""

    def __init__(self, code: str, status_code: int = 403):
        super().__init__(code)
        self.code = str(code)
        self.status_code = int(status_code)

# 17track 主状态 → 中文(写入 shipping.status,UI 直显)
STATUS_CN = {
    "InfoReceived": "已揽收",
    "InTransit": "在途",
    "OutForDelivery": "派送中",
    "AvailableForPickup": "待取件",
    "Delivered": "已签收",
    "DeliveryFailure": "投递失败",
    "Exception": "异常",
    "Expired": "查询过期",
    "NotFound": "暂无轨迹",
}


def _tokens() -> list[str]:
    """token 池(逗号分隔,2026-06-12 裁令"试 2 个免费的"):env 优先,
    兜底读仓库根 .env(gitignored;admin master 免重启)。配额耗尽自动轮换下一个。"""
    raw = os.getenv("VKPI_17TRACK_TOKEN", "").strip()
    if not raw:
        try:
            from pathlib import Path

            env_path = Path(__file__).resolve().parents[4] / ".env"
            for line in env_path.read_text().splitlines():
                if line.strip().startswith("VKPI_17TRACK_TOKEN="):
                    raw = line.split("=", 1)[1].strip()
                    break
        except OSError:
            pass
    return [token.strip() for token in raw.split(",") if token.strip()]


def _token() -> str:
    tokens = _tokens()
    return tokens[0] if tokens else ""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_project_id(value: Any) -> int | None:
    if value in (None, ""):
        return None
    project_id = _int(value)
    if project_id <= 0:
        raise LogisticsAccessError("logistics_project_id_invalid", 400)
    return project_id


def _system_actor(staff: dict[str, Any] | None) -> bool:
    actor = staff or {}
    return (
        scope.actor_staff_id(actor) <= 0
        and _int(actor.get("is_owner")) == 1
        and str(actor.get("role") or "").strip().lower() == "admin"
    )


def _authorize_enqueue_scope(
    project_id: Any,
    staff: dict[str, Any] | None,
) -> tuple[int | None, int, bool]:
    normalized = _normalize_project_id(project_id)
    actor = staff or {}
    if normalized is None:
        if not is_manager_staff(actor):
            raise LogisticsAccessError("logistics_global_manager_required")
    else:
        try:
            scope.assert_project_access(normalized, actor, write=True)
        except scope.ScopeDenied as exc:
            raise LogisticsAccessError("logistics_project_write_forbidden") from exc
    if not check_tab_permission(actor, "vkpi", "write"):
        raise LogisticsAccessError("logistics_write_permission_required")
    staff_id = scope.actor_staff_id(actor)
    is_system = _system_actor(actor)
    if staff_id <= 0 and not is_system:
        raise LogisticsAccessError("logistics_actor_identity_required")
    return normalized, staff_id, is_system


_QUOTA_ERROR_CODES = {-18019908, -18019909}  # 配额不足/超限(17track 文档码,命中即换 token)


def _api(path: str, payload: list[dict[str, Any]]) -> dict[str, Any]:
    """token 池轮换:配额类错误自动切下一个 token 重试,全池耗尽才抛。"""
    tokens = _tokens()
    if not tokens:
        raise RuntimeError("17track token missing")
    last: dict[str, Any] = {}
    for index, token in enumerate(tokens):
        request = urllib.request.Request(
            f"{API_BASE}/{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "17token": token},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            last = json.loads(response.read().decode("utf-8"))
        code = int(last.get("code") or 0)
        if code == 0:
            return last
        if code in _QUOTA_ERROR_CODES and index < len(tokens) - 1:
            logger.warning("17track token #%s quota hit(code=%s), rotating", index, code)
            continue
        return last
    return last


def enqueue_logistics_sync_job(
    *,
    project_id: int | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """物流同步入队(幂等:同范围活跃任务返回 already_queued;无 token 直接 blocked 诚实报)。"""
    project_id, actor_id, is_system = _authorize_enqueue_scope(project_id, staff)
    if not _token():
        return {
            "status": "blocked",
            "reason": "missing_token",
            "message": "未配置 VKPI_17TRACK_TOKEN——17track 免费注册后把 token 写入运行环境即可启用。",
        }
    conn = get_conn()
    scope_key = str(int(project_id)) if project_id else "all"
    active = conn.execute(
        """
        SELECT id FROM apify_jobs
        WHERE job_type=? AND status IN ('queued','running')
          AND COALESCE(payload->>'scope_key','')=? LIMIT 1
        """,
        (JOB_TYPE, scope_key),
    ).fetchone()
    if active:
        return {"status": "already_queued", "job_id": int(dict(active)["id"])}
    if project_id is not None:
        project = conn.execute(
            "SELECT id FROM vkpi_projects WHERE id=? LIMIT 1",
            (project_id,),
        ).fetchone()
        if not project:
            raise LogisticsAccessError("logistics_project_not_found", 404)
    assignment_ids = sorted(int(row["id"]) for row in _candidates(conn, project_id))
    actor_user_id = _int((staff or {}).get("user_id"))
    payload = {
        "project_id": project_id,
        "scope_key": scope_key,
        "query_text": f"物流同步 · {'项目 ' + scope_key if project_id else '全部在途'}",
        "target_type": "logistics",
        "triggered_by_user_id": actor_user_id or None,
        "staff_id": actor_id or None,
        FENCE_KEY: {
            "version": FENCE_VERSION,
            "actor_kind": "system" if is_system else "staff",
            "staff_id": actor_id,
            "user_id": actor_user_id,
            "project_id": project_id,
            "assignment_ids": assignment_ids,
        },
    }
    job = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id
        """,
        (JOB_TYPE, json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    conn.commit()
    return {"status": "queued", "job_id": int(dict(job)["id"]) if job else None}


def _candidates(conn: Any, project_id: int | None) -> list[dict[str, Any]]:
    clauses = [
        "COALESCE(tracking_number,'') != ''",
        "COALESCE(is_placeholder_tracking, FALSE) = FALSE",
        # 已签收的不再耗配额
        "COALESCE(metadata_json->'shipping'->>'status','') NOT IN ('Delivered','已签收')",
        # 6h 节流(2026-06-12 "不想这么早用完"):刚同步过的行跳过
        "(COALESCE(NULLIF(metadata_json->'shipping'->>'synced_at',''),'1970-01-01T00:00:00Z'))::timestamptz < NOW() - interval '6 hours'",
    ]
    params: list[Any] = []
    if project_id:
        clauses.append("project_id=?")
        params.append(int(project_id))
    rows = conn.execute(
        f"""
        SELECT id, project_id, tracking_number, metadata_json, kol_pool_id
        FROM vkpi_project_kol_assignments
        WHERE {' AND '.join(clauses)}
        ORDER BY updated_at DESC LIMIT 80
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def _active_fenced_actor(conn: Any, fence: dict[str, Any]) -> dict[str, Any]:
    if str(fence.get("actor_kind") or "") == "system":
        if _int(fence.get("staff_id")) or _int(fence.get("user_id")):
            raise LogisticsAccessError("logistics_actor_fence_invalid")
        return {"id": 0, "user_id": 0, "role": "admin", "is_owner": 1}
    staff_id = _int(fence.get("staff_id"))
    if staff_id <= 0:
        raise LogisticsAccessError("logistics_actor_fence_invalid")
    row = conn.execute(
        """
        SELECT s.*, u.status AS user_status
        FROM staff s JOIN users u ON u.id=s.user_id
        WHERE s.id=? LIMIT 1
        """,
        (staff_id,),
    ).fetchone()
    if not row:
        raise LogisticsAccessError("logistics_actor_inactive")
    actor = dict(row)
    if actor.get("active") not in (True, 1, "1") or str(actor.get("suspended_at") or "").strip():
        raise LogisticsAccessError("logistics_actor_inactive")
    if not user_status_allows_auth(actor.get("user_status"), production=True):
        raise LogisticsAccessError("logistics_actor_inactive")
    if not check_tab_permission(actor, "vkpi", "write"):
        raise LogisticsAccessError("logistics_actor_permission_revoked")
    fenced_user_id = _int(fence.get("user_id"))
    if fenced_user_id and fenced_user_id != _int(actor.get("user_id")):
        raise LogisticsAccessError("logistics_actor_changed")
    return actor


def _revalidate_access_fence(conn: Any, payload: dict[str, Any]) -> set[int]:
    fence = payload.get(FENCE_KEY)
    if not isinstance(fence, dict) or _int(fence.get("version")) != FENCE_VERSION:
        raise LogisticsAccessError("logistics_access_fence_required")
    project_id = _normalize_project_id(payload.get("project_id"))
    if project_id != _normalize_project_id(fence.get("project_id")):
        raise LogisticsAccessError("logistics_project_fence_drifted")
    expected_scope_key = str(project_id) if project_id is not None else "all"
    if str(payload.get("scope_key") or "") != expected_scope_key:
        raise LogisticsAccessError("logistics_project_fence_drifted")
    if _int(payload.get("staff_id")) != _int(fence.get("staff_id")):
        raise LogisticsAccessError("logistics_actor_fence_drifted")
    if _int(payload.get("triggered_by_user_id")) != _int(fence.get("user_id")):
        raise LogisticsAccessError("logistics_actor_fence_drifted")
    actor = _active_fenced_actor(conn, fence)
    if project_id is None:
        if not is_manager_staff(actor):
            raise LogisticsAccessError("logistics_global_permission_revoked")
    else:
        try:
            scope.assert_project_access(project_id, actor, write=True)
        except scope.ScopeDenied as exc:
            raise LogisticsAccessError("logistics_project_permission_revoked") from exc
        project = conn.execute(
            "SELECT id FROM vkpi_projects WHERE id=? LIMIT 1",
            (project_id,),
        ).fetchone()
        if not project:
            raise LogisticsAccessError("logistics_project_removed")
    raw_assignment_ids = fence.get("assignment_ids")
    if not isinstance(raw_assignment_ids, list) or len(raw_assignment_ids) > 80:
        raise LogisticsAccessError("logistics_assignment_fence_invalid")
    assignment_ids = [_int(value) for value in raw_assignment_ids]
    if any(value <= 0 for value in assignment_ids) or len(set(assignment_ids)) != len(assignment_ids):
        raise LogisticsAccessError("logistics_assignment_fence_invalid")
    if not assignment_ids:
        return set()
    placeholders = ",".join("?" for _ in assignment_ids)
    rows = conn.execute(
        f"SELECT id, project_id FROM vkpi_project_kol_assignments WHERE id IN ({placeholders})",
        tuple(assignment_ids),
    ).fetchall()
    assignments = {int(dict(row)["id"]): _int(dict(row).get("project_id")) for row in rows}
    if set(assignments) != set(assignment_ids) or any(value <= 0 for value in assignments.values()):
        raise LogisticsAccessError("logistics_assignment_scope_drifted")
    if project_id is not None and any(value != project_id for value in assignments.values()):
        raise LogisticsAccessError("logistics_assignment_scope_drifted")
    return set(assignment_ids)


def _access_blocked(exc: LogisticsAccessError, *, provider_called: bool) -> dict[str, Any]:
    return {
        "status": f"blocked:{exc.code}",
        "reason": exc.code,
        "provider_calls_performed": provider_called,
    }


def _revalidate_before_provider(
    conn: Any,
    payload: dict[str, Any],
    expected_assignment_ids: set[int],
) -> None:
    current = _revalidate_access_fence(conn, payload)
    if current != expected_assignment_ids:
        raise LogisticsAccessError("logistics_assignment_scope_drifted")


def run_logistics_sync_for_job(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """worker 入口:注册(幂等)+ 拉取轨迹 → 写回 shipping 元数据。"""
    del staff
    conn = get_conn()
    try:
        assignment_ids = _revalidate_access_fence(conn, payload)
    except LogisticsAccessError as exc:
        return _access_blocked(exc, provider_called=False)
    if not _token():
        return {"status": "blocked:missing_token", "provider_calls_performed": False}
    rows = [
        row for row in _candidates(conn, payload.get("project_id"))
        if int(row["id"]) in assignment_ids
    ]
    if not rows:
        return {
            "status": "ready",
            "synced": 0,
            "note": "no_active_tracking_numbers",
            "provider_calls_performed": False,
        }
    numbers = [{"number": str(row["tracking_number"]).strip()} for row in rows]
    # 注册(已注册会进 rejected,code -18019901,属幂等正常)
    rejected_invalid: dict[str, str] = {}
    try:
        _revalidate_before_provider(conn, payload, assignment_ids)
        reg = _api("register", numbers)
        for item in (reg.get("data") or {}).get("rejected") or []:
            error = item.get("error") or {}
            code = int(error.get("code") or 0)
            # -18019901 = 已注册(幂等正常),其余拒收=单号无法识别/无效
            if code != -18019901:
                rejected_invalid[str(item.get("number") or "")] = str(error.get("message") or "")[:120]
        logger.info("17track register accepted=%s rejected=%s invalid=%s",
                    len((reg.get("data") or {}).get("accepted") or []),
                    len((reg.get("data") or {}).get("rejected") or []),
                    len(rejected_invalid))
    except LogisticsAccessError as exc:
        return _access_blocked(exc, provider_called=False)
    except Exception as exc:
        return {"status": f"failed:register_{exc.__class__.__name__}", "provider_calls_performed": True}
    try:
        _revalidate_before_provider(conn, payload, assignment_ids)
        info = _api("gettrackinfo", numbers)
    except LogisticsAccessError as exc:
        return _access_blocked(exc, provider_called=True)
    except Exception as exc:
        return {"status": f"failed:gettrackinfo_{exc.__class__.__name__}", "provider_calls_performed": True}
    accepted = {str(item.get("number") or ""): item for item in (info.get("data") or {}).get("accepted") or []}
    synced = 0
    results = []
    now = _utcnow()
    for row in rows:
        number = str(row["tracking_number"]).strip()
        # 拒收回写(2026-06-12 首跑发现):无法识别的单号在 UI 明示,不再静默跳过
        if number in rejected_invalid:
            metadata = row.get("metadata_json")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            metadata = metadata if isinstance(metadata, dict) else {}
            shipping = metadata.get("shipping") if isinstance(metadata.get("shipping"), dict) else {}
            shipping.update({
                "status": "单号无法识别",
                "status_raw": "Rejected",
                "provider": "17track",
                "synced_at": now,
                "reject_reason": rejected_invalid[number],
            })
            metadata["shipping"] = shipping
            conn.execute(
                "UPDATE vkpi_project_kol_assignments SET metadata_json=?, updated_at=? WHERE id=?",
                (json.dumps(metadata, ensure_ascii=False), now, int(row["id"])),
            )
            results.append({"number": number, "status": "rejected_invalid"})
            continue
        item = accepted.get(number)
        if not item:
            results.append({"number": number, "status": "not_returned"})
            continue
        track = item.get("track_info") or {}
        latest_status = ((track.get("latest_status") or {}).get("status")) or "NotFound"
        latest_event = track.get("latest_event") or {}
        providers = (track.get("tracking") or {}).get("providers") or []
        events = []
        for provider in providers[:1]:
            for event in (provider.get("events") or [])[:5]:
                events.append({
                    "time": str(event.get("time_iso") or event.get("time_utc") or ""),
                    "desc": str(event.get("description") or "")[:160],
                    "location": str(event.get("location") or "")[:80],
                })
        metadata = row.get("metadata_json")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        metadata = metadata if isinstance(metadata, dict) else {}
        shipping = metadata.get("shipping") if isinstance(metadata.get("shipping"), dict) else {}
        shipping.update({
            "status": STATUS_CN.get(latest_status, latest_status),
            "status_raw": latest_status,
            "latest_event": {
                "time": str(latest_event.get("time_iso") or ""),
                "desc": str(latest_event.get("description") or "")[:160],
                "location": str(latest_event.get("location") or "")[:80],
            },
            "events": events,
            "provider": "17track",
            "synced_at": now,
        })
        metadata["shipping"] = shipping
        conn.execute(
            "UPDATE vkpi_project_kol_assignments SET metadata_json=?, updated_at=? WHERE id=?",
            (json.dumps(metadata, ensure_ascii=False), now, int(row["id"])),
        )
        synced += 1
        results.append({"number": number, "status": latest_status})
        # 点4 履约交付信号:仅在【真实已签收】时落 vkpi_shipments.delivered_at + 把派单
        # 推进到 canonical delivered(走 service 路径,不裸 UPDATE 绕校验)。非 delivered
        # 事件不进此分支——绝不误写。失败不影响 shipping 元数据已写成功的主流程。
        if latest_status == "Delivered":
            delivered_ts = str(latest_event.get("time_iso") or "") or now
            try:
                signal = record_delivered_signal(
                    project_id=int(row["project_id"]),
                    assignment_id=int(row["id"]),
                    tracking_number=number,
                    carrier="17track",
                    raw_status=latest_status,
                    delivered_at=delivered_ts,
                    last_checked_at=now,
                    kol_pool_id=row.get("kol_pool_id"),
                )
                results[-1]["delivered_signal"] = {
                    "shipment_action": signal.get("shipment_action"),
                    "stage_action": signal.get("stage_action"),
                }
            except Exception as exc:
                logger.warning("delivered signal write failed assignment=%s: %s", row["id"], exc)
                results[-1]["delivered_signal"] = {"error": exc.__class__.__name__}
    conn.commit()
    return {
        "status": "ready",
        "synced": synced,
        "total": len(rows),
        "results": results[:20],
        "provider_calls_performed": True,
    }
