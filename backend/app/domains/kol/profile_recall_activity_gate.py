"""Server-owned latest-video activity gate for Smart local recall.

The gate is deliberately split into two independent levels:

* ``known``  — the pool actually holds a latest-video timestamp for this
  creator.  ``age_days is None`` means "we never crawled it", which is a
  *data* gap, not a quality verdict.
* ``fresh``  — a timestamp exists and satisfies every freshness rule.

Only the *unknown* level may be deferred into a backfill bucket that fills
in strictly behind fully qualified candidates.  ``latest_video_stale`` /
``latest_video_in_future`` / ``latest_video_not_active_video`` /
``latest_video_identity_missing`` stay hard rejections, and the day
thresholds themselves are owned by the caller — nothing here relaxes them.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


UNKNOWN_ACTIVITY_DEFER = "defer"
UNKNOWN_ACTIVITY_REJECT = "reject"
UNKNOWN_ACTIVITY_MODES = (UNKNOWN_ACTIVITY_DEFER, UNKNOWN_ACTIVITY_REJECT)
UNKNOWN_ACTIVITY_POLICY_KEY = "unknown_video_activity"
UNKNOWN_ACTIVITY_REASON = "latest_video_unknown"
DEFERRED_ACTIVITY_STATUS = "activity_unknown_pending_fetch"
DEFERRED_SELECTION_TIER = "deferred_activity_unknown"
DEFERRED_SUPERSEDED_REASON = "duplicate_canonical_identity"
# Every gate a deferred candidate must still have passed.  ``activity`` is the
# one and only gate the deferral is allowed to leave open.
DEFERRED_PROOF_REQUIRED_GATES = (
    "account_quality",
    "followers",
    "market",
    "language",
    "profile_type",
    "platform",
    "relevance",
)
_FUTURE_TIMESTAMP_TOLERANCE = timedelta(minutes=5)
_ONLINE_ORIGIN_LANE = "online"
_VIDEO_IDENTITY_KEYS = ("content_url", "video_id")


def parse_evidence_datetime(value: Any) -> datetime | None:
    """Parse a stored evidence timestamp into an aware UTC datetime."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evaluate_activity(
    *,
    latest: Any,
    now: datetime,
    max_video_age_days: float,
    fresh_priority_days: float,
) -> dict[str, Any]:
    """Split the activity verdict into ``known`` and ``fresh`` levels.

    ``passed`` keeps the exact pre-existing meaning (fresh, active, auditable
    video identity).  ``known`` is the new, weaker fact that lets the caller
    tell "we have no data" apart from "the data says stale".
    """
    row = latest if isinstance(latest, dict) else {}
    posted_at = parse_evidence_datetime(row.get("posted_at"))
    future_timestamp = bool(posted_at and posted_at > now + _FUTURE_TIMESTAMP_TOLERANCE)
    age_days = (
        max(0.0, (now - posted_at).total_seconds() / 86_400.0) if posted_at else None
    )
    evidence_type = str(row.get("evidence_type") or "video").strip().lower()
    auditable_post = (
        evidence_type == "post" and row.get("platform") in {"x", "reddit"}
        and row.get("content_kind") == "post"
        and row.get("source") in {"platform_content_search", "account_recent_video", "provider_video_item"}
    )
    active_video = (evidence_type == "video" or auditable_post) and row.get("is_active") is not False
    identity = ""
    identity_kind = ""
    for key in _VIDEO_IDENTITY_KEYS:
        value = str(row.get(key) or "").strip()
        if value:
            identity = value
            identity_kind = key
            break

    known = age_days is not None
    fresh = bool(
        known
        and not future_timestamp
        and age_days <= max_video_age_days
        and active_video
        and identity
    )
    if not known:
        reason = UNKNOWN_ACTIVITY_REASON
    elif future_timestamp:
        reason = "latest_video_in_future"
    elif age_days > max_video_age_days:
        reason = "latest_video_stale"
    elif not active_video:
        reason = "latest_video_not_active_video"
    elif not identity:
        reason = "latest_video_identity_missing"
    else:
        reason = ""
    return {
        "posted_at": posted_at,
        "age_days": age_days,
        "future_timestamp": future_timestamp,
        "evidence_type": evidence_type,
        "active_video": active_video,
        "identity": identity,
        "identity_kind": identity_kind,
        "known": known,
        "fresh": fresh,
        "passed": fresh,
        "reason": reason,
        "fresh_priority": bool(fresh and age_days is not None and age_days <= fresh_priority_days),
        "source": row.get("source") or "vkpi_kol_video_evidence.posted_at",
    }


def unknown_activity_mode(policy: Any) -> str:
    """Resolve the live ``unknown_video_activity`` knob.

    This replaces the former ``allow_unknown_or_stale_video`` flag, which no
    caller ever read.  Two things are intentional here:

    * the knob can never speak for stale/future/inactive rows — its whole
      vocabulary is about the *unknown* bucket;
    * the online lane keeps hard rejection.  An online provider row with no
      video evidence is a stranger with zero history, not a pool member we
      can queue for a re-crawl, and that lane owns its own contract.
    """
    settings = policy if isinstance(policy, dict) else {}
    if str(settings.get("origin_lane") or "").strip().lower() == _ONLINE_ORIGIN_LANE:
        return UNKNOWN_ACTIVITY_REJECT
    mode = str(settings.get(UNKNOWN_ACTIVITY_POLICY_KEY) or "").strip().lower()
    return mode if mode in UNKNOWN_ACTIVITY_MODES else UNKNOWN_ACTIVITY_REJECT


def should_defer_activity(evaluation: Any, mode: str) -> bool:
    """True only for the "we never crawled this creator" bucket.

    Three independent locks, all of which must hold, so a stale row can not
    reach the deferred bucket through any future refactor: the resolved mode
    is ``defer``, the verdict is exactly ``latest_video_unknown``, and there
    is literally no timestamp to judge.
    """
    verdict = evaluation if isinstance(evaluation, dict) else {}
    return bool(
        mode == UNKNOWN_ACTIVITY_DEFER
        and verdict.get("reason") == UNKNOWN_ACTIVITY_REASON
        and verdict.get("known") is False
        and verdict.get("posted_at") is None
        and verdict.get("age_days") is None
        and verdict.get("passed") is not True
    )


def activity_gate_evidence(
    evaluation: dict[str, Any],
    *,
    maximum_age_days: float,
    deferred: bool,
) -> dict[str, Any]:
    """Build the auditable activity proof carried on every candidate."""
    age_days = evaluation.get("age_days")
    posted_at = evaluation.get("posted_at")
    return {
        "posted_at": posted_at.isoformat() if isinstance(posted_at, datetime) else None,
        "age_days": round(age_days, 3) if age_days is not None else None,
        "future_timestamp": bool(evaluation.get("future_timestamp")),
        "fresh_priority": bool(evaluation.get("fresh_priority")),
        "maximum_age_days": maximum_age_days,
        "evidence_type": evaluation.get("evidence_type") or None,
        "active_video": bool(evaluation.get("active_video")),
        "identity_kind": evaluation.get("identity_kind") or None,
        "identity": evaluation.get("identity") or None,
        "known": bool(evaluation.get("known")),
        "passed": bool(evaluation.get("passed")),
        "deferred": bool(deferred),
        "status": (
            "fresh"
            if evaluation.get("passed")
            else DEFERRED_ACTIVITY_STATUS
            if deferred
            else "rejected"
        ),
        "deferred_reason": UNKNOWN_ACTIVITY_REASON if deferred else None,
        "source": evaluation.get("source") or "vkpi_kol_video_evidence.posted_at",
    }


def mark_deferred_item(item: dict[str, Any]) -> dict[str, Any]:
    """Stamp an honest, non-progress label the UI can render verbatim."""
    item["selection_tier"] = DEFERRED_SELECTION_TIER
    item["activity_status"] = DEFERRED_ACTIVITY_STATUS
    item["activity_status_reason"] = UNKNOWN_ACTIVITY_REASON
    return item


def deferred_activity_proof(proof: Any) -> bool:
    """True only for a persisted proof that is *exactly* the unknown bucket.

    The selection boundary needs to tell "we never crawled this creator" apart
    from every other failing verdict using nothing but the stored proof.  Every
    gate other than ``activity`` must have passed, the activity block must say
    "never crawled" (unknown level, no timestamp, no age), and no rejection
    reason may be attached.  A stale / future / inactive / unauditable /
    duplicate row therefore can not reach a caller through this predicate, and
    the day thresholds are not consulted here at all — they already ran, and
    this reads their verdict rather than re-deciding it.
    """
    gate = proof if isinstance(proof, dict) else {}
    activity = gate.get("activity") if isinstance(gate.get("activity"), dict) else {}
    return bool(
        gate.get("deferred") is True
        and gate.get("passed") is not True
        and not list(gate.get("rejection_reasons") or [])
        and gate.get("deferred_reason") == UNKNOWN_ACTIVITY_REASON
        and activity.get("deferred") is True
        and activity.get("known") is False
        and activity.get("passed") is not True
        and activity.get("age_days") is None
        and not activity.get("posted_at")
        and activity.get("status") == DEFERRED_ACTIVITY_STATUS
        and activity.get("deferred_reason") == UNKNOWN_ACTIVITY_REASON
        and all(
            isinstance(gate.get(name), dict) and gate[name].get("passed") is True
            for name in DEFERRED_PROOF_REQUIRED_GATES
        )
    )


def _item_aliases(item: dict[str, Any]) -> set[str]:
    proof = item.get("qualification_evidence")
    aliases = proof.get("canonical_aliases") if isinstance(proof, dict) else None
    return {
        str(value or "").strip()
        for value in (aliases or [])
        if str(value or "").strip()
    }


def select_deferred_backfill(
    *,
    deferred_items: list[dict[str, Any]],
    qualified_aliases: set[str],
    capacity: int,
    sort_key: Callable[[dict[str, Any]], Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fill leftover target capacity, never widening it.

    Returns ``(selected, superseded)``.  ``superseded`` are unknown-activity
    rows for a creator who also showed up as a fully qualified candidate;
    they must not occupy a second slot.
    """
    available: list[dict[str, Any]] = []
    superseded: list[dict[str, Any]] = []
    for item in deferred_items:
        if _item_aliases(item).intersection(qualified_aliases):
            superseded.append(item)
        else:
            available.append(item)
    available.sort(key=sort_key, reverse=True)
    return available[: max(0, int(capacity))], superseded
