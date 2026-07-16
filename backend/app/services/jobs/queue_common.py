"""Common job queue types and helpers."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.ai.orchestrator import TaskStatus


# Jobs in this set may reach paid/network providers and therefore require the
# Redis + Postgres durable worker fence.  The in-process development queue has
# no cross-process claim, reservation, or execution-context proof and must
# reject them before it advertises a task as queued.
DURABLE_PROVIDER_JOB_TYPES = frozenset(
    {
        "apify_batch_refresh",
        "comment_intelligence_post",
        "comment_intelligence_recent",
        "comments_batch_collect",
        "comments_collect_post",
        "discovery_federated_search",
        "industry_account_refresh",
        "intel_bh_refresh",
        "intel_bh_reviews",
        "intel_lens_compare",
        "intel_lens_monitor",
        "intel_scan_account",
        "intel_scan_matrix",
        "intel_via_learning",
        "kol_apify_enrich",
        "kol_apify_enrich_candidates",
        "kol_dossier_scan",
        "kol_onboarding",
        "kol_platform_search",
        "official_visual_scan",
        "platform_ingest_amazon",
        "platform_ingest_bh",
        "platform_ingest_facebook",
        "platform_ingest_instagram",
        "platform_ingest_reddit",
        "platform_ingest_tiktok",
        "platform_ingest_web",
        "platform_ingest_youtube",
        "project_video_metadata_refresh",
        "score_kol_content",
        "verification_scan_pending",
        "verification_scan_single",
        "vkpi_analytics_compare",
        "vkpi_analytics_monitor",
        "vkpi_kol_pool_on_demand_refresh",
        "vkpi_official_channel_sync",
    }
)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(raw.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def seconds_between(start: Any, end: Any) -> Optional[float]:
    start_dt = parse_ts(start)
    end_dt = parse_ts(end)
    if not start_dt or not end_dt:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds())


def normalize_payload(payload: Any) -> Dict[str, Any]:
    if is_dataclass(payload):
        return asdict(payload)
    if isinstance(payload, dict):
        return payload
    raise TypeError(f"Unsupported job payload type: {type(payload)!r}")


def decode_json(raw: Any, default: Any) -> Any:
    if raw in (None, "", b""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


TERMINAL_JOB_STATUSES = {
    TaskStatus.DONE.value,
    TaskStatus.PARTIAL.value,
    TaskStatus.FAILED.value,
    "prefilter_rejected",
    "cancelled",
    "timeout",
}

TRANSIENT_JOB_STATUSES = {
    TaskStatus.QUEUED.value,
    TaskStatus.RETRYING.value,
    TaskStatus.PROCESSING.value,
    "running",
}


class BaseJobQueue:
    backend_name = "unknown"

    async def enqueue(
        self,
        job_type: str,
        payload: Any,
        submission_id: Optional[int] = None,
        *,
        priority: int | None = None,
        lock_key: str | None = None,
        timeout_seconds: int | None = None,
    ) -> str:
        raise NotImplementedError

    async def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def set_status(self, task_id: str, status: str, **extra: Any) -> None:
        raise NotImplementedError

    async def pop_job(self, consumer_name: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def ack(self, raw_job: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def move_to_dead_letter(self, raw_job: Dict[str, Any], reason: str) -> None:
        raise NotImplementedError

    async def subscribe_task_events(self, task_id: str):
        raise NotImplementedError

    async def close(self) -> None:
        return None

    async def runtime_stats(self) -> Dict[str, Any]:
        return {"backend": self.backend_name}
