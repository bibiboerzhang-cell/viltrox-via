"""Append-only content metric observations.

The evidence table is a latest-value read model.  Callers own the transaction:
``record_successful_refresh`` updates that read model and appends its observation
without committing, while ``record_failed_refresh`` appends only a failure row.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


VALID_STATUSES = frozenset({"success", "failed", "legacy_current_only"})
TRUTH_STATUSES = frozenset({"success", "legacy_current_only"})
TREND_STATUS = "success"
MAX_TREND_EVIDENCE = 200
MAX_SNAPSHOTS_PER_EVIDENCE = 80
FRESH_FOR_HOURS = 24
TREND_BASELINE_MAX_AGE_HOURS = {
    24: 36,
    24 * 7: 24 * 7 + 24 * 3.5,
}


def metric_or_none(value: Any) -> int | None:
    """Normalize a provider counter without turning unknown into zero."""

    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def error_code_from_exception(exc: BaseException) -> str:
    """Return a bounded, non-secret error class/code for the ledger."""

    name = re.sub(r"[^a-z0-9_]+", "_", type(exc).__name__.lower()).strip("_")
    return (name or "refresh_error")[:80]


def quality_flags_for_metrics(
    *,
    views: Any,
    likes: Any,
    comments: Any,
    shares: Any,
    source_observed_at: str | None,
    extra: Iterable[str] = (),
) -> list[str]:
    metrics = [metric_or_none(value) for value in (views, likes, comments, shares)]
    flags = {str(flag).strip()[:80] for flag in extra if str(flag).strip()}
    if all(value is None for value in metrics):
        flags.add("all_metrics_missing")
    elif any(value is None for value in metrics):
        flags.add("partial_metrics")
    if not str(source_observed_at or "").strip():
        flags.add("source_observed_at_missing")
    return sorted(flags)


def has_any_metric(*, views: Any, likes: Any, comments: Any, shares: Any) -> bool:
    return any(metric_or_none(value) is not None for value in (views, likes, comments, shares))


def make_capture_key(
    *,
    evidence_id: int,
    provider: str,
    status: str,
    fetched_at: str,
    source_observed_at: str | None = None,
    run_id: str | None = None,
) -> str:
    """Build a deterministic idempotency key for one provider observation.

    A provider run id is preferred.  Timestamp identity is the safe fallback
    for APIs that do not expose a run id; callers must reuse the same timestamp
    when retrying the same captured response.
    """

    identity = (
        str(run_id or "").strip()
        or str(source_observed_at or "").strip()
        or str(fetched_at).strip()
    )
    raw = "|".join(
        (
            "content_metric_v1",
            str(int(evidence_id)),
            str(provider or "unknown").strip().lower(),
            str(status).strip().lower(),
            identity,
        )
    )
    return "cms:v1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_sqlite_connection(conn: Any) -> bool:
    return callable(getattr(conn, "executescript", None))


def _execute(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    execute = getattr(conn, "execute", None)
    if callable(execute):
        return execute(sql, params)
    cursor = conn.cursor()
    cursor.execute(sql.replace("?", "%s"), params)
    return cursor


def _column_name(value: Any) -> str:
    return str(getattr(value, "name", value[0] if isinstance(value, (tuple, list)) and value else value))


def _row_as_dict(cursor: Any, row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    keys = getattr(row, "keys", None)
    if callable(keys):
        return {str(key): row[key] for key in keys()}
    columns = [_column_name(column) for column in (getattr(cursor, "description", None) or [])]
    if not columns:
        raise TypeError("database cursor returned tuple rows without column metadata")
    return dict(zip(columns, row, strict=False))


def _rows_as_dicts(cursor: Any) -> list[dict[str, Any]]:
    return [_row_as_dict(cursor, row) for row in cursor.fetchall()]


def ensure_sqlite_schema(conn: Any) -> None:
    """Create the local SQLite mirror; Postgres is migration-owned."""

    if not _is_sqlite_connection(conn):
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_content_metric_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_id INTEGER NOT NULL,
            capture_key TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL DEFAULT '',
            source_observed_at TEXT,
            fetched_at TEXT NOT NULL,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'legacy_current_only')),
            error_code TEXT,
            run_id TEXT,
            quality_flags TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(evidence_id) REFERENCES vkpi_kol_video_evidence(id) ON DELETE CASCADE,
            CHECK (views IS NULL OR views >= 0),
            CHECK (likes IS NULL OR likes >= 0),
            CHECK (comments IS NULL OR comments >= 0),
            CHECK (shares IS NULL OR shares >= 0)
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_content_metric_snapshots_evidence_time
            ON vkpi_content_metric_snapshots(evidence_id, fetched_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_content_metric_snapshots_status_time
            ON vkpi_content_metric_snapshots(status, fetched_at DESC);
        """
    )


def _evidence_columns(conn: Any) -> set[str]:
    if _is_sqlite_connection(conn):
        rows = _execute(conn, "PRAGMA table_info(vkpi_kol_video_evidence)").fetchall()
        return {str(row[1]) for row in rows}
    cursor = _execute(
        conn,
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = ?
        """,
        ("vkpi_kol_video_evidence",),
    )
    return {str(row[0]) for row in cursor.fetchall()}


def _lock_evidence_latest(conn: Any, evidence_id: int) -> None:
    if not _is_sqlite_connection(conn):
        # Do not use SELECT ... FOR UPDATE after the snapshot INSERT: its FK
        # key-share lock can deadlock with another writer doing the same order.
        # A transaction-scoped advisory lock serializes only this read model.
        _execute(
            conn,
            """
            SELECT pg_advisory_xact_lock(
                hashtextextended(
                    'vkpi_content_metric_latest:' || CAST(? AS TEXT),
                    0
                )
            )
            """,
            (int(evidence_id),),
        ).fetchone()
    cursor = _execute(
        conn,
        "SELECT id FROM vkpi_kol_video_evidence WHERE id=?",
        (int(evidence_id),),
    )
    if cursor.fetchone() is None:
        raise LookupError("video evidence not found for metric refresh")


def _latest_canonical_capture_key(conn: Any, evidence_id: int) -> str | None:
    """Return the deterministic winner for the evidence latest-value model.

    A provider observation timestamp wins over its fetch timestamp.  Legacy
    current-only rows participate only when they have a real metric observation
    timestamp; an unknown legacy timestamp must never outrank a new capture.
    Equal observations prefer a real success, then later fetch, then capture key.
    """

    cursor = _execute(
        conn,
        """
        SELECT capture_key
        FROM vkpi_content_metric_snapshots
        WHERE evidence_id=?
          AND (
              status='success'
              OR (status='legacy_current_only' AND source_observed_at IS NOT NULL)
          )
        ORDER BY
            CASE
                WHEN status='legacy_current_only' THEN source_observed_at
                ELSE COALESCE(source_observed_at, fetched_at)
            END DESC,
            CASE WHEN status='success' THEN 1 ELSE 0 END DESC,
            fetched_at DESC,
            capture_key DESC
        LIMIT 1
        """,
        (int(evidence_id),),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    mapped = _row_as_dict(cursor, row)
    return str(mapped.get("capture_key") or "") or None


def append_snapshot(
    conn: Any,
    *,
    evidence_id: int,
    provider: str,
    fetched_at: str,
    status: str,
    source_observed_at: str | None = None,
    views: Any = None,
    likes: Any = None,
    comments: Any = None,
    shares: Any = None,
    error_code: str | None = None,
    run_id: str | None = None,
    quality_flags: Iterable[str] = (),
    capture_key: str | None = None,
) -> dict[str, Any]:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in VALID_STATUSES:
        raise ValueError(f"unsupported metric snapshot status: {status}")
    normalized_metrics = {
        "views": metric_or_none(views),
        "likes": metric_or_none(likes),
        "comments": metric_or_none(comments),
        "shares": metric_or_none(shares),
    }
    flags = quality_flags_for_metrics(
        **normalized_metrics,
        source_observed_at=source_observed_at,
        extra=quality_flags,
    )
    key = str(capture_key or "").strip() or make_capture_key(
        evidence_id=int(evidence_id),
        provider=provider,
        status=normalized_status,
        fetched_at=fetched_at,
        source_observed_at=source_observed_at,
        run_id=run_id,
    )
    provider_value = str(provider or "unknown").strip().lower()[:120] or "unknown"
    error_value = str(error_code or "")[:120] or None
    run_value = str(run_id or "")[:240] or None
    flags_json = json.dumps(flags, ensure_ascii=False, separators=(",", ":"))
    expected = {
        "evidence_id": int(evidence_id),
        "capture_key": key[:240],
        "provider": provider_value,
        "source_observed_at": source_observed_at or None,
        "fetched_at": str(fetched_at),
        **normalized_metrics,
        "status": normalized_status,
        "error_code": error_value,
        "run_id": run_value,
        "quality_flags": flags,
    }
    cursor = _execute(
        conn,
        """
        INSERT INTO vkpi_content_metric_snapshots (
            evidence_id, capture_key, provider, source_observed_at, fetched_at,
            views, likes, comments, shares, status, error_code, run_id, quality_flags
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (capture_key) DO NOTHING
        """,
        (
            int(evidence_id),
            key[:240],
            provider_value,
            source_observed_at or None,
            str(fetched_at),
            normalized_metrics["views"],
            normalized_metrics["likes"],
            normalized_metrics["comments"],
            normalized_metrics["shares"],
            normalized_status,
            error_value,
            run_value,
            flags_json,
        ),
    )
    inserted = int(getattr(cursor, "rowcount", 0) or 0) > 0
    select_cursor = _execute(
        conn,
        """
        SELECT
            id, evidence_id, capture_key, provider, source_observed_at, fetched_at,
            views, likes, comments, shares, status, error_code, run_id, quality_flags
        FROM vkpi_content_metric_snapshots
        WHERE capture_key=?
        """,
        (key[:240],),
    )
    canonical = _row_as_dict(select_cursor, select_cursor.fetchone())
    if not canonical:
        raise RuntimeError("metric snapshot insert/select lost canonical row")

    def same_timestamp(left: Any, right: Any) -> bool:
        # Postgres compat read-back (_normalize_pg_value) renders TIMESTAMPTZ at
        # whole-second precision while callers such as the metric refresh worker
        # pass microsecond ISO strings.  The capture_key already pins the exact
        # fetched_at, so the conflict check compares at second precision; otherwise
        # every real PG write raised "payload conflict: fetched_at" and the job
        # was sent to triage as a code_error.
        left_parsed = _parse_timestamp(left)
        right_parsed = _parse_timestamp(right)
        if left_parsed is not None and right_parsed is not None:
            return left_parsed.replace(microsecond=0) == right_parsed.replace(microsecond=0)
        return str(left or "") == str(right or "")

    mismatches: list[str] = []
    for field in (
        "evidence_id",
        "capture_key",
        "provider",
        "views",
        "likes",
        "comments",
        "shares",
        "status",
        "error_code",
        "run_id",
    ):
        if canonical.get(field) != expected.get(field):
            mismatches.append(field)
    for field in ("source_observed_at", "fetched_at"):
        if not same_timestamp(canonical.get(field), expected.get(field)):
            mismatches.append(field)
    if _json_flags(canonical.get("quality_flags")) != expected["quality_flags"]:
        mismatches.append("quality_flags")
    if mismatches:
        raise ValueError(
            "metric snapshot capture_key payload conflict: " + ",".join(sorted(set(mismatches)))
        )
    canonical["quality_flags"] = expected["quality_flags"]
    return {"inserted": inserted, "snapshot": canonical}


def record_successful_refresh(
    conn: Any,
    *,
    evidence_id: int,
    provider: str,
    fetched_at: str,
    source_observed_at: str | None = None,
    views: Any = None,
    likes: Any = None,
    comments: Any = None,
    shares: Any = None,
    run_id: str | None = None,
    quality_flags: Iterable[str] = (),
    capture_key: str | None = None,
) -> dict[str, Any]:
    """Append canonical truth, then update latest only for a new row.

    A savepoint keeps snapshot and latest atomic even when a caller catches the
    exception before performing its outer transaction rollback.
    """

    metrics = {
        "views": metric_or_none(views),
        "likes": metric_or_none(likes),
        "comments": metric_or_none(comments),
        "shares": metric_or_none(shares),
    }
    if not has_any_metric(**metrics):
        raise ValueError("successful metric refresh requires at least one observed metric")
    savepoint = "vkpi_content_metric_refresh"
    _execute(conn, f"SAVEPOINT {savepoint}")
    try:
        result = append_snapshot(
            conn,
            evidence_id=int(evidence_id),
            provider=provider,
            fetched_at=fetched_at,
            source_observed_at=source_observed_at,
            views=metrics["views"],
            likes=metrics["likes"],
            comments=metrics["comments"],
            shares=metrics["shares"],
            status="success",
            run_id=run_id,
            quality_flags=quality_flags,
            capture_key=capture_key,
        )
        result["latest_updated"] = False
        if result["inserted"]:
            # Serialize writers for this evidence before choosing the winner.
            # At Postgres' default READ COMMITTED isolation, a waiter sees the
            # winner committed by the previous lock holder before this query.
            _lock_evidence_latest(conn, int(evidence_id))
            winning_key = _latest_canonical_capture_key(conn, int(evidence_id))
            if winning_key == result["snapshot"]["capture_key"]:
                columns = _evidence_columns(conn)
                assignments = [
                    ("view_count", metrics["views"]),
                    ("like_count", metrics["likes"]),
                    ("comment_count", metrics["comments"]),
                    ("share_count", metrics["shares"]),
                ]
                if "metrics_scraped_at" in columns:
                    assignments.append(("metrics_scraped_at", fetched_at))
                if "metrics_source" in columns:
                    assignments.append(
                        ("metrics_source", str(provider or "unknown").strip().lower()[:120])
                    )
                if "updated_at" in columns:
                    assignments.append(("updated_at", fetched_at))
                set_sql = ", ".join(f"{column}=?" for column, _value in assignments)
                cursor = _execute(
                    conn,
                    f"UPDATE vkpi_kol_video_evidence SET {set_sql} WHERE id=?",
                    tuple(value for _column, value in assignments) + (int(evidence_id),),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                    raise LookupError("video evidence not found for metric refresh")
                result["latest_updated"] = True
    except Exception:
        _execute(conn, f"ROLLBACK TO SAVEPOINT {savepoint}")
        _execute(conn, f"RELEASE SAVEPOINT {savepoint}")
        raise
    _execute(conn, f"RELEASE SAVEPOINT {savepoint}")
    return result


def record_failed_refresh(
    conn: Any,
    *,
    evidence_id: int,
    provider: str,
    fetched_at: str,
    error_code: str,
    source_observed_at: str | None = None,
    run_id: str | None = None,
    quality_flags: Iterable[str] = (),
    capture_key: str | None = None,
) -> dict[str, Any]:
    """Append failure truth only; latest evidence metrics are untouched."""

    return append_snapshot(
        conn,
        evidence_id=int(evidence_id),
        provider=provider,
        fetched_at=fetched_at,
        source_observed_at=source_observed_at,
        status="failed",
        error_code=error_code,
        run_id=run_id,
        quality_flags=("refresh_failed", *tuple(quality_flags)),
        capture_key=capture_key,
    )


def _parse_timestamp(value: Any) -> datetime | None:
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


def _json_flags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(flag) for flag in value if str(flag)]
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(flag) for flag in parsed if str(flag)] if isinstance(parsed, list) else []


def _attempt_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    status = str(row.get("status") or "")
    return {
        "status": status,
        # A legacy row's required storage timestamp can be an epoch sentinel;
        # it is not a provider refresh receipt and must not reach UI copy.
        "fetched_at": None if status == "legacy_current_only" else row.get("fetched_at"),
        "views": metric_or_none(row.get("views")),
        "likes": metric_or_none(row.get("likes")),
        "comments": metric_or_none(row.get("comments")),
        "shares": metric_or_none(row.get("shares")),
    }


def unavailable_tracking() -> dict[str, Any]:
    return {
        "last_attempt": None,
        "last_success": None,
        "sample_count": 0,
        "attempt_count": 0,
        "views_delta_24h": None,
        "views_delta_7d": None,
        "delta_24h_status": "insufficient_history",
        "delta_7d_status": "insufficient_history",
        "freshness": "unavailable",
        "tracking_status": "unavailable",
        "history_capped": False,
    }


def _table_available(conn: Any) -> bool:
    try:
        if _is_sqlite_connection(conn):
            row = _execute(
                conn,
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                ("vkpi_content_metric_snapshots",),
            ).fetchone()
        else:
            row = _execute(
                conn,
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_name=?
                  AND table_schema=ANY(current_schemas(FALSE))
                LIMIT 1
                """,
                ("vkpi_content_metric_snapshots",),
            ).fetchone()
        return row is not None
    except Exception:
        # Compatibility for a pre-migration database or narrow test double.
        return False


def _baseline_for(
    successful: list[dict[str, Any]],
    *,
    latest_at: datetime,
    hours: int,
) -> dict[str, Any] | None:
    target = latest_at - timedelta(hours=hours)
    # A baseline must be old enough to cover the named window, but not so old
    # that a weekly/monthly observation is mislabeled as a 24h delta.  The
    # bounded tolerance absorbs normal scheduler jitter while failing closed
    # after a missed cadence.  Callers then expose ``insufficient_history``
    # instead of a numerically real but semantically false fixed-window trend.
    max_age_hours = TREND_BASELINE_MAX_AGE_HOURS.get(hours, hours * 1.5)
    oldest = latest_at - timedelta(hours=max_age_hours)
    candidates: list[dict[str, Any]] = []
    for row in successful:
        fetched_at = _parse_timestamp(row.get("fetched_at"))
        if (
            metric_or_none(row.get("views")) is not None
            and fetched_at is not None
            and oldest <= fetched_at <= target
        ):
            candidates.append(row)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda row: _parse_timestamp(row.get("fetched_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
    )


def _trend_for_rows(rows: list[dict[str, Any]], *, now: datetime) -> dict[str, Any]:
    if not rows:
        result = unavailable_tracking()
        result.update({"freshness": "never", "tracking_status": "insufficient_history"})
        return result
    ordered = sorted(
        rows,
        key=lambda row: (
            _parse_timestamp(row.get("fetched_at")) or datetime.min.replace(tzinfo=timezone.utc),
            int(row.get("id") or 0),
        ),
        reverse=True,
    )
    truthful = [row for row in ordered if str(row.get("status") or "") in TRUTH_STATUSES]
    successful = [row for row in ordered if str(row.get("status") or "") == TREND_STATUS]
    last_attempt = ordered[0]
    last_success = truthful[0] if truthful else None
    latest_trend = successful[0] if successful else None
    latest_at = _parse_timestamp(latest_trend.get("fetched_at")) if latest_trend else None
    latest_views = metric_or_none(latest_trend.get("views")) if latest_trend else None

    def delta(hours: int) -> tuple[int | None, str]:
        if latest_at is None or latest_views is None:
            return None, "insufficient_history"
        baseline = _baseline_for(successful, latest_at=latest_at, hours=hours)
        baseline_views = metric_or_none(baseline.get("views")) if baseline else None
        if baseline_views is None:
            return None, "insufficient_history"
        return latest_views - baseline_views, "ready"

    delta_24h, delta_24h_status = delta(24)
    delta_7d, delta_7d_status = delta(24 * 7)
    truth_at = _parse_timestamp(last_success.get("fetched_at")) if last_success else None
    if str(last_success.get("status") or "") == "legacy_current_only":
        freshness = "unavailable"
    elif truth_at is None:
        freshness = "never"
    else:
        age = max(timedelta(0), now - truth_at)
        freshness = "fresh" if age <= timedelta(hours=FRESH_FOR_HOURS) else "stale"
    if str(last_attempt.get("status") or "") == "failed":
        tracking_status = "failed"
    elif freshness == "stale":
        tracking_status = "stale"
    elif freshness == "unavailable":
        tracking_status = "insufficient_history"
    elif delta_24h_status == "insufficient_history" and delta_7d_status == "insufficient_history":
        tracking_status = "insufficient_history"
    else:
        tracking_status = "tracked"
    attempt_count = int(ordered[0].get("attempt_count") or len(ordered))
    sample_count = int(ordered[0].get("sample_count") or len(successful))
    return {
        "last_attempt": _attempt_payload(last_attempt),
        "last_success": _attempt_payload(last_success),
        "sample_count": sample_count,
        "attempt_count": attempt_count,
        "views_delta_24h": delta_24h,
        "views_delta_7d": delta_7d,
        "delta_24h_status": delta_24h_status,
        "delta_7d_status": delta_7d_status,
        "freshness": freshness,
        "tracking_status": tracking_status,
        "history_capped": attempt_count > MAX_SNAPSHOTS_PER_EVIDENCE,
    }


def metric_trends_for_evidence(
    conn: Any,
    evidence_ids: Iterable[int],
    *,
    now: datetime | None = None,
) -> dict[int, dict[str, Any]]:
    """Read bounded histories for up to 200 evidence ids in one data query."""

    ids: list[int] = []
    for value in evidence_ids:
        try:
            evidence_id = int(value)
        except (TypeError, ValueError):
            continue
        if evidence_id > 0 and evidence_id not in ids:
            ids.append(evidence_id)
        if len(ids) >= MAX_TREND_EVIDENCE:
            break
    if not ids:
        return {}
    unavailable = {evidence_id: unavailable_tracking() for evidence_id in ids}
    if not _table_available(conn):
        return unavailable
    placeholders = ",".join("?" for _ in ids)
    try:
        cursor = _execute(
            conn,
            f"""
            WITH ranked AS (
                SELECT
                    id, evidence_id, fetched_at,
                    views, likes, comments, shares, status,
                    ROW_NUMBER() OVER (
                        PARTITION BY evidence_id ORDER BY fetched_at DESC, id DESC
                    ) AS row_num,
                    COUNT(*) OVER (PARTITION BY evidence_id) AS attempt_count,
                    SUM(CASE WHEN status='success' THEN 1 ELSE 0 END)
                        OVER (PARTITION BY evidence_id) AS sample_count
                FROM vkpi_content_metric_snapshots
                WHERE evidence_id IN ({placeholders})
            )
            SELECT *
            FROM ranked
            WHERE row_num <= ?
            ORDER BY evidence_id, fetched_at DESC, id DESC
            """,
            tuple(ids) + (MAX_SNAPSHOTS_PER_EVIDENCE,),
        )
        rows = _rows_as_dicts(cursor)
    except Exception:
        return unavailable
    grouped: dict[int, list[dict[str, Any]]] = {evidence_id: [] for evidence_id in ids}
    for row in rows:
        evidence_id = int(row.get("evidence_id") or 0)
        if evidence_id in grouped:
            grouped[evidence_id].append(row)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return {
        evidence_id: _trend_for_rows(grouped[evidence_id], now=current.astimezone(timezone.utc))
        for evidence_id in ids
    }
