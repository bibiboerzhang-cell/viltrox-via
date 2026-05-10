"""scripts/configure_platform_crawl.py

R-Phase2-A.5 v3: 一键配置平台抓取参数

修正历史:
  v1: 自己拼 SQL → Postgres boolean 不兼容
  v2: 改用 update_platform_settings API + dry-run 默认
  v3: 加 --staff-id 让 audit log 真正写入 (审包发现 v2 staff=None → audit 跳过)

用法:
  # 查看当前配置 (只读)
  source scripts/runtime_env.sh
  PYTHONPATH=backend .venv/bin/python scripts/configure_platform_crawl.py --list
  
  # Dry-run (默认,不写库,不需要 staff-id)
  source scripts/runtime_env.sh
  PYTHONPATH=backend .venv/bin/python scripts/configure_platform_crawl.py \\
    --platform instagram --enable --budget 25
  
  # 真实写库 (要 --apply 显式确认 + --staff-id 让 audit 生效)
  source scripts/runtime_env.sh
  PYTHONPATH=backend .venv/bin/python scripts/configure_platform_crawl.py \\
    --platform instagram --enable --budget 25 --daily-limit 5 --posts-per-account 12 \\
    --apply --staff-id 1
  
  # 真实写库但跳过 audit (不推荐,只在调试时用)
  PYTHONPATH=backend .venv/bin/python scripts/configure_platform_crawl.py \\
    --platform instagram --disable --apply --no-audit

注意:
  1. 必须先 source scripts/runtime_env.sh,否则连默认 5432 端口会 PoolTimeout
  2. --apply 默认要求 --staff-id (避免无 audit 的生产改动)
  3. --no-audit 是显式跳过 audit 的逃生通道
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# 启动检测: 没 source runtime_env.sh 时给清晰错误
def _check_environment() -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL 未设置")
        print("请先运行: source scripts/runtime_env.sh")
        sys.exit(2)
    if "5432" in db_url and "54329" not in db_url:
        # 检查是否有显式覆盖
        allow_nonlocal = "--allow-nonlocal-db" in sys.argv
        if not allow_nonlocal:
            print("ERROR: DATABASE_URL 看起来连默认 5432,不是本地 54329")
            print(f"  当前: {db_url[:80]}")
            print("")
            print("可能你忘了 source scripts/runtime_env.sh")
            print("如果你确定要连这个 DB (生产/远程),加 --allow-nonlocal-db 显式覆盖")
            sys.exit(2)
        print(f"⚠️  --allow-nonlocal-db 已确认,连接非本地 DB: {db_url[:80]}")


_check_environment()

from app.db.connection import get_conn
from app.services.vkpi.platform_crawl_settings import (
    ensure_defaults,
    platform_settings,
    update_platform_settings,
)
from app.services.vkpi.industry_crawlers import get_crawler, supported_platforms


SUPPORTED = {
    "youtube",
    "instagram",
    "tiktok",
    "xiaohongshu",
    "bilibili",
    "x",
    "twitch",
    "reddit",
    "facebook",
}


def list_platforms() -> None:
    ensure_defaults()
    settings = platform_settings()
    rows = settings.get("platforms") or []
    
    print()
    print("═══════════════════════════════════════════════")
    print("  Platform Crawl Settings 当前状态")
    print("═══════════════════════════════════════════════")
    print()
    print(f"  {'Platform':<15} {'Enabled':<8} {'Daily':<6} {'Posts':<6} {'Budget':<10} {'Status':<15} {'Crawler'}")
    print(f"  {'-'*15} {'-'*8} {'-'*6} {'-'*6} {'-'*10} {'-'*15} {'-'*10}")
    
    for row in rows:
        platform = row.get("platform", "")
        enabled = bool(row.get("crawl_enabled"))
        daily = int(row.get("daily_account_limit") or 0)
        posts = int(row.get("posts_per_account") or 0)
        budget = float(row.get("monthly_budget_usd") or 0)
        status = row.get("last_test_status") or "not_configured"
        
        crawler = get_crawler(platform)
        crawler_status = "n/a"
        if crawler is not None:
            crawler_status = "configured" if crawler.configured else "no token"
        
        enabled_str = "✓ YES" if enabled else "  no"
        budget_str = f"${budget:.0f}/mo" if budget else "0"
        
        print(f"  {platform:<15} {enabled_str:<8} {daily:<6} {posts:<6} {budget_str:<10} {status:<15} {crawler_status}")
    
    print()
    print(f"  Crawler registry: {supported_platforms()}")
    print(f"  APIFY_TOKEN: {'configured' if os.environ.get('APIFY_TOKEN') else 'NOT SET'}")
    print(f"  YOUTUBE_API_KEY: {'configured' if os.environ.get('YOUTUBE_API_KEY') else 'NOT SET'}")
    print()


def show_diff(platform: str, *, enable: bool | None, disable: bool,
              budget: float | None, daily_limit: int | None,
              posts_per_account: int | None) -> dict | None:
    ensure_defaults()
    conn = get_conn()
    
    row = conn.execute(
        "SELECT * FROM vkpi_platform_crawl_settings WHERE platform=?",
        (platform,),
    ).fetchone()
    
    if not row:
        print(f"ERROR: {platform} 不在 vkpi_platform_crawl_settings 表里")
        return None
    
    current = dict(row)
    
    new_enabled = bool(current.get("crawl_enabled"))
    if enable is True:
        new_enabled = True
    if disable:
        new_enabled = False
    
    new_budget = float(current.get("monthly_budget_usd") or 0)
    if budget is not None:
        new_budget = float(budget)
    
    new_daily = int(current.get("daily_account_limit") or 0)
    if daily_limit is not None:
        new_daily = int(daily_limit)
    
    new_posts = int(current.get("posts_per_account") or 0)
    if posts_per_account is not None:
        new_posts = int(posts_per_account)
    
    warnings = []
    if new_enabled and platform in {"instagram", "tiktok", "xiaohongshu", "bilibili", "facebook"}:
        if not os.environ.get("APIFY_TOKEN"):
            warnings.append(f"开启 {platform} 抓取,但 APIFY_TOKEN 未配置")
    if new_enabled and platform == "reddit":
        has_praw = (
            os.environ.get("REDDIT_CLIENT_ID")
            and os.environ.get("REDDIT_CLIENT_SECRET")
            and os.environ.get("REDDIT_USER_AGENT")
        )
        if not has_praw and not os.environ.get("APIFY_TOKEN"):
            warnings.append("开启 reddit 抓取,但 Reddit API 和 APIFY_TOKEN 都未配置")
    if new_enabled and platform == "youtube":
        if not os.environ.get("YOUTUBE_API_KEY"):
            warnings.append("开启 youtube 抓取,但 YOUTUBE_API_KEY 未配置")
    if new_enabled and new_budget == 0:
        warnings.append(f"{platform} 开启但 budget=0 → provider_gate 会拒绝抓取")
    
    print(f"\n准备更新 {platform}:")
    print(f"  crawl_enabled:        {bool(current.get('crawl_enabled'))} → {new_enabled}")
    print(f"  daily_account_limit:  {current.get('daily_account_limit')} → {new_daily}")
    print(f"  posts_per_account:    {current.get('posts_per_account')} → {new_posts}")
    print(f"  monthly_budget_usd:   ${float(current.get('monthly_budget_usd') or 0):.0f} → ${new_budget:.0f}")
    
    for w in warnings:
        print(f"  ⚠️  {w}")
    
    payload = {
        "platforms": [{
            "platform": platform,
            "crawl_enabled": new_enabled,
            "daily_account_limit": new_daily,
            "posts_per_account": new_posts,
            "monthly_budget_usd": new_budget,
        }]
    }
    return payload


def apply_payload(payload: dict, *, staff_id: int | None) -> None:
    """通过现有 API 真实写库
    
    audit 行为:
      - staff_id 传入 → audit log 写入 (推荐)
      - staff_id=None → 写库但跳过 audit log (--no-audit 显式确认时使用)
    """
    staff_obj = {"id": staff_id} if staff_id else None
    result = update_platform_settings(payload, staff=staff_obj)
    print(f"\n✓ 已通过 update_platform_settings 写入")
    if staff_id:
        print(f"  staff_id={staff_id} → audit log 已写入 vkpi_settings_change_logs")
    else:
        print(f"  staff=None → 写库成功,但 audit 已跳过 (--no-audit 确认)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="R-Phase2-A.5 v3 platform crawl configuration",
        epilog="必须先 source scripts/runtime_env.sh",
    )
    
    parser.add_argument("--list", action="store_true", help="查看当前配置 (只读)")
    parser.add_argument("--platform", type=str, default="", help="目标平台")
    parser.add_argument("--enable", action="store_true", help="开启抓取")
    parser.add_argument("--disable", action="store_true", help="关闭抓取")
    parser.add_argument("--budget", type=float, help="月度预算 USD")
    parser.add_argument("--daily-limit", type=int, help="每日账号上限")
    parser.add_argument("--posts-per-account", type=int, help="每账号抓取帖子数")
    parser.add_argument("--apply", action="store_true", help="真实写库 (默认 dry-run)")
    parser.add_argument("--staff-id", type=int, default=None, help="操作者 staff_id (audit log 必需)")
    parser.add_argument("--no-audit", action="store_true", help="显式跳过 audit (不推荐)")
    parser.add_argument("--allow-nonlocal-db", action="store_true", help="显式确认连非本地 DB (5432 等)")
    
    args = parser.parse_args()
    
    if args.list:
        list_platforms()
        return
    
    if not args.platform:
        parser.print_help()
        sys.exit(1)
    
    platform = args.platform.lower()
    if platform not in SUPPORTED:
        print(f"ERROR: {platform} 当前不支持. 已支持: {sorted(SUPPORTED)}")
        sys.exit(1)
    
    if args.enable and args.disable:
        print("ERROR: --enable 和 --disable 互斥")
        sys.exit(1)
    
    # --apply 必须 --staff-id 或 --no-audit
    if args.apply and args.staff_id is None and not args.no_audit:
        print("ERROR: --apply 时必须传 --staff-id N (让 audit log 生效)")
        print("       或加 --no-audit 显式跳过 (不推荐生产环境用)")
        print("")
        print("查看可用 staff_id:")
        print("  PGOPTIONS='-c search_path=public' \\")
        print('    psql "$DATABASE_URL" -c "SELECT id, full_name, email FROM vkpi_staff LIMIT 10"')
        sys.exit(1)
    
    payload = show_diff(
        platform,
        enable=True if args.enable else None,
        disable=args.disable,
        budget=args.budget,
        daily_limit=args.daily_limit,
        posts_per_account=args.posts_per_account,
    )
    
    if payload is None:
        sys.exit(1)
    
    if args.apply:
        apply_payload(payload, staff_id=args.staff_id)
    else:
        print(f"\nDRY-RUN: 未写库")
        print(f"加 --apply --staff-id N 真实执行")


if __name__ == "__main__":
    main()
