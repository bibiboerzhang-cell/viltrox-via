"""Pure response helpers for KOL pool search endpoints."""

from __future__ import annotations


_FAILED_FLOW_STATUSES = {
    "failed",
    "crawl_failed",
    "profile_crawl_failed",
    "provider_error",
    "timeout",
    "creator_unresolved",
}
_PENDING_FLOW_STATUSES = {"queued", "already_queued", "running", "pending"}
_READY_FLOW_STATUSES = {
    "ready",
    "already_ready",
    "already_analyzed",
    "already_fresh",
    "done",
    "executed",
}


def _body_bool(body: dict, key: str, *, default: bool = False) -> bool:
    if key not in body:
        return default
    value = body.get(key)
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _flow_status(result: dict) -> str:
    video_flow = result.get("video_flow") if isinstance(result.get("video_flow"), dict) else {}
    profile_flow = result.get("profile_flow") if isinstance(result.get("profile_flow"), dict) else {}
    return str(video_flow.get("status") or profile_flow.get("status") or result.get("status") or "").strip().lower()


def _url_response_status(result: dict) -> str:
    status = _flow_status(result)
    if status in _PENDING_FLOW_STATUSES:
        return "queued" if status in {"queued", "already_queued", "pending"} else "running"
    if status in _FAILED_FLOW_STATUSES or "failed" in status:
        return "failed"
    if not result.get("execute"):
        if result.get("url_type") not in {"profile", "video"} or status in {
            "unsupported",
            "unsupported_platform",
        }:
            return "partial"
        return "ready"
    if status in _READY_FLOW_STATUSES:
        return "ready"
    return "partial"


def _result_item_count(result: dict | None) -> int:
    if not isinstance(result, dict):
        return 0
    items = result.get("items")
    if isinstance(items, list) and items:
        return len(items)
    buckets = result.get("buckets") if isinstance(result.get("buckets"), dict) else {}
    bucket_count = sum(len(value) for value in buckets.values() if isinstance(value, list))
    return bucket_count if bucket_count else len(items or [])


def _text_response_status(recall_result: dict, discovery_result: dict | None) -> str:
    recall_status = str(recall_result.get("status") or "").strip().lower()
    discovery_status = str((discovery_result or {}).get("status") or "").strip().lower()
    recall_count = _result_item_count(recall_result)
    discovery_count = _result_item_count(discovery_result)
    total_count = recall_count + discovery_count
    failed = recall_status in _FAILED_FLOW_STATUSES or discovery_status in _FAILED_FLOW_STATUSES
    partial = recall_status == "partial" or discovery_status == "partial"
    if failed:
        return "partial" if total_count else "failed"
    if partial:
        return "partial"
    if total_count <= 0:
        return "empty"
    return "ready"


def _pending_enrichment_state() -> dict:
    return {
        "contacts": {
            "status": "pending",
            "async": True,
            "message": "公开联系方式将在账号抓取完成后由 worker 补充。",
        },
        "audience": {
            "status": "pending",
            "async": True,
            "message": "受众统计依赖评论样本，将在后台队列继续补充。",
        },
    }
