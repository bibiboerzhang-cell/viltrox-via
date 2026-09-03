"""Unified, read-only task-state recovery for one scoped MY KOL video library.

Contract ``my_kol_video_recovery_v1`` (served by
``GET /api/admin/vkpi/my-kol/{kol_pool_id}/videos``)::

    {
      "contract": "my_kol_video_recovery_v1",
      "kol_pool_id": 88,
      "read_only": true,
      "profile_crawl": TaskState,                # account crawl (kol_profile_deep_crawl)
      "items": [
        { ...video row from _video_evidence_for_kol...,
          "evidence_id": 701,
          "published_at": "2026-08-01T10:00:00+00:00" | null,
          "viltrox_modalities": ["visual", "subtitle", "audio"],  # subset, fixed order; [] when unknown
          "tasks": {
            "metric_refresh": TaskState,        # kol_video_metric_refresh + metric snapshot
            "final_v1": TaskState,              # main video analysis + cache
            "keyframe_qa": TaskState            # keyframe review + cache
          }
        }
      ],
      "summary": {"total", "views_total", "views_measured", "final_v1_ready"},
      "page": {
        "limit": 60, "returned": 60, "has_more": true,
        "next_cursor": "<opaque>" | null,
        "cursor_kind": "published_at_id",       # keyset: (published_at DESC, id DESC)
        "order": "published_at_desc_id_desc"
      },
      "total": <summary.total>, "returned": <page.returned>,
      "has_more": <page.has_more>, "next_cursor": <page.next_cursor>
    }

    TaskState = {
      "status": "queued" | "running" | "retrying" | "blocked" | "failed"
                | "ready" | "not_requested",     # durable job truth (apify_jobs)
      "job_id": int | null,
      "requested_at": iso | null,                # job created_at
      "updated_at": iso | null,                  # job updated_at
      "reason_class": str | null,                # only for blocked / failed, see below
      "failure_category": str | null,            # O→F 六类(download|authorization|budget|model|provider|unknown)
      "failure_reason_human": str | null,        # 中文一句,门面零内部术语
      "failure_code": str | null,                # 稳定机器码(JSON reason / 类别),绝不回显自由文本
      "data": {                                  # persisted output, independent of the job
        "status": "ready" | "stale" | "none",
        "freshness": "fresh" | "stale" | "never" | "unavailable",
        "updated_at": iso | null,
        "superseded_by_job": bool                # an active job is newer than this data
      }
    }

Rules the contract guarantees:

* **Task state != data freshness.**  ``status`` is the latest durable job for
  that target; ``data`` describes what is persisted right now.  A page reopen
  therefore restores queued / running / retrying work exactly as the worker
  ledger sees it, and a ``ready`` job whose promised output is missing reports
  ``failed`` rather than pretending.
* **Old results never mask a newer request.**  When an active job was created
  after the persisted data, ``data.superseded_by_job`` is true and ``status``
  is the active job state; the previous ``data`` stays visible as history.
* **Stable keyset paging.**  Ordering is ``published_at DESC, id DESC`` where
  ``published_at = COALESCE(publish_date, posted_at, created_at)`` (never
  ``updated_at`` / ``view_count``, which drift with every metric refresh).  The
  cursor encodes the last row's ``(published_at, id)``; offsets are gone.
* **reason_class is a closed vocabulary, never raw text.**  It is derived by
  rule from ``apify_jobs.status`` / ``last_error_category`` / ``last_error``
  (the text is classified, never echoed):

  - blocked → ``permission`` | ``budget`` | ``missing_profile`` | ``provider_unavailable``
  - failed  → ``provider_error`` | ``timeout`` | ``code_error`` | ``revoked``
  - any other status, or a blocked/failed job whose ledger text matches no
    rule → ``null`` (honest "unclassified"; the UI shows the bare status).

* **Failure fields share the O→F contract.**  ``failure_category`` /
  ``failure_reason_human`` / ``failure_code`` are produced by
  ``video_analysis_progress_reasons.failure_fields`` (the same rule table the
  account progress endpoint uses) for ``blocked`` / ``failed`` / ``retrying``
  jobs and are ``null`` otherwise.  ``failure_code`` keeps only token-like
  machine codes (JSON ``reason`` / ``last_error_category``); a free-text
  ``last_error`` first line is never echoed, so the "no raw worker text" rule
  above still holds.

* **Shared projection.**  ``attach_task_states`` is the single implementation
  behind this endpoint and the MY KOL board-ext ``recent_videos`` wall, so
  the same evidence id yields byte-identical ``tasks`` / ``viltrox_modalities``
  on both surfaces.  ``viltrox_modalities`` is the ordered subset of
  ``visual`` / ``subtitle`` / ``audio`` found in the latest ready final_v1
  ``brand_product_evidence.viltrox_evidence[].modality`` (``metadata`` hits
  and pre-evidence results give ``[]``); zero LLM calls.

Versioning: the contract name stays ``my_kol_video_recovery_v1``; every
addition above is optional / additive and old readers keep working.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.core.logging import get_logger
from app.domains.kol import video_analysis_progress_reasons as progress_reasons
from app.domains.kol.my_kol_video_cache_truth import analysis_cache_summary_for_kol, analysis_caches_for_evidence
from app.domains.kol.video_evidence_projection import (
    final_v1_modalities_for_evidence,
    viltrox_modalities,
)
from app.domains.kol.video_keyframe_qa_cache import KEYFRAME_QA_DERIVE_METHOD, valid_qa_caches

logger = get_logger("viltrox.domains.kol.my_kol_video_recovery")
CONTRACT = "my_kol_video_recovery_v1"
CURSOR_KIND = "published_at_id"
ORDER = "published_at_desc_id_desc"
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 60
FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
PROFILE_JOB_TYPE = "kol_profile_deep_crawl"
METRIC_JOB_TYPE = "kol_video_metric_refresh"
VIDEO_JOB_TYPE = "video"
PROFILE_FRESH_HOURS = 24

TASK_STATUSES = frozenset({"queued", "running", "retrying", "blocked", "failed", "ready", "legacy_unverified", "not_requested"})
ACTIVE_TASK_STATUSES = frozenset({"queued", "running", "retrying"})
DATA_STATUSES = frozenset({"ready", "stale", "legacy_unverified", "none"})
FRESHNESS_VALUES = frozenset({"fresh", "stale", "never", "unavailable"})
BLOCKED_REASON_CLASSES = frozenset({"permission", "budget", "missing_profile", "provider_unavailable"})
FAILED_REASON_CLASSES = frozenset({"provider_error", "timeout", "code_error", "revoked"})
REASON_CLASSES = BLOCKED_REASON_CLASSES | FAILED_REASON_CLASSES
FAILURE_FIELD_KEYS: tuple[str, ...] = ("failure_category", "failure_reason_human", "failure_code")
FAILURE_FIELD_STATUSES = frozenset({"blocked", "failed", "retrying"})
_EMPTY_FAILURE_FIELDS: dict[str, Any] = {key: None for key in FAILURE_FIELD_KEYS}
_MACHINE_CODE_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,80}$")

# ── reason_class 规则表(词表规则,零 LLM;只输出类别,绝不回显 last_error 原文)──
# 顺序即优先级:先命中先归类。blocked 与 failed 各自独立词表,互不串台。
# 词条来源:workers/apify_jobs_worker_helpers._error_category 细类 + 真库 last_error
# 形态普查(budget_guard_blocked / cancelled_by_scope / stale_running_reclaimed /
# NameError / yt-dlp / media_resolve / Gemini File API ... )。
_BLOCKED_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 就绪门/模型绑定未放行语义是「模型不可用」而非「没钱」,故先于 budget 判。
    ("provider_unavailable", (
        "readiness", "model_binding", "not_production_ready", "fallback_to_rule", "llm_gateway",
        "model_not_ready", "not configured", "not_configured", "no api key", "missing_api_key",
        "api_key_missing", "provider_disabled", "gate closed", "gate_closed",
    )),
    ("budget", ("budget", "quota_exhausted", "spend_cap", "cost_cap")),
    ("permission", (
        "permission", "forbidden", "unauthorized", "not_authorized", "not authorized",
        "authorization", "scope_denied", "scopedenied", "scope denied", "cancelled_by_scope",
        "consent", "403", "evaluation_only", "access_denied", "denied",
    )),
    ("missing_profile", (
        "missing_profile", "profile_missing", "no_profile", "profile not found", "kol_not_found",
        "kol not found", "missing_kol", "no_kol_pool", "evidence_missing", "missing_evidence",
        "no_evidence", "target_not_found", "no_ready_video_analysis",
        # kol_profile_deep_crawl:档案 URL 类型不明/不支持(url_unknown_unsupported)= 没有可爬的档案
        "url_unknown", "missing_url", "no_url",
    )),
    ("provider_unavailable", (
        "provider_unavailable", "provider_pressure", "unavailable", "disabled", "llm_json_malformed",
        "provider_candidates", "force_offline", "calls_blocked",  # 预算真话化后新码
    )),
)
_FAILED_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("timeout", (
        "timeout", "timed out", "timedout", "stale_running", "deadline exceeded", "deadline_exceeded",
        "time limit", "active timeout",
    )),
    ("code_error", (
        "code_error", "modulenotfounderror", "importerror", "nameerror", "attributeerror", "typeerror",
        "keyerror", "valueerror", "indexerror", "syntaxerror", "unboundlocalerror", "assertionerror",
        "zerodivisionerror", "traceback (most recent call last)", "no module named", "cannot import name",
        "undefinedcolumn", "undefinedtable", "'nonetype' object", "has no attribute", "is not defined",
    )),
    ("revoked", (
        "revoked", "cancelled", "canceled", "cancel_requested", "superseded", "scope denied",
        "scopedenied", "scope_denied", "withdrawn", "aborted by user", "user_abort",
    )),
    ("provider_error", (
        "provider_error", "provider_pressure", "download", "media_resolve", "content_unavailable",
        "content_restricted", "content_blocked", "permanent", "gemini", "apify", "yt-dlp", "yt_dlp",
        "openai", "anthropic", "claude", "vertex", "429", "500", "502", "503", "504", "5xx",
        "rate limit", "resource_exhausted", "server disconnected", "server error", "ssl",
        "upload failed", "expecting ',' delimiter", "expecting value", "json", "connection reset",
        "connection error", "remote end closed", "precheck", "oembed", "unsupported", "invalid_video_url",
        "not_found", "not found", "404", "unavailable", "http", "proxy", "tunnel",
    )),
)


# ── small helpers ───────────────────────────────────────────────────────


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _stamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_sqlite(conn: Any) -> bool:
    return callable(getattr(conn, "executescript", None))


def _table_available(conn: Any, table_name: str) -> bool:
    try:
        if _is_sqlite(conn):
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (str(table_name),),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_name=? AND table_schema=ANY(current_schemas(FALSE))
                LIMIT 1
                """,
                (str(table_name),),
            ).fetchone()
        return row is not None
    except Exception:
        # Pre-migration local mirrors degrade to an honest empty state.  No
        # fallback creates tables or performs any other write.
        logger.debug("table availability probe failed table=%s", table_name, exc_info=True)
        return False


# ── cursor: keyset (published_at, id) ───────────────────────────────────


def encode_cursor(published_at: Any, evidence_id: int) -> str:
    """Opaque keyset cursor for the row *after* ``(published_at, evidence_id)``."""
    payload = {"k": CURSOR_KIND, "p": _stamp(published_at), "i": int(evidence_id)}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: Any) -> tuple[str | None, int] | None:
    """Return ``(published_at, evidence_id)`` or None for an empty cursor.

    Raises ``ValueError`` for anything that is not the canonical encoding of a
    ``published_at_id`` cursor (wrong kind, offset-era cursors, tampered text).
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("invalid videos cursor") from exc
    if not isinstance(payload, dict) or payload.get("k") != CURSOR_KIND:
        raise ValueError("invalid videos cursor")
    published_at = payload.get("p")
    evidence_id = payload.get("i")
    if published_at is not None and not isinstance(published_at, str):
        raise ValueError("invalid videos cursor")
    if not isinstance(evidence_id, int) or isinstance(evidence_id, bool) or evidence_id <= 0:
        raise ValueError("invalid videos cursor")
    if encode_cursor(published_at, evidence_id) != raw:
        raise ValueError("invalid videos cursor")
    return published_at, int(evidence_id)


# ── durable job projection ──────────────────────────────────────────────


def _job_status(row: dict[str, Any]) -> str:
    raw = str(row.get("status") or "").strip().lower()
    if raw == "queued":
        if _int(row.get("attempts")) > 0 and row.get("next_retry_at") not in (None, ""):
            return "retrying"
        return "queued"
    if raw == "retrying":
        return "retrying"
    if raw in {"running", "processing", "in_progress", "started"}:
        return "running"
    if raw in {"done", "success", "completed", "complete"}:
        return "ready"
    if raw == "blocked":
        return "blocked"
    # triage / cancelled / timeout and unknown terminal values collapse to one
    # safe display state; raw worker text is never returned.
    return "failed"


def classify_reason(
    status: str,
    *,
    raw_status: Any = None,
    error_category: Any = None,
    error_text: Any = None,
) -> str | None:
    """Map one durable job row to the closed ``reason_class`` vocabulary.

    ``status`` is the projected TaskState status; only ``blocked`` / ``failed``
    ever classify.  The raw ledger status (``cancelled`` / ``timeout`` ...), the
    structured ``last_error_category`` and the ``last_error`` text are scanned
    against the rule tables in priority order; nothing from the text is
    returned.  No rule hit → ``None`` (honest unclassified, never a guess).
    """
    if status not in {"blocked", "failed"}:
        return None
    category = str(error_category or "").strip().lower()
    raw = str(raw_status or "").strip().lower()
    text = " ".join(str(error_text or "").replace("\x00", " ").split()).lower()[:4000]
    if status == "failed":
        if raw in {"cancelled", "canceled", "revoked"}:
            return "revoked"
        if raw == "timeout":
            return "timeout"
        rules = _FAILED_RULES
    else:
        rules = _BLOCKED_RULES
    blob = f"{category} {text}".strip()
    if not blob:
        return None
    for reason_class, markers in rules:
        if category and category == reason_class:
            return reason_class
        if any(marker in blob for marker in markers):
            return reason_class
    return None


def failure_fields_for_job(status: str, *, error_category: Any = None, error_text: Any = None) -> dict[str, Any]:
    """O→F failure fields for one projected job (shared rule table, never raw text).

    ``status`` is the projected TaskState status.  ``retrying`` is fed to the
    shared classifier as ``queued`` so a worker-categorised retry explains
    itself ("…会自动重试"); any other non-failure status yields three ``None``.
    ``failure_code`` is kept only when it looks like a machine code — a plain
    text ``last_error`` first line is replaced by the structured category.
    """
    if status not in FAILURE_FIELD_STATUSES:
        return dict(_EMPTY_FAILURE_FIELDS)
    fields = progress_reasons.failure_fields(
        status="queued" if status == "retrying" else status,
        last_error_category=error_category,
        last_error=error_text,
    )
    code = str(fields.get("failure_code") or "").strip()
    if code and not _MACHINE_CODE_RE.match(code):
        category = str(error_category or "").strip()
        code = category if _MACHINE_CODE_RE.match(category) else ""
    return {
        "failure_category": fields.get("failure_category"),
        "failure_reason_human": fields.get("failure_reason_human"),
        "failure_code": code or None,
    }


def _project_job(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    job_id = _int(item.get("id"))
    if job_id <= 0:
        return None
    status = _job_status(item)
    return {
        "job_id": job_id,
        "status": status,
        "requested_at": _stamp(item.get("created_at")),
        "updated_at": _stamp(item.get("updated_at")),
        "reason_class": classify_reason(
            status,
            raw_status=item.get("status"),
            error_category=item.get("last_error_category"),
            error_text=item.get("last_error"),
        ),
        **failure_fields_for_job(
            status,
            error_category=item.get("last_error_category"),
            error_text=item.get("last_error"),
        ),
    }


def _latest_jobs_for_targets(
    conn: Any,
    *,
    job_type: str,
    target_ids: Iterable[int],
    derive_method: str | None = None,
) -> dict[int, dict[str, Any]]:
    ids = list(dict.fromkeys(_int(value) for value in target_ids if _int(value) > 0))[:MAX_PAGE_SIZE]
    if not ids or not _table_available(conn, "apify_jobs"):
        return {}
    placeholders = ",".join("?" for _ in ids)
    if _is_sqlite(conn):
        target_expr = "CAST(json_extract(payload, '$.target_id') AS TEXT)"
        method_expr = "CAST(json_extract(payload, '$.derive_method') AS TEXT)"
    else:
        target_expr = "payload->>'target_id'"
        method_expr = "payload->>'derive_method'"
    method_clause = f" AND {method_expr}=?" if derive_method else ""
    params: tuple[Any, ...] = (
        str(job_type),
        *(str(value) for value in ids),
        *((str(derive_method),) if derive_method else ()),
    )
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT id, status, attempts, next_retry_at, created_at, updated_at,
                   last_error, last_error_category,
                   {target_expr} AS target_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY {target_expr}
                       ORDER BY id DESC
                   ) AS row_num
            FROM apify_jobs
            WHERE job_type=?
              AND {target_expr} IN ({placeholders})
              {method_clause}
        )
        SELECT id, status, attempts, next_retry_at, created_at, updated_at,
               last_error, last_error_category, target_id
        FROM ranked
        WHERE row_num=1
        """,
        params,
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        target_id = _int(item.get("target_id"))
        projected = _project_job(item)
        if target_id > 0 and projected:
            result[target_id] = projected
    return result


# ── TaskState assembly ──────────────────────────────────────────────────


def _data_block(
    *,
    status: str,
    freshness: str,
    updated_at: Any,
    superseded_by_job: bool,
) -> dict[str, Any]:
    return {
        "status": status if status in DATA_STATUSES else "none",
        "freshness": freshness if freshness in FRESHNESS_VALUES else "unavailable",
        "updated_at": _stamp(updated_at),
        "superseded_by_job": bool(superseded_by_job),
    }


def _task_state(job: dict[str, Any] | None, data: dict[str, Any]) -> dict[str, Any]:
    """Merge the latest durable job with persisted-data truth into one TaskState."""
    status = str((job or {}).get("status") or "not_requested")
    if status not in TASK_STATUSES:
        status = "failed"
    reason_class = (job or {}).get("reason_class")
    if status not in {"blocked", "failed"} or reason_class not in REASON_CLASSES:
        reason_class = None
    failure = dict(_EMPTY_FAILURE_FIELDS)
    if status in FAILURE_FIELD_STATUSES:
        category = (job or {}).get("failure_category")
        if category in progress_reasons.FAILURE_CATEGORIES:
            failure["failure_category"] = category
            failure["failure_reason_human"] = str((job or {}).get("failure_reason_human") or "") or None
            failure["failure_code"] = str((job or {}).get("failure_code") or "") or None
    return {
        "status": status,
        "job_id": (job or {}).get("job_id"),
        "requested_at": (job or {}).get("requested_at"),
        "updated_at": (job or {}).get("updated_at"),
        "reason_class": reason_class,
        **failure,
        "data": data,
    }


def _job_is_newer(job: dict[str, Any] | None, data_updated_at: Any) -> bool:
    if not job or job.get("status") not in ACTIVE_TASK_STATUSES:
        return False
    job_at = _moment(job.get("requested_at") or job.get("updated_at"))
    data_at = _moment(data_updated_at)
    return data_at is None or job_at is None or job_at > data_at


def _final_v1_caches(conn: Any, evidence_ids: list[int], derive_method: str = FINAL_V1_DERIVE_METHOD) -> dict[int, dict[str, Any]]:
    if not evidence_ids or not _table_available(conn, "vkpi_analysis_cache"):
        return {}
    return analysis_caches_for_evidence(conn, evidence_ids, derive_method=derive_method)
def final_v1_task_state(cache: dict[str, Any] | None, job: dict[str, Any] | None) -> dict[str, Any]:
    """Video-analysis job ledger vs. its analysis cache row.

    * active job newer than the cache  -> status = job state, data.superseded_by_job = true
    * finished job but no ready cache  -> status = failed (the promise was not kept)
    * ready cache without any job row  -> status = ready (legacy / pruned ledger)
    """
    cache_status = str((cache or {}).get("status") or "").strip().lower()
    cache_at = (cache or {}).get("updated_at")
    if cache_status == "ready":
        data_status, freshness = "ready", "fresh"
    elif cache_status == "legacy_unverified":
        data_status, freshness = "legacy_unverified", "stale"
    elif cache_status == "stale":
        data_status, freshness = "stale", "stale"
    else:
        data_status, freshness = "none", "never"
    data = _data_block(
        status=data_status,
        freshness=freshness,
        updated_at=cache_at,
        superseded_by_job=_job_is_newer(job, cache_at),
    )
    state = _task_state(job, data)
    if cache_status == "legacy_unverified":
        truth = {
            "cache_reuse_status": "legacy_unverified",
            "revalidation_required": True,
            "claim_status": "descriptive_only",
        }
        data.update(truth)
        state.update(truth)
        if state["status"] in {"ready", "not_requested"}:
            state.update(status="legacy_unverified", terminal=True, reason_class=None, **_EMPTY_FAILURE_FIELDS)
    elif state["status"] == "ready" and data["status"] != "ready":
        state["status"] = "failed"
    elif state["status"] == "not_requested" and data["status"] == "ready":
        state["status"] = "ready"
    return state


def metric_refresh_task_state(video: dict[str, Any], job: dict[str, Any] | None) -> dict[str, Any]:
    """Metric refresh: job ledger vs. persisted metric snapshot (tracking layer)."""
    last_success = video.get("last_success") if isinstance(video.get("last_success"), dict) else {}
    freshness = str(video.get("freshness") or "unavailable").strip().lower()
    if freshness not in FRESHNESS_VALUES:
        freshness = "unavailable"
    snapshot_at = last_success.get("fetched_at") or video.get("metrics_scraped_at")
    data_status = "ready" if freshness == "fresh" else "stale" if freshness == "stale" else "none"
    data = _data_block(
        status=data_status,
        freshness=freshness,
        updated_at=snapshot_at,
        superseded_by_job=_job_is_newer(job, snapshot_at),
    )
    data["tracking_status"] = str(video.get("tracking_status") or "unavailable")
    data["sample_count"] = max(0, _int(video.get("sample_count")))
    data["attempt_count"] = max(0, _int(video.get("attempt_count")))
    return _task_state(job, data)


def _profile_crawl_data(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    if not _table_available(conn, "vkpi_kol_url_deep_crawl_runs"):
        return _data_block(status="none", freshness="unavailable", updated_at=None, superseded_by_job=False)
    row = conn.execute(
        """
        SELECT created_at
        FROM vkpi_kol_url_deep_crawl_runs
        WHERE kol_pool_id=? AND status='ready'
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(kol_pool_id),),
    ).fetchone()
    crawled_at = dict(row).get("created_at") if row else None
    if not crawled_at:
        return _data_block(status="none", freshness="never", updated_at=None, superseded_by_job=False)
    fresh_cutoff = datetime.now(timezone.utc) - timedelta(hours=PROFILE_FRESH_HOURS)
    at = _moment(crawled_at)
    freshness = "fresh" if at is not None and at >= fresh_cutoff else "stale"
    return _data_block(
        status="ready" if freshness == "fresh" else "stale",
        freshness=freshness,
        updated_at=crawled_at,
        superseded_by_job=False,
    )


def profile_crawl_task_state(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    """Account crawl: job ledger vs. latest ready deep-crawl run (24h freshness)."""
    job = _latest_jobs_for_targets(conn, job_type=PROFILE_JOB_TYPE, target_ids=(int(kol_pool_id),)).get(
        int(kol_pool_id)
    )
    data = _profile_crawl_data(conn, int(kol_pool_id))
    data["superseded_by_job"] = _job_is_newer(job, data.get("updated_at"))
    return _task_state(job, data)


# ── summary + page ──────────────────────────────────────────────────────


def _library_summary(conn: Any, kol_pool_id: int) -> dict[str, int]:
    if not _table_available(conn, "vkpi_kol_video_evidence"):
        return {"total": 0, "views_total": 0, "views_measured": 0, "final_v1_ready": 0, "legacy_unverified": 0}
    cache_available = _table_available(conn, "vkpi_analysis_cache")
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN e.view_count IS NOT NULL THEN 1 ELSE 0 END), 0) AS views_measured,
               COALESCE(SUM(e.view_count), 0) AS views_total
        FROM vkpi_kol_video_evidence e
        WHERE e.kol_pool_id=?
          AND COALESCE(e.is_active, TRUE) != FALSE
          AND COALESCE(e.evidence_type, 'video') IN ('video', 'image')
        """,
        (int(kol_pool_id),),
    ).fetchone()
    item = dict(row) if row else {}
    cache_summary = analysis_cache_summary_for_kol(conn, int(kol_pool_id)) if cache_available else {}
    return {
        "total": max(0, _int(item.get("total"))),
        "views_total": max(0, _int(item.get("views_total"))),
        "views_measured": max(0, _int(item.get("views_measured"))),
        "final_v1_ready": max(0, _int(cache_summary.get("final_v1_ready"))),
        "legacy_unverified": max(0, _int(cache_summary.get("legacy_unverified"))),
    }


def _metric_trends_for_rows(conn: Any, evidence_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Tracking-layer truth for rows that did not come through pool_detail."""
    if not evidence_ids:
        return {}
    try:
        from app.domains import content_metric_snapshots

        return content_metric_snapshots.metric_trends_for_evidence(conn, evidence_ids) or {}
    except Exception:
        # Tracking layer missing/renamed on a narrow mirror: metric_refresh data
        # degrades to "unavailable" honestly instead of failing the page.
        logger.warning("metric trends unavailable for task-state projection", exc_info=True)
        return {}


def attach_task_states(
    conn: Any,
    rows: list[dict[str, Any]],
    *,
    fetch_metric_trends: bool = False,
    fetch_modalities: bool = True,
) -> list[dict[str, Any]]:
    """Attach ``tasks`` + ``viltrox_modalities`` to evidence rows (shared projection).

    Single implementation behind ``/my-kol/{id}/videos`` and the board-ext
    ``recent_videos`` wall: the same evidence id yields identical TaskStates on
    both surfaces.  ``rows`` are mutated in place and returned; each gains
    ``evidence_id`` (int), ``published_at`` (iso | null), ``viltrox_modalities``
    and ``tasks``.  ``fetch_metric_trends=True`` pulls the tracking snapshot
    summary (freshness / last_success / counts) for rows that do not already
    carry it (pool_detail rows do); it is read only for the TaskState and never
    copied onto the public row.  ``fetch_modalities=False`` keeps a caller's
    own ``viltrox_modalities`` projection (board-ext reads it inside its CTE)
    instead of batch-reading the analysis cache again; both paths normalise
    through ``video_evidence_projection.viltrox_modalities``.
    """
    evidence_ids = list(
        dict.fromkeys(
            _int(video.get("evidence_id") or video.get("id"))
            for video in rows
            if _int(video.get("evidence_id") or video.get("id")) > 0
        )
    )
    caches = _final_v1_caches(conn, evidence_ids)
    qa_caches = valid_qa_caches(caches, _final_v1_caches(conn, evidence_ids, KEYFRAME_QA_DERIVE_METHOD))
    final_jobs = _latest_jobs_for_targets(
        conn, job_type=VIDEO_JOB_TYPE, target_ids=evidence_ids, derive_method=FINAL_V1_DERIVE_METHOD
    )
    qa_jobs = _latest_jobs_for_targets(conn, job_type=VIDEO_JOB_TYPE, target_ids=evidence_ids,
                                       derive_method=KEYFRAME_QA_DERIVE_METHOD)
    metric_jobs = _latest_jobs_for_targets(conn, job_type=METRIC_JOB_TYPE, target_ids=evidence_ids)
    modalities = final_v1_modalities_for_evidence(conn, evidence_ids) if fetch_modalities else {}
    trends = _metric_trends_for_rows(conn, evidence_ids) if fetch_metric_trends else {}
    for video in rows:
        evidence_id = _int(video.get("evidence_id") or video.get("id"))
        cache = caches.get(evidence_id) or {}
        cache_reuse_status = str(cache.get("cache_reuse_status") or "")
        legacy_unverified = cache_reuse_status == "legacy_unverified"
        video["evidence_id"] = evidence_id
        video["published_at"] = _stamp(video.get("published_at"))
        video["has_final_v1_cache"] = cache.get("status") == "ready"
        if legacy_unverified:
            video.update(
                has_final_v1_raw_cache=True,
                analysis_cache_reuse_status="legacy_unverified",
                revalidation_required=True,
                claim_status="descriptive_only",
                v_tier="cooperation" if video.get("project_id") else "undetermined",
                llm_viltrox_status=None, llm_viltrox_detected=None,
                llm_viltrox_products=[], llm_competitor_mentions=[],
            )
        elif cache_reuse_status:
            video["analysis_cache_reuse_status"] = cache_reuse_status
        if legacy_unverified:
            video["viltrox_modalities"] = []
        elif fetch_modalities:
            video["viltrox_modalities"] = list(modalities.get(evidence_id) or [])
        else:
            video["viltrox_modalities"] = viltrox_modalities(video.get("viltrox_modalities"))
        tracking = {**video, **(trends.get(evidence_id) or {})} if fetch_metric_trends else video
        video["tasks"] = {
            "metric_refresh": metric_refresh_task_state(tracking, metric_jobs.get(evidence_id)),
            "final_v1": final_v1_task_state(cache, final_jobs.get(evidence_id)),
            "keyframe_qa": final_v1_task_state(qa_caches.get(evidence_id), qa_jobs.get(evidence_id)),
        }
    return rows


def build_video_recovery_page(
    conn: Any,
    *,
    kol_pool_id: int,
    videos: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    """Attach unified TaskState truth to one keyset page of evidence rows.

    ``videos`` must already be ordered ``published_at DESC, id DESC`` and may
    contain up to ``limit + 1`` rows; the extra row only signals ``has_more``.
    """
    page_limit = max(1, min(MAX_PAGE_SIZE, int(limit or 1)))
    rows = [dict(video) for video in videos[: page_limit + 1]]
    has_more = len(rows) > page_limit
    items = attach_task_states(conn, rows[:page_limit])

    summary = _library_summary(conn, int(kol_pool_id))
    last = items[-1] if items else None
    next_cursor = encode_cursor(last.get("published_at"), last["evidence_id"]) if has_more and last else None
    page = {
        "limit": page_limit,
        "returned": len(items),
        "has_more": bool(has_more),
        "next_cursor": next_cursor,
        "cursor_kind": CURSOR_KIND,
        "order": ORDER,
    }
    return {
        "contract": CONTRACT,
        "kol_pool_id": int(kol_pool_id),
        "read_only": True,
        "profile_crawl": profile_crawl_task_state(conn, int(kol_pool_id)),
        "items": items,
        "summary": summary,
        "page": page,
        "total": summary["total"],
        "returned": page["returned"],
        "has_more": page["has_more"],
        "next_cursor": page["next_cursor"],
    }


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "CONTRACT",
    "CURSOR_KIND",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "BLOCKED_REASON_CLASSES",
    "FAILED_REASON_CLASSES",
    "FAILURE_FIELD_KEYS",
    "FAILURE_FIELD_STATUSES",
    "KEYFRAME_QA_DERIVE_METHOD",
    "REASON_CLASSES",
    "TASK_STATUSES",
    "attach_task_states",
    "build_video_recovery_page",
    "classify_reason",
    "decode_cursor",
    "encode_cursor",
    "failure_fields_for_job",
    "final_v1_task_state",
    "metric_refresh_task_state",
    "profile_crawl_task_state",
]
