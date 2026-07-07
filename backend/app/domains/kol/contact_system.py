"""B1 联系方式 L0 · 可联系性打分(纯规则、零 LLM、零网络、零成本)。

数据源(全是已落库真数据,不发任何外部请求):
  - vkpi_kol_pool_contacts 审计表(contact_type/contact_source/confidence/first·last_seen)——canonical;
  - vkpi_kol_pool.email 兜底(历史行可能只有主表 email、无审计行)。

打分口径 scoring_method = "channels_recency_source_v1":
  - 渠道分:每个去重渠道贡献 weight x strength;strength = 该渠道最高置信度
    (行无 confidence 时按来源可信度 SOURCE_TRUST 兜底);渠道分合计封顶 85。
  - 新鲜度加成:最近验证 30 天内 +15 / 90 天内 +10 / 365 天内 +5 / 更久 +2 / 无时间 +0。
  - 总分 0-100;无任何渠道诚实 0 分(reason="no_contact_channels")。

合规红线:
  - 返回值只带脱敏联系方式(pool_common._mask_contact_value 同口径),明文绝不出本模块;
    真值仍走既有 contact_reveal.view_kol_contact 二次确认 + 审计门控。
  - contactability_score 是触达运营分,不是契合分:绝不读写 viltrox_fit_score、不碰 rule_v0。
  - refresh_contactability 只写 214 迁移的 4 列 + updated_at,列缺失诚实降级不炸。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

SCORING_METHOD = "channels_recency_source_v1"

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
                   first_seen_at, last_seen_at, created_at
            FROM vkpi_kol_pool_contacts
            WHERE kol_pool_id = ?
            ORDER BY id
            """,
            (int(kol_pool_id),),
        ).fetchall()
    except Exception as exc:  # 表缺失等:诚实降级为「无审计行」,主表 email 兜底仍生效
        logger.warning("vkpi_kol_pool_contacts read failed kol=%s: %s", kol_pool_id, exc)
        return []
    return [dict(r) for r in rows]


def contactability(kol_pool_id: int, *, conn: Any | None = None) -> dict[str, Any]:
    """可联系性评估(纯读,不写库):0-100 分 + 建议触达渠道排序 + 来源汇总。

    KOL 不存在抛 LookupError(路由层转 404)。返回值中的联系方式全部脱敏。
    """
    from app.domains.kol.pool_common import _mask_contact_value

    db = conn or get_conn()
    row = db.execute(
        "SELECT id, email, contact_source, contact_first_seen_at FROM vkpi_kol_pool WHERE id = ?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    pool = dict(row)

    rows = _contact_rows(db, int(kol_pool_id))
    # 主表 email 兜底:历史行可能只有 email 列、无审计行(来源按主表 contact_source 记,缺省 pool_email)。
    pool_email = str(pool.get("email") or "").strip()
    if pool_email and not any(_normalize_channel(r.get("contact_type")) == "email" for r in rows):
        rows.append({
            "contact_type": "email",
            "contact_value": pool_email,
            "contact_source": str(pool.get("contact_source") or "").strip() or "pool_email",
            "confidence": None,
            "first_seen_at": pool.get("contact_first_seen_at"),
            "last_seen_at": pool.get("contact_first_seen_at"),
            "created_at": None,
        })

    channels: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    last_verified: datetime | None = None
    for r in rows:
        channel = _normalize_channel(r.get("contact_type"))
        if not channel:
            continue
        source = str(r.get("contact_source") or "").strip().lower() or "unknown"
        strength = _row_strength(r.get("confidence"), source)
        seen = _parse_ts(r.get("last_seen_at")) or _parse_ts(r.get("first_seen_at")) or _parse_ts(r.get("created_at"))
        if seen and (last_verified is None or seen > last_verified):
            last_verified = seen

        entry = channels.get(channel)
        if entry is None:
            channels[channel] = {
                "masked_value": _mask_contact_value(r.get("contact_type"), r.get("contact_value")),
                "confidence": round(strength, 2),
                "source": source,
                "count": 1,
                "last_seen_at": seen.strftime("%Y-%m-%dT%H:%M:%SZ") if seen else None,
            }
        else:
            entry["count"] = int(entry.get("count") or 0) + 1
            if strength > float(entry.get("confidence") or 0.0):  # 保留该渠道最强一条作为代表
                entry["confidence"] = round(strength, 2)
                entry["source"] = source
                entry["masked_value"] = _mask_contact_value(r.get("contact_type"), r.get("contact_value"))
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
            "masked_value": entry.get("masked_value"),
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
        "scoring_method": SCORING_METHOD,
        "channels": channels,
        "recommended_channels": ranked,
        "sources": sorted(sources.values(), key=lambda s: (-float(s["max_confidence"]), -int(s["count"]))),
        "last_verified_at": last_verified.strftime("%Y-%m-%dT%H:%M:%SZ") if last_verified else None,
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
