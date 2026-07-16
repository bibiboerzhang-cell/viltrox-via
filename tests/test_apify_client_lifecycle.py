from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domains.costs import budget_guard
from app.db.connection import get_conn
from app.platform import industry_crawlers
from app.platform.apify_budget import (
    acquire_provider_execution_claim,
    apify_execution_context,
)
from app.platform.apify_lifecycle import (
    close_apify_client,
    managed_apify_client,
    managed_apify_client_async,
    register_apify_client_shutdown,
)
from app.platform.industry_crawlers.instagram_crawler import InstagramCrawler
from app.platform.industry_crawlers.tiktok_crawler import TikTokCrawler

try:
    from apify_client import ApifyClient as _InstalledApifyClient
except ImportError:  # pragma: no cover - optional provider dependency
    _InstalledApifyClient = None


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_apify_budget_allowance(monkeypatch: pytest.MonkeyPatch):
    """These lifecycle tests use only in-process fakes, never the live budget row."""
    budget_guard.ensure_budget_schema()
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_provider_execution_claims (
          task_id TEXT PRIMARY KEY, job_type TEXT, lease_owner TEXT, fence_token INTEGER,
          state TEXT, lease_expires_at TEXT, provider_run_id TEXT, created_at TEXT,
          updated_at TEXT, completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS vkpi_apify_budget_reservations (
          reservation_key TEXT PRIMARY KEY, task_id TEXT, actor_id TEXT, operation TEXT,
          payload_hash TEXT, execution_fence_token INTEGER, estimate_source TEXT,
          estimated_cost_usd REAL, actual_cost_usd REAL, state TEXT, apify_run_id TEXT,
          metadata_json TEXT, reserved_at TEXT, provider_started_at TEXT, settled_at TEXT,
          updated_at TEXT, UNIQUE(task_id,actor_id,operation,payload_hash)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_apify_reservation_run
          ON vkpi_apify_budget_reservations(apify_run_id)
          WHERE apify_run_id IS NOT NULL AND apify_run_id<>'';
        DELETE FROM vkpi_provider_execution_claims;
        DELETE FROM vkpi_apify_budget_reservations;
        """
    )
    conn.commit()
    budget_guard.update_budget("provider:apify", {"cap_usd": 10, "current_spend": 0, "hard_stop_at": 1})
    budget_guard.update_budget("monthly_total", {"cap_usd": 100, "current_spend": 0, "hard_stop_at": 1})
    token = acquire_provider_execution_claim("apify-lifecycle-test", "pytest")
    with apify_execution_context("apify-lifecycle-test", token):
        yield


class _CloseRecorder:
    def __init__(self) -> None:
        self.calls = 0

    def close(self) -> None:
        self.calls += 1


class _AsyncCloseRecorder:
    def __init__(self) -> None:
        self.calls = 0

    async def aclose(self) -> None:
        self.calls += 1


class _BlockingAsyncCloseRecorder:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def aclose(self) -> None:
        self.calls += 1
        self.started.set()
        await self.release.wait()


class _Dataset:
    def iterate_items(self):
        return iter([{"id": "item-1"}])


class _Actor:
    def __init__(self, *, fail: bool) -> None:
        self.fail = fail

    def start(self, **_kwargs):
        if self.fail:
            raise RuntimeError("provider failed")
        return {"id": "run-1", "status": "RUNNING", "defaultDatasetId": "dataset-1"}


class _Run:
    def wait_for_finish(self, *, wait_secs=None):
        return {"id": "run-1", "status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}


class _FakeApifyClient:
    created: list["_FakeApifyClient"] = []
    fail = False

    def __init__(self, _token: str) -> None:
        self.pool = _CloseRecorder()
        self.async_pool = _AsyncCloseRecorder()
        self.http_client = SimpleNamespace(
            httpx_client=self.pool,
            httpx_async_client=self.async_pool,
        )
        type(self).created.append(self)

    def actor(self, _actor_id: str) -> _Actor:
        return _Actor(fail=type(self).fail)

    def run(self, _run_id: str) -> _Run:
        return _Run()

    def dataset(self, _dataset_id: str) -> _Dataset:
        return _Dataset()


@pytest.fixture(autouse=True)
def _fake_apify(monkeypatch):
    _FakeApifyClient.created = []
    _FakeApifyClient.fail = False
    monkeypatch.setitem(sys.modules, "apify_client", SimpleNamespace(ApifyClient=_FakeApifyClient))
    monkeypatch.setattr(industry_crawlers, "record_apify_run_cost", lambda *_args, **_kwargs: None)


def test_close_apify_client_prefers_public_close() -> None:
    recorder = _CloseRecorder()

    industry_crawlers.close_apify_client(recorder)
    industry_crawlers.close_apify_client(recorder)

    assert recorder.calls == 1


def test_close_apify_client_closes_both_hidden_transports_once() -> None:
    client = _FakeApifyClient("token")

    close_apify_client(client)
    close_apify_client(client)

    assert client.pool.calls == 1
    assert client.async_pool.calls == 1


def test_managed_client_closes_on_early_return_and_exception() -> None:
    early = _FakeApifyClient("token")
    failed = _FakeApifyClient("token")

    def use_early():
        with managed_apify_client(early):
            return "done"

    assert use_early() == "done"
    with pytest.raises(RuntimeError, match="provider failed"):
        with managed_apify_client(failed):
            raise RuntimeError("provider failed")

    assert (early.pool.calls, early.async_pool.calls) == (1, 1)
    assert (failed.pool.calls, failed.async_pool.calls) == (1, 1)


def test_managed_async_client_awaits_async_transport_on_exception() -> None:
    client = _FakeApifyClient("token")

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="async provider failed"):
            async with managed_apify_client_async(client):
                raise RuntimeError("async provider failed")

    asyncio.run(scenario())

    assert client.pool.calls == 1
    assert client.async_pool.calls == 1


def test_sync_close_in_running_loop_marks_only_after_success_and_can_retry() -> None:
    async def scenario() -> None:
        client = _FakeApifyClient("token")
        blocking = _BlockingAsyncCloseRecorder()
        client.async_pool = blocking
        client.http_client.httpx_async_client = blocking

        close_apify_client(client)
        close_apify_client(client)
        await blocking.started.wait()

        first_task = getattr(client, "_vkpi_apify_lifecycle_pending_task")
        assert blocking.calls == 1
        assert client.pool.calls == 1
        assert getattr(client, "_vkpi_apify_lifecycle_closed", False) is False

        first_task.cancel()
        await asyncio.gather(first_task, return_exceptions=True)
        await asyncio.sleep(0)
        assert getattr(client, "_vkpi_apify_lifecycle_closed", False) is False
        assert getattr(client, "_vkpi_apify_lifecycle_pending_task", None) is None

        blocking.release.set()
        close_apify_client(client)
        retry_task = getattr(client, "_vkpi_apify_lifecycle_pending_task")
        await asyncio.shield(retry_task)
        await asyncio.sleep(0)

        assert blocking.calls == 2
        assert client.pool.calls == 2
        assert getattr(client, "_vkpi_apify_lifecycle_closed", False) is True
        close_apify_client(client)
        assert blocking.calls == 2
        assert client.pool.calls == 2

    asyncio.run(scenario())


def test_cleanup_failure_does_not_replace_business_result() -> None:
    class FailingClose:
        def close(self) -> None:
            raise RuntimeError("cleanup failed")

    def operation() -> str:
        with managed_apify_client(FailingClose()):
            return "business result"

    assert operation() == "business result"


def test_process_client_shutdown_registration_is_idempotent(monkeypatch) -> None:
    client = _FakeApifyClient("token")
    registrations: list[tuple[object, tuple[object, ...]]] = []

    monkeypatch.setattr(
        "app.platform.apify_lifecycle.atexit.register",
        lambda callback, *args: registrations.append((callback, args)),
    )

    assert register_apify_client_shutdown(client) is client
    assert register_apify_client_shutdown(client) is client
    assert len(registrations) == 1
    callback, args = registrations[0]
    callback(*args)
    assert (client.pool.calls, client.async_pool.calls) == (1, 1)


def test_installed_apify_client_hidden_transports_are_closed_offline() -> None:
    if _InstalledApifyClient is None:
        pytest.skip("apify-client is not installed")
    client = _InstalledApifyClient("offline-lifecycle-test")
    sync_transport = client.http_client.httpx_client
    async_transport = client.http_client.httpx_async_client

    close_apify_client(client)

    assert sync_transport.is_closed is True
    assert async_transport.is_closed is True


def test_every_apify_client_constructor_has_an_ownership_policy() -> None:
    short_lived = (
        "backend/app/platform/industry_crawlers/instagram_crawler.py",
        "backend/app/platform/industry_crawlers/tiktok_crawler.py",
        "backend/app/platform/industry_crawlers/x_crawler.py",
        "backend/app/platform/industry_crawlers/youtube_crawler.py",
        "backend/app/platform/industry_crawlers/facebook_crawler.py",
        "backend/app/platform/industry_crawlers/reddit_crawler.py",
        "backend/app/platform/industry_crawlers/bilibili_crawler.py",
        "backend/app/platform/industry_crawlers/xiaohongshu_crawler.py",
        "backend/app/domains/sync/apify_batch_refresh.py",
        "backend/app/domains/projects/workflow_evidence_video_metadata.py",
    )
    process_level = (
        "backend/app/services/intelligence/bh_scraper.py",
        "backend/app/services/intelligence/lens_compare.py",
        "backend/app/services/intelligence/lens_monitor.py",
        "backend/app/services/scraping/apify.py",
    )

    for relative in short_lived:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "ApifyClient(" in source
        assert "managed_apify_client" in source or "close_apify_client" in source
    for relative in process_level:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "ApifyClient(" in source
        assert "register_apify_client_shutdown" in source


@pytest.mark.parametrize("crawler", [InstagramCrawler(api_token="token"), TikTokCrawler(api_token="token")])
def test_profile_actor_client_pool_is_closed_after_success(crawler) -> None:
    result = crawler._start_run({"input": "value"})

    assert result["sync_status"] == "synced"
    assert len(_FakeApifyClient.created) == 1
    assert _FakeApifyClient.created[0].pool.calls == 1
    assert _FakeApifyClient.created[0].async_pool.calls == 1


@pytest.mark.parametrize("crawler", [InstagramCrawler(api_token="token"), TikTokCrawler(api_token="token")])
def test_profile_actor_client_pool_is_closed_after_provider_error(crawler) -> None:
    _FakeApifyClient.fail = True

    result = crawler._start_run({"input": "value"})

    assert result["sync_status"] == "error"
    assert len(_FakeApifyClient.created) == 1
    assert _FakeApifyClient.created[0].pool.calls == 1
    assert _FakeApifyClient.created[0].async_pool.calls == 1


@pytest.mark.parametrize(
    ("crawler", "post_ref"),
    [
        (InstagramCrawler(api_token="token"), "ABC123"),
        (TikTokCrawler(api_token="token"), "123456789"),
    ],
)
def test_comment_actor_client_pool_is_closed_after_success(crawler, post_ref: str) -> None:
    result = crawler.crawl_video_comments(post_ref, max_results=5)

    assert result["sync_status"] == "synced"
    assert len(_FakeApifyClient.created) == 1
    assert _FakeApifyClient.created[0].pool.calls == 1
    assert _FakeApifyClient.created[0].async_pool.calls == 1
