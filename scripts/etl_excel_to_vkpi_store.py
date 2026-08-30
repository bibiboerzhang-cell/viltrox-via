"""PostgreSQL reads and idempotent writes for the promotion-plan ETL."""

from __future__ import annotations

import os
from typing import Any

try:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency. Install into the project venv: "
        ".venv/bin/python -m pip install 'pandas<3' python-calamine rapidfuzz psycopg2-binary"
    ) from exc

if __package__:
    from .etl_excel_to_vkpi_core import (
        STAGE_SCORE,
        AssignmentPlan,
        PoolRecord,
        load_dotenv,
        normalize_name,
        text,
    )
else:  # pragma: no cover - exercised by the legacy script entry point
    from etl_excel_to_vkpi_core import (
        STAGE_SCORE,
        AssignmentPlan,
        PoolRecord,
        load_dotenv,
        normalize_name,
        text,
    )


def connect():
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("DATABASE_URL is required. Source .env or set DATABASE_URL.")
    return psycopg2.connect(dsn)


def fetch_pool_records(conn) -> list[PoolRecord]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, handle, display_name, platform FROM vkpi_kol_pool ORDER BY id"
        )
        return [
            PoolRecord(
                id=int(row["id"]),
                handle=text(row.get("handle")),
                display_name=text(row.get("display_name")),
                platform=text(row.get("platform")),
            )
            for row in cur.fetchall()
        ]


def fetch_staff_map(conn) -> dict[str, int]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT s.id, COALESCE(NULLIF(u.name, ''), u.email, s.role) AS label, u.email
            FROM staff s
            LEFT JOIN users u ON u.id = s.user_id
            WHERE s.active = 1
            """
        )
        rows = cur.fetchall()
    mapping: dict[str, int] = {}
    for row in rows:
        staff_id = int(row["id"])
        for candidate in (row.get("label"), row.get("email")):
            value = normalize_name(text(candidate))
            if value:
                mapping[value] = staff_id
    return mapping


def fetch_existing_evidence_urls(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT content_url FROM vkpi_kol_video_evidence")
        return {text(row[0]) for row in cur.fetchall()}


def fetch_active_pool_ids(conn) -> set[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM vkpi_kol_pool WHERE has_video_evidence = TRUE")
        return {int(row[0]) for row in cur.fetchall()}


def fetch_pool_details(
    conn, pool_ids: set[int]
) -> dict[int, dict[str, Any]]:
    if not pool_ids:
        return {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, display_name, handle, dashboard_account_type, dashboard_tier
            FROM vkpi_kol_pool
            WHERE id = ANY(%s)
            """,
            (list(pool_ids),),
        )
        return {int(row["id"]): dict(row) for row in cur.fetchall()}


def fetch_project_ids_by_uid(
    conn, project_uids: list[str]
) -> dict[str, int]:
    if not project_uids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT project_uid, id FROM vkpi_projects WHERE project_uid = ANY(%s)",
            (project_uids,),
        )
        return {text(row[0]): int(row[1]) for row in cur.fetchall()}


def apply_projects(cur, projects: list[dict[str, Any]]) -> dict[str, int]:
    sheet_to_project_id: dict[str, int] = {}
    for project in projects:
        cur.execute(
            """
            INSERT INTO vkpi_projects
              (project_uid, project_name, product_sku, product_name, platform,
               assigned_staff_id, created_by_staff_id, stage, source_type,
               metadata_json, created_at, updated_at)
            VALUES
              (%(project_uid)s, %(project_name)s, %(product_sku)s, %(product_name)s, %(platform)s,
               %(assigned_staff_id)s, %(created_by_staff_id)s, 'discovered', %(source_type)s,
               %(metadata_json)s, NOW(), NOW())
            ON CONFLICT (project_uid) DO UPDATE SET
              project_name = EXCLUDED.project_name,
              product_sku = EXCLUDED.product_sku,
              product_name = EXCLUDED.product_name,
              platform = EXCLUDED.platform,
              assigned_staff_id = EXCLUDED.assigned_staff_id,
              created_by_staff_id = EXCLUDED.created_by_staff_id,
              source_type = EXCLUDED.source_type,
              metadata_json = EXCLUDED.metadata_json,
              updated_at = NOW()
            RETURNING id
            """,
            project,
        )
        sheet_to_project_id[project["sheet"]] = int(cur.fetchone()[0])
    return sheet_to_project_id


def apply_new_pools(
    cur, new_pool_plans: list[dict[str, Any]]
) -> dict[int, int]:
    temp_to_real: dict[int, int] = {}
    for plan in new_pool_plans:
        cur.execute(
            """
            INSERT INTO vkpi_kol_pool
              (pool_uid, handle, display_name, platform, source_type, source_ref,
               sync_status, dashboard_account_type, dashboard_tier, followers,
               created_at, updated_at)
            VALUES
              (%(pool_uid)s, %(handle)s, %(display_name)s, %(platform)s, %(source_type)s,
               %(source_ref)s, %(sync_status)s, %(dashboard_account_type)s,
               %(dashboard_tier)s, %(followers)s, NOW(), NOW())
            ON CONFLICT (platform, handle) DO UPDATE SET
              source_type = EXCLUDED.source_type,
              source_ref = EXCLUDED.source_ref,
              sync_status = EXCLUDED.sync_status,
              dashboard_account_type = EXCLUDED.dashboard_account_type,
              updated_at = NOW()
            RETURNING id
            """,
            plan,
        )
        temp_to_real[int(plan["temp_id"])] = int(cur.fetchone()[0])
    return temp_to_real


def stage_score_sql(expr: str) -> str:
    parts = " ".join(
        f"WHEN '{stage}' THEN {score}" for stage, score in STAGE_SCORE.items()
    )
    return f"(CASE {expr} {parts} ELSE 0 END)"


def apply_assignments(cur, assignments: list[AssignmentPlan]) -> None:
    old_score = stage_score_sql("vkpi_project_kol_assignments.stage")
    new_score = stage_score_sql("EXCLUDED.stage")
    for plan in assignments:
        cur.execute(
            f"""
            INSERT INTO vkpi_project_kol_assignments
              (project_id, kol_pool_id, stage, stage_status, assigned_staff_id,
               tracking_number, is_placeholder_tracking, source, source_ref,
               excel_progress, metadata_json, created_at, updated_at)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, 'excel', %s, %s, %s, %s, NOW())
            ON CONFLICT (project_id, kol_pool_id) DO UPDATE SET
              stage = CASE WHEN {new_score} > {old_score} THEN EXCLUDED.stage ELSE vkpi_project_kol_assignments.stage END,
              stage_status = EXCLUDED.stage_status,
              assigned_staff_id = COALESCE(EXCLUDED.assigned_staff_id, vkpi_project_kol_assignments.assigned_staff_id),
              tracking_number = COALESCE(EXCLUDED.tracking_number, vkpi_project_kol_assignments.tracking_number),
              is_placeholder_tracking = EXCLUDED.is_placeholder_tracking,
              source_ref = EXCLUDED.source_ref,
              excel_progress = EXCLUDED.excel_progress,
              metadata_json = vkpi_project_kol_assignments.metadata_json || EXCLUDED.metadata_json,
              updated_at = NOW()
            """,
            (
                plan.project_id,
                plan.kol_pool_id,
                plan.stage,
                "inactive" if plan.stage == "churned" else "active",
                plan.staff_id,
                plan.tracking_number,
                plan.is_placeholder_tracking,
                plan.source_ref,
                plan.stage_raw,
                Json(plan.metadata),
                plan.created_at,
            ),
        )


def apply_evidence(cur, evidence: list[dict[str, Any]]) -> None:
    for row in evidence:
        cur.execute(
            """
            INSERT INTO vkpi_kol_video_evidence
              (kol_pool_id, project_id, content_url, platform, source, source_ref,
               confidence, evidence_type, posted_at, created_at)
            VALUES
              (%(kol_pool_id)s, %(project_id)s, %(content_url)s, %(platform)s, %(source)s,
               %(source_ref)s, %(confidence)s, %(evidence_type)s, %(posted_at)s, %(created_at)s)
            ON CONFLICT (content_url) DO NOTHING
            """,
            row,
        )


def apply_needs_scrape(cur) -> int:
    cur.execute(
        """
        UPDATE vkpi_kol_pool
           SET needs_scrape = TRUE,
               scrape_status = 'pending'
         WHERE id IN (
           SELECT DISTINCT kol_pool_id
           FROM vkpi_project_kol_assignments
           WHERE stage IN ('content_posted', 'reviewed')
         )
           AND has_video_evidence = FALSE
        """
    )
    return int(cur.rowcount)
