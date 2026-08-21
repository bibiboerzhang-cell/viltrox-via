from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool, vkpi_kol_pool_intel, vkpi_kol_pool_jobs
from app.api.routers import vkpi_my_kol as router_mod
from app.domains.comments import collector as comments_collector
from app.domains.discovery import buildout
from app.domains.kol import video_analysis_enqueue
from app.domains.kol.my_kol_paid_action_access import (
    FENCE_KEY,
    MyKolPaidActionError,
    assert_target_readable,
    assert_target_writable,
    build_target_fence,
)
from app.domains.kol.video_url_identity import parse_supported_video_url
from app.workers import apify_jobs_worker as pg_worker
from test_my_kol_video_tracking import VIDEO_URL, _tracking_conn


@pytest.fixture()
def tracking_conn():
    conn = _tracking_conn()
    yield conn
    conn.close()


def _job_count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM apify_jobs").fetchone()[0])


def _paid_staff(staff_id: int, user_id: int) -> dict:
    return {
        "id": staff_id,
        "user_id": user_id,
        "role": "member",
        "permissions_json": '{"vkpi":"write"}',
        "active": 1,
    }


def test_paid_action_scope_is_owner_or_manager_write_and_share_read_only(tracking_conn):
    owner = _paid_staff(10, 110)
    shared = _paid_staff(20, 120)

    assert assert_target_writable(tracking_conn, kol_pool_id=1, staff=owner) == 10
    assert assert_target_readable(tracking_conn, kol_pool_id=1, staff=shared) == 20
    with pytest.raises(MyKolPaidActionError) as denied:
        assert_target_writable(tracking_conn, kol_pool_id=1, staff=shared)
    assert denied.value.code == "my_kol_paid_action_write_forbidden"


def test_direct_id_shared_writes_and_unrelated_viewer_context_fail_before_queue(
    tracking_conn,
    monkeypatch,
):
    from app.db import connection

    shared = _paid_staff(20, 120)
    monkeypatch.setattr(video_analysis_enqueue, "get_conn", lambda: tracking_conn)
    monkeypatch.setattr(connection, "get_conn", lambda: tracking_conn)
    monkeypatch.setattr(buildout, "get_conn", lambda: tracking_conn)
    monkeypatch.setattr(router_mod, "get_conn", lambda: tracking_conn)
    monkeypatch.setattr(vkpi_kol_pool_intel, "release_validation_active", lambda: False)

    with pytest.raises(HTTPException) as video_error:
        vkpi_kol_pool.enqueue_pool_item_video_analysis(
            1,
            body={"evidence_id": 101},
            staff=shared,
        )
    assert video_error.value.status_code == 403

    with pytest.raises(HTTPException) as comments_error:
        vkpi_kol_pool_jobs.enqueue_kol_pool_comments_collect(
            body={"kol_pool_id": 1, "evidence_ids": [101]},
            staff=shared,
        )
    assert comments_error.value.status_code == 403

    with pytest.raises(HTTPException) as audience_error:
        asyncio.run(vkpi_kol_pool_intel.refresh_kol_audience_stats(1, staff=shared))
    assert audience_error.value.status_code == 403

    with pytest.raises(HTTPException) as build_error:
        vkpi_kol_pool_intel.build_full_profile_endpoint(1, staff=shared)
    assert build_error.value.status_code == 403

    with pytest.raises(HTTPException) as viewer_error:
        router_mod.my_kol_viewer_context_endpoint(
            1,
            staff={"id": 999, "role": "member"},
        )
    assert viewer_error.value.status_code == 403
    assert _job_count(tracking_conn) == 0


@pytest.mark.parametrize(
    ("job_type", "action"),
    [
        ("video", "video_analysis"),
        ("kol_pool_comments_collect", "comments_collect"),
        ("kol_audience_stats_refresh", "audience_refresh"),
    ],
)
def test_revoked_paid_action_worker_blocks_before_provider(
    tracking_conn,
    monkeypatch,
    job_type,
    action,
):
    from app.db import connection
    from app.workers import apify_jobs_worker_runtime

    evidence_ids = [101] if action != "audience_refresh" else []
    fence = build_target_fence(
        tracking_conn,
        action=action,
        kol_pool_id=1,
        staff=_paid_staff(10, 110),
        evidence_ids=evidence_ids,
    )
    payload = {
        "kol_pool_id": 1,
        "target_type": "video" if action == "video_analysis" else "kol_profile",
        "target_id": "101" if action == "video_analysis" else 1,
        "evidence_ids": evidence_ids if action == "comments_collect" else None,
        "source_url": VIDEO_URL if action == "video_analysis" else None,
        FENCE_KEY: fence,
    }
    tracking_conn.execute(
        "DELETE FROM vkpi_kol_pool_favorites WHERE kol_pool_id=1 AND staff_id=10"
    )
    tracking_conn.commit()
    provider_calls: list[str] = []
    blocked: list[tuple] = []
    monkeypatch.setattr(connection, "get_conn", lambda: tracking_conn)
    monkeypatch.setattr(pg_worker, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(
        pg_worker,
        "_block_job",
        lambda *_args: blocked.append(_args),
    )
    monkeypatch.setattr(
        pg_worker,
        "_process_kol_pool_comments_collect",
        lambda *_args: provider_calls.append("comments"),
    )
    monkeypatch.setattr(
        pg_worker,
        "_process_kol_audience_stats_refresh",
        lambda *_args: provider_calls.append("audience"),
    )
    monkeypatch.setattr(
        apify_jobs_worker_runtime,
        "process_job_impl",
        lambda *_args: provider_calls.append("video"),
    )

    pg_worker._process_job(None, {"id": 901, "job_type": job_type, "payload": payload})

    assert provider_calls == []
    assert len(blocked) == 1
    assert blocked[0][2] == "my_kol_paid_action_write_forbidden"
    assert blocked[0][3]["provider_calls_performed"] is False


@pytest.mark.parametrize("drift", ["payload_evil_host", "stored_evidence_changed"])
def test_video_worker_rejects_identity_drift_before_provider(
    tracking_conn,
    monkeypatch,
    drift,
):
    from app.db import connection
    from app.workers import apify_jobs_worker_runtime

    fence = build_target_fence(
        tracking_conn,
        action="video_analysis",
        kol_pool_id=1,
        staff=_paid_staff(10, 110),
        evidence_ids=[101],
    )
    payload = {
        "kol_pool_id": 1,
        "target_type": "video",
        "target_id": "101",
        "source_url": VIDEO_URL,
        FENCE_KEY: fence,
    }
    if drift == "payload_evil_host":
        payload["source_url"] = "https://youtube.com.evil.test/watch?v=abcDEF12345"
    else:
        tracking_conn.execute(
            "UPDATE vkpi_kol_video_evidence SET content_url=? WHERE id=101",
            ("https://www.youtube.com/watch?v=changed12345",),
        )
        tracking_conn.commit()
    provider_calls: list[str] = []
    blocked: list[tuple] = []
    monkeypatch.setattr(connection, "get_conn", lambda: tracking_conn)
    monkeypatch.setattr(pg_worker, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(pg_worker, "_block_job", lambda *_args: blocked.append(_args))
    monkeypatch.setattr(
        apify_jobs_worker_runtime,
        "process_job_impl",
        lambda *_args: provider_calls.append("video"),
    )

    pg_worker._process_job(None, {"id": 902, "job_type": "video", "payload": payload})

    assert provider_calls == []
    assert blocked[0][3]["provider_calls_performed"] is False
    assert "identity" in blocked[0][2]


def test_paid_video_payload_and_fence_strip_sensitive_query_and_fragment(
    tracking_conn,
    monkeypatch,
):
    sensitive_url = (
        "https://www.youtube.com/watch?v=Sensitive99&utm_source=private-token#secret"
    )
    canonical_url = "https://www.youtube.com/watch?v=Sensitive99"
    tracking_conn.execute(
        """
        INSERT INTO vkpi_kol_video_evidence (
            id, kol_pool_id, content_url, platform, evidence_type, is_active,
            channel_id, view_count
        ) VALUES (303, 1, ?, 'youtube', 'video', 1, 'UC-owner', 1)
        """,
        (sensitive_url,),
    )
    tracking_conn.commit()
    identity = parse_supported_video_url(sensitive_url)
    assert identity.normalized_url == canonical_url

    evidence = {
        "evidence_id": 303,
        "kol_pool_id": 1,
        "content_url": sensitive_url,
        "evidence_platform": "youtube",
        "evidence_type": "video",
        "title": "sensitive locator",
        "kol_handle": "creator",
        "view_count": 1,
        "duration_seconds": 30,
        "viltrox_fit_score": 88.5,
    }
    captured: dict = {}
    monkeypatch.setattr(video_analysis_enqueue, "_load_owned_evidence", lambda *_args, **_kwargs: evidence)
    monkeypatch.setattr(video_analysis_enqueue, "_ready_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(video_analysis_enqueue, "_active_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(video_analysis_enqueue, "_fit_snapshot", lambda *_args, **_kwargs: 88.5)
    monkeypatch.setattr(
        video_analysis_enqueue.llm_gateway,
        "budget_preflight",
        lambda *_args, **_kwargs: {
            "provider_gate_reason": "allowed",
            "model_readiness_status": "production_ready",
            "providers": [
                {
                    "provider": "google",
                    "provider_calls_allowed": True,
                    "model": "gemini-test",
                }
            ],
        },
    )
    monkeypatch.setattr(
        video_analysis_enqueue,
        "enqueue_active_apify_job",
        lambda _conn, **kwargs: captured.update(kwargs)
        or ({"id": 303, "payload": kwargs["payload"], "status": "queued"}, True),
    )

    result = video_analysis_enqueue._enqueue_final_v1_video_analysis(
        tracking_conn,
        kol_pool_id=1,
        evidence_id=303,
        staff=_paid_staff(10, 110),
        commit=False,
        enforce_target_write=True,
    )

    assert result["status"] == "queued"
    serialized = json.dumps(captured["payload"], ensure_ascii=False)
    assert captured["payload"]["source_url"] == canonical_url
    assert captured["payload"][FENCE_KEY]["evidence"][0]["normalized_url"] == canonical_url
    assert "private-token" not in serialized
    assert "#secret" not in serialized


def test_tiktok_identity_keeps_creator_path_but_strips_tracking_query():
    identity = parse_supported_video_url(
        "https://www.tiktok.com/@creator/video/7123456789012345678?utm_source=secret#frag"
    )
    assert identity.normalized_url == (
        "https://www.tiktok.com/@creator/video/7123456789012345678/"
    )
