#!/usr/bin/env python3
"""P2.27 smoke for single-account refresh safety.

Default mode is offline: it verifies the API refresh gate does not create fake
snapshots and that the live write seam persists one raw fixture snapshot + post.
Set VKPI_P2_27_LIVE=1 to run one real provider call through
collect_account_snapshot(force_local=True) without changing Settings budgets.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
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
PREFIX = "vkpi-p2-27-refresh-"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _raw_fixture(marker: str) -> dict[str, Any]:
    return {
        "source": "p2_27_fixture",
        "snapshot_date": "2026-05-10",
        "youtube_kpi_status": "fixture",
        "youtube_kpi_source_ref": f"fixture-{marker}",
        "profile": {
            "items": [
                {
                    "id": f"UC{marker[-10:]}",
                    "snippet": {"title": f"{marker} test channel", "description": "Viltrox lens creator test."},
                    "statistics": {"subscriberCount": "3210", "videoCount": "12", "viewCount": "45678"},
                }
            ]
        },
        "videos": [
            {
                "id": f"{marker}-video-a",
                "snippet": {
                    "publishedAt": "2026-05-10T08:00:00Z",
                    "title": f"{marker} Viltrox 35mm attachment QA",
                    "description": "Autofocus test #viltrox #lens",
                    "thumbnails": {"high": {"url": f"https://img.example/{marker}.jpg"}},
                },
                "statistics": {"viewCount": "500", "likeCount": "50", "commentCount": "5"},
                "contentDetails": {"duration": "PT3M10S"},
            }
        ],
    }


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.user_id = 0
        self.staff_id = 0
        self.token = ""
        self.project_id = 0
        self.account_id = 0

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

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if ids:
                c.execute(f"DELETE FROM {table} WHERE {column} IN ({','.join('?' for _ in ids)})", ids)

        delete_in("vkpi_industry_post_metrics", "post_id", [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_industry_posts WHERE account_id IN (" + ",".join("?" for _ in account_ids) + ")" if account_ids else "SELECT id FROM vkpi_industry_posts WHERE 1=0", account_ids).fetchall()])
        delete_in("vkpi_industry_posts", "account_id", account_ids)
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

    def staff(self) -> dict[str, Any]:
        return {"id": self.staff_id, "role": "admin", "is_owner": True}

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

    def seed_account(self, *, platform: str = "youtube", handle: str | None = None, crawl_enabled: bool = False) -> None:
        project = industry_data.create_project({"name": f"{self.marker} project", "metadata": {"marker": self.marker}}).get("project") or {}
        self.project_id = int(project["id"])
        normalized_handle = (handle or f"{self.marker}_channel").strip().lstrip("@")
        account = industry_data.add_account(
            self.project_id,
            {
                "platform": platform,
                "handle": normalized_handle,
                "profile_url": f"https://www.youtube.com/@{normalized_handle}",
                "crawl_enabled": crawl_enabled,
                "notes": self.marker,
            },
        ).get("account") or {}
        self.account_id = int(account["id"])

    def run_offline(self) -> dict[str, Any]:
        if os.environ.get("VKPI_P2_27_LIVE") == "1":
            blocked = industry_data.refresh_account(
                self.account_id,
                staff=self.staff(),
            )
        else:
            blocked = self.request_json("POST", f"/api/marketing/industry-data/accounts/{self.account_id}/refresh")
        if blocked.get("sync_status") not in {"disabled", "not_configured"}:
            raise AssertionError(f"disabled account should not refresh live: {blocked}")
        snapshot_count = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_industry_account_snapshots WHERE account_id=?", (self.account_id,)).fetchone()["n"])
        if snapshot_count != 0:
            raise AssertionError(f"disabled refresh wrote fake snapshot: {snapshot_count}")

        collected = industry_snapshot_collector.collect_account_snapshot(
            self.account_id,
            raw_data=_raw_fixture(self.marker),
            force_local=True,
            staff=self.staff(),
        )
        if collected.get("sync_status") != "synced" or int(collected.get("posts_written") or 0) != 1:
            raise AssertionError(f"fixture refresh did not persist snapshot/post: {collected}")
        post_count = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_industry_posts WHERE account_id=?", (self.account_id,)).fetchone()["n"])
        if post_count != 1:
            raise AssertionError(f"expected one persisted post, got {post_count}")
        return {"blocked": blocked, "fixture": collected, "post_count": post_count}

    def run_live_if_requested(self) -> dict[str, Any] | None:
        if os.environ.get("VKPI_P2_27_LIVE") != "1":
            return None
        platform = os.environ.get("VKPI_P2_27_PLATFORM", "youtube").strip().lower()
        handle = os.environ.get("VKPI_P2_27_HANDLE", "@viltroxofficial").strip()
        self.seed_account(platform=platform, handle=handle, crawl_enabled=True)
        result = industry_snapshot_collector.collect_account_snapshot(
            self.account_id,
            force_local=True,
            staff=self.staff(),
        )
        safe = {key: result.get(key) for key in ["provider_status", "sync_status", "posts_written", "updated_by_staff_id"]}
        if str(safe.get("sync_status") or "").lower() in {"error", "not_configured"}:
            raise AssertionError(f"live provider did not sync: {safe}")
        if safe.get("sync_status") == "synced" and int(safe.get("posts_written") or 0) < 0:
            raise AssertionError(f"invalid live result: {safe}")
        return safe

    def run(self) -> dict[str, Any]:
        ensure_vkpi_schema()
        ensure_vkpi_product_industry_schema()
        self.cleanup()
        self.seed_actor()
        self.seed_account()
        try:
            offline = self.run_offline()
            live = self.run_live_if_requested()
            residue = self.cleanup()
            if any(residue.values()):
                raise AssertionError(f"smoke residue not cleaned: {residue}")
            return {
                "ok": True,
                "marker": self.marker,
                "mode": "live" if live else "offline",
                "offline_post_count": offline["post_count"],
                "live_result": live,
                "residue": residue,
            }
        except Exception:
            self.cleanup()
            raise


def main() -> None:
    result = Smoke().run()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("VKPI_P2_27_SINGLE_ACCOUNT_REFRESH_SMOKE_OK")


if __name__ == "__main__":
    main()
