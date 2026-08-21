"""Pure claim-lane policy shared by worker startup and offline tests.

``payload.batch`` predates the worker pool and is not a reliable ownership
field.  In particular, representative-video and worker-follow-up jobs used
either a custom batch value or no batch value at all, so a nominal
``interactive`` worker still consumed most of the background chain.

New enqueue paths should write ``payload.queue_lane`` explicitly.  The legacy
rules below are deliberately conservative so rows written by an older server
still land on the batch lane instead of starving user-triggered work.
"""
from __future__ import annotations

from collections.abc import Mapping


BATCH_MARKERS = ("on_demand_batch", "recent", "remaining")
VALID_CLAIM_LANES = frozenset({"all", "interactive", "batch"})
EXPLICIT_QUEUE_LANES = frozenset({"interactive", "batch"})
LEGACY_BATCH_SOURCES = (
    "final_v1_worker_followup",
    "kol_url_deep_crawl",
    "kol_url_profile_flow",
)

# Seven-day observed service-time bands (2026-07-15, local real queue):
# dossier/logistics ~0s, content-fit ~5.5s, video/profile ~30-33s,
# audience ~120s, smart advance/comments ~172-184s.  The scheduler uses only
# broad, reviewed bands; it never trusts a request payload to choose its own
# runtime class.  Unknown/new work stays in the middle band until measured.
VERY_SHORT_JOB_TYPES = frozenset({"account_dossier_extract", "logistics_track_sync"})
SHORT_JOB_TYPES = frozenset({"kol_content_fit_analysis"})
MEDIUM_JOB_TYPES = frozenset(
    {"video", "kol_profile_deep_crawl", "kol_video_metric_refresh"}
)
LONG_JOB_TYPES = frozenset({"kol_audience_stats_refresh"})
VERY_LONG_JOB_TYPES = frozenset({"smart_search_profile_advance", "kol_pool_comments_collect"})
QUEUE_SPT_AGING_MINUTES = 15


def classify_queue_lane(payload: Mapping[str, object] | None) -> str:
    """Classify one payload using the same precedence as the SQL predicate."""

    data = payload or {}
    explicit = str(data.get("queue_lane") or "").strip().lower()
    if explicit in EXPLICIT_QUEUE_LANES:
        return explicit
    if str(data.get("batch") or "").strip():
        return "batch"
    if str(data.get("source") or "").strip().lower() in LEGACY_BATCH_SOURCES:
        return "batch"
    if str(data.get("trigger") or "").strip().lower() == "final_v1_done":
        return "batch"
    return "interactive"


def queue_lane_sql_expression(payload_column: str = "payload") -> str:
    """Return the reviewed SQL expression for a JSONB payload column.

    ``payload_column`` is supplied only by worker source, never by a request.
    Keeping the expression in this pure module prevents the lane predicate and
    the all-lane priority order from drifting apart.
    """

    sources = ", ".join(f"'{source}'" for source in LEGACY_BATCH_SOURCES)
    return f"""(
        CASE
          WHEN LOWER(COALESCE({payload_column}->>'queue_lane', '')) IN ('interactive', 'batch')
            THEN LOWER({payload_column}->>'queue_lane')
          WHEN COALESCE({payload_column}->>'batch', '') <> '' THEN 'batch'
          WHEN LOWER(COALESCE({payload_column}->>'source', '')) IN ({sources}) THEN 'batch'
          WHEN LOWER(COALESCE({payload_column}->>'trigger', '')) = 'final_v1_done' THEN 'batch'
          ELSE 'interactive'
        END
    )"""


def queue_priority_sql_expression(payload_column: str = "payload") -> str:
    """Return the claim/display priority expression for one payload column."""

    lane = queue_lane_sql_expression(payload_column)
    return f"""(
        CASE
          WHEN {lane} = 'interactive' THEN 0
          WHEN {payload_column}->>'batch' = 'on_demand_batch' THEN 1
          ELSE 2
        END
    )"""


def queue_service_priority(job_type: object, *, age_minutes: float = 0.0) -> int:
    """Return the bounded SPT band used after the lane/interaction priority.

    Jobs waiting at least ``QUEUE_SPT_AGING_MINUTES`` are promoted to band 0;
    the following ``created_at`` key then restores FIFO among aged work.  This
    keeps comments/audience jobs from starving under a stream of short tasks.
    """

    if float(age_minutes or 0.0) >= QUEUE_SPT_AGING_MINUTES:
        return 0
    normalized = str(job_type or "").strip().lower()
    if normalized in VERY_SHORT_JOB_TYPES:
        return 0
    if normalized in SHORT_JOB_TYPES:
        return 1
    if normalized in MEDIUM_JOB_TYPES:
        return 2
    if normalized in LONG_JOB_TYPES:
        return 4
    if normalized in VERY_LONG_JOB_TYPES:
        return 5
    return 3


def queue_service_priority_sql_expression(
    job_type_column: str = "job_type",
    created_at_column: str = "created_at",
) -> str:
    """Return the SQL twin of :func:`queue_service_priority`.

    Column names are fixed by worker source, never supplied by a request.
    Lane priority remains the first sort key, so interactive work still wins.
    """

    def values(items: frozenset[str]) -> str:
        return ", ".join(f"'{item}'" for item in sorted(items))

    return f"""(
        CASE
          WHEN {created_at_column} <= NOW() - INTERVAL '{QUEUE_SPT_AGING_MINUTES} minutes' THEN 0
          WHEN LOWER(COALESCE({job_type_column}, '')) IN ({values(VERY_SHORT_JOB_TYPES)}) THEN 0
          WHEN LOWER(COALESCE({job_type_column}, '')) IN ({values(SHORT_JOB_TYPES)}) THEN 1
          WHEN LOWER(COALESCE({job_type_column}, '')) IN ({values(MEDIUM_JOB_TYPES)}) THEN 2
          WHEN LOWER(COALESCE({job_type_column}, '')) IN ({values(LONG_JOB_TYPES)}) THEN 4
          WHEN LOWER(COALESCE({job_type_column}, '')) IN ({values(VERY_LONG_JOB_TYPES)}) THEN 5
          ELSE 3
        END
    )"""


def normalize_claim_lane(value: object) -> str:
    lane = str(value or "all").strip().lower() or "all"
    if lane not in VALID_CLAIM_LANES:
        raise ValueError(f"unsupported APIFY_WORKER_CLAIM_LANE={lane!r}")
    return lane


def claim_lane_sql(value: object) -> str:
    lane = normalize_claim_lane(value)
    expression = queue_lane_sql_expression("payload")
    if lane == "interactive":
        return f"AND {expression} = 'interactive'"
    if lane == "batch":
        return f"AND {expression} = 'batch'"
    return ""
