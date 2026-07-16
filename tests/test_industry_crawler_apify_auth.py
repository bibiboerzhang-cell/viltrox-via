from __future__ import annotations

import pytest

from app.domains.costs import budget_guard
from app.db.connection import get_conn
from app.platform.apify_budget import (
    acquire_provider_execution_claim,
    apify_execution_context,
)
from app.platform.industry_crawlers.bilibili_crawler import BilibiliCrawler
from app.platform.industry_crawlers.xiaohongshu_crawler import XiaohongshuCrawler


@pytest.fixture(autouse=True)
def _isolated_apify_budget_allowance(monkeypatch: pytest.MonkeyPatch):
    """The transport is monkeypatched; isolate this contract from live spend."""
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
    monkeypatch.setenv("VKPI_APIFY_UNKNOWN_CALL_ESTIMATE_USD", "1.00")


@pytest.mark.parametrize("crawler_type", [BilibiliCrawler, XiaohongshuCrawler])
def test_sdk_apify_crawlers_keep_token_out_of_url_and_redact_errors(
    monkeypatch: pytest.MonkeyPatch,
    crawler_type,
) -> None:
    secret = "apify-secret-token"
    captured: dict[str, object] = {}

    class FakeActor:
        def start(self, **kwargs):
            captured["start_kwargs"] = kwargs
            raise RuntimeError(f"transport failed with token={secret}")

    class FakeClient:
        def __init__(self, token: str) -> None:
            captured["token"] = token

        def actor(self, actor_id: str):
            captured["actor_id"] = actor_id
            return FakeActor()

    monkeypatch.setattr("apify_client.ApifyClient", FakeClient)
    crawler = crawler_type(api_token=secret)
    task_id = f"auth-{crawler_type.__name__.lower()}"
    token = acquire_provider_execution_claim(task_id, "test-consumer")
    with apify_execution_context(task_id, token):
        result = crawler._start_run({"startUrls": [{"url": "https://example.test"}]})

    assert captured["token"] == secret
    assert "token" not in str(captured.get("start_kwargs") or {}).lower()
    assert secret not in str(captured.get("actor_id") or "")
    assert secret not in result["error"]
    assert "[redacted]" in result["error"]
