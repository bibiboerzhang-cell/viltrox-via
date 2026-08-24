"""Canonical creator identity projection for search-session cards."""
from __future__ import annotations

from typing import Any

from app.domains.kol.identity import (
    YOUTUBE_CHANNEL_ID_RE,
    canonical_creator_aliases,
    canonical_creator_key,
)
from app.domains.kol.search_sessions_serde import (
    _dict,
    _float_or_none,
    _int_or_none,
    _text,
)


_CREATOR_ITEM_LANES = {
    "recall_candidate": "recall",
    "online_qualified_candidate": "online",
    "new_creator": "discovery",
    "existing_kol": "discovery",
}
_CREATOR_ITEM_PREFERENCE = {
    "existing_kol": 3,
    "online_qualified_candidate": 2,
    "recall_candidate": 2,
    "new_creator": 1,
}

# Read-time-only evidence projected from the exact linked Pool row.  This key
# must be consumed and removed by the account display gate before any session
# item is returned to a caller; historical payloads are never rewritten.
POOL_ACCOUNT_GATE_BIO_FIELD = "_pool_account_gate_bio"


def _session_creator_probe(item: dict[str, Any]) -> dict[str, Any]:
    payload = _dict(item.get("payload"))
    return {
        **payload,
        "platform": payload.get("platform") or item.get("platform"),
        "kol_pool_id": item.get("kol_pool_id") or payload.get("kol_pool_id"),
        "history_kol_pool_id": payload.get("history_kol_pool_id"),
        "profile_url": payload.get("profile_url") or item.get("source_url"),
        "source_url": item.get("source_url") or payload.get("source_url"),
        "historical_match": payload.get("historical_match"),
        "bio": (
            payload.get("bio")
            or payload.get("description")
            or payload.get(POOL_ACCOUNT_GATE_BIO_FIELD)
        ),
    }


def _canonical_session_dedupe_key(item: dict[str, Any]) -> str:
    lane = _CREATOR_ITEM_LANES.get(_text(item.get("item_type")))
    probe = _session_creator_probe(item)
    pool_id = _int_or_none(probe.get("kol_pool_id"))
    # ``recall:<pool_id>`` is a persisted/public snapshot contract. Canonical
    # aliases still drive the internal fold, but must not change that key to
    # the incompatible ``recall:pool:<pool_id>`` shape.
    if lane == "recall" and pool_id:
        return f"recall:{pool_id}"
    canonical = f"pool:{pool_id}" if pool_id else canonical_creator_key(probe)
    return f"{lane}:{canonical}" if lane and canonical else ""


def _merge_session_candidate(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge two observed shapes without inventing fields or scores."""
    existing_type = _text(existing.get("item_type"))
    incoming_type = _text(incoming.get("item_type"))
    existing_preference = _CREATOR_ITEM_PREFERENCE.get(existing_type, 0)
    incoming_preference = _CREATOR_ITEM_PREFERENCE.get(incoming_type, 0)
    incoming_wins = incoming_preference > existing_preference
    if incoming_preference == existing_preference:
        incoming_wins = _session_handle_quality(incoming) > _session_handle_quality(existing)
    winner = dict(incoming if incoming_wins else existing)
    other = existing if incoming_wins else incoming
    winner_payload = _dict(winner.get("payload")).copy()
    other_payload = _dict(other.get("payload"))
    for key, value in other_payload.items():
        if winner_payload.get(key) in (None, "", [], {}):
            winner_payload[key] = value
    _prefer_usable_avatar(winner_payload, other_payload)
    winner["payload"] = winner_payload
    ranks = [
        value
        for value in (_int_or_none(existing.get("rank")), _int_or_none(incoming.get("rank")))
        if value is not None
    ]
    if ranks:
        winner["rank"] = min(ranks)
    scores = [
        value
        for value in (_float_or_none(existing.get("score")), _float_or_none(incoming.get("score")))
        if value is not None
    ]
    if scores:
        winner["score"] = max(scores)
    if not _int_or_none(winner.get("kol_pool_id")):
        winner["kol_pool_id"] = _int_or_none(other.get("kol_pool_id"))
    if not _text(winner.get("source_url")):
        winner["source_url"] = other.get("source_url")
    return winner


def _prefer_usable_avatar(
    winner_payload: dict[str, Any],
    other_payload: dict[str, Any],
) -> None:
    """Keep stronger profile-avatar evidence when two creator cards fold."""
    from app.services.intelligence.account_scan_helpers import _avatar_url_policy

    priority = {"durable": 3, "ephemeral": 2}

    def candidate(payload: dict[str, Any]) -> tuple[int, str, str]:
        declared = _text(payload.get("avatar_url_status")).lower()
        url, status = _avatar_url_policy(payload.get("avatar_url"))
        if declared in {"expired", "invalid", "missing"}:
            return 0, "", declared
        if not url:
            return 0, "", status
        return priority.get(status, 0), url, status

    winner_avatar = candidate(winner_payload)
    other_avatar = candidate(other_payload)
    if other_avatar[0] <= winner_avatar[0]:
        return
    winner_payload["avatar_url"] = other_avatar[1]
    winner_payload["avatar_url_status"] = other_avatar[2]


def _session_handle_quality(item: dict[str, Any]) -> int:
    probe = _session_creator_probe(item)
    handle = _text(probe.get("handle")).lstrip("@")
    if not handle:
        return 0
    if (
        _text(probe.get("platform")).lower() in {"youtube", "yt"}
        and YOUTUBE_CHANNEL_ID_RE.fullmatch(handle)
    ):
        return 1
    return 2


def canonicalize_session_creator_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold creator cards by stable-alias intersection within each UI lane."""
    pending = [dict(item) for item in items]
    while True:
        output: list[dict[str, Any]] = []
        groups: list[tuple[str, set[str], int]] = []
        fallback_indexes: dict[tuple[str, str], int] = {}
        for item in pending:
            lane = _CREATOR_ITEM_LANES.get(_text(item.get("item_type")))
            if not lane:
                output.append(item)
                continue
            probe = _session_creator_probe(item)
            aliases = canonical_creator_aliases(probe)
            pool_id = _int_or_none(probe.get("kol_pool_id"))
            if pool_id:
                aliases.add(f"pool:{pool_id}")
            match_index: int | None = None
            if aliases:
                for group_lane, group_aliases, output_index in groups:
                    if group_lane == lane and aliases.intersection(group_aliases):
                        match_index = output_index
                        group_aliases.update(aliases)
                        break
            else:
                fallback = _text(item.get("dedupe_key")) or f"item:{_text(item.get('id'))}"
                match_index = fallback_indexes.get((lane, fallback))
            if match_index is None:
                output_index = len(output)
                output.append(item)
                if aliases:
                    groups.append((lane, set(aliases), output_index))
                else:
                    fallback_indexes[(lane, fallback)] = output_index
                continue
            output[match_index] = _merge_session_candidate(output[match_index], item)
        if len(output) == len(pending):
            for item in output:
                canonical_key = _canonical_session_dedupe_key(item)
                if canonical_key:
                    item["dedupe_key"] = canonical_key
            return output
        pending = output
