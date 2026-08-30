"""Provider-free regression tests for URL search history/session mapping."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class _Rows:
    def __init__(self, value: Any) -> None:
        self.value = value

    def fetchone(self):
        return self.value


class _RawCommitSpy:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _LinkConn:
    def __init__(
        self,
        status: str,
        *,
        legacy_scalar_link: bool = False,
        terminal_after_lineage_commit: str = "",
    ) -> None:
        payload: dict[str, Any] = {"target_id": "3951"}
        if legacy_scalar_link:
            payload.update(
                {
                    "search_session_id": 1085,
                    "search_session_item_id": 2312,
                }
            )
        self.row: dict[str, Any] = {
            "id": 18428,
            "payload": payload,
            "status": status,
            "last_error": "model_binding_blocked" if status == "blocked" else "",
        }
        self.commits = 0
        self.terminal_after_lineage_commit = terminal_after_lineage_commit
        self.sql: list[str] = []
        self.raw = _RawCommitSpy()
        self._raw = self.raw

    def execute(self, sql: str, params=()):
        compact = " ".join(str(sql).split())
        self.sql.append(compact)
        if compact.startswith("SELECT id, payload, status, last_error FROM apify_jobs"):
            return _Rows(dict(self.row))
        if compact.startswith("SELECT id, status, last_error FROM apify_jobs"):
            return _Rows(
                {
                    "id": self.row["id"],
                    "status": self.row["status"],
                    "last_error": self.row["last_error"],
                }
            )
        if compact.startswith("UPDATE apify_jobs SET payload="):
            self.row["payload"] = json.loads(str(params[0]))
            return _Rows(None)
        raise AssertionError(compact)

    def commit(self) -> None:
        self.commits += 1
        if self.commits == 1 and self.terminal_after_lineage_commit:
            self.row["status"] = self.terminal_after_lineage_commit
            self.row["last_error"] = (
                "finished_during_lineage_commit"
                if self.terminal_after_lineage_commit != "done"
                else ""
            )


@pytest.mark.parametrize("terminal_status", ["done", "blocked"])
def test_terminal_video_job_is_replayed_after_late_session_attach(
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    from app.domains.kol import search_session_job_sync, search_sessions_attach
    from app.domains.tasks.search_session_lineage import search_session_lineages

    conn = _LinkConn(terminal_status)
    synced: list[dict[str, Any]] = []
    monkeypatch.setattr(search_sessions_attach, "get_conn", lambda: conn)
    monkeypatch.setattr(
        search_session_job_sync,
        "sync_search_session_job",
        lambda raw_conn, job_id, **kwargs: synced.append(
            {"conn": raw_conn, "job_id": job_id, **kwargs}
        ) or True,
    )
    item = {
        "id": 2312,
        "job_id": 18428,
        "item_type": "url_video",
        "status": "queued",
        "stage": "analysis",
    }

    assert search_sessions_attach._link_job_payloads(1085, [item]) == 1

    assert conn.commits == 2
    assert conn.row["payload"]["search_session_role"] == "video"
    assert search_session_lineages(conn.row["payload"]) == [
        {
            "search_session_id": 1085,
            "search_session_item_id": 2312,
            "role": "video",
        }
    ]
    assert synced == [
        {
            "conn": conn.raw,
            "job_id": 18428,
            "raw_status": terminal_status,
            "reason": "model_binding_blocked" if terminal_status == "blocked" else "",
        }
    ]
    assert conn.raw.commits == 1


def test_terminal_transition_during_lineage_commit_is_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.kol import search_session_job_sync, search_sessions_attach

    conn = _LinkConn("running", terminal_after_lineage_commit="done")
    synced: list[dict[str, Any]] = []
    monkeypatch.setattr(search_sessions_attach, "get_conn", lambda: conn)
    monkeypatch.setattr(
        search_session_job_sync,
        "sync_search_session_job",
        lambda raw_conn, job_id, **kwargs: synced.append(
            {"conn": raw_conn, "job_id": job_id, **kwargs}
        ) or True,
    )

    linked = search_sessions_attach._link_job_payloads(
        1085,
        [
            {
                "id": 2312,
                "job_id": 18428,
                "item_type": "url_video",
                "status": "queued",
                "stage": "analysis",
            }
        ],
    )

    assert linked == 1
    assert conn.commits == 2
    assert synced == [
        {
            "conn": conn.raw,
            "job_id": 18428,
            "raw_status": "done",
            "reason": "",
        }
    ]
    assert conn.raw.commits == 1


def test_postgres_link_merge_takes_job_row_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import search_sessions_attach

    conn = _LinkConn("running")
    monkeypatch.setattr(search_sessions_attach, "get_conn", lambda: conn)
    monkeypatch.setattr(search_sessions_attach, "is_postgres_runtime", lambda: True)

    search_sessions_attach._link_job_payloads(
        1085,
        [
            {
                "id": 2312,
                "job_id": 18428,
                "item_type": "url_video",
                "status": "queued",
                "stage": "analysis",
            }
        ],
    )

    assert any(
        sql.endswith("WHERE id=? FOR UPDATE")
        for sql in conn.sql
        if sql.startswith("SELECT id, payload, status, last_error")
    )


def test_terminal_replay_failure_is_observable_and_not_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.kol import search_session_job_sync, search_sessions_attach

    conn = _LinkConn("done")
    monkeypatch.setattr(
        search_session_job_sync,
        "sync_search_session_job",
        lambda *_a, **_k: False,
    )

    replayed = search_sessions_attach._sync_linked_terminal_job(
        conn,
        job_id=18428,
        status="done",
    )

    assert replayed is False
    assert conn.raw.commits == 0
    assert conn.raw.rollbacks == 1


def test_worker_sync_wrapper_reports_impl_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import apify_jobs_worker_session

    def _boom(*_args, **_kwargs):
        raise RuntimeError("sync write failed")

    monkeypatch.setattr(apify_jobs_worker_session, "_sync_search_session_job_impl", _boom)

    assert (
        apify_jobs_worker_session._sync_search_session_job(
            object(),
            18428,
            raw_status="done",
        )
        is False
    )


def test_worker_sync_wrapper_reports_no_lineage_as_not_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import apify_jobs_worker_session

    monkeypatch.setattr(
        apify_jobs_worker_session,
        "_sync_search_session_job_impl",
        lambda *_a, **_k: 0,
    )

    assert (
        apify_jobs_worker_session._sync_search_session_job(
            object(),
            18428,
            raw_status="done",
        )
        is False
    )


def test_worker_sync_compatibility_facade_uses_domain_owned_impl() -> None:
    from app.domains.kol import search_session_job_analysis
    from app.domains.kol import search_session_job_lineage
    from app.domains.kol import search_session_job_sync
    from app.workers import apify_jobs_worker_lineage
    from app.workers import apify_jobs_worker_session
    from app.workers import apify_jobs_worker_session_cache

    assert (
        apify_jobs_worker_session._sync_search_session_job_impl
        is search_session_job_sync.sync_search_session_job_impl
    )
    assert (
        apify_jobs_worker_lineage._lineage_item_state
        is search_session_job_lineage.lineage_item_state
    )
    assert (
        apify_jobs_worker_session_cache.search_session_analysis_summary_from_ready_cache
        is search_session_job_analysis.search_session_analysis_summary_from_ready_cache
    )


def test_late_replay_terminal_statuses_match_real_apify_job_states() -> None:
    from app.domains.kol import search_sessions_attach

    assert search_sessions_attach._TERMINAL_LINKED_JOB_STATUSES == {
        "done",
        "failed",
        "blocked",
        "triage",
    }


def test_repeated_terminal_attach_keeps_one_lineage_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.kol import search_sessions_attach
    from app.domains.tasks.search_session_lineage import search_session_lineages

    conn = _LinkConn("done", legacy_scalar_link=True)
    monkeypatch.setattr(search_sessions_attach, "get_conn", lambda: conn)
    monkeypatch.setattr(search_sessions_attach, "_sync_linked_terminal_job", lambda *_args, **_kwargs: None)
    item = {
        "id": 2312,
        "job_id": 18428,
        "item_type": "url_video",
        "status": "queued",
        "stage": "analysis",
    }

    search_sessions_attach._link_job_payloads(1085, [item])
    search_sessions_attach._link_job_payloads(1085, [item])

    assert search_session_lineages(conn.row["payload"]) == [
        {
            "search_session_id": 1085,
            "search_session_item_id": 2312,
            "role": "video",
        }
    ]


def test_compact_flow_keeps_public_history_mapping_and_drops_private_fields() -> None:
    from app.domains.kol.search_sessions_serde import _compact_flow

    compact = _compact_flow(
        {
            "status": "ready",
            "cached_video_url": "/api/vkpi-media/video-cache/abc123",
            "profile_data": {
                "platform": "youtube",
                "handle": "ItiJarve",
                "display_name": "Iti Jarve",
                "profile_url": "https://www.youtube.com/@ItiJarve",
                "avatar_url": "https://images.example/avatar.jpg",
                "followers": 87271,
                "posts_count": 94,
                "bio": "Camera creator",
                "raw_platform_data": {"provider_payload": "must-not-leak"},
                "email": "private@example.com",
                "access_token": "must-not-leak",
            },
            "cache_status": "cached",
            "cache_error": "",
            "media_cache": {
                "status": "cached",
                "cached": True,
                "storage_backend": "r2",
                "updated_at": "2026-07-14T12:00:00Z",
                "r2_key": "private/storage/key.mp4",
                "local_path": "/private/cache/file.mp4",
                "source_url": "https://provider.example/private",
            },
        }
    )

    assert compact["cached_video_url"] == "/api/vkpi-media/video-cache/abc123"
    assert compact["profile_data"] == {
        "platform": "youtube",
        "handle": "ItiJarve",
        "display_name": "Iti Jarve",
        "profile_url": "https://www.youtube.com/@ItiJarve",
        "avatar_url": "https://images.example/avatar.jpg",
        "followers": 87271,
        "posts_count": 94,
        "bio": "Camera creator",
    }
    assert compact["cache_status"] == "cached"
    assert compact["media_cache"] == {
        "status": "cached",
        "cached": True,
        "storage_backend": "r2",
        "updated_at": "2026-07-14T12:00:00Z",
    }
    serialized = json.dumps(compact, ensure_ascii=False)
    assert "provider_payload" not in serialized
    assert "private@example.com" not in serialized
    assert "must-not-leak" not in serialized
    assert "private/storage/key.mp4" not in serialized
    assert "/private/cache/file.mp4" not in serialized


def test_url_history_item_rebuild_keeps_profile_card_and_video_player_fields() -> None:
    from app.domains.kol.search_sessions_attach import _url_result_item

    item = _url_result_item(
        1085,
        {
            "execute": True,
            "url": {"normalized": "https://www.instagram.com/p/DX8prCJOe6V/"},
            "url_type": "video",
            "platform": "instagram",
            "video_id": "DX8prCJOe6V",
            "profile_flow": {
                "status": "ready",
                "kol_pool_id": 14060,
                "profile_data": {
                    "handle": "decadentdepictions",
                    "avatar_url": "https://images.example/creator.jpg",
                    "followers": 87271,
                    "email": "private@example.com",
                },
            },
            "video_flow": {
                "status": "queued",
                "kol_pool_id": 14060,
                "evidence_id": 3951,
                "cached_video_url": "/api/vkpi-media/video-cache/digest",
                "enqueue_result": {"job": {"id": 18428}},
            },
        },
    )

    payload = item["payload"]
    assert payload["profile_flow"]["profile_data"] == {
        "handle": "decadentdepictions",
        "avatar_url": "https://images.example/creator.jpg",
        "followers": 87271,
    }
    assert payload["video_flow"]["cached_video_url"] == "/api/vkpi-media/video-cache/digest"
    assert "private@example.com" not in json.dumps(payload)


def test_compact_flow_refuses_presigned_cache_credentials_and_redacts_raw_error() -> None:
    from app.domains.kol.search_sessions_serde import _compact_flow

    compact = _compact_flow(
        {
            "cached_video_url": "https://r2.example/video.mp4?X-Amz-Signature=secret",
            "media_cache_error": "provider failed with token=secret",
        }
    )

    assert "cached_video_url" not in compact
    assert compact["media_cache_error"] == "media_cache_failed"
    assert "secret" not in json.dumps(compact)
