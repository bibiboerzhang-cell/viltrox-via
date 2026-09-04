from __future__ import annotations

import json
from contextlib import nullcontext

import pytest

from app.domains.kol import (
    url_deep_crawl,
    url_deep_crawl_execute,
    url_deep_crawl_queue,
    video_tracking,
)
from app.workers import apify_jobs_worker_handlers as worker_handlers
from tests.test_my_kol_profile_deep_crawl_scope import (  # noqa: F401
    PROFILE_URL,
    _queued_payload,
    crawl_conn,
)


def test_maintenance_job_never_absorbs_later_interactive_analysis(crawl_conn):
    maintenance = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
        source="kol_search_inventory_daily",
        queue_lane="batch",
        maintenance_refresh=True,
        maintenance_batch_date="2026-09-04",
    )
    interactive = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
    )

    assert maintenance["status"] == "queued"
    assert interactive["status"] == "queued"
    rows = crawl_conn.execute(
        "SELECT payload, idempotency_key FROM apify_jobs ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    first = json.loads(rows[0]["payload"])
    second = json.loads(rows[1]["payload"])
    assert first["maintenance_refresh"] is True
    assert first["maintenance_batch_date"] == "2026-09-04"
    assert "target_write_fence" not in first
    assert first["maintenance_target_fence"] == {
        "version": 1,
        "kind": "kol_search_inventory_daily",
        "kol_pool_id": 1,
        "canonical_profile_url": "https://youtube.com/@Creator",
        "platform": "youtube",
        "stable_identity_key": "youtube:handle:creator",
        "stable_handle": "creator",
        "stable_native_ids": {},
    }
    assert second.get("maintenance_refresh") is None
    assert rows[0]["idempotency_key"] != rows[1]["idempotency_key"]


def test_maintenance_refresh_can_reuse_richer_interactive_job(crawl_conn):
    interactive = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
    )
    maintenance = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
        source="kol_search_inventory_daily",
        queue_lane="batch",
        maintenance_refresh=True,
    )

    assert interactive["status"] == "queued"
    assert maintenance == {
        "status": "already_queued",
        "job_id": interactive["job_id"],
    }
    assert _queued_payload(crawl_conn)["url"] == "https://youtube.com/@Creator"
    assert crawl_conn.execute("SELECT COUNT(*) FROM apify_jobs").fetchone()[0] == 1


def test_maintenance_enqueue_rejects_dirty_handle_url_identity(crawl_conn):
    crawl_conn.execute(
        "UPDATE vkpi_kol_pool SET handle='@Mismatch' WHERE id=1"
    )
    crawl_conn.commit()

    with pytest.raises(video_tracking.VideoTrackingError) as error:
        url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
            PROFILE_URL,
            kol_pool_id=1,
            source="kol_search_inventory_daily",
            queue_lane="batch",
            maintenance_refresh=True,
        )

    assert error.value.code == "maintenance_refresh_target_identity_invalid"
    assert crawl_conn.execute("SELECT COUNT(*) FROM apify_jobs").fetchone()[0] == 0


def test_maintenance_youtube_channel_locator_allows_readable_row_handle():
    channel_id = "UCaaaaaaaaaaaaaaaaaaaaaa"
    row = {
        "id": 1,
        "duplicate_of_id": None,
        "platform": "youtube",
        "handle": "@ReadableCreator",
        "profile_url": f"https://www.youtube.com/channel/{channel_id}",
        "raw_platform_data": json.dumps(
            {"profile": {"items": [{"id": channel_id}]}}
        ),
    }

    identity = url_deep_crawl_queue._validated_maintenance_profile_identity(
        row,
        row["profile_url"],
    )

    assert identity["stable_handle"] == "readablecreator"
    assert identity["stable_native_ids"] == {"channel_id": channel_id}
    url_deep_crawl_execute._verify_maintenance_crawl_identity(
        {
            "maintenance_refresh": True,
            "maintenance_target_fence": identity,
        },
        {
            "status": "ok",
            "profile_payload": {
                "items": [
                    {
                        "id": channel_id,
                        "channelUrl": f"https://youtube.com/channel/{channel_id}",
                    }
                ]
            },
        },
    )


def test_tiktok_native_fence_uses_author_identity_not_video_id():
    stored = {
        "profile": {
            "items": [
                {
                    "id": "video-v1",
                    "authorMeta": {
                        "id": "author-account-a",
                        "name": "alice",
                        "secUid": "secure-account-a",
                    },
                }
            ]
        }
    }
    observed = {
        "items": [
            {
                "id": "video-v2",
                "authorId": "author-account-a",
                "secUid": "secure-account-a",
                "authorMeta": {"name": "alice"},
            }
        ]
    }

    expected_ids = url_deep_crawl_queue._stable_profile_native_ids(
        "tiktok",
        stored,
    )
    assert expected_ids == {
        "account_id": "author-account-a",
        "sec_uid": "secure-account-a",
    }
    assert url_deep_crawl_queue._stable_profile_native_ids(
        "tiktok",
        observed,
    ) == expected_ids
    url_deep_crawl_execute._verify_maintenance_crawl_identity(
        {
            "maintenance_refresh": True,
            "maintenance_target_fence": {
                "platform": "tiktok",
                "stable_handle": "alice",
                "stable_native_ids": expected_ids,
            },
        },
        {"status": "ok", "profile_payload": observed},
    )


def test_maintenance_provider_handle_mismatch_blocks_before_profile_write(
    crawl_conn,
    monkeypatch,
):
    crawl_conn.execute(
        "UPDATE vkpi_kol_pool SET platform='instagram', handle='alice', "
        "profile_url='https://www.instagram.com/alice/', raw_platform_data='{}' "
        "WHERE id=1"
    )
    crawl_conn.commit()
    url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        "https://www.instagram.com/alice/",
        kol_pool_id=1,
        source="kol_search_inventory_daily",
        queue_lane="batch",
        maintenance_refresh=True,
    )
    queued = _queued_payload(crawl_conn)
    body = {
        "url": queued["url"],
        "kol_pool_id": 1,
        "maintenance_refresh": True,
        "maintenance_target_fence": queued["maintenance_target_fence"],
        "mode": "account_deep",
        "max_posts": 1,
    }
    calls = {"provider": 0, "write": 0}

    def wrong_provider(*_args, **_kwargs):
        calls["provider"] += 1
        return {
            "status": "ok",
            "profile_payload": {"items": [{"username": "bob"}]},
        }

    def wrong_write(*_args, **_kwargs):
        calls["write"] += 1
        raise AssertionError("provider identity mismatch must not write the profile")

    monkeypatch.setattr(url_deep_crawl_execute, "get_conn", lambda: crawl_conn)
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_profile_incremental_state",
        lambda *_args, **_kwargs: {"enabled": True},
    )
    monkeypatch.setattr(url_deep_crawl_execute, "_crawl_profile_basics", wrong_provider)
    monkeypatch.setattr(url_deep_crawl_execute, "write_kol_profile_basics", wrong_write)

    with pytest.raises(video_tracking.VideoTrackingError) as error:
        url_deep_crawl_execute._execute_profile_flow(
            url_deep_crawl.classify_url(queued["url"]),
            [{"kol_pool_id": 1, "handle": "alice"}],
            body,
        )

    assert error.value.code == "maintenance_refresh_provider_identity_mismatch"
    assert error.value.provider_calls_performed is True
    assert calls == {"provider": 1, "write": 0}


@pytest.mark.parametrize(
    ("mutation_sql", "expected_code"),
    [
        (
            "UPDATE vkpi_kol_pool SET handle='@Other', "
            "profile_url='https://www.youtube.com/@Other' WHERE id=1",
            "maintenance_refresh_target_drifted",
        ),
        (
            "UPDATE vkpi_kol_pool SET duplicate_of_id=2 WHERE id=1",
            "maintenance_refresh_target_merged",
        ),
    ],
)
def test_maintenance_target_fence_blocks_drift_or_merge_before_provider(
    crawl_conn,
    monkeypatch,
    mutation_sql,
    expected_code,
):
    url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
        source="kol_search_inventory_daily",
        queue_lane="batch",
        maintenance_refresh=True,
    )
    payload = _queued_payload(crawl_conn)
    crawl_conn.execute(mutation_sql)
    crawl_conn.commit()
    provider_calls: list[dict] = []
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "_maintenance_refresh_execution_block_reason",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda body: provider_calls.append(dict(body)) or {"status": "ready"},
    )

    with pytest.raises(video_tracking.VideoTrackingError) as error:
        url_deep_crawl_queue.run_profile_deep_crawl_for_job(payload)

    assert error.value.code == expected_code
    assert provider_calls == []


def test_durable_handler_terminalizes_maintenance_target_merge_without_provider(
    crawl_conn,
    monkeypatch,
):
    url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
        source="kol_search_inventory_daily",
        queue_lane="batch",
        maintenance_refresh=True,
    )
    payload = _queued_payload(crawl_conn)
    crawl_conn.execute("UPDATE vkpi_kol_pool SET duplicate_of_id=2 WHERE id=1")
    crawl_conn.commit()
    provider_calls: list[dict] = []
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "_maintenance_refresh_execution_block_reason",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda body: provider_calls.append(dict(body)) or {"status": "ready"},
    )
    monkeypatch.setattr(worker_handlers, "_resolve_job_staff", lambda *_args: {})
    monkeypatch.setattr(worker_handlers, "db_connection_sync_scope", nullcontext)

    state: dict[str, object] = {
        "status": "running",
        "last_error": None,
        "payload": None,
    }

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=()):
            assert "status='blocked'" in " ".join(str(sql).split())
            state["status"] = "blocked"
            state["last_error"] = params[0]
            state["payload"] = json.loads(params[1])

    class WorkerConn:
        def transaction(self):
            return nullcontext()

        def cursor(self, **_kwargs):
            return Cursor()

    worker_handlers._process_kol_profile_deep_crawl(
        WorkerConn(),
        {"id": 1},
        payload,
    )

    assert state["status"] == "blocked"
    assert state["last_error"] == "maintenance_refresh_target_merged"
    assert state["payload"]["provider_calls_performed"] is False
    assert provider_calls == []


def test_terminalizer_persists_postprovider_fence_truth(monkeypatch):
    from app.domains.kol import content_monitoring

    stored: dict[str, object] = {}
    payload = {
        "maintenance_refresh": True,
        "maintenance_target_fence": {"kol_pool_id": 1},
    }
    error = video_tracking.VideoTrackingError(
        "maintenance_refresh_target_drifted",
        409,
    )
    error.provider_calls_performed = True

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _sql, params=()):
            stored["payload"] = json.loads(params[1])

    class Conn:
        def transaction(self):
            return nullcontext()

        def cursor(self, **_kwargs):
            return Cursor()

    class Logger:
        def warning(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(
        content_monitoring,
        "record_monitor_job_terminal",
        lambda *_args, **_kwargs: None,
    )

    terminalized = worker_handlers.deep_crawl_worker.terminalize_write_fence_error(
        Conn(),
        {"id": 17},
        payload,
        error,
        terminal_codes=url_deep_crawl_queue.TARGET_WRITE_FENCE_TERMINAL_CODES,
        db_connection_sync_scope=nullcontext,
        json_dump=json.dumps,
        logger=Logger(),
    )

    assert terminalized is True
    assert stored["payload"]["provider_calls_performed"] is True


@pytest.mark.parametrize(
    ("mutation_sql", "expected_code"),
    [
        (
            "UPDATE vkpi_kol_pool SET duplicate_of_id=2 WHERE id=1",
            "maintenance_refresh_target_merged",
        ),
        (
            "UPDATE vkpi_kol_pool SET handle='@Changed', "
            "profile_url='https://www.youtube.com/@Changed' WHERE id=1",
            "maintenance_refresh_target_drifted",
        ),
    ],
)
def test_maintenance_target_is_rechecked_after_provider_before_any_write(
    crawl_conn,
    monkeypatch,
    mutation_sql,
    expected_code,
):
    url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
        source="kol_search_inventory_daily",
        queue_lane="batch",
        maintenance_refresh=True,
    )
    queued = _queued_payload(crawl_conn)
    body = {
        "url": queued["url"],
        "kol_pool_id": 1,
        "maintenance_refresh": True,
        "maintenance_target_fence": queued["maintenance_target_fence"],
        "mode": "account_deep",
        "max_posts": 1,
    }
    calls = {"provider": 0, "profile_write": 0, "run_write": 0}

    def provider_returns_after_merge(*_args, **_kwargs):
        calls["provider"] += 1
        crawl_conn.execute(mutation_sql)
        crawl_conn.commit()
        return {"status": "ok"}

    def wrong_profile_write(*_args, **_kwargs):
        calls["profile_write"] += 1
        raise AssertionError("drifted maintenance result must not update another row")

    def wrong_run_write(*_args, **_kwargs):
        calls["run_write"] += 1
        raise AssertionError("drifted maintenance result must not write a crawl receipt")

    monkeypatch.setattr(url_deep_crawl_execute, "get_conn", lambda: crawl_conn)
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_profile_incremental_state",
        lambda *_args, **_kwargs: {"enabled": True},
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_crawl_profile_basics",
        provider_returns_after_merge,
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "write_kol_profile_basics",
        wrong_profile_write,
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_record_deep_crawl_run",
        wrong_run_write,
    )

    with pytest.raises(video_tracking.VideoTrackingError) as error:
        url_deep_crawl_execute._execute_profile_flow(
            url_deep_crawl.classify_url(queued["url"]),
            [{"kol_pool_id": 1}],
            body,
        )

    assert error.value.code == expected_code
    assert error.value.provider_calls_performed is True
    assert calls == {"provider": 1, "profile_write": 0, "run_write": 0}


def test_maintenance_fence_records_db_native_id_and_rejects_rebound_handle_result(
    crawl_conn,
    monkeypatch,
):
    original_channel_id = "UCaaaaaaaaaaaaaaaaaaaaaa"
    rebound_channel_id = "UCbbbbbbbbbbbbbbbbbbbbbb"
    crawl_conn.execute(
        "UPDATE vkpi_kol_pool SET raw_platform_data=? WHERE id=1",
        (
            json.dumps(
                {"profile": {"items": [{"id": original_channel_id}]}}
            ),
        ),
    )
    crawl_conn.commit()
    url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
        source="kol_search_inventory_daily",
        queue_lane="batch",
        maintenance_refresh=True,
    )
    queued = _queued_payload(crawl_conn)
    assert queued["maintenance_target_fence"]["stable_native_ids"] == {
        "channel_id": original_channel_id,
    }
    body = {
        "url": queued["url"],
        "kol_pool_id": 1,
        "maintenance_refresh": True,
        "maintenance_target_fence": queued["maintenance_target_fence"],
        "mode": "account_deep",
        "max_posts": 1,
    }
    calls = {"provider": 0, "write": 0}

    def provider_result(*_args, **_kwargs):
        calls["provider"] += 1
        return {
            "status": "ok",
            "profile_payload": {"items": [{"id": rebound_channel_id}]},
        }

    def wrong_write(*_args, **_kwargs):
        calls["write"] += 1
        raise AssertionError("rebound handle result must not be persisted")

    monkeypatch.setattr(url_deep_crawl_execute, "get_conn", lambda: crawl_conn)
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_profile_incremental_state",
        lambda *_args, **_kwargs: {"enabled": True},
    )
    monkeypatch.setattr(url_deep_crawl_execute, "_crawl_profile_basics", provider_result)
    monkeypatch.setattr(url_deep_crawl_execute, "write_kol_profile_basics", wrong_write)

    with pytest.raises(video_tracking.VideoTrackingError) as error:
        url_deep_crawl_execute._execute_profile_flow(
            url_deep_crawl.classify_url(queued["url"]),
            [{"kol_pool_id": 1}],
            body,
        )

    assert error.value.code == "maintenance_refresh_provider_identity_mismatch"
    assert calls == {"provider": 1, "write": 0}


def test_maintenance_locked_recheck_uses_postgres_row_lock(monkeypatch):
    executed: list[str] = []

    class Result:
        def fetchone(self):
            return {
                "id": 1,
                "duplicate_of_id": None,
                "platform": "youtube",
                "handle": "@Creator",
                "profile_url": PROFILE_URL,
                "raw_platform_data": "{}",
            }

    class Conn:
        def execute(self, sql, _params=()):
            executed.append(" ".join(str(sql).split()))
            return Result()

    payload = {
        "url": "https://youtube.com/@Creator",
        "kol_pool_id": 1,
        "maintenance_refresh": True,
        "maintenance_target_fence": {
            "version": 1,
            "kind": "kol_search_inventory_daily",
            "kol_pool_id": 1,
            "canonical_profile_url": "https://youtube.com/@Creator",
            "platform": "youtube",
            "stable_identity_key": "youtube:handle:creator",
            "stable_handle": "creator",
            "stable_native_ids": {},
        },
    }
    monkeypatch.setattr(url_deep_crawl_queue, "is_postgres_runtime", lambda: True)

    url_deep_crawl_queue._revalidate_maintenance_target_fence(
        payload,
        conn=Conn(),
        lock_target=True,
    )

    assert executed and executed[0].endswith("LIMIT 1 FOR UPDATE")


def test_maintenance_postprovider_locks_precede_writes_on_same_connection(monkeypatch):
    conn = object()
    events: list[tuple[object, ...]] = []
    fence = {
        "version": 1,
        "kind": "kol_search_inventory_daily",
        "kol_pool_id": 1,
        "canonical_profile_url": "https://youtube.com/@Creator",
        "platform": "youtube",
        "stable_identity_key": "youtube:handle:creator",
        "stable_handle": "creator",
        "stable_native_ids": {},
    }
    body = {
        "url": "https://youtube.com/@Creator",
        "kol_pool_id": 1,
        "maintenance_refresh": True,
        "maintenance_target_fence": fence,
        "mode": "account_deep",
        "max_posts": 12,
    }

    def revalidate(_payload, *, conn=None, lock_target=False):
        events.append(("revalidate", lock_target, conn))
        return {"kol_pool_id": 1}

    def provider(*_args, **_kwargs):
        events.append(("provider",))
        return {
            "status": "ok",
            "profile_payload": {"items": [{"handle": "Creator"}]},
        }

    def profile_write(_kol_id, _profile_data, **kwargs):
        events.append(("profile_write", kwargs.get("conn")))
        assert events[-2] == ("revalidate", True, conn)
        return {
            "kol_pool_id": 1,
            "fields_written": ["followers"],
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    def run_write(write_conn, **_kwargs):
        events.append(("run_write", write_conn))
        assert events[-2] == ("revalidate", True, conn)
        return 99

    monkeypatch.setattr(url_deep_crawl_execute, "get_conn", lambda: conn)
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "_revalidate_maintenance_target_fence",
        revalidate,
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_profile_incremental_state",
        lambda *_args, **_kwargs: {"enabled": True},
    )
    monkeypatch.setattr(url_deep_crawl_execute, "_crawl_profile_basics", provider)
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_profile_data_from_crawl",
        lambda *_args, **_kwargs: {
            "platform": "youtube",
            "handle": "creator",
            "profile_url": "https://youtube.com/@Creator",
        },
    )
    monkeypatch.setattr(url_deep_crawl_execute, "write_kol_profile_basics", profile_write)
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_execute_profile_representative_video_analysis",
        lambda stage_conn, **_kwargs: events.append(("representative", stage_conn))
        or {"worker_touched": False, "viltrox_fit_score_changed_ids": []},
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_execute_profile_history_video_evidence",
        lambda stage_conn, **_kwargs: events.append(("history", stage_conn))
        or {"worker_touched": False, "viltrox_fit_score_changed_ids": []},
    )
    monkeypatch.setattr(url_deep_crawl_execute, "_record_deep_crawl_run", run_write)

    result = url_deep_crawl_execute._execute_profile_flow(
        url_deep_crawl.classify_url(PROFILE_URL),
        [{"kol_pool_id": 1, "handle": "Creator"}],
        body,
    )

    assert result["run_id"] == 99
    assert result["max_posts"] == 1
    assert events == [
        ("revalidate", False, conn),
        ("provider",),
        ("revalidate", True, conn),
        ("profile_write", conn),
        ("revalidate", True, conn),
        ("representative", conn),
        ("history", conn),
        ("revalidate", True, conn),
        ("run_write", conn),
    ]


def test_maintenance_merge_after_profile_commit_blocks_all_followup_writes(
    crawl_conn,
    monkeypatch,
):
    url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
        source="kol_search_inventory_daily",
        queue_lane="batch",
        maintenance_refresh=True,
    )
    queued = _queued_payload(crawl_conn)
    body = {
        "url": queued["url"],
        "kol_pool_id": 1,
        "maintenance_refresh": True,
        "maintenance_target_fence": queued["maintenance_target_fence"],
        "mode": "account_deep",
        "max_posts": 1,
    }
    calls = {"provider": 0, "profile_write": 0, "later_write": 0}

    def provider(*_args, **_kwargs):
        calls["provider"] += 1
        return {
            "status": "ok",
            "profile_payload": {"items": [{"handle": "Creator"}]},
        }

    def profile_write(*_args, **_kwargs):
        calls["profile_write"] += 1
        crawl_conn.execute("UPDATE vkpi_kol_pool SET duplicate_of_id=2 WHERE id=1")
        crawl_conn.commit()
        return {
            "kol_pool_id": 1,
            "fields_written": ["followers"],
            "viltrox_fit_score_changed_ids": [],
        }

    def later_write(*_args, **_kwargs):
        calls["later_write"] += 1
        raise AssertionError("merged target must block evidence/follow-up/run writes")

    monkeypatch.setattr(url_deep_crawl_execute, "get_conn", lambda: crawl_conn)
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_profile_incremental_state",
        lambda *_args, **_kwargs: {"enabled": True},
    )
    monkeypatch.setattr(url_deep_crawl_execute, "_crawl_profile_basics", provider)
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_profile_data_from_crawl",
        lambda *_args, **_kwargs: {
            "platform": "youtube",
            "handle": "creator",
            "profile_url": "https://youtube.com/@Creator",
        },
    )
    monkeypatch.setattr(url_deep_crawl_execute, "write_kol_profile_basics", profile_write)
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_execute_profile_representative_video_analysis",
        later_write,
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_execute_profile_history_video_evidence",
        later_write,
    )
    monkeypatch.setattr(url_deep_crawl_execute, "_record_deep_crawl_run", later_write)

    with pytest.raises(video_tracking.VideoTrackingError) as error:
        url_deep_crawl_execute._execute_profile_flow(
            url_deep_crawl.classify_url(queued["url"]),
            [{"kol_pool_id": 1, "handle": "Creator"}],
            body,
        )

    assert error.value.code == "maintenance_refresh_target_merged"
    assert error.value.provider_calls_performed is True
    assert calls == {"provider": 1, "profile_write": 1, "later_write": 0}


@pytest.mark.parametrize("provider_truth", [False, None])
def test_durable_gate_return_persists_provider_truth(monkeypatch, provider_truth):
    stored: dict[str, object] = {}
    payload = {"maintenance_refresh": True, "kol_pool_id": 1}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=()):
            stored["sql"] = " ".join(str(sql).split())
            stored["status"] = params[0]
            stored["last_error"] = params[1]
            stored["payload"] = json.loads(params[2])

    class WorkerConn:
        def transaction(self):
            return nullcontext()

        def cursor(self, **_kwargs):
            return Cursor()

    monkeypatch.setattr(worker_handlers, "_resolve_job_staff", lambda *_args: {})
    monkeypatch.setattr(worker_handlers, "db_connection_sync_scope", nullcontext)
    monkeypatch.setattr(
        url_deep_crawl,
        "run_profile_deep_crawl_for_job",
        lambda *_args, **_kwargs: {
            "status": "maintenance_refresh_task_disabled",
            "provider_calls_performed": provider_truth,
        },
    )
    monkeypatch.setattr(
        worker_handlers.deep_crawl_worker,
        "record_monitor_terminal",
        lambda *_args, **_kwargs: None,
    )

    worker_handlers._process_kol_profile_deep_crawl(
        WorkerConn(),
        {"id": 41},
        payload,
    )

    assert stored["status"] == "blocked"
    assert stored["last_error"] == "maintenance_refresh_task_disabled"
    assert stored["payload"]["provider_calls_performed"] is provider_truth

