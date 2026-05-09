"""backend/app/services/vkpi/industry_crawlers/x_crawler.py

R-Phase2-B: X (Twitter) 抓取适配器

策略 (双重 fallback):
  1. 优先用官方 X API v2 (BEARER token)
  2. 没 token 时降级到 Apify scraper

Apify Actor:
  - apidojo/twitter-scraper-lite

环境变量:
  X_BEARER_TOKEN: X API v2 Bearer Token (优先)
  APIFY_TOKEN: Apify token (备用)
  APIFY_X_ACTOR_ID: 默认 apidojo/twitter-scraper-lite

注意:
  - X 官方 API 免费层 100 reads/月,基本不够用
  - 推荐用 Apify (~$0.02/账号,$30/月够 1500 账号)
  - rate limit 严重,失败要重试
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any


APIFY_API_BASE = "https://api.apify.com/v2"
X_API_BASE = "https://api.twitter.com/2"
DEFAULT_ACTOR_ID = "apidojo~twitter-scraper-lite"


class XCrawler:
    """X (Twitter) via X API + Apify fallback."""

    def __init__(
        self,
        bearer_token: str | None = None,
        api_token: str | None = None,
        *,
        actor_id: str | None = None,
        timeout_seconds: int = 30,
        run_timeout_seconds: int = 180,
    ) -> None:
        self.bearer_token = (bearer_token or os.environ.get("X_BEARER_TOKEN") or "").strip()
        self.api_token = (api_token or os.environ.get("APIFY_TOKEN") or "").strip()
        self.actor_id = (actor_id or os.environ.get("APIFY_X_ACTOR_ID") or DEFAULT_ACTOR_ID).strip()
        self.timeout_seconds = max(3, min(60, int(timeout_seconds or 30)))
        self.run_timeout_seconds = max(60, min(600, int(run_timeout_seconds or 180)))

    @property
    def configured(self) -> bool:
        # 任一可用即视为 configured
        return bool(self.bearer_token or self.api_token)

    @property
    def use_official_api(self) -> bool:
        return bool(self.bearer_token)

    def provider_status(self) -> dict[str, Any]:
        mode = "official_api" if self.use_official_api else ("apify" if self.api_token else "not_configured")
        return {
            "provider": "x",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "mode": mode,
            "actor_id": self.actor_id if mode == "apify" else "",
            "key_visible": False,
        }

    def _not_configured(self, operation: str) -> dict[str, Any]:
        return {
            "provider": "x",
            "operation": operation,
            "provider_status": "not_configured",
            "sync_status": "not_configured",
            "items": [],
            "raw": {},
            "message": "X_BEARER_TOKEN 和 APIFY_TOKEN 都未配置,X 抓取未执行。",
        }

    @staticmethod
    def normalize_handle_ref(value: str) -> dict[str, str]:
        raw = str(value or "").strip()
        if not raw:
            return {"kind": "empty", "value": ""}
        
        # URL: https://x.com/{handle} 或 https://twitter.com/{handle}
        if "://" in raw:
            match = re.search(r"(?:x\.com|twitter\.com)/([a-zA-Z0-9_]+)", raw)
            if match:
                return {"kind": "handle", "value": match.group(1)}
        
        if raw.startswith("@"):
            return {"kind": "handle", "value": raw[1:]}
        
        if re.match(r"^[a-zA-Z0-9_]{1,15}$", raw):
            return {"kind": "handle", "value": raw}
        
        return {"kind": "query", "value": raw}

    # ─── X API v2 (官方,优先) ────────────────────

    def _x_api_request(self, endpoint: str, params: dict | None = None) -> dict[str, Any]:
        if not self.bearer_token:
            return {}
        
        query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in (params or {}).items())
        url = f"{X_API_BASE}{endpoint}{'?' + query if query else ''}"
        
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {self.bearer_token}",
                    "Accept": "application/json",
                    "User-Agent": "ViltroxMarketing/1.0",
                },
            )
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                body = response.read().decode("utf-8")
            return json.loads(body or "{}")
        except Exception as exc:  # pragma: no cover
            return {"error": str(exc)}

    # ─── Apify (备用) ──────────────────────────

    def _apify_run(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_token:
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
            return {
                "provider": "x", "provider_status": "ok", "sync_status": "synced",
                "items": items,
                "raw": {"actor_id": self.actor_id, "input": input_payload},
            }
        except Exception as exc:  # pragma: no cover
            return {
                "provider": "x", "provider_status": "error", "sync_status": "error",
                "items": [], "error": str(exc),
            }

    # ─── 公开接口 ─────────────────────────────────

    def crawl_channel_profile(self, handle_or_url: str, *, channel_id: str = "", max_posts: int = 12) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_channel_profile")
        
        ref = self.normalize_handle_ref(handle_or_url)
        if ref["kind"] == "empty":
            return {"provider": "x", "provider_status": "no_results", "sync_status": "no_results", "items": [], "message": "handle 为空"}
        
        # 优先官方 API
        if self.use_official_api and ref["kind"] == "handle":
            result = self._x_api_request(
                f"/users/by/username/{ref['value']}",
                {"user.fields": "public_metrics,description,verified,profile_image_url"},
            )
            if result.get("data"):
                return {
                    "provider": "x", "provider_status": "ok", "sync_status": "synced",
                    "items": [result["data"]], "query": ref, "mode": "official_api",
                }
        
        # 降级到 Apify
        if self.api_token:
            input_payload = {
                "twitterHandles": [ref["value"]] if ref["kind"] == "handle" else [],
                "maxItems": max(1, min(50, int(max_posts or 12))),
            }
            result = self._apify_run(input_payload)
            result["query"] = ref
            result["mode"] = "apify"
            return result
        
        return self._not_configured("crawl_channel_profile")

    def crawl_channel_videos(self, channel_id: str, *, max_results: int = 25) -> dict[str, Any]:
        return self.crawl_channel_profile(channel_id, max_posts=max_results)

    def crawl_video_comments(self, video_id: str, *, max_results: int = 50) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_video_comments")
        return {"provider": "x", "provider_status": "not_implemented", "sync_status": "not_configured", "items": [], "message": "X 评论 (replies) 留 R-Phase3.2"}
