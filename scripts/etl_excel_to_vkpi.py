#!/usr/bin/env python3
"""Import Viltrox promotion-plan Excel into Project/KOL/video evidence tables.

Default mode is dry-run. --commit writes in one serial transaction.

This module remains the stable CLI and import facade. Parsing/planning,
PostgreSQL access, and report rendering live in focused sibling modules, while
their established public names remain available here for legacy callers.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from stdout_utils import out

if __package__:
    from .etl_excel_to_vkpi_core import *  # noqa: F403
    from .etl_excel_to_vkpi_report import *  # noqa: F403
    from .etl_excel_to_vkpi_store import *  # noqa: F403
else:  # pragma: no cover - exercised by direct CLI and incremental importer
    from etl_excel_to_vkpi_core import *  # noqa: F403
    from etl_excel_to_vkpi_report import *  # noqa: F403
    from etl_excel_to_vkpi_store import *  # noqa: F403


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", default=str(DEFAULT_EXCEL))  # noqa: F405
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--evidence-only",
        action="store_true",
        help="only insert evidence rows; do not touch projects, pool, or assignments",
    )
    args = parser.parse_args()

    excel_path = Path(args.excel).expanduser()
    if not excel_path.exists():
        raise SystemExit(f"Excel not found: {excel_path}")

    today = date.today()
    skipped, empty_products, rows_by_sheet = load_excel(excel_path, today)  # noqa: F405
    with connect() as conn:  # noqa: F405
        pool_records = fetch_pool_records(conn)  # noqa: F405
        staff_map = fetch_staff_map(conn)  # noqa: F405
        assignments, match_report = merge_assignments(  # noqa: F405
            rows_by_sheet, pool_records, staff_map
        )
        workshop_by_sheet: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in match_report["workshop_skipped"]:
            workshop_by_sheet[item["sheet"]].append(
                {"row": item["row"], "name": item["name"]}
            )
        projects = build_project_plans(  # noqa: F405
            rows_by_sheet, staff_map, workshop_by_sheet
        )
        evidence, evidence_stats = build_evidence_plans(assignments)  # noqa: F405
        existing_evidence_urls = fetch_existing_evidence_urls(conn)  # noqa: F405
        active_pool_ids = fetch_active_pool_ids(conn)  # noqa: F405
        new_evidence_pool_ids = {
            int(row["kol_pool_id"])
            for row in evidence
            if int(row["kol_pool_id"]) > 0
            and row["content_url"] not in existing_evidence_urls
            and int(row["kol_pool_id"]) not in active_pool_ids
        }
        pool_details = fetch_pool_details(conn, new_evidence_pool_ids)  # noqa: F405

        if args.commit:
            with conn.cursor() as cur:
                project_uid_to_sheet = {
                    project["project_uid"]: project["sheet"] for project in projects
                }
                if args.evidence_only:
                    sheet_to_id = fetch_project_ids_by_uid(  # noqa: F405
                        conn, [project["project_uid"] for project in projects]
                    )
                    sheet_to_id = {
                        project_uid_to_sheet[uid]: project_id
                        for uid, project_id in sheet_to_id.items()
                    }
                else:
                    sheet_to_id = apply_projects(cur, projects)  # noqa: F405
                    temp_to_real = apply_new_pools(  # noqa: F405
                        cur, match_report["new_pool_plans"]
                    )
                    for plan in assignments:
                        plan.project_id = sheet_to_id[plan.sheet]
                        if plan.kol_pool_id < 0:
                            plan.kol_pool_id = temp_to_real[plan.kol_pool_id]
                        if plan.is_placeholder_tracking:
                            plan.tracking_number = (
                                f"UPS-FAKE-{str(plan.kol_pool_id)[-4:]}-"
                                f"{str(plan.project_id)[-4:]}-{plan.created_at:%m%d}"
                            )
                for row in evidence:
                    if row["kol_pool_id"] < 0:
                        continue
                    row["project_id"] = sheet_to_id.get(
                        project_uid_to_sheet[row["project_key"]]
                    )
                if not args.evidence_only:
                    apply_assignments(cur, assignments)  # noqa: F405
                apply_evidence(cur, evidence)  # noqa: F405
                if not args.evidence_only:
                    needs_scrape_count = apply_needs_scrape(cur)  # noqa: F405
                    out(f"commit needs_scrape rows updated: {needs_scrape_count}")

        print_report(  # noqa: F405
            skipped=skipped,
            empty_products=empty_products,
            rows_by_sheet=rows_by_sheet,
            projects=projects,
            match_report=match_report,
            assignments=assignments,
            evidence=evidence,
            evidence_stats=evidence_stats,
            existing_evidence_urls=existing_evidence_urls,
            active_pool_ids=active_pool_ids,
            pool_details=pool_details,
            mode="commit" if args.commit else "dry-run",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
