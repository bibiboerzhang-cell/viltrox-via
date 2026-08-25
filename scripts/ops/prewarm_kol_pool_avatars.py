#!/usr/bin/env python3
"""Bounded, explicit Pool/session KOL avatar cache prewarm.

The command is deliberately narrow:

* exactly one of ``--pool-id`` or ``--session-id`` is required;
* Pool IDs are capped at 50; session IDs at 5 and session URLs at 50;
* the database is queried only for those explicit IDs and is never written;
* dry-run is the default; only ``--execute`` may call ``cache_image``;
* only a live, allowlisted, external profile-avatar URL is eligible; stable
  external URLs are cached too so they can become a genuinely local durable
  projection rather than being relabelled as durable in place;
* release validation fails closed before any execute-side cache mutation;
* Pool mode emits only ``pool_id``, ``status`` and ``reason``; session mode
  emits aggregate counts only. Source URLs, names, handles, provider payloads,
  cache URLs and secrets are never rendered.

This does not call Apify, an LLM, YouTube APIs, or any discovery provider.
``cache_image`` performs the single reviewed CDN fetch for eligible rows and
writes only the existing local media cache.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(1, str(SCRIPTS))

from stdout_utils import out_json  # noqa: E402

from app.core.release_validation import release_validation_active  # noqa: E402
from app.domains.kol.pool_read_projection import project_pool_avatar  # noqa: E402
from app.domains.media.cache import cache_image  # noqa: E402
from app.domains.media.cache_core import _normalize_image_url  # noqa: E402


MAX_POOL_IDS = 50
MAX_SESSION_IDS = 5
MAX_SESSION_AVATAR_URLS = 50
MAX_SESSION_CREATOR_ITEMS = 500
SELECT_EXPLICIT_POOL_ROWS_SQL = """
SELECT id, avatar_url, raw_platform_data
FROM vkpi_kol_pool
WHERE id IN ({placeholders})
ORDER BY id
"""
SELECT_EXPLICIT_SESSION_ITEMS_SQL = """
SELECT session_id, item_type, payload_json
FROM vkpi_kol_search_session_items
WHERE session_id IN ({placeholders})
  AND item_type IN ('existing_kol', 'new_creator', 'online_qualified_candidate', 'recall_candidate')
ORDER BY session_id, id
LIMIT ?
"""


def _positive_pool_id(value: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("pool id must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("pool id must be a positive integer")
    return parsed


def _positive_session_id(value: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("session id must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("session id must be a positive integer")
    return parsed


def normalize_pool_ids(values: Iterable[int]) -> list[int]:
    """Validate the supplied count before deduplicating, then preserve order."""

    supplied = [int(value) for value in values]
    if not supplied:
        raise ValueError("at least one pool id is required")
    if len(supplied) > MAX_POOL_IDS:
        raise ValueError(f"at most {MAX_POOL_IDS} pool ids may be supplied")
    if any(value <= 0 for value in supplied):
        raise ValueError("pool ids must be positive")
    return list(dict.fromkeys(supplied))


def normalize_session_ids(values: Iterable[int]) -> list[int]:
    """Validate the small explicit session set before deduplication."""

    supplied = [int(value) for value in values]
    if not supplied:
        raise ValueError("at least one session id is required")
    if len(supplied) > MAX_SESSION_IDS:
        raise ValueError(f"at most {MAX_SESSION_IDS} session ids may be supplied")
    if any(value <= 0 for value in supplied):
        raise ValueError("session ids must be positive")
    return list(dict.fromkeys(supplied))


def _result(pool_id: int, status: str, reason: str) -> dict[str, Any]:
    """Construct the complete and intentionally tiny public output schema."""

    return {
        "pool_id": int(pool_id),
        "status": str(status),
        "reason": str(reason),
    }


def _blocked_results(pool_ids: Iterable[int]) -> list[dict[str, Any]]:
    return [
        _result(pool_id, "blocked", "release_validation_fenced")
        for pool_id in pool_ids
    ]


def _fetch_explicit_rows(conn: Any, pool_ids: list[int]) -> dict[int, dict[str, Any]]:
    placeholders = ",".join("?" for _ in pool_ids)
    rows = conn.execute(
        SELECT_EXPLICIT_POOL_ROWS_SQL.format(placeholders=placeholders),
        tuple(pool_ids),
    ).fetchall()
    return {int(row["id"]): dict(row) for row in rows}


def _project_eligibility(row: dict[str, Any]) -> tuple[str, str, str]:
    """Return ``(status, reason, private_url)`` without consulting providers.

    Suppressing the cache lookup retains the upstream health state.  An
    already-cached live ephemeral source therefore stays eligible and lets
    ``cache_image`` return its idempotent cache hit during execute.
    """

    projected = project_pool_avatar(row, cached_avatar_lookup=lambda _url: "")
    state = str(projected.get("avatar_upstream_status") or "missing").strip().lower()
    if state in {"expired", "missing", "invalid"}:
        reason = state
        return "skipped", reason, ""

    raw_url = str(projected.get("avatar_url") or "").strip()
    if not raw_url:
        return "skipped", "missing", ""
    if raw_url.startswith((
        "/api/vkpi-media/image-cache/",
        "/api/admin/vkpi/media/image-cache/",
    )):
        return "skipped", "durable", ""
    # This is the same URL allowlist used internally by cache_image.  Check it
    # before execute so cache_image is never even invoked for an unapproved
    # host, while cache_image remains the final enforcement boundary.
    if _normalize_image_url(raw_url) is None:
        return "skipped", "not_allowlisted", ""
    return "eligible", f"{state}_allowlisted", raw_url


def _cache_result(pool_id: int, payload: Any) -> dict[str, Any]:
    """Map cache internals to a fixed non-secret output vocabulary."""

    data = payload if isinstance(payload, dict) else {}
    status = str(data.get("status") or "").strip().lower()
    if status == "cached":
        return _result(pool_id, "cached", "cache_hit_or_fetched")
    if status == "skipped":
        return _result(pool_id, "skipped", "cache_image_skipped")
    if status == "failed":
        return _result(pool_id, "failed", "cache_image_failed")
    return _result(pool_id, "failed", "cache_image_unexpected")


def _exit_code(results: Iterable[dict[str, Any]]) -> int:
    """Keep the process verdict aligned with the per-row honest statuses."""

    statuses = {str(item.get("status") or "") for item in results}
    if "blocked" in statuses:
        return 2
    if "failed" in statuses:
        return 1
    return 0


def _session_summary(session_ids: list[int], *, execute: bool) -> dict[str, Any]:
    """Return the fixed, aggregate-only session output contract."""

    return {
        "mode": "session",
        "status": "ok" if execute else "dry_run",
        "dry_run": not execute,
        "sessions_requested": len(session_ids),
        "sessions_found": 0,
        "creator_items_scanned": 0,
        "avatar_references": 0,
        "unique_avatar_urls": 0,
        "duplicate_avatar_references": 0,
        "eligible_urls": 0,
        "cached_urls": 0,
        "skipped_urls": 0,
        "failed_urls": 0,
        "blocked_urls": 0,
        "invalid_payloads": 0,
        "url_cap": MAX_SESSION_AVATAR_URLS,
        "url_cap_exceeded": False,
        "item_scan_cap_exceeded": False,
        "avatar_scan_complete": True,
        "provider_calls_performed": False,
        "business_db_writes": 0,
    }


def _blocked_session_summary(session_ids: list[int], reason: str) -> dict[str, Any]:
    summary = _session_summary(session_ids, execute=True)
    summary.update({"status": "blocked", "reason": str(reason)})
    return summary


def _parse_session_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, (str, bytes)) or not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _fetch_explicit_session_items(conn: Any, session_ids: list[int]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(
        SELECT_EXPLICIT_SESSION_ITEMS_SQL.format(placeholders=placeholders),
        (*session_ids, MAX_SESSION_CREATOR_ITEMS + 1),
    ).fetchall()
    return [dict(row) for row in rows]


def run_sessions(
    conn: Any,
    *,
    session_ids: Iterable[int],
    execute: bool = False,
    cache_image_fn: Callable[[Any], dict[str, Any]] | None = None,
    fence_active_fn: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Plan or cache creator avatars from explicit historical sessions.

    Only the top-level ``payload_json.avatar_url`` is inspected. Content
    thumbnails, names, handles and raw provider documents are never selected.
    """

    ids = normalize_session_ids(session_ids)
    cache_fn = cache_image if cache_image_fn is None else cache_image_fn
    fence_fn = release_validation_active if fence_active_fn is None else fence_active_fn
    if execute and fence_fn():
        return _blocked_session_summary(ids, "release_validation_fenced")

    summary = _session_summary(ids, execute=execute)
    rows = _fetch_explicit_session_items(conn, ids)
    if len(rows) > MAX_SESSION_CREATOR_ITEMS:
        summary.update({
            "status": "blocked",
            "reason": "creator_item_scan_cap_exceeded",
            # The query reads one sentinel row past the cap solely to prove
            # that processing must stop before any payload is inspected.
            "creator_items_scanned": MAX_SESSION_CREATOR_ITEMS + 1,
            "item_scan_cap_exceeded": True,
            "avatar_scan_complete": False,
        })
        return summary
    summary["creator_items_scanned"] = len(rows)
    summary["sessions_found"] = len({int(row["session_id"]) for row in rows})

    unique_urls: list[str] = []
    seen_urls: set[str] = set()
    for row in rows:
        payload = _parse_session_payload(row.get("payload_json"))
        if payload is None:
            summary["invalid_payloads"] += 1
            continue
        raw_url = str(payload.get("avatar_url") or "").strip()
        if not raw_url:
            continue
        summary["avatar_references"] += 1
        if raw_url in seen_urls:
            summary["duplicate_avatar_references"] += 1
            continue
        seen_urls.add(raw_url)
        if len(unique_urls) >= MAX_SESSION_AVATAR_URLS:
            summary.update({
                "status": "blocked",
                "reason": "avatar_url_cap_exceeded",
                "url_cap_exceeded": True,
                # The extra URL is observed but never retained or fetched.
                "unique_avatar_urls": len(seen_urls),
                "avatar_scan_complete": False,
            })
            return summary
        unique_urls.append(raw_url)
    summary["unique_avatar_urls"] = len(unique_urls)

    eligible: list[str] = []
    for raw_url in unique_urls:
        status, _reason, private_url = _project_eligibility({
            "avatar_url": raw_url,
            "raw_platform_data": {},
        })
        if status == "eligible":
            eligible.append(private_url)
        else:
            summary["skipped_urls"] += 1
    summary["eligible_urls"] = len(eligible)
    if not execute:
        return summary

    for index, private_url in enumerate(eligible):
        if fence_fn():
            summary["blocked_urls"] += len(eligible) - index
            summary.update({"status": "blocked", "reason": "release_validation_fenced"})
            break
        try:
            payload = cache_fn(private_url)
        except Exception:
            summary["failed_urls"] += 1
            continue
        cache_status = str(payload.get("status") if isinstance(payload, dict) else "").lower()
        if cache_status == "cached":
            summary["cached_urls"] += 1
        elif cache_status == "skipped":
            summary["skipped_urls"] += 1
        else:
            summary["failed_urls"] += 1
    if summary["status"] != "blocked" and summary["failed_urls"]:
        summary["status"] = "failed"
    return summary


def _session_exit_code(summary: dict[str, Any]) -> int:
    status = str(summary.get("status") or "")
    if status == "blocked":
        return 2
    if status == "failed":
        return 1
    return 0


def run(
    conn: Any,
    *,
    pool_ids: Iterable[int],
    execute: bool = False,
    cache_image_fn: Callable[[Any], dict[str, Any]] | None = None,
    fence_active_fn: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """Plan or execute an explicit-ID prewarm without database writes."""

    ids = normalize_pool_ids(pool_ids)
    cache_fn = cache_image if cache_image_fn is None else cache_image_fn
    fence_fn = release_validation_active if fence_active_fn is None else fence_active_fn
    if execute and fence_fn():
        return _blocked_results(ids)

    rows_by_id = _fetch_explicit_rows(conn, ids)
    results: list[dict[str, Any]] = []
    for pool_id in ids:
        row = rows_by_id.get(pool_id)
        if row is None:
            results.append(_result(pool_id, "skipped", "not_found"))
            continue
        status, reason, private_url = _project_eligibility(row)
        if status != "eligible":
            results.append(_result(pool_id, status, reason))
            continue
        if not execute:
            results.append(_result(pool_id, "eligible", "dry_run"))
            continue
        # The fence may appear after the read-only plan was built.  Re-check
        # immediately before every cache write/fetch and stop all remaining
        # execute work fail-closed.
        if fence_fn():
            results.append(_result(pool_id, "blocked", "release_validation_fenced"))
            continue
        try:
            cache_payload = cache_fn(private_url)
        except Exception:
            # Exception messages may contain a signed URL.  Emit only a fixed
            # category and never interpolate the exception.
            results.append(_result(pool_id, "failed", "cache_image_exception"))
            continue
        results.append(_cache_result(pool_id, cache_payload))
    return results


def _make_read_only(conn: Any) -> None:
    """Fence the database handle itself; cache files remain the only execute write."""

    from app.db.connection import is_postgres_runtime

    if is_postgres_runtime():
        raw = getattr(conn, "_raw", None)
        if raw is None:
            raise RuntimeError("postgres_read_only_handle_unavailable")
        # Set before the first statement so psycopg applies read-only mode to
        # every transaction opened by this one-shot process.
        raw.read_only = True
        if not bool(getattr(raw, "read_only", False)):
            raise RuntimeError("postgres_read_only_not_verified")
        return

    conn.execute("PRAGMA query_only=ON")
    row = conn.execute("PRAGMA query_only").fetchone()
    value = row[0] if isinstance(row, (tuple, list)) else row["query_only"]
    if int(value or 0) != 1:
        raise RuntimeError("sqlite_query_only_not_verified")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按显式 Pool 或历史搜索会话有界预热外部头像；默认 dry-run",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--pool-id",
        action="append",
        type=_positive_pool_id,
        help="vkpi_kol_pool.id；可重复，最多 50 个",
    )
    target.add_argument(
        "--session-id",
        action="append",
        type=_positive_session_id,
        help="vkpi_kol_search_sessions.id；可重复，最多 5 个",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际调用现有 cache_image；省略时只输出 dry-run 资格",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        pool_ids = normalize_pool_ids(args.pool_id) if args.pool_id else []
        session_ids = normalize_session_ids(args.session_id) if args.session_id else []
    except ValueError as exc:
        _build_parser().error(str(exc))

    if args.execute and release_validation_active():
        blocked = (
            _blocked_results(pool_ids)
            if pool_ids
            else _blocked_session_summary(session_ids, "release_validation_fenced")
        )
        out_json(blocked, ensure_ascii=False, sort_keys=True)
        return 2

    from app.db.connection import get_conn

    conn = get_conn()
    try:
        _make_read_only(conn)
        results = (
            run(conn, pool_ids=pool_ids, execute=bool(args.execute))
            if pool_ids
            else run_sessions(conn, session_ids=session_ids, execute=bool(args.execute))
        )
    finally:
        conn.rollback()
        conn.close()
    out_json(results, ensure_ascii=False, sort_keys=True)
    return _exit_code(results) if isinstance(results, list) else _session_exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
