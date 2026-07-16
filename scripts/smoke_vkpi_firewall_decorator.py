"""scripts/smoke_vkpi_firewall_decorator.py

R59 smoke: 验证防火墙装饰器拦截 + 通过 + force bypass 三种行为.

测试场景:
  1. platform 没开 crawl → 503 firewall_blocked
  2. platform 开了 crawl 但 budget=0 → 503 firewall_blocked
  3. platform 都满足 → 业务正常返回
  4. 业务函数加 @firewall_check 后,装饰器在调用前拦截
  5. owner 用 force=true bypass 成功

不依赖真实 HTTP server,直接调装饰过的函数.
"""
from __future__ import annotations

from stdout_utils import out

import os
import sys
import time
from pathlib import Path

# 让 smoke 可以独立运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# 共享 seed helper (R58E)
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _smoke_seed import seed_admin, cleanup_admin
except ImportError:
    seed_admin = None
    cleanup_admin = None

from app.db.connection import get_conn
import importlib

platform_crawl_settings = importlib.import_module("app.domains.settings.platform_crawl")
from app.domains.access.firewall import check_firewall, firewall_check


PREFIX = "vkpi-fw-"
MARKER = f"{PREFIX}{int(time.time())}"


# ─── 测试目标:用装饰器包过的假业务函数 ─────────────


@firewall_check(platform="instagram", action="crawl", require_budget=True)
def fake_instagram_crawl(*, staff: dict | None = None, body: dict | None = None) -> dict:
    """模拟一个调 Instagram API 的端点"""
    return {"called": True, "platform": "instagram"}


# ─── Smoke 主体 ─────────────────────────────────────


def main() -> None:
    conn = get_conn()
    
    # 清掉测试数据状态(平台设置)
    _reset_platform("instagram")
    
    failures: list[str] = []
    
    # ── 场景 1: 平台 crawl 未开 → 拒 ──
    out("[1/5] 平台 crawl=0 → 应拒")
    _set_platform("instagram", crawl_enabled=0, monthly_budget_usd=0)
    result = check_firewall(platform="instagram", action="crawl")
    if result["allowed"]:
        failures.append(f"场景 1 失败: 期望拒,实际允许 {result}")
    elif result["reason"] != "platform_crawl_disabled":
        failures.append(f"场景 1 reason 错: 期望 platform_crawl_disabled,实际 {result['reason']}")
    else:
        out(f"   PASS: 拒绝 reason={result['reason']}")
    
    # ── 场景 2: crawl 开了,budget=0 → 拒 ──
    out("[2/5] crawl=1 但 budget=0 → 应拒")
    _set_platform("instagram", crawl_enabled=1, monthly_budget_usd=0)
    result = check_firewall(platform="instagram", action="crawl", require_budget=True)
    if result["allowed"]:
        failures.append(f"场景 2 失败: 期望拒,实际允许 {result}")
    elif result["reason"] != "platform_budget_zero":
        failures.append(f"场景 2 reason 错: 期望 platform_budget_zero,实际 {result['reason']}")
    else:
        out(f"   PASS: 拒绝 reason={result['reason']}")
    
    # ── 场景 3: 全部满足 → 通过 ──
    out("[3/5] crawl=1 + budget>0 → 应通过")
    _set_platform("instagram", crawl_enabled=1, monthly_budget_usd=100)
    result = check_firewall(platform="instagram", action="crawl", require_budget=True)
    if not result["allowed"]:
        failures.append(f"场景 3 失败: 期望通过,实际拒 {result}")
    else:
        out(f"   PASS: 通过 reason={result['reason']}")
    
    # ── 场景 4: 装饰器拦截 ──
    out("[4/5] 装饰器在 budget=0 时拦截调用")
    _set_platform("instagram", crawl_enabled=1, monthly_budget_usd=0)
    try:
        fake_instagram_crawl(staff={"id": 1}, body={})
        failures.append("场景 4 失败: 装饰器没抛 HTTPException")
    except Exception as exc:
        if "firewall_blocked" in str(exc) or "503" in str(exc) or hasattr(exc, "status_code"):
            out(f"   PASS: 装饰器拦截 -> {type(exc).__name__}")
        else:
            failures.append(f"场景 4 失败: 抛了非预期异常 {type(exc).__name__}: {exc}")
    
    # ── 场景 5: owner force=true bypass ──
    out("[5/5] owner 用 force=true bypass")
    # 先把 budget 设回 0,确保正常会被拦
    _set_platform("instagram", crawl_enabled=1, monthly_budget_usd=0)
    try:
        result = fake_instagram_crawl(
            staff={"id": 1, "is_owner": True},
            body={"force": True},
        )
        if result.get("called"):
            out("   PASS: force=true bypass 成功")
        else:
            failures.append(f"场景 5 失败: bypass 后没返回业务结果 {result}")
    except Exception as exc:
        failures.append(f"场景 5 失败: bypass 还是被拦 {exc}")
    
    # ── cleanup ──
    _reset_platform("instagram")
    
    # ── 总结 ──
    if failures:
        out("\n=== FAIL ===")
        for f in failures:
            out(f"  - {f}")
        sys.exit(1)
    else:
        out("\nVKPI_FIREWALL_DECORATOR_SMOKE_OK")
        sys.exit(0)


def _set_platform(platform: str, *, crawl_enabled: int, monthly_budget_usd: float) -> None:
    """直接 SQL 设置 platform_crawl_settings 行"""
    platform_crawl_settings.ensure_defaults()
    conn = get_conn()
    
    is_pg = os.environ.get("DB_RUNTIME_BACKEND", "").lower() == "postgres"
    crawl_val = bool(crawl_enabled) if is_pg else int(crawl_enabled)
    
    conn.execute(
        """
        UPDATE vkpi_platform_crawl_settings
        SET crawl_enabled=?, monthly_budget_usd=?, updated_at=?
        WHERE platform=?
        """,
        (crawl_val, float(monthly_budget_usd), _now(), platform),
    )
    conn.commit()


def _reset_platform(platform: str) -> None:
    """重置平台状态到默认"""
    _set_platform(platform, crawl_enabled=0, monthly_budget_usd=0)


def _now() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    main()
