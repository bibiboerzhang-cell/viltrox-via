"""Read-only validation for raw external market-signal artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ARTIFACT_MODE = "market_external_signal_smoke_v0"
ARTIFACT_PATTERN = "*market-external-signal-smoke-v0.json"
MAX_AGE_DAYS = 7
TARGET_SOURCES = 9
TARGET_ITEMS = 36
_MAX_FUTURE_SKEW_SECONDS = 300
_REQUIRED_TRUE_FLAGS = ("provider_calls", "external_http_calls")
_REQUIRED_FALSE_FLAGS = (
    "llm_calls",
    "gemini_calls",
    "write_db",
    "sync_triggered",
    "task_enqueued",
)
_REQUIRED_CHECKS = (
    "no_db_write",
    "no_sync_triggered",
    "no_llm_call",
    "allowlisted_sources_only",
    "live_fetch_returned_items",
)
_DEMO_MARKERS = {"demo", "fixture", "sample", "synthetic", "test"}
_POLICY = {
    "read_only": True,
    "counts_as_raw_market_source_only": True,
    "counts_as_promoted_competitor_signal": False,
    "counts_as_market_mention": False,
    "counts_as_outcome": False,
}


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _coverage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, float(numerator) / float(denominator)))


def _ramp(value: int, target: int) -> float:
    return _coverage(value, target)


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _is_http_url(value: Any) -> bool:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _empty_observation(
    status: str,
    blockers: list[str],
    *,
    artifact_path: str | Path | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "validated": False,
        "observed": False,
        "evidence_score": 0.0,
        "artifact_path": str(Path(artifact_path).resolve()) if artifact_path else None,
        "generated_at": None,
        "age_days": None,
        "max_age_days": MAX_AGE_DAYS,
        "sources_requested": 0,
        "sources_fetched": 0,
        "items_loaded": 0,
        "source_fetch_coverage": 0.0,
        "source_url_coverage": 0.0,
        "source_provenance_coverage": 0.0,
        "item_url_coverage": 0.0,
        "item_provenance_coverage": 0.0,
        "blockers": blockers,
        "policy": dict(_POLICY),
    }


def _is_demo_artifact(payload: dict[str, Any], path: Path | None) -> bool:
    if any(payload.get(key) is True for key in ("demo", "is_demo", "synthetic", "is_synthetic", "fixture")):
        return True
    for key in ("environment", "data_origin", "run_type"):
        if str(payload.get(key) or "").strip().lower() in _DEMO_MARKERS:
            return True
    if path:
        name_tokens = set(path.name.lower().replace(".", "-").replace("_", "-").split("-"))
        if name_tokens & _DEMO_MARKERS:
            return True
    return False


def _append_contract_blockers(
    payload: dict[str, Any],
    path: Path | None,
    blockers: list[str],
) -> None:
    if payload.get("mode") != ARTIFACT_MODE:
        blockers.append("contract:mode")
    if payload.get("passed") is not True:
        blockers.append("contract:passed")
    if _is_demo_artifact(payload, path):
        blockers.append("contract:demo_or_synthetic")
    for field in _REQUIRED_TRUE_FLAGS:
        if payload.get(field) is not True:
            blockers.append(f"contract:{field}")
    for field in _REQUIRED_FALSE_FLAGS:
        if payload.get(field) is not False:
            blockers.append(f"side_effect:{field}")

    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    for field in _REQUIRED_CHECKS:
        if checks.get(field) is not True:
            blockers.append(f"contract_check:{field}")
    if payload.get("errors") not in (None, []):
        blockers.append("contract:errors_present")


def _timestamp_observation(
    payload: dict[str, Any],
    current: datetime,
    max_age_days: int,
    blockers: list[str],
) -> tuple[datetime | None, float | None]:
    generated_at = _parse_timestamp(payload.get("generated_at"))
    age_days: float | None = None
    if generated_at is None:
        blockers.append("generated_at:invalid")
    else:
        age_seconds = (current - generated_at).total_seconds()
        age_days = round(age_seconds / 86400.0, 3)
        if age_seconds < -_MAX_FUTURE_SKEW_SECONDS:
            blockers.append("generated_at:future")
        elif age_seconds > max(0, int(max_age_days)) * 86400:
            blockers.append(f"generated_at:stale>{max(0, int(max_age_days))}d")
    return generated_at, age_days


def _summary_counts(
    payload: dict[str, Any],
    blockers: list[str],
) -> tuple[int, int, int]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    sources_requested = _integer(summary.get("sources_requested"))
    sources_fetched = _integer(summary.get("sources_fetched"))
    items_loaded = _integer(summary.get("items_loaded"))
    if sources_requested is None or sources_requested <= 0:
        blockers.append("sources_requested:nonpositive")
    if sources_fetched is None or sources_fetched <= 0:
        blockers.append("sources_fetched:nonpositive")
    if items_loaded is None or items_loaded <= 0:
        blockers.append("items_loaded:nonpositive")
    sources_requested = max(0, int(sources_requested or 0))
    sources_fetched = max(0, int(sources_fetched or 0))
    items_loaded = max(0, int(items_loaded or 0))
    if sources_fetched > sources_requested:
        blockers.append("sources_fetched:exceeds_requested")
    return sources_requested, sources_fetched, items_loaded


def _dict_rows(
    payload: dict[str, Any],
    field: str,
    malformed_blocker: str,
    blockers: list[str],
) -> list[dict[str, Any]]:
    value = payload.get(field)
    rows = value if isinstance(value, list) else []
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in rows):
        blockers.append(malformed_blocker)
        rows = [row for row in rows if isinstance(row, dict)]
    return rows


def _fetched_source_rows(source_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in source_rows if str(row.get("status") or "").lower() == "fetched"]


def _append_row_count_blockers(
    source_rows: list[dict[str, Any]],
    fetched_rows: list[dict[str, Any]],
    item_rows: list[dict[str, Any]],
    sources_requested: int,
    sources_fetched: int,
    items_loaded: int,
    blockers: list[str],
) -> None:
    if len(source_rows) != sources_requested:
        blockers.append("source_statuses:count_mismatch")
    if len(fetched_rows) != sources_fetched:
        blockers.append("source_statuses:fetched_count_mismatch")
    if len(item_rows) != items_loaded:
        blockers.append("items:count_mismatch")


def _source_evidence(
    fetched_rows: list[dict[str, Any]],
    blockers: list[str],
) -> tuple[set[str], int, int]:
    source_keys = [str(row.get("source_key") or "").strip() for row in fetched_rows]
    known_source_keys = {key for key in source_keys if key}
    if len(known_source_keys) != len(fetched_rows):
        blockers.append("source_statuses:source_key_not_unique")
    source_url_count = sum(1 for row in fetched_rows if _is_http_url(row.get("url")))
    source_provenance_count = sum(
        1
        for row in fetched_rows
        if _has_text(row.get("source_key"))
        and _has_text(row.get("provider"))
        and _has_text(row.get("source_type"))
        and row.get("allowlisted") is True
    )
    return known_source_keys, source_url_count, source_provenance_count


def _item_evidence(
    item_rows: list[dict[str, Any]],
    known_source_keys: set[str],
    blockers: list[str],
) -> tuple[int, int]:
    item_url_count = sum(1 for row in item_rows if _is_http_url(row.get("source_url")))
    item_provenance_count = sum(
        1
        for row in item_rows
        if _has_text(row.get("source_uid"))
        and _has_text(row.get("provider"))
        and _has_text(row.get("source_type"))
        and str(row.get("source_key") or "").strip() in known_source_keys
    )
    item_uids = {str(row.get("source_uid") or "").strip() for row in item_rows if _has_text(row.get("source_uid"))}
    if len(item_uids) != len(item_rows):
        blockers.append("items:source_uid_not_unique")
    return item_url_count, item_provenance_count


def _coverage_observation(
    sources_fetched: int,
    sources_requested: int,
    fetched_rows: list[dict[str, Any]],
    item_rows: list[dict[str, Any]],
    source_url_count: int,
    source_provenance_count: int,
    item_url_count: int,
    item_provenance_count: int,
    blockers: list[str],
) -> tuple[float, float, float, float, float]:
    source_fetch_coverage = _coverage(sources_fetched, sources_requested)
    source_url_coverage = _coverage(source_url_count, len(fetched_rows))
    source_provenance_coverage = _coverage(source_provenance_count, len(fetched_rows))
    item_url_coverage = _coverage(item_url_count, len(item_rows))
    item_provenance_coverage = _coverage(item_provenance_count, len(item_rows))
    for field, value in (
        ("source_url", source_url_coverage),
        ("source_provenance", source_provenance_coverage),
        ("item_url", item_url_coverage),
        ("item_provenance", item_provenance_coverage),
    ):
        if value < 1.0:
            blockers.append(f"coverage:{field}<1")
    return (
        source_fetch_coverage,
        source_url_coverage,
        source_provenance_coverage,
        item_url_coverage,
        item_provenance_coverage,
    )


def _validation_summary(
    blockers: list[str],
    sources_fetched: int,
    items_loaded: int,
    source_fetch_coverage: float,
    max_age_days: int,
) -> tuple[str, bool, float]:
    validated = not blockers
    evidence_score = 0.0
    if validated:
        evidence_score = (
            0.40 * _ramp(sources_fetched, TARGET_SOURCES)
            + 0.40 * _ramp(items_loaded, TARGET_ITEMS)
            + 0.20 * source_fetch_coverage
        )
    status = "validated" if validated else "rejected"
    if not validated and blockers == [f"generated_at:stale>{max(0, int(max_age_days))}d"]:
        status = "stale"
    return status, validated, evidence_score


def _artifact_observation(
    *,
    status: str,
    validated: bool,
    evidence_score: float,
    path: Path | None,
    generated_at: datetime | None,
    age_days: float | None,
    max_age_days: int,
    sources_requested: int,
    sources_fetched: int,
    items_loaded: int,
    source_fetch_coverage: float,
    source_url_coverage: float,
    source_provenance_coverage: float,
    item_url_coverage: float,
    item_provenance_coverage: float,
    blockers: list[str],
) -> dict[str, Any]:
    return {
        "status": status,
        "validated": validated,
        "observed": validated,
        "evidence_score": round(evidence_score, 3),
        "artifact_path": str(path.resolve()) if path else None,
        "generated_at": generated_at.isoformat() if generated_at else None,
        "age_days": age_days,
        "max_age_days": max(0, int(max_age_days)),
        "sources_requested": sources_requested,
        "sources_fetched": sources_fetched,
        "items_loaded": items_loaded,
        "source_fetch_coverage": round(source_fetch_coverage, 3),
        "source_url_coverage": round(source_url_coverage, 3),
        "source_provenance_coverage": round(source_provenance_coverage, 3),
        "item_url_coverage": round(item_url_coverage, 3),
        "item_provenance_coverage": round(item_provenance_coverage, 3),
        "blockers": blockers,
        "policy": dict(_POLICY),
    }


def validate_raw_market_source_artifact(
    payload: Any,
    *,
    artifact_path: str | Path | None = None,
    now: datetime | None = None,
    max_age_days: int = MAX_AGE_DAYS,
) -> dict[str, Any]:
    """Validate one artifact without mutating the artifact or any database."""
    path = Path(artifact_path) if artifact_path else None
    if not isinstance(payload, dict):
        return _empty_observation("rejected", ["payload_not_object"], artifact_path=path)

    blockers: list[str] = []
    _append_contract_blockers(payload, path, blockers)
    generated_at, age_days = _timestamp_observation(payload, _now(now), max_age_days, blockers)
    sources_requested, sources_fetched, items_loaded = _summary_counts(payload, blockers)
    source_rows = _dict_rows(payload, "source_statuses", "source_statuses:malformed", blockers)
    item_rows = _dict_rows(payload, "items", "items:malformed", blockers)
    fetched_rows = _fetched_source_rows(source_rows)
    _append_row_count_blockers(
        source_rows,
        fetched_rows,
        item_rows,
        sources_requested,
        sources_fetched,
        items_loaded,
        blockers,
    )
    known_source_keys, source_url_count, source_provenance_count = _source_evidence(
        fetched_rows,
        blockers,
    )
    item_url_count, item_provenance_count = _item_evidence(item_rows, known_source_keys, blockers)
    (
        source_fetch_coverage,
        source_url_coverage,
        source_provenance_coverage,
        item_url_coverage,
        item_provenance_coverage,
    ) = _coverage_observation(
        sources_fetched,
        sources_requested,
        fetched_rows,
        item_rows,
        source_url_count,
        source_provenance_count,
        item_url_count,
        item_provenance_count,
        blockers,
    )
    status, validated, evidence_score = _validation_summary(
        blockers,
        sources_fetched,
        items_loaded,
        source_fetch_coverage,
        max_age_days,
    )
    return _artifact_observation(
        status=status,
        validated=validated,
        evidence_score=evidence_score,
        path=path,
        generated_at=generated_at,
        age_days=age_days,
        max_age_days=max_age_days,
        sources_requested=sources_requested,
        sources_fetched=sources_fetched,
        items_loaded=items_loaded,
        source_fetch_coverage=source_fetch_coverage,
        source_url_coverage=source_url_coverage,
        source_provenance_coverage=source_provenance_coverage,
        item_url_coverage=item_url_coverage,
        item_provenance_coverage=item_provenance_coverage,
        blockers=blockers,
    )


def latest_raw_market_source_observation(
    ops_dir: str | Path = "runtime/ops",
    *,
    now: datetime | None = None,
    pattern: str = ARTIFACT_PATTERN,
) -> dict[str, Any]:
    """Return the newest generated artifact that passes the strict raw-source gate."""
    root = Path(ops_dir)
    if not root.exists() or not root.is_dir():
        result = _empty_observation("missing", ["artifact:missing"])
        result.update({"candidates_scanned": 0, "rejected_candidates": 0})
        return result

    candidates = sorted(
        (path for path in root.glob(pattern) if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    if not candidates:
        result = _empty_observation("missing", ["artifact:missing"])
        result.update({"candidates_scanned": 0, "rejected_candidates": 0})
        return result

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            rejected.append(_empty_observation("rejected", ["artifact:malformed_json"], artifact_path=path))
            continue
        observation = validate_raw_market_source_artifact(payload, artifact_path=path, now=now)
        (accepted if observation["validated"] else rejected).append(observation)

    if accepted:
        result = max(
            accepted,
            key=lambda item: (
                _parse_timestamp(item.get("generated_at"))
                or datetime.min.replace(tzinfo=timezone.utc),
                item.get("artifact_path") or "",
            ),
        )
    else:
        result = rejected[0]
    result = dict(result)
    result.update(
        {
            "candidates_scanned": len(candidates),
            "rejected_candidates": len(rejected),
        }
    )
    return result


__all__ = [
    "ARTIFACT_MODE",
    "ARTIFACT_PATTERN",
    "MAX_AGE_DAYS",
    "latest_raw_market_source_observation",
    "validate_raw_market_source_artifact",
]
