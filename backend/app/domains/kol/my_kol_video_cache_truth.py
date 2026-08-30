"""Canonical/legacy cache truth for MY KOL video read projections.

This module is intentionally read-only.  Historical paid rows remain in
``vkpi_analysis_cache`` and can still be described by callers, but only rows
that pass the shared final-v1 proof gate count as completed analysis.
"""
from __future__ import annotations

from typing import Any, Iterable

from app.core.video_analysis_contract import FINAL_V1_DERIVE_METHOD
from app.domains.analysis.cache_reuse import canonical_final_v1_cache_reuse


_CACHE_REQUIRED_COLUMNS = frozenset(
    {"target_type", "target_id", "derive_method", "status", "result"}
)
_CACHE_PROJECTION = (
    "id",
    "target_type",
    "target_id",
    "derive_method",
    "model",
    "prompt_version",
    "status",
    "result",
    "updated_at",
)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _classified_rows(rows: Iterable[Any], *, derive_method: str) -> dict[int, dict[str, Any]]:
    classified: dict[int, dict[str, Any]] = {}
    for raw in rows:
        item = dict(raw)
        evidence_id = _int(item.get("target_id"))
        raw_status = str(item.get("status") or "").strip().lower()
        if evidence_id <= 0 or raw_status not in {"ready", "stale"}:
            continue
        item["raw_status"] = raw_status
        if derive_method == FINAL_V1_DERIVE_METHOD and raw_status == "ready":
            decision = canonical_final_v1_cache_reuse(
                item,
                target_type="video",
                target_id=str(evidence_id),
                derive_method=FINAL_V1_DERIVE_METHOD,
            )
            item.update(
                cache_reuse_status=decision["cache_reuse_status"],
                revalidation_required=decision["revalidation_required"],
                claim_status=decision["claim_status"],
                cache_reuse_reasons=decision["reasons"],
            )
            item["status"] = "ready" if decision["reusable"] else "legacy_unverified"
        else:
            item.update(
                cache_reuse_status="canonical" if raw_status == "ready" else "stale",
                revalidation_required=False,
                claim_status="descriptive_only",
                cache_reuse_reasons=[],
            )
        classified[evidence_id] = item
    return classified


def _analysis_cache_columns(conn: Any) -> set[str]:
    """Return the real cache schema without speculatively selecting columns.

    A zero-row SELECT exposes cursor metadata in SQLite, Postgres, and the
    compatibility connection without a backend-specific PRAGMA or a failing
    speculative projection.  Errors are intentionally not caught here: a
    broken connection or denied schema read is operational failure, not
    evidence that optional columns are absent.
    """

    cursor = conn.execute("SELECT * FROM vkpi_analysis_cache WHERE 1=0")
    description = getattr(cursor, "description", None) or []
    columns = {
        str(column[0] if isinstance(column, (tuple, list)) else getattr(column, "name", ""))
        for column in description
    }
    if not columns:
        raise RuntimeError("vkpi_analysis_cache_schema_unavailable")
    return {column for column in columns if column}


def _cache_projection(columns: set[str]) -> str:
    """Prefer production columns; project absent legacy fields as NULL."""

    missing_required = sorted(_CACHE_REQUIRED_COLUMNS.difference(columns))
    if missing_required:
        raise RuntimeError(
            "vkpi_analysis_cache_missing_required_columns:"
            + ",".join(missing_required)
        )
    return ", ".join(
        column if column in columns else f"NULL AS {column}"
        for column in _CACHE_PROJECTION
    )


def analysis_caches_for_evidence(
    conn: Any,
    evidence_ids: Iterable[int],
    *,
    derive_method: str = FINAL_V1_DERIVE_METHOD,
) -> dict[int, dict[str, Any]]:
    """Read and classify the latest cache row for each bounded evidence id."""
    ids = list(dict.fromkeys(_int(value) for value in evidence_ids if _int(value) > 0))
    if not ids:
        return {}
    columns = _analysis_cache_columns(conn)
    projection = _cache_projection(columns)
    placeholders = ",".join("?" for _ in ids)
    order_by = [
        f"{column} ASC"
        for column in ("updated_at", "id")
        if column in columns
    ] or ["target_id ASC"]
    rows = conn.execute(
        f"""
        SELECT {projection}
        FROM vkpi_analysis_cache
        WHERE target_type='video'
          AND target_id IN ({placeholders})
          AND derive_method=?
        ORDER BY {", ".join(order_by)}
        """,
        (*(str(value) for value in ids), str(derive_method)),
    ).fetchall()
    return _classified_rows(rows, derive_method=str(derive_method))


def analysis_cache_summary_for_kol(conn: Any, kol_pool_id: int) -> dict[str, int]:
    """Count canonical and legacy final-v1 rows without trusting SQL status."""
    rows = conn.execute(
        """
        SELECT c.id, c.target_type, c.target_id, c.derive_method, c.model,
               c.prompt_version, c.status, c.result, c.updated_at
        FROM vkpi_analysis_cache c
        JOIN vkpi_kol_video_evidence e ON c.target_id=CAST(e.id AS TEXT)
        WHERE c.target_type='video'
          AND c.derive_method=?
          AND c.status IN ('ready', 'stale')
          AND e.kol_pool_id=?
          AND COALESCE(e.is_active, TRUE) != FALSE
          AND COALESCE(e.evidence_type, 'video') IN ('video', 'image')
        ORDER BY c.updated_at ASC, c.id ASC
        """,
        (FINAL_V1_DERIVE_METHOD, int(kol_pool_id)),
    ).fetchall()
    states = _classified_rows(rows, derive_method=FINAL_V1_DERIVE_METHOD)
    return {
        "final_v1_ready": sum(1 for item in states.values() if item.get("status") == "ready"),
        "legacy_unverified": sum(
            1 for item in states.values() if item.get("status") == "legacy_unverified"
        ),
    }


__all__ = ["analysis_cache_summary_for_kol", "analysis_caches_for_evidence"]
