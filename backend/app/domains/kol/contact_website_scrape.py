"""L1 外链独立站/Linktree 抓联系方式 —— fetch + 正则(邮箱/mailto/社媒)+ 常见 contact 子页。
主力零 LLM;Gemini 兜底留接口(默认关,省预算,仅正则抓空且页面像有联系页时才建议开)。
有界护栏:超时、最多 N 页、只 HTML、抓 500KB 上限、域名黑名单(社媒/CDN 不当独立站爬)。
出站安全(S-04,2026-09-02):所有抓取走 app.platform.safe_fetch —— 只 https(http:// 链接升级成 https)、
DNS 解析后拒私网/回环/链路本地、连接钉在校验过的地址、禁跟随重定向;KOL bio 里塞什么 URL 都打不到内网。
红线:纯读公开页,绝不触 viltrox_fit_score。
"""
from __future__ import annotations

import re
import time
from html import unescape as _html_unescape
from typing import Any

from app.domains.kol.business_contact_extract import (
    _LINK_HUBS,
    _SOCIAL_HOSTS,
    _host_matches,
    _url_host,
    _valid_email,
)
from app.platform import safe_fetch

_MAILTO_RE = re.compile(r"mailto:([^\"'?\s>]+)", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)
_SUBPAGES = ("/contact", "/contact-us", "/about", "/work-with-me", "/collaborate", "/media-kit", "/press")
# 不当"独立站"抓的域名(社媒/CDN/大平台);Linktree 类聚合页保留(它们常直接列邮箱)。
_DENY_HOST_SUBSTR = (
    "ytimg", "tiktokcdn", "cdninstagram", "googleusercontent", "fbcdn", "gstatic",
    "youtube.com", "youtu.be", "facebook.com", "twitter.com", "x.com", "amazon.", "shopee.",
)
_UA = {"User-Agent": "Mozilla/5.0 (compatible; ViltroxContactEnrich/1.0)"}
_MAX_PAGE_BYTES = 500_000
# 批跑器速率闸 + 抓取错误台账(供批跑器区分 超时/连接失败 与 页面确实无邮箱)。
_FETCH_STATE: dict[str, float] = {"min_interval": 0.0, "last_at": 0.0}
_FETCH_ERRORS: list[str] = []
_FETCH_ERRORS_CAP = 200


def set_fetch_throttle(seconds: float) -> None:
    """全局抓取节流:两次 _fetch 之间至少间隔 seconds 秒(批跑器用,默认 0=不节流)。"""
    _FETCH_STATE["min_interval"] = max(0.0, float(seconds))


def pop_fetch_errors() -> list[str]:
    """取走并清空自上次调用以来累计的抓取错误(`异常类型: 摘要 @url` 格式)。"""
    errs = list(_FETCH_ERRORS)
    _FETCH_ERRORS.clear()
    return errs


def _host(url: str) -> str:
    return url.lower().split("//", 1)[-1].split("/", 1)[0]


def _is_link_hub(host: str) -> bool:
    """host 是否聚合页。判定口径与 L0 抽取腿共用一份(_url_host + _host_matches),
    别在这里另抄一份 www/端口/query 的剥法——两腿一漂,同一域名就会一半 link_hub 一半 website。"""
    return any(_host_matches(_url_host(host), hub) for hub in _LINK_HUBS)


def _throttle_wait() -> None:
    gap = float(_FETCH_STATE["min_interval"])
    if gap <= 0:
        return
    wait = _FETCH_STATE["last_at"] + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def _https_only(url: str) -> str:
    """只走 https:``http://`` 链接升级成 ``https://``(独立站/聚合页基本都有 https;没有的就当抓不到,
    绝不降级回明文——safe_fetch 也会把 http 拒掉,这里只是把常见写法救回来)。"""
    if url.lower().startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def _fetch(url: str, *, timeout: int = 6) -> str:
    """抓一页公开 HTML(≤500KB,超出截断)。出站经 safe_fetch:https only、DNS 解析拒私网、
    连接钉地址、禁跟随重定向;策略拒绝与网络错误一样进错误台账,返回空串。"""
    _throttle_wait()
    target = _https_only(url)
    try:
        with safe_fetch.open_url(target, headers=_UA, timeout=timeout) as r:
            ctype = safe_fetch.content_type_of(r)
            if "html" not in ctype and "text" not in ctype:
                return ""
            data, _truncated = safe_fetch.read_capped(r, _MAX_PAGE_BYTES, truncate=True)
            return data.decode("utf-8", errors="ignore")
    except Exception as exc:  # 不吞:登记错误台账供上层分类(超时/HTTP 4xx/连接失败/策略拒绝)
        if len(_FETCH_ERRORS) < _FETCH_ERRORS_CAP:
            _FETCH_ERRORS.append(f"{type(exc).__name__}: {str(exc)[:120]} @{url[:160]}")
        return ""
    finally:
        _FETCH_STATE["last_at"] = time.monotonic()


def _decode_short_unicode_escapes(text: str) -> str:
    """把残留的 \\uXXXX(含丢了反斜杠的 uXXXX)解成真字符,避免 > < 等被吞进 local 部分。"""
    import re as _re

    def _sub(match: "_re.Match[str]") -> str:
        try:
            return chr(int(match.group(1), 16))
        except ValueError:
            return match.group(0)

    return _re.sub(r"\\?u00([0-9a-fA-F]{2})", _sub, text)


def _normalize_page_text(html: str) -> str:
    """Linktree/beacons/carrd 类聚合页把邮箱埋在内嵌 JSON(\\u0040、\\/ 转义)或
    HTML 实体(&#64;)里;先做最小解转义再跑正则。零 JS 渲染,不改抓取逻辑。"""
    if "\\/" in html or "\\u00" in html:
        html = html.replace("\\/", "/").replace("\\u0040", "@").replace("\\u002e", ".").replace("\\u002E", ".")
    # 2026-08-31 实测:内嵌 JSON 里 \u003e(>)等残留会被正则当成 local 部分前缀
    # (u003eguidelines@patreon.com)。统一按 \uXXXX 解码,不只解 @ 和 .。
    if "\\u00" in html or "u003" in html:
        html = _decode_short_unicode_escapes(html)
    if "&" in html:
        html = _html_unescape(html)
    return html


def _extract_from_html(html: str, source_url: str) -> list[dict[str, Any]]:
    html = _normalize_page_text(html)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(ctype: str, value: str, conf: float, ev: str) -> None:
        value = (value or "").strip().rstrip(".,)")
        key = (ctype, value.lower())
        if not value or key in seen:
            return
        seen.add(key)
        out.append({
            "contact_type": ctype, "contact_value": value, "source_type": "website_declared",
            "confidence": conf, "evidence_text": (ev or "")[:200], "source_url": source_url,
        })

    for m in _MAILTO_RE.findall(html):
        em = m.split("?", 1)[0].strip()
        if _valid_email(em):
            _add("email", em, 0.85, "mailto")  # mailto 显式声明,高置信
    for m in _EMAIL_RE.findall(html):
        if _valid_email(m):
            _add("email", m.strip().rstrip("."), 0.7, "page")
    for href in _HREF_RE.findall(html):
        low = href.lower()
        social = next((tag for h, tag in _SOCIAL_HOSTS.items() if h in low), "")
        if social and href.startswith("http"):
            _add(f"{social}_link", href.strip(), 0.6, "site link")
    return out


def scrape_contacts_from_url(url: str, *, max_pages: int = 4, timeout: int = 6) -> list[dict[str, Any]]:
    """抓 url 首页 + 常见 contact 子页,正则邮箱/mailto/社媒。零 LLM。域名黑名单直接跳过。"""
    url = (url or "").strip()
    if not url.startswith("http"):
        return []
    host = _host(url)
    if not host or any(s in url.lower() for s in _DENY_HOST_SUBSTR):
        return []
    base = "https://" + host
    pages = [url]
    if not _is_link_hub(host):  # 聚合页(Linktree 类)是单页档案,/contact 子页属平台自己,不爬
        for sp in _SUBPAGES:
            if len(pages) >= max_pages:
                break
            pages.append(base + sp)
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for p in pages[:max_pages]:
        html = _fetch(p, timeout=timeout)
        if not html:
            continue
        for c in _extract_from_html(html, p):
            key = (c["contact_type"], c["contact_value"].lower())
            if key not in seen:
                seen.add(key)
                results.append(c)
        if any(c["contact_type"] == "email" and c["confidence"] >= 0.85 for c in results):
            break  # 拿到高置信 mailto 邮箱即够,不必跑完子页
    return results


def _gemini_extract_stub(html: str) -> list[dict[str, Any]]:
    """Gemini 兜底接口(默认关):正则抓空、但页面像有联系信息(邮箱做成图片/JS 渲染)时,
    可喂给 Gemini 提取。此处留桩返回空,接线后走 llm_gateway 预算闸。"""
    return []


def _filter_quality(found: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """写库前过质检:占位/平台/畸形域名的邮箱一律不入表(2026-08-31 首批 50 个实测,
    18 个新邮箱里 4 个是这类污染)。非邮箱联系方式原样放行。拒收项返回供台账留痕。"""
    from app.domains.kol.contact_email_quality import validate_email_syntax

    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for item in found:
        if str(item.get("contact_type") or "") != "email":
            kept.append(item)
            continue
        verdict = validate_email_syntax(str(item.get("contact_value") or ""))
        if verdict.get("ok"):
            kept.append(item)
        else:
            rejected.append({
                "value": str(item.get("contact_value") or ""),
                "reason": str(verdict.get("reason") or "invalid"),
            })
    return kept, rejected


def enrich_website_contacts_l1(kol_pool_id: int, *, conn: Any = None, allow_url: Any = None) -> dict[str, Any]:
    """L1:取该 KOL 已抽到的 website/link_hub 外链 -> 抓取 -> 落 vkpi_kol_pool_contacts + 回填 email。
    仅对已有外链的 KOL 生效(先跑 L0 抽外链)。有网络成本,按需/批量调。红线不触 fit。
    allow_url: 可选谓词(url)->bool,批跑器用来做 robots.txt 预检;返回 False 的链接跳过。"""
    import json
    from datetime import datetime, timezone

    from app.db.connection import get_conn

    db = conn or get_conn()
    rows = db.execute(
        "SELECT contact_value FROM vkpi_kol_pool_contacts WHERE kol_pool_id=? AND contact_type IN ('website','link_hub')",
        (int(kol_pool_id),),
    ).fetchall()
    links = [str(dict(r)["contact_value"]) for r in rows]
    if not links:
        return {"status": "no_links", "kol_pool_id": int(kol_pool_id)}
    now = datetime.now(timezone.utc).isoformat()
    found: list[dict[str, Any]] = []
    for link in links[:3]:
        if allow_url is not None and not allow_url(link):
            continue
        found += scrape_contacts_from_url(link)
    if not found:
        return {"status": "no_contacts_from_web", "kol_pool_id": int(kol_pool_id), "links_tried": len(links[:3])}
    found, rejected = _filter_quality(found)
    if not found:
        return {
            "status": "no_contacts_from_web", "kol_pool_id": int(kol_pool_id),
            "links_tried": len(links[:3]), "quality_rejected": rejected,
        }
    for c in found:
        db.execute(
            """
            INSERT INTO vkpi_kol_pool_contacts
                (kol_pool_id, contact_type, contact_value, contact_source, source_url,
                 consent_basis, is_public_declared, confidence, evidence_text, first_seen_at, last_seen_at, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(kol_pool_id, contact_type, contact_value) DO NOTHING
            """,
            (int(kol_pool_id), c["contact_type"], c["contact_value"], "website_declared", c.get("source_url") or "",
             "public_scan", c.get("confidence", 0.7) >= 0.85, round(float(c.get("confidence") or 0.7), 2),
             (c.get("evidence_text") or "")[:200], now, now, now),
        )
    emails = sorted([c for c in found if c["contact_type"] == "email"], key=lambda c: -float(c.get("confidence") or 0))
    best = emails[0]["contact_value"] if emails else ""
    if best:
        db.execute(
            "UPDATE vkpi_kol_pool SET email=CASE WHEN COALESCE(email,'')='' THEN ? ELSE email END, updated_at=? WHERE id=?",
            (best, now, int(kol_pool_id)),
        )
    db.commit()
    return {"status": "ok", "kol_pool_id": int(kol_pool_id), "found": len(found), "email": best}
