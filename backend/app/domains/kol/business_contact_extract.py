"""P0-1 公开商务邮箱富化(默认关、白名单来源、单条、闸A 预算 + PII 合规)。

严格边界(合规):
  - 只取创作者公开声明的商务邮箱,来源白名单 = {youtube_about_declared, ig_business_profile, bio_explicit_contact}。
  - 绝不做全网正则爬邮箱;只在 raw_platform_data 已抓回的 about/profile/bio 字段内,匹配「显式 contact 行」。
  - 默认 feature_flag business_email_enrichment = OFF;关时函数直接返回 disabled,不写库不调 Apify。
红线:不触 viltrox_fit_score;只写 vkpi_kol_pool.email/other_contacts_json/contact_* 与 vkpi_kol_pool_contacts。

Apify 专抓 about 页已实现(Jianbo 已授权;2026-07-19 用户明令放行 Apify 花费,provider:apify
月帽已提至 $150):批量入口 fetch_about_and_enrich(默认 dry_run),单条兜底在 enrich_business_contacts。
Apify 直呼走 durable claim(acquire/apify_execution_context/finalize)+ call_apify_actor 内建预检记账。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.costs.budget_guard import check_budget, record_cost
from app.domains.kol.business_contact_about_helpers import (
    about_profile_from_result as _about_profile_from_result,
    record_about_scrape_cost as _record_about_scrape_cost,
)

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
    "business inquiries", "business inquiry", "business enquiries", "business enquiry",
    "for business", "business email",
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
# 页面腿(run_website_contact_batch)按 contact_type='link_hub' 排在 website 前、每 KOL 只取前 3 条,
# 所以漏一个聚合页域名 = 把邮箱产出率最高的目标挤到随机个人站后面。名单只收「一页列全套链接的
# link-in-bio 服务」,收录前逐个核过产品形态。
# 刻意不收(2026-08-31 核过,是纯跳转短链/深链而非聚合页,收了会让页面腿去抓一个空转发页):
#   tr.ee(Linktree 旗下通用短链,实测 tr.ee/0lE1CH 直跳 Shopee 商品页)、
#   linktw.in(LinkTwin 深链短链)、flowcode.com(QR/企业跳转平台)。
_LINK_HUBS = (
    "linktr.ee", "beacons.ai", "carrd.co", "stan.store", "linkin.bio", "koji.to",
    "campsite.bio", "solo.to", "linkpop.com",
    # 2026-08-31 补:本地库 49 条外链原被误判成 'website'(bio.site 一家就 26 条)。
    "bio.site", "bio.link", "link.me", "liinks.co", "taplink.cc", "hoo.be", "dott.bio",
    "superprofile.bio", "allmylinks.com", "linkfly.to", "linkgenie.co", "lnk.bio",
    "msha.ke", "milkshake.app", "shorby.com", "manylink.co",
)
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


def _url_host(url: str) -> str:
    """URL/裸串 -> 小写 host(去 scheme、path、query、fragment、userinfo、端口、www. 前缀)。

    取不到 host 返回 ''。口径与 scripts/backfill_social_bio_links.link_host 一致——两条腿
    (L0 抽取 与 bio 外链回填)必须对同一个 URL 判出同一个 host,否则同一域名会一半 link_hub
    一半 website。
    """
    rest = str(url or "").split("//", 1)[-1]
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.rsplit("@", 1)[-1].split(":", 1)[0].strip().strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else ""


def _host_matches(host: str, needle: str) -> bool:
    """精确 host 匹配(== 或子域后缀)。

    绝不能退回 substring:`jurjax.com` 含 "x.com" 会被误判成 twitter,
    `example.com/linktr.ee` 这种 path 也会被误判成聚合页。
    """
    return host == needle or host.endswith("." + needle)


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


def _author_nested_blobs(
    container: dict[str, Any], *, prefix: str
) -> list[tuple[str, str]]:
    """TT/IG raw 的 bio 常嵌在 authorMeta/author/owner/user 里(TT=authorMeta.signature)。
    C6 零新抓提列:结构化提出来走正常置信度分级,不再只靠低置信 full_raw 兜底扫。"""
    found: list[tuple[str, str]] = []
    for ak in ("authorMeta", "author", "owner", "user"):
        av = container.get(ak)
        if not isinstance(av, dict):
            continue
        for fk in ("signature", "bio", "biography", "description"):
            fv = av.get(fk)
            if isinstance(fv, str) and fv:
                found.append((f"{prefix}.{ak}.{fk}", fv))
    return found


def _text_blobs(
    profile: dict[str, Any], *, prefix: str
) -> list[tuple[str, str]]:
    """Return public text together with its exact field-level locator."""
    blobs: list[tuple[str, str]] = []
    for k in ("biography", "bio", "description", "about", "channel_description", "signature", "title", "caption", "desc"):
        v = profile.get(k)
        if isinstance(v, str):
            blobs.append((f"{prefix}.{k}", v))
        elif isinstance(v, dict) and isinstance(v.get("description"), str):
            blobs.append((f"{prefix}.{k}.description", v["description"]))
    snip = profile.get("snippet")
    if isinstance(snip, dict) and isinstance(snip.get("description"), str):
        blobs.append((f"{prefix}.snippet.description", snip["description"]))
    blobs.extend(_author_nested_blobs(profile, prefix=prefix))
    items = profile.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        sn = items[0].get("snippet") or {}
        if isinstance(sn, dict) and isinstance(sn.get("description"), str):
            blobs.append((f"{prefix}.items.0.snippet.description", sn["description"]))
        blobs.extend(_author_nested_blobs(items[0], prefix=f"{prefix}.items.0"))
    return [(field, blob) for field, blob in blobs if blob]


def _iter_raw_strings(node: Any) -> list[str]:
    """深度遍历 raw 结构,收集全部字符串叶子(含 dict key),供兜底扫描直接吃原文。

    绝不可用 json.dumps(raw) 的文本喂 _EMAIL_RE:dumps 会把真实换行/制表符
    转义成字面两字符序列(\\n、\\t),邮箱正则本地部分含字母不含反斜杠,
    「换行+邮箱」会被吞成 n/t 前缀假地址(\\nfoo@bar.com -> nfoo@bar.com),
    外联就往错地址发。原始字符串里真实换行不在字符类内,天然是边界。
    """
    out: list[str] = []
    stack: list[Any] = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            out.append(cur)
        elif isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(k, str):
                    out.append(k)
                stack.append(v)
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    return out


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
    platform_key = str(platform or "").strip().casefold()
    del source_url  # 预留签名(L1 独立站抓取会用 source_url)
    raw = raw_platform_data or {}
    nested_profile = raw.get("profile") if isinstance(raw, dict) else None
    has_profile_container = isinstance(nested_profile, dict) and bool(nested_profile)
    profile = nested_profile if has_profile_container else raw
    field_prefix = "profile" if has_profile_container else "raw_platform_data"
    if not isinstance(profile, dict):
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(
        ctype: str,
        value: str,
        source_type: str,
        confidence: float,
        evidence: str,
        *,
        source_field: str,
    ) -> None:
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
            "source_field": source_field,
        })

    blobs = _text_blobs(profile, prefix=field_prefix)
    # A structured Instagram business field is the only L0 platform-native
    # field that can carry the IG verification source.  Generic ``email`` and
    # the same field names on YouTube/TikTok remain observations; their raw
    # payload shape does not prove a public business declaration.
    trusted_ig_fields = ("public_email", "publicEmail", "business_email", "businessEmail")
    if has_profile_container and platform_key in {"instagram", "ig"}:
        for key in trusted_ig_fields:
            value = str(profile.get(key) or "").strip()
            if value and _valid_email(value):
                _add(
                    "email",
                    value,
                    SOURCE_IG_BUSINESS,
                    0.92,
                    f"{key}={value}",
                    source_field=f"{field_prefix}.{key}",
                )

    for source_field, blob in blobs:
        for m in _EMAIL_RE.findall(blob):
            if _valid_email(m):
                conf, ev = _email_confidence(m, blob)
                _add(
                    "email",
                    m.strip().rstrip("."),
                    SOURCE_RAW_BIO,
                    conf,
                    ev,
                    source_field=source_field,
                )
        for match in _URL_RE.findall(blob):
            url = match.strip().rstrip(".,)​")
            low = url.lower()
            if any(junk in low for junk in _CDN_JUNK):
                continue
            host = _url_host(low)
            # 精确 host 匹配(== 或子域后缀),不能用 substring:jurjax.com 含 "x.com" 会误判 twitter
            social = next((tag for h, tag in _SOCIAL_HOSTS.items() if _host_matches(host, h)), "")
            if social:
                _add(f"{social}_link", url, SOURCE_RAW_BIO, 0.6, url, source_field=source_field)
            elif any(_host_matches(host, hub) for hub in _LINK_HUBS):
                _add("link_hub", url, SOURCE_RAW_BIO, 0.5, url, source_field=source_field)
            else:
                _add("website", url, SOURCE_RAW_BIO, 0.45, url, source_field=source_field)

    # Untrusted structured fields are useful discovery clues but never become
    # platform verification evidence at this layer.
    for k in ("public_email", "publicEmail", "business_email", "businessEmail", "email"):
        v = str(profile.get(k) or "").strip()
        if v and _valid_email(v):
            _add(
                "email",
                v,
                SOURCE_RAW_BIO,
                0.65,
                f"{k}={v}",
                source_field=f"{field_prefix}.{k}",
            )
    # 全 raw 兜底扫描:邮箱常在帖文案/嵌套字段里,结构化字段扫不到。创作者档案 raw 绝大多数是
    # 其自有内容,故兜底扫到的邮箱多为本人。低置信(0.45)+ 独立来源标签,与结构化高置信条目区分;
    # 已见的不重复(结构化先跑,高置信保留)。仍只吃已抓回的公开 raw,不做全网爬。
    # 注意:必须扫原始字符串叶子而非 json.dumps 文本(转义换行会腐蚀出 n 前缀假邮箱,见 _iter_raw_strings)。
    try:
        full_raw = "\n".join(_iter_raw_strings(raw_platform_data))
    except Exception:
        full_raw = "\n".join(blob for _field, blob in blobs)
    for m in _EMAIL_RE.findall(full_raw):
        if _valid_email(m):
            _add(
                "email",
                m.strip().rstrip("."),
                "raw_full_scan",
                0.45,
                "",
                source_field="raw_platform_data",
            )
    return out


def enrich_contacts_l0(
    kol_pool_id: int, *, conn: Any | None = None, staff: dict[str, Any] | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """Compatibility entry that queues the durable provider-free L0 cycle.

    Canonical writes are owned by ``contact_acquisition_queue`` and
    ``contact_ingest``.  This legacy helper no longer writes pool snapshots or
    canonical rows directly and never invokes a provider.
    """
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
    by_type: dict[str, int] = {}
    for c in contacts:
        by_type[c["contact_type"]] = by_type.get(c["contact_type"], 0) + 1
    if dry_run:
        return {
            "status": "dry_run", "kol_pool_id": int(kol_pool_id), "found": len(contacts),
            "by_type": by_type, "provider_calls": False, "write_db": False,
        }
    if not contacts:
        return {"status": "no_contacts", "kol_pool_id": int(kol_pool_id)}
    from app.domains.kol.contact_acquisition_queue import enqueue_contact_acquisition

    queued = enqueue_contact_acquisition(
        int(kol_pool_id), trigger_source="reconcile", conn=db
    )
    return {
        "status": "queued_l0",
        "kol_pool_id": int(kol_pool_id),
        "found": len(contacts),
        "by_type": by_type,
        "queue_status": queued.get("status"),
        "provider_calls": False,
        "website_crawls": False,
        "messages_sent": False,
    }


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
    """Queue the zero-provider L0 reconciler; never scrape from a read flow."""
    del staff
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)
    ).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    from app.domains.kol.contact_acquisition_queue import enqueue_contact_acquisition

    queued = enqueue_contact_acquisition(
        int(kol_pool_id), trigger_source="reconcile", conn=conn
    )
    return {
        "status": "queued_l0",
        "kol_pool_id": int(kol_pool_id),
        "queue_status": queued.get("status"),
        "provider_calls": False,
        "website_crawls": False,
        "messages_sent": False,
    }


def add_manual_contact(
    kol_pool_id: int,
    *,
    email: str = "",
    platform: str = "",
    handle: str = "",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store manual observations through the canonical ingest lifecycle."""
    email = (email or "").strip()
    handle = (handle or "").strip()
    platform = (platform or "").strip()
    if not email and not handle:
        return {"status": "empty", "reason": "need email or handle"}
    conn = get_conn()
    row = conn.execute("SELECT id FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    actor = (staff or {}).get("staff_id") or (staff or {}).get("id") or (staff or {}).get("user_id")
    from app.domains.kol.contact_ingest import ContactValidationError, ingest_contact

    new_entries: list[tuple[str, str, str]] = []
    if email:
        new_entries.append(("email", email, "operator.email"))
    if handle:
        platform_key = platform.casefold()
        dm_types = {
            "instagram": "instagram_dm",
            "ig": "instagram_dm",
            "tiktok": "tiktok_dm",
            "x": "x_dm",
            "twitter": "x_dm",
            "facebook": "facebook_dm",
            "telegram": "telegram_dm",
        }
        if handle.startswith(("http://", "https://")):
            new_entries.append(("website", handle, "operator.link"))
        elif platform_key in dm_types:
            new_entries.append((dm_types[platform_key], handle, f"operator.{platform_key}"))
        elif platform_key in {"youtube", "yt"} and handle.startswith("@"):
            new_entries.append(("youtube_link", f"https://youtube.com/{handle}", "operator.youtube"))

    requested = int(bool(email)) + int(bool(handle))
    saved = rejected = 0
    for contact_type, contact_value, source_field in new_entries:
        try:
            ingest_contact(
                kol_pool_id=int(kol_pool_id),
                contact_type=contact_type,
                contact_value=contact_value,
                source_type="manual",
                source_field=source_field,
                confidence=1.0,
                is_public_declared=False,
                verification_status="observed",
                staff_id=actor,
                consent_basis="manual_entry",
                conn=conn,
            )
            saved += 1
        except (ContactValidationError, TypeError, ValueError):
            rejected += 1
    from app.domains.kol.contact_system import refresh_contactability

    refresh_contactability(int(kol_pool_id), conn=conn)
    return {
        "status": "saved" if saved else "invalid",
        "kol_pool_id": int(kol_pool_id),
        "saved": saved,
        "rejected": rejected + max(0, requested - len(new_entries)),
        "contacts": [],
        "contact_masked": True,
        "verification_status": "observed",
        "provider_calls": False,
    }


def _about_claim_task_id(kol_pool_id: int) -> str:
    """稳定 task_id(不带日期):claim 表 state='completed' 兼作「已花钱抓过」标记,批量选人时排除。"""
    return f"contact_about:{int(kol_pool_id)}"


def _run_about_profile_crawl(
    crawler: Any,
    *,
    profile_url: str,
    handle: str,
    task_id: str,
    own_claim: bool,
    fence: int,
    apify_execution_context: Any,
    finalize_provider_execution_claim: Any,
) -> dict[str, Any] | None:
    from contextlib import nullcontext

    state = "failed"
    try:
        with (apify_execution_context(task_id, fence) if own_claim else nullcontext()):
            result = crawler.crawl_channel_profile(profile_url or handle, max_posts=1)
        status = str((result or {}).get("provider_status") or "") if isinstance(result, dict) else ""
        state = "completed" if status in {"ok", "no_results"} else "failed"
        return result
    finally:
        if own_claim:
            try:
                finalize_provider_execution_claim(task_id, fence, state)
            except Exception:
                logger.debug("about-scrape claim finalize failed task=%s", task_id, exc_info=True)


def _apify_scrape_about(*, platform: str, handle: str, profile_url: str, kol_pool_id: int, staff: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    """Apify 专抓 about 页(默认仅 youtube 有现成 about 抓取链路;Jianbo 已授权)。

    复用既有 YouTubeCrawler + call_apify_actor 管线(内建 provider:apify 预检 + record_apify_run_cost 记账),
    不新写 ApifyClient。本功能=「Apify 专抓」授权项:置空 api_key 强制走 Apify 车道(streamers/youtube-scraper
    profile 模式 max_posts=1),不吃 Data API 优先分支,保证 claim/预检/记账真实生效。
    Apify 直呼必走 durable claim:acquire → apify_execution_context → finalize(缺 context 时
    call_apify_actor 会以 durable_execution_context_required 拒发)。已有外层 context 则复用不重复抢。
    返回 (raw_platform_data 形 dict, apify_run_ref)。任一异常 → 返回 ({}, '') 不阻断批次。
    红线:抓回的 raw 只喂联系方式抽取;绝不触 viltrox_fit_score。
    """
    if platform != "youtube":
        return {}, ""
    try:
        from app.platform.apify_budget import (
            acquire_provider_execution_claim,
            apify_execution_context,
            current_apify_execution_context,
            finalize_provider_execution_claim,
        )
        from app.platform.industry_crawlers import get_crawler
        crawler = get_crawler("youtube")
        if crawler is None:
            return {}, ""
        crawler.api_key = ""  # 强制 Apify 车道(见 docstring);无 APIFY_TOKEN 时 configured=False 直接不抓
        if not crawler.configured:
            return {}, ""
        task_id = _about_claim_task_id(kol_pool_id)
        own_claim = current_apify_execution_context() is None
        fence = acquire_provider_execution_claim(
            task_id, "business_contact_extract", job_type="business_email_about_scrape", lease_seconds=900,
        ) if own_claim else 0
        result = _run_about_profile_crawl(
            crawler,
            profile_url=profile_url,
            handle=handle,
            task_id=task_id,
            own_claim=own_claim,
            fence=fence,
            apify_execution_context=apify_execution_context,
            finalize_provider_execution_claim=finalize_provider_execution_claim,
        )
        if not isinstance(result, dict) or str(result.get("provider_status") or "") != "ok":
            return {}, ""
        profile = _about_profile_from_result(result)
        apify_run_ref = str(((result.get("raw") or {}).get("apify_run_id")) or result.get("apify_run_id") or "")
        # 补一条带 kol_pool_id + about 用途的归因记账(crawler 已记主成本,此处 cost_usd=0 仅留 ref)。
        _record_about_scrape_cost(
            kol_pool_id=kol_pool_id,
            staff=staff,
            apify_run_ref=apify_run_ref,
            platform=platform,
            budget_scope=APIFY_BUDGET_SCOPE,
            record_cost=record_cost,
            logger=logger,
        )
        return ({"profile": profile}, apify_run_ref)
    except Exception:
        logger.warning("about-scrape failed kol=%s", kol_pool_id, exc_info=True)
        return {}, ""


def _about_backlog_rows(db: Any, limit: int) -> tuple[int, list[dict[str, Any]]]:
    """目标人群:youtube、主表 email 空、有 handle/profile_url;排除已 completed 抓过的(claim 表标记)。
    返回 (全量 backlog 数, 本批行)。claim 表缺失(极端本地环境)时回退不带排除的查询。"""
    base = (
        "FROM vkpi_kol_pool p WHERE p.platform='youtube' AND COALESCE(p.email,'')='' "
        "AND (COALESCE(p.handle,'')<>'' OR COALESCE(p.profile_url,'')<>'')"
    )
    excl = (
        " AND NOT EXISTS (SELECT 1 FROM vkpi_provider_execution_claims c "
        "WHERE c.task_id=('contact_about:' || CAST(p.id AS TEXT)) AND c.state='completed')"
    )
    for where in (base + excl, base):
        try:
            total = int(dict(db.execute(f"SELECT COUNT(*) AS n {where}").fetchone())["n"])
            rows = [dict(r) for r in db.execute(
                f"SELECT p.id, p.handle, p.profile_url, p.email, p.other_contacts_json {where} ORDER BY p.id LIMIT ?",
                (int(limit),),
            ).fetchall()]
            return total, rows
        except Exception:
            try:
                db.rollback()
            except Exception:
                logger.debug("回滚失败(best-effort)", exc_info=True)
    return 0, []


def fetch_about_and_enrich(batch_size: int = 50, *, dry_run: bool = True, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read-only L1 backlog preview; execution is fail-closed in this slice.

    ``dry_run=True`` reports counts/cost only.  ``dry_run=False`` remains
    disabled and performs zero provider calls and zero writes until a separate
    authorized acquisition/export workflow exists.
    """
    batch = max(1, min(50, int(batch_size or 1)))
    db = get_conn()
    total, rows = _about_backlog_rows(db, batch)
    est_cost = round(len(rows) * APIFY_ABOUT_EST_COST_USD, 2)
    if dry_run:
        return {
            "status": "dry_run", "target_backlog": total, "batch": len(rows),
            "est_cost_usd": est_cost, "flag_enabled": _flag_enabled(),
            "budget_ok": _budget_ok(est_cost=APIFY_ABOUT_EST_COST_USD),
            "sample": [{"id": r["id"], "handle": r.get("handle")} for r in rows[:10]],
        }
    del staff
    # Provider-backed contact acquisition needs a separately authorized export
    # workflow.  This compatibility entry remains observable but executable
    # only as a zero-write dry run; it cannot call Apify or write canonical
    # contact rows in the L0 contactability slice.
    return {
        "status": "disabled",
        "reason": "external_contact_acquisition_not_authorized",
        "target_backlog": total,
        "batch": len(rows),
        "provider_calls": False,
        "write_db": False,
    }
