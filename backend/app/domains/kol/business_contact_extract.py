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


def _budget_ok() -> bool:
    """闸A:Apify 预算闸。零成本来源(已抓回 raw)走这里仍返回 True;真要 Apify 专抓 about 页
    (Jianbo 已授权)由调用方先查 vkpi_budget_settings 'apify' 闸,预算不足则不发 call。
    """
    # TODO: 复用 vkpi_budget_settings 'apify' 闸预检;此处占位,授权前不接 Apify 专抓。
    return True


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
    if not _budget_ok():
        return {"status": "budget_blocked", "reason": "apify budget gate"}
    conn = get_conn()
    row = conn.execute("SELECT raw_platform_data, platform, email, other_contacts_json FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    try:
        raw = json.loads(row["raw_platform_data"] or "{}")
    except Exception:
        raw = {}
    contacts = extract_public_business_contacts(raw, platform=str(row["platform"] or ""))
    if not contacts:
        return {"status": "no_public_contact", "kol_pool_id": int(kol_pool_id)}
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
    return {"status": "enriched", "kol_pool_id": int(kol_pool_id), "contacts": len(contacts), "source": contacts[0]["contact_source"]}
