"""backend/app/services/vkpi/industry_crawlers/bilibili_crawler.py

R-Phase2-B: Bilibili (B 站) 抓取适配器

Apify Actor:
  - zhorex/bilibili-scraper

环境变量:
  APIFY_TOKEN
  APIFY_BILIBILI_ACTOR_ID (默认 mtrunkat/bilibili-scraper)

注意:
  - Bilibili 反爬中等
  - 月度预算建议 $20-30/月
  - up 主 ID 是 mid (数字),不是 username
"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any

from app.core.logging import get_logger
from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyProviderReplayBlocked,
    call_apify_actor,
)

logger = get_logger(__name__)


DEFAULT_ACTOR_ID = "zhorex~bilibili-scraper"


class BilibiliCrawler:
    """Bilibili via Apify scraper."""

    def __init__(self, api_token: str | None = None, *, actor_id: str | None = None,
                 timeout_seconds: int = 30, run_timeout_seconds: int = 180) -> None:
        self.api_token = (api_token or os.environ.get("APIFY_TOKEN") or "").strip()
        self.actor_id = (actor_id or os.environ.get("APIFY_BILIBILI_ACTOR_ID") or DEFAULT_ACTOR_ID).strip()
        self.timeout_seconds = max(3, min(60, int(timeout_seconds or 30)))
        self.run_timeout_seconds = max(60, min(600, int(run_timeout_seconds or 180)))

    @property
    def configured(self) -> bool:
        return bool(self.api_token)

    def provider_status(self) -> dict[str, Any]:
        return {
            "provider": "bilibili",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "actor_id": self.actor_id,
            "key_visible": False,
        }

    def _not_configured(self, operation: str) -> dict[str, Any]:
        return {
            "provider": "bilibili",
            "operation": operation,
            "provider_status": "not_configured",
            "sync_status": "not_configured",
            "items": [],
            "raw": {},
            "message": "APIFY_TOKEN 未配置,Bilibili 抓取未执行。",
        }

    @staticmethod
    def normalize_handle_ref(value: str) -> dict[str, str]:
        """Bilibili up 主 ID 是 mid (数字)"""
        raw = str(value or "").strip()
        if not raw:
            return {"kind": "empty", "value": ""}
        
        # URL: https://space.bilibili.com/{mid}
        if "://" in raw:
            match = re.search(r"space\.bilibili\.com/(\d+)", raw)
            if match:
                return {"kind": "mid", "value": match.group(1)}
        
        # 纯数字 mid
        if raw.isdigit():
            return {"kind": "mid", "value": raw}
        
        return {"kind": "query", "value": raw}

    def _start_run(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("apify_run")

        client: Any | None = None
        try:
            from apify_client import ApifyClient  # type: ignore

            client = ApifyClient(self.api_token)
            run = call_apify_actor(
                client,
                self.actor_id.replace("/", "~"),
                platform="bilibili",
                operation="run_sync_get_dataset_items",
                source="industry_crawlers",
                run_input=input_payload,
                timeout_secs=self.run_timeout_seconds,
                wait_secs=self.run_timeout_seconds,
            )
            if not run or str(run.get("status") or "").upper() != "SUCCEEDED":
                return {
                    "provider": "bilibili",
                    "provider_status": "error",
                    "sync_status": "error",
                    "items": [],
                    "error": f"Bilibili actor did not finish: {str((run or {}).get('status') or 'unknown')}",
                    "raw": {"actor_id": self.actor_id, "input": input_payload},
                }
            dataset_id = str(run.get("defaultDatasetId") or "")
            items = list(client.dataset(dataset_id).iterate_items()) if dataset_id else []
            try:
                from app.domains.costs.budget_guard import record_apify_run

                record_apify_run(
                    run,
                    actor_id=self.actor_id,
                    platform="bilibili",
                    operation="run_sync_dataset_items",
                    source="industry_crawlers.bilibili",
                    dataset_item_count=len(items),
                )
            except Exception:
                logger.debug("apify 记账失败,不阻断爬取(best-effort)", exc_info=True)
            return {
                "provider": "bilibili",
                "provider_status": "ok",
                "sync_status": "synced",
                "items": items,
                "raw": {"actor_id": self.actor_id, "input": input_payload},
            }
        except ApifyBudgetBlocked as exc:
            return {**exc.payload(), "items": [], "raw": {"actor_id": self.actor_id}}
        except ApifyProviderReplayBlocked as exc:
            return {"provider": "bilibili", "provider_status": "blocked", "sync_status": "blocked", "code": exc.code, "items": [], "raw": {"actor_id": self.actor_id}}
        except Exception as exc:  # pragma: no cover
            return {
                "provider": "bilibili",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": str(exc).replace(self.api_token, "[redacted]")[:500],
                "raw": {"actor_id": self.actor_id},
            }
        finally:
            from . import close_apify_client

            close_apify_client(client)

    def crawl_channel_profile(self, handle_or_url: str, *, channel_id: str = "", max_posts: int = 12) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_channel_profile")
        
        ref = self.normalize_handle_ref(handle_or_url)
        if ref["kind"] == "empty":
            return {"provider": "bilibili", "provider_status": "no_results", "sync_status": "no_results", "items": [], "message": "mid 为空"}
        
        target_url = handle_or_url if "://" in handle_or_url else f"https://space.bilibili.com/{ref['value']}"
        input_payload = {
            "mode": "user_videos",
            "userIds": [str(ref["value"])],
            "maxResults": max(1, min(50, int(max_posts or 12))),
            "sourceUrl": target_url,
        }
        result = self._start_run(input_payload)
        result["query"] = ref
        return result

    def crawl_channel_videos(self, channel_id: str, *, max_results: int = 25) -> dict[str, Any]:
        return self.crawl_channel_profile(channel_id, max_posts=max_results)

    def crawl_video_comments(self, video_id: str, *, max_results: int = 50) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_video_comments")
        return {"provider": "bilibili", "provider_status": "not_implemented", "sync_status": "not_configured", "items": [], "message": "Bilibili 评论留 R-Phase3.2"}
