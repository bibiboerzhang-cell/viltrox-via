"""Export a P13 recommendation review backlog CSV for human feedback.

The export uses existing vkpi_kol_recommendations.id values whenever available
so the filled CSV can be imported into vkpi_recommendation_feedback without
violating the existing recommendation_id foreign key.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402


FIELDNAMES = [
    "recommendation_id",
    "kol_handle",
    "platform",
    "followers",
    "country",
    "suggested_sku",
    "recommendation_reason",
    "top_evidence_summary",
    "action",
    "reject_reason",
    "reviewer_name",
]

SOCIAL_PLATFORMS = {"facebook", "instagram", "reddit", "tiktok", "x", "youtube"}


def _loads(raw: Any, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw or ""))
    except Exception:
        return default


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _reason(row: dict[str, Any]) -> str:
    explanation = _loads(row.get("explanation_json"), {})
    breakdown = _loads(row.get("scoring_breakdown_json"), {})
    snapshot = _loads(row.get("feature_snapshot_json"), {})
    candidates = [
        explanation.get("summary") if isinstance(explanation, dict) else "",
        explanation.get("reason") if isinstance(explanation, dict) else "",
        explanation.get("recommended_action") if isinstance(explanation, dict) else "",
        row.get("viltrox_fit_reason"),
        row.get("primary_topic"),
        f"score={row.get('score')}" if row.get("score") is not None else "",
        f"breakdown={breakdown}" if breakdown else "",
        f"features={snapshot}" if snapshot else "",
    ]
    for item in candidates:
        clean = _text(item)
        if clean:
            return clean
    return "Needs human review based on existing recommendation score and KOL profile."


def _cooperation_evidence(kol_pool_id: int) -> str:
    if not kol_pool_id:
        return ""
    rows = get_conn().execute(
        """
        SELECT product, project, status, cooperation_date, result, notes
        FROM vkpi_legacy_cooperations_staging
        WHERE matched_kol_pool_id = ?
        ORDER BY cooperation_date DESC NULLS LAST, id DESC
        LIMIT 2
        """,
        (int(kol_pool_id),),
    ).fetchall()
    snippets: list[str] = []
    for raw in rows:
        row = dict(raw)
        parts = [
            row.get("cooperation_date"),
            row.get("product"),
            row.get("project"),
            row.get("status"),
            row.get("result"),
            row.get("notes"),
        ]
        text = _text(" | ".join(str(part or "") for part in parts if part), 260)
        if text:
            snippets.append(text)
    return " ; ".join(snippets)


def _normalize_platforms(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def build_rows(
    limit: int = 100,
    *,
    include_platforms: set[str] | None = None,
    exclude_platforms: set[str] | None = None,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 100), 500))
    where = ["fb.id IS NULL"]
    params: list[Any] = []

    if include_platforms:
        placeholders = ", ".join("?" for _ in include_platforms)
        where.append(f"LOWER(rec.platform) IN ({placeholders})")
        params.extend(sorted(include_platforms))

    if exclude_platforms:
        placeholders = ", ".join("?" for _ in exclude_platforms)
        where.append(f"LOWER(rec.platform) NOT IN ({placeholders})")
        params.extend(sorted(exclude_platforms))

    params.append(safe_limit)
    rows = get_conn().execute(
        f"""
        SELECT
          rec.id AS recommendation_id,
          rec.kol_pool_id,
          rec.platform,
          rec.handle,
          rec.display_name,
          rec.score,
          rec.explanation_json,
          rec.scoring_breakdown_json,
          rec.feature_snapshot_json,
          kp.followers,
          kp.country,
          kp.primary_topic,
          kp.viltrox_fit_reason,
          l.product_sku AS launch_product_sku
        FROM vkpi_kol_recommendations rec
        LEFT JOIN vkpi_kol_pool kp ON kp.id = rec.kol_pool_id
        LEFT JOIN vkpi_product_launches l ON l.id = rec.launch_id
        LEFT JOIN vkpi_recommendation_feedback fb ON fb.recommendation_id = rec.id
        WHERE {" AND ".join(where)}
        ORDER BY rec.score DESC NULLS LAST, rec.rank ASC NULLS LAST, rec.id ASC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        evidence = _cooperation_evidence(int(row.get("kol_pool_id") or 0))
        output.append(
            {
                "recommendation_id": row.get("recommendation_id"),
                "kol_handle": _text(row.get("handle") or row.get("display_name"), 160),
                "platform": _text(row.get("platform"), 60),
                "followers": row.get("followers") or "",
                "country": _text(row.get("country"), 80),
                "suggested_sku": _text(row.get("launch_product_sku"), 160),
                "recommendation_reason": _reason(row),
                "top_evidence_summary": evidence,
                "action": "",
                "reject_reason": "",
                "reviewer_name": "",
            }
        )
    return output


def export_csv(
    path: Path,
    *,
    limit: int = 100,
    include_platforms: set[str] | None = None,
    exclude_platforms: set[str] | None = None,
) -> dict[str, Any]:
    rows = build_rows(
        limit=limit,
        include_platforms=include_platforms,
        exclude_platforms=exclude_platforms,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "output": str(path),
        "rows": len(rows),
        "encoding": "utf-8-sig",
        "uses_existing_recommendation_ids": True,
        "include_platforms": sorted(include_platforms or []),
        "exclude_platforms": sorted(exclude_platforms or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="exports/2026-05-21-p13-review-backlog.csv")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--platforms",
        default="",
        help="Comma-separated platform allowlist, e.g. youtube,instagram,tiktok.",
    )
    parser.add_argument(
        "--exclude-platforms",
        default="",
        help="Comma-separated platform denylist, e.g. media,project.",
    )
    parser.add_argument(
        "--social-only",
        action="store_true",
        help="Export only social platforms: facebook, instagram, reddit, tiktok, x, youtube.",
    )
    args = parser.parse_args()
    try:
        include_platforms = _normalize_platforms(args.platforms)
        exclude_platforms = _normalize_platforms(args.exclude_platforms)
        if args.social_only:
            include_platforms = SOCIAL_PLATFORMS if not include_platforms else include_platforms & SOCIAL_PLATFORMS

        result = export_csv(
            Path(args.out),
            limit=args.limit,
            include_platforms=include_platforms or None,
            exclude_platforms=exclude_platforms or None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
