"""Pure read-time projection for visible KOL recall session rows."""
from __future__ import annotations

from typing import Any

from app.domains.kol.profile_recall_match_evidence import (
    candidate_set_distribution_from_items,
)
from app.domains.kol.search_sessions_attach import _safe_candidate_facets
from app.domains.kol.search_sessions_items import project_session_result_summary
from app.domains.kol.search_sessions_serde import _dict, _int_or_none, _text


def canonical_visible_recall(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project unique visible recall rows for read-time counts and facets."""

    canonical: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if _text(item.get("item_type")) != "recall_candidate":
            continue
        payload = _dict(item.get("payload"))
        pool_id = _int_or_none(item.get("kol_pool_id") or payload.get("kol_pool_id"))
        platform = _text(payload.get("platform")).lower()
        handle = _text(payload.get("handle") or payload.get("channel_name")).lstrip("@").lower()
        source_url = _text(item.get("source_url") or payload.get("profile_url")).lower()
        identity = (
            f"pool:{pool_id}" if pool_id
            else f"profile:{platform}:{handle}" if platform and handle
            else f"url:{source_url}" if source_url
            else f"item:{_int_or_none(item.get('id')) or len(canonical) + 1}"
        )
        if identity in seen:
            continue
        seen.add(identity)
        qualification_evidence = _dict(payload.get("qualification_evidence"))
        counts_toward_target: bool | None = None
        if isinstance(payload.get("counts_toward_target"), bool):
            counts_toward_target = payload["counts_toward_target"] is True
        elif isinstance(qualification_evidence.get("counts_toward_target"), bool):
            counts_toward_target = qualification_evidence["counts_toward_target"] is True
        elif isinstance(qualification_evidence.get("passed"), bool):
            growth_pass = payload.get("growth_qualification_pass")
            counts_toward_target = (
                qualification_evidence.get("passed") is True
                and qualification_evidence.get("deferred") is not True
                and (growth_pass is True if isinstance(growth_pass, bool) else True)
            )
        elif payload.get("growth_qualification_pass") is False:
            counts_toward_target = False
        canonical.append(
            {
                "kol_pool_id": pool_id,
                "bucket": (
                    "reviewer"
                    if _text(payload.get("bucket")) == "reviewer"
                    else "creator"
                ),
                "candidate_facets": _safe_candidate_facets(
                    payload.get("candidate_facets")
                ),
                "counts_toward_target": counts_toward_target,
            }
        )
    return canonical


def _project_local_qualification(
    local_qualification: dict[str, Any],
    canonical: list[dict[str, Any]],
) -> dict[str, Any]:
    visible_count = len(canonical)
    policy = _dict(local_qualification.get("policy"))
    target = _int_or_none(policy.get("target_count")) or 30
    explicit = [
        item.get("counts_toward_target")
        for item in canonical
        if isinstance(item.get("counts_toward_target"), bool)
    ]
    explicit_true = sum(value is True for value in explicit)
    unknown_visible = visible_count - len(explicit)
    recorded_qualified = _int_or_none(
        local_qualification.get("qualified_returned_count")
    )
    if recorded_qualified is None:
        recorded_qualified = _int_or_none(local_qualification.get("qualified_count"))
    legacy_remaining = max(0, (recorded_qualified or 0) - explicit_true)
    qualified_count = explicit_true + min(unknown_visible, legacy_remaining)
    local_qualification.update(
        {
            "status": "ready" if qualified_count >= target else "shortfall",
            "qualified_count": qualified_count,
            "returned_count": visible_count,
            "qualified_returned_count": qualified_count,
            "shortfall": max(0, target - qualified_count),
            "shortfall_reason": (
                "" if qualified_count >= target else "visible_qualified_candidates_exhausted"
            ),
        }
    )
    funnel = _dict(local_qualification.get("funnel"))
    funnel["qualified"] = qualified_count
    funnel["returned"] = visible_count
    local_qualification["funnel"] = funnel
    return local_qualification


def refresh_visible_recall_summary(
    session: dict[str, Any],
    items: list[dict[str, Any]],
) -> None:
    """Make a full session snapshot describe only recall cards still visible."""

    summary = _dict(session.get("result_summary"))
    recall_snapshot_complete = (
        summary.get("recall_snapshot_attached") is True
        or _text(summary.get("kind")) == "kol_recall"
        or any(_text(item.get("item_type")) == "recall_candidate" for item in items)
    )
    summary["items_snapshot_complete"] = True
    summary["recall_snapshot_complete"] = recall_snapshot_complete
    if recall_snapshot_complete:
        canonical = canonical_visible_recall(items)
        creator_count = sum(1 for item in canonical if item["bucket"] == "creator")
        diagnostics = _dict(summary.get("diagnostics"))
        diagnostics.update(
            {
                "returned_count": len(canonical),
                "creator_returned": creator_count,
                "reviewer_returned": len(canonical) - creator_count,
            }
        )
        summary["diagnostics"] = diagnostics
        summary["match_status"] = "matched" if canonical else "empty"
        summary["candidate_set_distribution"] = candidate_set_distribution_from_items(
            canonical
        )
        local_qualification = _dict(summary.get("local_qualification"))
        if _text(local_qualification.get("schema")) == "smart_local_qualified_v2":
            summary["local_qualification"] = _project_local_qualification(
                local_qualification,
                canonical,
            )
    summary = project_session_result_summary(
        summary,
        items,
        status=_text(session.get("status")),
    )
    session["result_summary"] = summary
    session["items_snapshot_complete"] = True
    session["recall_snapshot_complete"] = recall_snapshot_complete
    online = _dict(summary.get("online_qualification"))
    session["online_snapshot_complete"] = bool(
        _text(online.get("schema")) == "smart_online_net_new_qualified_v1"
        and online.get("server_owned") is True
        and online.get("snapshot_complete") is True
    )


__all__ = ["canonical_visible_recall", "refresh_visible_recall_summary"]
