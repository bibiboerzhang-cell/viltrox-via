"""Export a P13 recommendation review backlog CSV for human feedback.

The export uses existing vkpi_kol_recommendations.id values so the filled CSV
can be imported into vkpi_recommendation_feedback without violating the
existing recommendation_id foreign key.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime  # noqa: E402
from app.domains.recommendations import feedback_backlog as recommendation_feedback_backlog  # noqa: E402


SOCIAL_PLATFORMS = {"facebook", "instagram", "reddit", "tiktok", "x", "youtube"}


def _normalize_platforms(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _filter_csv_rows(
    csv_text: str,
    *,
    limit: int,
    include_platforms: set[str] | None = None,
    exclude_platforms: set[str] | None = None,
) -> tuple[str, int]:
    clean = csv_text[1:] if csv_text.startswith("\ufeff") else csv_text
    reader = csv.DictReader(io.StringIO(clean))
    rows: list[dict[str, str]] = []
    for row in reader:
        platform = str(row.get("platform") or "").strip().lower()
        if include_platforms and platform not in include_platforms:
            continue
        if exclude_platforms and platform in exclude_platforms:
            continue
        rows.append(row)
        if len(rows) >= max(1, min(int(limit or 100), 500)):
            break
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames or list(recommendation_feedback_backlog.CSV_FIELDS))
    writer.writeheader()
    writer.writerows(rows)
    return "\ufeff" + output.getvalue(), len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="", help="CSV output path. Defaults to runtime/ops/vkpi-p13-review-backlog.csv.")
    parser.add_argument("--run-uid", default="", help="Optional recommendation run_uid filter.")
    parser.add_argument("--limit", type=int, default=120, help="Maximum rows to export.")
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

    out_path = Path(args.out or ROOT / "runtime" / "ops" / "vkpi-p13-review-backlog.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        include_platforms = _normalize_platforms(args.platforms)
        exclude_platforms = _normalize_platforms(args.exclude_platforms)
        if args.social_only:
            include_platforms = SOCIAL_PLATFORMS if not include_platforms else include_platforms & SOCIAL_PLATFORMS
        source_limit = 500 if include_platforms or exclude_platforms else args.limit
        csv_text = recommendation_feedback_backlog.build_recommendation_feedback_backlog_csv(
            run_uid=args.run_uid,
            limit=source_limit,
        )
        csv_text, row_count = _filter_csv_rows(
            csv_text,
            limit=args.limit,
            include_platforms=include_platforms or None,
            exclude_platforms=exclude_platforms or None,
        )
        out_path.write_text(csv_text, encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": True,
                    "path": str(out_path),
                    "run_uid": args.run_uid,
                    "limit": args.limit,
                    "rows": row_count,
                    "encoding": "utf-8-sig",
                    "uses_existing_recommendation_ids": True,
                    "include_platforms": sorted(include_platforms or []),
                    "exclude_platforms": sorted(exclude_platforms or []),
                    "write_db": False,
                    "provider_calls": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
