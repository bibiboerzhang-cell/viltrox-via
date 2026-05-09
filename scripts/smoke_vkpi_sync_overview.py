"""scripts/smoke_vkpi_sync_overview.py

R60 smoke: 验证 sync overview 聚合工作.

测试场景:
  1. GET /sync/overview 返回完整结构 (industry + shopify + cron + platform_settings + summary)
  2. industry.platforms 数组按 platform 聚合
  3. summary.overall_health 字段存在 + 是合法值
  4. 各子模块即使无数据也不挂 (优雅降级)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _smoke_seed import seed_admin, cleanup_admin
except ImportError:
    seed_admin = None
    cleanup_admin = None

from app.db.connection import get_conn


PREFIX = "vkpi-sync-overview-"
MARKER = f"{PREFIX}{int(time.time())}"
BASE_URL = "http://127.0.0.1:8102"


def main() -> None:
    conn = get_conn()
    user_id = 0
    staff_id = 0
    
    if seed_admin:
        user_id, staff_id = seed_admin(conn, marker=MARKER)
    else:
        sys.exit(1)
    
    try:
        from app.core.security import make_token
        token = make_token(user_id, "admin")
    except Exception as exc:
        print(f"[token] failed: {exc}")
        sys.exit(1)
    
    failures: list[str] = []
    
    # ── 场景 1: GET /sync/overview 返回完整结构 ──
    print("[1/4] GET /sync/overview 返回完整结构")
    
    resp = _get_json(f"{BASE_URL}/api/admin/vkpi/sync/overview", token)
    
    if resp.get("status_code") != 200:
        failures.append(f"场景 1 HTTP 错: {resp}")
    else:
        body = resp["body"]
        required_keys = ["industry", "shopify", "cron_jobs", "platform_settings", "summary"]
        missing = [k for k in required_keys if k not in body]
        if missing:
            failures.append(f"场景 1: 缺字段 {missing}")
        else:
            print(f"   PASS: 5 个 section 都返回")
    
    # ── 场景 2: industry.platforms 聚合正确 ──
    print("[2/4] industry.platforms 数组结构")
    
    if resp.get("status_code") == 200:
        industry = resp["body"].get("industry") or {}
        if "platforms" not in industry:
            failures.append("场景 2: industry 缺 platforms 字段")
        elif not isinstance(industry["platforms"], list):
            failures.append(f"场景 2: platforms 不是 list,实际 {type(industry['platforms'])}")
        else:
            # 检查每个 platform 行的字段
            for p in industry["platforms"]:
                required = ["platform", "total_accounts", "ok_count", "failed_count", "ok_rate"]
                missing_p = [k for k in required if k not in p]
                if missing_p:
                    failures.append(f"场景 2: platform 行缺字段 {missing_p}: {p}")
                    break
            else:
                print(f"   PASS: platforms[{len(industry['platforms'])}] 结构完整")
    
    # ── 场景 3: summary.overall_health 字段 ──
    print("[3/4] summary.overall_health 是合法值")
    
    if resp.get("status_code") == 200:
        summary = resp["body"].get("summary") or {}
        health = summary.get("overall_health")
        if health not in {"healthy", "degraded", "down"}:
            failures.append(f"场景 3: overall_health 非法值 {health!r}")
        elif "issues" not in summary:
            failures.append("场景 3: summary 缺 issues 字段")
        elif "checked_at" not in summary:
            failures.append("场景 3: summary 缺 checked_at 字段")
        else:
            print(f"   PASS: overall_health={health}, issues={len(summary['issues'])}")
    
    # ── 场景 4: GET /sync/industry/failures ──
    print("[4/4] GET /sync/industry/failures")
    
    resp_fail = _get_json(f"{BASE_URL}/api/admin/vkpi/sync/industry/failures?limit=20", token)
    
    if resp_fail.get("status_code") != 200:
        failures.append(f"场景 4 HTTP 错: {resp_fail}")
    else:
        body = resp_fail["body"]
        if "failures" not in body:
            failures.append("场景 4: 缺 failures 字段")
        elif not isinstance(body["failures"], list):
            failures.append(f"场景 4: failures 不是 list")
        else:
            print(f"   PASS: failures count={len(body['failures'])}")
    
    # ── cleanup ──
    if cleanup_admin and staff_id:
        cleanup_admin(conn, user_id=user_id, staff_id=staff_id)
    
    if failures:
        print("\n=== FAIL ===")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\nVKPI_SYNC_OVERVIEW_SMOKE_OK")
        sys.exit(0)


def _get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"status_code": resp.status, "body": json.loads(resp.read().decode("utf-8") or "{}")}
    except urllib.error.HTTPError as exc:
        return {"status_code": exc.code, "error": exc.read().decode("utf-8", errors="replace")[:500]}
    except Exception as exc:
        return {"status_code": -1, "error": str(exc)}


if __name__ == "__main__":
    main()
