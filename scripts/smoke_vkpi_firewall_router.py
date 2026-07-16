"""scripts/smoke_vkpi_firewall_router.py

R59 v3 新增 smoke: 验证 vkpi_firewall router 真实写入数据库.

为什么需要这个:
  v2 的 router 有静默 bug — 接口返回 200 但 service 没收到正确 payload,
  数据库实际没更新。这个 smoke 走真实 HTTP 调用,然后直接查 DB 验证.

测试场景:
  1. POST /feature-flags → 数据库 vkpi_feature_flags 表 enabled 字段真的变了
  2. POST /platform/instagram → vkpi_platform_crawl_settings 表 monthly_budget_usd 真的变了
  3. POST /budget/llm → vkpi_budget_settings 表 monthly_limit_usd 真的变了
  4. 兼容旧 payload: {flags: [...]} 也能写入
  5. 装饰器写了 audit log
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from stdout_utils import out

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _smoke_seed import seed_admin, cleanup_admin
except ImportError:
    seed_admin = None
    cleanup_admin = None

from app.db.connection import get_conn
import importlib

platform_crawl_settings = importlib.import_module("app.domains.settings.platform_crawl")


PREFIX = "vkpi-fw-router-"
MARKER = f"{PREFIX}{int(time.time())}"
BASE_URL = "http://127.0.0.1:8102"

# 测试用的 flag/budget key (避免污染真实数据)
TEST_FLAG_KEY = f"{MARKER}-test-flag"
TEST_BUDGET_KEY = f"{MARKER}-test-budget"
TEST_PLATFORM = "instagram"  # 用真实平台,但只改字段不删除


def main() -> None:
    conn = get_conn()
    user_id = 0
    staff_id = 0
    
    if seed_admin:
        user_id, staff_id = seed_admin(conn, marker=MARKER)
        out(f"[seed] user_id={user_id} staff_id={staff_id}")
    else:
        out("[seed] _smoke_seed.py missing")
        sys.exit(1)
    
    # 拿 token (smoke 内部逻辑,这里假设有 make_token helper)
    try:
        from app.core.security import make_token
        token = make_token(user_id, "admin")
    except Exception as exc:
        out(f"[token] failed: {exc}")
        sys.exit(1)
    
    failures: list[str] = []
    
    # 备份 platform 原始状态(测试后还原)
    pre_platform = _get_platform_row(TEST_PLATFORM)
    
    # ── 场景 1: feature_flag toggle ──
    out("[1/5] POST /feature-flags 真实写入")
    
    # 调 API
    resp = _post_json(
        f"{BASE_URL}/api/admin/vkpi/settings/firewall/feature-flags",
        token,
        {"flag_key": TEST_FLAG_KEY, "enabled": True, "description": "R59 test flag"},
    )
    
    if resp.get("status_code") != 200:
        failures.append(f"场景 1 HTTP 错: {resp}")
    else:
        # 直接查 DB
        row = _get_flag_row(TEST_FLAG_KEY)
        if not row:
            failures.append(f"场景 1: 数据库没写入 flag={TEST_FLAG_KEY}")
        elif not _truthy(row.get("enabled")):
            failures.append(f"场景 1: enabled 字段没生效 row={dict(row)}")
        else:
            out(f"   PASS: flag {TEST_FLAG_KEY} enabled=true 真实写入")
    
    # ── 场景 2: platform update ──
    out("[2/5] POST /platform/instagram 真实写入 monthly_budget_usd")
    
    target_budget = 123.45
    resp = _post_json(
        f"{BASE_URL}/api/admin/vkpi/settings/firewall/platform/{TEST_PLATFORM}",
        token,
        {"monthly_budget_usd": target_budget},
    )
    
    if resp.get("status_code") != 200:
        failures.append(f"场景 2 HTTP 错: {resp}")
    else:
        row = _get_platform_row(TEST_PLATFORM)
        actual = float(row.get("monthly_budget_usd") or 0) if row else 0
        if abs(actual - target_budget) > 0.01:
            failures.append(f"场景 2: monthly_budget_usd 没生效 期望={target_budget} 实际={actual}")
        else:
            out(f"   PASS: instagram monthly_budget_usd={actual} 真实写入")
    
    # ── 场景 3: budget update ──
    out("[3/5] POST /budget/{key} 真实写入 monthly_limit_usd")
    
    target_limit = 567.89
    resp = _post_json(
        f"{BASE_URL}/api/admin/vkpi/settings/firewall/budget/{TEST_BUDGET_KEY}",
        token,
        {"monthly_limit_usd": target_limit, "enabled": True, "alert_threshold_pct": 80},
    )
    
    if resp.get("status_code") != 200:
        failures.append(f"场景 3 HTTP 错: {resp}")
    else:
        row = _get_budget_row(TEST_BUDGET_KEY)
        if not row:
            failures.append(f"场景 3: 数据库没写入 budget={TEST_BUDGET_KEY}")
        else:
            actual = float(row.get("monthly_limit_usd") or 0)
            if abs(actual - target_limit) > 0.01:
                failures.append(f"场景 3: monthly_limit_usd 没生效 期望={target_limit} 实际={actual}")
            else:
                out(f"   PASS: budget {TEST_BUDGET_KEY} monthly_limit_usd={actual} 真实写入")
    
    # ── 场景 4: 兼容旧 payload {flags: [...]} ──
    out("[4/5] 兼容旧 payload 形状")
    
    resp = _post_json(
        f"{BASE_URL}/api/admin/vkpi/settings/firewall/feature-flags",
        token,
        {"flags": [{"flag_key": TEST_FLAG_KEY, "enabled": False, "description": "disabled"}]},
    )
    
    if resp.get("status_code") != 200:
        failures.append(f"场景 4 HTTP 错: {resp}")
    else:
        row = _get_flag_row(TEST_FLAG_KEY)
        if _truthy(row.get("enabled") if row else None):
            failures.append(f"场景 4: enabled 应该被 toggle 为 false")
        else:
            out(f"   PASS: 旧 payload {{flags: [...]}} 也能 toggle")
    
    # ── 场景 5: 装饰器写了 audit log ──
    out("[5/5] @audit_action 装饰器写日志")
    
    audit_count = _count_audit("firewall_feature_flag_toggle", TEST_FLAG_KEY)
    if audit_count < 2:  # 场景 1 + 场景 4 至少 2 条
        failures.append(f"场景 5: 期望 >=2 条 audit log,实际 {audit_count}")
    else:
        out(f"   PASS: audit log 记录了 {audit_count} 次 toggle")
    
    # ── cleanup ──
    out("\n[cleanup]")
    _cleanup_test_data(TEST_FLAG_KEY, TEST_BUDGET_KEY)
    _restore_platform(TEST_PLATFORM, pre_platform)
    if cleanup_admin and staff_id:
        cleanup_admin(conn, user_id=user_id, staff_id=staff_id)
    
    if failures:
        out("\n=== FAIL ===")
        for f in failures:
            out(f"  - {f}")
        sys.exit(1)
    else:
        out("\nVKPI_FIREWALL_ROUTER_SMOKE_OK")
        sys.exit(0)


# ─── HTTP 辅助 ───────────────────────────────────


def _post_json(url: str, token: str, body: dict) -> dict:
    """POST 请求,返回 {status_code, body, error}"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {
                "status_code": resp.status,
                "body": json.loads(resp.read().decode("utf-8") or "{}"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status_code": exc.code,
            "error": exc.read().decode("utf-8", errors="replace")[:500],
        }
    except Exception as exc:
        return {"status_code": -1, "error": str(exc)}


# ─── DB 查询辅助 ──────────────────────────────────


def _get_flag_row(flag_key: str) -> dict:
    row = get_conn().execute(
        "SELECT * FROM vkpi_feature_flags WHERE flag_key=?", (flag_key,)
    ).fetchone()
    return dict(row) if row else {}


def _get_platform_row(platform: str) -> dict:
    row = get_conn().execute(
        "SELECT * FROM vkpi_platform_crawl_settings WHERE platform=?", (platform,)
    ).fetchone()
    return dict(row) if row else {}


def _get_budget_row(budget_key: str) -> dict:
    row = get_conn().execute(
        "SELECT * FROM vkpi_budget_settings WHERE budget_key=?", (budget_key,)
    ).fetchone()
    return dict(row) if row else {}


def _count_audit(action_type: str, target_id: str) -> int:
    row = get_conn().execute(
        "SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE action_type=? AND target_id=?",
        (action_type, target_id),
    ).fetchone()
    return int(row["n"]) if row else 0


def _truthy(value) -> bool:
    """SQLite/Postgres boolean 兼容"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.lower() in ("1", "true", "t", "yes")
    return bool(value)


# ─── Cleanup ──────────────────────────────────


def _cleanup_test_data(flag_key: str, budget_key: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_feature_flags WHERE flag_key=?", (flag_key,))
    conn.execute("DELETE FROM vkpi_budget_settings WHERE budget_key=?", (budget_key,))
    # audit log 也清掉避免残留
    conn.execute(
        "DELETE FROM vkpi_business_audit_logs WHERE target_id IN (?,?)",
        (flag_key, budget_key),
    )
    conn.commit()


def _restore_platform(platform: str, pre_state: dict) -> None:
    """恢复 platform 测试前的状态"""
    if not pre_state:
        return
    conn = get_conn()
    # 用 service 自己的 update,确保兼容
    try:
        platform_crawl_settings.update_platform_settings(
            {
                "platforms": [
                    {
                        "platform": platform,
                        "monthly_budget_usd": float(pre_state.get("monthly_budget_usd") or 0),
                    }
                ]
            },
            staff=None,
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
