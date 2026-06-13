"""backend/app/services/vkpi/industry_crawlers/instagram_crawler.py

R-Phase2-A: Instagram 抓取适配器 (Apify scraper)

接口规范与 YouTubeCrawler 完全对齐,确保 industry_snapshot_collector
的 dispatch 逻辑能以同样方式调用.

关键设计:
  - 没配置 APIFY_TOKEN → 返回 not_configured,不假数据
  - 调用失败 → 返回 error 状态,不抛
  - 月度预算控制由 platform_crawl_settings + R59 firewall_decorator 负责
  
Apify Actor 选型:
  - apify/instagram-profile-scraper (默认,获取 profile + recent posts)
  - apify/instagram-post-scraper (备选,纯 posts)
  
环境变量:
  APIFY_TOKEN: Apify 访问 token
  APIFY_INSTAGRAM_ACTOR_ID: Apify actor ID (默认 apify/instagram-profile-scraper)
"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any


DEFAULT_ACTOR_ID = "apify~instagram-profile-scraper"
DEFAULT_POSTS_ACTOR_ID = "apify~instagram-scraper"
DEFAULT_RUN_TIMEOUT_SECONDS = 120
DEFAULT_DATASET_LIMIT = 50
DEFAULT_MAX_POST_RESULTS = 5000


def _max_post_results() -> int:
    try:
        return max(1, min(10000, int(os.environ.get("VKPI_INSTAGRAM_MAX_POST_RESULTS") or DEFAULT_MAX_POST_RESULTS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_POST_RESULTS


class InstagramCrawler:
    """Instagram via Apify scraper."""

    def __init__(
        self,
        api_token: str | None = None,
        *,
        actor_id: str | None = None,
        timeout_seconds: int = 30,
        run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
    ) -> None:
        self.api_token = (api_token or os.environ.get("APIFY_TOKEN") or "").strip()
        self.actor_id = (actor_id or os.environ.get("APIFY_INSTAGRAM_ACTOR_ID") or DEFAULT_ACTOR_ID).strip()
        self.posts_actor_id = (os.environ.get("APIFY_INSTAGRAM_POSTS_ACTOR_ID") or DEFAULT_POSTS_ACTOR_ID).strip()
        self.timeout_seconds = max(3, min(60, int(timeout_seconds or 30)))
        self.run_timeout_seconds = max(30, min(1800, int(run_timeout_seconds or DEFAULT_RUN_TIMEOUT_SECONDS)))

    @property
    def configured(self) -> bool:
        return bool(self.api_token)

    def provider_status(self) -> dict[str, Any]:
        return {
            "provider": "instagram",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "actor_id": self.actor_id,
            "key_visible": False,
        }

    def _not_configured(self, operation: str) -> dict[str, Any]:
        return {
            "provider": "instagram",
            "operation": operation,
            "provider_status": "not_configured",
            "sync_status": "not_configured",
            "items": [],
            "raw": {},
            "message": "APIFY_TOKEN 未配置,Instagram 抓取未执行。",
        }

    @staticmethod
    def normalize_handle_ref(value: str) -> dict[str, str]:
        """从 URL / @handle / handle 提取规范化的 handle"""
        raw = str(value or "").strip()
        if not raw:
            return {"kind": "empty", "value": ""}
        
        # URL 形式
        if "://" in raw:
            parsed = urllib.parse.urlparse(raw)
            path = parsed.path.strip("/")
            if path:
                handle = path.split("/")[0]
                return {"kind": "handle", "value": handle.lstrip("@")}
        
        # @handle 形式
        if raw.startswith("@"):
            return {"kind": "handle", "value": raw[1:]}
        
        # 纯 handle
        if re.match(r"^[a-zA-Z0-9_.]+$", raw):
            return {"kind": "handle", "value": raw}
        
        return {"kind": "query", "value": raw}

    # ─── Apify 调用 ──────────────────────────────────

    def _start_run(self, input_payload: dict[str, Any], *, actor_id: str | None = None) -> dict[str, Any]:
        """启动 Apify run,等待完成,返回 dataset items"""
        if not self.configured:
            return self._not_configured("apify_run")

        try:
            from apify_client import ApifyClient  # type: ignore

            selected_actor_id = actor_id or self.actor_id
            actor_path = selected_actor_id.replace("/", "~")
            client = ApifyClient(self.api_token)
            run = client.actor(actor_path).call(
                run_input=input_payload,
                timeout_secs=self.run_timeout_seconds,
                wait_secs=self.run_timeout_seconds,
            )
            if not run or str(run.get("status") or "").upper() != "SUCCEEDED":
                return {
                    "provider": "instagram",
                    "provider_status": "error",
                    "sync_status": "error",
                    "items": [],
                    "error": f"Instagram actor did not finish: {str((run or {}).get('status') or 'unknown')}",
                    "raw": {"actor_id": selected_actor_id, "input": input_payload},
                }
            from . import record_apify_run_cost

            record_apify_run_cost(run, platform="instagram", actor_id=selected_actor_id, operation="start_run")
            items = list(client.dataset(run.get("defaultDatasetId")).iterate_items())
            return {
                "provider": "instagram",
                "provider_status": "ok",
                "sync_status": "synced",
                "items": items,
                "raw": {"actor_id": selected_actor_id, "input": input_payload},
            }
        except ImportError:
            return {
                "provider": "instagram",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": "apify-client not installed",
                "raw": {"actor_id": actor_id or self.actor_id},
            }
        except Exception as exc:  # pragma: no cover - 实际调用才会触发
            return {
                "provider": "instagram",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": str(exc),
                "raw": {"actor_id": actor_id or self.actor_id},
            }
    # ─── 公开接口 (与 YouTubeCrawler 对齐) ─────────

    def crawl_channel_profile(
        self,
        handle_or_url: str,
        *,
        channel_id: str = "",
        max_posts: int = 12,
        since: str = "",
    ) -> dict[str, Any]:
        """抓取 IG 账号 profile + 最近 N 帖

        since: 增量游标(ISO YYYY-MM-DD)。空=维持现状(向后兼容)。
        profile-scraper actor 不支持日期字段 → since 非空仅放宽 resultsLimit 窗口(12→48),
        真正日期裁剪由 url_deep_crawl._filter_incremental_profile_videos 客户端完成。
        """
        if not self.configured:
            return self._not_configured("crawl_channel_profile")

        ref = self.normalize_handle_ref(handle_or_url)
        if ref["kind"] == "empty":
            return {
                "provider": "instagram",
                "provider_status": "no_results",
                "sync_status": "no_results",
                "items": [],
                "message": "handle 为空",
            }

        # Apify input 格式 (instagram-profile-scraper)
        # since 非空且 actor 无日期游标 → 放宽 resultsLimit 窗口(12→48)给客户端裁剪留余量;空=原口径不变。
        _results_cap = 48 if str(since or "").strip() else 12
        input_payload: dict[str, Any] = {
            "usernames": [ref["value"]] if ref["kind"] == "handle" else [],
            "resultsLimit": max(1, min(_results_cap, int(max_posts or 12))),
            "addParentData": False,
        }
        if ref["kind"] == "query":
            # 走 search,但默认 actor 不一定支持,作为兜底
            input_payload["searchType"] = "user"
            input_payload["search"] = ref["value"]
        
        result = self._start_run(input_payload)
        result["query"] = ref
        return result

    def crawl_channel_videos(
        self,
        channel_id: str,
        *,
        max_results: int = 25,
        since: str = "",
    ) -> dict[str, Any]:
        """抓取最近视频/帖子. 对 IG 来说 channel_id 可以是 username 或 profile URL.

        since: 增量游标(ISO YYYY-MM-DD)。空=维持现状。posts actor apify~instagram-scraper
        支持 onlyPostsNewerThan → since 非空时下推到爬虫端(真增量)。
        """
        if not self.configured:
            return self._not_configured("crawl_channel_videos")

        if not channel_id:
            return {
                "provider": "instagram",
                "provider_status": "no_results",
                "sync_status": "no_results",
                "items": [],
                "message": "channel_id (username) 为空",
            }

        ref = self.normalize_handle_ref(channel_id)
        if ref["kind"] == "handle":
            direct_url = f"https://www.instagram.com/{ref['value'].lstrip('@')}/"
        else:
            direct_url = str(channel_id or "").strip()
        input_payload = {
            "directUrls": [direct_url],
            "resultsType": "posts",
            "resultsLimit": max(1, min(_max_post_results(), int(max_results or 25))),
            "addParentData": False,
        }
        _since = str(since or "").strip()
        if _since:
            input_payload["onlyPostsNewerThan"] = _since
        return self._start_run(input_payload, actor_id=self.posts_actor_id)

    def crawl_video_comments(
        self,
        video_id: str,
        *,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """抓取帖子评论 (Apify instagram-comment-scraper)."""
        if not self.configured:
            return self._not_configured("crawl_video_comments")

        post_url = self._comment_url(video_id)
        if not post_url:
            return {
                "provider": "instagram",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": "could not construct Instagram post URL",
            }

        try:
            from apify_client import ApifyClient  # type: ignore
        except ImportError:
            return {
                "provider": "instagram",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": "apify-client not installed",
            }

        actor_id = (os.environ.get("APIFY_INSTAGRAM_COMMENT_ACTOR_ID") or "apify~instagram-comment-scraper").replace("/", "~")
        try:
            client = ApifyClient(self.api_token)
            run = client.actor(actor_id).call(
                run_input={
                    "directUrls": [post_url],
                    "resultsLimit": max(1, min(100, int(max_results or 50))),
                },
                timeout_secs=self.run_timeout_seconds,
                wait_secs=self.run_timeout_seconds,
            )
            from . import record_apify_run_cost

            record_apify_run_cost(run, platform="instagram", actor_id=actor_id, operation="crawl_video_comments")
            dataset_id = run.get("defaultDatasetId")
            items = list(client.dataset(dataset_id).iterate_items()) if dataset_id else []
            return {
                "provider": "instagram",
                "provider_status": "ok",
                "sync_status": "synced",
                "items": items,
                "raw": {"actor_id": actor_id, "post_url": post_url},
            }
        except Exception as exc:  # pragma: no cover - live provider path
            return {
                "provider": "instagram",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": str(exc)[:500],
                "raw": {"actor_id": actor_id, "post_url": post_url},
            }

    @staticmethod
    def _comment_url(video_id_or_url: str) -> str:
        """Normalize Instagram shortcode or URL for comment actor input."""
        raw = str(video_id_or_url or "").strip()
        if not raw:
            return ""
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        return f"https://www.instagram.com/p/{raw.strip('/')}/"
