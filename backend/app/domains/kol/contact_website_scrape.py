"""L1 外链独立站/Linktree 抓联系方式 —— fetch + 正则(邮箱/mailto/社媒)+ 常见 contact 子页。
主力零 LLM;Gemini 兜底留接口(默认关,省预算,仅正则抓空且页面像有联系页时才建议开)。
有界护栏:超时、最多 N 页、只 HTML、抓 500KB 上限、域名黑名单(社媒/CDN 不当独立站爬)。
红线:纯读公开页,绝不触 viltrox_fit_score。
"""
from __future__ import annotations

import re
import urllib.request
from typing import Any

from app.domains.kol.business_contact_extract import _valid_email, _SOCIAL_HOSTS

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


def _host(url: str) -> str:
    return url.lower().split("//", 1)[-1].split("/", 1)[0]


def _fetch(url: str, *, timeout: int = 6) -> str:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (公开页只读)
            ctype = str(r.headers.get("Content-Type") or "")
            if "html" not in ctype.lower() and "text" not in ctype.lower():
                return ""
            return r.read(500_000).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_from_html(html: str, source_url: str) -> list[dict[str, Any]]:
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


def enrich_website_contacts_l1(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
    """L1:取该 KOL 已抽到的 website/link_hub 外链 -> 抓取 -> 落 vkpi_kol_pool_contacts + 回填 email。
    仅对已有外链的 KOL 生效(先跑 L0 抽外链)。有网络成本,按需/批量调。红线不触 fit。"""
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
        found += scrape_contacts_from_url(link)
    if not found:
        return {"status": "no_contacts_from_web", "kol_pool_id": int(kol_pool_id), "links_tried": len(links[:3])}
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
