"""Pure identity-evidence helpers for the employee-visible KOL Pool read.

This module deliberately depends only on the canonical identity helpers.  It
does not read or write the database and does not import the projection module,
which keeps the read projection dependency graph acyclic.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from app.domains.kol.identity import (
    canonical_creator_aliases,
    canonical_identity_platform,
)


_CREATOR_ITEM_TYPES = {
    "existing_kol",
    "new_creator",
    "online_qualified_candidate",
    "recall_candidate",
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


def _session_probe(item: dict[str, Any]) -> dict[str, Any]:
    payload = _json_obj(item.get("payload") or item.get("payload_json"))
    return {
        **payload,
        "kol_pool_id": item.get("kol_pool_id") or payload.get("kol_pool_id"),
        "profile_url": payload.get("profile_url") or item.get("source_url"),
        "source_url": item.get("source_url") or payload.get("source_url"),
    }


def _union_components(
    rows: list[dict[str, Any]],
    aliases_by_id: dict[int, set[str]],
) -> list[list[dict[str, Any]]]:
    parents = {int(row["id"]): int(row["id"]) for row in rows}

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
    for pool_id in sorted(aliases_by_id):
        for alias in sorted(aliases_by_id[pool_id]):
            if alias in owner:
                union(pool_id, owner[alias])
            else:
                owner[alias] = pool_id
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[find(int(row["id"]))].append(row)
    return list(grouped.values())


def _manual_bridge_conflict_ids(
    rows: list[dict[str, Any]],
    aliases_by_id: dict[int, set[str]],
    session_items: list[dict[str, Any]],
) -> set[int]:
    observed_owner: dict[str, set[int]] = defaultdict(set)
    for pool_id, aliases in aliases_by_id.items():
        for alias in aliases:
            observed_owner[alias].add(pool_id)
    evidence: dict[int, dict[str, set[str]]] = defaultdict(
        lambda: {"ids": set(), "handles": set()}
    )
    reverse_bridge_owner: dict[str, set[int]] = defaultdict(set)
    active_ids = set(aliases_by_id)
    for item in session_items:
        if str(item.get("item_type") or "") not in _CREATOR_ITEM_TYPES:
            continue
        probe = _session_probe(item)
        try:
            pool_id = int(probe.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            continue
        if pool_id not in active_ids:
            continue
        aliases = canonical_creator_aliases(probe)
        ids = {alias for alias in aliases if alias.startswith("youtube:id:")}
        handles = {alias for alias in aliases if alias.startswith("youtube:handle:")}
        if not ids or not handles:
            continue
        evidence[pool_id]["ids"].update(ids)
        evidence[pool_id]["handles"].update(handles)
        for alias in ids | handles:
            reverse_bridge_owner[alias].add(pool_id)

    conflicts: set[int] = set()
    for pool_id, aliases in evidence.items():
        combined = aliases["ids"] | aliases["handles"]
        if len(aliases["ids"]) != 1 or len(aliases["handles"]) != 1:
            conflicts.add(pool_id)
        for alias in combined:
            other_pool_ids = observed_owner.get(alias, set()) - {pool_id}
            other_bridge_ids = reverse_bridge_owner.get(alias, set()) - {pool_id}
            if other_pool_ids or other_bridge_ids:
                conflicts.update({pool_id, *other_pool_ids, *other_bridge_ids})
    return conflicts


def _component_native_ids(
    pool_ids: set[int],
    aliases_by_id: dict[int, set[str]],
    session_items: list[dict[str, Any]],
) -> set[str]:
    """Return every observed native account id for one Pool component."""
    native_ids = {
        alias
        for pool_id in pool_ids
        for alias in aliases_by_id.get(pool_id, set())
        if ":id:" in alias
    }
    for item in session_items:
        try:
            pool_id = int(item.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            continue
        if pool_id not in pool_ids:
            continue
        native_ids.update(
            alias
            for alias in canonical_creator_aliases(_session_probe(item))
            if ":id:" in alias
        )
    return native_ids


def _shared_explicit_profile_aliases(group: list[dict[str, Any]]) -> set[str]:
    """Return exact platform-profile URL evidence shared by every Pool row.

    ``canonical_creator_aliases`` rejects video/story/system routes before it
    emits ``:url:`` aliases.  Requiring the intersection across every row is
    therefore materially stronger than a shared display name or imported
    handle, while still normalizing harmless host/query/profile-tab variants.
    """
    platforms = {
        canonical_identity_platform(row.get("platform")) for row in group
    } - {""}
    if len(platforms) != 1:
        return set()
    profile_aliases: list[set[str]] = []
    for row in group:
        aliases = {
            alias
            for alias in canonical_creator_aliases(
                {
                    "platform": row.get("platform"),
                    "profile_url": row.get("profile_url"),
                }
            )
            if ":url:" in alias
        }
        if not aliases:
            return set()
        profile_aliases.append(aliases)
    return set.intersection(*profile_aliases)
