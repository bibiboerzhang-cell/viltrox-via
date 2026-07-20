"""Fail-closed cross-process resource slots for bounded Apify worker bursts.

Multiple ``apify_jobs`` processes already coordinate claims with
``FOR UPDATE SKIP LOCKED``.  That protects a row, but it does not protect a
shared provider or media pipeline from an unsafe fan-out.  This module maps
the long-running job families to explicit resource groups and supplies the
small, deterministic slot-selection primitive used by the worker.

2026-07-20 容量评审(owner 明令满功率):硬顶 2→16。实测口径:Gemini 付费
Tier 2(3000 RPM/3M TPM)撑得住 16 路视频深析;Apify SCALE 128 并发/256GB
(16 路仅用 12%);prod 8 核 30GB 负载 <0.3。费用护栏不在并发层——LLM 预算
预约(单任务+cron 帽)与 Apify durable claim 记账全程兜底。env 取值仍被
[1, MAX] 校验,fail-closed 语义不变。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


RESOURCE_SLOT_SCOPE = "vkpi_apify_worker_resource_slot"
MAX_RESOURCE_SLOT_CAP = 16

RESOURCE_ENV_NAMES: Mapping[str, str] = {
    "profile_media": "APIFY_WORKER_PROFILE_MEDIA_CONCURRENCY",
    "comments_pipeline": "APIFY_WORKER_COMMENTS_CONCURRENCY",
    "gemini_video": "APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY",
}

_JOB_RESOURCE_GROUPS: Mapping[str, str] = {
    "kol_profile_deep_crawl": "profile_media",
    "video_url_resolve": "profile_media",
    "kol_pool_comments_collect": "comments_pipeline",
    "official_channel_comments_collect": "comments_pipeline",
    # Audience refresh can issue Gemini age-inference calls.  Sharing the
    # existing Gemini slot means the split improves overlap without increasing
    # default provider concurrency (gemini_video remains globally capped at 1).
    "kol_audience_stats_refresh": "gemini_video",
}


def resource_slot_limits(env: Mapping[str, str]) -> dict[str, int]:
    """Return reviewed resource limits, rejecting unsafe configuration.

    Defaults preserve the current single-resource execution shape.  Values in
    [1, 16] are reviewed burst tiers; zero, negative, non-numeric, or greater
    values fail startup instead of silently widening provider pressure.
    """

    limits: dict[str, int] = {}
    for group, env_name in RESOURCE_ENV_NAMES.items():
        raw = str(env.get(env_name, "1") or "").strip()
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{env_name} must be an integer") from exc
        if not 1 <= value <= MAX_RESOURCE_SLOT_CAP:
            raise ValueError(
                f"{env_name} must stay within [1, {MAX_RESOURCE_SLOT_CAP}]"
            )
        limits[group] = value
    return limits


def resource_group_for_job(job: Mapping[str, Any]) -> str | None:
    """Classify only reviewed provider-heavy jobs; unknowns stay unbounded."""

    job_type = str(job.get("job_type") or "").strip().lower()
    direct = _JOB_RESOURCE_GROUPS.get(job_type)
    if direct:
        return direct
    if job_type != "video":
        return None
    payload = job.get("payload") if isinstance(job.get("payload"), Mapping) else {}
    derive_method = str(payload.get("derive_method") or "mock").strip().lower()
    return "gemini_video" if derive_method != "mock" else None


def acquire_resource_slot(
    resource_group: str,
    limit: int,
    *,
    try_lock: Callable[[str, str], bool],
) -> str | None:
    """Try the finite slot set in stable order and return the acquired key."""

    group = str(resource_group or "").strip().lower()
    if group not in RESOURCE_ENV_NAMES:
        raise ValueError("unsupported worker resource group")
    if not 1 <= int(limit) <= MAX_RESOURCE_SLOT_CAP:
        raise ValueError("worker resource slot limit is outside reviewed bounds")
    for index in range(int(limit)):
        slot_key = f"{group}:{index}"
        if try_lock(RESOURCE_SLOT_SCOPE, slot_key):
            return slot_key
    return None
