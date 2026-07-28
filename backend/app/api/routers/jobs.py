"""
api/routers/jobs.py — Worker 状态路由
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies.auth import get_user_required
from app.services.jobs.results import load_job_result

router = APIRouter(tags=["jobs"])

_MAX_JSON_DECODE_PASSES = 2
_RESULT_POINTER_KEYS = frozenset({"status", "result_path"})


def _decode_json_field(data: dict, source_key: str, target_key: str) -> None:
    raw = data.get(source_key)
    if not raw:
        return
    if isinstance(raw, (dict, list)):
        data[target_key] = raw
        return
    value = raw
    for _ in range(_MAX_JSON_DECODE_PASSES):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except Exception:
            break
    data[target_key] = value


def _result_pointer_path(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    if not set(value).issubset(_RESULT_POINTER_KEYS):
        return ""
    result_path = value.get("result_path")
    if not isinstance(result_path, str):
        return ""
    return result_path.strip()


@router.get("/api/jobs/{task_id}")
async def get_job_status(task_id: str, request: Request, user=Depends(get_user_required)):
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="job queue unavailable")

    snapshot = await queue.get_status(task_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="job not found")

    data = dict(snapshot)
    role = str(user.get("role") or user.get("auth_role") or "").strip().lower()
    is_admin = role in {"admin", "founder", "owner"} or bool(user.get("is_owner"))
    user_id = str(user.get("id") or "").strip()
    staff_id = str(user.get("staff_id") or "").strip()
    job_user_id = str(data.get("user_id") or "").strip()
    triggered_by = str(data.get("triggered_by_staff_id") or "").strip()
    owns_job = bool(user_id and job_user_id and user_id == job_user_id) or bool(
        staff_id and triggered_by and staff_id == triggered_by
    )
    if not is_admin and not owns_job:
        raise HTTPException(status_code=404, detail="job not found")

    _decode_json_field(data, "stats_json", "stats")
    _decode_json_field(data, "result_json", "result")

    pointer_path = _result_pointer_path(data.get("result"))
    result_path = data.get("result_path") or pointer_path
    if result_path and (pointer_path or "result" not in data):
        try:
            data["result"] = load_job_result(result_path)
        except Exception as exc:
            if pointer_path:
                data.pop("result", None)
            data["result_load_error"] = str(exc)

    allowed = {
        "task_id",
        "job_type",
        "status",
        "stage",
        "summary",
        "progress",
        "retry_count",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "stats",
        "result",
    }
    return {key: value for key, value in data.items() if key in allowed}
