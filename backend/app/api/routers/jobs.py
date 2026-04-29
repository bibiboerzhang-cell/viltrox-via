"""
api/routers/jobs.py — Worker 状态路由
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from app.services.jobs.results import load_job_result

router = APIRouter(tags=["jobs"])


def _decode_json_field(data: dict, source_key: str, target_key: str) -> None:
    raw = data.get(source_key)
    if not raw:
        return
    if isinstance(raw, (dict, list)):
        data[target_key] = raw
        return
    try:
        data[target_key] = json.loads(raw)
    except Exception:
        data[target_key] = raw


@router.get("/api/jobs/{task_id}")
async def get_job_status(task_id: str, request: Request):
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="job queue unavailable")

    snapshot = await queue.get_status(task_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="job not found")

    data = dict(snapshot)
    _decode_json_field(data, "stats_json", "stats")
    _decode_json_field(data, "result_json", "result")

    result_path = data.get("result_path")
    if result_path and "result" not in data:
        try:
            data["result"] = load_job_result(result_path)
        except Exception as exc:
            data["result_load_error"] = str(exc)

    return data
