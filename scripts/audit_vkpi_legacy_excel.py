#!/usr/bin/env python3
"""Run the V-KPI P2A read-only legacy Excel audit."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime  # noqa: E402
from app.domains.legacy_import.legacy_import_audit import audit_legacy_file, write_reports  # noqa: E402
from app.domains.legacy_import.legacy_entity_resolution import (  # noqa: E402
    bulk_decide,
    decide_resolution,
    format_bulk_decision_result,
    format_decision_result,
    format_entity_detail,
    format_pending_reviews,
    format_resolution_summary,
    format_review_progress,
    inspect_resolution,
    list_pending_reviews,
    review_progress,
    resolve_batch,
    show_entity,
)
from app.domains.legacy_import.legacy_import_staging import (  # noqa: E402
    ensure_legacy_staging_schema,
    format_batch_summary,
    inspect_batch,
    rollback_staging_batch,
    stage_legacy_file,
)
from app.domains.legacy_import.legacy_kol_commit import (  # noqa: E402
    commit_kol_pool_batch,
    dry_run_kol_pool_commit,
    format_kol_pool_commit_plan,
    format_kol_pool_rollback,
    preview_kol_pool_rollback,
    rollback_kol_pool_commit,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a legacy V-KPI Excel/CSV file without writing main tables.")
    parser.add_argument("input", nargs="?", help="Path to a .xlsx or .csv legacy file")
    parser.add_argument("--sheet", default="", help="Optional .xlsx sheet name filter")
    parser.add_argument("--max-rows", type=int, default=0, help="Optional data row limit for a fast sample audit")
    parser.add_argument("--out-dir", default=str(ROOT / "docs/audits"), help="Directory for Markdown and CSV audit outputs")
    parser.add_argument("--prefix", default="", help="Optional output filename prefix, defaults to UTC date")
    parser.add_argument("--json", action="store_true", help="Print full JSON audit result")
    parser.add_argument("--no-write", action="store_true", help="Do not write Markdown/CSV report files")
    parser.add_argument("--stage", action="store_true", help="Write parsed rows into legacy staging tables")
    parser.add_argument("--batch-label", default="", help="Optional label recorded on a staging batch")
    parser.add_argument("--inspect-batch", default="", help="Print staging summary for an existing batch_uid")
    parser.add_argument("--rollback-batch", default="", help="Clear staging rows for a batch that has not been committed")
    parser.add_argument("--resolve-batch", default="", help="Run P2C canonical KOL resolution for a staged batch_uid")
    parser.add_argument("--inspect-resolution", default="", help="Print P2C resolution summary for a batch_uid")
    parser.add_argument("--list-pending-reviews", default="", help="List P2C entities that still need review for a batch_uid")
    parser.add_argument("--weak-label", default="", help="Filter review decisions by weak_label")
    parser.add_argument("--include-blocked", action="store_true", help="Include blocked_risk entities in review listing")
    parser.add_argument("--show-entity", default="", help="Show one P2C entity with its staging refs")
    parser.add_argument("--decide-resolution", default="", help="Record a decision for one entity_uid; dry-run unless --commit is set")
    parser.add_argument("--bulk-decide", default="", help="Record one decision for all pending entities with --weak-label in a batch_uid")
    parser.add_argument("--review-progress", default="", help="Print P2C review-decision progress for a batch_uid")
    parser.add_argument("--dry-run-kol-pool-commit", default="", help="Plan P2D writes into vkpi_kol_pool without mutating main tables")
    parser.add_argument("--commit-kol-pool-batch", default="", help="Commit P2D writes into vkpi_kol_pool; requires --commit")
    parser.add_argument("--rollback-kol-pool-commit", default="", help="Rollback P2D vkpi_kol_pool writes; requires --commit")
    parser.add_argument("--force-rollback", action="store_true", help="Force P2D rollback when rollback window has expired")
    parser.add_argument("--action", default="", help="Decision action: merge_with, keep_separate, drop, or escalate")
    parser.add_argument("--target", default="", help="Target entity_uid for merge_with decisions")
    parser.add_argument("--reason", default="", help="Decision reason; required for drop")
    parser.add_argument("--note", default="", help="Decision note; required for escalate")
    parser.add_argument("--commit", action="store_true", help="Apply a decision command; default is dry-run")
    parser.add_argument("--limit", type=int, default=50, help="Maximum rows shown for list/show commands")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if (
        args.inspect_batch
        or args.rollback_batch
        or args.resolve_batch
        or args.inspect_resolution
        or args.list_pending_reviews
        or args.show_entity
        or args.decide_resolution
        or args.bulk_decide
        or args.review_progress
        or args.dry_run_kol_pool_commit
        or args.commit_kol_pool_batch
        or args.rollback_kol_pool_commit
    ):
        try:
            ensure_legacy_staging_schema()
            if args.resolve_batch:
                print(format_resolution_summary(resolve_batch(args.resolve_batch)))
            elif args.inspect_resolution:
                print(format_resolution_summary(inspect_resolution(args.inspect_resolution)))
            elif args.list_pending_reviews:
                print(
                    format_pending_reviews(
                        list_pending_reviews(
                            args.list_pending_reviews,
                            weak_label=args.weak_label,
                            include_blocked=bool(args.include_blocked),
                            limit=max(0, int(args.limit or 0)),
                        )
                    )
                )
            elif args.show_entity:
                print(format_entity_detail(show_entity(args.show_entity, ref_limit=max(1, int(args.limit or 50)))))
            elif args.decide_resolution:
                print(
                    format_decision_result(
                        decide_resolution(
                            args.decide_resolution,
                            action=args.action,
                            target_entity_uid=args.target,
                            reason=args.reason,
                            note=args.note,
                            commit=bool(args.commit),
                        )
                    )
                )
            elif args.bulk_decide:
                print(
                    format_bulk_decision_result(
                        bulk_decide(
                            args.bulk_decide,
                            weak_label=args.weak_label,
                            action=args.action,
                            reason=args.reason,
                            note=args.note,
                            commit=bool(args.commit),
                        )
                    )
                )
            elif args.review_progress:
                print(format_review_progress(review_progress(args.review_progress)))
            elif args.dry_run_kol_pool_commit:
                print(
                    format_kol_pool_commit_plan(
                        dry_run_kol_pool_commit(
                            args.dry_run_kol_pool_commit,
                            include_blocked=bool(args.include_blocked),
                            sample_limit=max(0, int(args.limit or 0)),
                        )
                    )
                )
            elif args.commit_kol_pool_batch:
                if not args.commit:
                    print(
                        format_kol_pool_commit_plan(
                            dry_run_kol_pool_commit(
                                args.commit_kol_pool_batch,
                                include_blocked=bool(args.include_blocked),
                                sample_limit=max(0, int(args.limit or 0)),
                            )
                        )
                    )
                    print("Add --commit to apply P2D commit.")
                else:
                    print(
                        format_kol_pool_commit_plan(
                            commit_kol_pool_batch(
                                args.commit_kol_pool_batch,
                                include_blocked=bool(args.include_blocked),
                                sample_limit=max(0, int(args.limit or 0)),
                            )
                        )
                    )
            elif args.rollback_kol_pool_commit:
                if not args.commit:
                    print(
                        format_kol_pool_rollback(
                            preview_kol_pool_rollback(
                                args.rollback_kol_pool_commit,
                                sample_limit=max(0, int(args.limit or 0)),
                                force=bool(args.force_rollback),
                            )
                        )
                    )
                else:
                    print(
                        format_kol_pool_rollback(
                            rollback_kol_pool_commit(
                                args.rollback_kol_pool_commit,
                                sample_limit=max(0, int(args.limit or 0)),
                                force=bool(args.force_rollback),
                            )
                        )
                    )
            elif args.inspect_batch:
                print(format_batch_summary(inspect_batch(args.inspect_batch)))
            else:
                result = rollback_staging_batch(args.rollback_batch)
                print(f"batch_uid={result['batch_uid']}")
                print(f"status={result['status']}")
                print(f"rolled_back_rows={result['rolled_back_rows']}")
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        finally:
            asyncio.run(close_db_runtime())
        return 0

    if not args.input:
        print("ERROR: input is required unless a batch command is used", file=sys.stderr)
        return 2

    try:
        result = audit_legacy_file(args.input, sheet_name=args.sheet, max_rows=max(0, int(args.max_rows or 0)))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    paths: dict[str, str] = {}
    if not args.no_write:
        paths = write_reports(result, args.out_dir, prefix=args.prefix or None)
        result["outputs"] = paths

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        summary = result.get("summary") or {}
        print(f"source={result.get('source', {}).get('path', '')}")
        print(f"total_rows={summary.get('total_rows', 0)}")
        print(f"recognizable_kol_rows={summary.get('recognizable_kol_rows', 0)}")
        print(f"duplicate_groups={summary.get('duplicate_groups', 0)}")
        print(f"manual_review_rows={summary.get('manual_review_rows', 0)}")
        print(f"high_risk_rows={summary.get('high_risk_rows', 0)}")
        for key, value in paths.items():
            print(f"{key}={value}")
    if args.stage:
        try:
            ensure_legacy_staging_schema()
            staged = stage_legacy_file(
                args.input,
                batch_label=args.batch_label,
                sheet_name=args.sheet,
                max_rows=max(0, int(args.max_rows or 0)),
            )
            print(format_batch_summary(staged))
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        finally:
            asyncio.run(close_db_runtime())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
