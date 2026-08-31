"""Policy assembly helpers for local recall qualification."""
from __future__ import annotations

from typing import Any, Callable, Iterable


def normalized_excluded_identities(*groups: Iterable[Any] | None) -> set[str]:
    """Normalize every configured identity group into one non-empty set."""
    identities: set[str] = set()
    for group in groups:
        identities.update(
            str(value or "").strip()
            for value in (group or set())
            if str(value or "").strip()
        )
    return identities


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build_gate_policy(
    policy: dict[str, Any],
    *,
    now: Any,
    excluded_identities: set[str],
    excluded_identity_reason: str,
    policy_factory: Callable[..., Any],
    effective_follower_filter: Callable[[dict[str, Any]], dict[str, Any]],
    unknown_activity_mode: Callable[[dict[str, Any]], str],
    maximum_video_age_days: int,
    fresh_priority_days: int,
    gate_schema: str,
) -> tuple[Any, str]:
    """Build the immutable gate policy while keeping runtime factories injectable."""
    target_platforms = set(policy.get("platforms") or [])
    target_languages = set(policy.get("languages") or [])
    target_profile_types = set(policy.get("profile_types") or [])
    operator_filters = _mapping(policy.get("operator_filters"))
    language_filter = _mapping(operator_filters.get("languages"))
    profile_type_filter = _mapping(operator_filters.get("profile_types"))
    unknown_activity = unknown_activity_mode(policy)
    gate_policy = policy_factory(
        now=now,
        target_market=str(policy.get("market") or ""),
        target_platforms=frozenset(target_platforms),
        target_languages=frozenset(target_languages),
        target_profile_types=frozenset(target_profile_types),
        follower_filter=dict(effective_follower_filter(policy)),
        language_requested=bool(language_filter.get("requested") or target_languages),
        profile_type_requested=bool(profile_type_filter.get("requested") or target_profile_types),
        invalid_languages=tuple(language_filter.get("invalid") or []),
        invalid_profile_types=tuple(profile_type_filter.get("invalid") or []),
        evidence_sources=dict(_mapping(policy.get("evidence_sources"))),
        unknown_activity=unknown_activity,
        excluded_identities=frozenset(excluded_identities),
        excluded_identity_reason=excluded_identity_reason,
        excluded_account_types=tuple(policy.get("excluded_account_types") or []),
        require_trusted_market=policy.get("require_trusted_market") is True,
        max_video_age_days=maximum_video_age_days,
        fresh_priority_days=fresh_priority_days,
        gate_schema=gate_schema,
    )
    return gate_policy, unknown_activity
