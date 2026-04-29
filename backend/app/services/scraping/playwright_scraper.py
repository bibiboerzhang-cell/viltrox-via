"""
services/scraping/playwright_scraper.py — Playwright 持久浏览器 + Instagram 发布时间解析
Fix: 持久浏览器（省内存）+ 自恢复 + Instagram 发布时间解析
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

from app.core.constants import USER_AGENT
from app.core.logging import get_logger
from app.utils.counts import parse_metrics_from_text, parse_metrics_from_html, merge_metrics
from app.utils.urls import detect_platform

logger = get_logger(__name__)

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

# ── 持久浏览器全局实例 ────────────────────────────────────
_browser = None
_playwright_instance = None
_browser_lock = asyncio.Lock()
_browser_last_fail_at: float = 0.0


async def _ensure_browser(force_restart: bool = False) -> bool:
    """确保浏览器实例存在，崩溃后自动重建"""
    global _browser, _playwright_instance, _browser_last_fail_at

    if not PLAYWRIGHT_AVAILABLE:
        return False

    async with _browser_lock:
        if force_restart:
            await _stop_browser()

        if _browser is not None:
            return True

        # 退避：避免疯狂重试
        now = time.time()
        if _browser_last_fail_at and (now - _browser_last_fail_at) < 5:
            await asyncio.sleep(5 - (now - _browser_last_fail_at))

        try:
            _playwright_instance = await async_playwright().start()
            _browser = await _playwright_instance.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ]
            )
            logger.info("playwright_scraper.browser_started")
            return True
        except Exception as e:
            _browser_last_fail_at = time.time()
            _browser = None
            _playwright_instance = None
            logger.warning("playwright_scraper.browser_start_failed", extra={"error": str(e)})
            return False


async def _start_browser() -> None:
    """在 lifespan startup 里调用"""
    await _ensure_browser()


async def _stop_browser() -> None:
    """在 lifespan shutdown 里调用"""
    global _browser, _playwright_instance
    try:
        if _browser:
            await _browser.close()
        if _playwright_instance:
            await _playwright_instance.stop()
        logger.info("playwright_scraper.browser_stopped")
    except Exception:
        pass
    finally:
        _browser = None
        _playwright_instance = None


# ── Instagram 发布时间解析 ────────────────────────────────
def parse_instagram_publish_date(html: str, body_text: str):
    """
    从 Instagram 页面解析发布时间，按优先级：
    1. <time datetime="...">
    2. JSON-LD uploadDate/datePublished
    3. 相对时间（"3 days ago"、"2w"、"1mo"）
    """
    now = datetime.utcnow()

    # 方法1: <time datetime="2026-04-18T12:00:00Z">
    m = re.search(r'datetime=["\'](\d{4}-\d{2}-\d{2}T[\d:Z.+-]{5,25})["\']', html or "", re.I)
    if m:
        try:
            raw = m.group(1).replace("Z", "").split(".")[0]
            return datetime.fromisoformat(raw).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    # 方法2: JSON-LD
    m = re.search(r'"(?:uploadDate|datePublished)"\s*:\s*"(\d{4}-\d{2}-\d{2}[^"]{0,20})"', html or "", re.I)
    if m:
        try:
            raw = m.group(1).replace("Z", "").split(".")[0]
            return datetime.fromisoformat(raw).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    # 方法3: 相对时间
    def _parse_rel(text: str):
        t = (text or "").lower().strip()
        # 短格式: 2w 3d 5h 1mo 1y
        m2 = re.search(r"(\d+)\s*(w|d|h|mo|y)\b", t)
        if m2:
            n, u = int(m2.group(1)), m2.group(2)
            delta = {"h": timedelta(hours=n), "d": timedelta(days=n),
                     "w": timedelta(weeks=n), "mo": timedelta(days=n*30),
                     "y": timedelta(days=n*365)}.get(u)
            if delta:
                return (now - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
        # 长格式: "3 days ago"
        m2 = re.search(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", t)
        if m2:
            n, u = int(m2.group(1)), m2.group(2)
            delta = {
                "second": timedelta(seconds=n), "minute": timedelta(minutes=n),
                "hour": timedelta(hours=n), "day": timedelta(days=n),
                "week": timedelta(weeks=n), "month": timedelta(days=n*30),
                "year": timedelta(days=n*365)
            }.get(u)
            if delta:
                return (now - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
        return None

    for line in (body_text or "").splitlines()[:30]:
        r = _parse_rel(line)
        if r:
            return r
    return None


# ── 评论提取 ─────────────────────────────────────────────
async def extract_visible_comments(page: Any, platform: str) -> List[str]:
    comments: List[str] = []
    try:
        if platform == "YouTube":
            loc = page.locator("ytd-comment-thread-renderer #content-text")
            count = await loc.count()
            for i in range(min(count, 8)):
                txt = (await loc.nth(i).inner_text()).strip()
                if txt:
                    comments.append(txt)
        elif platform == "TikTok":
            loc = page.locator('[data-e2e="comment-level-1"]')
            count = await loc.count()
            for i in range(min(count, 8)):
                txt = (await loc.nth(i).inner_text()).strip()
                if txt:
                    comments.append(txt)
        elif platform == "Instagram":
            loc = page.locator("article ul li span")
            count = await loc.count()
            for i in range(min(count, 8)):
                txt = (await loc.nth(i).inner_text()).strip()
                if txt and len(txt) > 3:
                    comments.append(txt)
        elif platform == "Facebook":
            loc = page.locator('[data-testid="UFI2Comment/body"]')
            count = await loc.count()
            for i in range(min(count, 8)):
                txt = (await loc.nth(i).inner_text()).strip()
                if txt:
                    comments.append(txt)
    except Exception:
        pass
    return comments


# ── 主抓取函数 ────────────────────────────────────────────
async def scrape_with_playwright(url: str) -> Dict[str, Any]:
    """使用持久浏览器抓取页面，崩溃后自动重建"""
    platform = detect_platform(url)

    EMPTY = {
        "scraped_ok": False,
        "title": "", "caption": "", "scraped_text": "",
        "metrics": {"views": 0, "likes": 0, "comments": 0, "shares": 0, "favorites": 0},
        "metrics_available": {"views": False, "likes": False, "comments": False,
                              "shares": False, "favorites": False},
        "visible_comments": [],
        "published_at": None,
        "error": "playwright not available",
    }

    if not PLAYWRIGHT_AVAILABLE:
        return EMPTY

    for attempt in range(2):
        ok = await _ensure_browser(force_restart=(attempt == 1))
        if not ok:
            return {**EMPTY, "error": "browser not ready"}

        context = None
        try:
            context = await _browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1440, "height": 1200},
            )
            page = await context.new_page()

            target_url = url
            if platform == "Facebook":
                target_url = (url + ("&_fb_noscript=1" if "?" in url else "?_fb_noscript=1"))

            await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3500)

            title = await page.title()
            html = await page.content()
            body_text = await page.evaluate("document.body ? document.body.innerText : ''")

            og_desc = ""
            og_image = ""
            try:
                og_desc = await page.locator('meta[property="og:description"]').get_attribute("content") or ""
            except Exception:
                pass
            try:
                og_image = await page.evaluate("""() => {
                    const og = document.querySelector('meta[property="og:image"]');
                    if (og) return og.getAttribute('content') || '';
                    const tw = document.querySelector('meta[name="twitter:image"]');
                    if (tw) return tw.getAttribute('content') || '';
                    return '';
                }""") or ""
            except Exception:
                pass

            visible_comments = await extract_visible_comments(page, platform)

            html_metrics, html_found = parse_metrics_from_html(html, platform)
            text_metrics, text_found = parse_metrics_from_text(
                "\n".join([title or "", og_desc or "", body_text or ""]), platform
            )
            metrics, metrics_available = merge_metrics(html_metrics, html_found, text_metrics, text_found)

            generic_titles = {
                "instagram", "tiktok", "youtube", "tiktok - make your day",
                "facebook", "facebook - log in or sign up",
            }
            clean_title = title.strip() if (title and title.strip().lower() not in generic_titles) else ""

            # Instagram 发布时间解析
            published_at = None
            if platform == "Instagram":
                published_at = parse_instagram_publish_date(html, body_text)
                if published_at:
                    logger.info("instagram_publish_date_parsed", extra={"published_at": published_at})

            return {
                "scraped_ok": True,
                "title": clean_title,
                "caption": og_desc or "",
                "scraped_text": body_text or "",
                "og_image": og_image or "",
                "metrics": metrics,
                "metrics_available": metrics_available,
                "visible_comments": visible_comments,
                "published_at": published_at,
                "error": None,
            }

        except Exception as e:
            msg = str(e).lower()
            if attempt == 0 and (
                "browser has been closed" in msg
                or "browser has been disconnected" in msg
                or "target page, context or browser has been closed" in msg
            ):
                logger.warning("playwright_browser_restart")
                await _stop_browser()
                continue
            return {**EMPTY, "error": str(e)}
        finally:
            if context:
                await context.close()  # 只关 context，不关 browser

    return {**EMPTY, "error": "browser unavailable after retry"}
