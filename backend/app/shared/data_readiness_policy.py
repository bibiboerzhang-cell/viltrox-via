"""Dependency-free policy primitives for evidence-readiness claims.

This module owns only deterministic value normalization and serialization.  It
must stay independent from database, logging, service, and business-domain
modules so any domain can evaluate the same readiness contract without taking
ownership of another domain's runtime dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable


READINESS_VERSION = "market_brain_data_readiness_v1"
DEFAULT_MAX_AGE_DAYS = 30


def _parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class DataRequirement:
    key: str
    observed: int
    minimum: int
    freshest_at: Any = None
    max_age_days: int | None = None
    label: str = ""


@dataclass(frozen=True)
class DataReadiness:
    status: str
    ready: bool
    claimable: bool
    claim_level: str
    checks: dict[str, dict[str, Any]]
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": READINESS_VERSION,
            "status": self.status,
            "ready": self.ready,
            "claimable": self.claimable,
            "claim_level": self.claim_level,
            "checks": self.checks,
            "blockers": list(self.blockers),
            "note": (
                "Effectiveness claims require every sample and freshness check to pass; "
                "otherwise values are descriptive observations only."
            ),
        }


def evaluate_requirements(
    requirements: Iterable[DataRequirement],
    *,
    now: datetime | None = None,
) -> DataReadiness:
    """Evaluate sample and freshness requirements without touching a database."""
    current = _now(now)
    checks: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    has_stale = False
    has_insufficient = False

    for requirement in requirements:
        observed = max(0, int(requirement.observed or 0))
        minimum = max(0, int(requirement.minimum or 0))
        max_age_days = (
            max(0, int(requirement.max_age_days))
            if requirement.max_age_days is not None
            else None
        )
        parsed = _parse_ts(requirement.freshest_at)
        age_days = None
        freshness_status = "not_required"
        if max_age_days is not None:
            if parsed is None:
                freshness_status = "unknown"
            else:
                age_days = round(max(0.0, (current - parsed).total_seconds() / 86400.0), 2)
                freshness_status = "fresh" if age_days <= max_age_days else "stale"

        if observed < minimum:
            status = "insufficient"
            has_insufficient = True
            blockers.append(f"{requirement.key}:sample<{minimum}")
        elif freshness_status == "unknown":
            status = "freshness_unknown"
            has_insufficient = True
            blockers.append(f"{requirement.key}:freshness_unknown")
        elif freshness_status == "stale":
            status = "stale"
            has_stale = True
            blockers.append(f"{requirement.key}:stale>{max_age_days}d")
        else:
            status = "ready"

        checks[requirement.key] = {
            "label": requirement.label or requirement.key,
            "status": status,
            "observed": observed,
            "minimum": minimum,
            "freshest_at": parsed.isoformat() if parsed is not None else None,
            "age_days": age_days,
            "max_age_days": max_age_days,
            "sample_ready": observed >= minimum,
            "freshness_status": freshness_status,
        }

    ready = bool(checks) and not blockers
    if ready:
        status = "ready"
    elif has_insufficient:
        status = "insufficient"
    elif has_stale:
        status = "stale"
    else:
        status = "insufficient"
    return DataReadiness(
        status=status,
        ready=ready,
        claimable=ready,
        claim_level="validated" if ready else "descriptive_only",
        checks=checks,
        blockers=tuple(blockers),
    )


def build_source_readiness(
    source_key: str,
    *,
    observed: int,
    freshest_at: Any,
    minimum: int = 1,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    return evaluate_requirements(
        [
            DataRequirement(
                key=source_key,
                label=f"{source_key} observed source rows",
                observed=observed,
                minimum=minimum,
                freshest_at=freshest_at,
                max_age_days=max_age_days,
            )
        ],
        now=now,
    ).to_dict()


__all__ = [
    "DataRequirement",
    "DataReadiness",
    "evaluate_requirements",
    "build_source_readiness",
    "READINESS_VERSION",
    "DEFAULT_MAX_AGE_DAYS",
]
