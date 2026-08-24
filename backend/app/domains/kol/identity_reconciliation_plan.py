"""Read-only historical identity, avatar, and official-account reconciliation.

The planner turns current database evidence into an auditable proposal.  It
never writes a database row, never chooses a duplicate master, and never
converts ambiguous YouTube channel-id/handle evidence into an automatic alias.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any

from app.domains.kol.discovery_filters import discovery_account_gate_verdict
from app.domains.kol.identity import (
    YOUTUBE_CHANNEL_ID_RE,
    canonical_creator_aliases,
)
from app.domains.kol.search_sessions_items import canonicalize_session_creator_items
from app.services.intelligence.account_scan_helpers import _avatar_url_policy


CREATOR_ITEM_TYPES = {
    "recall_candidate",
    "online_qualified_candidate",
    "new_creator",
    "existing_kol",
}
_RESERVED_LOCATORS = {
    "c",
    "channel",
    "channels",
    "dp",
    "p",
    "product",
    "products",
    "reel",
    "reels",
    "short",
    "shorts",
    "user",
    "video",
    "videos",
    "watch",
}
_PLATFORM_RESERVED_LOCATORS = {
    "facebook": {
        "groups",
        "pages",
        "people",
        "profile.php",
        "story.php",
        "watch",
    },
    "instagram": {"direct", "explore", "instagram", "p", "reel", "reels", "stories"},
    "tiktok": {"discover", "tag", "tiktok", "video"},
    "twitter": {"home", "i", "intent", "search", "status", "twitter"},
    "youtube": {"live", "playlist", "shorts", "watch", "youtube"},
}


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, (str, bytes)) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pool_probe(row: dict[str, Any]) -> dict[str, Any]:
    raw = _json_obj(row.get("raw_platform_data"))
    return {**raw, **row, "raw_platform_data": raw}


def _session_probe(item: dict[str, Any]) -> dict[str, Any]:
    payload = _json_obj(item.get("payload"))
    return {
        **payload,
        "kol_pool_id": item.get("kol_pool_id") or payload.get("kol_pool_id"),
        "profile_url": payload.get("profile_url") or item.get("source_url"),
        "source_url": item.get("source_url") or payload.get("source_url"),
    }


def _alias_locator(alias: str) -> tuple[str, str, str] | None:
    parts = str(alias or "").split(":", 2)
    if len(parts) != 3 or parts[1] not in {"id", "handle"}:
        return None
    platform, kind, handle = parts
    if not platform or not handle:
        return None
    return platform, kind, handle


def _locator_is_safe(platform: str, kind: str, handle: str) -> bool:
    clean = str(handle or "").strip().lower()
    if (
        not clean
        or clean in _RESERVED_LOCATORS
        or clean in _PLATFORM_RESERVED_LOCATORS.get(platform, set())
    ):
        return False
    if kind == "id" and platform == "youtube":
        return bool(YOUTUBE_CHANNEL_ID_RE.fullmatch(clean))
    return kind == "handle"


def _union_components(
    active_rows: list[dict[str, Any]],
    aliases_by_pool: dict[int, set[str]],
) -> list[list[dict[str, Any]]]:
    parents = {int(row["id"]): int(row["id"]) for row in active_rows}

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    owner: dict[str, int] = {}
    for pool_id in sorted(aliases_by_pool):
        for alias in sorted(aliases_by_pool[pool_id]):
            if alias in owner:
                union(pool_id, owner[alias])
            else:
                owner[alias] = pool_id
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in active_rows:
        grouped[find(int(row["id"]))].append(row)
    return list(grouped.values())


def _public_pool_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "platform": str(row.get("platform") or ""),
        "handle": str(row.get("handle") or ""),
        "display_name": str(row.get("display_name") or ""),
        "dashboard_account_type": str(row.get("dashboard_account_type") or ""),
    }


def _avatar_counts(rows: list[dict[str, Any]], field: str = "avatar_url") -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        _url, status = _avatar_url_policy(row.get(field))
        counts[status] += 1
    for status in ("durable", "ephemeral", "expired", "invalid", "missing"):
        counts.setdefault(status, 0)
    return dict(sorted(counts.items()))


def _candidate_alias_plan(
    *,
    pool_rows: list[dict[str, Any]],
    alias_rows: list[dict[str, Any]],
    session_items: list[dict[str, Any]],
    aliases_by_pool: dict[int, set[str]],
) -> dict[str, Any]:
    active_ids = {
        int(row["id"])
        for row in pool_rows
        if not row.get("duplicate_of_id")
    }
    existing_owner: dict[str, set[int]] = defaultdict(set)
    for row in alias_rows:
        pool_id = int(row.get("kol_pool_id") or 0)
        if not pool_id:
            continue
        for alias in canonical_creator_aliases(row):
            existing_owner[alias].add(pool_id)

    pool_observed_owner: dict[str, set[int]] = defaultdict(set)
    for pool_id, aliases in aliases_by_pool.items():
        for alias in aliases:
            pool_observed_owner[alias].add(pool_id)

    bridge_evidence: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"ids": set(), "handles": set(), "item_ids": set()}
    )
    bridge_reverse_owner: dict[str, set[int]] = defaultdict(set)
    for item in session_items:
        if str(item.get("item_type") or "") not in CREATOR_ITEM_TYPES:
            continue
        probe = _session_probe(item)
        try:
            pool_id = int(probe.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            pool_id = 0
        if pool_id not in active_ids:
            continue
        aliases = canonical_creator_aliases(probe)
        ids = {alias for alias in aliases if alias.startswith("youtube:id:")}
        handles = {alias for alias in aliases if alias.startswith("youtube:handle:")}
        if not ids or not handles:
            continue
        evidence = bridge_evidence[pool_id]
        evidence["ids"].update(ids)
        evidence["handles"].update(handles)
        if item.get("id") is not None:
            evidence["item_ids"].add(int(item["id"]))
        for alias in ids | handles:
            bridge_reverse_owner[alias].add(pool_id)

    safe_bridges: list[dict[str, Any]] = []
    manual_bridges: list[dict[str, Any]] = []
    for pool_id, evidence in sorted(bridge_evidence.items()):
        ids = sorted(evidence["ids"])
        handles = sorted(evidence["handles"])
        all_aliases = ids + handles
        reasons: list[str] = []
        if len(ids) != 1:
            reasons.append("multiple_native_ids")
        if len(handles) != 1:
            reasons.append("multiple_handles")
        if any(bridge_reverse_owner[alias] != {pool_id} for alias in all_aliases):
            reasons.append("alias_seen_for_multiple_pool_rows")
        if any(pool_observed_owner[alias] - {pool_id} for alias in all_aliases):
            reasons.append("alias_conflicts_with_pool_row")
        record = {
            "kol_pool_id": pool_id,
            "youtube_id_aliases": ids,
            "youtube_handle_aliases": handles,
            "evidence_item_ids": sorted(evidence["item_ids"]),
        }
        if reasons:
            manual_bridges.append({**record, "review_reasons": sorted(set(reasons))})
        else:
            safe_bridges.append(record)

    proposed: dict[tuple[str, str], dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []

    def propose(pool_id: int, alias: str, source: str, evidence_ids: list[int]) -> None:
        locator = _alias_locator(alias)
        if locator is None:
            return
        platform, kind, handle = locator
        owners = existing_owner.get(alias, set())
        other_pool_owners = pool_observed_owner.get(alias, set()) - {pool_id}
        if owners == {pool_id}:
            return
        reasons: list[str] = []
        if owners - {pool_id}:
            reasons.append("existing_alias_owned_by_other_pool")
        if other_pool_owners:
            reasons.append("alias_observed_on_other_pool_row")
        if not _locator_is_safe(platform, kind, handle):
            reasons.append("unsafe_or_reserved_locator")
        if reasons:
            rejected.append(
                {
                    "kol_pool_id": pool_id,
                    "canonical_alias": alias,
                    "review_reasons": sorted(set(reasons)),
                }
            )
            return
        key = (platform, handle)
        current = proposed.get(key)
        candidate = {
            "kol_pool_id": pool_id,
            "platform": platform,
            "handle": handle,
            "alias_kind": kind,
            "confidence": 1.0 if source == "pool_top_level" else 0.95,
            "source": source,
            "evidence_item_ids": sorted(set(evidence_ids)),
        }
        if current and int(current["kol_pool_id"]) != pool_id:
            rejected.append(
                {
                    "kol_pool_id": pool_id,
                    "canonical_alias": alias,
                    "review_reasons": ["plan_locator_collision"],
                }
            )
            return
        if current and current["source"] == "pool_top_level":
            return
        proposed[key] = candidate

    for row in pool_rows:
        if row.get("duplicate_of_id"):
            continue
        if str(row.get("dashboard_account_type") or "").lower() in {"company", "media"}:
            continue
        pool_id = int(row["id"])
        minimal_probe = {
            "platform": row.get("platform"),
            "handle": row.get("handle"),
        }
        for alias in canonical_creator_aliases(minimal_probe):
            propose(pool_id, alias, "pool_top_level", [])
    for bridge in safe_bridges:
        pool_id = int(bridge["kol_pool_id"])
        for alias in bridge["youtube_id_aliases"] + bridge["youtube_handle_aliases"]:
            propose(pool_id, alias, "historical_session_bridge", bridge["evidence_item_ids"])

    safe_backfills = sorted(
        proposed.values(),
        key=lambda item: (item["platform"], item["handle"], int(item["kol_pool_id"])),
    )
    return {
        "existing_alias_rows": len(alias_rows),
        "safe_bridge_group_count": len(safe_bridges),
        "manual_bridge_group_count": len(manual_bridges),
        "safe_bridge_groups": safe_bridges,
        "manual_bridge_groups": manual_bridges,
        "safe_alias_backfill_count": len(safe_backfills),
        "safe_alias_backfills": safe_backfills,
        "review_alias_count": len(rejected),
        "review_aliases": sorted(
            rejected,
            key=lambda item: (int(item["kol_pool_id"]), item["canonical_alias"]),
        ),
        "write_contract": {
            "physical_delete_allowed": False,
            "duplicate_pointer_write_allowed": False,
            "master_selection_allowed": False,
            "score_field_write_allowed": False,
            "apply_supported_by_this_planner": False,
        },
    }


def build_identity_reconciliation_plan(
    *,
    pool_rows: list[dict[str, Any]],
    alias_rows: list[dict[str, Any]],
    session_items: list[dict[str, Any]],
    generated_at: str,
    source_label: str = "local_production_snapshot",
) -> dict[str, Any]:
    """Build a deterministic, evidence-only plan from already-read rows."""
    rows = [dict(row) for row in pool_rows]
    aliases = [dict(row) for row in alias_rows]
    sessions = [dict(item) for item in session_items]
    active = [row for row in rows if not row.get("duplicate_of_id")]
    active_ids = {int(row["id"]) for row in active}

    aliases_by_pool = {
        int(row["id"]): canonical_creator_aliases(_pool_probe(row))
        for row in active
    }
    for alias_row in aliases:
        pool_id = int(alias_row.get("kol_pool_id") or 0)
        if pool_id in active_ids:
            aliases_by_pool[pool_id].update(canonical_creator_aliases(alias_row))
    components = _union_components(active, aliases_by_pool)
    duplicate_groups = [group for group in components if len(group) > 1]
    canonical_group_details: list[dict[str, Any]] = []
    for group in duplicate_groups:
        ids = {int(row["id"]) for row in group}
        frequency: Counter[str] = Counter(
            alias for pool_id in ids for alias in aliases_by_pool[pool_id]
        )
        canonical_group_details.append(
            {
                "pool_rows": [_public_pool_row(row) for row in sorted(group, key=lambda x: int(x["id"]))],
                "shared_aliases": sorted(alias for alias, count in frequency.items() if count > 1),
                "action": "manual_review_only",
            }
        )

    session_by_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in sessions:
        session_by_id[int(item.get("session_id") or 0)].append(item)
    folded_sessions: list[dict[str, int]] = []
    for session_id, items in session_by_id.items():
        raw_count = sum(
            str(item.get("item_type") or "") in CREATOR_ITEM_TYPES for item in items
        )
        folded = canonicalize_session_creator_items(items)
        folded_count = sum(
            str(item.get("item_type") or "") in CREATOR_ITEM_TYPES for item in folded
        )
        if raw_count > folded_count:
            folded_sessions.append(
                {
                    "session_id": session_id,
                    "raw_creator_cards": raw_count,
                    "canonical_creator_cards": folded_count,
                    "folded_cards": raw_count - folded_count,
                }
            )

    session_avatar_rows: list[dict[str, Any]] = []
    session_official_rows: list[dict[str, Any]] = []
    for item in sessions:
        if str(item.get("item_type") or "") not in CREATOR_ITEM_TYPES:
            continue
        probe = _session_probe(item)
        session_avatar_rows.append({"avatar_url": probe.get("avatar_url")})
        verdict = discovery_account_gate_verdict(probe)
        if verdict:
            session_official_rows.append(
                {
                    "item_id": int(item.get("id") or 0),
                    "session_id": int(item.get("session_id") or 0),
                    "kol_pool_id": int(probe.get("kol_pool_id") or 0) or None,
                    "platform": str(probe.get("platform") or ""),
                    "handle": str(probe.get("handle") or ""),
                    "verdict": verdict,
                    "session_archived": bool(item.get("session_archived_at")),
                    "planned_read_action": "hide_from_discovery_projection_keep_evidence_row",
                }
            )

    pool_official_rows: list[dict[str, Any]] = []
    for row in active:
        verdict = discovery_account_gate_verdict(_pool_probe(row))
        if not verdict:
            continue
        current_type = str(row.get("dashboard_account_type") or "").strip().lower()
        already_segmented = current_type in {"company", "media"}
        pool_official_rows.append(
            {
                **_public_pool_row(row),
                "verdict": verdict,
                "plan_action": (
                    "keep_existing_non_kol_segment"
                    if already_segmented
                    else "propose_company_segment_and_discovery_quarantine"
                ),
                "proposed_dashboard_account_type": current_type if already_segmented else "company",
                "metadata_marker": {
                    "kind": "discovery_official_isolation_v1",
                    "verdict": verdict,
                    "source": "conservative_discovery_account_gate",
                },
            }
        )

    alias_plan = _candidate_alias_plan(
        pool_rows=rows,
        alias_rows=aliases,
        session_items=sessions,
        aliases_by_pool=aliases_by_pool,
    )
    body: dict[str, Any] = {
        "schema_version": "discovery_identity_reconciliation_plan_v1",
        "generated_at": str(generated_at),
        "source": source_label,
        "mode": "dry_run",
        "claim_status": "descriptive_only",
        "writes_performed": 0,
        "pool": {
            "physical_rows": len(rows),
            "currently_visible_master_rows": len(active),
            "already_soft_folded_rows": len(rows) - len(active),
            "rows_with_canonical_alias": sum(bool(value) for value in aliases_by_pool.values()),
            "canonical_duplicate_group_count": len(duplicate_groups),
            "canonical_extra_visible_rows": sum(len(group) - 1 for group in duplicate_groups),
            "canonical_projection_unique_rows": len(components),
            "duplicate_groups": sorted(
                canonical_group_details,
                key=lambda item: int(item["pool_rows"][0]["id"]),
            ),
        },
        "session_read_projection": {
            "session_count": len(session_by_id),
            "creator_item_count": sum(
                str(item.get("item_type") or "") in CREATOR_ITEM_TYPES for item in sessions
            ),
            "sessions_with_canonical_folds": len(folded_sessions),
            "creator_cards_folded": sum(item["folded_cards"] for item in folded_sessions),
            "folded_sessions": sorted(folded_sessions, key=lambda item: item["session_id"]),
        },
        "avatar_integrity": {
            "pool_visible_rows": _avatar_counts(active),
            "session_creator_items": _avatar_counts(session_avatar_rows),
            "network_probe_performed": False,
        },
        "identity_alias_backfill": alias_plan,
        "official_isolation": {
            "pool_confirmed_count": len(pool_official_rows),
            "pool_plan": sorted(pool_official_rows, key=lambda item: int(item["id"])),
            "session_confirmed_count": len(session_official_rows),
            "session_unarchived_confirmed_count": sum(
                not item["session_archived"] for item in session_official_rows
            ),
            "session_read_plan": sorted(
                session_official_rows,
                key=lambda item: (item["session_id"], item["item_id"]),
            ),
            "physical_history_delete_allowed": False,
        },
        "estimated_impact": {
            "before_release_snapshot": {
                "pool_visible_master_rows": len(active),
                "alias_table_rows": len(aliases),
                "unarchived_confirmed_official_session_cards": sum(
                    not item["session_archived"] for item in session_official_rows
                ),
            },
            "after_read_guard_release": {
                "confirmed_official_session_cards_hidden": sum(
                    not item["session_archived"] for item in session_official_rows
                ),
                "history_rows_deleted": 0,
                "pool_rows_deleted": 0,
                "duplicate_pointer_rows_written": 0,
            },
            "after_separately_reviewed_alias_backfill": {
                "status": "estimate_only_not_executed",
                "expected_alias_table_rows_without_drift": (
                    len(aliases) + int(alias_plan["safe_alias_backfill_count"])
                ),
                "safe_uc_handle_bridge_groups": alias_plan["safe_bridge_group_count"],
                "manual_bridge_groups_unchanged": alias_plan["manual_bridge_group_count"],
                "pool_physical_rows_unchanged": len(rows),
                "score_fields_unchanged": True,
            },
        },
    }
    digest_payload = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    body["plan_sha256"] = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
    return body


def plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    """Compact stdout-friendly summary; the full JSON remains the audit record."""
    pool = plan["pool"]
    session = plan["session_read_projection"]
    aliases = plan["identity_alias_backfill"]
    official = plan["official_isolation"]
    return {
        "schema_version": plan["schema_version"],
        "mode": plan["mode"],
        "writes_performed": plan["writes_performed"],
        "plan_sha256": plan["plan_sha256"],
        "pool_rows": pool["physical_rows"],
        "pool_visible_rows": pool["currently_visible_master_rows"],
        "canonical_duplicate_groups": pool["canonical_duplicate_group_count"],
        "canonical_extra_visible_rows": pool["canonical_extra_visible_rows"],
        "session_cards_folded": session["creator_cards_folded"],
        "safe_bridge_groups": aliases["safe_bridge_group_count"],
        "manual_bridge_groups": aliases["manual_bridge_group_count"],
        "safe_alias_backfills": aliases["safe_alias_backfill_count"],
        "pool_confirmed_official": official["pool_confirmed_count"],
        "unarchived_session_confirmed_official": official["session_unarchived_confirmed_count"],
    }
