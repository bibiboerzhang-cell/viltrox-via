"""Safe KOL Pool video-evidence writer for URL-driven flows.

This service is the reusable boundary for turning a video URL into one
``vkpi_kol_video_evidence`` row. It is deliberately isolated from V6 Fit:
callers can create/reuse evidence without touching KOL scoring fields.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.db.connection import get_conn
from app.domains.kol.pool_common import _table_columns
from app.domains.kol.video_evidence_sources import (
    SOURCE_MANUAL_URL,
    SOURCE_URL_METADATA,
    validate_source_value,
)
from app.domains.projects.workflow_evidence import _fetch_video_metadata

from app.core.logging import get_logger

logger = get_logger(__name__)

VIDEO_EVIDENCE_METHOD = "kol_video_evidence_url_service_v1"
SCORE_FIELDS = ("viltrox_fit_score", "viltrox_fit_reason")


def find_video_evidence_by_url(
    video_url: str,
    *,
    conn: Any | None = None,
) -> dict[str, Any] | None:
    """Return existing evidence for one native video identity without providers.

    URL search uses this read-only lookup before scheduling a background
    refresh.  It deliberately shares the writer's native-ID dedupe contract so
    ``youtu.be/<id>?...`` resolves an existing ``youtube.com/watch?v=<id>``
    row instead of presenting the creator as unknown.
    """

    value = _text(video_url)
    if not re.match(r"^https?://", value, flags=re.I):
        return None
    db = conn or get_conn()
    return _load_existing_evidence(db, value, kol_pool_id=0)


def ensure_video_evidence_from_url(
    kol_pool_id: int,
    source_url: str,
    metadata: dict[str, Any] | None = None,
    *,
    dry_run: bool = True,
    conn: Any | None = None,
    method: str = VIDEO_EVIDENCE_METHOD,
) -> dict[str, Any]:
    """Create or reuse video evidence for one KOL without touching V6 Fit."""
    if not kol_pool_id:
        raise ValueError("kol_pool_id is required")
    video_url = _text(source_url)
    if not re.match(r"^https?://", video_url, flags=re.I):
        raise ValueError("valid source_url is required")

    db = conn or get_conn()
    kol = _load_kol(db, int(kol_pool_id))
    if not kol:
        raise LookupError(f"kol_pool_id not found: {kol_pool_id}")

    columns = _table_columns(db, "vkpi_kol_video_evidence")
    if not columns:
        raise RuntimeError("vkpi_kol_video_evidence schema unavailable")

    evidence = _load_existing_evidence(db, video_url, kol_pool_id=int(kol_pool_id))
    if evidence and int(evidence.get("kol_pool_id") or 0) != int(kol_pool_id):
        return {
            "ok": False,
            "dry_run": dry_run,
            "status": "conflict_existing_other_kol",
            "kol_pool_id": int(kol_pool_id),
            "existing_evidence_id": int(evidence["id"]),
            "existing_kol_pool_id": int(evidence["kol_pool_id"]),
            "source_url": video_url,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
            "method": method,
        }

    resolved_metadata = dict(metadata or _fetch_video_metadata(video_url))
    now = _utcnow()
    values = _evidence_values(
        int(kol_pool_id),
        video_url,
        resolved_metadata,
        columns=columns,
        now=now,
        method=method,
    )
    # 兜底:写入用的 content_url 是 metadata 规范化后的 canonical,可能与传入的 raw source_url 不同
    # (典型:TikTok 链接带 ?is_from_webapp=/sender_device= 等查询串;按 raw 查不到、但 canonical 已存在)。
    # content_url 是全局唯一约束,不按 canonical 兜底查一次就会 INSERT 撞
    # vkpi_kol_video_evidence_content_url_key -> 整条 deep_crawl run 崩、前端啥都不出。
    if not evidence:
        canonical_url = _text(values.get("content_url"))
        if canonical_url and canonical_url != video_url:
            existing_canonical = _load_existing_evidence(db, canonical_url, kol_pool_id=int(kol_pool_id))
            if existing_canonical:
                if int(existing_canonical.get("kol_pool_id") or 0) != int(kol_pool_id):
                    return {
                        "ok": False,
                        "dry_run": dry_run,
                        "status": "conflict_existing_other_kol",
                        "kol_pool_id": int(kol_pool_id),
                        "existing_evidence_id": int(existing_canonical["id"]),
                        "existing_kol_pool_id": int(existing_canonical["kol_pool_id"]),
                        "source_url": video_url,
                        "viltrox_fit_score_changed_ids": [],
                        "viltrox_fit_score_untouched": True,
                        "method": method,
                    }
                evidence = existing_canonical
    operation = "reuse_update" if evidence else "insert"
    planned_values = _actual_write_values(values, operation=operation)
    before_scores = _score_snapshot(db, [int(kol_pool_id)])

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "status": "would_reuse" if evidence else "would_create",
            "operation": operation,
            "kol_pool_id": int(kol_pool_id),
            "evidence_id": int(evidence["id"]) if evidence else None,
            "source_url": video_url,
            "fields_to_write": sorted(planned_values),
            "metadata": _public_metadata(resolved_metadata),
            "score_before": before_scores,
            "score_after": before_scores,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
            "method": method,
        }

    try:
        if evidence:
            evidence_id = int(evidence["id"])
            _update_evidence(db, evidence_id, values)
            status = "reused"
        else:
            evidence_id = _insert_evidence(db, values)
            status = "created"

        after_scores = _score_snapshot(db, [int(kol_pool_id)])
        changed_ids = _changed_score_ids(before_scores, after_scores)
        if changed_ids:
            _rollback(db)
            raise RuntimeError(f"viltrox_fit_score changed unexpectedly: {changed_ids}")

        _commit(db)
        return {
            "ok": True,
            "dry_run": False,
            "status": status,
            "operation": operation,
            "kol_pool_id": int(kol_pool_id),
            "evidence_id": evidence_id,
            "source_url": video_url,
            "fields_written": sorted(planned_values),
            "metadata": _public_metadata(resolved_metadata),
            "score_before": before_scores,
            "score_after": after_scores,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
            "method": method,
        }
    except Exception:
        _rollback(db)
        raise


def _evidence_values(
    kol_pool_id: int,
    video_url: str,
    metadata: dict[str, Any],
    *,
    columns: set[str],
    now: str,
    method: str,
) -> dict[str, Any]:
    title = _text(metadata.get("title")) or video_url
    source = validate_source_value("source", SOURCE_MANUAL_URL, max_len=20)
    scrape_source = validate_source_value(
        "scrape_source",
        _text(metadata.get("scrape_source")) or SOURCE_URL_METADATA,
    )
    metrics_source = validate_source_value("metrics_source", scrape_source)
    # C6 零新抓提列:媒体种类细分列 video / image / carousel(migration 208)。
    # metadata.media_kind 语义保持 video/image 二值不动(evidence_type 分流继续照旧);
    # 本列在 image 基础上按多图 URL 数再细分出 carousel。metadata 无信号时留空串,
    # 由 _should_write_value 过滤不写 -> 列保持 NULL,交给回填脚本兜底。
    media_kind = _text(metadata.get("media_kind")).lower()
    image_urls_meta = metadata.get("image_urls") if isinstance(metadata.get("image_urls"), list) else []
    if media_kind == "image" and len(image_urls_meta) > 1:
        media_kind = "carousel"
    values = {
        "kol_pool_id": int(kol_pool_id),
        "content_url": _text(metadata.get("content_url")) or video_url,
        "platform": _text(metadata.get("platform")),
        "video_title": title,
        "title": title,
        "posted_at": metadata.get("posted_at"),
        "publish_date": metadata.get("publish_date"),
        "view_count": _int_or_none(metadata.get("view_count")),
        "like_count": _int_or_none(metadata.get("like_count")),
        "comment_count": _int_or_none(metadata.get("comment_count")),
        "share_count": _int_or_none(metadata.get("share_count")),
        # 识别分流:抓取层标 media_kind=image(IG 图文/轮播)→ 存成 image 证据,不进视频深析;
        # 缺省/video → 仍按 video。只动类型标签,不碰 viltrox_fit_score。
        "evidence_type": "image" if _text(metadata.get("media_kind")) == "image" else "video",
        # 细分媒体种类列(空串会被 _should_write_value 过滤;列未迁移时被 columns 过滤,双保险)。
        "media_kind": media_kind,
        # 轮播多图 URL 列表(TEXT 存 json 串;无则不写,由 _should_write_value 过滤)。供前端图片轮播展示。
        "image_urls": (json.dumps(metadata.get("image_urls"), ensure_ascii=False) if metadata.get("image_urls") else None),
        "source": source,
        "source_ref": f"url_video:{method}:{_digest(video_url)}",
        "confidence": "high",
        "is_active": True,
        "duration_seconds": _int_or_none(metadata.get("duration_seconds")),
        "thumbnail_url": _text(metadata.get("thumbnail_url")),
        "channel_id": _text(metadata.get("channel_id")),
        "channel_name": _text(metadata.get("channel_name")),
        "scrape_status": _text(metadata.get("scrape_status")) or "success",
        "scrape_source": scrape_source,
        "scraped_at": now,
        "metrics_scraped_at": now,
        "metrics_source": metrics_source,
        "scrape_error": _text(metadata.get("scrape_error")),
        "created_at": now,
        "updated_at": now,
    }
    return {key: value for key, value in values.items() if key in columns and _should_write_value(key, value)}


def _insert_evidence(conn: Any, values: dict[str, Any]) -> int:
    columns = [field for field in values]
    placeholders = ", ".join("?" for _ in columns)
    row = conn.execute(
        f"""
        INSERT INTO vkpi_kol_video_evidence ({', '.join(columns)})
        VALUES ({placeholders})
        RETURNING id
        """,
        tuple(values[field] for field in columns),
    ).fetchone()
    if row and row["id"] is not None:
        return int(row["id"])
    reloaded = _load_existing_evidence(conn, str(values.get("content_url") or ""), kol_pool_id=int(values.get("kol_pool_id") or 0))
    if not reloaded:
        raise RuntimeError("inserted video evidence row could not be reloaded")
    return int(reloaded["id"])


def _update_evidence(conn: Any, evidence_id: int, values: dict[str, Any]) -> None:
    update_values = _actual_write_values(values, operation="reuse_update")
    if not update_values:
        return
    assignments = ", ".join(f"{field}=?" for field in update_values)
    conn.execute(
        f"UPDATE vkpi_kol_video_evidence SET {assignments} WHERE id=?",
        tuple(update_values[field] for field in update_values) + (int(evidence_id),),
    )


def _load_kol(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT id, viltrox_fit_score, viltrox_fit_reason FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    return dict(row) if row else None


def _actual_write_values(values: dict[str, Any], *, operation: str) -> dict[str, Any]:
    if operation == "reuse_update":
        return {key: value for key, value in values.items() if key not in {"kol_pool_id", "content_url", "created_at"}}
    return values


def _load_existing_evidence(conn: Any, video_url: str, *, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_video_evidence
        WHERE content_url=?
        LIMIT 1
        """,
        (_text(video_url),),
    ).fetchone()
    if row:
        return dict(row)

    identity = _video_identity(video_url)
    if not identity:
        return None
    _platform, video_id = identity
    if not video_id:
        return None
    # 跨 KOL 视频身份判定:同一视频带追踪参数/不同 URL 形态(youtu.be↔watch?v=、TikTok
    # ?is_from_webapp= 等)被别的 KOL 认领时,精确 content_url 串比不上,必须按「平台+去追踪
    # 参数的 video_id」这个视频身份跨 KOL 找。此前候选只在本 KOL 内(WHERE kol_pool_id=?)→
    # 别的 KOL 已占的同视频查不到 → 上层错判 would_create 造成跨 KOL 错归属。
    # 用 POSITION 按 video_id 包含收窄候选(无字面 %,compat 安全),再用 _video_identity 精确
    # 核对身份相等;同 KOL 命中优先返回(→ 上层 reuse),否则返回别的 KOL 命中(→ 上层判
    # conflict_existing_other_kol)。
    candidates = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_video_evidence
        WHERE content_url IS NOT NULL AND POSITION(? IN content_url) > 0
        ORDER BY id DESC
        LIMIT 200
        """,
        (str(video_id),),
    ).fetchall()
    other_kol_match: dict[str, Any] | None = None
    for candidate in candidates:
        item = dict(candidate)
        if _video_identity(str(item.get("content_url") or "")) != identity:
            continue
        if int(item.get("kol_pool_id") or 0) == int(kol_pool_id):
            return item
        if other_kol_match is None:
            other_kol_match = item
    return other_kol_match


def _score_snapshot(conn: Any, ids: list[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT id, viltrox_fit_score, viltrox_fit_reason
        FROM vkpi_kol_pool
        WHERE id IN ({placeholders})
        """,
        tuple(int(item) for item in ids),
    ).fetchall()
    return {
        int(row["id"]): {
            "viltrox_fit_score": row["viltrox_fit_score"],
            "viltrox_fit_reason": row["viltrox_fit_reason"],
        }
        for row in rows
    }


def _changed_score_ids(before: dict[int, dict[str, Any]], after: dict[int, dict[str, Any]]) -> list[int]:
    changed: list[int] = []
    for kol_id, before_item in before.items():
        after_item = after.get(kol_id, {})
        if any(before_item.get(field) != after_item.get(field) for field in SCORE_FIELDS):
            changed.append(kol_id)
    return changed


def _public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "platform",
        "content_url",
        "title",
        "view_count",
        "like_count",
        "comment_count",
        "share_count",
        "publish_date",
        "posted_at",
        "duration_seconds",
        "thumbnail_url",
        "channel_id",
        "channel_name",
        "scrape_source",
        "scrape_status",
        "scrape_error",
    )
    return {key: metadata.get(key) for key in keys if metadata.get(key) not in (None, "")}


def _should_write_value(key: str, value: Any) -> bool:
    if key in {"kol_pool_id", "content_url", "source", "source_ref", "confidence", "is_active", "created_at", "updated_at"}:
        return value is not None
    return value not in (None, "")


def _digest(value: str) -> str:
    return hashlib.sha1(_text(value).encode("utf-8")).hexdigest()[:16]


def _video_identity(value: str) -> tuple[str, str] | None:
    parsed = urlparse(_text(value))
    host = parsed.netloc.lower().removeprefix("www.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    lowered = [part.lower() for part in parts]
    if "youtube.com" in host or host == "youtu.be":
        video_id = ""
        if host == "youtu.be" and parts:
            video_id = parts[0]
        elif lowered[:1] == ["watch"]:
            video_id = str((parse_qs(parsed.query).get("v") or [""])[0]).strip()
        elif len(parts) >= 2 and lowered[0] in {"shorts", "embed", "live"}:
            video_id = parts[1]
        return ("youtube", video_id) if video_id else None
    if "instagram.com" in host:
        if len(parts) >= 2 and lowered[0] in {"p", "reel", "tv"}:
            return ("instagram", parts[1])
        if len(parts) >= 3 and lowered[1] in {"p", "reel", "tv"}:
            return ("instagram", parts[2])
    if "tiktok.com" in host:
        for index, part in enumerate(lowered):
            if part == "video" and index + 1 < len(parts):
                return ("tiktok", parts[index + 1])
    return None


from app.core.coerce import _text


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _commit(conn: Any) -> None:
    try:
        conn.commit()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass


def _rollback(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
