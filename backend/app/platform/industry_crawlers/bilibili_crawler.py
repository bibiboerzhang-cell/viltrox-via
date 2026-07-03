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

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any


APIFY_API_BASE = "https://api.apify.com/v2"
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
        
        actor_path = self.actor_id.replace("/", "~")
        url = f"{APIFY_API_BASE}/acts/{actor_path}/run-sync-get-dataset-items?token={self.api_token}"
        
        try:
            data = json.dumps(input_payload).encode("utf-8")
            request = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ViltroxMarketing/1.0"},
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
                    platform="bilibili",
                    operation="run_sync_dataset_items",
                    source="industry_crawlers.bilibili",
                    dataset_item_count=len(items),
                )
            except Exception:
                pass  # 记账失败绝不阻断爬取
            return {
                "provider": "bilibili",
                "provider_status": "ok",
                "sync_status": "synced",
                "items": items,
                "raw": {"actor_id": self.actor_id, "input": input_payload},
            }
        except Exception as exc:  # pragma: no cover
            return {
                "provider": "bilibili",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": str(exc),
                "raw": {"actor_id": self.actor_id},
            }

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
