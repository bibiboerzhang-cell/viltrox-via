"""PRAW (official Reddit API) path for the V-KPI Reddit crawler.

W4 class-LOC 拆刀:从 ``reddit_crawler.RedditCrawler`` 逐字搬出的 PRAW 段
(client 懒初始化 / subreddit 抓取 / 品牌搜索 / 嵌套评论扁平化 / Submission
转 V-KPI dict)。与既有 ``RedditJsonPathMixin`` 同款 mixin 形态,方法仍从
实例可达,实例级 monkeypatch 面不变。本模块只依赖可选的 ``praw`` 与 stdlib,
不 import 包内模块(import-time 环棘轮)。
"""

from __future__ import annotations

from typing import Any

# PRAW import is optional - graceful degradation
try:
    import praw  # type: ignore
    _PRAW_AVAILABLE = True
except ImportError:
    _PRAW_AVAILABLE = False
    praw = None


class RedditPrawPathMixin:
    """PRAW 主路径:被 RedditCrawler 混入,依赖其 client_id/client_secret/user_agent。"""

    client_id: str
    client_secret: str
    user_agent: str

    def _get_praw_client(self):
        """Lazy-init PRAW Reddit client. Returns None if not available."""
        if not _PRAW_AVAILABLE:
            return None
        if not (self.client_id and self.client_secret):
            return None

        if self._praw_client is None:
            try:
                self._praw_client = praw.Reddit(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    user_agent=self.user_agent,
                    check_for_async=False,
                )
                # Read-only mode (no user OAuth)
                self._praw_client.read_only = True
            except Exception:
                self._praw_client = None

        return self._praw_client

    def _crawl_subreddit_via_praw(self, subreddit: str, limit: int) -> dict[str, Any]:
        """PRAW path: subreddit profile + posts."""
        client = self._get_praw_client()
        if client is None:
            return {
                "items": [],
                "provider_status": "not_configured",
                "sync_status": "skip",
                "provider": "praw",
                "error": "PRAW client unavailable",
            }

        try:
            sr = client.subreddit(subreddit)
            # Profile data
            profile_item = {
                "type": "subreddit_profile",
                "id": sr.id,
                "display_name": sr.display_name,
                "title": sr.title,
                "description": sr.public_description,
                "subscribers": sr.subscribers,  # ← maps to followers
                "accounts_active": sr.accounts_active,
                "created_utc": sr.created_utc,
                "over18": sr.over18,
                "url": f"https://reddit.com/r/{sr.display_name}",
            }
            # Posts (hot)
            posts = []
            for submission in sr.hot(limit=limit):
                posts.append(self._praw_post_to_dict(submission))

            return {
                "items": [profile_item] + posts,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "praw",
            }
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "praw",
                "error": str(exc)[:500],
            }

    def _crawl_brand_mentions_via_praw(
        self, query: str, limit: int
    ) -> dict[str, Any]:
        """PRAW path: search-based brand mention discovery."""
        client = self._get_praw_client()
        if client is None:
            return {
                "items": [],
                "provider_status": "not_configured",
                "sync_status": "skip",
                "provider": "praw",
            }

        try:
            results = client.subreddit("all").search(
                query, sort="new", time_filter="month", limit=limit
            )
            posts = [self._praw_post_to_dict(s) for s in results]
            return {
                "items": posts,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "praw",
                "query": query,
            }
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "praw",
                "error": str(exc)[:500],
            }

    def _crawl_post_comments_via_praw(
        self, post_id: str, max_depth: int = 3
    ) -> dict[str, Any]:
        """PRAW path: nested comments with depth limit."""
        client = self._get_praw_client()
        if client is None:
            return {
                "items": [],
                "provider_status": "not_configured",
                "sync_status": "skip",
            }

        try:
            submission = client.submission(id=post_id)
            submission.comments.replace_more(limit=0)  # ignore "more" placeholders

            comments = []
            self._flatten_comments(
                submission.comments,
                comments,
                depth=0,
                max_depth=max_depth,
                parent_id=None,
            )

            return {
                "items": comments,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "praw",
                "post_id": post_id,
            }
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "praw",
                "error": str(exc)[:500],
            }

    def _flatten_comments(
        self,
        comment_forest,
        out: list,
        *,
        depth: int,
        max_depth: int,
        parent_id: str | None,
    ) -> None:
        """Recursively flatten Reddit CommentForest with depth limit."""
        if depth > max_depth:
            return
        for comment in comment_forest:
            if not hasattr(comment, "id"):
                continue
            out.append(
                {
                    "id": comment.id,
                    "parent_id": parent_id,
                    "depth": depth,
                    "author": (
                        comment.author.name if comment.author else "[deleted]"
                    ),
                    "body": (comment.body or "")[:5000],  # cap text length
                    "score": comment.score,
                    "created_utc": comment.created_utc,
                    "is_submitter": comment.is_submitter,
                }
            )
            # Recurse
            if hasattr(comment, "replies") and depth < max_depth:
                self._flatten_comments(
                    comment.replies,
                    out,
                    depth=depth + 1,
                    max_depth=max_depth,
                    parent_id=comment.id,
                )

    def _praw_post_to_dict(self, submission) -> dict[str, Any]:
        """Convert PRAW Submission to V-KPI dict format."""
        return {
            "type": "post",
            "id": submission.id,
            "title": submission.title,
            "author": (submission.author.name if submission.author else "[deleted]"),
            "subreddit": submission.subreddit.display_name,
            "permalink": f"https://reddit.com{submission.permalink}",
            "url": submission.url,
            "selftext": (submission.selftext or "")[:5000],
            "score": submission.score,  # ← maps to likes
            "upvote_ratio": submission.upvote_ratio,
            "num_comments": submission.num_comments,  # ← maps to comments
            "created_utc": submission.created_utc,
            "is_video": submission.is_video,
            "is_self": submission.is_self,
            "over_18": submission.over_18,
            "stickied": submission.stickied,
        }
