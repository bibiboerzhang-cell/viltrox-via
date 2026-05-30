#!/usr/bin/env python3
"""Backfill project assignment owners from project_kol_matrix.csv.

Default mode is read-only. Use --commit-staff to create missing placeholder
staff rows. Use --commit-assignments only after reviewing the dry-run output.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor
from rapidfuzz import fuzz, process

from app.core.permissions import default_permissions_for_role
from app.core.security import hash_password


DEFAULT_MATRIX = Path("/Users/bibiboer/Downloads/vkpi-final/data/project_kol_matrix.csv")
PLACEHOLDER_DOMAIN = "pending.viltrox.local"
VALID_STAFF_RE = re.compile(r"^[\u4e00-\u9fff]{2,4}$")


@dataclass(frozen=True)
class PoolRecord:
    id: int
    handle: str
    display_name: str


@dataclass
class MatrixRow:
    row_number: int
    project_name: str
    kol_name: str
    kol_original: str
    staff_name: str
    platform: str
    stage_raw: str


@dataclass
class AssignmentHit:
    assignment_id: int
    project_id: int
    project_name: str
    kol_pool_id: int
    kol_name: str
    staff_name: str
    staff_id: int
    match_confidence: str
    matched_via: str
    matrix_row: int


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)


def text(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value).strip()
    if raw.lower() in {"nan", "none", "null"}:
        return ""
    return raw


def normalize_name(value: str) -> str:
    cleaned = re.split(r"\s*-?\s*【", text(value))[0].strip().lower()
    return re.sub(r"[\s\.\-_@]+", "", cleaned)


def placeholder_email(name: str) -> str:
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:10]
    return f"staff-{digest}@{PLACEHOLDER_DOMAIN}"


def placeholder_creator_code(name: str) -> str:
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:12]
    return f"staff_{digest}"


def is_valid_staff_name(value: str) -> bool:
    return bool(VALID_STAFF_RE.match(text(value)))


def load_matrix(path: Path) -> list[MatrixRow]:
    rows: list[MatrixRow] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader, start=2):
            rows.append(
                MatrixRow(
                    row_number=index,
                    project_name=text(row.get("项目名")),
                    kol_name=text(row.get("KOL名")),
                    kol_original=text(row.get("KOL原名")),
                    staff_name=text(row.get("对接人")),
                    platform=text(row.get("平台")),
                    stage_raw=text(row.get("合作进度")),
                )
            )
    return rows


def fetch_existing_staff(conn) -> dict[str, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id AS staff_id, s.user_id, u.name, u.email, s.role, s.active
            FROM staff s
            LEFT JOIN users u ON u.id = s.user_id
            ORDER BY s.id
            """
        )
        staff: dict[str, dict[str, Any]] = {}
        for row in cur.fetchall():
            name = text(row.get("name"))
            if name:
                staff[name] = dict(row)
        return staff


def ensure_staff(conn, names: list[str], *, commit: bool) -> dict[str, dict[str, Any]]:
    existing = fetch_existing_staff(conn)
    missing = [name for name in names if name not in existing]
    created: dict[str, dict[str, Any]] = {}
    if not commit:
        return {**existing, **{name: {"staff_id": None, "email": placeholder_email(name), "pending_create": True} for name in missing}}

    permissions = json.dumps(default_permissions_for_role("employee"), ensure_ascii=False)
    with conn.cursor() as cur:
        for name in missing:
            email = placeholder_email(name)
            creator_code = placeholder_creator_code(name)
            password_hash = hash_password(f"staff-placeholder:{email}:{secrets.token_urlsafe(24)}")
            cur.execute(
                """
                INSERT INTO users
                  (email, password_hash, name, creator_code, status, role, email_verified)
                VALUES
                  (%s, %s, %s, %s, 'pending', 'creator', 0)
                ON CONFLICT (email) DO UPDATE
                  SET name = EXCLUDED.name
                RETURNING id, email, name
                """,
                (email, password_hash, name, creator_code),
            )
            user_row = cur.fetchone()
            cur.execute(
                """
                INSERT INTO staff
                  (user_id, role, permissions_json, mfa_enabled, active, invited_by, invited_at,
                   is_owner, email_domain_verified, invited_by_staff_id)
                VALUES
                  (%s, 'employee', %s, 0, 1, NULL, now(), 0, 0, NULL)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (int(user_row["id"]), permissions),
            )
            staff_row = cur.fetchone()
            if not staff_row:
                cur.execute("SELECT id FROM staff WHERE user_id = %s ORDER BY id DESC LIMIT 1", (int(user_row["id"]),))
                staff_row = cur.fetchone()
            created[name] = {
                "staff_id": int(staff_row["id"]),
                "user_id": int(user_row["id"]),
                "name": name,
                "email": user_row["email"],
                "role": "employee",
                "active": 1,
                "created": True,
            }
    conn.commit()
    return fetch_existing_staff(conn) | created


def fetch_projects(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, project_name
            FROM vkpi_projects
            WHERE source_type = 'excel_promo_plan'
            """
        )
        return {text(row["project_name"]): int(row["id"]) for row in cur.fetchall()}


def fetch_pool(conn) -> list[PoolRecord]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, handle, display_name FROM vkpi_kol_pool")
        return [
            PoolRecord(int(row["id"]), text(row["handle"]), text(row["display_name"]))
            for row in cur.fetchall()
        ]


def fetch_assignments(conn) -> dict[tuple[int, int], int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, project_id, kol_pool_id FROM vkpi_project_kol_assignments")
        return {
            (int(row["project_id"]), int(row["kol_pool_id"])): int(row["id"])
            for row in cur.fetchall()
        }


def match_pool(name: str, pool: list[PoolRecord]) -> tuple[PoolRecord | None, str, str]:
    norm = normalize_name(name)
    if not norm:
        return None, "blank", ""
    for record in pool:
        if norm == normalize_name(record.handle) or norm == normalize_name(record.display_name):
            return record, "exact", record.handle
    candidates = [(f"{record.handle}|{record.display_name}", record) for record in pool]
    best = process.extractOne(name, [item[0] for item in candidates], scorer=fuzz.token_sort_ratio)
    if best and best[1] >= 90:
        record = candidates[int(best[2])][1]
        return record, f"fuzzy_{int(best[1])}", record.handle
    return None, "unmatched", ""


def build_assignment_hits(
    rows: list[MatrixRow],
    *,
    staff_by_name: dict[str, dict[str, Any]],
    projects_by_name: dict[str, int],
    pool: list[PoolRecord],
    assignments: dict[tuple[int, int], int],
) -> tuple[dict[int, AssignmentHit], dict[str, Counter], list[AssignmentHit]]:
    failures: dict[str, Counter] = defaultdict(Counter)
    hits_by_assignment: dict[int, list[AssignmentHit]] = defaultdict(list)
    for row in rows:
        if not is_valid_staff_name(row.staff_name):
            failures["invalid_staff"][row.staff_name or "<blank>"] += 1
            continue
        staff = staff_by_name.get(row.staff_name)
        if not staff or not staff.get("staff_id"):
            failures["missing_staff_id"][row.staff_name] += 1
            continue
        project_id = projects_by_name.get(row.project_name)
        if not project_id:
            failures["project_unmatched"][row.project_name or "<blank>"] += 1
            continue
        pool_record, confidence, matched_via = match_pool(row.kol_name or row.kol_original, pool)
        if not pool_record:
            failures["kol_unmatched"][row.kol_name or row.kol_original or "<blank>"] += 1
            continue
        assignment_id = assignments.get((project_id, pool_record.id))
        if not assignment_id:
            failures["assignment_unmatched"][f"{row.project_name} | {row.kol_name or row.kol_original}"] += 1
            continue
        hits_by_assignment[assignment_id].append(
            AssignmentHit(
                assignment_id=assignment_id,
                project_id=project_id,
                project_name=row.project_name,
                kol_pool_id=pool_record.id,
                kol_name=row.kol_name or row.kol_original,
                staff_name=row.staff_name,
                staff_id=int(staff["staff_id"]),
                match_confidence=confidence,
                matched_via=matched_via,
                matrix_row=row.row_number,
            )
        )

    chosen: dict[int, AssignmentHit] = {}
    conflicts: list[AssignmentHit] = []
    for assignment_id, hits in hits_by_assignment.items():
        counts = Counter(hit.staff_id for hit in hits)
        chosen_staff_id, _ = counts.most_common(1)[0]
        chosen_hit = next(hit for hit in hits if hit.staff_id == chosen_staff_id)
        chosen[assignment_id] = chosen_hit
        if len(counts) > 1:
            conflicts.extend(hits)
    return chosen, failures, conflicts


def current_assignment_distribution(conn) -> Counter:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(u.name, '<null>') AS staff_name, COUNT(*) AS count
            FROM vkpi_project_kol_assignments a
            LEFT JOIN staff s ON s.id = a.assigned_staff_id
            LEFT JOIN users u ON u.id = s.user_id
            GROUP BY COALESCE(u.name, '<null>')
            ORDER BY count DESC
            """
        )
        return Counter({text(row["staff_name"]): int(row["count"]) for row in cur.fetchall()})


def apply_assignments(conn, chosen: dict[int, AssignmentHit]) -> int:
    if not chosen:
        return 0
    with conn.cursor() as cur:
        updates = [(hit.staff_id, assignment_id) for assignment_id, hit in chosen.items()]
        cur.executemany(
            "UPDATE vkpi_project_kol_assignments SET assigned_staff_id = %s, updated_at = now() WHERE id = %s",
            updates,
        )
        count = cur.rowcount
    conn.commit()
    return count


def print_counter(title: str, counter: Counter, *, limit: int = 20) -> None:
    print(title)
    for key, count in counter.most_common(limit):
        print(f"  {key}: {count}")
    if len(counter) > limit:
        print(f"  ... {len(counter) - limit} more")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--commit-staff", action="store_true", help="Create missing placeholder users/staff.")
    parser.add_argument("--commit-assignments", action="store_true", help="Write assigned_staff_id updates.")
    parser.add_argument("--sample", type=int, default=20)
    args = parser.parse_args()

    rows = load_matrix(args.matrix)
    valid_staff = sorted({row.staff_name for row in rows if is_valid_staff_name(row.staff_name)})
    invalid_staff_counter = Counter(row.staff_name or "<blank>" for row in rows if not is_valid_staff_name(row.staff_name))

    conn = connect()
    try:
        staff_by_name = ensure_staff(conn, valid_staff, commit=args.commit_staff)
        projects_by_name = fetch_projects(conn)
        pool = fetch_pool(conn)
        assignments = fetch_assignments(conn)
        chosen, failures, conflicts = build_assignment_hits(
            rows,
            staff_by_name=staff_by_name,
            projects_by_name=projects_by_name,
            pool=pool,
            assignments=assignments,
        )

        print("=" * 72)
        print("A-3.5 assignment staff backfill report")
        print("=" * 72)
        print(f"matrix: {args.matrix}")
        print(f"matrix rows: {len(rows)}")
        print(f"valid staff names: {len(valid_staff)}")
        print(f"commit_staff: {args.commit_staff}")
        print(f"commit_assignments: {args.commit_assignments}")
        print()

        print("[staff mapping]")
        for name in valid_staff:
            staff = staff_by_name.get(name, {})
            marker = "existing" if not staff.get("created") and not staff.get("pending_create") else "created" if staff.get("created") else "pending_create"
            print(f"  {name} -> staff_id={staff.get('staff_id')} email={staff.get('email')} {marker}")
        print_counter("[invalid/blank staff values skipped]", invalid_staff_counter, limit=30)
        print()

        print("[assignment dry-run]")
        print(f"  assignments total: {len(assignments)}")
        print(f"  assignments matched for staff update: {len(chosen)}")
        print(f"  assignment staff conflicts: {len({hit.assignment_id for hit in conflicts})}")
        for reason, counter in failures.items():
            print_counter(f"  failures.{reason}", counter, limit=10)
        print_counter("  planned staff distribution", Counter(hit.staff_name for hit in chosen.values()), limit=30)
        print()

        print(f"[sample {args.sample}]")
        for hit in list(chosen.values())[: args.sample]:
            print(
                "  "
                f"assignment={hit.assignment_id} project={hit.project_name} "
                f"kol={hit.kol_name} staff={hit.staff_name}/{hit.staff_id} "
                f"match={hit.match_confidence}:{hit.matched_via} row={hit.matrix_row}"
            )
        print()

        print_counter("[current db assigned_staff distribution before assignment commit]", current_assignment_distribution(conn), limit=30)

        if args.commit_assignments:
            updated = apply_assignments(conn, chosen)
            print()
            print(f"[commit] updated assignments: {updated}")
            print_counter("[current db assigned_staff distribution after assignment commit]", current_assignment_distribution(conn), limit=30)
        else:
            print()
            print("[dry-run] assignment updates not written. Re-run with --commit-assignments after review.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
