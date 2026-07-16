#!/usr/bin/env python3
"""P2.29 smoke for non-YouTube single-account refresh safety.

Default mode is offline and does not call external providers. It verifies that
Instagram/TikTok/Bilibili/Xiaohongshu account refreshes do not write fake data
when crawl is disabled, and that the shared collect_account_snapshot() write
seam persists one real-shaped raw fixture for each platform.

Set VKPI_P2_29_LIVE=1 to run one minimal live refresh for a single platform.
The live path is explicit, uses platform-specific profile URLs, bypasses local
Settings gates with force_local=True, and never prints provider keys.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "postgres")
os.environ.setdefault("DATABASE_URL", os.environ.get("LOCAL_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/viltrox2"))

from app.core.security import make_token
from app.db.connection import get_conn
from app.services.vkpi import industry_data, industry_snapshot_collector
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema

BASE = os.environ.get("VKPI_SMOKE_BASE", "http://127.0.0.1:8102")
PREFIX = "vkpi-p2-29-refresh-"
OFFLINE_PLATFORMS = ("instagram", "tiktok", "bilibili", "xiaohongshu")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _profile_ref(platform: str, handle: str) -> tuple[str, str]:
    clean = str(handle or "").strip().lstrip("@")
    if platform == "instagram":
        return clean, f"https://www.instagram.com/{clean}/"
    if platform == "tiktok":
        return clean, f"https://www.tiktok.com/@{clean}"
    if platform == "bilibili":
        mid = clean if clean.isdigit() else "373471445"
        return mid, f"https://space.bilibili.com/{mid}"
    if platform == "xiaohongshu":
        user_id = clean or "60346fc0000000000101c9be"
        return user_id, f"https://www.xiaohongshu.com/user/profile/{user_id}"
    return clean, clean


def _raw_fixture(platform: str, marker: str) -> dict[str, Any]:
    post_id = f"{marker}-{platform}-post"
    if platform == "instagram":
        return {
            "source": "p2_29_instagram_fixture",
            "profile": {
                "items": [
                    {
                        "username": f"{marker}_ig",
                        "followersCount": 12345,
                        "postsCount": 42,
                    }
                ]
            },
            "videos": [
                {
                    "id": post_id,
                    "url": f"https://www.instagram.com/p/{post_id}/",
                    "caption": f"{marker} Instagram Viltrox lens QA",
                    "timestamp": "2026-05-10T08:00:00Z",
                    "displayUrl": f"https://img.example/{post_id}.jpg",
                    "likesCount": 321,
                    "commentsCount": 23,
                    "videoViewCount": 4567,
                }
            ],
        }
    if platform == "tiktok":
        return {
            "source": "p2_29_tiktok_fixture",
            "profile": {
                "items": [
                    {
                        "authorMeta": {
                            "name": f"{marker}_tt",
                            "fans": 9876,
                            "video": 31,
                        }
                    }
                ]
            },
            "videos": [
                {
                    "id": post_id,
                    "url": f"https://www.tiktok.com/@viltrox/video/{post_id}",
                    "text": f"{marker} TikTok autofocus QA",
                    "timestamp": "2026-05-10T08:00:00Z",
                    "playCount": 7654,
                    "diggCount": 543,
                    "commentCount": 32,
                    "shareCount": 12,
                }
            ],
        }
    if platform == "bilibili":
        return {
            "source": "p2_29_bilibili_fixture",
            "profile": {
                "items": [
                    {
                        "mid": "373471445",
                        "fans": 24680,
                        "archiveCount": 64,
                    }
                ]
            },
            "videos": [
                {
                    "id": post_id,
                    "url": f"https://www.bilibili.com/video/{post_id}",
                    "title": f"{marker} Bilibili creator QA",
                    "published_at": "2026-05-10T08:00:00Z",
                    "play": 8888,
                    "like": 777,
                    "reply_count": 66,
                    "share": 21,
                }
            ],
        }
    if platform == "xiaohongshu":
        return {
            "source": "p2_29_xiaohongshu_fixture",
            "profile": {
                "items": [
                    {
                        "userId": "60346fc0000000000101c9be",
                        "fansCount": 13579,
                        "noteCount": 28,
                    }
                ]
            },
            "videos": [
                {
                    "id": post_id,
                    "url": f"https://www.xiaohongshu.com/explore/{post_id}",
                    "title": f"{marker} Xiaohongshu lens QA",
                    "timestamp": "2026-05-10T08:00:00Z",
                    "viewCount": 2345,
                    "likedCount": 123,
                    "commentCount": 0,
                    "collectCount": 45,
                    "shareCount": 6,
                }
            ],
        }
    raise ValueError(f"unsupported fixture platform: {platform}")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time() * 1000)}-{os.getpid()}"
        self.now = _now()
        self.conn = get_conn()
        self.user_id = 0
        self.staff_id = 0
        self.token = ""

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:800]}") from exc

    def staff(self) -> dict[str, Any]:
        return {"id": self.staff_id, "role": "admin", "is_owner": True}

    def cleanup(self) -> dict[str, int]:
        c = self.conn
        like = f"%{self.marker}%"
        project_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_industry_projects WHERE name LIKE ? OR metadata_json LIKE ?", (like, like)).fetchall()]
        account_where = "handle LIKE ? OR profile_url LIKE ? OR raw_platform_data LIKE ? OR notes LIKE ?"
        account_params: list[Any] = [like, like, like, like]
        if project_ids:
            account_where += f" OR project_id IN ({','.join('?' for _ in project_ids)})"
            account_params.extend(project_ids)
        account_ids = [int(r["id"]) for r in c.execute(f"SELECT id FROM vkpi_industry_accounts WHERE {account_where}", tuple(account_params)).fetchall()]
        user_ids = [int(r["id"]) for r in c.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        post_ids = [int(r["id"]) for r in c.execute(
            f"SELECT id FROM vkpi_industry_posts WHERE account_id IN ({','.join('?' for _ in account_ids)})" if account_ids else "SELECT id FROM vkpi_industry_posts WHERE 1=0",
            account_ids,
        ).fetchall()]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if ids:
                c.execute(f"DELETE FROM {table} WHERE {column} IN ({','.join('?' for _ in ids)})", ids)

        delete_in("vkpi_industry_post_metrics", "post_id", post_ids)
        delete_in("vkpi_industry_posts", "id", post_ids)
        delete_in("vkpi_industry_account_snapshots", "account_id", account_ids)
        delete_in("vkpi_industry_accounts", "id", account_ids)
        delete_in("vkpi_industry_projects", "id", project_ids)
        delete_in("staff", "user_id", user_ids)
        delete_in("users", "id", user_ids)
        c.commit()
        return {
            "accounts": int(c.execute(f"SELECT COUNT(*) AS n FROM vkpi_industry_accounts WHERE {account_where}", tuple(account_params)).fetchone()["n"]),
            "projects": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_industry_projects WHERE name LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "users": int(c.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
        }

    def seed_actor(self) -> None:
        email = f"{self.marker}@viltrox.com"
        self.conn.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified) VALUES (?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", self.marker, "approved", "admin", 1),
        )
        self.user_id = int(self.conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        staff_cols = {str(row["name"]) for row in self.conn.execute("PRAGMA table_info(staff)").fetchall()}
        cols = ["user_id", "role", "permissions_json", "active", "invited_at"]
        vals: list[Any] = [self.user_id, "admin", _json({"vkpi": "admin"}), 1, self.now]
        if "is_owner" in staff_cols:
            cols.append("is_owner")
            vals.append(1)
        if "email_domain_verified" in staff_cols:
            cols.append("email_domain_verified")
            vals.append(1)
        self.conn.execute(f"INSERT INTO staff ({', '.join(cols)}) VALUES ({','.join('?' for _ in cols)})", vals)
        self.staff_id = int(self.conn.execute("SELECT id FROM staff WHERE user_id=?", (self.user_id,)).fetchone()["id"])
        self.conn.commit()
        self.token = make_token(self.user_id, "admin")

    def seed_account(self, *, platform: str, handle: str, crawl_enabled: bool) -> tuple[int, int]:
        handle_value, profile_url = _profile_ref(platform, handle)
        project = industry_data.create_project({"name": f"{self.marker} {platform} project", "metadata": {"marker": self.marker, "platform": platform}}).get("project") or {}
        project_id = int(project["id"])
        account = industry_data.add_account(
            project_id,
            {
                "platform": platform,
                "handle": f"{self.marker}-{platform}-{handle_value}",
                "profile_url": profile_url,
                "platform_user_id": handle_value,
                "crawl_enabled": crawl_enabled,
                "notes": self.marker,
            },
        ).get("account") or {}
        return project_id, int(account["id"])

    def run_platform_offline(self, platform: str) -> dict[str, Any]:
        _, account_id = self.seed_account(platform=platform, handle=f"{platform}_fixture", crawl_enabled=False)
        if os.environ.get("VKPI_P2_29_LIVE") == "1":
            # Direct live smoke may not share JWT_SECRET with the currently
            # running 8102 process. Keep the default smoke on the real HTTP
            # route, but use the service seam in explicit live mode.
            blocked = industry_data.refresh_account(account_id, staff=self.staff())
        else:
            blocked = self.request_json("POST", f"/api/marketing/industry-data/accounts/{account_id}/refresh")
        if blocked.get("sync_status") not in {"disabled", "not_configured"}:
            raise AssertionError(f"{platform} disabled account should not refresh live: {blocked}")
        snapshot_count = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_industry_account_snapshots WHERE account_id=?", (account_id,)).fetchone()["n"])
        if snapshot_count != 0:
            raise AssertionError(f"{platform} disabled refresh wrote fake snapshot: {snapshot_count}")

        collected = industry_snapshot_collector.collect_account_snapshot(
            account_id,
            raw_data=_raw_fixture(platform, self.marker),
            force_local=True,
            staff=self.staff(),
        )
        if collected.get("sync_status") != "synced" or int(collected.get("posts_written") or 0) != 1:
            raise AssertionError(f"{platform} fixture refresh did not persist snapshot/post: {collected}")
        snapshot = collected.get("snapshot") or {}
        if snapshot.get("followers") is None or snapshot.get("posts") is None:
            raise AssertionError(f"{platform} fixture KPI mapping missing followers/posts: {snapshot}")
        return {
            "platform": platform,
            "blocked_status": blocked.get("sync_status"),
            "followers": snapshot.get("followers"),
            "posts": snapshot.get("posts"),
            "posts_written": collected.get("posts_written"),
        }

    def run_live_if_requested(self) -> dict[str, Any] | None:
        if os.environ.get("VKPI_P2_29_LIVE") != "1":
            return None
        platform = os.environ.get("VKPI_P2_29_PLATFORM", "instagram").strip().lower()
        handle = os.environ.get("VKPI_P2_29_HANDLE", "viltrox.cine").strip()
        max_posts = max(1, min(5, int(os.environ.get("VKPI_P2_29_MAX_POSTS", "3") or 3)))
        if platform not in {"instagram", "tiktok", "bilibili", "xiaohongshu", "youtube"}:
            raise AssertionError(f"unsupported live platform for P2.29: {platform}")
        _, account_id = self.seed_account(platform=platform, handle=handle, crawl_enabled=True)
        original_platform_config = industry_snapshot_collector._platform_config
        try:
            industry_snapshot_collector._platform_config = lambda platform_key: {
                **(original_platform_config(platform_key) or {}),
                "posts_per_account": max_posts,
            }
            result = industry_snapshot_collector.collect_account_snapshot(account_id, force_local=True, staff=self.staff())
        finally:
            industry_snapshot_collector._platform_config = original_platform_config
        safe = {key: result.get(key) for key in ["provider_status", "sync_status", "posts_written", "updated_by_staff_id"]}
        if safe.get("sync_status") != "synced":
            raise AssertionError(f"{platform} live provider did not sync: {safe}")
        return {"platform": platform, "handle": handle, **safe}

    def run(self) -> dict[str, Any]:
        ensure_vkpi_schema()
        ensure_vkpi_product_industry_schema()
        self.cleanup()
        self.seed_actor()
        try:
            offline = [self.run_platform_offline(platform) for platform in OFFLINE_PLATFORMS]
            live = self.run_live_if_requested()
            residue = self.cleanup()
            if any(residue.values()):
                raise AssertionError(f"smoke residue not cleaned: {residue}")
            return {"ok": True, "marker": self.marker, "offline_platforms": offline, "live_result": live, "residue": residue}
        except Exception:
            self.cleanup()
            raise


def main() -> None:
    result = Smoke().run()
    stdout_out(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    stdout_out("VKPI_P2_29_OTHER_PLATFORM_REFRESH_SMOKE_OK")


if __name__ == "__main__":
    main()
