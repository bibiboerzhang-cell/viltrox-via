"""backend/app/services/vkpi/industry_crawlers/tiktok_crawler.py

R-Phase2-A: TikTok 抓取适配器 (Apify scraper)

接口规范与 YouTubeCrawler / InstagramCrawler 完全对齐.

Apify Actor 选型:
  - clockworks/tiktok-scraper (默认,profile + posts)
  
环境变量:
  APIFY_TOKEN: Apify 访问 token
  APIFY_TIKTOK_ACTOR_ID: Apify actor ID (默认 clockworks/tiktok-scraper)

注意 TikTok 反爬严:
  - 失败率高,需要 retry + 优雅降级
  - 月预算控制要严格
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any


APIFY_API_BASE = "https://api.apify.com/v2"
DEFAULT_ACTOR_ID = "clockworks~tiktok-scraper"
DEFAULT_RUN_TIMEOUT_SECONDS = 180  # TikTok 抓取慢,timeout 长一点


class TikTokCrawler:
    """TikTok via Apify scraper."""

    def __init__(
        self,
        api_token: str | None = None,
        *,
        actor_id: str | None = None,
        timeout_seconds: int = 30,
        run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
    ) -> None:
        self.api_token = (api_token or os.environ.get("APIFY_TOKEN") or "").strip()
        self.actor_id = (actor_id or os.environ.get("APIFY_TIKTOK_ACTOR_ID") or DEFAULT_ACTOR_ID).strip()
        self.timeout_seconds = max(3, min(60, int(timeout_seconds or 30)))
        self.run_timeout_seconds = max(60, min(600, int(run_timeout_seconds or DEFAULT_RUN_TIMEOUT_SECONDS)))

    @property
    def configured(self) -> bool:
        return bool(self.api_token)

    def provider_status(self) -> dict[str, Any]:
        return {
            "provider": "tiktok",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "actor_id": self.actor_id,
            "key_visible": False,
        }

    def _not_configured(self, operation: str) -> dict[str, Any]:
        return {
            "provider": "tiktok",
            "operation": operation,
            "provider_status": "not_configured",
            "sync_status": "not_configured",
            "items": [],
            "raw": {},
            "message": "APIFY_TOKEN 未配置,TikTok 抓取未执行。",
        }

    @staticmethod
    def normalize_handle_ref(value: str) -> dict[str, str]:
        """从 URL / @handle / handle 提取规范化的 handle"""
        raw = str(value or "").strip()
        if not raw:
            return {"kind": "empty", "value": ""}
        
        if "://" in raw:
            parsed = urllib.parse.urlparse(raw)
            path = parsed.path.strip("/")
            if path.startswith("@"):
                return {"kind": "handle", "value": path[1:].split("/")[0]}
            if path:
                return {"kind": "handle", "value": path.split("/")[0].lstrip("@")}
        
        if raw.startswith("@"):
            return {"kind": "handle", "value": raw[1:]}
        
        if re.match(r"^[a-zA-Z0-9_.]+$", raw):
            return {"kind": "handle", "value": raw}
        
        return {"kind": "query", "value": raw}

    # ─── Apify 调用 ──────────────────────────────────

    def _start_run(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        """启动 Apify run,等待完成,返回 dataset items"""
        if not self.configured:
            return self._not_configured("apify_run")
        
        actor_path = self.actor_id.replace("/", "~")
        url = f"{APIFY_API_BASE}/acts/{actor_path}/run-sync-get-dataset-items?token={self.api_token}"
        
        try:
            data = json.dumps(input_payload).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                method="POST",
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
            return {
                "provider": "tiktok",
                "provider_status": "ok",
                "sync_status": "synced",
                "items": items,
                "raw": {"actor_id": self.actor_id, "input": input_payload},
            }
        except Exception as exc:  # pragma: no cover
            return {
                "provider": "tiktok",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": str(exc),
                "raw": {"actor_id": self.actor_id},
            }

    # ─── 公开接口 ─────────────────────────────────

    def crawl_channel_profile(
        self,
        handle_or_url: str,
        *,
        channel_id: str = "",
        max_posts: int = 12,
    ) -> dict[str, Any]:
        """抓取 TikTok 账号 profile + 最近 N 帖"""
        if not self.configured:
            return self._not_configured("crawl_channel_profile")
        
        ref = self.normalize_handle_ref(handle_or_url)
        if ref["kind"] == "empty":
            return {
                "provider": "tiktok",
                "provider_status": "no_results",
                "sync_status": "no_results",
                "items": [],
                "message": "handle 为空",
            }
        
        # Apify clockworks/tiktok-scraper input
        input_payload: dict[str, Any] = {
            "profiles": [ref["value"]] if ref["kind"] == "handle" else [],
            "resultsPerPage": max(1, min(50, int(max_posts or 12))),
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
            "shouldDownloadSlideshowImages": False,
        }
        if ref["kind"] == "query":
            input_payload["searchQueries"] = [ref["value"]]
            input_payload["searchSection"] = "user"
        
        result = self._start_run(input_payload)
        result["query"] = ref
        return result

    def crawl_channel_videos(
        self,
        channel_id: str,
        *,
        max_results: int = 25,
    ) -> dict[str, Any]:
        """抓取最近视频. channel_id 就是 username (不带 @)."""
        if not self.configured:
            return self._not_configured("crawl_channel_videos")
        
        if not channel_id:
            return {
                "provider": "tiktok",
                "provider_status": "no_results",
                "sync_status": "no_results",
                "items": [],
                "message": "channel_id (username) 为空",
            }
        
        input_payload = {
            "profiles": [channel_id.lstrip("@")],
            "resultsPerPage": max(1, min(100, int(max_results or 25))),
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }
        return self._start_run(input_payload)

    def crawl_video_comments(
        self,
        video_id: str,
        *,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """评论抓取 - 留 R-Phase3.2 sentiment 时实装"""
        if not self.configured:
            return self._not_configured("crawl_video_comments")
        
        return {
            "provider": "tiktok",
            "provider_status": "not_implemented",
            "sync_status": "not_configured",
            "items": [],
            "message": "TikTok 评论抓取留 R-Phase3.2 sentiment 时实装",
        }
