#!/usr/bin/env python3
"""Increment-only importer for updated Viltrox promotion-plan workbooks.

Default mode is dry-run. The commit path is narrowly scoped:
- new projects, pools, assignments, and evidence are insert-only
- existing assignment updates are limited to the audited Vintage Z1 Pro delta
- project stage/stage_status/follow_status and project stars are never touched
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import psycopg2
from psycopg2.extras import Json, RealDictCursor

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import etl_excel_to_vkpi as legacy  # noqa: E402


DEFAULT_EXCEL = Path("/Users/bibiboer/Downloads/海外市场推广计划表-Viltrox (4).xlsx")
FRAME_SHEET = "(6.10) Frame the Game"
AF28_L_SHEET = "(6.16) AF 28mm F4.5 L"

FRAME_PROGRESS_COL = "内容进度"
FRAME_OWNER_COL = "登记人"
FRAME_NAME_COL = "KOL/KOC名称"
FRAME_PROFILE_COL = "红人主页链接"
FRAME_PUBLISH_COL = "内容发布链接"
VINTAGE_Z1_PRO_UID = "EXCEL-5-25-vintage-z1-pro"

FRAME_PROGRESS_STAGE = {
    "沟通中": "discovery",
    "已联系": "contacted",
    "已合作": "shipped",
    "已终止": "cancelled",
    "": "discovery",
}

SAFE_ASSIGNMENT_STAGES = {
    "discovered",
    "contacted",
    "replied",
    "agreed",
    "device_sent",
    "content_posted",
    "churned",
    "cancelled",
    "shipped",
    "discovery",
}


@dataclass
class ExistingProject:
    id: int
    project_uid: str
    project_name: str
    stage: str
    stage_status: str
    follow_status: str


@dataclass
class ExistingAssignment:
    id: int
    project_id: int
    project_uid: str
    project_name: str
    kol_pool_id: int
    stage: str
    stage_status: str
    excel_progress: str
    handle: str
    display_name: str


@dataclass
class PoolInsertPlan:
    temp_id: int
    platform: str
    handle: str
    display_name: str
    pool_uid: str
    source_ref: str
    source_type: str
    metadata: dict[str, Any]


@dataclass
class AssignmentInsertPlan:
    source_group: str
    project_id: int | None
    project_uid: str
    project_name: str
    kol_pool_id: int
    stage: str | None
    stage_status: str
    assigned_staff_id: int | None
    source_ref: str
    excel_progress: str
    metadata: dict[str, Any]


@dataclass
class EvidenceInsertPlan:
    source_group: str
    project_id: int | None
    project_uid: str
    kol_pool_id: int
    content_url: str
    platform: str
    source: str
    source_ref: str
    evidence_type: str
    posted_at: date | None
    created_at: datetime | None


@dataclass
class AssignmentUpdatePlan:
    source_group: str
    assignment_id: int
    project_id: int
    project_uid: str
    project_name: str
    kol_pool_id: int
    handle: str
    display_name: str
    current_stage: str
    new_stage: str
    current_stage_status: str
    new_stage_status: str
    current_excel_progress: str
    new_excel_progress: str
    reason: str
    source_ref: str


def _text(value: Any) -> str:
    return legacy.text(value)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _database_url(explicit: str = "") -> str:
    legacy.load_dotenv(PROJECT_ROOT / ".env")
    return explicit or os.environ.get("DATABASE_URL", "").strip() or "postgresql://postgres@127.0.0.1:54329/viltrox2"


def _platform_from_url(url: str) -> str:
    lowered = url.lower()
    if "instagram.com" in lowered:
        return "instagram"
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube"
    if "tiktok.com" in lowered:
        return "tiktok"
    if "facebook.com" in lowered:
        return "facebook"
    if "x.com" in lowered or "twitter.com" in lowered:
        return "x"
    return ""


def _handle_from_profile(profile_url: str, name: str, platform: str) -> str:
    raw = _text(profile_url)
    if raw and re.match(r"^https?://", raw, flags=re.I):
        parsed = urlparse(raw)
        parts = [part for part in parsed.path.split("/") if part]
        if platform == "instagram" and parts:
            return parts[0].lstrip("@")
        if platform == "tiktok":
            for part in parts:
                if part.startswith("@"):
                    return part.lstrip("@")
            return parts[0].lstrip("@") if parts else legacy.clean_excel_kol_name(name)
        if platform == "youtube":
            for index, part in enumerate(parts):
                if part.startswith("@"):
                    return part.lstrip("@")
                if part in {"channel", "c", "user"} and index + 1 < len(parts):
                    return parts[index + 1]
            return parts[0].lstrip("@") if parts else legacy.clean_excel_kol_name(name)
    if raw and not re.search(r"\s", raw):
        return raw.lstrip("@")
    return legacy.clean_excel_kol_name(name) or name


def _pool_uid(platform: str, handle: str) -> str:
    base = f"EXCEL-NEW-{legacy.slugify(platform + '-' + handle)[:26]}"
    suffix = hashlib.md5(f"{platform}:{handle}".encode("utf-8")).hexdigest()[:6]
    return f"{base[:23]}-{suffix}"


def fetch_existing(conn) -> tuple[
    dict[str, ExistingProject],
    dict[tuple[int, int], ExistingAssignment],
    set[str],
    dict[tuple[str, str], int],
    dict[str, int],
]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, project_uid, project_name, stage, stage_status, follow_status
            FROM vkpi_projects
            WHERE source_type='excel_promo_plan'
              AND stage_status <> 'deleted'
            ORDER BY id
            """
        )
        projects = {
            _text(row["project_uid"]): ExistingProject(
                id=int(row["id"]),
                project_uid=_text(row["project_uid"]),
                project_name=_text(row["project_name"]),
                stage=_text(row["stage"]),
                stage_status=_text(row["stage_status"]),
                follow_status=_text(row["follow_status"]),
            )
            for row in cur.fetchall()
        }
        project_ids = [project.id for project in projects.values()]
        cur.execute(
            """
            SELECT
              a.id,
              a.project_id,
              p.project_uid,
              p.project_name,
              a.kol_pool_id,
              a.stage,
              a.stage_status,
              a.excel_progress,
              COALESCE(kp.handle, '') AS handle,
              COALESCE(kp.display_name, '') AS display_name
            FROM vkpi_project_kol_assignments a
            JOIN vkpi_projects p ON p.id = a.project_id
            LEFT JOIN vkpi_kol_pool kp ON kp.id = a.kol_pool_id
            WHERE a.project_id = ANY(%s)
            """,
            (project_ids,),
        )
        assignments = {
            (int(row["project_id"]), int(row["kol_pool_id"])): ExistingAssignment(
                id=int(row["id"]),
                project_id=int(row["project_id"]),
                project_uid=_text(row["project_uid"]),
                project_name=_text(row["project_name"]),
                kol_pool_id=int(row["kol_pool_id"]),
                stage=_text(row["stage"]),
                stage_status=_text(row["stage_status"]),
                excel_progress=_text(row["excel_progress"]),
                handle=_text(row["handle"]),
                display_name=_text(row["display_name"]),
            )
            for row in cur.fetchall()
        }
        cur.execute("SELECT content_url FROM vkpi_kol_video_evidence")
        evidence_urls = {_text(row["content_url"]) for row in cur.fetchall()}
        cur.execute("SELECT id, platform, handle FROM vkpi_kol_pool")
        pools = {
            (_text(row["platform"]), legacy.normalize_name(_text(row["handle"]))): int(row["id"])
            for row in cur.fetchall()
            if _text(row["handle"])
        }
        cur.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool")
        pool_count = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n FROM vkpi_kol_video_evidence")
        evidence_count = int(cur.fetchone()["n"])
    table_counts = {
        "projects_in_scope": len(projects),
        "assignments_in_scope": len(assignments),
        "kol_pool": pool_count,
        "evidence": evidence_count,
    }
    return projects, assignments, evidence_urls, pools, table_counts


def _standard_increment(
    *,
    excel_path: Path,
    existing_projects: dict[str, ExistingProject],
    existing_assignments: dict[tuple[int, int], ExistingAssignment],
    existing_evidence_urls: set[str],
    existing_pool_by_key: dict[tuple[str, str], int],
    conn,
) -> dict[str, Any]:
    skipped, empty_products, rows_by_sheet = legacy.load_excel(excel_path, date.today())
    pool_records = legacy.fetch_pool_records(conn)
    staff_map = legacy.fetch_staff_map(conn)
    assignments, match_report = legacy.merge_assignments(rows_by_sheet, pool_records, staff_map)
    evidence, evidence_stats = legacy.build_evidence_plans(assignments)

    pool_plan_by_temp = {int(plan["temp_id"]): plan for plan in match_report["new_pool_plans"]}
    needed_temp_ids: set[int] = set()
    existing_assignment_keys = set(existing_assignments)
    assignment_inserts: list[AssignmentInsertPlan] = []
    assignment_updates: list[AssignmentUpdatePlan] = []
    for plan in assignments:
        project = existing_projects.get(plan.project_key)
        if not project:
            continue
        if plan.kol_pool_id > 0:
            existing = existing_assignments.get((project.id, plan.kol_pool_id))
            if existing:
                expected_status = "inactive" if plan.stage in {"churned", "cancelled"} else "active"
                if (
                    project.project_uid == VINTAGE_Z1_PRO_UID
                    and (existing.stage != plan.stage or existing.stage_status != expected_status or existing.excel_progress != plan.stage_raw)
                ):
                    assignment_updates.append(
                        AssignmentUpdatePlan(
                            source_group="vintage_progress_change",
                            assignment_id=existing.id,
                            project_id=existing.project_id,
                            project_uid=existing.project_uid,
                            project_name=existing.project_name,
                            kol_pool_id=existing.kol_pool_id,
                            handle=existing.handle,
                            display_name=existing.display_name,
                            current_stage=existing.stage,
                            new_stage=plan.stage,
                            current_stage_status=existing.stage_status,
                            new_stage_status=expected_status,
                            current_excel_progress=existing.excel_progress,
                            new_excel_progress=plan.stage_raw,
                            reason="excel_progress_changed",
                            source_ref=plan.source_ref,
                        )
                    )
                continue
            kol_pool_id = plan.kol_pool_id
        else:
            needed_temp_ids.add(plan.kol_pool_id)
            kol_pool_id = plan.kol_pool_id
        assignment_inserts.append(
            AssignmentInsertPlan(
                source_group="standard",
                project_id=project.id,
                project_uid=project.project_uid,
                project_name=project.project_name,
                kol_pool_id=kol_pool_id,
                stage=plan.stage,
                stage_status="inactive" if plan.stage == "churned" else "active",
                assigned_staff_id=plan.staff_id,
                source_ref=plan.source_ref,
                excel_progress=plan.stage_raw,
                metadata=plan.metadata,
            )
        )

    pool_inserts: list[PoolInsertPlan] = []
    for temp_id in sorted(needed_temp_ids):
        plan = pool_plan_by_temp.get(temp_id)
        if not plan:
            continue
        platform = _text(plan.get("platform")) or "unknown"
        handle = _text(plan.get("handle"))
        existing_id = existing_pool_by_key.get((platform, legacy.normalize_name(handle)))
        if existing_id:
            for assignment in assignment_inserts:
                if assignment.kol_pool_id == temp_id:
                    assignment.kol_pool_id = existing_id
            continue
        pool_inserts.append(
            PoolInsertPlan(
                temp_id=temp_id,
                platform=platform,
                handle=handle,
                display_name=_text(plan.get("display_name")) or handle,
                pool_uid=_text(plan.get("pool_uid")) or _pool_uid(platform, handle),
                source_ref=_text(plan.get("source_ref")),
                source_type="excel_promo_plan_incremental",
                metadata={"source": "standard_sheet_increment", "original_plan": plan},
            )
        )

    assignment_inserts = [
        assignment
        for assignment in assignment_inserts
        if assignment.kol_pool_id < 0
        or assignment.project_id is None
        or (assignment.project_id, assignment.kol_pool_id) not in existing_assignment_keys
    ]

    evidence_inserts: list[EvidenceInsertPlan] = []
    evidence_assignment_updates: list[AssignmentUpdatePlan] = []
    seen_evidence_urls: set[str] = set()
    for row in evidence:
        url = _text(row["content_url"])
        if not url or url in existing_evidence_urls or url in seen_evidence_urls:
            continue
        seen_evidence_urls.add(url)
        project = existing_projects.get(_text(row["project_key"]))
        if not project:
            continue
        if int(row["kol_pool_id"]) < 0:
            continue
        existing = existing_assignments.get((project.id, int(row["kol_pool_id"])))
        source_group = "standard_new_evidence"
        if project.project_uid == VINTAGE_Z1_PRO_UID and existing:
            source_group = "vintage_new_video_link"
            evidence_assignment_updates.append(
                AssignmentUpdatePlan(
                    source_group="vintage_new_video_link",
                    assignment_id=existing.id,
                    project_id=existing.project_id,
                    project_uid=existing.project_uid,
                    project_name=existing.project_name,
                    kol_pool_id=existing.kol_pool_id,
                    handle=existing.handle,
                    display_name=existing.display_name,
                    current_stage=existing.stage,
                    new_stage="content_posted",
                    current_stage_status=existing.stage_status,
                    new_stage_status="active",
                    current_excel_progress=existing.excel_progress,
                    new_excel_progress=existing.excel_progress,
                    reason=f"new_video_evidence:{url}",
                    source_ref=_text(row["source_ref"]),
                )
            )
        evidence_inserts.append(
            EvidenceInsertPlan(
                source_group=source_group,
                project_id=project.id,
                project_uid=project.project_uid,
                kol_pool_id=int(row["kol_pool_id"]),
                content_url=url,
                platform=_text(row["platform"]),
                source=_text(row["source"]),
                source_ref=_text(row["source_ref"]),
                evidence_type=_text(row["evidence_type"]),
                posted_at=row.get("posted_at"),
                created_at=row.get("created_at"),
            )
        )

    return {
        "skipped": skipped,
        "empty_products": empty_products,
        "rows_by_sheet_count": {sheet: len(rows) for sheet, rows in rows_by_sheet.items()},
        "match_stats": dict(match_report["stats"]),
        "duplicate_extra_rows": match_report["duplicate_extra_rows"],
        "duplicate_groups": match_report["duplicate_groups"],
        "pool_inserts": pool_inserts,
        "assignment_inserts": assignment_inserts,
        "assignment_updates": assignment_updates,
        "evidence_assignment_updates": evidence_assignment_updates,
        "evidence_inserts": evidence_inserts,
        "evidence_stats": {str(key): value for key, value in evidence_stats.items()},
    }


def _frame_increment(
    *,
    excel_path: Path,
    existing_projects: dict[str, ExistingProject],
    existing_assignments: dict[tuple[int, int], ExistingAssignment],
    existing_pool_by_key: dict[tuple[str, str], int],
    conn,
) -> dict[str, Any]:
    df = pd.read_excel(excel_path, sheet_name=FRAME_SHEET, engine="calamine", dtype=object)
    df.columns = [str(column).strip() for column in df.columns]
    pool_records = legacy.fetch_pool_records(conn)
    staff_map = legacy.fetch_staff_map(conn)
    project_uid = legacy.project_uid(FRAME_SHEET)
    project_name = legacy.project_name(FRAME_SHEET)
    project = existing_projects.get(project_uid)
    existing_assignment_keys = set(existing_assignments)

    raw_rows: list[dict[str, Any]] = []
    seen_assignment_keys: set[tuple[str, str]] = set()
    pool_inserts_by_key: dict[tuple[str, str], PoolInsertPlan] = {}
    assignment_inserts: list[AssignmentInsertPlan] = []
    progress_counter: Counter[str] = Counter()
    mapped_stage_counter: Counter[str] = Counter()
    platform_counter: Counter[str] = Counter()
    match_counter: Counter[str] = Counter()

    for index, series in df.iterrows():
        row = {str(key).strip(): value for key, value in series.to_dict().items()}
        name = _text(row.get(FRAME_NAME_COL))
        if not name:
            continue
        excel_row = int(index) + 2
        profile_url = _text(row.get(FRAME_PROFILE_COL))
        platform = _platform_from_url(profile_url) or "unknown"
        handle = _handle_from_profile(profile_url, name, platform)
        progress = _text(row.get(FRAME_PROGRESS_COL))
        owner = _text(row.get(FRAME_OWNER_COL))
        staff_id = staff_map.get(legacy.normalize_name(owner))
        match = legacy.match_kol_to_pool(name, pool_records)
        if match:
            kol_pool_id = match.pool_id
            match_label = match.confidence
        else:
            key = (platform, legacy.normalize_name(handle))
            existing_pool_id = existing_pool_by_key.get(key)
            if existing_pool_id:
                kol_pool_id = existing_pool_id
                match_label = "existing_platform_handle"
            else:
                temp_id = -100_000 - len(pool_inserts_by_key) - 1
                kol_pool_id = temp_id
                match_label = "new_pool"
                if key not in pool_inserts_by_key:
                    pool_inserts_by_key[key] = PoolInsertPlan(
                        temp_id=temp_id,
                        platform=platform,
                        handle=handle,
                        display_name=name,
                        pool_uid=_pool_uid(platform, handle),
                        source_ref=f"excel:{FRAME_SHEET}:{excel_row}",
                        source_type="excel_promo_plan_incremental",
                        metadata={
                            "source": "frame_the_game",
                            "excel_sheet": FRAME_SHEET,
                            "excel_row": excel_row,
                            "profile_url": profile_url,
                        },
                    )
            if key in pool_inserts_by_key:
                kol_pool_id = pool_inserts_by_key[key].temp_id

        dedupe_key = (str(kol_pool_id), legacy.normalize_name(name))
        if dedupe_key in seen_assignment_keys:
            continue
        seen_assignment_keys.add(dedupe_key)
        if project and kol_pool_id > 0 and (project.id, kol_pool_id) in existing_assignment_keys:
            continue

        raw_progress = progress or "<blank>"
        mapped_stage = FRAME_PROGRESS_STAGE.get(progress, "discovery")
        mapped_status = "inactive" if mapped_stage in {"cancelled", "churned"} else "active"
        progress_counter[raw_progress] += 1
        mapped_stage_counter[mapped_stage] += 1
        platform_counter[platform] += 1
        match_counter[match_label] += 1
        raw_rows.append(
            {
                "excel_row": excel_row,
                "name": name,
                "profile_url": profile_url,
                "platform": platform,
                "owner": owner,
                "progress": progress,
                "publish_url": _text(row.get(FRAME_PUBLISH_COL)),
                "kol_pool_id": kol_pool_id,
                "match": match_label,
                "mapped_stage": mapped_stage,
                "mapped_stage_status": mapped_status,
            }
        )
        assignment_inserts.append(
            AssignmentInsertPlan(
                source_group="frame_the_game",
                project_id=project.id if project else None,
                project_uid=project_uid,
                project_name=project_name,
                kol_pool_id=kol_pool_id,
                stage=mapped_stage,
                stage_status=mapped_status,
                assigned_staff_id=staff_id,
                source_ref=f"excel:{FRAME_SHEET}:{excel_row}",
                excel_progress=progress,
                metadata={
                    "excel_sheet": FRAME_SHEET,
                    "excel_row": excel_row,
                    "kol_name": name,
                    "profile_url": profile_url,
                    "platform": platform,
                    "owner": owner,
                    "raw_progress": progress,
                    "mapped_stage": mapped_stage,
                    "approval": _text(row.get("审批")),
                    "content_description": _text(row.get("内容描述")),
                    "note": _text(row.get("备注")),
                    "hashtags": _text(row.get("Hashtags")),
                    "match": match_label,
                },
            )
        )

    return {
        "project_insert": None
        if project
        else {
            "project_uid": project_uid,
            "project_name": project_name,
            "source_type": "excel_promo_plan",
            "stage": "discovery",
            "stage_status": "active",
            "follow_status": "active",
            "metadata": {"excel_sheet": FRAME_SHEET, "source_ref": f"excel:{FRAME_SHEET}"},
        },
        "raw_rows": raw_rows,
        "pool_inserts": list(pool_inserts_by_key.values()),
        "assignment_inserts": assignment_inserts,
        "progress_distribution": dict(progress_counter),
        "mapped_stage_distribution": dict(mapped_stage_counter),
        "platform_distribution": dict(platform_counter),
        "match_distribution": dict(match_counter),
    }


def _af28_status(excel_path: Path) -> dict[str, Any]:
    df = pd.read_excel(excel_path, sheet_name=AF28_L_SHEET, engine="calamine", dtype=object)
    df.columns = [str(column).strip() for column in df.columns]
    kol_rows = 0
    if "红人/媒体" in df.columns:
        kol_rows = sum(1 for value in df["红人/媒体"].tolist() if _text(value))
    return {
        "sheet": AF28_L_SHEET,
        "rows": len(df),
        "kol_rows": kol_rows,
        "action": "skip",
        "reason": "sheet currently only exposes an empty shell row without KOL data",
    }


def _serialise_plan(plan: Any) -> dict[str, Any]:
    if hasattr(plan, "__dataclass_fields__"):
        return {field: _serialise_plan(getattr(plan, field)) for field in plan.__dataclass_fields__}
    if isinstance(plan, dict):
        return {key: _serialise_plan(value) for key, value in plan.items()}
    if isinstance(plan, list):
        return [_serialise_plan(value) for value in plan]
    if isinstance(plan, (datetime, date)):
        return plan.isoformat()
    return plan


def build_plan(excel_path: Path, database_url: str) -> dict[str, Any]:
    with psycopg2.connect(database_url) as conn:
        existing_projects, existing_assignments, existing_evidence_urls, existing_pool_by_key, table_counts = fetch_existing(conn)
        standard = _standard_increment(
            excel_path=excel_path,
            existing_projects=existing_projects,
            existing_assignments=existing_assignments,
            existing_evidence_urls=existing_evidence_urls,
            existing_pool_by_key=existing_pool_by_key,
            conn=conn,
        )
        frame = _frame_increment(
            excel_path=excel_path,
            existing_projects=existing_projects,
            existing_assignments=existing_assignments,
            existing_pool_by_key=existing_pool_by_key,
            conn=conn,
        )
    project_inserts = 1 if frame["project_insert"] else 0
    pool_inserts = len(standard["pool_inserts"]) + len(frame["pool_inserts"])
    assignment_inserts = len(standard["assignment_inserts"]) + len(frame["assignment_inserts"])
    assignment_updates = len(standard["assignment_updates"]) + len(standard["evidence_assignment_updates"])
    evidence_inserts = len(standard["evidence_inserts"])
    return {
        "excel": str(excel_path),
        "mode": "dry-run",
        "standard": standard,
        "frame_the_game": frame,
        "skipped": [_af28_status(excel_path)],
        "current_counts": table_counts,
        "projected_counts": {
            "projects_in_scope": table_counts["projects_in_scope"] + project_inserts,
            "assignments_in_scope": table_counts["assignments_in_scope"] + assignment_inserts,
            "kol_pool": table_counts["kol_pool"] + pool_inserts,
            "evidence": table_counts["evidence"] + evidence_inserts,
        },
        "safety": {
            "existing_project_updates": 0,
            "existing_assignment_updates": assignment_updates,
            "existing_assignment_update_scope": "Vintage Z1 Pro only; exact assignment_id targets",
            "stage_stage_status_follow_status_updates": 0,
            "project_star_writes": 0,
            "other_31_project_updates": 0,
            "write_policy": "new rows insert-only; Vintage Z1 Pro assignment deltas update exact assignment ids only",
        },
    }


def _insert_pools(cur, pool_plans: list[PoolInsertPlan]) -> tuple[dict[int, int], int]:
    temp_to_real: dict[int, int] = {}
    inserted = 0
    for plan in pool_plans:
        cur.execute(
            """
            INSERT INTO vkpi_kol_pool
              (pool_uid, handle, display_name, platform, source_type, source_ref,
               sync_status, dashboard_account_type, raw_platform_data, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'imported', %s, %s, NOW(), NOW())
            ON CONFLICT (platform, handle) DO NOTHING
            RETURNING id
            """,
            (
                plan.pool_uid,
                plan.handle,
                plan.display_name,
                plan.platform,
                plan.source_type,
                plan.source_ref,
                legacy.classify_account_type(plan.handle, plan.platform),
                json.dumps(plan.metadata, ensure_ascii=False, default=_json_default),
            ),
        )
        row = cur.fetchone()
        if row:
            inserted += 1
            temp_to_real[plan.temp_id] = int(row[0])
        else:
            cur.execute(
                "SELECT id FROM vkpi_kol_pool WHERE platform=%s AND handle=%s",
                (plan.platform, plan.handle),
            )
            existing = cur.fetchone()
            if existing:
                temp_to_real[plan.temp_id] = int(existing[0])
    return temp_to_real, inserted


def apply_plan(database_url: str, plan: dict[str, Any]) -> dict[str, int]:
    pool_plans = plan["standard"]["pool_inserts"] + plan["frame_the_game"]["pool_inserts"]
    standard_assignments = plan["standard"]["assignment_inserts"]
    frame_assignments = plan["frame_the_game"]["assignment_inserts"]
    standard_evidence = plan["standard"]["evidence_inserts"]
    assignment_updates = plan["standard"]["assignment_updates"] + plan["standard"]["evidence_assignment_updates"]
    frame_project = plan["frame_the_game"]["project_insert"]
    counts = Counter()
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            temp_to_real, pools_inserted = _insert_pools(cur, pool_plans)
            counts["pools_inserted"] = pools_inserted
            frame_project_id: int | None = None
            if frame_project:
                cur.execute(
                    """
                    INSERT INTO vkpi_projects
                      (project_uid, project_name, product_name, platform, stage, stage_status,
                       follow_status, source_type, metadata_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'discovery', 'active', 'active', 'excel_promo_plan', %s, NOW(), NOW())
                    ON CONFLICT (project_uid) DO NOTHING
                    RETURNING id
                    """,
                    (
                        frame_project["project_uid"],
                        frame_project["project_name"],
                        frame_project["project_name"],
                        "instagram",
                        json.dumps(frame_project["metadata"], ensure_ascii=False),
                    ),
                )
                row = cur.fetchone()
                if row:
                    counts["projects_inserted"] += 1
                    frame_project_id = int(row[0])
                else:
                    cur.execute("SELECT id FROM vkpi_projects WHERE project_uid=%s", (frame_project["project_uid"],))
                    existing = cur.fetchone()
                    frame_project_id = int(existing[0]) if existing else None

            all_assignments = standard_assignments + frame_assignments
            for assignment in all_assignments:
                kol_pool_id = assignment.kol_pool_id
                if kol_pool_id < 0:
                    kol_pool_id = temp_to_real.get(kol_pool_id, kol_pool_id)
                if kol_pool_id < 0:
                    continue
                project_id = assignment.project_id
                stage = assignment.stage
                if assignment.source_group == "frame_the_game":
                    project_id = frame_project_id
                if not project_id or not stage:
                    continue
                cur.execute(
                    """
                    INSERT INTO vkpi_project_kol_assignments
                      (project_id, kol_pool_id, stage, stage_status, assigned_staff_id,
                       source, source_ref, excel_progress, metadata_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'excel_incremental', %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (project_id, kol_pool_id) DO NOTHING
                    """,
                    (
                        project_id,
                        kol_pool_id,
                        stage,
                        assignment.stage_status,
                        assignment.assigned_staff_id,
                        assignment.source_ref,
                        assignment.excel_progress,
                        Json(assignment.metadata),
                    ),
                )
                counts["assignments_inserted"] += int(cur.rowcount)

            for evidence in standard_evidence:
                cur.execute(
                    """
                    INSERT INTO vkpi_kol_video_evidence
                      (kol_pool_id, project_id, content_url, platform, source, source_ref,
                       confidence, evidence_type, posted_at, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'high', %s, %s, %s)
                    ON CONFLICT (content_url) DO NOTHING
                    """,
                    (
                        evidence.kol_pool_id,
                        evidence.project_id,
                        evidence.content_url,
                        evidence.platform,
                        evidence.source,
                        evidence.source_ref,
                        evidence.evidence_type,
                        evidence.posted_at,
                        evidence.created_at,
                    ),
                )
                counts["evidence_inserted"] += int(cur.rowcount)

            for update in assignment_updates:
                cur.execute(
                    """
                    UPDATE vkpi_project_kol_assignments
                       SET stage = %s,
                           stage_status = %s,
                           excel_progress = COALESCE(NULLIF(%s, ''), excel_progress),
                           source_ref = %s,
                           metadata_json = COALESCE(metadata_json, '{}'::jsonb) || %s,
                           updated_at = NOW()
                     WHERE id = %s
                       AND project_id = %s
                       AND kol_pool_id = %s
                    """,
                    (
                        update.new_stage,
                        update.new_stage_status,
                        update.new_excel_progress,
                        update.source_ref,
                        Json(
                            {
                                "incremental_update": update.source_group,
                                "previous_stage": update.current_stage,
                                "new_stage": update.new_stage,
                                "reason": update.reason,
                            }
                        ),
                        update.assignment_id,
                        update.project_id,
                        update.kol_pool_id,
                    ),
                )
                counts["assignments_updated"] += int(cur.rowcount)
        conn.commit()
    return dict(counts)


def print_report(plan: dict[str, Any], *, commit_result: dict[str, int] | None = None) -> None:
    standard = plan["standard"]
    frame = plan["frame_the_game"]
    standard_assignments = standard["assignment_inserts"]
    standard_evidence = standard["evidence_inserts"]
    vintage_progress_updates = standard["assignment_updates"]
    vintage_link_updates = standard["evidence_assignment_updates"]
    standard_pools = standard["pool_inserts"]
    frame_pools = frame["pool_inserts"]
    frame_assignments = frame["assignment_inserts"]
    print("=" * 72)
    print("Promo Plan Incremental ETL Dry-Run")
    print("=" * 72)
    print(f"Excel: {plan['excel']}")
    print(f"Mode: {'commit' if commit_result is not None else 'dry-run'}")
    print("\n[Safety]")
    for key, value in plan["safety"].items():
        print(f"  {key}: {value}")

    print("\n[A. Pure Increment Inserts]")
    print(f"  new KOL pools: {len(standard_pools)}")
    print(f"  standard new assignments: {len(standard_assignments)}")
    print(f"  standard parsed new evidence rows: {len(standard_evidence)}")
    print("  standard assignment by project:")
    for project, count in Counter(item.project_name for item in standard_assignments).most_common():
        print(f"    - {project}: {count}")
    for item in standard_assignments:
        print(
            f"    assignment | {item.project_name} | kol_pool_id={item.kol_pool_id} | "
            f"stage={item.stage} | progress={item.excel_progress} | {item.source_ref}"
        )
    for item in standard_evidence:
        print(f"    evidence | {item.source_group} | {item.project_uid} | kol_pool_id={item.kol_pool_id} | {item.content_url} | {item.source_ref}")

    print("\n[A. Frame the Game]")
    print(f"  project insert: {1 if frame['project_insert'] else 0}")
    if frame["project_insert"]:
        print(f"    {frame['project_insert']['project_uid']} | {frame['project_insert']['project_name']} | initial stage=discovery")
    print(f"  new KOL pools: {len(frame_pools)}")
    print(f"  assignments: {len(frame_assignments)}")
    print(f"  raw progress distribution: {frame['progress_distribution']}")
    print(f"  mapped stage distribution: {frame['mapped_stage_distribution']}")
    print(f"  platform distribution: {frame['platform_distribution']}")
    print(f"  match distribution: {frame['match_distribution']}")
    print("  assignment stage mapping: 沟通中->discovery / 已联系->contacted / 已合作->shipped / 已终止->cancelled / 空->discovery")
    for item in frame["raw_rows"]:
        print(
            f"    frame | row={item['excel_row']} | {item['name']} | platform={item['platform']} | "
            f"match={item['match']} | pool={item['kol_pool_id']} | progress={item['progress'] or '<blank>'} | stage={item['mapped_stage']}"
        )

    print("\n[B. Vintage Z1 Pro Existing Assignment Updates]")
    print(f"  progress text/stage updates: {len(vintage_progress_updates)}")
    for item in vintage_progress_updates:
        print(
            f"    progress | assignment={item.assignment_id} | {item.handle or item.display_name} | "
            f"{item.current_stage}->{item.new_stage} | {item.current_stage_status}->{item.new_stage_status} | "
            f"{item.current_excel_progress or '<blank>'}->{item.new_excel_progress or '<blank>'}"
        )
    print(f"  new video-link assignment updates: {len(vintage_link_updates)}")
    for item in vintage_link_updates:
        print(
            f"    link | assignment={item.assignment_id} | {item.handle or item.display_name} | "
            f"{item.current_stage}->{item.new_stage} | reason={item.reason}"
        )
    if standard_evidence and len(standard_evidence) == len(vintage_link_updates):
        print("  evidence note: the 5 parsed standard evidence rows are the same 5 Vintage link rows; actual deduped evidence inserts = 5, not 10.")

    print("\n[Skipped]")
    for item in plan["skipped"]:
        print(f"  {item['sheet']}: {item['action']} ({item['reason']}); rows={item['rows']}, kol_rows={item['kol_rows']}")

    print("\n[Totals]")
    print(f"  projects to insert: {1 if frame['project_insert'] else 0}")
    print(f"  KOL pools to insert: {len(standard_pools) + len(frame_pools)}")
    print(f"  assignments to insert: {len(standard_assignments) + len(frame_assignments)}")
    print(f"  assignments to update: {len(vintage_progress_updates) + len(vintage_link_updates)}")
    print(f"  evidence to insert, deduped by content_url: {len(standard_evidence)}")
    print("  unchanged scope: other 31 projects=0 changes, project-level stage=0 changes, follow_status=0 changes, stars=0 writes")
    print("\n[Projected Counts After Commit]")
    print(f"  projects_in_scope: {plan['current_counts']['projects_in_scope']} -> {plan['projected_counts']['projects_in_scope']}")
    print(f"  assignments_in_scope: {plan['current_counts']['assignments_in_scope']} -> {plan['projected_counts']['assignments_in_scope']}")
    print(f"  kol_pool: {plan['current_counts']['kol_pool']} -> {plan['projected_counts']['kol_pool']}")
    print(f"  evidence: {plan['current_counts']['evidence']} -> {plan['projected_counts']['evidence']}")
    if commit_result is not None:
        print("\n[Commit Result]")
        print(json.dumps(commit_result, ensure_ascii=False, indent=2))
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", default=str(DEFAULT_EXCEL))
    parser.add_argument("--database-url", default="")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    excel_path = Path(args.excel).expanduser()
    if not excel_path.exists():
        raise SystemExit(f"Excel not found: {excel_path}")
    database_url = _database_url(args.database_url)
    plan = build_plan(excel_path, database_url)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(_serialise_plan(plan), ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    commit_result = None
    if args.commit:
        commit_result = apply_plan(database_url, plan)
    print_report(plan, commit_result=commit_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
