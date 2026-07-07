"""backend/app/services/vkpi/industry_crawlers/xiaohongshu_crawler.py

R-Phase2-B: 小红书 (XiaoHongShu / RedNote) 抓取适配器

接口规范与 InstagramCrawler / TikTokCrawler 完全对齐.

Apify Actor:
  - zhorex/rednote-xiaohongshu-scraper (默认)

环境变量:
  APIFY_TOKEN: Apify 访问 token
  APIFY_XIAOHONGSHU_ACTOR_ID: 默认 zhorex/rednote-xiaohongshu-scraper

注意:
  - 小红书反爬严格,失败率较高
  - 中国 IP 抓取效果更好,Apify 默认走美国 IP
  - 月度预算建议 $30-40/月
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


APIFY_API_BASE = "https://api.apify.com/v2"
DEFAULT_ACTOR_ID = "zhorex~rednote-xiaohongshu-scraper"
DEFAULT_RUN_TIMEOUT_SECONDS = 180


class XiaohongshuCrawler:
    """小红书 via Apify scraper."""

    def __init__(
        self,
        api_token: str | None = None,
        *,
        actor_id: str | None = None,
        timeout_seconds: int = 30,
        run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
    ) -> None:
        self.api_token = (api_token or os.environ.get("APIFY_TOKEN") or "").strip()
        self.actor_id = (actor_id or os.environ.get("APIFY_XIAOHONGSHU_ACTOR_ID") or DEFAULT_ACTOR_ID).strip()
        self.timeout_seconds = max(3, min(60, int(timeout_seconds or 30)))
        self.run_timeout_seconds = max(60, min(600, int(run_timeout_seconds or DEFAULT_RUN_TIMEOUT_SECONDS)))

    @property
    def configured(self) -> bool:
        return bool(self.api_token)

    def provider_status(self) -> dict[str, Any]:
        return {
            "provider": "xiaohongshu",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "actor_id": self.actor_id,
            "key_visible": False,
        }

    def _not_configured(self, operation: str) -> dict[str, Any]:
        return {
            "provider": "xiaohongshu",
            "operation": operation,
            "provider_status": "not_configured",
            "sync_status": "not_configured",
            "items": [],
            "raw": {},
            "message": "APIFY_TOKEN 未配置,小红书抓取未执行。",
        }

    @staticmethod
    def normalize_handle_ref(value: str) -> dict[str, str]:
        """从 URL / user_id / handle 提取规范化"""
        raw = str(value or "").strip()
        if not raw:
            return {"kind": "empty", "value": ""}
        
        # 小红书 URL: https://www.xiaohongshu.com/user/profile/{user_id}
        if "://" in raw:
            parsed = urllib.parse.urlparse(raw)
            match = re.search(r"/user/profile/([a-zA-Z0-9]+)", parsed.path)
            if match:
                return {"kind": "user_id", "value": match.group(1)}
            return {"kind": "url", "value": raw}
        
        # 纯 user_id (24 位 hex)
        if re.match(r"^[a-fA-F0-9]{24}$", raw):
            return {"kind": "user_id", "value": raw}
        
        return {"kind": "query", "value": raw}

    def _start_run(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("apify_run")
        
        actor_path = self.actor_id.replace("/", "~")
        url = f"{APIFY_API_BASE}/acts/{actor_path}/run-sync-get-dataset-items?token={self.api_token}"
        
        try:
            data = json.dumps(input_payload).encode("utf-8")
            request = urllib.request.Request(
                url, data=data, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "ViltroxMarketing/1.0",
                },
            )
            with urllib.request.urlopen(request, timeout=self.run_timeout_seconds) as response:  # nosec B310
                body = response.read().decode("utf-8")
            
            payload = json.loads(body or "[]")
            items = payload if isinstance(payload, list) else (payload.get("items") or [])
            # C5 成本记账收口:run-sync HTTP 拿不到 run 对象 → run=None 口径记账
            # (pricing_basis=no_run_object,盲区在成本卡如实可见;失败绝不影响抓取)。
            try:
                from app.domains.costs.budget_guard import record_apify_run

                record_apify_run(
                    None,
                    actor_id=self.actor_id,
                    platform="xiaohongshu",
                    operation="run_sync_dataset_items",
                    source="industry_crawlers.xiaohongshu",
                    dataset_item_count=len(items),
                )
            except Exception:
                logger.debug("apify 记账失败,不阻断爬取(best-effort)", exc_info=True)
            return {
                "provider": "xiaohongshu",
                "provider_status": "ok",
                "sync_status": "synced",
                "items": items,
                "raw": {"actor_id": self.actor_id, "input": input_payload},
            }
        except Exception as exc:  # pragma: no cover
            return {
                "provider": "xiaohongshu",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": str(exc),
                "raw": {"actor_id": self.actor_id},
            }

    def crawl_channel_profile(
        self,
        handle_or_url: str,
        *,
        channel_id: str = "",
        max_posts: int = 12,
    ) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_channel_profile")
        
        ref = self.normalize_handle_ref(handle_or_url)
        if ref["kind"] == "empty":
            return {"provider": "xiaohongshu", "provider_status": "no_results", "sync_status": "no_results", "items": [], "message": "user_id 为空"}
        
        target_url = handle_or_url if "://" in handle_or_url else f"https://www.xiaohongshu.com/user/profile/{ref['value']}"
        max_items = max(1, min(50, int(max_posts or 12)))
        if "zhorex~rednote-xiaohongshu-scraper" in self.actor_id:
            input_payload = {
                "mode": "profile",
                "userUrl": target_url,
                "maxResults": max_items,
                "includeComments": False,
            }
        else:
            input_payload = {
                "startUrls": [{"url": target_url}],
                "maxItems": max_items,
            }
        result = self._start_run(input_payload)
        result["query"] = ref
        return result

    def crawl_channel_videos(self, channel_id: str, *, max_results: int = 25) -> dict[str, Any]:
        return self.crawl_channel_profile(channel_id, max_posts=max_results)

    def crawl_video_comments(self, video_id: str, *, max_results: int = 50) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_video_comments")
        return {
            "provider": "xiaohongshu",
            "provider_status": "not_implemented",
            "sync_status": "not_configured",
            "items": [],
            "message": "小红书评论留 R-Phase3.2 sentiment 时实装",
        }
