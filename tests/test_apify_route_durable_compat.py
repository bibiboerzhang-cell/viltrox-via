from __future__ import annotations

import asyncio
import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool_search as search_routes
from app.api.routers import (
    account_scanner,
    deepsight,
    kol_ops,
    verify,
    vkpi_comment_intelligence,
    vkpi_comments,
    vkpi_industry_automation,
    vkpi_kol_pool,
    vkpi_kol_pool_jobs,
    vkpi_operations,
    vkpi_projects,
)
from app.domains.sync import cron as sync_cron
from app.domains.sync import daily_sync
from app.services.jobs.queue_inprocess import InProcessJobQueue
from app.services.jobs.processor import JOB_HANDLERS


class FakeQueue:
    backend_name = "redis-stream"

    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict, dict]] = []

    async def enqueue(self, job_type: str, payload: dict, **kwargs):
        self.jobs.append((job_type, payload, kwargs))
        return f"job-{len(self.jobs)}"


def _request(queue: FakeQueue | None = None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_queue=queue)))


def test_profile_item_execute_ignores_sync_flag_and_enqueues(monkeypatch):
    owner_checks: list[tuple[int, int, bool]] = []

    def current_staff_session(session_id, *, staff, scope_to_staff):
        owner_checks.append((int(session_id), int(staff["id"]), bool(scope_to_staff)))
        return {"id": int(session_id), "created_by": int(staff["id"]), "items": []}

    monkeypatch.setattr(search_routes.kol_search_sessions, "get_session", current_staff_session)
    monkeypatch.setattr(
        search_routes.kol_search_sessions,
        "require_session_owner",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        search_routes.kol_profile_discovery,
        "enqueue_search_session_advance",
        lambda **_kwargs: {"status": "queued", "job_id": 71},
    )
    monkeypatch.setattr(
        search_routes.kol_profile_discovery,
        "execute_profile_crawl_for_session_item",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("sync provider path must not run")),
    )

    result = search_routes.execute_kol_search_session_item_profile_crawl(
        11,
        12,
        {"execute": True, "defer_to_queue": False},
        staff={"id": 1},
    )

    assert result["status"] == "queued"
    assert result["deferred_to_queue"] is True
    assert result["provider_calls_performed"] is False
    assert owner_checks == [(11, 1, True)]


def test_profile_advance_execute_ignores_sync_flag_and_enqueues(monkeypatch):
    owner_checks: list[tuple[int, int, bool]] = []

    def current_staff_session(session_id, *, staff, scope_to_staff):
        owner_checks.append((int(session_id), int(staff["id"]), bool(scope_to_staff)))
        return {"id": int(session_id), "created_by": int(staff["id"]), "items": []}

    monkeypatch.setattr(search_routes.kol_search_sessions, "get_session", current_staff_session)
    monkeypatch.setattr(
        search_routes.kol_search_sessions,
        "require_session_owner",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        search_routes.kol_profile_discovery,
        "enqueue_search_session_advance",
        lambda **_kwargs: {"status": "queued", "job_id": 72},
    )
    monkeypatch.setattr(
        search_routes.kol_profile_discovery,
        "advance_search_session_items",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("sync provider path must not run")),
    )

    result = search_routes.advance_kol_search_session_items(
        11,
        {"execute": True, "defer_to_queue": False},
        staff={"id": 1},
    )

    assert result["status"] == "queued"
    assert result["deferred_to_queue"] is True
    assert owner_checks == [(11, 1, True)]


def test_profile_advance_job_ignores_queue_pipeline_false(monkeypatch):
    monkeypatch.setattr(
        search_routes.kol_profile_discovery,
        "enqueue_smart_search_profile_advance",
        lambda **_kwargs: {"status": "queued", "job": {"id": 73}},
    )
    monkeypatch.setattr(
        search_routes.kol_smart_query_planner,
        "plan_text_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("request-side planner must not run")),
    )

    result = asyncio.run(
        search_routes.smart_kol_search_profile_advance_job(
            {"input": "overseas camera creators", "queue_pipeline": False},
            staff={"id": 1},
        )
    )

    assert result["status"] == "queued"
    assert result["provider_calls"] is False
    assert result["branch"] == "kol_recall_profile_advance_pipeline"


def test_smart_search_execute_new_discovery_enqueues_provider_phase(monkeypatch):
    monkeypatch.setattr(
        search_routes.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {"status": "ok", "search_query": "camera creators"},
    )
    monkeypatch.setattr(
        search_routes.kol_profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: {"items": [], "buckets": {"creator": [], "reviewer": []}, "diagnostics": {}},
    )
    monkeypatch.setattr(search_routes.kol_profile_discovery, "filter_recall_result_platforms", lambda value, _platforms: value)
    monkeypatch.setattr(
        search_routes,
        "_attach_smart_recall_session",
        lambda **kwargs: {**kwargs["result"], "search_session": {"id": 55}},
    )
    monkeypatch.setattr(
        search_routes.kol_profile_discovery,
        "discover_new_creators",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("request-side discovery must not run")),
    )
    monkeypatch.setattr(
        search_routes.kol_profile_discovery,
        "enqueue_smart_search_profile_advance",
        lambda **_kwargs: {"status": "queued", "job": {"id": 74}},
    )

    result = asyncio.run(
        search_routes.smart_kol_search(
            {
                "input": "camera creators",
                "create_session": False,
                "include_new_discovery": True,
                "execute_new_discovery": True,
            },
            staff={"id": 1},
        )
    )

    assert result["new_discovery"]["status"] == "queued"
    assert result["new_discovery"]["job_id"] == 74
    assert result["new_discovery"]["provider_calls_performed"] is False


def test_every_new_provider_route_job_has_a_registered_worker_handler():
    expected = {
        "intel_lens_monitor",
        "intel_lens_compare",
        "intel_bh_refresh",
        "intel_bh_reviews",
        "intel_via_learning",
        "discovery_federated_search",
        "vkpi_analytics_monitor",
        "vkpi_analytics_compare",
        "apify_batch_refresh",
        "kol_dossier_scan",
        "kol_platform_search",
        "kol_apify_enrich",
        "kol_apify_enrich_candidates",
        "kol_onboarding",
        "official_visual_scan",
        "industry_account_refresh",
        "project_video_metadata_refresh",
        "comments_collect_post",
        "comments_batch_collect",
        "comment_intelligence_post",
        "comment_intelligence_recent",
    }
    assert expected <= set(JOB_HANDLERS)


def test_user_provider_routes_enqueue_instead_of_calling_services(monkeypatch):
    queue = FakeQueue()
    request = _request(queue)
    monkeypatch.setattr(
        kol_ops,
        "search_platform_content",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("route must not call provider")),
    )
    from app.domains.kol import my_kol_paid_action_access as paid_access

    monkeypatch.setattr(
        paid_access,
        "build_target_fence",
        lambda *_args, **_kwargs: {"version": 1, "action": "kol_apify_enrich"},
    )

    async def run():
        results = []
        results.append(
            await vkpi_kol_pool.kol_pool_enrich_via_apify(
                request,
                101,
                staff={"id": 1},
            )
        )
        results.append(
            await vkpi_kol_pool.kol_onboarding_sweep(
                request,
                "camera creators",
                staff={"id": 1},
            )
        )
        results.append(
            await vkpi_operations.official_visual_scan(
                request,
                max_total=3,
                staff={"id": 1},
            )
        )
        results.append(
            await kol_ops.search_kol_platform(
                request,
                {"query": "camera creators", "platform": "youtube", "market": "US"},
                staff={"id": 1},
            )
        )
        results.append(
            await account_scanner.api_scan.__wrapped__(
                request,
                {"platform": "instagram", "handle": "creator", "max_posts": 12},
                staff={"id": 1},
            )
        )
        results.append(
            await deepsight.scan_official_matrix.__wrapped__(
                request,
                {"accounts": [{"platform": "youtube", "handle": "creator"}]},
                staff={"id": 1},
            )
        )
        return results

    results = asyncio.run(run())
    assert all(result["status"] == "queued" for result in results)
    assert [item[0] for item in queue.jobs] == [
        "kol_apify_enrich",
        "kol_onboarding",
        "official_visual_scan",
        "kol_platform_search",
        "intel_scan_account",
        "intel_scan_matrix",
    ]


def test_legacy_verification_sync_flag_still_enqueues():
    queue = FakeQueue()
    request = _request(queue)

    result = asyncio.run(
        verify.admin_trigger_scan.__wrapped__(
            request,
            platform="instagram",
            only_oldest_n=10,
            sync=True,
            admin_user={"email": "owner@example.test"},
        )
    )

    assert result["status"] == "queued"
    assert result["progressive"] is True
    assert queue.jobs[0][0] == "verification_scan_pending"


def test_apify_leaf_inventory_is_explicit_and_opaque_network_wrapper_is_unused():
    root = Path(__file__).resolve().parents[1] / "backend" / "app"
    calls: dict[str, list[str]] = {"call_apify_actor": [], "run_apify_network": []}
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in calls:
                calls[name].append(f"{path.relative_to(root)}:{node.lineno}")

    # 33 + listening_executors(2026-07-16 市场监听接线:X/Reddit 经 call_apify_actor
    # 走预算预检+记账,属显式登记的合法叶子)。
    # 34 = 33 + services/scraping/apify_cn.py(2026-07-20 CN 三平台「仅视频分析」通道:
    # bilibili/抖音/小红书视频元数据+直链取数,durable claim + 预算预检 + record_apify_run 记账)。
    # 联邦发现不再自造第 35 个通用 payload 叶子；它复用已登记的按平台 discovery
    # adapters，避免把 YouTube 输入误发给 TikTok/Instagram actor。
    assert len(calls["call_apify_actor"]) == 34
    assert calls["run_apify_network"] == []
    media_source = inspect.getsource(
        __import__("app.workers.apify_jobs_worker_media", fromlist=["_scrape_with_apify_timeout"])._scrape_with_apify_timeout
    )
    assert "subprocess.run" not in media_source
    assert "current_apify_execution_context" in media_source


def test_remaining_p0_user_routes_enqueue_only(monkeypatch):
    queue = FakeQueue()
    request = _request(queue)

    async def fake_kol_refresh(_queue, kol_pool_id, **_kwargs):
        task_id = await _queue.enqueue(
            "vkpi_kol_pool_on_demand_refresh",
            {"kol_pool_id": kol_pool_id},
        )
        return {"status": "queued", "task_id": task_id}

    monkeypatch.setattr(vkpi_kol_pool.task_enqueue, "enqueue_kol_pool_on_demand_refresh", fake_kol_refresh)
    monkeypatch.setattr(
        vkpi_kol_pool.kol_pool,
        "enrich_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sync enrich must not run")),
    )
    monkeypatch.setattr(vkpi_industry_automation.industry_domain, "get_account", lambda *_args, **_kwargs: {"account": {"id": 9}})
    monkeypatch.setattr(
        vkpi_industry_automation.industry_access,
        "build_refresh_payload",
        lambda account_id, **_kwargs: {
            "account_id": int(account_id),
            "project_id": 3,
            "staff_id": 1,
            "user_id": 10,
            "industry_account_refresh_fence": {"signature": "test-only"},
        },
    )
    monkeypatch.setattr(
        vkpi_industry_automation.industry_domain,
        "refresh_account",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sync industry refresh must not run")),
    )

    async def fake_channel_sync(_queue, channel_id, **_kwargs):
        task_id = await _queue.enqueue("vkpi_official_channel_sync", {"channel_id": channel_id})
        return {"status": "queued", "task_id": task_id}

    monkeypatch.setattr(vkpi_operations.task_enqueue, "enqueue_official_channel_sync", fake_channel_sync)
    monkeypatch.setattr(
        vkpi_operations.channels,
        "sync_now",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sync channel refresh must not run")),
    )
    monkeypatch.setattr(
        vkpi_operations.channel_comments,
        "enqueue_official_channel_comments_job",
        lambda channel_id, **_kwargs: {"status": "queued", "job_id": 501, "channel_id": channel_id},
    )
    monkeypatch.setattr(
        vkpi_operations.channel_comments,
        "collect_channel_post_comments",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sync comment collect must not run")),
    )

    async def run():
        kol = await vkpi_kol_pool.enrich_pool_item.__wrapped__(request, 7, {"max_posts": 3}, staff={"id": 1})
        industry = await vkpi_industry_automation.industry_refresh_account(request, 9, staff={"id": 1})
        channel = await vkpi_operations.sync_channel(request, 11, max_posts=12, staff={"id": 1})
        comments = vkpi_operations.collect_channel_post_comments(
            11,
            {"post_id": "p-1", "limit": 20},
            staff={"id": 1},
        )
        return kol, industry, channel, comments

    kol, industry, channel, comments = asyncio.run(run())
    assert kol["status"] == "queued"
    assert industry["status"] == "queued"
    assert channel["status"] == "queued"
    assert comments["status"] == "queued"
    assert [item[0] for item in queue.jobs] == [
        "vkpi_kol_pool_on_demand_refresh",
        "industry_account_refresh",
        "vkpi_official_channel_sync",
    ]
    industry_payload = queue.jobs[1][1]
    assert "staff" not in industry_payload
    assert industry_payload["staff_id"] == 1
    assert "industry_account_refresh_fence" in industry_payload


def test_batch_enrich_and_comment_routes_enqueue_only(monkeypatch):
    queue = FakeQueue()
    request = _request(queue)

    async def fake_kol_refresh(_queue, kol_pool_id, **_kwargs):
        task_id = await _queue.enqueue(
            "vkpi_kol_pool_on_demand_refresh",
            {"kol_pool_id": kol_pool_id},
        )
        return {"status": "queued", "task_id": task_id}

    import app.domains.tasks.enqueue as task_enqueue
    from app.domains.kol import my_kol_paid_action_access as paid_access

    monkeypatch.setattr(task_enqueue, "enqueue_kol_pool_on_demand_refresh", fake_kol_refresh)
    monkeypatch.setattr(paid_access, "assert_target_writable", lambda *_a, **_kw: 1)

    async def run():
        batch = await vkpi_kol_pool_jobs.batch_enrich_pool_items.__wrapped__(
            request,
            {"ids": [7, 8], "max_posts": 3},
            staff={"id": 1},
        )
        collected = await vkpi_comments.api_collect_post_comments(
            request,
            21,
            post_table="industry_posts",
            max_comments=30,
            staff={"id": 1},
        )
        recent = await vkpi_comment_intelligence.api_process_recent(
            request,
            platform="youtube",
            days=7,
            limit=5,
            collect_comments=True,
            analyze_sentiment=True,
            classify_pillar=True,
            force_reprocess=False,
            staff={"id": 1},
        )
        return batch, collected, recent

    batch, collected, recent = asyncio.run(run())
    assert batch["queued"] == 2
    assert collected["status"] == "queued"
    assert recent["status"] == "queued"
    assert [item[0] for item in queue.jobs] == [
        "vkpi_kol_pool_on_demand_refresh",
        "vkpi_kol_pool_on_demand_refresh",
        "comments_collect_post",
        "comment_intelligence_recent",
    ]


def test_project_video_pending_is_followed_by_metadata_job(monkeypatch):
    queue = FakeQueue()
    request = _request(queue)
    monkeypatch.setattr(
        vkpi_projects.workflow,
        "record_project_kol_video",
        lambda *_args, **_kwargs: {
            "ok": True,
            "status": "metadata_pending",
            "evidence": {"id": 88},
        },
    )

    result = asyncio.run(
        vkpi_projects.project_kol_action_stub(
            request,
            4,
            "9",
            "video",
            {"video_url": "https://example.test/video"},
            staff={"id": 1},
        )
    )

    assert result["status"] == "metadata_pending"
    assert result["job_id"] == "job-1"
    assert result["progressive"] is True
    assert queue.jobs[0][0] == "project_video_metadata_refresh"


def test_inprocess_queue_rejects_provider_job_before_fake_queued_state():
    queue = InProcessJobQueue(SimpleNamespace())
    try:
        asyncio.run(queue.enqueue("industry_account_refresh", {"account_id": 1}))
    except RuntimeError as exc:
        assert "durable_queue_required" in str(exc)
    else:  # pragma: no cover - explicit fail-closed contract
        raise AssertionError("provider job was accepted by in-process queue")
    assert queue._generic_status == {}


@pytest.mark.parametrize(
    "handler",
    [
        vkpi_kol_pool.kol_pool_federated_search_refresh,
        vkpi_kol_pool.kol_onboarding_sweep,
    ],
)
def test_external_discovery_routes_require_manager_before_handler(handler):
    dependency = inspect.signature(handler).parameters["staff"].default.dependency
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            dependency(
                staff={
                    "id": 18,
                    "role": "employee",
                    "is_owner": 0,
                    "permissions": {"vkpi": "write"},
                }
            )
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "management permission required"


@pytest.mark.parametrize(
    "handler,args",
    [
        (vkpi_kol_pool.kol_pool_federated_search_refresh, ("camera creators", 20)),
        (vkpi_kol_pool.kol_onboarding_sweep, ("camera creators",)),
    ],
)
def test_external_discovery_routes_reject_inprocess_queue(handler, args):
    queue = InProcessJobQueue(SimpleNamespace())
    request = _request(queue)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            handler(
                request,
                *args,
                staff={"id": 1, "role": "manager"},
            )
        )

    assert exc_info.value.status_code == 503
    assert "durable_queue_required" in str(exc_info.value.detail)
    assert queue._generic_status == {}


def test_federated_refresh_rejects_empty_durable_job_id():
    class EmptyIdQueue:
        backend_name = "redis-stream"

        async def enqueue(self, *_args, **_kwargs):
            return ""

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            vkpi_kol_pool.kol_pool_federated_search_refresh(
                _request(EmptyIdQueue()),
                "camera creators",
                20,
                staff={"id": 1, "role": "manager"},
            )
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "durable job queue returned no job id"


def test_cron_and_daily_sync_have_no_direct_provider_invocations():
    forbidden = {
        "sync_now",
        "enrich_item",
        "monitor_product",
        "sync_enabled_accounts",
        "collect_account_snapshot",
    }
    for module in (sync_cron, daily_sync):
        tree = ast.parse(inspect.getsource(module))
        called_attrs = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert forbidden.isdisjoint(called_attrs)
