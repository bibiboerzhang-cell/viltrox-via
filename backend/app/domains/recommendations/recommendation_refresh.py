"""Periodic recommendation refresh job (B: learning loop eats fresh data).

Why this exists
---------------
``vkpi_kol_recommendations`` was last written 2026-05-19. The learning loop
(``vkpi_recommendation_feedback`` scoring + outcome backfill) keeps grading the
*same* stale recommendation set. This job recomputes recommendations from the
**current** KOL pool + **existing** product-fit/memory state and lands fresh
``vkpi_kol_recommendations`` rows so the learning chain has new material.

How it works (deterministic, cheap, idempotent)
------------------------------------------------
- Reuses the existing deterministic engine ``build_new_launch_match_preview``
  with ``with_llm_reasons=False`` (zero LLM/provider calls, zero budget burn).
- Picks the top product families that the *current* pool actually has coverage
  for (most cooperation/member count), capped at ``max_families``.
- Each family produces one ``vkpi_kol_recommendation_runs`` row + up to
  ``per_family_limit`` ``vkpi_kol_recommendations`` rows via the existing
  ``persist_run=True`` path.
- Idempotent: families that already produced a ``new_launch_match_v1`` run today
  are skipped (unless ``force=True``), so re-running the same day is a no-op.

Red lines
---------
- Reads ``viltrox_fit_score`` / pool / memory only. NEVER writes
  ``viltrox_fit_score`` (this module issues no UPDATE/INSERT to that column).
- No provider calls; deterministic rule path only. Bounded family + item count.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.recommendations.new_launch_match import build_new_launch_match_preview

logger = get_logger(__name__)

# Hard upper bounds so the job can never balloon cost/rows.
_MAX_FAMILIES_CAP = 25
_PER_FAMILY_CAP = 50


def _top_product_families(limit: int) -> list[str]:
    """Top product families by current pool coverage (cooperation then members).

    Read-only over memory tables. Returns display_name strings that feed back
    into ``build_new_launch_match_preview(product_query=...)``.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT f.display_name AS display_name,
               COUNT(DISTINCT w.id) AS cooperation_count,
               COUNT(DISTINCT nl.source_entity_id) AS member_count
        FROM vkpi_memory_entities f
        LEFT JOIN vkpi_memory_links nl
          ON nl.target_entity_id=f.id
         AND nl.link_type='normalized_to_product_family'
        LEFT JOIN vkpi_memory_links w
          ON w.target_entity_id=nl.source_entity_id
         AND w.link_type='worked_on_product'
        WHERE f.entity_type='product_family'
          AND f.display_name IS NOT NULL
          AND f.display_name <> ''
        GROUP BY f.id, f.display_name
        HAVING COUNT(DISTINCT nl.source_entity_id) > 0
        ORDER BY cooperation_count DESC, member_count DESC, f.display_name
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        name = (row[0] if not isinstance(row, dict) else row.get("display_name")) or ""
        name = str(name).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _family_refreshed_today(family_name: str) -> bool:
    """True when a new_launch_match_v1 run for this family already landed today.

    Idempotency guard: ``filters_json`` carries ``target_family_name``; we match
    on it with a like-fragment (compat: ``%x%`` not ``%s``). ``created_at`` is a
    timestamptz, so we date-truncate it and compare to CURRENT_DATE rather than
    LIKE (LIKE on timestamptz raises in Postgres).
    """
    conn = get_conn()
    frag = f'%"target_family_name": "{family_name}"%'
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM vkpi_kol_recommendation_runs
        WHERE strategy_version='new_launch_match_v1'
          AND CAST(created_at AS DATE)=CURRENT_DATE
          AND filters_json LIKE ?
        """,
        (frag,),
    ).fetchone()
    count = int((row[0] if not isinstance(row, dict) else list(row.values())[0]) or 0)
    return count > 0


def refresh_recommendations(
    *,
    max_families: int = 8,
    per_family_limit: int = 25,
    force: bool = False,
) -> dict[str, Any]:
    """Recompute + persist fresh recommendations from the current pool/fit.

    Args:
        max_families: how many top product families to refresh this run
            (clamped to ``_MAX_FAMILIES_CAP``).
        per_family_limit: max recommendation rows per family
            (clamped to ``_PER_FAMILY_CAP``).
        force: when False, families already refreshed today are skipped.

    Returns a summary dict. Never raises for a single-family failure; it is
    logged and counted so one bad family does not abort the whole sweep.
    """
    fam_budget = max(1, min(int(max_families or 0) or 8, _MAX_FAMILIES_CAP))
    item_budget = max(1, min(int(per_family_limit or 0) or 25, _PER_FAMILY_CAP))

    families = _top_product_families(fam_budget)
    refreshed: list[dict[str, Any]] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []
    total_recs = 0

    for family in families:
        if not force and _family_refreshed_today(family):
            skipped.append(family)
            continue
        try:
            payload = build_new_launch_match_preview(
                product_query=family,
                limit=item_budget,
                with_llm_reasons=False,  # deterministic, zero provider/budget burn
                persist_run=True,        # writes vkpi_kol_recommendation(s) rows
            )
        except Exception as exc:  # noqa: BLE001 - one bad family must not abort sweep
            logger.warning("recommendation_refresh.family_failed", extra={"family": family, "error": str(exc)[:240]})
            failed.append({"family": family, "error": str(exc)[:240]})
            continue
        persistence = payload.get("persistence") or {}
        rec_count = int(persistence.get("recommendation_count") or 0)
        total_recs += rec_count
        refreshed.append(
            {
                "family": family,
                "run_uid": persistence.get("run_uid"),
                "run_id": persistence.get("run_id"),
                "recommendation_count": rec_count,
            }
        )

    result = {
        "ok": True,
        "families_considered": len(families),
        "families_refreshed": len(refreshed),
        "families_skipped_today": len(skipped),
        "families_failed": len(failed),
        "recommendations_written": total_recs,
        "refreshed": refreshed,
        "skipped": skipped,
        "failed": failed,
        "force": bool(force),
        "max_families": fam_budget,
        "per_family_limit": item_budget,
    }
    logger.info(
        "recommendation_refresh.done",
        extra={
            "families_refreshed": len(refreshed),
            "recommendations_written": total_recs,
            "families_skipped_today": len(skipped),
            "families_failed": len(failed),
        },
    )
    return result
