"""backend/app/services/vkpi/industry_crawlers/twitch_crawler.py

R-Phase2-B: Twitch 抓取适配器 (官方 Helix API)

策略:
  - 用 Twitch Helix API (官方,免费配额够大)
  - 不需要 Apify
  - 月度成本 $0 (免费层)

环境变量:
  TWITCH_CLIENT_ID
  TWITCH_CLIENT_SECRET (用于换 OAuth token)

接口:
  - https://dev.twitch.tv/docs/api/

注意:
  - Twitch 免费层 800 reads/分钟
  - 不需要 Apify,成本最低
  - rate limit 是按分钟,几乎不会触发
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Any


TWITCH_HELIX = "https://api.twitch.tv/helix"
TWITCH_OAUTH = "https://id.twitch.tv/oauth2/token"


class TwitchCrawler:
    """Twitch via Helix API."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        timeout_seconds: int = 20,
    ) -> None:
        self.client_id = (client_id or os.environ.get("TWITCH_CLIENT_ID") or "").strip()
        self.client_secret = (client_secret or os.environ.get("TWITCH_CLIENT_SECRET") or "").strip()
        self.timeout_seconds = max(3, min(60, int(timeout_seconds or 20)))
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def provider_status(self) -> dict[str, Any]:
        return {
            "provider": "twitch",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "key_visible": False,
        }

    def _not_configured(self, operation: str) -> dict[str, Any]:
        return {
            "provider": "twitch",
            "operation": operation,
            "provider_status": "not_configured",
            "sync_status": "not_configured",
            "items": [],
            "raw": {},
            "message": "TWITCH_CLIENT_ID 或 TWITCH_CLIENT_SECRET 未配置。",
        }

    @staticmethod
    def normalize_handle_ref(value: str) -> dict[str, str]:
        raw = str(value or "").strip()
        if not raw:
            return {"kind": "empty", "value": ""}
        
        # URL: https://www.twitch.tv/{handle}
        if "://" in raw:
            match = re.search(r"twitch\.tv/([a-zA-Z0-9_]+)", raw)
            if match:
                return {"kind": "login", "value": match.group(1)}
        
        if re.match(r"^[a-zA-Z0-9_]{4,25}$", raw):
            return {"kind": "login", "value": raw}
        
        return {"kind": "query", "value": raw}

    # ─── OAuth Token 管理 ───────────────────

    def _get_access_token(self) -> str:
        """获取 / 刷新 Twitch app access token (有效期 ~60 天)"""
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token
        
        if not self.configured:
            return ""
        
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }).encode("utf-8")
        
        try:
            request = urllib.request.Request(TWITCH_OAUTH, data=data, method="POST")
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                body = response.read().decode("utf-8")
            payload = json.loads(body or "{}")
            self._access_token = str(payload.get("access_token") or "")
            self._token_expires_at = now + int(payload.get("expires_in") or 0)
            return self._access_token
        except Exception:
            return ""

    def _helix_request(self, endpoint: str, params: dict | None = None) -> dict[str, Any]:
        token = self._get_access_token()
        if not token:
            return {"error": "no_access_token"}
        
        query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in (params or {}).items())
        url = f"{TWITCH_HELIX}{endpoint}{'?' + query if query else ''}"
        
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Client-Id": self.client_id,
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                body = response.read().decode("utf-8")
            return json.loads(body or "{}")
        except Exception as exc:  # pragma: no cover
            return {"error": str(exc)}

    # ─── 公开接口 ─────────────────────────────

    def crawl_channel_profile(self, handle_or_url: str, *, channel_id: str = "", max_posts: int = 12) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_channel_profile")
        
        ref = self.normalize_handle_ref(handle_or_url)
        if ref["kind"] not in ("login",):
            return {"provider": "twitch", "provider_status": "no_results", "sync_status": "no_results", "items": [], "message": "需要 login (handle)"}
        
        result = self._helix_request("/users", {"login": ref["value"]})
        items = result.get("data") or []
        
        return {
            "provider": "twitch",
            "provider_status": "ok" if items else "no_results",
            "sync_status": "synced" if items else "no_results",
            "items": items,
            "query": ref,
            "raw": {"helix_users": result},
        }

    def crawl_channel_videos(self, channel_id: str, *, max_results: int = 25) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_channel_videos")
        if not channel_id:
            return {"provider": "twitch", "provider_status": "no_results", "sync_status": "no_results", "items": [], "message": "channel_id (broadcaster_id) 为空"}
        
        result = self._helix_request("/videos", {
            "user_id": channel_id,
            "first": max(1, min(100, int(max_results or 25))),
        })
        items = result.get("data") or []
        
        return {
            "provider": "twitch",
            "provider_status": "ok" if items else "no_results",
            "sync_status": "synced" if items else "no_results",
            "items": items,
            "raw": {"helix_videos": result},
        }

    def crawl_video_comments(self, video_id: str, *, max_results: int = 50) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_video_comments")
        # Twitch chat 历史走 Helix /chat,留 sentiment 时实装
        return {"provider": "twitch", "provider_status": "not_implemented", "sync_status": "not_configured", "items": [], "message": "Twitch chat 留 R-Phase3.2"}
