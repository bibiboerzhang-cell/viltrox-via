"""Row projections for the legacy market-observation schema."""
from __future__ import annotations

from typing import Any, Callable, Iterable


def project_legacy_observations(
    rows: Iterable[Any],
    *,
    impact: str | None,
    decode_json: Callable[[Any, Any], Any],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        observations.append(
            {
                "id": item["id"],
                "observed_at": item.get("observed_at") or "",
                "event_kind": item.get("observation_type") or "",
                "event_title": item.get("summary") or "",
                "impact": impact or "neutral",
                "source_url": "",
                "notes": item.get("summary") or "",
                "source_platform": item.get("source_platform") or "",
                "subject_type": item.get("subject_type") or "",
                "subject_key": item.get("subject_key") or "",
                "metrics": decode_json(item.get("metrics_json"), {}),
                "evidence": decode_json(item.get("evidence_json"), []),
                "region_code": item.get("region_code") or "",
            }
        )
    return observations


__all__ = ["project_legacy_observations"]
