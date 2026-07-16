from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool_search as router_module
from app.domains.kol import search_sessions_attach
from app.domains.kol import url_deep_crawl_queue


def _profile_result() -> dict:
    return {
        "dry_run": True,
        "execute": False,
        "url": {
            "input": "https://www.youtube.com/@ItiJarve",
            "normalized": "https://www.youtube.com/@ItiJarve",
        },
        "url_type": "profile",
        "platform": "youtube",
        "handle": "itijarve",
        "in_pool": False,
        "matched_kol_pool_id": None,
        "profile_flow": {"status": "dry_run_ready", "operation": "insert"},
    }


def test_profile_execute_defaults_to_worker_and_keeps_search_session(monkeypatch):
    seen: dict = {}

    def fake_crawl(body):
        seen["crawl_body"] = dict(body)
        return _profile_result()

    def fake_ensure(**kwargs):
        seen["ensure"] = kwargs
        return {"id": 42}

    def fake_enqueue(url, **kwargs):
        seen["enqueue"] = {"url": url, **kwargs}
        return {"status": "queued", "job_id": 7001}

    def fake_attach(session_id, result):
        seen["attach"] = {"session_id": session_id, "result": result}
        return {"session_id": session_id, "items": []}

    monkeypatch.setattr(router_module.kol_url_deep_crawl, "dry_run_url_deep_crawl", fake_crawl)
    monkeypatch.setattr(router_module.kol_url_deep_crawl, "profile_deep_crawl_is_fresh", lambda _kol_id: False)
    monkeypatch.setattr(router_module.kol_url_deep_crawl, "enqueue_profile_deep_crawl_job", fake_enqueue)
    monkeypatch.setattr(router_module.kol_search_sessions, "ensure_session_for_result", fake_ensure)
    monkeypatch.setattr(router_module.kol_search_sessions, "attach_url_result", fake_attach)

    result = router_module.dry_run_kol_url_deep_crawl(
        {
            "url": "https://www.youtube.com/@ItiJarve",
            "execute": True,
            "create_session": True,
            "source": "smart_kol_input_auto",
            "mode": "profile_with_video",
            "representative_video_limit": 2,
        },
        staff={"id": 1, "user_id": 1},
    )

    assert seen["crawl_body"]["execute"] is False
    assert seen["enqueue"]["search_session_id"] == 42
    assert seen["enqueue"]["source"] == "smart_kol_input_auto"
    assert seen["enqueue"]["mode"] == "profile_with_video"
    assert seen["enqueue"]["representative_video_limit"] == 2
    assert result["execute"] is True
    assert result["deferred_to_queue"] is True
    assert result["provider_calls_performed"] is False
    assert result["profile_flow"]["status"] == "queued"
    assert result["profile_flow"]["job_id"] == 7001
    assert result["enrichment"]["contacts"]["status"] == "pending"
    assert result["enrichment"]["audience"]["status"] == "pending"


class _QueueConn:
    def __init__(self) -> None:
        self.insert_payload: dict | None = None
        self.commits = 0

    def execute(self, sql, params=()):
        if "SELECT id FROM apify_jobs" in sql:
            return _Rows(None)
        if "INSERT INTO apify_jobs" in sql:
            self.insert_payload = json.loads(params[1] if len(params) > 1 else params[0])
            return _Rows({"id": 7002})
        raise AssertionError(sql)

    def commit(self):
        self.commits += 1


class _Rows:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def test_queue_json_keeps_profile_execution_contract(monkeypatch):
    conn = _QueueConn()
    monkeypatch.setattr(url_deep_crawl_queue, "get_conn", lambda: conn)

    result = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        "https://www.youtube.com/@ItiJarve",
        max_posts=3,
        mode="profile_with_video",
        representative_video_limit=2,
        staff={"id": 1},
    )

    assert result == {"status": "queued", "job_id": 7002}
    assert conn.insert_payload is not None
    assert conn.insert_payload["mode"] == "profile_with_video"
    assert conn.insert_payload["representative_video_limit"] == 2
    assert conn.commits == 1


def test_worker_body_keeps_new_contract_and_legacy_defaults(monkeypatch):
    from app.domains.kol import search_sessions
    from app.domains.kol import url_deep_crawl

    bodies: list[dict] = []
    monkeypatch.setattr(url_deep_crawl, "dry_run_url_deep_crawl", lambda body: bodies.append(dict(body)) or {"status": "ready"})
    monkeypatch.setattr(search_sessions, "ensure_session_for_result", lambda **_kwargs: None)

    url_deep_crawl_queue.run_profile_deep_crawl_for_job(
        {
            "url": "https://www.youtube.com/@ItiJarve",
            "max_posts": 3,
            "mode": "profile_with_video",
            "representative_video_limit": 2,
            "source": "smart_kol_input_auto",
        }
    )
    url_deep_crawl_queue.run_profile_deep_crawl_for_job(
        {"url": "https://www.youtube.com/@legacy", "max_posts": 3}
    )

    assert bodies[0]["mode"] == "profile_with_video"
    assert bodies[0]["representative_video_limit"] == 2
    assert bodies[0]["source"] == "smart_kol_input_auto"
    assert bodies[1]["mode"] == "account_deep"
    assert bodies[1]["representative_video_limit"] == 1


def test_profile_queue_job_is_linked_to_url_session_item():
    result = _profile_result()
    result.update(
        {
            "execute": True,
            "profile_flow": {
                "status": "queued",
                "operation": "profile_deep_crawl_queue",
                "job_id": 7001,
            },
        }
    )

    item = search_sessions_attach._url_result_item(42, result)

    assert item["job_id"] == 7001
    assert item["status"] == "queued"
    assert item["stage"] == "profile"


def test_recent_profile_is_reused_without_provider_or_new_job(monkeypatch):
    monkeypatch.setattr(router_module.kol_url_deep_crawl, "dry_run_url_deep_crawl", lambda _body: {
        **_profile_result(),
        "in_pool": True,
        "matched_kol_pool_id": 13053,
    })
    monkeypatch.setattr(router_module.kol_url_deep_crawl, "profile_deep_crawl_is_fresh", lambda _kol_id: True)
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fresh profile must not enqueue")),
    )
    monkeypatch.setattr(router_module.kol_search_sessions, "ensure_session_for_result", lambda **_kwargs: None)

    result = router_module.dry_run_kol_url_deep_crawl(
        {
            "url": "https://www.youtube.com/@ItiJarve",
            "execute": True,
            "defer_to_queue": True,
            "create_session": False,
        },
        staff={"id": 1, "user_id": 1},
    )

    assert result["profile_flow"]["status"] == "ready"
    assert result["profile_flow"]["operation"] == "reuse_recent_profile"
    assert result["worker_touched"] is False
    assert result["provider_calls_performed"] is False


def test_video_execute_with_defer_flag_is_durable_and_request_stays_provider_free(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "classify_url",
        lambda _url: SimpleNamespace(url_type="video", platform="youtube"),
    )

    def fake_crawl(body):
        seen.append(dict(body))
        return {
            "execute": bool(body.get("execute")),
            "url_type": "video",
            "platform": "youtube",
            "matched_kol_pool_id": 88,
            "video_flow": {"status": "ready_to_execute", "evidence_id": 7},
        }

    monkeypatch.setattr(router_module.kol_url_deep_crawl, "dry_run_url_deep_crawl", fake_crawl)
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stored video evidence must not trigger profile deep crawl")
        ),
    )
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "enqueue_stored_video_analysis_job",
        lambda **_kwargs: {
            "status": "queued",
            "job_id": 901,
            "write_db": True,
            "ai_analysis": {"state": "queued", "provider_calls_allowed": True},
        },
    )
    monkeypatch.setattr(router_module.kol_search_sessions, "ensure_session_for_result", lambda **_kwargs: None)

    result = router_module.dry_run_kol_url_deep_crawl(
        {
            "url": "https://www.youtube.com/watch?v=abc123",
            "execute": True,
            "defer_to_queue": True,
        },
        staff={"id": 1},
    )

    assert len(seen) == 1
    assert seen[0]["execute"] is False
    assert result["video_flow"]["status"] == "queued"
    assert result["video_flow"]["job_id"] == 901
    assert result["video_flow"]["evidence_id"] == 7
    assert result["video_flow"]["operation"] == "existing_creator_video_analysis"
    assert result["video_flow"]["enqueue_result"]["job_id"] == 901
    assert result["provider_calls_performed"] is False


def test_unresolved_video_never_enters_profile_deep_crawl_queue(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "classify_url",
        lambda _url: SimpleNamespace(url_type="video", platform="youtube"),
    )
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda body: {
            "execute": bool(body.get("execute")),
            "url_type": "video",
            "platform": "youtube",
            "matched_kol_pool_id": None,
            "video_flow": {"status": "provider_refresh_pending"},
        },
    )
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("video URL must never enter the profile deep-crawl queue")
        ),
    )
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "enqueue_stored_video_analysis_job",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("unresolved evidence must not enqueue final_v1")
        ),
    )
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "enqueue_video_url_resolve_job",
        lambda url, **kwargs: seen.update({"url": url, **kwargs}) or {
            "status": "queued",
            "job_id": 9901,
            "job_type": "video_url_resolve",
            "write_db": True,
            "provider_calls_performed": False,
            "resolution_progress": {
                "status": "queued",
                "base_status": "pending",
                "current_step": "resolve_video",
                "steps": [
                    {"key": "resolve_video", "label": "解析视频", "status": "queued"},
                    {"key": "identify_creator", "label": "识别作者", "status": "pending"},
                    {"key": "cache_media", "label": "缓存媒体", "status": "pending"},
                    {"key": "ai_analysis", "label": "AI分析", "status": "pending"},
                ],
            },
            "ai_analysis": {"state": "waiting_for_evidence"},
        },
    )
    monkeypatch.setattr(router_module.kol_search_sessions, "ensure_session_for_result", lambda **_kwargs: None)

    result = router_module.dry_run_kol_url_deep_crawl(
        {
            "url": "https://www.youtube.com/watch?v=unknown123",
            "execute": True,
            "mode": "video_deep",
        },
        staff={"id": 1},
    )

    assert result["status"] == "queued"
    assert result["deferred_to_queue"] is True
    assert result["worker_touched"] is True
    assert result["writes_performed"] is True
    assert result["enrichment"] is None
    assert result["video_flow"]["status"] == "queued"
    assert result["video_flow"]["operation"] == "video_url_resolve_queue"
    assert result["video_flow"]["job_id"] == 9901
    assert result["video_flow"]["ai_analysis"]["state"] == "waiting_for_evidence"
    assert [step["label"] for step in result["video_flow"]["resolution_progress"]["steps"]] == [
        "解析视频",
        "识别作者",
        "缓存媒体",
        "AI分析",
    ]
    assert seen["source"] == "kol_url_deep_crawl_video_resolve"


def test_smart_url_unexpected_failure_is_diagnostic_503_not_500(monkeypatch):
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda _body: (_ for _ in ()).throw(TypeError("malformed provider payload")),
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            router_module.smart_kol_search(
                {"input": "https://www.youtube.com/@creator", "execute": False},
                staff={"id": 1},
            )
        )

    assert raised.value.status_code == 503
    assert "未被标记为完成" in str(raised.value.detail)
