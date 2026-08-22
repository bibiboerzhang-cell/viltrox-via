"""B1 联系方式 L0 · 可联系性打分(纯规则、零 LLM、零网络、零成本)。

数据源(全是已落库真数据,不发任何外部请求):
  - vkpi_kol_pool_contacts canonical 审计表;
  - legacy vkpi_kol_pool.email 不作为 verified/contactable 真值。

打分口径 scoring_method = "channels_recency_source_v1":
  - 渠道分:每个去重渠道贡献 weight x strength;strength = 该渠道最高置信度
    (行无 confidence 时按来源可信度 SOURCE_TRUST 兜底);渠道分合计封顶 85。
  - 新鲜度加成:最近验证 30 天内 +15 / 90 天内 +10 / 365 天内 +5 / 更久 +2 / 无时间 +0。
  - 总分 0-100;无任何渠道诚实 0 分(reason="no_contact_channels")。

合规红线:
  - 返回值只带渠道/计数/置信度等 value-free 元数据,明文或星号掩码绝不出本模块;
    真值只走 contact_reveal.view_kol_contact 二次确认 + 审计门控。
  - contactability_score 是触达运营分,不是契合分:绝不读写 viltrox_fit_score、不碰 rule_v0。
  - refresh_contactability 只写 214 迁移的 4 列 + updated_at,列缺失诚实降级不炸。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlsplit

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

SCORING_METHOD = "contact_clue_channels_verified_recency_v2"

# 渠道权重(总权重刻意 email 独大:邮箱是唯一可直接发商务合作邀约的渠道)。
CHANNEL_WEIGHTS: dict[str, float] = {
    "email": 55.0,
    "phone": 15.0,
    "whatsapp_link": 14.0,
    "telegram_link": 14.0,
    "link_hub": 12.0,   # Linktree 类聚合页,常列全套联系方式
    "website": 10.0,
    "linkedin_link": 6.0,
    "discord_link": 5.0,
    "instagram_link": 5.0,
    "tiktok_link": 5.0,
    "youtube_link": 5.0,
    "twitter_link": 5.0,
    "facebook_link": 5.0,
    "twitch_link": 4.0,
    "pinterest_link": 4.0,
    "link": 4.0,
}
DEFAULT_CHANNEL_WEIGHT = 3.0
CHANNEL_POINTS_CAP = 85.0

# 来源可信度(行缺 confidence 时的 strength 兜底;与既有取证漏斗口径一致:
# 锚点行 0.9 / 裸 raw 0.55 / 全 raw 兜底 0.45)。
SOURCE_TRUST: dict[str, float] = {
    "manual": 1.0,
    "ig_business_profile": 0.95,
    "youtube_about_declared": 0.95,
    "bio_explicit_contact": 0.9,
    "website_declared": 0.85,
    "raw_bio_scan": 0.6,
    "video_caption": 0.6,
    "raw_scan": 0.55,
    "raw_full_scan": 0.45,
}
DEFAULT_SOURCE_TRUST = 0.5

# 新鲜度加成(天数上限, 加分),按 contact_last_verified_at 距今天数取第一档命中。
RECENCY_BONUS: tuple[tuple[int, float], ...] = ((30, 15.0), (90, 10.0), (365, 5.0))
RECENCY_BONUS_STALE = 2.0  # 有时间但超过一年:聊胜于无

_VALUE_FREE_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
_VALUE_FREE_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
_VALUE_FREE_MASKED_EMAIL_RE = re.compile(r"(?<!\w)[^\s@]*\*{2,}[^\s@]*@[^\s]+")
_VALUE_FREE_MASKED_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+|\d)\*{2,}[A-Za-z0-9]?(?!\w)"
)
_VALUE_FREE_TEXT_FIELDS = frozenset(
    {
        "bio",
        "biography",
        "about",
        "description",
        "caption",
        "title",
        "summary",
        "reason",
        "text",
        "content",
        "body",
        "narrative",
        "transcript",
        "comment",
        "comments",
    }
)
_VALUE_FREE_DROP_KEYS = frozenset(
    {
        "email",
        "emails",
        "phone",
        "phones",
        "phone_number",
        "mobile",
        "whatsapp",
        "telegram",
        "other_contacts",
        "other_contacts_json",
        "contacts",
        "contact_links_json",
        "contact_raw_json",
        "contact_channels",
        "contact_sources",
        "website",
        "website_url",
        "link_hub",
        "normalized_value",
        "evidence_text",
        "raw_platform_data",
    }
)
_VALUE_FREE_SAFE_CONTACT_KEYS = frozenset(
    {"contact_summary", "contact_masked", "contact_projection_reason"}
)
_EXTERNAL_CONTACT_URI_RE = re.compile(
    r"(?i)(?:mailto:|tel:|whatsapp:)[^\s<>'\"]+|"
    r"https?://(?:www\.)?"
    r"(?:wa\.me|api\.whatsapp\.com|t\.me|telegram\.me|m\.me|discord\.gg|discord\.com/invite)"
    r"/[^\s<>'\"]*"
)
_EXTERNAL_HTTP_URL_RE = re.compile(r"(?i)https?://[^\s<>'\"]+")
_EXTERNAL_WHATSAPP_LABEL_RE = re.compile(
    r"(?i)\b(?:whatsapp|wa)\s*[:：]\s*[^\s,;]+"
)
_EXTERNAL_DM_HANDLE_RE = re.compile(
    r"(?i)\b(?:"
    r"(?:instagram|tiktok|x|twitter)\s+(?:dm|contact)|"
    r"(?:telegram|messenger|discord)(?:\s+(?:dm|contact))?|"
    r"(?:dm|contact(?:\s+me)?)"
    r")\s*[:：]?\s*@[-\w.]+"
)
_EXTERNAL_LABELED_CONTACT_RE = re.compile(
    r"(?i)\b(?:telegram|messenger|discord)\s*[:：]\s*[^\s,;]+"
)
_EXTERNAL_CONTACT_MARKER = "[contact removed]"
_SUMMARY_COUNT_KEYS = frozenset(
    {
        "known_contact_count",
        "verified_contact_count",
        "channel_count",
        "verified_channel_count",
    }
)
_SUMMARY_BOOL_KEYS = frozenset(
    {"has_contact", "contact_masked", "reveal_required"}
)
_SUMMARY_TOKEN_KEYS = frozenset({"status", "actionability", "reason"})
_SUMMARY_CHANNEL_KEYS = frozenset({"channel_types", "verified_channel_types"})
_SUMMARY_ALLOWED_CHANNELS = frozenset(CHANNEL_WEIGHTS) | frozenset(
    {
        "phone",
        "whatsapp",
        "instagram_dm",
        "tiktok_dm",
        "x_dm",
        "facebook_dm",
        "telegram_dm",
    }
)
_SAFE_SUMMARY_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_SAFE_SUMMARY_TIME_RE = re.compile(r"^[0-9T:Z+.-]{10,40}$")

_CONTACT_ROUTE_HOSTS = frozenset(
    {
        "wa.me",
        "api.whatsapp.com",
        "chat.whatsapp.com",
        "whatsapp.com",
        "t.me",
        "telegram.me",
        "telegram.dog",
        "m.me",
        "messenger.com",
        "discord.gg",
    }
)
_CONTACT_ROUTE_SEGMENT_RE = re.compile(
    r"(?i)^(?:contact(?:[-_](?:us|me))?|call|chat|dm|direct|"
    r"message(?:s)?|compose|invite|send|users)$"
)
_PLATFORM_HOST_SUFFIXES = frozenset(
    {
        "youtube.com",
        "instagram.com",
        "tiktok.com",
        "x.com",
        "twitter.com",
        "facebook.com",
        "linkedin.com",
        "twitch.tv",
        "pinterest.com",
        "threads.net",
    }
)


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _contains_contact_phone(value: str) -> bool:
    # URL paths/queries are identity locators, not prose.  Treat every
    # standalone 8-15 digit candidate as contact data here, including local
    # 8/9-digit phone formats that the prose sanitizer deliberately leaves
    # alone to avoid erasing ordinary metrics.
    for match in _VALUE_FREE_PHONE_RE.finditer(value):
        digits = re.sub(r"\D", "", match.group(0))
        if 8 <= len(digits) <= 15:
            return True
    return False


def _bounded_unquote(value: str) -> str:
    """Decode nested URL escapes without allowing unbounded expansion."""

    current = str(value or "")
    for _ in range(3):
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded
    return current


def is_contact_route_url(value: Any) -> bool:
    """Return true when a URL is a contact/DM route rather than identity page.

    This predicate intentionally checks the original path *and query* before
    URL normalization can discard them.  It is shared by legacy profile DTOs
    and provider-prompt sanitization so a contact URL cannot move between the
    two boundaries under a generic ``website`` label.
    """

    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").rstrip(".").casefold()
    except (TypeError, ValueError):
        return True
    if scheme in {"mailto", "tel", "whatsapp"}:
        return True
    if scheme not in {"http", "https"} or not host:
        return True
    if any(_host_matches(host, domain) for domain in _CONTACT_ROUTE_HOSTS):
        return True

    decoded_path = _bounded_unquote(parsed.path or "")
    decoded_query = _bounded_unquote(parsed.query or "")
    decoded_fragment = _bounded_unquote(parsed.fragment or "")
    route_text = f"{decoded_path}?{decoded_query}#{decoded_fragment}"
    if _VALUE_FREE_EMAIL_RE.search(route_text) or _contains_contact_phone(route_text):
        return True
    route_tokens = [
        token
        for token in re.split(r"[/&=?#;]+", route_text)
        if token
    ]
    if _host_matches(host, "discord.com") and any(
        token.casefold() in {"channels", "invite", "users"} for token in route_tokens
    ):
        return True
    return any(_CONTACT_ROUTE_SEGMENT_RE.fullmatch(token) for token in route_tokens)


def _is_platform_identity_page(normalized_url: str) -> bool:
    parsed = urlsplit(normalized_url)
    host = (parsed.hostname or "").rstrip(".").casefold()
    segments = [_bounded_unquote(part) for part in (parsed.path or "").split("/") if part]
    matched = next(
        (domain for domain in _PLATFORM_HOST_SUFFIXES if _host_matches(host, domain)),
        "",
    )
    if not matched:
        # A creator-owned/public website remains useful identity evidence once
        # contact routes and inline values have been rejected above.
        return True
    if matched == "youtube.com":
        return bool(
            (segments and segments[0].startswith("@") and len(segments[0]) > 1)
            or (len(segments) >= 2 and segments[0].casefold() in {"channel", "c", "user"})
        )
    if matched == "instagram.com":
        return len(segments) == 1 and segments[0].casefold() not in {
            "accounts",
            "direct",
            "explore",
            "p",
            "reel",
            "reels",
            "stories",
        }
    if matched == "tiktok.com":
        return len(segments) == 1 and segments[0].startswith("@") and len(segments[0]) > 1
    if matched in {"x.com", "twitter.com"}:
        return len(segments) == 1 and segments[0].casefold() not in {
            "compose",
            "home",
            "i",
            "intent",
            "messages",
            "search",
            "share",
        }
    if matched == "facebook.com":
        return len(segments) == 1 and segments[0].casefold() not in {
            "dialog",
            "groups",
            "login",
            "messages",
            "profile.php",
            "share",
        }
    if matched == "linkedin.com":
        return len(segments) >= 2 and segments[0].casefold() in {
            "company",
            "in",
            "school",
            "showcase",
        }
    if matched == "threads.net":
        return len(segments) == 1 and segments[0].startswith("@") and len(segments[0]) > 1
    return len(segments) == 1


def project_public_profile_url(value: Any) -> str:
    """Normalize a public creator identity page or fail closed to empty."""

    if is_contact_route_url(value):
        return ""
    try:
        from app.domains.kol.contact_ingest import normalize_contact

        normalized = normalize_contact("website", value).normalized_value
    except Exception:
        return ""
    if is_contact_route_url(normalized) or not _is_platform_identity_page(normalized):
        return ""
    return normalized


def _redact_phone_candidates(text: str, *, marker: str) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        compact = token.strip()
        # Do not turn common ISO dates into contact claims.  A phone candidate
        # otherwise needs 8-15 digits and either an international prefix,
        # punctuation/spacing, or at least ten contiguous digits.
        if re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", compact):
            return token
        digits = re.sub(r"\D", "", compact)
        if not 8 <= len(digits) <= 15:
            return token
        if compact.startswith("+") or re.search(r"[\s().-]", compact) or len(digits) >= 10:
            return marker
        return token

    return _VALUE_FREE_PHONE_RE.sub(replace, text)


def _neutralize_inline_contacts(value: Any, *, redact_phone: bool) -> Any:
    if not isinstance(value, str) or not value:
        return value
    text = _VALUE_FREE_EMAIL_RE.sub("[contact hidden]", value)
    text = _VALUE_FREE_MASKED_EMAIL_RE.sub("[contact hidden]", text)
    text = _VALUE_FREE_MASKED_PHONE_RE.sub("[contact hidden]", text)
    if redact_phone:
        text = _redact_phone_candidates(text, marker="[contact hidden]")
    return text


def _is_contact_value_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower()
    if normalized in _VALUE_FREE_SAFE_CONTACT_KEYS:
        return False
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    if normalized in _VALUE_FREE_DROP_KEYS:
        return True
    if compact in {
        "email",
        "emails",
        "phone",
        "phones",
        "phonenumber",
        "mobile",
        "whatsapp",
        "telegram",
        "othercontacts",
        "contacts",
        "contactlinks",
        "contactraw",
        "contactchannels",
        "contactsources",
        "website",
        "websiteurl",
        "linkhub",
        "rawplatformdata",
    }:
        return True
    if normalized.startswith("contact_"):
        return True
    # Legacy/import schemas use both snake_case and camelCase.  All contact*
    # aliases are value-bearing unless they are one of the explicit safe DTO
    # keys above; publicEmail/businessEmail/managerPhone are covered too.
    if compact.startswith("contact"):
        return True
    if "email" in compact or "phone" in compact or "whatsapp" in compact:
        return True
    return normalized.endswith(("_contact", "_contacts"))


def _value_free_contact_summary(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    summary: dict[str, Any] = {}
    for key in _SUMMARY_COUNT_KEYS:
        candidate = raw.get(key)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
            summary[key] = candidate
    for key in _SUMMARY_BOOL_KEYS:
        if isinstance(raw.get(key), bool):
            summary[key] = raw[key]
    for key in _SUMMARY_TOKEN_KEYS:
        candidate = str(raw.get(key) or "").strip().lower()
        if candidate and _SAFE_SUMMARY_TOKEN_RE.fullmatch(candidate):
            summary[key] = candidate
    for key in _SUMMARY_CHANNEL_KEYS:
        candidate = raw.get(key)
        if isinstance(candidate, list):
            summary[key] = sorted(
                {
                    token
                    for item in candidate
                    if (token := str(item or "").strip().lower())
                    and token in _SUMMARY_ALLOWED_CHANNELS
                }
            )
    timestamp = raw.get("last_verified_at")
    if timestamp is None:
        summary["last_verified_at"] = None
    elif isinstance(timestamp, str) and _SAFE_SUMMARY_TIME_RE.fullmatch(timestamp.strip()):
        summary["last_verified_at"] = timestamp.strip()
    purposes = raw.get("allowed_reveal_purposes")
    if isinstance(purposes, list):
        summary["allowed_reveal_purposes"] = [
            purpose
            for purpose in (str(item or "").strip() for item in purposes)
            if purpose in {"kol_detail_view", "compose_outreach"}
        ]
    return summary


def value_free_contact_projection(payload: Any) -> Any:
    """Recursively remove contact values from ordinary pool/detail payloads.

    ``contact_summary`` is the sole preserved contact subtree and contains only
    counts, channel names and lifecycle timestamps.  Contact records with
    generic ``value`` fields, legacy aliases, raw metadata and inline masked
    fragments are removed or replaced by a neutral marker.
    """

    if isinstance(payload, list):
        return [value_free_contact_projection(item) for item in payload]
    if not isinstance(payload, dict):
        return _neutralize_inline_contacts(payload, redact_phone=False)

    compact_keys = {
        re.sub(r"[^a-z0-9]", "", str(key).strip().lower()) for key in payload
    }
    record_kind = str(
        payload.get("contact_type")
        or payload.get("contactType")
        or payload.get("type")
        or payload.get("kind")
        or ""
    ).casefold()
    contact_record = bool(
        {"contacttype", "channel", "contactvalue", "normalizedvalue"}.intersection(
            compact_keys
        )
        or any(token in record_kind for token in ("email", "phone", "whatsapp", "contact"))
    )
    projected: dict[str, Any] = {}
    for raw_key, raw_value in payload.items():
        key = str(raw_key)
        normalized = key.strip().lower()
        if normalized == "contact_summary":
            projected[key] = _value_free_contact_summary(raw_value)
            continue
        if re.sub(r"[^a-z0-9]", "", normalized) in {"profileurl", "channelurl"}:
            projected[key] = project_public_profile_url(raw_value)
            continue
        if _is_contact_value_key(normalized):
            continue
        if contact_record and normalized in {
            "value",
            "display_value",
            "source_url",
            "evidence",
            "raw_value",
        }:
            continue
        nested = value_free_contact_projection(raw_value)
        if isinstance(nested, str):
            nested = _neutralize_inline_contacts(
                nested,
                redact_phone=normalized in _VALUE_FREE_TEXT_FIELDS,
            )
        projected[key] = nested
    return projected


def _sanitize_external_contact_text(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value

    def replace_url(match: re.Match[str]) -> str:
        token = match.group(0)
        core = token.rstrip(".,;:!?)]}")
        suffix = token[len(core) :]
        if is_contact_route_url(core):
            return _EXTERNAL_CONTACT_MARKER + suffix
        return token

    text = _EXTERNAL_HTTP_URL_RE.sub(replace_url, value)
    text = _EXTERNAL_CONTACT_URI_RE.sub(_EXTERNAL_CONTACT_MARKER, text)
    text = _EXTERNAL_WHATSAPP_LABEL_RE.sub(_EXTERNAL_CONTACT_MARKER, text)
    text = _EXTERNAL_DM_HANDLE_RE.sub(_EXTERNAL_CONTACT_MARKER, text)
    text = _EXTERNAL_LABELED_CONTACT_RE.sub(_EXTERNAL_CONTACT_MARKER, text)
    text = _VALUE_FREE_EMAIL_RE.sub(_EXTERNAL_CONTACT_MARKER, text)
    text = _VALUE_FREE_MASKED_EMAIL_RE.sub(_EXTERNAL_CONTACT_MARKER, text)
    text = _VALUE_FREE_MASKED_PHONE_RE.sub(_EXTERNAL_CONTACT_MARKER, text)
    return _redact_phone_candidates(text, marker=_EXTERNAL_CONTACT_MARKER)


def sanitize_contact_values_for_external_processing(payload: Any) -> Any:
    """Remove contact values before any LLM/provider prompt boundary.

    Unlike the ordinary GET projection, this keeps non-contact raw profile
    context and ordinary public URLs so relevance/outreach features retain
    useful evidence.  Email/phone/WhatsApp/mailto/tel values, legacy aliases,
    nested contact records and masked fragments are removed.  The returned
    structure is safe to serialize into a provider request; the input is not
    mutated.
    """

    if isinstance(payload, list):
        return [sanitize_contact_values_for_external_processing(item) for item in payload]
    if not isinstance(payload, dict):
        return _sanitize_external_contact_text(payload)

    compact_keys = {
        re.sub(r"[^a-z0-9]", "", str(key).strip().lower()) for key in payload
    }
    record_kind = str(
        payload.get("contact_type")
        or payload.get("contactType")
        or payload.get("type")
        or payload.get("kind")
        or ""
    ).casefold()
    contact_record = bool(
        {"contacttype", "channel", "contactvalue", "normalizedvalue"}.intersection(
            compact_keys
        )
        or any(token in record_kind for token in ("email", "phone", "whatsapp", "contact"))
    )
    projected: dict[str, Any] = {}
    for raw_key, raw_value in payload.items():
        key = str(raw_key)
        normalized = key.strip().lower()
        # Provider prompts may retain sanitized raw text and ordinary profile/
        # website URLs.  These are removed only from user-facing GET DTOs.
        keep_container = normalized in {
            "raw_platform_data",
            "website",
            "website_url",
            "profile_url",
            "link_hub",
        }
        if _is_contact_value_key(normalized) and not keep_container:
            continue
        if contact_record and normalized in {
            "value",
            "display_value",
            "source_url",
            "evidence",
            "raw_value",
        }:
            continue
        projected[key] = sanitize_contact_values_for_external_processing(raw_value)
    return projected


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: Any) -> datetime | None:
    """宽容解析时间(compat 层可能回 str 或 datetime);解析不了诚实返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _normalize_channel(contact_type: Any) -> str:
    ctype = str(contact_type or "").strip().lower()
    if not ctype:
        return ""
    if "email" in ctype:  # email / business_email 归并同一渠道
        return "email"
    return ctype


def _channel_weight(channel: str) -> float:
    return float(CHANNEL_WEIGHTS.get(channel, DEFAULT_CHANNEL_WEIGHT))


def _row_strength(confidence: Any, source: str) -> float:
    """单条联系方式的强度:优先行内 confidence,缺失按来源可信度兜底,夹在 0-1。"""
    try:
        if confidence is not None and str(confidence) != "":
            return max(0.0, min(1.0, float(confidence)))
    except Exception:
        logger.debug("confidence 数值化失败,按来源可信度兜底(best-effort)", exc_info=True)
    return float(SOURCE_TRUST.get(source, DEFAULT_SOURCE_TRUST))


def _contact_rows(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT contact_type, contact_value, contact_source, confidence,
                   first_seen_at, last_seen_at, created_at,
                   verification_status, verified_at, invalidated_at, revoked_at
            FROM vkpi_kol_pool_contacts
            WHERE kol_pool_id = ?
            ORDER BY id
            """,
            (int(kol_pool_id),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # Rolling migration: legacy canonical rows remain observations only.
        # They may contribute to a clue score but can never become verified.
        try:
            rows = conn.execute(
                """
                SELECT contact_type, contact_value, contact_source, confidence,
                       first_seen_at, last_seen_at, created_at
                FROM vkpi_kol_pool_contacts
                WHERE kol_pool_id = ?
                ORDER BY id
                """,
                (int(kol_pool_id),),
            ).fetchall()
        except Exception as exc:
            logger.warning("vkpi_kol_pool_contacts read failed kol=%s: %s", kol_pool_id, type(exc).__name__)
            return []
        return [{**dict(r), "verification_status": "observed"} for r in rows]


def contactability(kol_pool_id: int, *, conn: Any | None = None) -> dict[str, Any]:
    """可联系性评估(纯读,不写库):0-100 分 + 建议触达渠道排序 + 来源汇总。

    KOL 不存在抛 LookupError(路由层转 404)。返回值中的联系方式全部脱敏。
    """
    db = conn or get_conn()
    row = db.execute(
        "SELECT id FROM vkpi_kol_pool WHERE id = ?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    rows = _contact_rows(db, int(kol_pool_id))

    channels: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    last_verified: datetime | None = None
    known_contact_count = 0
    verified_contact_count = 0
    for r in rows:
        status = str(r.get("verification_status") or "observed").strip().lower()
        if status in {"invalid", "revoked"} or r.get("invalidated_at") or r.get("revoked_at"):
            continue
        channel = _normalize_channel(r.get("contact_type"))
        if not channel:
            continue
        known_contact_count += 1
        is_verified = status == "verified_public_business" and bool(r.get("verified_at"))
        if is_verified:
            verified_contact_count += 1
        source = str(r.get("contact_source") or "").strip().lower() or "unknown"
        strength = _row_strength(r.get("confidence"), source)
        seen = _parse_ts(r.get("last_seen_at")) or _parse_ts(r.get("first_seen_at")) or _parse_ts(r.get("created_at"))
        verified_at = _parse_ts(r.get("verified_at")) if is_verified else None
        if verified_at and (last_verified is None or verified_at > last_verified):
            last_verified = verified_at

        entry = channels.get(channel)
        if entry is None:
            channels[channel] = {
                "confidence": round(strength, 2),
                "source": source,
                "count": 1,
                "verified_count": 1 if is_verified else 0,
                "last_seen_at": seen.strftime("%Y-%m-%dT%H:%M:%SZ") if seen else None,
            }
        else:
            entry["count"] = int(entry.get("count") or 0) + 1
            entry["verified_count"] = int(entry.get("verified_count") or 0) + (1 if is_verified else 0)
            if strength > float(entry.get("confidence") or 0.0):  # 保留该渠道最强一条作为代表
                entry["confidence"] = round(strength, 2)
                entry["source"] = source
            if seen:
                prev = _parse_ts(entry.get("last_seen_at"))
                if prev is None or seen > prev:
                    entry["last_seen_at"] = seen.strftime("%Y-%m-%dT%H:%M:%SZ")

        src = sources.setdefault(source, {"source": source, "count": 0, "max_confidence": 0.0})
        src["count"] = int(src["count"]) + 1
        src["max_confidence"] = round(max(float(src["max_confidence"]), strength), 2)

    channel_points = 0.0
    ranked: list[dict[str, Any]] = []
    for channel, entry in channels.items():
        weight = _channel_weight(channel)
        strength = float(entry.get("confidence") or 0.0)
        priority = round(weight * strength, 2)
        channel_points += priority
        ranked.append({
            "channel": channel,
            "priority": priority,
            "reason": f"weight {weight:g} x confidence {strength:g} (source={entry.get('source')})",
        })
    ranked.sort(key=lambda item: (-float(item["priority"]), str(item["channel"])))
    channel_points = min(channel_points, CHANNEL_POINTS_CAP)

    recency_bonus = 0.0
    if channels and last_verified is not None:
        age_days = max(0.0, (datetime.now(timezone.utc) - last_verified).total_seconds() / 86400.0)
        recency_bonus = RECENCY_BONUS_STALE
        for max_days, bonus in RECENCY_BONUS:
            if age_days <= max_days:
                recency_bonus = bonus
                break

    score = round(min(100.0, channel_points + recency_bonus), 1) if channels else 0.0
    result: dict[str, Any] = {
        "status": "ready",
        "kol_pool_id": int(kol_pool_id),
        "score": score,
        "score_kind": "contact_clue_score",
        "scoring_method": SCORING_METHOD,
        "channels": channels,
        "recommended_channels": ranked,
        "sources": sorted(sources.values(), key=lambda s: (-float(s["max_confidence"]), -int(s["count"]))),
        "last_verified_at": last_verified.strftime("%Y-%m-%dT%H:%M:%SZ") if last_verified else None,
        "known_contact_count": known_contact_count,
        "verified_contact_count": verified_contact_count,
        "actionability": "requires_reveal" if verified_contact_count else "not_verified",
        "breakdown": {
            "channel_points": round(channel_points, 2),
            "recency_bonus": recency_bonus,
            "channel_count": len(channels),
        },
        "contact_masked": True,  # 全脱敏;真值走 contact_reveal 二次确认门控
    }
    if not channels:
        result["reason"] = "no_contact_channels"
    return result


def contact_summary(kol_pool_id: int, *, conn: Any | None = None) -> dict[str, Any]:
    """Return a value-free contact projection for ordinary KOL reads.

    This summary deliberately does not reuse :func:`contactability`: that legacy
    score includes observed/legacy rows and ``last_seen_at`` and therefore
    cannot prove that a contact is verified or usable.  Ordinary item/detail
    reads expose counts and channel names only.  Even a verified row remains
    ``requires_reveal`` because organization-scoped suppression is evaluated
    only inside the explicit POST reveal boundary.

    Two reveal tiers are reported, mirroring ``contact_reveal``: ``verified``
    (public-business verified) and ``observed`` (pipeline scan / declaration,
    not yet verified).  ``reason`` is one of ``verified_available`` /
    ``observed_available`` / ``verification_required`` / ``no_contacts``.
    """
    from app.domains.kol.contact_suppression import observed_source_eligible

    db = conn or get_conn()
    schema_current = True
    try:
        rows = db.execute(
            """
            SELECT id, COALESCE(NULLIF(channel, ''), contact_type) AS channel,
                   contact_source, verification_status, verified_at,
                   invalidated_at, revoked_at
            FROM vkpi_kol_pool_contacts
            WHERE kol_pool_id=? AND COALESCE(contact_value, '') <> ''
            ORDER BY id
            """,
            (int(kol_pool_id),),
        ).fetchall()
    except Exception:
        # Rolling-migration compatibility: legacy rows are known observations,
        # never verified facts.  The selected projection is still value-free.
        schema_current = False
        try:
            rows = db.execute(
                """
                SELECT id, contact_type AS channel
                FROM vkpi_kol_pool_contacts
                WHERE kol_pool_id=? AND COALESCE(contact_value, '') <> ''
                ORDER BY id
                """,
                (int(kol_pool_id),),
            ).fetchall()
        except Exception:
            return {
                "status": "unknown",
                "has_contact": False,
                "contact_masked": True,
                "known_contact_count": 0,
                "verified_contact_count": 0,
                "channel_count": 0,
                "channel_types": [],
                "verified_channel_count": 0,
                "verified_channel_types": [],
                "observed_contact_count": 0,
                "observed_channel_count": 0,
                "observed_channel_types": [],
                "reveal_tier": None,
                "last_verified_at": None,
                "actionability": "unavailable",
                "reveal_required": False,
                "allowed_reveal_purposes": ["kol_detail_view", "compose_outreach"],
                "reason": "contact_store_unavailable",
            }

    known_rows: list[dict[str, Any]] = []
    verified_rows: list[dict[str, Any]] = []
    observed_rows: list[dict[str, Any]] = []
    for raw_row in rows:
        row = dict(raw_row)
        status = str(row.get("verification_status") or "observed").strip().lower()
        if status in {"invalid", "revoked"} or row.get("invalidated_at") or row.get("revoked_at"):
            continue
        known_rows.append(row)
        if not schema_current:
            continue
        if status == "verified_public_business" and row.get("verified_at"):
            verified_rows.append(row)
        elif status == "observed" and observed_source_eligible(row.get("contact_source")):
            observed_rows.append(row)

    channel_types = sorted(
        {
            str(row.get("channel") or "").strip().lower()
            for row in known_rows
            if str(row.get("channel") or "").strip()
        }
    )
    verified_channel_types = sorted(
        {
            str(row.get("channel") or "").strip().lower()
            for row in verified_rows
            if str(row.get("channel") or "").strip()
        }
    )
    observed_channel_types = sorted(
        {
            str(row.get("channel") or "").strip().lower()
            for row in observed_rows
            if str(row.get("channel") or "").strip()
        }
    )
    verified_times = [row.get("verified_at") for row in verified_rows if row.get("verified_at")]
    last_verified = max(verified_times, key=lambda value: str(value)) if verified_times else None
    if isinstance(last_verified, datetime):
        if last_verified.tzinfo is None:
            last_verified = last_verified.replace(tzinfo=timezone.utc)
        last_verified = last_verified.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif last_verified is not None:
        last_verified = str(last_verified)

    has_contact = bool(known_rows)
    if verified_rows:
        reveal_tier: str | None = "verified"
        reason = "verified_available"
    elif observed_rows:
        reveal_tier = "observed"
        reason = "observed_available"
    else:
        reveal_tier = None
        reason = "verification_required" if has_contact else "no_contacts"
    return {
        "status": "known" if has_contact else "empty",
        "has_contact": has_contact,
        "contact_masked": True,
        "known_contact_count": len(known_rows),
        "verified_contact_count": len(verified_rows),
        "channel_count": len(channel_types),
        "channel_types": channel_types,
        "verified_channel_count": len(verified_channel_types),
        "verified_channel_types": verified_channel_types,
        "observed_contact_count": len(observed_rows),
        "observed_channel_count": len(observed_channel_types),
        "observed_channel_types": observed_channel_types,
        "reveal_tier": reveal_tier,
        "last_verified_at": last_verified,
        "actionability": "requires_reveal" if reveal_tier else "not_verified",
        "reveal_required": has_contact,
        "allowed_reveal_purposes": ["kol_detail_view", "compose_outreach"],
        "reason": reason,
    }


REFRESH_COLUMNS = ("contact_channels", "contact_last_verified_at", "contactability_score", "contact_sources")


def refresh_contactability(kol_pool_id: int, *, conn: Any | None = None) -> dict[str, Any]:
    """重算并写回 214 迁移 4 列(contactability_score / contact_channels /
    contact_sources / contact_last_verified_at)+ updated_at。

    单点写入口:回填脚本与刷新端点都走这里。列缺失(未跑迁移 214)诚实降级不炸。
    红线:只写上述列,绝不触 viltrox_fit_score / rule_v0。
    """
    from app.domains.kol.pool_common import _clear_kol_pool_read_cache, _table_columns

    db = conn or get_conn()
    pool_cols = _table_columns(db, "vkpi_kol_pool")
    missing = [col for col in REFRESH_COLUMNS if col not in pool_cols]
    if missing:
        return {
            "status": "columns_missing_run_migration_214",
            "kol_pool_id": int(kol_pool_id),
            "missing_columns": missing,
            "written": False,
        }

    snapshot = contactability(int(kol_pool_id), conn=db)
    db.execute(
        """
        UPDATE vkpi_kol_pool
        SET contact_channels = ?::jsonb,
            contact_sources = ?::jsonb,
            contactability_score = ?,
            contact_last_verified_at = ?::timestamptz,
            updated_at = ?
        WHERE id = ?
        """,
        (
            json.dumps(snapshot.get("channels") or {}, ensure_ascii=False),
            json.dumps(snapshot.get("sources") or [], ensure_ascii=False),
            float(snapshot.get("score") or 0.0),
            snapshot.get("last_verified_at"),
            _utcnow(),
            int(kol_pool_id),
        ),
    )
    db.commit()
    try:
        _clear_kol_pool_read_cache()
    except Exception:
        logger.warning("kol pool cache clear failed after contactability refresh", exc_info=True)
    return {**snapshot, "status": "refreshed", "written": True}
