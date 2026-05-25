"""Daily staff outreach digest helpers."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.services.system import staff as staff_service
import importlib

platform_crawl_settings = importlib.import_module("app.domains.settings.platform_crawl")
from app.domains.analytics.common import _actor, _china_today, _int, _json, _loads_json, _utcnow
from app.domains.analytics.suggestions import rank_uncontacted_suggestions
from app.domains.analytics.schema import ensure_vkpi_analytics_schema


def _staff_display_name(member: dict[str, Any]) -> str:
    return str(
        member.get("name")
        or member.get("user_name")
        or member.get("display_name")
        or member.get("email")
        or member.get("user_email")
        or ""
    ).strip()


def _is_test_or_smoke_staff(member: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(member.get(key) or "")
        for key in ("name", "user_name", "display_name", "email", "user_email", "user_handle")
    ).lower()
    markers = (
        "viltrox-smoke.local",
        "-smoke-",
        "_smoke_",
        "smoke_",
        "smoke-",
        "vkpi-",
    )
    return any(marker in haystack for marker in markers)


def _active_staff_members(include_test_staff: bool = True) -> list[dict[str, Any]]:
    members = staff_service.list_members().get("members") or []
    active = []
    for member in members:
        if str(member.get("active", 1)) in {"0", "false", "False"}:
            continue
        staff_id_value = _int(member.get("id") or member.get("staff_id") or member.get("user_id"))
        if not staff_id_value:
            continue
        row = dict(member)
        row["id"] = staff_id_value
        row["name"] = _staff_display_name(row) or f"staff-{staff_id_value}"
        row["email"] = str(row.get("email") or row.get("user_email") or "")
        if not include_test_staff and _is_test_or_smoke_staff(row):
            continue
        active.append(row)
    return active


def _daily_digest_staff_scope() -> tuple[list[dict[str, Any]], int]:
    all_active = _active_staff_members(include_test_staff=True)
    scoped = [member for member in all_active if not _is_test_or_smoke_staff(member)]
    return scoped, max(0, len(all_active) - len(scoped))


def _is_manager_like(staff: dict[str, Any] | None) -> bool:
    if not staff:
        return True
    role = str(staff.get("role") or "").strip().lower()
    return int(staff.get("is_owner") or 0) == 1 or role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def _digest_item_owner_staff_id(item: dict[str, Any], eligible_staff_ids: set[int]) -> tuple[int, str]:
    """Return the preferred owner for an uncontacted suggestion.

    Daily Top100 intentionally excludes already-contacted KOLs, so ownership
    here must come from suggestion/import metadata rather than existing project
    history. This keeps the queue uncontacted while still respecting CSV/import
    responsibility when it is available.
    """
    direct_keys = ("claimed_by_staff_id", "last_collab_staff_id")
    for key in direct_keys:
        sid = _int(item.get(key))
        if sid in eligible_staff_ids:
            return sid, key

    metadata = _loads_json(item.get("metadata_json"), {}) or {}
    if isinstance(metadata, dict):
        for key in ("responsible_staff_id", "owner_staff_id", "assigned_staff_id", "created_by_staff_id", "source_staff_id"):
            sid = _int(metadata.get(key))
            if sid in eligible_staff_ids:
                return sid, f"metadata.{key}"
    return 0, ""


def _digest_item_with_assignment(item: dict[str, Any], staff_id_value: int, reason: str) -> dict[str, Any]:
    assigned = dict(item)
    assigned["_assignment_staff_id"] = staff_id_value
    assigned["_assignment_reason"] = reason
    return assigned


def _assign_digest_items(
    items: list[dict[str, Any]],
    members: list[dict[str, Any]],
    limit: int,
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, int]]:
    """Distribute ranked candidates so one candidate is assigned once per day.

    Daily Top100 is a work queue, not a broadcast list. If the same suggestion
    is sent to every staff member, two people can contact the same KOL.
    """
    safe_limit = max(1, min(100, int(limit or 100)))
    staff_ids: list[int] = []
    for member in members:
        sid = _int(member.get("id"))
        if sid and sid not in staff_ids:
            staff_ids.append(sid)
    assignments: dict[int, list[dict[str, Any]]] = {sid: [] for sid in staff_ids}
    stats = {"owned_assignment_count": 0, "fallback_assignment_count": 0}
    if not staff_ids:
        return assignments, stats

    eligible_staff_ids = set(staff_ids)
    cursor = 0
    seen_suggestions: set[int] = set()
    fallback_items: list[dict[str, Any]] = []
    for item in items:
        suggestion_id = _int(item.get("id"))
        if suggestion_id and suggestion_id in seen_suggestions:
            continue
        owner_staff_id, owner_reason = _digest_item_owner_staff_id(item, eligible_staff_ids)
        if owner_staff_id and len(assignments[owner_staff_id]) < safe_limit:
            assignments[owner_staff_id].append(_digest_item_with_assignment(item, owner_staff_id, owner_reason))
            if suggestion_id:
                seen_suggestions.add(suggestion_id)
            stats["owned_assignment_count"] += 1
            continue
        fallback_items.append(item)

    for item in fallback_items:
        suggestion_id = _int(item.get("id"))
        if suggestion_id and suggestion_id in seen_suggestions:
            continue
        placed = False
        for offset in range(len(staff_ids)):
            sid = staff_ids[(cursor + offset) % len(staff_ids)]
            if len(assignments[sid]) < safe_limit:
                assignments[sid].append(_digest_item_with_assignment(item, sid, "fallback_round_robin"))
                if suggestion_id:
                    seen_suggestions.add(suggestion_id)
                cursor = (cursor + offset + 1) % len(staff_ids)
                stats["fallback_assignment_count"] += 1
                placed = True
                break
        if not placed:
            break
    return assignments, stats


def _daily_digest_duplicate_count(digest_date: str) -> int:
    ensure_vkpi_analytics_schema()
    rows = get_conn().execute(
        """
        SELECT i.suggestion_id, COUNT(*) AS n
        FROM vkpi_staff_outreach_digest_items i
        JOIN vkpi_staff_outreach_digests d ON d.id = i.digest_id
        WHERE d.digest_date=?
        GROUP BY i.suggestion_id
        HAVING COUNT(*) > 1
        """,
        (digest_date,),
    ).fetchall()
    return sum(max(0, _int(row["n"]) - 1) for row in rows)


def _upsert_digest(staff_id_value: int, digest_date: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    conn = get_conn()
    now = _utcnow()
    uid = f"digest-{digest_date}-{staff_id_value}"
    if is_postgres_runtime():
        conn.execute(
            """
            INSERT INTO vkpi_staff_outreach_digests
                (digest_uid, staff_id, digest_date, generated_at, item_count, status, metadata_json)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(staff_id, digest_date) DO UPDATE SET
                generated_at=excluded.generated_at,
                item_count=excluded.item_count,
                status=excluded.status,
                metadata_json=excluded.metadata_json
            """,
            (uid, staff_id_value, digest_date, now, len(items), "ready", _json({"limit": len(items), "source": "daily_morning_sync"})),
        )
    else:
        conn.execute(
            """
            INSERT INTO vkpi_staff_outreach_digests
                (digest_uid, staff_id, digest_date, generated_at, item_count, status, metadata_json)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(staff_id, digest_date) DO UPDATE SET
                generated_at=excluded.generated_at,
                item_count=excluded.item_count,
                status=excluded.status,
                metadata_json=excluded.metadata_json
            """,
            (uid, staff_id_value, digest_date, now, len(items), "ready", _json({"limit": len(items), "source": "daily_morning_sync"})),
        )
    digest_row = conn.execute("SELECT * FROM vkpi_staff_outreach_digests WHERE staff_id=? AND digest_date=?", (staff_id_value, digest_date)).fetchone()
    digest_id = _int(digest_row["id"] if digest_row else 0)
    if digest_id:
        conn.execute("DELETE FROM vkpi_staff_outreach_digest_items WHERE digest_id=?", (digest_id,))
        for index, item in enumerate(items, start=1):
            conn.execute(
                """
                INSERT INTO vkpi_staff_outreach_digest_items
                    (digest_id, suggestion_id, rank, quality_score, relevance_reason, buyer_profile,
                     viewer_profile, content_angle, metadata_json, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    digest_id,
                    _int(item.get("id")),
                    index,
                    float(item.get("quality_score") or 0),
                    str(item.get("relevance_reason") or ""),
                    str(item.get("buyer_profile") or ""),
                    str(item.get("viewer_profile") or ""),
                    str(item.get("content_angle") or ""),
                    _json(
                        {
                            "source_product_sku": item.get("source_product_sku"),
                            "matched_competitors": item.get("matched_competitors"),
                            "matched_intents": item.get("matched_intents"),
                            "matched_kol_id": item.get("matched_kol_id"),
                            "assignment_staff_id": item.get("_assignment_staff_id"),
                            "assignment_reason": item.get("_assignment_reason"),
                        }
                    ),
                    now,
                ),
            )
    conn.commit()
    return {"digest_id": digest_id, "staff_id": staff_id_value, "item_count": len(items)}


def generate_daily_staff_outreach_digest(target_date: str | None = None, limit: int = 100, staff: dict[str, Any] | None = None, product_sku: str = "") -> dict[str, Any]:
    digest_date = str(target_date or _china_today())
    safe_limit = max(1, min(100, int(limit or 100)))
    ranked = rank_uncontacted_suggestions(limit=max(100, safe_limit), product_sku=product_sku)
    items = ranked.get("items") or []
    excluded_staff_count = 0
    if _is_manager_like(staff):
        members, excluded_staff_count = _daily_digest_staff_scope()
    else:
        members = []
    if not members and staff:
        sid = _actor(staff)
        if sid:
            members = [{"id": sid, "user_name": staff.get("name") or staff.get("email") or "staff"}]
    assignments, assignment_stats = _assign_digest_items(items, members, safe_limit)
    digests = [
        _upsert_digest(_int(member.get("id")), digest_date, assignments.get(_int(member.get("id")), []))
        for member in members
    ]
    eligible_staff_count = len(digests)
    item_counts = [int(digest.get("item_count") or 0) for digest in digests]
    items_total = sum(item_counts)
    return {
        "status": "ok",
        "digest_date": digest_date,
        "staff_count": eligible_staff_count,
        "eligible_staff_count": eligible_staff_count,
        "active_staff_count": eligible_staff_count + excluded_staff_count,
        "items_per_staff": max(item_counts) if item_counts else 0,
        "items_total": items_total,
        "assigned_unique_count": items_total,
        "assignment_strategy": "owner_first_then_round_robin",
        "owned_assignment_count": assignment_stats.get("owned_assignment_count", 0),
        "fallback_assignment_count": assignment_stats.get("fallback_assignment_count", 0),
        "duplicate_suggestion_count": _daily_digest_duplicate_count(digest_date),
        "excluded_staff_count": excluded_staff_count,
        "no_candidate_staff_count": sum(1 for count in item_counts if count == 0),
        "total_candidates": ranked.get("total_candidates", 0),
        "uncontacted_count": ranked.get("uncontacted_count", 0),
        "candidate_source": ranked.get("candidate_source", "none"),
        "bridge_seeded_count": ranked.get("bridge_seeded_count", 0),
        "digests": digests,
    }


def daily_staff_outreach_digest_status(target_date: str | None = None, limit: int = 100, staff: dict[str, Any] | None = None, product_sku: str = "") -> dict[str, Any]:
    """Return the 08:00 China Top-100 digest status without fabricating KOL data."""
    ensure_vkpi_analytics_schema()
    digest_date = str(target_date or _china_today())
    safe_limit = max(1, min(100, int(limit or 100)))
    ranked = rank_uncontacted_suggestions(limit=safe_limit, product_sku=product_sku)
    excluded_staff_count = 0
    if _is_manager_like(staff):
        members, excluded_staff_count = _daily_digest_staff_scope()
    else:
        members = []
    if not members and staff:
        sid = _actor(staff)
        if sid:
            members = [{"id": sid, "user_name": staff.get("name") or staff.get("email") or "staff", "email": staff.get("email")}]

    conn = get_conn()
    staff_rows: list[dict[str, Any]] = []
    digest_count = 0
    generated_staff_count = 0
    ready_staff_count = 0
    empty_staff_count = 0
    items_total = 0
    owned_assignment_count = 0
    fallback_assignment_count = 0
    last_generated_at = ""
    for member in members:
        sid = _int(member.get("id"))
        row = conn.execute(
            "SELECT * FROM vkpi_staff_outreach_digests WHERE staff_id=? AND digest_date=?",
            (sid, digest_date),
        ).fetchone()
        digest = dict(row) if row else {}
        item_count = _int(digest.get("item_count")) if digest else 0
        status = str(digest.get("status") or "not_generated")
        generated_at = str(digest.get("generated_at") or "")
        if digest:
            digest_count += 1
        if status == "ready":
            generated_staff_count += 1
        if status == "ready" and item_count > 0:
            ready_staff_count += 1
        if status == "ready" and item_count == 0:
            empty_staff_count += 1
        items_total += item_count
        if generated_at and generated_at > last_generated_at:
            last_generated_at = generated_at
        if digest:
            item_rows = conn.execute(
                "SELECT metadata_json FROM vkpi_staff_outreach_digest_items WHERE digest_id=?",
                (_int(digest.get("id")),),
            ).fetchall()
            for item_row in item_rows:
                item_metadata = _loads_json(item_row.get("metadata_json"), {}) or {}
                if not isinstance(item_metadata, dict):
                    continue
                reason = str(item_metadata.get("assignment_reason") or "")
                if reason == "fallback_round_robin":
                    fallback_assignment_count += 1
                elif reason:
                    owned_assignment_count += 1
        staff_rows.append(
            {
                "staff_id": sid,
                "name": member.get("name") or member.get("user_name") or member.get("email") or f"staff-{sid}",
                "email": member.get("email") or "",
                "status": status,
                "item_count": item_count,
                "generated_at": generated_at,
            }
        )

    flags = platform_crawl_settings.feature_flags().get("flags") or []
    digest_flag = next((dict(item) for item in flags if str(item.get("flag_key") or "") == "daily_staff_digest"), {})
    eligible_staff_count = len(staff_rows)
    return {
        "status": "ok",
        "digest_date": digest_date,
        "scheduled_time": "08:00",
        "timezone": "Asia/Shanghai",
        "limit_per_staff": safe_limit,
        "feature_enabled": bool(_int(digest_flag.get("enabled"))),
        "staff_count": eligible_staff_count,
        "eligible_staff_count": eligible_staff_count,
        "active_staff_count": eligible_staff_count + excluded_staff_count,
        "generated_staff_count": generated_staff_count,
        "digest_count": digest_count,
        "ready_staff_count": ready_staff_count,
        "empty_staff_count": empty_staff_count,
        "staff_filter": "active_non_test_staff",
        "excluded_staff_count": excluded_staff_count,
        "items_total": items_total,
        "duplicate_suggestion_count": _daily_digest_duplicate_count(digest_date),
        "assignment_strategy": "owner_first_then_round_robin",
        "owned_assignment_count": owned_assignment_count,
        "fallback_assignment_count": fallback_assignment_count,
        "last_generated_at": last_generated_at,
        "total_candidates": ranked.get("total_candidates", 0),
        "uncontacted_count": ranked.get("uncontacted_count", 0),
        "candidate_source": ranked.get("candidate_source", "none"),
        "bridge_seeded_count": ranked.get("bridge_seeded_count", 0),
        "rule": "仅推荐未联系、未认领、未建项目的 KOL；排除公司官方账号；按质量分、播放量和产品相关度排序，每员工最多 100 条。",
        "staff": staff_rows,
    }


def list_daily_staff_outreach_digest(staff_id: int, target_date: str | None = None, limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    digest_date = str(target_date or _china_today())
    conn = get_conn()
    digest = conn.execute(
        "SELECT * FROM vkpi_staff_outreach_digests WHERE staff_id=? AND digest_date=?",
        (int(staff_id), digest_date),
    ).fetchone()
    if not digest:
        return {"digest": None, "items": [], "digest_date": digest_date}
    rows = conn.execute(
        """
        SELECT
            i.*,
            s.platform,
            s.handle,
            s.channel_name,
            s.follower_count,
            s.engagement_rate,
            s.avatar_url,
            s.profile_url,
            s.source_product_sku,
            s.source_video_url,
            s.source_video_title,
            s.source_view_count,
            s.source_like_count,
            s.source_published_at,
            s.score,
            s.status AS suggestion_status
        FROM vkpi_staff_outreach_digest_items i
        JOIN vkpi_outreach_suggestions s ON s.id = i.suggestion_id
        WHERE i.digest_id=?
        ORDER BY i.rank ASC
        LIMIT ?
        """,
        (_int(digest["id"]), max(1, min(100, int(limit or 100)))),
    ).fetchall()
    return {"digest": dict(digest), "items": [dict(row) for row in rows], "digest_date": digest_date}
