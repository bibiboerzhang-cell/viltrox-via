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
    order: list[str] = []
    queued_payload: dict = {}
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
        lambda **kwargs: (
            order.append("enqueue"),
            queued_payload.update(kwargs),
            {
                "status": "queued",
                "job_id": 901,
                "write_db": True,
                "ai_analysis": {"state": "queued", "provider_calls_allowed": True},
            },
        )[-1],
    )
    ensure_calls: list[dict] = []

    def fake_ensure(**kwargs):
        ensure_calls.append(dict(kwargs))
        return None if len(ensure_calls) == 1 else {"id": 42}

    def fake_attach(session_id, result):
        order.append("attach")
        return {
            "id": session_id,
            "items": [
                {
                    "id": 77,
                    "item_type": "url_video",
                    "kol_pool_id": 88,
                    "evidence_id": 7,
                }
            ],
        }

    monkeypatch.setattr(router_module.kol_search_sessions, "ensure_session_for_result", fake_ensure)
    monkeypatch.setattr(router_module.kol_search_sessions, "attach_url_result", fake_attach)

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
    assert ensure_calls[0]["create"] is False
    assert ensure_calls[1]["create"] is True
    assert order[:2] == ["attach", "enqueue"]
    assert queued_payload["search_session_id"] == 42
    assert queued_payload["search_session_item_id"] == 77


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


def test_unresolved_video_item_is_persisted_before_resolver_enqueue(monkeypatch):
    order: list[str] = []
    captured: dict = {}
    normalized_url = "https://www.youtube.com/watch?v=abcdefghijk"
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "classify_url",
        lambda _url: SimpleNamespace(url_type="video", platform="youtube"),
    )
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda _body: {
            "execute": False,
            "url_type": "video",
            "platform": "youtube",
            "url": {"input": normalized_url, "normalized": normalized_url},
            "matched_kol_pool_id": None,
            "video_flow": {"status": "provider_refresh_pending"},
        },
    )
    monkeypatch.setattr(
        router_module.kol_search_sessions,
        "ensure_session_for_result",
        lambda **_kwargs: {"id": 55},
    )

    def fake_attach(session_id, result):
        job_id = (result.get("video_flow") or {}).get("job_id")
        order.append(f"attach:{job_id or 'pending'}")
        return {
            "session_id": session_id,
            "items": [
                {
                    "id": 66,
                    "item_type": "url_video",
                    "source_url": normalized_url,
                    "job_id": job_id,
                }
            ],
        }

    def fake_enqueue(_url, **kwargs):
        order.append("enqueue")
        captured.update(kwargs)
        return {
            "status": "queued",
            "job_id": 9911,
            "job_type": "video_url_resolve",
            "write_db": True,
            "provider_calls_performed": False,
            "resolution_progress": {"status": "queued"},
            "ai_analysis": {"state": "waiting_for_evidence"},
        }

    monkeypatch.setattr(router_module.kol_search_sessions, "attach_url_result", fake_attach)
    monkeypatch.setattr(router_module.kol_url_deep_crawl, "enqueue_video_url_resolve_job", fake_enqueue)

    router_module._run_url_deep_crawl(
        {"url": normalized_url, "execute": True},
        staff={"id": 12, "user_id": 34},
        default_defer_profile=True,
        default_create_session=True,
        default_source="smart_kol_input_auto",
    )

    assert order[:2] == ["attach:pending", "enqueue"]
    assert captured["search_session_id"] == 55
    assert captured["search_session_item_id"] == 66


def test_ambiguous_video_items_do_not_enqueue_resolver(monkeypatch):
    normalized_url = "https://www.youtube.com/watch?v=abcdefghijk"
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "classify_url",
        lambda _url: SimpleNamespace(url_type="video", platform="youtube"),
    )
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda _body: {
            "execute": False,
            "url_type": "video",
            "platform": "youtube",
            "url": {"input": normalized_url, "normalized": normalized_url},
            "matched_kol_pool_id": None,
            "video_flow": {"status": "provider_refresh_pending"},
        },
    )
    monkeypatch.setattr(
        router_module.kol_search_sessions,
        "ensure_session_for_result",
        lambda **_kwargs: {"id": 55},
    )
    monkeypatch.setattr(
        router_module.kol_search_sessions,
        "attach_url_result",
        lambda *_args, **_kwargs: {
            "items": [
                {"id": 66, "item_type": "url_video", "source_url": normalized_url},
                {"id": 67, "item_type": "url_video", "source_url": normalized_url},
            ]
        },
    )
    monkeypatch.setattr(
        router_module.kol_url_deep_crawl,
        "enqueue_video_url_resolve_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambiguous session items must not enqueue provider work")
        ),
    )

    with pytest.raises(RuntimeError, match="video_url_resolve_session_item_required"):
        router_module._run_url_deep_crawl(
            {"url": normalized_url, "execute": True},
            staff={"id": 12, "user_id": 34},
            default_defer_profile=True,
            default_create_session=True,
            default_source="smart_kol_input_auto",
        )


@pytest.mark.parametrize(
    ("shared_url", "stored_public_url"),
    [
        (
            "https://www.youtube.com/watch?v=abcdefghijk&utm_source=share",
            "https://www.youtube.com/watch?v=abcdefghijk",
        ),
        (
            "https://www.instagram.com/reel/ABC123/?igsh=token&utm_source=share",
            "https://www.instagram.com/reel/ABC123/",
        ),
        (
            "https://www.tiktok.com/@creator/video/1234567890123456789?_t=token&_r=1",
            "https://www.tiktok.com/@creator/video/1234567890123456789",
        ),
    ],
)
def test_video_resolver_item_match_uses_the_persistence_url_projection(
    monkeypatch,
    shared_url,
    stored_public_url,
):
    monkeypatch.setattr(
        router_module.kol_search_sessions,
        "attach_url_result",
        lambda *_args, **_kwargs: {
            "items": [
                {
                    "id": 66,
                    "item_type": "url_video",
                    "source_url": stored_public_url,
                }
            ]
        },
    )

    item_id = router_module._prepare_video_resolver_session_item(
        {"id": 55},
        {
            "url_type": "video",
            "url": {"input": shared_url, "normalized": shared_url},
        },
    )

    assert item_id == 66


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
