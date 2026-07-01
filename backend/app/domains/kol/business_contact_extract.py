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

# ---- L0 多源抽取(分层取证漏斗第一刀:榨已抓回的公开 raw)----
# CDN/缩略图垃圾域名(外链过滤,非真个人站)。
_CDN_JUNK = (
    "ytimg", "tiktokcdn", "cdninstagram", "googleusercontent", "fbcdn",
    "licdn", "twimg", "sndcdn", "w3.org", "schema.org", "gstatic",
)
# 社媒域名 -> 渠道标签(外链里属"社媒渠道"而非"独立站")。
_SOCIAL_HOSTS = {
    "instagram.com": "instagram", "tiktok.com": "tiktok", "youtube.com": "youtube",
    "youtu.be": "youtube", "facebook.com": "facebook", "twitter.com": "twitter",
    "x.com": "twitter", "t.me": "telegram", "wa.me": "whatsapp", "discord.gg": "discord",
    "linkedin.com": "linkedin", "twitch.tv": "twitch", "pinterest.com": "pinterest",
}
# 聚合页(Linktree 类)—— L1 优先跟进,常把邮箱+全套社媒列齐。
_LINK_HUBS = ("linktr.ee", "beacons.ai", "carrd.co", "stan.store", "linkin.bio", "koji.to", "campsite.bio", "solo.to", "linkpop.com")
_EMAIL_PLACEHOLDER = {"user@domain.com", "name@example.com", "email@example.com", "you@example.com", "your@email.com", "someone@example.com"}
_EMAIL_BAD_SUFFIX = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".mp4")
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")

SOURCE_RAW_BIO = "raw_bio_scan"        # 裸 raw/bio/描述扫到(置信中)
SOURCE_VIDEO_CAPTION = "video_caption"  # 视频标题/文案
SOURCE_WEBSITE = "website_declared"    # 独立站/contact 页抓到(L1)


# 真 TLD 白名单:所有 2 字母国别码视为有效 + 常见多字母 gTLD。用来砍掉
# IG @提及链被误判成邮箱的假命中(如 n@hamid.monadi / n@flawless.finish.by.aaminah,
# .monadi/.aaminah 不是真 TLD)。宁缺勿滥,漏掉极生僻 gTLD(.photography)可接受。
_VALID_MULTICHAR_TLDS = {
    "com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "name", "pro",
    "mobi", "online", "store", "shop", "site", "tech", "dev", "app", "live", "studio",
    "media", "agency", "team", "world", "email", "link", "xyz", "club", "design",
    "photography", "film", "video", "social", "blog", "news", "photo", "pics",
    "asia", "eu", "cat", "tel",
}


def _valid_email(m: str) -> bool:
    """过滤 CDN 后缀 / 占位 / @提及式假命中,只留合法 TLD 的真邮箱。"""
    m = (m or "").strip().lower().rstrip(".")
    if not m or m in _EMAIL_PLACEHOLDER or m.endswith(_EMAIL_BAD_SUFFIX):
        return False
    if m.count("@") != 1:
        return False
    local, _, domain = m.partition("@")
    if not local or "." not in domain:
        return False
    tld = domain.rsplit(".", 1)[-1]
    if not tld.isalpha():
        return False
    if len(tld) == 2:  # 所有 2 字母国别 TLD 视为有效(.de/.uk/.by/.jp ...)
        return True
    return tld in _VALID_MULTICHAR_TLDS


def _text_blobs(profile: dict[str, Any]) -> list[str]:
    """汇总 raw 里可能含联系方式的公开文本字段(bio/about/描述/文案/签名)。"""
    blobs: list[str] = []
    for k in ("biography", "bio", "description", "about", "channel_description", "signature", "title", "caption", "desc"):
        v = profile.get(k)
        if isinstance(v, str):
            blobs.append(v)
        elif isinstance(v, dict) and isinstance(v.get("description"), str):
            blobs.append(v["description"])
    snip = profile.get("snippet")
    if isinstance(snip, dict) and isinstance(snip.get("description"), str):
        blobs.append(snip["description"])
    items = profile.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        sn = items[0].get("snippet") or {}
        if isinstance(sn, dict) and isinstance(sn.get("description"), str):
            blobs.append(sn["description"])
    return [b for b in blobs if b]


def _email_confidence(email: str, blob: str) -> tuple[float, str]:
    """按上下文给置信度 + 取证片段:所在行/全文有商务锚点 → 0.9,否则裸 raw → 0.55。"""
    low = blob.lower()
    idx = low.find(email.lower())
    snippet = blob[max(0, idx - 40): idx + len(email) + 20].strip() if idx >= 0 else email
    line = next((ln.lower() for ln in blob.splitlines() if email.lower() in ln.lower()), "")
    if any(a in line for a in _BUSINESS_ANCHORS) or any(a in low for a in _BUSINESS_ANCHORS):
        return 0.9, snippet
    return 0.55, snippet


def extract_contacts_multi_source(
    raw_platform_data: dict[str, Any], *, platform: str = "", source_url: str = ""
) -> list[dict[str, Any]]:
    """L0 多源联系方式抽取(纯函数、零网络、零成本):从已抓回的公开 raw 里榨联系方式。
    覆盖:email(bio/描述/文案)、显式 business_email 字段、外链独立站/Linktree(待 L1 跟进)、社媒渠道。
    每条带 source_type + confidence + evidence_text。不做全网爬;比既有白名单版更全,用置信度分级替代硬白名单。
    红线:纯读文本抽联系方式,绝不触 viltrox_fit_score。
    """
    del platform, source_url  # 预留签名(L1 独立站抓取会用 source_url)
    profile = (raw_platform_data or {}).get("profile") or raw_platform_data or {}
    if not isinstance(profile, dict):
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(ctype: str, value: str, source_type: str, confidence: float, evidence: str) -> None:
        value = (value or "").strip().rstrip(".,​")
        if not value:
            return
        key = (ctype, value.lower())
        if key in seen:
            return
        seen.add(key)
        out.append({
            "contact_type": ctype, "contact_value": value, "source_type": source_type,
            "confidence": round(float(confidence), 2), "evidence_text": (evidence or "")[:280],
        })

    blobs = _text_blobs(profile)
    full = "\n".join(blobs)
    for blob in blobs:
        for m in _EMAIL_RE.findall(blob):
            if _valid_email(m):
                conf, ev = _email_confidence(m, blob)
                _add("email", m.strip().rstrip("."), SOURCE_RAW_BIO, conf, ev)
    for k in ("public_email", "publicEmail", "business_email", "businessEmail", "email"):
        v = str(profile.get(k) or "").strip()
        if v and _valid_email(v):
            _add("email", v, SOURCE_IG_BUSINESS, 0.92, f"{k}={v}")
    for m in _URL_RE.findall(full):
        u = m.strip().rstrip(".,)​")
        low = u.lower()
        if any(j in low for j in _CDN_JUNK):
            continue
        host = low.split("//", 1)[-1].split("/", 1)[0]
        social = next((tag for h, tag in _SOCIAL_HOSTS.items() if h in host), "")
        if social:
            _add(f"{social}_link", u, SOURCE_RAW_BIO, 0.6, u)
        elif any(h in low for h in _LINK_HUBS):
            _add("link_hub", u, SOURCE_RAW_BIO, 0.5, u)
        else:
            _add("website", u, SOURCE_RAW_BIO, 0.45, u)
    # 全 raw 兜底扫描:邮箱常在帖文案/嵌套字段里,结构化字段扫不到。创作者档案 raw 绝大多数是
    # 其自有内容,故兜底扫到的邮箱多为本人。低置信(0.45)+ 独立来源标签,与结构化高置信条目区分;
    # 已见的不重复(结构化先跑,高置信保留)。仍只吃已抓回的公开 raw,不做全网爬。
    try:
        full_raw = json.dumps(raw_platform_data, ensure_ascii=False)
    except Exception:
        full_raw = full
    for m in _EMAIL_RE.findall(full_raw):
        if _valid_email(m):
            _add("email", m.strip().rstrip("."), "raw_full_scan", 0.45, "")
    return out


def enrich_contacts_l0(
    kol_pool_id: int, *, conn: Any | None = None, staff: dict[str, Any] | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """L0 免费富化:读该 KOL 已抓回的 raw -> extract_contacts_multi_source -> 落 vkpi_kol_pool_contacts
    (带 confidence/evidence/首末见)+ 回填 vkpi_kol_pool.email(择最高置信 email,仅当主表空)/other_contacts_json。
    纯读已有公开 raw、零外调、零成本。dry_run=True 只返回将写内容、不落库。红线:不触 viltrox_fit_score。"""
    db = conn or get_conn()
    row = db.execute(
        "SELECT id, platform, email, other_contacts_json, raw_platform_data FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        return {"status": "not_found", "kol_pool_id": int(kol_pool_id)}
    d = dict(row)
    try:
        raw = json.loads(d.get("raw_platform_data") or "{}")
    except Exception:
        raw = {}
    if not isinstance(raw, dict) or not raw:
        return {"status": "no_raw", "kol_pool_id": int(kol_pool_id)}
    contacts = extract_contacts_multi_source(raw, platform=str(d.get("platform") or ""))
    emails = sorted([c for c in contacts if c["contact_type"] == "email"], key=lambda c: -float(c.get("confidence") or 0))
    best_email = emails[0]["contact_value"] if emails else ""
    by_type: dict[str, int] = {}
    for c in contacts:
        by_type[c["contact_type"]] = by_type.get(c["contact_type"], 0) + 1
    if dry_run:
        return {
            "status": "dry_run", "kol_pool_id": int(kol_pool_id), "found": len(contacts),
            "best_email": best_email, "email_would_backfill": bool(best_email and not str(d.get("email") or "").strip()),
            "by_type": by_type, "contacts": contacts[:20],
        }
    if not contacts:
        return {"status": "no_contacts", "kol_pool_id": int(kol_pool_id)}
    now = _utcnow()
    actor = (staff or {}).get("staff_id") or (staff or {}).get("id") or (staff or {}).get("user_id")
    for cc in contacts:
        conf = round(float(cc.get("confidence") or 0.0), 2)
        db.execute(
            """
            INSERT INTO vkpi_kol_pool_contacts
                (kol_pool_id, contact_type, contact_value, contact_source, source_url,
                 consent_basis, is_public_declared, extracted_by_staff_id,
                 confidence, evidence_text, first_seen_at, last_seen_at, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(kol_pool_id, contact_type, contact_value) DO NOTHING
            """,
            (int(kol_pool_id), cc["contact_type"], cc["contact_value"],
             cc.get("source_type") or "raw_bio_scan", "", "public_scan", conf >= 0.85, actor,
             conf, (cc.get("evidence_text") or "")[:280], now, now, now),
        )
    try:
        existing = json.loads(d.get("other_contacts_json") or "[]")
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []
    seen = {str((e or {}).get("contact_value") or "").strip().lower() for e in existing if isinstance(e, dict)}
    for cc in contacts:
        k = cc["contact_value"].strip().lower()
        if k and k not in seen:
            existing.append({
                "contact_type": cc["contact_type"], "contact_value": cc["contact_value"],
                "contact_source": cc.get("source_type"), "confidence": cc.get("confidence"),
            })
            seen.add(k)
    db.execute(
        """
        UPDATE vkpi_kol_pool
        SET email=CASE WHEN COALESCE(email,'')='' THEN ? ELSE email END,
            other_contacts_json=?,
            contact_source=CASE WHEN COALESCE(contact_source,'')='' THEN 'raw_scan' ELSE contact_source END,
            contact_first_seen_at=COALESCE(contact_first_seen_at, ?),
            updated_at=?
        WHERE id=?
        """,
        (best_email, json.dumps(existing, ensure_ascii=False), now, now, int(kol_pool_id)),
    )
    db.commit()
    return {"status": "ok", "kol_pool_id": int(kol_pool_id), "found": len(contacts), "email": best_email, "by_type": by_type}


def backfill_contacts_l0(*, limit: int | None = None, only_missing_email: bool = False) -> dict[str, Any]:
    """对全池已有 raw 的 KOL 跑一遍 L0 富化(纯读 raw、零外调、零成本)。管理员在 prod 手跑。
    返回汇总。红线:不触 viltrox_fit_score。"""
    db = get_conn()
    where = "raw_platform_data IS NOT NULL AND raw_platform_data <> ''"
    if only_missing_email:
        where += " AND COALESCE(email,'')=''"
    sql = f"SELECT id FROM vkpi_kol_pool WHERE {where} ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    ids = [int(dict(r)["id"]) for r in db.execute(sql).fetchall()]
    processed = with_contacts = email_filled = 0
    for kid in ids:
        try:
            res = enrich_contacts_l0(kid, conn=db)
        except Exception:
            logger.warning("enrich_contacts_l0 failed kol=%s", kid, exc_info=True)
            continue
        processed += 1
        if res.get("found"):
            with_contacts += 1
        if res.get("email"):
            email_filled += 1
    return {"status": "done", "candidates": len(ids), "processed": processed, "with_contacts": with_contacts, "email_filled": email_filled}


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
