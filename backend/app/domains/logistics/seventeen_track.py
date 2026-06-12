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
from app.db.connection import get_conn

logger = get_logger("viltrox.domains.logistics.seventeen_track")

JOB_TYPE = "logistics_track_sync"
API_BASE = "https://api.17track.net/track/v2.2"

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


def _token() -> str:
    """env 优先;admin master 进程不重启拿不到新 env——兜底读仓库根 .env(gitignored)。"""
    token = os.getenv("VKPI_17TRACK_TOKEN", "").strip()
    if token:
        return token
    try:
        from pathlib import Path

        env_path = Path(__file__).resolve().parents[4] / ".env"
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("VKPI_17TRACK_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _api(path: str, payload: list[dict[str, Any]]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{API_BASE}/{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "17token": _token()},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def enqueue_logistics_sync_job(
    *,
    project_id: int | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """物流同步入队(幂等:同范围活跃任务返回 already_queued;无 token 直接 blocked 诚实报)。"""
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
    payload = {
        "project_id": int(project_id) if project_id else None,
        "scope_key": scope_key,
        "query_text": f"物流同步 · {'项目 ' + scope_key if project_id else '全部在途'}",
        "target_type": "logistics",
        "triggered_by_user_id": (staff or {}).get("user_id"),
        "staff_id": (staff or {}).get("id") or (staff or {}).get("staff_id"),
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
    ]
    params: list[Any] = []
    if project_id:
        clauses.append("project_id=?")
        params.append(int(project_id))
    rows = conn.execute(
        f"""
        SELECT id, project_id, tracking_number, metadata_json
        FROM vkpi_project_kol_assignments
        WHERE {' AND '.join(clauses)}
        ORDER BY updated_at DESC LIMIT 80
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def run_logistics_sync_for_job(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """worker 入口:注册(幂等)+ 拉取轨迹 → 写回 shipping 元数据。"""
    del staff
    if not _token():
        return {"status": "blocked:missing_token"}
    conn = get_conn()
    rows = _candidates(conn, payload.get("project_id"))
    if not rows:
        return {"status": "ready", "synced": 0, "note": "no_active_tracking_numbers"}
    numbers = [{"number": str(row["tracking_number"]).strip()} for row in rows]
    # 注册(已注册会进 rejected,code -18019901,属幂等正常)
    rejected_invalid: dict[str, str] = {}
    try:
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
    except Exception as exc:
        return {"status": f"failed:register_{exc.__class__.__name__}"}
    try:
        info = _api("gettrackinfo", numbers)
    except Exception as exc:
        return {"status": f"failed:gettrackinfo_{exc.__class__.__name__}"}
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
    conn.commit()
    return {"status": "ready", "synced": synced, "total": len(rows), "results": results[:20]}
