"""P0-1 公开商务邮箱富化(默认关、白名单来源、单条、闸A 预算 + PII 合规)。

严格边界(合规):
  - 只取创作者公开声明的商务邮箱,来源白名单 = {youtube_about_declared, ig_business_profile, bio_explicit_contact}。
  - 绝不做全网正则爬邮箱;只在 raw_platform_data 已抓回的 about/profile/bio 字段内,匹配「显式 contact 行」。
  - 默认 feature_flag business_email_enrichment = OFF;关时函数直接返回 disabled,不写库不调 Apify。
红线:不触 viltrox_fit_score;只写 vkpi_kol_pool.email/other_contacts_json/contact_* 与 vkpi_kol_pool_contacts。

本文件为设计骨架:Apify 专抓 about 页(Jianbo 已授权)接入点为 _budget_ok + TODO;上线前需
跑 .venv py_compile + 单测 + Jianbo 拍板辖区(consent_basis/destination_region 枚举)。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.costs.budget_guard import check_budget, record_cost

from app.core.logging import get_logger

logger = get_logger(__name__)

# Apify 专抓 about 页的单次预估成本上限(USD);超此或闸不过 → 拒抓。
# YouTube channel-about actor 单跑约 $0.01-0.03 量级,取 0.05 保守预估。
APIFY_ABOUT_EST_COST_USD = 0.05
APIFY_BUDGET_SCOPE = "provider:apify"

# 白名单来源标签(写入 contact_source)
SOURCE_YOUTUBE_ABOUT = "youtube_about_declared"
SOURCE_IG_BUSINESS = "ig_business_profile"
SOURCE_BIO_EXPLICIT = "bio_explicit_contact"
WHITELIST_SOURCES = {SOURCE_YOUTUBE_ABOUT, SOURCE_IG_BUSINESS, SOURCE_BIO_EXPLICIT}

FEATURE_FLAG = "business_email_enrichment"  # 默认 OFF(不进 DEFAULT_FLAG_ENABLED)

# 仅匹配「显式商务联系行」附近的邮箱,避免全文扫描。先找触发词锚点,再取同行邮箱。
_BUSINESS_ANCHORS = (
    "business inquiries", "business inquiry", "for business", "business email",
    "商务合作", "商务", "合作请联系", "contact:", "contact me", "reach me", "联系邮箱",
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _flag_enabled() -> bool:
    """合规门禁:抓取总开关。默认 OFF。"""
    try:
        row = get_conn().execute(
            "SELECT enabled FROM vkpi_feature_flags WHERE flag_key=?", (FEATURE_FLAG,)
        ).fetchone()
    except Exception:
        return False
    return bool(row and row["enabled"])  # 缺行视为关


def _budget_ok(*, est_cost: float = 0.0) -> bool:
    """闸A:Apify 预算闸预检(双闸并查,防御纵深)。

    零成本来源(纯读已抓回 raw,est_cost=0)默认放行;真要 Apify 专抓 about 页(Jianbo 已授权)
    传入 est_cost>0 时,必须同时过两道闸才放行:
      1) budget_guard 硬闸 vkpi_provider_budget_caps scope='provider:apify'(check_budget,与既有
         record_apify_run_cost 记账同 scope,累计 spend 对齐)。
      2) 操作员月度上限 vkpi_budget_settings budget_key='apify'(enabled 且 monthly>0 且 remaining>0)。
    任一闸不过 → 拒抓(返回 False),不发 Apify call。闸查异常一律保守拒抓。
    """
    cost = max(0.0, float(est_cost or 0))
    if cost <= 0:
        # 零成本路径(只读已抓 raw):仍需 budget_guard 未硬停才放行,保持与记账闸一致。
        try:
            return bool(check_budget(APIFY_BUDGET_SCOPE, 0.0, require_configured=False))
        except Exception:
            return False
    try:
        if not check_budget(APIFY_BUDGET_SCOPE, cost, require_configured=True):
            return False
    except Exception:
        return False
    # 操作员月度上限(vkpi_budget_settings 'apify')预检。
    try:
        from app.domains.settings.platform_crawl import _budget_available
        row = get_conn().execute(
            "SELECT * FROM vkpi_budget_settings WHERE budget_key=?", ("apify",)
        ).fetchone()
        ok, _monthly, _spent, _remaining = _budget_available(dict(row) if row else None)
        return bool(ok)
    except Exception:
        return False


def _extract_from_text(text: str, source: str) -> list[dict[str, Any]]:
    """只在 business 锚点行附近取邮箱 —— 非全网爬。"""
    if not text:
        return []
    out: list[dict[str, Any]] = []
    low = text.lower()
    for line in text.splitlines():
        ll = line.lower()
        if any(a in ll for a in _BUSINESS_ANCHORS):
            for m in _EMAIL_RE.findall(line):
                out.append({"contact_type": "business_email", "contact_value": m.strip(), "contact_source": source})
    # bio 显式 contact 行单独兜底(IG/YT 简介常把商务邮箱单独成行)
    if source == SOURCE_BIO_EXPLICIT and not out and ("contact" in low or "商务" in text):
        for m in _EMAIL_RE.findall(text):
            out.append({"contact_type": "business_email", "contact_value": m.strip(), "contact_source": source})
    return out


def extract_public_business_contacts(raw_platform_data: dict[str, Any], *, platform: str) -> list[dict[str, Any]]:
    """纯函数:从已抓回的 raw 里抽白名单来源公开商务邮箱。零网络、零成本。

    接入点(设计):pool.enrich_item 拿到 profile_payload 后,把 about/description/bio 字段喂进来;
    返回的 contacts 由调用方在门禁通过后落库。
    """
    contacts: list[dict[str, Any]] = []
    profile = (raw_platform_data or {}).get("profile") or {}
    if platform == "youtube":
        about = str((profile.get("snippet") or {}).get("description") or profile.get("description") or "")
        # YouTube Data API channels.list/search 把频道包在 items[0].snippet.description —— 真实 raw 形态。
        # 创作者常把明文商务邮箱写在频道简介(公开声明=白名单来源,合规)。
        if not about:
            items = profile.get("items") if isinstance(profile.get("items"), list) else []
            if items and isinstance(items[0], dict):
                about = str((items[0].get("snippet") or {}).get("description") or "")
        contacts += _extract_from_text(about, SOURCE_YOUTUBE_ABOUT)
    elif platform == "instagram":
        # IG 商务资料:public_email / business_email 字段直取(profile-scraper 偶含),再兜底 bio
        for key in ("public_email", "publicEmail", "business_email", "businessEmail"):
            val = str(profile.get(key) or "").strip()
            if val and _EMAIL_RE.fullmatch(val):
                contacts.append({"contact_type": "business_email", "contact_value": val, "contact_source": SOURCE_IG_BUSINESS})
        bio = str(profile.get("biography") or profile.get("bio") or "")
        contacts += _extract_from_text(bio, SOURCE_BIO_EXPLICIT)
    else:
        bio = str(profile.get("bio") or profile.get("description") or "")
        contacts += _extract_from_text(bio, SOURCE_BIO_EXPLICIT)
    # 去重 + 白名单兜底
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for c in contacts:
        k = c["contact_value"].lower()
        if k in seen or c["contact_source"] not in WHITELIST_SOURCES:
            continue
        seen.add(k)
        uniq.append(c)
    return uniq


def enrich_business_contacts(kol_pool_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """单条富化入口(禁群抓)。门禁:flag OFF / 预算不足 → 不写不调,返回原因。

    红线:本函数只写 email/other_contacts_json/contact_* 与 vkpi_kol_pool_contacts;不触 viltrox_fit_score。
    """
    if not _flag_enabled():
        return {"status": "disabled", "reason": "feature_flag business_email_enrichment OFF"}
    # 第一道预检:纯读已抓 raw 走零成本路径(est_cost=0),仅查 budget_guard 未硬停。
    if not _budget_ok(est_cost=0.0):
        return {"status": "budget_blocked", "reason": "apify budget gate (provider:apify hard-stopped)"}
    conn = get_conn()
    row = conn.execute("SELECT raw_platform_data, platform, email, other_contacts_json FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    try:
        raw = json.loads(row["raw_platform_data"] or "{}")
    except Exception:
        raw = {}
    contacts = extract_public_business_contacts(raw, platform=str(row["platform"] or ""))
    apify_run_ref = ""
    # raw 内无白名单联系 → Apify 专抓 about 页兜底(Jianbo 已授权;est_cost>0 触发双闸硬预检)。
    if not contacts:
        platform = str(row["platform"] or "")
        if _budget_ok(est_cost=APIFY_ABOUT_EST_COST_USD):
            scraped_raw, apify_run_ref = _apify_scrape_about(
                platform=platform,
                handle=str((raw.get("profile") or {}).get("handle") or ""),
                profile_url=str((raw.get("profile") or {}).get("profile_url") or (raw.get("profile") or {}).get("url") or ""),
                kol_pool_id=int(kol_pool_id),
                staff=staff,
            )
            if scraped_raw:
                contacts = extract_public_business_contacts(scraped_raw, platform=platform)
    if not contacts:
        return {"status": "no_public_contact", "kol_pool_id": int(kol_pool_id), "apify_run_ref": apify_run_ref}
    now = _utcnow()
    actor = (staff or {}).get("staff_id") or (staff or {}).get("user_id")
    primary = contacts[0]["contact_value"]
    for c in contacts:
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool_contacts
                (kol_pool_id, contact_type, contact_value, contact_source, consent_basis, extracted_by_staff_id, created_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(kol_pool_id, contact_type, contact_value) DO NOTHING
            """,
            (int(kol_pool_id), c["contact_type"], c["contact_value"], c["contact_source"], "legitimate_interest_public_business", actor, now),
        )
    # 写展示快照 + 来源元数据(只写联系列,绝不写 fit_score)
    conn.execute(
        """
        UPDATE vkpi_kol_pool
        SET email=CASE WHEN COALESCE(email,'')='' THEN ? ELSE email END,
            other_contacts_json=?,
            contact_source=?,
            contact_first_seen_at=COALESCE(contact_first_seen_at, ?),
            updated_at=?
        WHERE id=?
        """,
        (primary, json.dumps(contacts, ensure_ascii=False), contacts[0]["contact_source"], now, now, int(kol_pool_id)),
    )
    conn.commit()
    return {"status": "enriched", "kol_pool_id": int(kol_pool_id), "contacts": len(contacts), "source": contacts[0]["contact_source"], "apify_run_ref": apify_run_ref}


def add_manual_contact(
    kol_pool_id: int,
    *,
    email: str = "",
    platform: str = "",
    handle: str = "",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """人工保存联系方式(ContactModal「保存联系方式」)。合规留痕:contact_source='manual'、
    consent_basis='manual_entry'、is_public_declared=FALSE(员工录入,不主张公开声明)、
    extracted_by_staff_id=操作人;写 vkpi_kol_pool_contacts 审计表 + other_contacts_json 展示快照
    (并集去重)。无富化预算闸(纯人工录入、零外调)。红线:只写联系列,绝不触 viltrox_fit_score。"""
    email = (email or "").strip()
    handle = (handle or "").strip()
    platform = (platform or "").strip()
    if not email and not handle:
        return {"status": "empty", "reason": "need email or handle"}
    conn = get_conn()
    row = conn.execute(
        "SELECT id, email, other_contacts_json FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)
    ).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    now = _utcnow()
    actor = (staff or {}).get("staff_id") or (staff or {}).get("id") or (staff or {}).get("user_id")

    new_entries: list[dict[str, Any]] = []
    if email:
        new_entries.append({"contact_type": "email", "contact_value": email, "contact_source": "manual", "label": "email"})
    if handle:
        new_entries.append({
            "contact_type": "link", "contact_value": handle, "contact_source": "manual",
            "platform": platform or "other", "label": platform or "link",
        })

    for c in new_entries:
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool_contacts
                (kol_pool_id, contact_type, contact_value, contact_source, source_url,
                 consent_basis, is_public_declared, extracted_by_staff_id, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(kol_pool_id, contact_type, contact_value) DO NOTHING
            """,
            (int(kol_pool_id), c["contact_type"], c["contact_value"], "manual", "",
             "manual_entry", False, actor, now),
        )

    # 展示快照:并集去重(by contact_value 小写),保留既有抓取来源条目
    try:
        existing = json.loads(row["other_contacts_json"] or "[]")
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []
    seen = {str((e or {}).get("contact_value") or "").strip().lower() for e in existing if isinstance(e, dict)}
    for c in new_entries:
        key = c["contact_value"].strip().lower()
        if key and key not in seen:
            existing.append(c)
            seen.add(key)

    conn.execute(
        """
        UPDATE vkpi_kol_pool
        SET email=CASE WHEN COALESCE(email,'')='' THEN ? ELSE email END,
            other_contacts_json=?,
            contact_source=CASE WHEN COALESCE(contact_source,'')='' THEN 'manual' ELSE contact_source END,
            contact_first_seen_at=COALESCE(contact_first_seen_at, ?),
            updated_at=?
        WHERE id=?
        """,
        (email or "", json.dumps(existing, ensure_ascii=False), now, now, int(kol_pool_id)),
    )
    conn.commit()
    return {"status": "saved", "kol_pool_id": int(kol_pool_id), "saved": len(new_entries), "contacts": existing}


def _apify_scrape_about(*, platform: str, handle: str, profile_url: str, kol_pool_id: int, staff: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    """Apify 专抓 about 页(默认仅 youtube 有现成 about 抓取链路)。

    复用既有 YouTubeCrawler + record_apify_run_cost 记账(industry_crawlers),不新写 ApifyClient。
    返回 (raw_platform_data 形 dict, apify_run_ref)。任一异常 → 返回 ({}, '') 不阻断。
    记账由 crawler 内 record_apify_run_cost 落 vkpi_provider_budget_caps('provider:apify'),此处再补一条
    带 kol_pool_id/about 用途的 record_cost 留痕 apify_run_ref(便于合规追溯单条富化的 Apify 归因)。
    红线:抓回的 raw 只喂 extract_public_business_contacts;绝不触 viltrox_fit_score。
    """
    if platform != "youtube":
        return {}, ""
    try:
        from app.platform.industry_crawlers import get_crawler
        crawler = get_crawler("youtube")
        if crawler is None:
            return {}, ""
        result = crawler.crawl_channel_profile(profile_url or handle, max_posts=1)
        if not isinstance(result, dict) or str(result.get("provider_status") or "") != "ok":
            return {}, ""
        items = result.get("items") or []
        profile = items[0] if items and isinstance(items[0], dict) else {}
        apify_run_ref = str(((result.get("raw") or {}).get("apify_run_id")) or result.get("apify_run_id") or "")
        # 补一条带 kol_pool_id + about 用途的归因记账(crawler 已记主成本,此处 cost_usd=0 仅留 ref)。
        try:
            record_cost(
                scope=APIFY_BUDGET_SCOPE,
                ai_provider="apify",
                model_name="about_scrape",
                cost_usd=0.0,
                kol_pool_id=int(kol_pool_id),
                staff_id=int((staff or {}).get("staff_id") or (staff or {}).get("user_id") or 0) or None,
                metadata={"operation": "business_email_about_scrape", "apify_run_ref": apify_run_ref, "platform": platform},
            )
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
        return ({"profile": profile}, apify_run_ref)
    except Exception:
        return {}, ""
