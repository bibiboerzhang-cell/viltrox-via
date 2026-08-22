"""Bulk, idempotent enrollment of MY KOL favorite evidence into metric tracking.

The durable subscription table ``vkpi_kol_video_metric_tracking`` is normally
fed one video at a time from the MY KOL drawer.  This module registers every
eligible video evidence of every favorited KOL (the "MY KOL collection") so the
scheduler (``video_metric_schedule``) samples them on the hot 6h / warm 24h /
cold 7d cadence derived from each video's publish age.

Guarantees:
- Never calls a provider and never enqueues; it only writes subscriptions.
- Idempotent: ``INSERT ... ON CONFLICT (evidence_id) DO NOTHING`` — existing
  active or paused rows are left exactly as they are.
- Every subscription carries an actor that passes the same revalidation the
  scheduler performs (``authorize_video_metric_refresh_actor``); evidence
  without an authorizable actor is reported, not registered.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domains.kol import video_metric_refresh, video_metric_schedule
from app.domains.kol.video_url_identity import (
    SUPPORTED_VIDEO_HOSTS,
    VideoUrlIdentityError,
    parse_supported_video_url,
)


ENROLL_SOURCE = "enroll_metric_tracking"
_VIDEO_MEDIA_KINDS = frozenset({"", "video"})


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def select_my_kol_evidence(
    conn: Any,
    *,
    kol_pool_ids: list[int] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Evidence rows of favorited KOLs with the favorite owners attached.

    Only active ``video`` evidence on metric-supported platforms is returned;
    the URL identity check happens in :func:`plan_enrollment` so the plan can
    report invalid rows instead of silently dropping them.
    """

    params: list[Any] = []
    kol_filter = ""
    if kol_pool_ids:
        placeholders = ", ".join("?" for _ in kol_pool_ids)
        kol_filter = f" AND e.kol_pool_id IN ({placeholders})"
        params.extend(int(value) for value in kol_pool_ids)
    limit_sql = ""
    if limit is not None and int(limit) > 0:
        limit_sql = " LIMIT ?"
        params.append(int(limit))
    rows = conn.execute(
        f"""
        SELECT e.id AS evidence_id, e.kol_pool_id, e.platform, e.content_url,
               e.media_kind, e.published_at_norm, e.publish_date, e.posted_at,
               t.status AS tracking_status, t.tracked_by_staff_id AS tracked_by,
               (
                   SELECT STRING_AGG(CAST(f.staff_id AS TEXT), ',' ORDER BY f.id)
                   FROM vkpi_kol_pool_favorites f
                   WHERE f.kol_pool_id=e.kol_pool_id
               ) AS favorite_staff_ids
        FROM vkpi_kol_video_evidence e
        LEFT JOIN vkpi_kol_video_metric_tracking t ON t.evidence_id=e.id
        WHERE e.is_active IS NOT FALSE
          AND LOWER(COALESCE(e.evidence_type, 'video'))='video'
          AND LOWER(COALESCE(e.platform, '')) IN ('youtube', 'instagram', 'tiktok')
          AND EXISTS (
              SELECT 1 FROM vkpi_kol_pool_favorites f WHERE f.kol_pool_id=e.kol_pool_id
          )
          {kol_filter}
        ORDER BY e.id
        {limit_sql}
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


class ActorResolver:
    """Pick, per evidence, an actor that will pass scheduler revalidation."""

    def __init__(self, conn: Any, *, fallback_staff_id: int | None = None):
        self.conn = conn
        self.fallback_staff_id = _int(fallback_staff_id) or None
        self._cache: dict[tuple[int, int], bool] = {}

    def _authorized(self, staff_id: int, kol_pool_id: int) -> bool:
        key = (int(staff_id), int(kol_pool_id))
        if key not in self._cache:
            actor, _error = video_metric_refresh.authorize_video_metric_refresh_actor(
                self.conn, staff_id=int(staff_id), kol_pool_id=int(kol_pool_id),
            )
            self._cache[key] = actor is not None
        return self._cache[key]

    def resolve(self, *, kol_pool_id: int, favorite_staff_ids: list[int]) -> tuple[int | None, str]:
        for staff_id in favorite_staff_ids:
            if staff_id > 0 and self._authorized(staff_id, kol_pool_id):
                return staff_id, "favorite_owner"
        if self.fallback_staff_id and self._authorized(self.fallback_staff_id, kol_pool_id):
            return self.fallback_staff_id, "fallback_actor"
        return None, "no_authorized_actor"


def plan_enrollment(
    conn: Any,
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    fallback_staff_id: int | None = None,
) -> dict[str, Any]:
    """Classify each evidence row; nothing is written."""

    current = now or datetime.now(timezone.utc)
    resolver = ActorResolver(conn, fallback_staff_id=fallback_staff_id)
    plan: dict[str, Any] = {
        "to_register": [],
        "already_active": 0,
        "already_paused": 0,
        "skipped": {},
        "tiers": {"hot": 0, "warm": 0, "cold": 0},
        "platforms": {},
        "actors": {},
    }

    def skip(reason: str) -> None:
        plan["skipped"][reason] = plan["skipped"].get(reason, 0) + 1

    for row in rows:
        status = _text(row.get("tracking_status")).lower()
        if status == "active":
            plan["already_active"] += 1
            continue
        if status:
            plan["already_paused"] += 1
            continue
        if _text(row.get("media_kind")).lower() not in _VIDEO_MEDIA_KINDS:
            skip(f"media_kind_{_text(row.get('media_kind')).lower()}")
            continue
        platform = _text(row.get("platform")).lower()
        if platform not in SUPPORTED_VIDEO_HOSTS:
            skip("platform_unsupported")
            continue
        try:
            identity = parse_supported_video_url(row.get("content_url"))
        except VideoUrlIdentityError as exc:
            skip(f"url_{exc.code}")
            continue
        if identity.platform != platform:
            skip("platform_mismatch")
            continue
        favorite_ids = [
            _int(value) for value in _text(row.get("favorite_staff_ids")).split(",") if _text(value)
        ]
        actor_id, actor_kind = resolver.resolve(
            kol_pool_id=_int(row.get("kol_pool_id")), favorite_staff_ids=favorite_ids,
        )
        if actor_id is None:
            skip(actor_kind)
            continue
        tier = video_metric_schedule.tier_for_evidence(row, current)
        plan["tiers"][tier] += 1
        plan["platforms"][platform] = plan["platforms"].get(platform, 0) + 1
        plan["actors"][str(actor_id)] = plan["actors"].get(str(actor_id), 0) + 1
        plan["to_register"].append({
            "evidence_id": _int(row.get("evidence_id")),
            "kol_pool_id": _int(row.get("kol_pool_id")),
            "platform": platform,
            "tier": tier,
            "cadence_hours": round(video_metric_schedule.TIER_CADENCES[tier].total_seconds() / 3600, 1),
            "actor_staff_id": actor_id,
            "actor_kind": actor_kind,
        })
    return plan


def apply_enrollment(conn: Any, entries: list[dict[str, Any]]) -> dict[str, int]:
    """Insert subscriptions for ``entries``; existing rows are never modified."""

    inserted = 0
    for entry in entries:
        row = conn.execute(
            """
            INSERT INTO vkpi_kol_video_metric_tracking (
                evidence_id, tracked_by_staff_id, status, source,
                pause_reason, created_at, updated_at
            ) VALUES (?, ?, 'active', ?, '', NOW(), NOW())
            ON CONFLICT (evidence_id) DO NOTHING
            RETURNING evidence_id
            """,
            (int(entry["evidence_id"]), int(entry["actor_staff_id"]), ENROLL_SOURCE),
        ).fetchone()
        inserted += 1 if row else 0
    return {"inserted": inserted, "conflicts": len(entries) - inserted}


def enroll_my_kol_evidence(
    conn: Any,
    *,
    apply: bool = False,
    limit: int | None = None,
    kol_pool_ids: list[int] | None = None,
    fallback_staff_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Plan (and with ``apply=True`` write) metric-tracking subscriptions.

    Caller owns the transaction; with ``apply`` the caller must commit.
    """

    rows = select_my_kol_evidence(conn, kol_pool_ids=kol_pool_ids, limit=limit)
    plan = plan_enrollment(conn, rows, now=now, fallback_staff_id=fallback_staff_id)
    summary: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run",
        "candidates": len(rows),
        "to_register": len(plan["to_register"]),
        "already_active": plan["already_active"],
        "already_paused": plan["already_paused"],
        "skipped": plan["skipped"],
        "tiers": plan["tiers"],
        "platforms": plan["platforms"],
        "actors": plan["actors"],
        "cadence_hours": {
            tier: round(delta.total_seconds() / 3600, 1)
            for tier, delta in video_metric_schedule.TIER_CADENCES.items()
        },
        "inserted": 0,
        "conflicts": 0,
        "provider_calls_performed": False,
    }
    if apply and plan["to_register"]:
        written = apply_enrollment(conn, plan["to_register"])
        summary.update(written)
    summary["sample"] = plan["to_register"][:10]
    return summary


__all__ = [
    "ENROLL_SOURCE",
    "ActorResolver",
    "apply_enrollment",
    "enroll_my_kol_evidence",
    "plan_enrollment",
    "select_my_kol_evidence",
]
