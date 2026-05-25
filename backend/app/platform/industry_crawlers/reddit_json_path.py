"""Public Reddit JSON listing/comment path for the V-KPI Reddit crawler."""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class RedditJsonPathMixin:
    user_agent: str

    def _reddit_get_json(self, url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _clean_media_url(value: Any) -> str:
        url = str(value or "").replace("&amp;", "&").strip()
        if url.startswith("//"):
            url = f"https:{url}"
        return url if url.startswith(("http://", "https://")) else ""

    def _push_media_url(self, urls: list[str], value: Any) -> None:
        url = self._clean_media_url(value)
        if url and url not in urls:
            urls.append(url)

    @staticmethod
    def _reddit_video_url(data: dict[str, Any]) -> str:
        for key in ("secure_media", "media"):
            media = data.get(key) if isinstance(data.get(key), dict) else {}
            video = media.get("reddit_video") if isinstance(media.get("reddit_video"), dict) else {}
            fallback = str(video.get("fallback_url") or "").replace("&amp;", "&").strip()
            if fallback.startswith(("http://", "https://")):
                return fallback
        return ""

    def _json_post_to_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        created = data.get("created_utc")
        created_at = ""
        if created is not None:
            try:
                created_at = datetime.utcfromtimestamp(float(created)).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                created_at = ""
        media_urls: list[str] = []
        preview = data.get("preview") if isinstance(data.get("preview"), dict) else {}
        images = preview.get("images") if isinstance(preview.get("images"), list) else []
        for image in images:
            source = image.get("source") if isinstance(image, dict) and isinstance(image.get("source"), dict) else {}
            self._push_media_url(media_urls, source.get("url"))
        gallery = data.get("gallery_data") if isinstance(data.get("gallery_data"), dict) else {}
        gallery_items = gallery.get("items") if isinstance(gallery.get("items"), list) else []
        media_metadata = data.get("media_metadata") if isinstance(data.get("media_metadata"), dict) else {}
        for gallery_item in gallery_items:
            media_id = gallery_item.get("media_id") if isinstance(gallery_item, dict) else ""
            metadata = media_metadata.get(str(media_id)) if isinstance(media_metadata.get(str(media_id)), dict) else {}
            source = metadata.get("s") if isinstance(metadata.get("s"), dict) else {}
            self._push_media_url(media_urls, source.get("u"))
        direct_url = data.get("url_overridden_by_dest") or data.get("url")
        if str(data.get("post_hint") or "").lower() == "image":
            self._push_media_url(media_urls, direct_url)
        video_url = self._reddit_video_url(data)
        return {
            "dataType": "post",
            "type": "post",
            "id": data.get("name") or f"t3_{data.get('id')}",
            "parsedId": data.get("id"),
            "name": data.get("name"),
            "title": data.get("title"),
            "body": data.get("selftext"),
            "author": data.get("author"),
            "username": data.get("author"),
            "subreddit": data.get("subreddit"),
            "communityName": data.get("subreddit"),
            "url": f"https://www.reddit.com{data.get('permalink')}" if data.get("permalink") else data.get("url"),
            "permalink": f"https://www.reddit.com{data.get('permalink')}" if data.get("permalink") else "",
            "externalUrl": data.get("url_overridden_by_dest") or data.get("url"),
            "thumbnailUrl": data.get("thumbnail") if str(data.get("thumbnail") or "").startswith("http") else (media_urls[0] if media_urls else ""),
            "images": media_urls,
            "videoUrl": video_url,
            "flair": data.get("link_flair_text"),
            "linkFlairText": data.get("link_flair_text"),
            "createdAt": created_at,
            "created_utc": created,
            "score": data.get("score"),
            "upVotes": data.get("ups"),
            "ups": data.get("ups"),
            "downs": data.get("downs"),
            "upvoteRatio": data.get("upvote_ratio"),
            "upvote_ratio": data.get("upvote_ratio"),
            "numberOfComments": data.get("num_comments"),
            "num_comments": data.get("num_comments"),
            "isVideo": data.get("is_video"),
            "isSelf": data.get("is_self"),
            "over18": data.get("over_18"),
            "pinned": data.get("stickied"),
            "stickied": data.get("stickied"),
            "locked": data.get("locked"),
            "removed": bool(data.get("removed_by_category")),
            "removedByCategory": data.get("removed_by_category"),
        }

    def _crawl_subreddit_via_json_api(self, subreddit: str, limit: int) -> dict[str, Any]:
        max_items = max(1, min(5000, int(limit or 100)))
        items: list[dict[str, Any]] = []
        after = ""
        try:
            about = self._reddit_get_json(f"https://www.reddit.com/r/{subreddit}/about.json")
            data = about.get("data") if isinstance(about.get("data"), dict) else {}
            items.append(
                {
                    "dataType": "community",
                    "type": "subreddit_profile",
                    "id": data.get("id"),
                    "display_name": data.get("display_name") or subreddit,
                    "title": data.get("title"),
                    "description": data.get("public_description"),
                    "subscribers": data.get("subscribers"),
                    "numberOfMembers": data.get("subscribers"),
                    "accounts_active": data.get("active_user_count"),
                    "createdAt": datetime.utcfromtimestamp(float(data.get("created_utc") or 0)).strftime("%Y-%m-%dT%H:%M:%SZ") if data.get("created_utc") else "",
                    "url": f"https://www.reddit.com/r/{data.get('display_name') or subreddit}/",
                    "iconUrl": data.get("community_icon") or data.get("icon_img"),
                    "bannerUrl": data.get("banner_background_image") or data.get("banner_img"),
                }
            )
            while len(items) - 1 < max_items:
                params = {"limit": min(100, max_items - (len(items) - 1)), "raw_json": 1}
                if after:
                    params["after"] = after
                listing = self._reddit_get_json(f"https://www.reddit.com/r/{subreddit}/new.json?{urlencode(params)}")
                listing_data = listing.get("data") if isinstance(listing.get("data"), dict) else {}
                children = listing_data.get("children") if isinstance(listing_data.get("children"), list) else []
                if not children:
                    break
                for child in children:
                    if isinstance(child, dict) and child.get("kind") == "t3":
                        post_data = child.get("data") if isinstance(child.get("data"), dict) else {}
                        if post_data:
                            items.append(self._json_post_to_dict(post_data))
                after = str(listing_data.get("after") or "")
                if not after:
                    break
                time.sleep(0.25)
            return {"items": items, "provider_status": "ok", "sync_status": "ok", "provider": "reddit_json"}
        except Exception as exc:
            return {"items": [], "provider_status": "error", "sync_status": "fail", "provider": "reddit_json", "error": str(exc)[:500]}

    def _json_comment_to_dict(self, data: dict[str, Any], *, depth: int, parent_id: str | None, post_id: str) -> dict[str, Any]:
        created = data.get("created_utc")
        created_at = ""
        if created is not None:
            try:
                created_at = datetime.utcfromtimestamp(float(created)).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                created_at = ""
        return {
            "id": data.get("id"),
            "parent_id": parent_id or data.get("parent_id"),
            "postId": post_id,
            "depth": depth,
            "author": data.get("author"),
            "body": data.get("body"),
            "score": data.get("score"),
            "ups": data.get("ups"),
            "created_utc": created,
            "created_at": created_at,
            "is_submitter": data.get("is_submitter"),
        }

    def _flatten_json_comments(self, children: list[Any], out: list[dict[str, Any]], *, depth: int, max_depth: int, parent_id: str | None, post_id: str) -> None:
        if depth > max_depth:
            return
        for child in children:
            if not isinstance(child, dict) or child.get("kind") != "t1":
                continue
            data = child.get("data") if isinstance(child.get("data"), dict) else {}
            comment_id = str(data.get("id") or "").strip()
            body = str(data.get("body") or "").strip()
            if not comment_id or not body:
                continue
            out.append(self._json_comment_to_dict(data, depth=depth, parent_id=parent_id, post_id=post_id))
            replies = data.get("replies")
            if depth >= max_depth or not isinstance(replies, dict):
                continue
            replies_data = replies.get("data") if isinstance(replies.get("data"), dict) else {}
            reply_children = replies_data.get("children") if isinstance(replies_data.get("children"), list) else []
            self._flatten_json_comments(reply_children, out, depth=depth + 1, max_depth=max_depth, parent_id=comment_id, post_id=post_id)

    def _crawl_post_comments_via_json_api(self, post_id: str, max_depth: int = 3) -> dict[str, Any]:
        clean_post_id = post_id.replace("t3_", "").strip()
        try:
            payload = self._reddit_get_json(f"https://www.reddit.com/comments/{clean_post_id}.json?limit=500&raw_json=1")
            listing = payload[1] if isinstance(payload, list) and len(payload) > 1 else {}
            data = listing.get("data") if isinstance(listing, dict) and isinstance(listing.get("data"), dict) else {}
            children = data.get("children") if isinstance(data.get("children"), list) else []
            comments: list[dict[str, Any]] = []
            self._flatten_json_comments(children, comments, depth=0, max_depth=max(0, min(8, int(max_depth or 3))), parent_id=None, post_id=clean_post_id)
            return {"items": comments, "provider_status": "ok", "sync_status": "ok", "provider": "reddit_json", "post_id": clean_post_id}
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "reddit_json",
                "post_id": clean_post_id,
                "error": str(exc)[:500],
            }
