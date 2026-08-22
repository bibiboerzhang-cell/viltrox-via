"""Metric-tracking ignition + prediction verification against a disposable Postgres.

Run with::

    VKPI_PYTEST_ALLOW_LIVE_SERVICES=1 DATABASE_URL=postgresql://.../vkpi_closeout_test \
        pytest -m pg tests/test_metric_tracking_pg.py

Every DB test runs inside one transaction on the ``pg_compat`` connection with
``commit`` neutralised, so nothing persists.  The pure-parser tests need no DB.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domains import content_metric_snapshots
from app.domains.kol import (
    video_metric_refresh,
    video_metric_schedule,
    video_tracking_budget,
    video_tracking_enroll,
)
from app.domains.market_brain import prediction_ledger, prediction_rollup_truth
from app.domains.projects import workflow_evidence_video_metadata as metadata_parser


NOW = datetime.now(timezone.utc)


# ── pure provider parsing (no DB) ─────────────────────────────────────────


def test_instagram_item_metrics_survive_actor_field_drift() -> None:
    reel = {
        "type": "Video", "url": "https://www.instagram.com/reel/ABC123/",
        "ownerUsername": "creator", "caption": "hello", "timestamp": "2026-08-01T10:00:00.000Z",
        "videoPlayCount": "12.3k", "likesCount": -1, "commentsCount": 45, "reshareCount": 6,
    }
    parsed = metadata_parser._apify_item_metadata("instagram", reel["url"], reel, "run-ig")
    assert parsed["media_kind"] == "video"
    assert (parsed["view_count"], parsed["like_count"], parsed["comment_count"], parsed["share_count"]) == (
        12300, None, 45, 6,
    )
    assert content_metric_snapshots.has_any_metric(
        views=parsed["view_count"], likes=parsed["like_count"],
        comments=parsed["comment_count"], shares=parsed["share_count"],
    )
    graphql_shape = {
        "type": "Video", "ownerUsername": "creator", "caption": "x",
        "igPlayCount": 900, "edge_liked_by": {"count": 10}, "edge_media_to_comment": {"count": 2},
    }
    parsed = metadata_parser._apify_item_metadata("instagram", "https://www.instagram.com/p/X/", graphql_shape, "r")
    assert (parsed["view_count"], parsed["like_count"], parsed["comment_count"]) == (900, 10, 2)


def test_tiktok_item_metrics_read_nested_stats() -> None:
    item = {
        "text": "clip", "webVideoUrl": "https://www.tiktok.com/@a/video/7000000000000000001",
        "authorMeta": {"id": "1", "name": "a"}, "createTimeISO": "2026-08-10T00:00:00.000Z",
        "stats": {"playCount": 5000, "diggCount": 120, "commentCount": 9, "shareCount": 3},
    }
    parsed = metadata_parser._apify_item_metadata("tiktok", item["webVideoUrl"], item, "run-tt")
    assert (parsed["view_count"], parsed["like_count"], parsed["comment_count"], parsed["share_count"]) == (
        5000, 120, 9, 3,
    )
    legacy = {**item, "stats": None, "playCount": "1.2M", "diggCount": 7}
    parsed = metadata_parser._apify_item_metadata("tiktok", item["webVideoUrl"], legacy, "run-tt")
    assert (parsed["view_count"], parsed["like_count"]) == (1_200_000, 7)


def test_failure_classifier_keeps_reason_and_drops_message() -> None:
    classified = content_metric_snapshots.classify_refresh_failure(
        RuntimeError("APIFY_API_TOKEN is not configured sk-secret")
    )
    assert classified == {
        "reason": "provider_not_configured",
        "error_code": "provider_not_configured",
        "exception": "runtimeerror",
    }
    assert content_metric_snapshots.classify_refresh_failure(LookupError("x"))["reason"] == "no_media"
    assert content_metric_snapshots.classify_refresh_failure(ValueError("HTTP 429"))["reason"] == "rate_limited"
    assert content_metric_snapshots.classify_refresh_failure(OSError("boom"))["reason"] == "provider_error"
    flags = content_metric_snapshots.quality_flags_for_metrics(
        views=None, likes=None, comments=None, shares=None, source_observed_at=None, failed=True,
    )
    assert "all_metrics_missing" not in flags


# ── disposable Postgres ───────────────────────────────────────────────────


def _seed_kol(conn, *, staff_id: int) -> int:
    uid = uuid.uuid4().hex[:12]
    row = conn.execute(
        """
        INSERT INTO vkpi_kol_pool (pool_uid, platform, handle)
        VALUES (?, 'youtube', ?)
        RETURNING id
        """,
        (f"pgtest-{uid}", f"pgtest_{uid}"),
    ).fetchone()
    kol_id = int(dict(row)["id"])
    conn.execute(
        "INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id) VALUES (?, ?)",
        (kol_id, int(staff_id)),
    )
    return kol_id


def _seed_evidence(
    conn, *, kol_id: int, platform: str, url: str, published_at: datetime,
    media_kind: str = "video", view_count: int | None = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO vkpi_kol_video_evidence (
            kol_pool_id, content_url, platform, source, evidence_type, is_active,
            media_kind, published_at_norm, posted_at, view_count, channel_id
        ) VALUES (?, ?, ?, 'pgtest', 'video', TRUE, ?, ?, ?, ?, 'UC-pgtest')
        RETURNING id
        """,
        (kol_id, url, platform, media_kind, published_at, published_at.date(), view_count),
    ).fetchone()
    return int(dict(row)["id"])


def _seed_actor(conn) -> int:
    """An active employee with vkpi write, created inside the test transaction.

    The shared ``pg_test_identities`` rows may pre-exist in a cloned business
    database with a pending user status, which the production-mode actor
    revalidation rejects; a private identity keeps the test deterministic.
    """
    seed = 900_000_000 + (uuid.uuid4().int % 90_000_000)
    conn.execute(
        """
        INSERT INTO users (id, email, password_hash, name, status, role, email_verified)
        VALUES (?, ?, '!pg-test-only!', 'Metric Tracking PG', 'active', 'creator', 1)
        """,
        (seed, f"metric-tracking-pg-{seed}@example.invalid"),
    )
    conn.execute(
        """
        INSERT INTO staff (id, user_id, role, permissions_json, active, is_owner, accepted_at)
        VALUES (?, ?, 'employee', ?, 1, 0, NOW())
        """,
        (seed, seed, json.dumps({"vkpi": "write", "kol_ops": "read"})),
    )
    return seed


@pytest.fixture()
def tracking_pg(pg_compat, monkeypatch):
    """Compat connection with commit neutralised; rolled back on close."""
    monkeypatch.setattr(pg_compat, "commit", lambda: None)
    return pg_compat, _seed_actor(pg_compat)


@pytest.mark.pg
def test_enroll_is_idempotent_and_registers_only_eligible_evidence(tracking_pg) -> None:
    conn, staff_id = tracking_pg
    kol_id = _seed_kol(conn, staff_id=staff_id)
    tag = uuid.uuid4().hex[:10]
    hot = _seed_evidence(
        conn, kol_id=kol_id, platform="youtube",
        url=f"https://www.youtube.com/watch?v=h{tag}", published_at=NOW - timedelta(days=2),
    )
    cold = _seed_evidence(
        conn, kol_id=kol_id, platform="instagram",
        url=f"https://www.instagram.com/reel/C{tag}/", published_at=NOW - timedelta(days=90),
    )
    _seed_evidence(
        conn, kol_id=kol_id, platform="instagram", media_kind="carousel",
        url=f"https://www.instagram.com/p/D{tag}/", published_at=NOW - timedelta(days=3),
    )

    first = video_tracking_enroll.enroll_my_kol_evidence(conn, apply=True, kol_pool_ids=[kol_id], now=NOW)
    assert first["inserted"] == 2 and first["conflicts"] == 0
    assert first["tiers"] == {"hot": 1, "warm": 0, "cold": 1}
    assert first["skipped"] == {"media_kind_carousel": 1}
    assert first["provider_calls_performed"] is False
    entries = {item["evidence_id"]: item for item in first["sample"]}
    assert entries[hot]["cadence_hours"] == 6.0 and entries[cold]["cadence_hours"] == 168.0
    assert all(item["actor_staff_id"] == staff_id and item["actor_kind"] == "favorite_owner" for item in entries.values())

    second = video_tracking_enroll.enroll_my_kol_evidence(conn, apply=True, kol_pool_ids=[kol_id], now=NOW)
    assert second["inserted"] == 0 and second["to_register"] == 0 and second["already_active"] == 2

    rows = conn.execute(
        """
        SELECT evidence_id, status, source, tracked_by_staff_id
        FROM vkpi_kol_video_metric_tracking WHERE evidence_id IN (?, ?) ORDER BY evidence_id
        """,
        (min(hot, cold), max(hot, cold)),
    ).fetchall()
    assert [(dict(r)["status"], dict(r)["source"], int(dict(r)["tracked_by_staff_id"])) for r in rows] == [
        ("active", video_tracking_enroll.ENROLL_SOURCE, staff_id),
        ("active", video_tracking_enroll.ENROLL_SOURCE, staff_id),
    ]
    assert conn.execute("SELECT COUNT(*) AS n FROM apify_jobs WHERE payload->>'evidence_id' IN (?, ?)",
                        (str(hot), str(cold))).fetchone()["n"] == 0


@pytest.mark.pg
def test_budget_scope_seed_is_idempotent_and_gate_reads_ledger(tracking_pg, monkeypatch) -> None:
    conn, _staff_id = tracking_pg
    conn.execute("DELETE FROM vkpi_provider_budget_caps WHERE scope=?", (video_tracking_budget.BUDGET_SCOPE,))
    monkeypatch.setenv(video_tracking_budget.CAP_ENV, "12.5")

    seeded = video_tracking_budget.ensure_budget_scope(conn, now=NOW)
    assert seeded["action"] == "inserted" and seeded["cap_usd"] == 12.5
    assert video_tracking_budget.ensure_budget_scope(conn, now=NOW)["action"] == "unchanged"
    scope = video_tracking_budget.load_scope(conn)
    assert json.loads(scope["metadata_json"])["cost_tag"] == "metric_tracking"
    assert float(scope["cap_usd"]) == 12.5

    assert video_tracking_budget.budget_gate(conn, now=NOW)["allowed"] is True
    marker = json.dumps({"operation": video_tracking_budget.LEDGER_OPERATION})
    conn.execute(
        """
        INSERT INTO vkpi_ai_cost_ledger (cron_task, ai_provider, model_name, cost_usd, metadata_json, occurred_at)
        VALUES ('provider:apify', 'apify', 'apify/instagram-scraper', 13.0, ?, ?)
        """,
        (marker, NOW),
    )
    assert video_tracking_budget.month_spend_usd(conn, now=NOW) >= 13.0
    gate = video_tracking_budget.budget_gate(conn, now=NOW)
    assert gate["allowed"] is False and gate["reason"] == "hard_stop_or_projected_cap:metric_tracking"
    assert float(video_tracking_budget.load_scope(conn)["current_spend"]) == gate["spend_usd"]

    blocked = video_metric_schedule.enqueue_due_tracked_video_refreshes(conn, now=NOW)
    assert blocked["status"] == "budget_blocked" and blocked["queued"] == 0


@pytest.mark.pg
def test_failure_reason_lands_on_pg_snapshot_without_all_metrics_missing(tracking_pg, monkeypatch) -> None:
    conn, staff_id = tracking_pg
    kol_id = _seed_kol(conn, staff_id=staff_id)
    url = f"https://www.tiktok.com/@pgtest/video/7{uuid.uuid4().int % 10**17:017d}"
    evidence_id = _seed_evidence(
        conn, kol_id=kol_id, platform="tiktok", url=url, published_at=NOW - timedelta(days=1),
    )

    def not_configured(_url):
        raise RuntimeError("APIFY_API_TOKEN is not configured")

    monkeypatch.setattr(video_metric_refresh, "_fetch_video_metadata", not_configured)
    result = video_metric_refresh.run_video_metric_refresh_for_job(
        {
            "evidence_id": evidence_id, "kol_pool_id": kol_id, "platform": "tiktok",
            "content_url": url, "staff_id": staff_id,
        },
        conn=conn,
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "provider_not_configured"
    snapshot = dict(conn.execute(
        "SELECT status, error_code, quality_flags FROM vkpi_content_metric_snapshots WHERE id=?",
        (result["snapshot_id"],),
    ).fetchone())
    flags = set(json.loads(snapshot["quality_flags"]))
    assert snapshot["status"] == "failed" and snapshot["error_code"] == "provider_not_configured"
    assert {"failure_reason:provider_not_configured", "exception:runtimeerror", "refresh_failed"} <= flags
    assert "all_metrics_missing" not in flags


@pytest.mark.pg
def test_rollup_writes_measured_actuals_and_evals(tracking_pg) -> None:
    conn, staff_id = tracking_pg
    kol_id = _seed_kol(conn, staff_id=staff_id)
    # Older than any pre-existing pending row so the bounded, oldest-first
    # backfill scan reaches it even in a cloned business database.
    created_at = NOW - timedelta(days=400)
    forecast = conn.execute(
        """
        INSERT INTO vkpi_forecast_log (kol_pool_id, sku, p10, p50, p90, confidence, method, context, created_at, outcome)
        VALUES (?, 'PGTEST-SKU', 800, 1000, 1500, 'medium', 'evidence_quantile_v1', 'drawer', ?, 'pending')
        RETURNING id
        """,
        (kol_id, created_at),
    ).fetchone()
    log_id = int(dict(forecast)["id"])
    tag = uuid.uuid4().hex[:10]
    for index, views in enumerate((900, 1200, 1300)):
        evidence_id = _seed_evidence(
            conn, kol_id=kol_id, platform="youtube", view_count=views,
            url=f"https://www.youtube.com/watch?v=r{index}{tag}", published_at=created_at + timedelta(days=5 + index),
        )
        content_metric_snapshots.record_successful_refresh(
            conn, evidence_id=evidence_id, provider="youtube_api",
            fetched_at=(NOW - timedelta(days=1)).isoformat(), views=views, likes=1, comments=1,
        )

    rollup = prediction_rollup_truth.rollup_forecast_log_truth(conn, commit=False, scan_limit=5000)

    assert rollup["backfill"]["status"] == "ok" and rollup["backfill"]["updated"] >= 1
    row = dict(conn.execute(
        "SELECT actual_views, outcome, actual_at FROM vkpi_forecast_log WHERE id=?", (log_id,),
    ).fetchone())
    assert (row["actual_views"], row["outcome"]) == (1200, "hit_in_band") and row["actual_at"] is not None

    evals = dict(conn.execute(
        f"SELECT actual_value, error_abs, interval_hit, actual_json FROM {prediction_ledger.EVALS_TABLE} "
        "WHERE run_id=? AND outcome_id IS NULL",
        (f"fclog_{log_id}",),
    ).fetchone())
    payload = evals["actual_json"] if isinstance(evals["actual_json"], dict) else json.loads(evals["actual_json"])
    assert float(evals["actual_value"]) == 1200.0 and float(evals["error_abs"]) == 200.0
    assert evals["interval_hit"] in (True, 1)
    assert payload["binding_status"] == prediction_rollup_truth.MEASURED_BINDING_STATUS
    assert payload["prediction_source"] == "forecast_log"
    assert payload["sample_count"] == 3 and payload["snapshot_backed_count"] == 3

    measured = rollup["weekly"]["measured_nonbinary"]
    assert measured["n"] >= 1 and measured["wape"] is not None
    assert rollup["metrics"]["measured_minimum"] == prediction_rollup_truth.MIN_MEASURED_CLAIMABLE_EVALS
    assert "fva" in rollup["metrics"]
    # Verified tier stays untouched by measured rows.
    assert rollup["weekly"]["verified_nonbinary"]["n"] == 0

    again = prediction_rollup_truth.record_forecast_log_evals(conn, scan_limit=5000)
    assert again["recorded"] == 0 and again["updated"] >= 1
