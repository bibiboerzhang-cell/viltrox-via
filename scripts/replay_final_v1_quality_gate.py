#!/usr/bin/env python3
"""Re-judge stored final_v1 analyses against the repaired quality gate.

Read-side gate defects degraded structurally sound analyses to
``quality_incomplete``: the gate demanded ``{"score": N}`` while the current
model family emits bare integers (and sometimes the numeric string ``"85"``),
it read ``inspection_complete`` with ``is not True`` while the provider (and the
DB compat adapter) hand back non-``bool`` truth, and ``NaN`` cleared the gate
only to project as a perfect 100.  The analyses themselves were never wrong --
only the verdict written next to them was.

So this recomputes the verdict *in place* from the payload already in the
database.  It never downloads a video, never calls a model, and never spends a
cent: the only inputs are ``vkpi_analysis_cache.result`` rows that are already
paid for.  Dry-run is the default; ``--apply`` is required to write.

**A recovered row is moved back to where readers look for it.**  A degraded row
does not merely carry ``quality_status='quality_incomplete'`` inside its JSON:
migration 299 also parks it at ``status='quality_incomplete'`` under the
isolated ``target_type='video_quality_triage'`` namespace, and may have suffixed
its ``derive_method`` to dodge the unique key.  Every reader
(``list_project_video_analysis_cache`` and friends) matches on
``target_type='video'`` + ``status='ready'`` + the exact derive method, so
rewriting the JSON alone leaves the analysis exactly as invisible as before.
Recovery therefore restores all four facts together, or reports that it cannot.

Three guards are absolute: a row that is currently complete is never demoted
(``would_demote`` aborts the write); a restore that would collide on the unique
key -- with a stored row *or* with another row in the same run -- is refused
row-by-row (``blocked_conflict``), never resolved by overwriting either paid
record; and only the namespace migration 299 actually filled is ever emptied.
A row under any other ``target_type`` merely shares a derive method, was never
moved by 299, and is reported (``foreign_namespace``) rather than dragged into
the visible namespace.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from stdout_utils import out

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - local dependency guard.
    load_dotenv = None  # type: ignore[assignment]

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from app.domains.analysis.cache_repo import (  # noqa: E402
    VIDEO_QUALITY_TRIAGE_TARGET_TYPE,
)
from app.services.ai.analyzers.gemini_video_results import (  # noqa: E402
    FINAL_V1_QUALITY_COMPLETE,
    FINAL_V1_QUALITY_INCOMPLETE,
    final_v1_quality_issues,
)

FINAL_DERIVE_METHOD = "video_analysis_final_v1"
# Where a readable analysis lives: readers select target_type='video' AND
# status='ready' AND derive_method=<exact>. Anything else is invisible.
VISIBLE_TARGET_TYPE = "video"
READY_STATUS = "ready"
INCOMPLETE_STATUS = "quality_incomplete"
# Migration 299 appends this plus the row id when moving a legacy row into the
# triage namespace would have collided on (target_type, target_id, derive_method).
MIGRATED_SUFFIX_MARKER = "__quality_migrated_"

# The only namespace a row may be moved *out of*.  Migration 299 moved degraded
# rows from 'video' into the triage namespace and nowhere else, so undoing
# exactly that move is the whole job.  A row sitting under any other
# target_type was never touched by 299: dragging it into 'video' would invent a
# placement nobody asked for, against a unique key it does not own.  Such rows
# are reported, counted, and left alone.
RELOCATABLE_SOURCE_TARGET_TYPE = VIDEO_QUALITY_TRIAGE_TARGET_TYPE
# The namespaces this replay judges at all: the one 299 filled, and the visible
# one it emptied.
JUDGED_TARGET_TYPES = frozenset({VISIBLE_TARGET_TYPE, RELOCATABLE_SOURCE_TARGET_TYPE})
FOREIGN_NAMESPACE_ACTION = "foreign_namespace"

# The analyses stranded by the gate defects, as identified on production.
DEFAULT_TARGET_IDS = (
    "5829",
    "5830",
    "5831",
    "5832",
    "5833",
    "5834",
    "5838",
    "5839",
    "5845",
    "5853",
)

WRITABLE_ACTIONS = frozenset({"recovered", "still_incomplete"})


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env")
        return
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip() or "postgresql://viltrox2@127.0.0.1:54329/viltrox2"


def _connect() -> psycopg.Connection[Any]:
    return psycopg.connect(_database_url(), row_factory=dict_row)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def natural_derive_method(value: Any) -> str:
    """Strip migration 299's collision suffix so readers can match the row again.

    ``video_analysis_final_v1__quality_migrated_4211`` is invisible to every
    reader, which all filter on the exact derive method.  Only the documented
    suffix is removed; any other derive method is returned untouched.
    """
    text = str(value or "")
    marker = FINAL_DERIVE_METHOD + MIGRATED_SUFFIX_MARKER
    if not text.startswith(marker):
        return text
    return FINAL_DERIVE_METHOD if text[len(marker) :].isdigit() else text


def is_visible(row: dict[str, Any]) -> bool:
    """True when readers can already see this row's analysis."""
    return (
        str(row.get("target_type") or "") == VISIBLE_TARGET_TYPE
        and str(row.get("status") or "") == READY_STATUS
        and str(row.get("derive_method") or "") == FINAL_DERIVE_METHOD
    )


def fetch_rows(
    conn: psycopg.Connection[Any],
    *,
    target_ids: list[str],
    cache_ids: list[int],
) -> list[dict[str, Any]]:
    """Load the stored analyses to re-judge. Read-only, one statement.

    ``= ANY(...)`` keeps this a single static statement with no interpolated
    placeholder list; an empty array simply matches nothing.  ``strpos(...)=1``
    picks up rows whose derive method migration 299 suffixed, which the previous
    exact-match filter silently skipped -- the rows most in need of recovery.

    The select deliberately does **not** filter on ``target_type``: a row under
    some other namespace must be *counted and shown*, not silently omitted, so
    the operator can see it exists.  Refusing to touch it is ``judge``'s job
    (``foreign_namespace``), which is where the placement decision lives.
    """
    if not target_ids and not cache_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, target_type, target_id, derive_method, model, status, result
            FROM vkpi_analysis_cache
            WHERE (
                    derive_method = %(derive_method)s
                 OR strpos(derive_method, %(migrated_prefix)s) = 1
              )
              AND (
                    CAST(target_id AS TEXT) = ANY(%(target_ids)s)
                 OR id = ANY(%(cache_ids)s)
              )
            ORDER BY id
            """,
            {
                "derive_method": FINAL_DERIVE_METHOD,
                "migrated_prefix": FINAL_DERIVE_METHOD + MIGRATED_SUFFIX_MARKER,
                "target_ids": list(target_ids),
                "cache_ids": list(cache_ids),
            },
        )
        return [dict(row) for row in cur.fetchall()]


def occupied_visible_slots(
    conn: psycopg.Connection[Any], *, target_ids: list[str]
) -> dict[tuple[str, str], int]:
    """Map the visible-namespace unique keys already taken, to their row id."""
    if not target_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, CAST(target_id AS TEXT) AS target_id, derive_method
            FROM vkpi_analysis_cache
            WHERE target_type = %(visible)s
              AND CAST(target_id AS TEXT) = ANY(%(target_ids)s)
            """,
            {"visible": VISIBLE_TARGET_TYPE, "target_ids": list(target_ids)},
        )
        return {
            (str(row["target_id"]), str(row["derive_method"])): int(row["id"])
            for row in cur.fetchall()
        }


def restore_plan(row: dict[str, Any], *, recomputed_status: str) -> dict[str, Any]:
    """Where this row must live for the recomputed verdict to be true.

    A complete verdict belongs in the visible namespace -- but only for a row
    that came *from* there, i.e. one migration 299 parked in the triage
    namespace, or one already visible.  A row under any other ``target_type``
    keeps its placement whatever the verdict: see
    ``RELOCATABLE_SOURCE_TARGET_TYPE``.

    An incomplete verdict likewise leaves the row's placement exactly as found:
    this replay recovers analyses, it never hides one that is currently
    readable.
    """
    current = {
        "target_type": str(row.get("target_type") or ""),
        "status": str(row.get("status") or ""),
        "derive_method": str(row.get("derive_method") or ""),
    }
    if recomputed_status == FINAL_V1_QUALITY_COMPLETE and current["target_type"] in JUDGED_TARGET_TYPES:
        desired = {
            "target_type": VISIBLE_TARGET_TYPE,
            "status": READY_STATUS,
            "derive_method": natural_derive_method(current["derive_method"]),
        }
    else:
        desired = dict(current)
    return {
        "current": current,
        "desired": desired,
        "changes": {key: value for key, value in desired.items() if value != current[key]},
    }


def judge(row: dict[str, Any], *, include_legacy: bool = False) -> dict[str, Any]:
    """Recompute one row's quality verdict, and where it then has to live."""
    result = _as_dict(row.get("result"))
    stored_status = str(result.get("quality_status") or "").strip() or None
    stored_issues = result.get("quality_issues")
    stored_issues = [str(item) for item in stored_issues] if isinstance(stored_issues, list) else []

    issues = final_v1_quality_issues(result)
    recomputed_status = FINAL_V1_QUALITY_INCOMPLETE if issues else FINAL_V1_QUALITY_COMPLETE
    plan = restore_plan(row, recomputed_status=recomputed_status)
    json_unchanged = recomputed_status == stored_status and issues == stored_issues

    if str(row.get("target_type") or "") not in JUDGED_TARGET_TYPES:
        # Migration 299 only ever moved rows between 'video' and the triage
        # namespace. A row somewhere else shares nothing but a derive method;
        # this replay has no mandate over it and no evidence it was degraded by
        # the gate, so it is counted and shown, never written.
        action = FOREIGN_NAMESPACE_ACTION
    elif stored_status is None and not include_legacy and is_visible(row):
        # Rows written before the gate existed carry no verdict at all and are
        # already served through their ``ready`` row status.  Stamping a
        # first-ever ``quality_incomplete`` on them would retroactively hide
        # analyses that were never degraded -- the opposite of this replay's
        # purpose.  A verdict-less row that is *not* visible is not one of
        # those: only the gate could have moved it, so it is judged normally.
        action = "legacy_skipped"
    elif json_unchanged and not plan["changes"]:
        action = "unchanged"
    elif recomputed_status == FINAL_V1_QUALITY_COMPLETE:
        action = "recovered"
    elif stored_status == FINAL_V1_QUALITY_COMPLETE:
        # Never silently demote a row this replay was meant to help.
        action = "would_demote"
    else:
        action = "still_incomplete"

    return {
        "cache_id": row.get("id"),
        "target_id": str(row.get("target_id")),
        "model": row.get("model"),
        "row_status": row.get("status"),
        "visible_before": is_visible(row),
        "stored_quality_status": stored_status,
        "stored_issues": stored_issues,
        "recomputed_quality_status": recomputed_status,
        "recomputed_issues": issues,
        "plan": plan,
        "conflict_with_cache_id": None,
        "conflict_scope": None,
        "action": action,
    }


def annotate_conflicts(
    verdicts: list[dict[str, Any]], slots: dict[tuple[str, str], int]
) -> None:
    """Refuse, row by row, any restore that would collide on the unique key.

    ``(target_type, target_id, derive_method)`` is unique, and a collision has
    two sources, not one:

    * **stored** -- the visible slot is already held by another *paid* row.
    * **batch** -- two rows *in this same run* normalise to the same visible
      key (typically one natural row plus one migration-299-suffixed twin of
      the same analysis).  The stored-slot check cannot see this: the slot is
      free when the run starts, so the first UPDATE takes it and the second
      raises ``UniqueViolation`` mid-apply, after earlier writes are already on
      the connection.

    Both survive the same way: every contender is refused.  This function does
    not pick a winner -- both records are paid, and choosing between them is an
    operator's call, not a script's.
    """
    claimants: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for verdict in verdicts:
        changes = verdict["plan"]["changes"]
        if verdict["action"] != "recovered":
            continue
        if not ({"target_type", "derive_method"} & set(changes)):
            continue
        key = (verdict["target_id"], verdict["plan"]["desired"]["derive_method"])
        occupant = slots.get(key)
        if occupant is not None and occupant != verdict["cache_id"]:
            verdict["conflict_with_cache_id"] = occupant
            verdict["conflict_scope"] = "stored"
            verdict["action"] = "blocked_conflict"
            continue
        claimants.setdefault(key, []).append(verdict)

    for contenders in claimants.values():
        if len(contenders) < 2:
            continue
        for verdict in contenders:
            rivals = [
                other["cache_id"] for other in contenders if other["cache_id"] != verdict["cache_id"]
            ]
            verdict["conflict_with_cache_id"] = rivals[0] if rivals else None
            verdict["conflict_scope"] = "batch"
            verdict["action"] = "blocked_conflict"


def apply_verdict(conn: psycopg.Connection[Any], row: dict[str, Any], verdict: dict[str, Any]) -> None:
    """Write the quality verdict *and* the placement it implies. Payload untouched."""
    result = dict(_as_dict(row.get("result")))
    result["quality_status"] = verdict["recomputed_quality_status"]
    result["quality_issues"] = verdict["recomputed_issues"]
    desired = verdict["plan"]["desired"]
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE vkpi_analysis_cache
            SET result = %(result)s,
                target_type = %(target_type)s,
                status = %(status)s,
                derive_method = %(derive_method)s,
                updated_at = NOW()
            WHERE id = %(cache_id)s
            """,
            {
                "result": Jsonb(result),
                "target_type": desired["target_type"],
                "status": desired["status"],
                "derive_method": desired["derive_method"],
                "cache_id": verdict["cache_id"],
            },
        )


def _render(verdict: dict[str, Any]) -> None:
    out(
        f"  [{verdict['action']:>16}] cache_id={verdict['cache_id']} "
        f"target_id={verdict['target_id']} model={verdict['model']}"
    )
    out(
        f"      quality_status: {verdict['stored_quality_status']} "
        f"-> {verdict['recomputed_quality_status']}"
    )
    changes = verdict["plan"]["changes"]
    if changes:
        current = verdict["plan"]["current"]
        moves = ", ".join(f"{key}: {current[key]} -> {value}" for key, value in sorted(changes.items()))
        out(f"      placement: {moves}")
    visible_after = verdict["plan"]["desired"] == {
        "target_type": VISIBLE_TARGET_TYPE,
        "status": READY_STATUS,
        "derive_method": FINAL_DERIVE_METHOD,
    }
    if verdict["action"] in WRITABLE_ACTIONS:
        out(
            f"      visible to readers: {'yes' if verdict['visible_before'] else 'no'} "
            f"-> {'yes' if visible_after else 'no'}"
        )
    if verdict["action"] == FOREIGN_NAMESPACE_ACTION:
        out(
            f"      left alone: target_type={verdict['plan']['current']['target_type']} is outside "
            f"({VISIBLE_TARGET_TYPE}, {RELOCATABLE_SOURCE_TARGET_TYPE}); migration 299 never moved "
            "it, so this replay does not move it back. Nothing is written."
        )
    if verdict["conflict_with_cache_id"] is not None:
        held = (
            "in this same run also claims"
            if verdict["conflict_scope"] == "batch"
            else "already holds"
        )
        out(
            f"      refused: cache_id={verdict['conflict_with_cache_id']} {held} "
            f"({VISIBLE_TARGET_TYPE}, {verdict['target_id']}, "
            f"{verdict['plan']['desired']['derive_method']}); both rows are paid, "
            "neither is overwritten."
        )
    if verdict["action"] == "still_incomplete" and verdict["visible_before"]:
        out(
            f"      note: still readable while judged {INCOMPLETE_STATUS}. Parking it under "
            f"target_type={VIDEO_QUALITY_TRIAGE_TARGET_TYPE} would hide a paid analysis, "
            "which this replay never does; move it by hand if that is intended."
        )
    resolved = [item for item in verdict["stored_issues"] if item not in verdict["recomputed_issues"]]
    if resolved:
        out(f"      resolved: {', '.join(resolved)}")
    if verdict["recomputed_issues"]:
        out(f"      remaining: {', '.join(verdict['recomputed_issues'])}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Re-judge stored final_v1 analyses against the repaired quality gate, and "
            "restore recovered rows to the namespace readers actually read. "
            "Recomputes only -- no video download, no model call, no spend."
        )
    )
    parser.add_argument(
        "--target-id",
        action="append",
        default=[],
        help="target_id to re-judge (repeatable). Defaults to the known stranded set.",
    )
    parser.add_argument(
        "--cache-id",
        action="append",
        type=int,
        default=[],
        help="vkpi_analysis_cache.id to re-judge (repeatable).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the recomputed verdict and placement back. Without this the run is a dry run.",
    )
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help=(
            "Also judge visible rows that pre-date the gate and carry no stored verdict. "
            "Skipped by default so this replay cannot hide analyses that were never degraded."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    target_ids = [str(item) for item in args.target_id]
    cache_ids = list(args.cache_id)
    if not target_ids and not cache_ids:
        target_ids = list(DEFAULT_TARGET_IDS)

    _load_env()
    conn = _connect()
    try:
        rows = fetch_rows(conn, target_ids=target_ids, cache_ids=cache_ids)
        out(f"mode: {'APPLY' if args.apply else 'DRY RUN (no write)'}")
        out(f"requested: {len(target_ids)} target_id, {len(cache_ids)} cache_id")
        out(f"matched:   {len(rows)} stored final_v1 row(s)")

        found = {str(row.get("target_id")) for row in rows}
        missing = [item for item in target_ids if item not in found]
        if missing:
            out(f"not found in this database: {', '.join(missing)}")

        if not rows:
            out("nothing to re-judge.")
            return 0

        out("")
        verdicts = [judge(row, include_legacy=args.include_legacy) for row in rows]
        annotate_conflicts(
            verdicts,
            occupied_visible_slots(conn, target_ids=sorted(found)),
        )
        counts: Counter[str] = Counter()
        for verdict in verdicts:
            counts[verdict["action"]] += 1
            _render(verdict)

        out("")
        out("summary: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))

        pairs = list(zip(rows, verdicts))
        writable = [(row, verdict) for row, verdict in pairs if verdict["action"] in WRITABLE_ACTIONS]
        restored = [verdict for _row, verdict in writable if verdict["plan"]["changes"]]
        demotions = [verdict for verdict in verdicts if verdict["action"] == "would_demote"]
        blocked = [verdict for verdict in verdicts if verdict["action"] == "blocked_conflict"]
        foreign = [verdict for verdict in verdicts if verdict["action"] == FOREIGN_NAMESPACE_ACTION]
        if foreign:
            out(
                f"left untouched: {len(foreign)} row(s) outside the "
                f"({VISIBLE_TARGET_TYPE}, {RELOCATABLE_SOURCE_TARGET_TYPE}) namespaces; "
                "migration 299 never moved them, so this replay does not either."
            )
        if restored:
            out(f"{len(restored)} row(s) would return to the reader-visible namespace.")
        if blocked:
            out(
                f"refusing to restore {len(blocked)} row(s) whose visible slot is held by "
                "another paid row; resolve those by hand."
            )
        if demotions:
            out(
                f"refusing to demote {len(demotions)} row(s) that are currently complete; "
                "inspect them before writing."
            )

        if not args.apply:
            out(f"dry run: {len(writable)} row(s) would be updated. Re-run with --apply to write.")
            return 0

        if demotions:
            out("aborted: nothing written while a demotion is pending.")
            return 1

        for row, verdict in writable:
            apply_verdict(conn, row, verdict)
        conn.commit()
        out(f"applied: {len(writable)} row(s) updated, {len(restored)} restored to visible.")
        return 1 if blocked else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
