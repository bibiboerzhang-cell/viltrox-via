"""Bounded website diversion for the KOL deep-crawl enqueue path."""
from __future__ import annotations

import urllib.robotparser
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.kol import url_route_plan

logger = get_logger("viltrox.domains.kol.url_deep_crawl")


# ── 网页抓取腿(分流去向 website)的有界护栏 ──
# 同步跑,上限压得比批跑器更紧:两页(首页 + 一个常见联系页)、5 秒超时。出站一律经
# safe_fetch(只 https、DNS 后拒私网、连接钉地址、禁跟随重定向、500KB 截断)。
# 站点根地址本身既是一条站点资料,也当作「这个站点读过了」的记号,免得反复去打同一家。
_SITE_SCAN_MAX_PAGES = 2
_SITE_SCAN_TIMEOUT_S = 5
_ROBOTS_UA = "ViltroxContactEnrich"
_ROBOTS_MAX_BYTES = 64_000
_ROBOTS_CACHE: dict[str, Any] = {}
_ROBOTS_CACHE_CAP = 512
_SITE_CONTACT_TYPE = "website"
_SITE_CONTACT_SOURCE = "website_declared"


def _load_robots(host: str) -> Any:
    """取一份 robots 规则;取不到按业界惯例视为允许,但留痕不静默。"""
    from app.platform import safe_fetch

    try:
        fetched = safe_fetch.fetch_bytes(
            f"https://{host}/robots.txt",
            timeout=_SITE_SCAN_TIMEOUT_S,
            max_bytes=_ROBOTS_MAX_BYTES,
            truncate=True,
        )
    except Exception as exc:  # noqa: BLE001 — 拉不到规则不等于禁止,但必须看得见
        logger.info("site robots unavailable host=%s err=%s", host, type(exc).__name__)
        return True
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(fetched.data.decode("utf-8", errors="ignore").splitlines())
    return parser


def _robots_allows(url: str) -> bool:
    """站点声明不许自动读取就不读。每个主机名只取一次规则,进程内缓存。"""
    host = url_route_plan.host_of(url)
    if not host:
        return False
    parser = _ROBOTS_CACHE.get(host)
    if parser is None:
        parser = _load_robots(host)
        if len(_ROBOTS_CACHE) < _ROBOTS_CACHE_CAP:
            _ROBOTS_CACHE[host] = parser
    return True if parser is True else bool(parser.can_fetch(_ROBOTS_UA, url))


def _site_already_scanned(conn: Any, kol_pool_id: int, base: str) -> bool:
    row = conn.execute(
        "SELECT id FROM vkpi_kol_pool_contacts WHERE kol_pool_id=? AND contact_type=? AND contact_value=? LIMIT 1",
        (int(kol_pool_id), _SITE_CONTACT_TYPE, base),
    ).fetchone()
    return row is not None


def _save_site_contacts(conn: Any, kol_pool_id: int, base: str, found: list[dict[str, Any]]) -> int:
    """联系方式 + 站点根地址落进既有 contacts 结构;抓到的正文一个字都不进召回证据链(红线)。"""
    now = datetime.now(timezone.utc).isoformat()
    rows = url_route_plan.site_contact_rows(base, found)
    for contact_type, value, source_url, confidence, evidence in rows:
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool_contacts
                (kol_pool_id, contact_type, contact_value, contact_source, source_url,
                 consent_basis, is_public_declared, confidence, evidence_text,
                 first_seen_at, last_seen_at, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(kol_pool_id, contact_type, contact_value) DO NOTHING
            """,
            (
                int(kol_pool_id), contact_type, value, _SITE_CONTACT_SOURCE, source_url,
                "public_scan", confidence >= 0.85, round(confidence, 2), evidence, now, now, now,
            ),
        )
    conn.commit()
    return len(rows)


def _scan_site_contacts(
    conn: Any,
    url: str,
    kol_pool_id: int,
    *,
    robots_allows: Callable[[str], bool] | None = None,
    site_already_scanned: Callable[[Any, int, str], bool] | None = None,
    save_site_contacts: Callable[[Any, int, str, list[dict[str, Any]]], int] | None = None,
) -> dict[str, Any]:
    """同步跑一次网页抓取腿;每一种结局都如实回执,不假装成功也不假装失败。"""
    from app.domains.kol import contact_website_scrape

    robots_allows = robots_allows or _robots_allows
    site_already_scanned = site_already_scanned or _site_already_scanned
    save_site_contacts = save_site_contacts or _save_site_contacts
    base = url_route_plan.site_base(url)
    if kol_pool_id and site_already_scanned(conn, kol_pool_id, base):
        return {"status": "site_already_scanned", "message": "这个网站之前已经读过,直接用已有的资料。"}
    if not robots_allows(url):
        return {"status": "site_scan_skipped", "message": "这个网站声明了不允许自动读取,已按它的要求跳过。"}
    try:
        found = contact_website_scrape.scrape_contacts_from_url(
            url, max_pages=_SITE_SCAN_MAX_PAGES, timeout=_SITE_SCAN_TIMEOUT_S
        )
        kept, _rejected = contact_website_scrape._filter_quality(found)
    except Exception as exc:  # noqa: BLE001 — 一个站点打不开不该把入队口打成 500
        logger.warning("site contact scan failed host=%s err=%s", url_route_plan.host_of(url), type(exc).__name__)
        return {"status": "site_scan_failed", "message": "这个网站这次没能打开,可以稍后再试。"}
    if not kol_pool_id:
        return {"status": "site_scanned", "contacts_found": len(kept), "contacts_saved": 0}
    return {"status": "site_scanned", "contacts_found": len(kept), "contacts_saved": save_site_contacts(conn, kol_pool_id, base, kept)}


def _divert_off_crawler_url(
    conn: Any,
    clean_url: str,
    *,
    kol_pool_id: int | None,
    monitored: bool,
    scan_site_contacts: Callable[[Any, str, int], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """账号抓取通道读不了的链接就地了结,返回回执;能走原通道的返回 None。

    公开站点顺手读一次公开联系方式,其余原样诚实拒绝 —— 两种结局都不留下一条
    卡住的活。调用点在归属围栏**之后**,校验一步不绕;内容监控是「盯住某个账号」
    的长期约定,不参与分流。
    """
    if monitored:
        return None
    route = url_route_plan.plan_url_route_from_url(clean_url)
    if route.handled_by_account_crawler:
        return None
    receipt = route.receipt()
    if route.route == url_route_plan.ROUTE_WEBSITE:
        scanner = scan_site_contacts or _scan_site_contacts
        receipt.update(scanner(conn, route.target_url, int(kol_pool_id or 0)))
    return receipt
