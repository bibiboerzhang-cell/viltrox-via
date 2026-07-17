"""Latest-receipt projection for Dealer web verification (migration 271).

One bounded read: the newest ``vkpi_dealer_web_verification`` row per
published dealer, ranked by public prominence.  Boundaries:

* ``carries_viltrox = 'confirmed'`` is one retailer-owned page observed at
  ``verified_at`` — never current inventory, sales, or Viltrox authorization.
* ``prominence_score`` is a descriptive 0-100 public-prominence signal; it
  never feeds fit scoring and is never written back to ``vkpi_dealers``.
* Databases without migration 271 fail closed to ``migration_pending``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn, table_exists

_WEBSITE_STATUSES = frozenset({"alive", "dead", "unreachable"})
_CARRIES_VALUES = frozenset({"confirmed", "not_found", "unknown"})
_SCALE_TIERS = frozenset(
    {"national_chain", "regional_chain", "local_store", "online_focus", "unknown"}
)

_TRUTH_BOUNDARIES = {
    "viltrox_shelf_proves_current_inventory": False,
    "viltrox_shelf_proves_authorization": False,
    "prominence_score_feeds_fit_scoring": False,
    "claim_status": "descriptive_only",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool | None:
    """Compat BOOLEAN read-back: psycopg gives bool, the shim may give 1/0."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if value in (1, "1", "t", "true", "TRUE", "True"):
        return True
    if value in (0, "0", "f", "false", "FALSE", "False"):
        return False
    return None


def _score(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= 100 else None


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if hasattr(row, "keys") else {}


def dealer_rankings(*, limit: int = 200) -> dict[str, Any]:
    """Return the latest verification receipt per published dealer, ranked."""
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not table_exists("vkpi_dealers"):
        return {
            "status": "empty",
            "rankings": [],
            "verified_count": 0,
            "unverified_count": 0,
            "published_total": 0,
            "viltrox_confirmed_count": 0,
            "generated_at": generated_at,
            "truth_boundaries": dict(_TRUTH_BOUNDARIES),
        }
    conn = get_conn()
    published_row = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_dealers WHERE publication_status = 'published'",
        (),
    ).fetchone()
    published_total = int(
        (published_row["n"] if hasattr(published_row, "keys") else published_row[0]) or 0
    )
    if not table_exists("vkpi_dealer_web_verification"):
        return {
            "status": "migration_pending",
            "rankings": [],
            "verified_count": 0,
            "unverified_count": published_total,
            "published_total": published_total,
            "viltrox_confirmed_count": 0,
            "generated_at": generated_at,
            "truth_boundaries": dict(_TRUTH_BOUNDARIES),
        }
    safe_limit = max(1, min(int(limit or 200), 500))
    rows = conn.execute(
        """
        SELECT d.id AS dealer_id, d.name, d.city, d.state, d.website_url,
               d.stable_org_key,
               v.verified_at, v.website_status, v.is_camera_retailer,
               v.carries_viltrox, v.viltrox_evidence_url, v.scale_tier,
               v.prominence_score, v.prominence_rationale
        FROM vkpi_dealers d
        JOIN vkpi_dealer_web_verification v ON v.dealer_id = d.id
        JOIN (
            SELECT dealer_id, MAX(verified_at) AS verified_at
            FROM vkpi_dealer_web_verification
            GROUP BY dealer_id
        ) latest
          ON latest.dealer_id = v.dealer_id
         AND latest.verified_at = v.verified_at
        WHERE d.publication_status = 'published'
        ORDER BY v.prominence_score DESC NULLS LAST, d.name ASC, d.id ASC
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    # 2026-07-18 组织聚合:同 stable_org_key 的连锁多址合并为一行(Samy's ×3 等),
    # 取最高分成员为代表;任一门店 confirmed 即组织 confirmed。key 为空按单店处理。
    location_rows: list[dict[str, Any]] = []
    for raw in rows:
        item = _row_dict(raw)
        website_status = _text(item.get("website_status")).casefold()
        if website_status not in _WEBSITE_STATUSES:
            website_status = "unreachable"
        carries = _text(item.get("carries_viltrox")).casefold()
        if carries not in _CARRIES_VALUES:
            carries = "unknown"
        evidence_url = _text(item.get("viltrox_evidence_url")) or None
        if carries != "confirmed":
            evidence_url = None
        scale_tier = _text(item.get("scale_tier")).casefold()
        if scale_tier not in _SCALE_TIERS:
            scale_tier = "unknown"
        location_rows.append(
            {
                "dealer_id": item.get("dealer_id"),
                "name": _text(item.get("name")),
                "city": _text(item.get("city")) or None,
                "state": _text(item.get("state")) or None,
                "website_url": _text(item.get("website_url")) or None,
                "verified_at": _text(item.get("verified_at")) or None,
                "website_status": website_status,
                "is_camera_retailer": _truthy(item.get("is_camera_retailer")),
                "carries_viltrox": carries,
                "viltrox_evidence_url": evidence_url,
                "scale_tier": scale_tier,
                "prominence_score": _score(item.get("prominence_score")),
                "prominence_rationale": _text(item.get("prominence_rationale")) or None,
                "org_key": _text(item.get("stable_org_key")) or None,
            }
        )
    groups: dict[str, list[dict[str, Any]]] = {}
    for loc in location_rows:
        key = loc["org_key"] or f"solo:{loc['dealer_id']}"
        groups.setdefault(key, []).append(loc)
    _TIER_RANK = {"national_chain": 4, "regional_chain": 3, "local_store": 2, "online_focus": 1, "unknown": 0}
    merged: list[dict[str, Any]] = []
    for key, members in groups.items():
        members.sort(key=lambda m: (-(m["prominence_score"] or -1), str(m["name"])))
        top = members[0]
        confirmed_members = [m for m in members if m["carries_viltrox"] == "confirmed"]
        best_tier = max(members, key=lambda m: _TIER_RANK.get(m["scale_tier"], 0))["scale_tier"]
        entry = dict(top)
        entry.pop("org_key", None)
        entry["carries_viltrox"] = "confirmed" if confirmed_members else top["carries_viltrox"]
        entry["viltrox_evidence_url"] = (
            confirmed_members[0]["viltrox_evidence_url"] if confirmed_members else None
        )
        entry["scale_tier"] = best_tier
        entry["website_status"] = (
            "alive" if any(m["website_status"] == "alive" for m in members) else top["website_status"]
        )
        entry["location_count"] = len(members)
        entry["states"] = sorted({m["state"] for m in members if m["state"]})
        if len(members) > 1:
            entry["locations"] = [
                {
                    "dealer_id": m["dealer_id"],
                    "city": m["city"],
                    "state": m["state"],
                    "carries_viltrox": m["carries_viltrox"],
                    "prominence_score": m["prominence_score"],
                }
                for m in members
            ]
        merged.append(entry)
    merged.sort(key=lambda e: (-(e["prominence_score"] or -1), str(e["name"])))
    rankings: list[dict[str, Any]] = []
    viltrox_confirmed = 0
    rank = 0
    for entry in merged:
        if entry["carries_viltrox"] == "confirmed":
            viltrox_confirmed += 1
        if entry["prominence_score"] is not None:
            rank += 1
            entry["rank"] = rank
        else:
            entry["rank"] = None
        rankings.append(entry)
    verified_count = len(location_rows)
    return {
        "status": "ready" if verified_count > 0 else "empty",
        "rankings": rankings,
        "verified_count": verified_count,
        "unverified_count": max(0, published_total - verified_count),
        "published_total": published_total,
        "viltrox_confirmed_count": viltrox_confirmed,
        "generated_at": generated_at,
        "truth_boundaries": dict(_TRUTH_BOUNDARIES),
    }
