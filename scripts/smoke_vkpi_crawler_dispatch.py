"""scripts/smoke_vkpi_crawler_dispatch.py

R-Phase2-A smoke: 验证多平台 crawler dispatch 工作.

测试场景:
  1. industry_crawlers.get_crawler("youtube") 返回 YouTubeCrawler
  2. industry_crawlers.get_crawler("instagram") 返回 InstagramCrawler
  3. industry_crawlers.get_crawler("tiktok") 返回 TikTokCrawler
  4. industry_crawlers.get_crawler("unsupported") 返回 None
  5. is_supported() / supported_platforms() 正确
  6. 所有 crawler 都满足接口规范 (configured / provider_status / crawl_channel_profile)
  7. 没配置时返回 not_configured (不假数据)
  8. provider_gate 多平台路径正确

不依赖真实 Apify token / YouTube key,跑完不会真烧 API.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# 关键: 确保测试环境没 token (即使 .env 有,也覆盖掉)
os.environ["APIFY_TOKEN"] = ""
os.environ["YOUTUBE_API_KEY"] = ""
os.environ["REDDIT_CLIENT_ID"] = ""
os.environ["REDDIT_CLIENT_SECRET"] = ""
os.environ["REDDIT_USER_AGENT"] = ""

from app.services.vkpi.industry_crawlers import (
    YouTubeCrawler,
    InstagramCrawler,
    TikTokCrawler,
    RedditCrawler,
    get_crawler,
    is_supported,
    supported_platforms,
)


def main() -> None:
    failures: list[str] = []
    
    # ── 场景 1-3: registry 返回正确类 ──
    print("[1/8] get_crawler 返回正确实例")
    
    yt = get_crawler("youtube")
    if not isinstance(yt, YouTubeCrawler):
        failures.append(f"get_crawler('youtube') 应返回 YouTubeCrawler,实际 {type(yt)}")
    
    ig = get_crawler("instagram")
    if not isinstance(ig, InstagramCrawler):
        failures.append(f"get_crawler('instagram') 应返回 InstagramCrawler,实际 {type(ig)}")
    
    tt = get_crawler("tiktok")
    if not isinstance(tt, TikTokCrawler):
        failures.append(f"get_crawler('tiktok') 应返回 TikTokCrawler,实际 {type(tt)}")

    reddit = get_crawler("reddit")
    if not isinstance(reddit, RedditCrawler):
        failures.append(f"get_crawler('reddit') 应返回 RedditCrawler,实际 {type(reddit)}")
    
    if not failures:
        print("   PASS: 3 个 crawler 都注册")
    
    # ── 场景 4: unsupported 返回 None ──
    print("[2/8] get_crawler('unsupported') 返回 None")
    
    unsupported = get_crawler("nonexistent_platform_xyz")
    if unsupported is not None:
        failures.append(f"get_crawler('nonexistent_platform_xyz') 应返回 None,实际 {unsupported}")
    else:
        print("   PASS: 未注册平台返回 None")
    
    # ── 场景 5: is_supported / supported_platforms ──
    print("[3/8] is_supported / supported_platforms")
    
    if not is_supported("youtube"):
        failures.append("is_supported('youtube') 应 True")
    if not is_supported("instagram"):
        failures.append("is_supported('instagram') 应 True")
    if is_supported("nonexistent_platform_xyz"):
        failures.append("is_supported('nonexistent_platform_xyz') 应 False")
    
    platforms = supported_platforms()
    expected = {"youtube", "instagram", "tiktok", "xiaohongshu", "bilibili", "x", "twitch", "reddit"}
    if not expected.issubset(set(platforms)):
        failures.append(f"supported_platforms 缺: {expected - set(platforms)}")
    else:
        print(f"   PASS: supported_platforms = {platforms}")
    
    # ── 场景 6: 接口规范统一 ──
    print("[4/8] 所有 crawler 都满足接口规范")
    
    required_attrs = ["configured", "provider_status", "crawl_channel_profile"]
    xhs = get_crawler("xiaohongshu")
    bili = get_crawler("bilibili")
    xc = get_crawler("x")
    tw = get_crawler("twitch")
    
    for name, crawler in [
        ("YouTubeCrawler", yt),
        ("InstagramCrawler", ig),
        ("TikTokCrawler", tt),
        ("XiaohongshuCrawler", xhs),
        ("BilibiliCrawler", bili),
        ("XCrawler", xc),
        ("TwitchCrawler", tw),
        ("RedditCrawler", reddit),
    ]:
        for attr in required_attrs:
            if not hasattr(crawler, attr):
                failures.append(f"{name} 缺接口 {attr}")
    
    if not [f for f in failures if "缺接口" in f]:
        print("   PASS: 3 个 crawler 接口规范统一")
    
    # ── 场景 7: 没配置时返回 not_configured ──
    print("[5/8] 没 token 时返回 not_configured (不假数据)")
    
    for name, crawler in [
        ("YouTubeCrawler", yt),
        ("InstagramCrawler", ig),
        ("TikTokCrawler", tt),
        ("XiaohongshuCrawler", xhs),
        ("BilibiliCrawler", bili),
        ("XCrawler", xc),
        ("TwitchCrawler", tw),
        ("RedditCrawler", reddit),
    ]:
        if crawler.configured:
            print(f"   SKIP: {name} 有 token,跳过 not_configured 测试")
            continue
        
        result = crawler.crawl_channel_profile("test_handle")
        if result.get("provider_status") != "not_configured":
            failures.append(f"{name} 无 token 时 provider_status 应 not_configured,实际 {result.get('provider_status')}")
        elif result.get("items"):
            failures.append(f"{name} 无 token 时不应返回 items,实际 {len(result['items'])} 条")
    
    if not [f for f in failures if "无 token" in f]:
        print("   PASS: 3 个 crawler 没配置时优雅降级")
    
    # ── 场景 8: provider_status 字段完整 ──
    print("[6/8] provider_status() 返回字段完整")
    
    for name, crawler in [("YouTubeCrawler", yt), ("InstagramCrawler", ig), ("TikTokCrawler", tt)]:
        status = crawler.provider_status()
        required_keys = ["provider", "configured", "provider_status"]
        missing = [k for k in required_keys if k not in status]
        if missing:
            failures.append(f"{name}.provider_status() 缺字段 {missing}")
    
    if not [f for f in failures if "provider_status() 缺" in f]:
        print("   PASS: provider_status 字段完整")
    
    # ── 场景 7: 接口规范 normalize_handle_ref (如果有) ──
    print("[7/8] normalize_handle_ref (IG / TikTok)")
    
    ig_ref = ig.normalize_handle_ref("@instagram_user")
    if ig_ref.get("kind") != "handle" or ig_ref.get("value") != "instagram_user":
        failures.append(f"IG normalize_handle_ref 错: {ig_ref}")
    
    ig_ref_url = ig.normalize_handle_ref("https://www.instagram.com/test_user/")
    if ig_ref_url.get("kind") != "handle" or ig_ref_url.get("value") != "test_user":
        failures.append(f"IG normalize_handle_ref URL 错: {ig_ref_url}")
    
    tt_ref = tt.normalize_handle_ref("@tiktok_user")
    if tt_ref.get("kind") != "handle" or tt_ref.get("value") != "tiktok_user":
        failures.append(f"TikTok normalize_handle_ref 错: {tt_ref}")
    
    tt_ref_url = tt.normalize_handle_ref("https://www.tiktok.com/@another_user")
    if tt_ref_url.get("kind") != "handle" or tt_ref_url.get("value") != "another_user":
        failures.append(f"TikTok normalize_handle_ref URL 错: {tt_ref_url}")
    
    if not [f for f in failures if "normalize_handle_ref" in f]:
        print("   PASS: handle 规范化正确")
    
    # ── 场景 8: provider_gate 多平台 (in-process 测试) ──
    print("[8/8] provider_gate 路径区分多平台")
    
    try:
        from app.services.vkpi.industry_snapshot_collector import provider_gate
        
        # 模拟 IG 账号 - 没 APIFY_TOKEN 应该 not_configured
        ig_account = {
            "platform": "instagram",
            "crawl_enabled": 1,
            "handle": "test",
        }
        # 平台未配置 budget 也会拒,先 force=True 跳过 budget 检查
        # 但 force 会绕过 crawler 检查,所以这里测试 not force
        result = provider_gate(ig_account, force=False)
        # 没开 platform crawl,会先在那一关被拒,这里只验证不挂
        if "allowed" not in result:
            failures.append(f"provider_gate IG 应返回 allowed 字段: {result}")
        
        # 不支持平台
        unsupported_account = {
            "platform": "xiaohongshu",
            "crawl_enabled": 1,
            "handle": "test",
        }
        result = provider_gate(unsupported_account, force=False)
        if "allowed" not in result:
            failures.append(f"provider_gate xiaohongshu 应返回 allowed: {result}")
        
        if not [f for f in failures if "provider_gate" in f]:
            print("   PASS: provider_gate 多平台路径不挂")
    except Exception as exc:
        failures.append(f"provider_gate 调用挂: {exc}")
    
    # ── 总结 ──
    if failures:
        print("\n=== FAIL ===")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\nVKPI_CRAWLER_DISPATCH_SMOKE_OK")
        sys.exit(0)


if __name__ == "__main__":
    main()
