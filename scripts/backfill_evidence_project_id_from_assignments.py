#!/usr/bin/env python3
"""Plan or apply safe project_id backfill for video evidence rows.

Default mode is dry-run. A row is considered safe only when its KOL has exactly
one non-deleted project assignment, so the evidence can be attributed without
guessing product names, titles, or dates.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


ARTIFACT_DIR = Path("artifacts")
GENERIC_PROJECT_TERMS = {
    "af",
    "air",
    "evo",
    "fe",
    "lab",
    "lens",
    "mark",
    "mount",
    "nikon",
    "pro",
    "sony",
    "viltrox",
    "xf",
}
BRAND_TERMS = {"viltrox", "nexusfocus", "nexus", "spark"}
SPECIFIC_NAMED_TERMS = {
    "90dl",
    "dca1",
    "dc550",
    "dcl1",
    "dcx2",
    "k90",
    "nexusfocus",
    "raze",
    "sparkz3",
    "vintagez1",
    "vintagez2",
}


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


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_product_terms(value: Any) -> set[str]:
    raw = text(value).lower()
    if not raw:
        return set()

    terms: set[str] = set()
    collapsed = re.sub(r"\s+", "", raw)

    for focal in re.findall(r"(\d{1,3})\s*mm", raw):
        terms.add(f"{focal}mm")
    for focal, aperture in re.findall(r"(\d{1,3})\s*/\s*(\d(?:\.\d)?)", raw):
        terms.add(f"{focal}mm")
        terms.add(f"f{aperture}")
    for aperture in re.findall(r"\bf\s*(\d(?:\.\d)?)", raw):
        terms.add(f"f{aperture}")

    named_patterns = [
        "nexusfocus",
        "sparkz3",
        "spark",
        "vintagez1",
        "vintagez2",
        "frame",
        "dc-a1",
        "dca1",
        "dc-550",
        "dc550",
        "dc-l1",
        "dcl1",
        "dc-x2",
        "dcx2",
        "z1",
        "z2",
        "z3",
    ]
    for pattern in named_patterns:
        if pattern in collapsed or pattern in raw:
            terms.add(pattern.replace("-", ""))

    for word in re.findall(r"[a-z][a-z0-9]{2,}", raw):
        if word not in GENERIC_PROJECT_TERMS:
            terms.add(word)
    return terms


def specific_match_terms(terms: set[str]) -> set[str]:
    """Return terms strong enough to infer a product/project.

    Aperture-only matches such as f1.8 are intentionally excluded because many
    products share the same aperture. Broad category words such as monitor,
    flash, tube, and chip also stay out unless paired into a named product term.
    """
    specific = {term for term in terms if re.fullmatch(r"\d{1,3}mm", term)}
    specific |= terms & SPECIFIC_NAMED_TERMS
    if {"spark", "z3"} <= terms:
        specific.add("sparkz3")
    if {"vintage", "z1"} <= terms:
        specific.add("vintagez1")
    if {"vintage", "z2"} <= terms:
        specific.add("vintagez2")
    return specific


def score_project_match(row: dict[str, Any], all_project_terms: set[str]) -> None:
    row["recommended_decision"] = ""
    row["recommended_project_id"] = ""
    row["recommended_project_name"] = ""
    row["recommended_reason"] = ""

    title = " ".join([text(row.get("title")), text(row.get("content_url"))]).lower()
    title_terms = normalize_product_terms(title)

    if row.get("decision") == "skip_multiple_assignment_projects":
        candidate_projects = parse_candidate_projects(row.get("candidate_projects"))
        scored_candidates: list[dict[str, Any]] = []
        for candidate in candidate_projects:
            candidate_terms = normalize_product_terms(
                " ".join(
                    [
                        text(candidate.get("project_name")),
                        text(candidate.get("product_name")),
                        text(candidate.get("product_sku")),
                    ]
                )
            )
            matched = sorted(candidate_terms & title_terms)
            matched_specific = sorted(specific_match_terms(set(matched)))
            if matched_specific:
                scored_candidates.append(
                    {
                        **candidate,
                        "matched_terms": matched,
                        "matched_specific_terms": matched_specific,
                        "terms": sorted(candidate_terms),
                    }
                )
        if len(scored_candidates) == 1:
            candidate = scored_candidates[0]
            row["recommended_decision"] = "safe_multi_candidate_title_match"
            row["recommended_project_id"] = candidate.get("project_id") or ""
            row["recommended_project_name"] = candidate.get("project_name") or ""
            row["recommended_reason"] = ", ".join(candidate.get("matched_specific_terms") or [])
        elif len(scored_candidates) > 1:
            row["recommended_decision"] = "skip_multi_candidate_ambiguous_title_match"
            row["recommended_reason"] = "; ".join(
                f"{candidate.get('project_name')}: {', '.join(candidate.get('matched_specific_terms') or [])}"
                for candidate in scored_candidates[:5]
            )

    if row.get("decision") != "safe_unique_assignment_project":
        row["project_terms"] = ""
        row["matched_project_terms"] = ""
        row["other_project_terms_in_title"] = ""
        row["attribution_confidence"] = ""
        return

    project_terms = normalize_product_terms(
        " ".join(
            [
                text(row.get("project_name")),
                text(row.get("project_product_name")),
                text(row.get("project_product_sku")),
            ]
        )
    )
    matched_terms = sorted(project_terms & title_terms)
    matched_specific_terms = sorted(specific_match_terms(set(matched_terms)))
    other_terms = sorted((all_project_terms - project_terms) & title_terms)

    has_brand_signal = any(term in title for term in BRAND_TERMS)
    row["project_terms"] = ", ".join(sorted(project_terms))
    row["matched_project_terms"] = ", ".join(matched_terms)
    row["other_project_terms_in_title"] = ", ".join(other_terms)

    if matched_specific_terms and not other_terms:
        row["attribution_confidence"] = "strong_title_product_match"
    elif matched_specific_terms and other_terms:
        row["attribution_confidence"] = "mixed_title_product_signal"
    elif matched_terms:
        row["attribution_confidence"] = "weak_aperture_or_category_match"
    elif has_brand_signal:
        row["attribution_confidence"] = "brand_only_unique_project"
    else:
        row["attribution_confidence"] = "weak_unique_project_only"


def parse_candidate_projects(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [dict(item) for item in parsed if isinstance(item, dict)]
    return []


def fetch_summary(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) AS evidence_total,
                COUNT(*) FILTER (WHERE project_id IS NULL) AS missing_project_id,
                COUNT(*) FILTER (WHERE project_id IS NOT NULL) AS has_project_id
            FROM vkpi_kol_video_evidence
            """
        )
        row = cur.fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}


def fetch_candidate_rows(conn) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH null_evidence AS (
                SELECT
                    e.id,
                    e.kol_pool_id,
                    e.content_url,
                    COALESCE(e.title, e.video_title, '') AS title,
                    e.platform,
                    e.publish_date,
                    e.created_at,
                    e.source,
                    e.scrape_source
                FROM vkpi_kol_video_evidence e
                WHERE e.project_id IS NULL
            ),
            assignment_options AS (
                SELECT
                    e.id AS evidence_id,
                    a.project_id,
                    p.project_name,
                    p.product_name,
                    p.product_sku,
                    p.project_uid,
                    p.source_type,
                    COUNT(*) FILTER (WHERE a.stage IN ('device_sent', 'received', 'content_posted')) AS execution_rows
                FROM null_evidence e
                JOIN vkpi_project_kol_assignments a ON a.kol_pool_id = e.kol_pool_id
                JOIN vkpi_projects p ON p.id = a.project_id
                WHERE COALESCE(p.stage_status, '') NOT IN ('deleted')
                  AND COALESCE(p.stage, '') NOT IN ('cancelled', 'canceled')
                  AND COALESCE(p.source_type, '') <> 'codex_test'
                GROUP BY e.id, a.project_id, p.project_name, p.product_name, p.product_sku, p.project_uid, p.source_type
            ),
            option_counts AS (
                SELECT
                    evidence_id,
                    COUNT(DISTINCT project_id) AS project_count,
                    SUM(execution_rows) AS execution_rows,
                    array_agg(project_name ORDER BY project_name) AS project_names
                    ,
                    jsonb_agg(
                        jsonb_build_object(
                            'project_id', project_id,
                            'project_name', project_name,
                            'product_name', product_name,
                            'product_sku', product_sku,
                            'project_uid', project_uid,
                            'source_type', source_type,
                            'execution_rows', execution_rows
                        )
                        ORDER BY execution_rows DESC, project_id
                    ) AS candidate_projects
                FROM assignment_options
                GROUP BY evidence_id
            ),
            safe_options AS (
                SELECT DISTINCT ON (evidence_id)
                    evidence_id,
                    project_id,
                    project_name,
                    product_name,
                    product_sku,
                    project_uid,
                    source_type
                FROM assignment_options
                ORDER BY evidence_id, execution_rows DESC, project_id
            )
            SELECT
                e.id AS evidence_id,
                e.kol_pool_id,
                kp.display_name AS kol_name,
                kp.handle,
                e.platform,
                e.title,
                e.content_url,
                e.publish_date,
                e.created_at,
                e.source,
                e.scrape_source,
                COALESCE(c.project_count, 0) AS candidate_project_count,
                COALESCE(c.execution_rows, 0) AS candidate_execution_rows,
                c.project_names AS candidate_project_names,
                c.candidate_projects,
                s.project_id,
                s.project_name,
                s.product_name AS project_product_name,
                s.product_sku AS project_product_sku,
                s.project_uid,
                s.source_type AS project_source_type,
                CASE
                    WHEN COALESCE(c.project_count, 0) = 1 THEN 'safe_unique_assignment_project'
                    WHEN COALESCE(c.project_count, 0) = 0 THEN 'skip_no_assignment_project'
                    ELSE 'skip_multiple_assignment_projects'
                END AS decision
            FROM null_evidence e
            LEFT JOIN option_counts c ON c.evidence_id = e.id
            LEFT JOIN safe_options s ON s.evidence_id = e.id AND COALESCE(c.project_count, 0) = 1
            LEFT JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
            ORDER BY decision, e.id
            """
        )
        return [dict(row) for row in cur.fetchall()]


def fetch_all_project_terms(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT project_name, product_name, product_sku
            FROM vkpi_projects
            WHERE COALESCE(stage_status, '') NOT IN ('deleted')
              AND COALESCE(stage, '') NOT IN ('cancelled', 'canceled')
              AND COALESCE(source_type, '') <> 'codex_test'
            """
        )
        terms: set[str] = set()
        for row in cur.fetchall():
            terms |= normalize_product_terms(
                " ".join(
                    [
                        text(row.get("project_name")),
                        text(row.get("product_name")),
                        text(row.get("product_sku")),
                    ]
                )
            )
        return terms


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "decision",
        "evidence_id",
        "kol_pool_id",
        "kol_name",
        "handle",
        "platform",
        "project_id",
        "project_name",
        "project_product_name",
        "project_product_sku",
        "project_uid",
        "project_source_type",
        "attribution_confidence",
        "matched_project_terms",
        "other_project_terms_in_title",
        "project_terms",
        "recommended_decision",
        "recommended_project_id",
        "recommended_project_name",
        "recommended_reason",
        "candidate_project_count",
        "candidate_execution_rows",
        "candidate_project_names",
        "candidate_projects",
        "publish_date",
        "created_at",
        "source",
        "scrape_source",
        "title",
        "content_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def review_bucket(row: dict[str, Any]) -> str:
    if row.get("recommended_decision") == "safe_multi_candidate_title_match":
        return "review_multi_title_match"
    if row.get("recommended_decision") == "skip_multi_candidate_ambiguous_title_match":
        return "review_multi_ambiguous"
    if row.get("decision") == "safe_unique_assignment_project":
        confidence = text(row.get("attribution_confidence"))
        if confidence == "mixed_title_product_signal":
            return "review_unique_mixed_signal"
        if confidence in {"brand_only_unique_project", "weak_unique_project_only", "weak_aperture_or_category_match"}:
            return "review_unique_weak_signal"
    if row.get("decision") == "skip_no_assignment_project":
        return "review_no_assignment"
    return ""


def write_review_artifacts(rows: list[dict[str, Any]], csv_path: Path, md_path: Path) -> dict[str, int]:
    review_rows = [row for row in rows if review_bucket(row)]
    counts = Counter(review_bucket(row) for row in review_rows)
    fieldnames = [
        "review_bucket",
        "evidence_id",
        "kol_pool_id",
        "kol_name",
        "handle",
        "platform",
        "recommended_project_id",
        "recommended_project_name",
        "recommended_reason",
        "project_id",
        "project_name",
        "attribution_confidence",
        "matched_project_terms",
        "other_project_terms_in_title",
        "candidate_project_count",
        "candidate_project_names",
        "publish_date",
        "title",
        "content_url",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in review_rows:
            payload = {key: row.get(key) for key in fieldnames}
            payload["review_bucket"] = review_bucket(row)
            writer.writerow(payload)

    lines = [
        "# Evidence Project Backfill Review",
        "",
        "This file is review-only. No database rows were changed.",
        "",
        "## Buckets",
    ]
    labels = {
        "review_multi_title_match": "多项目候选, 标题命中唯一项目词",
        "review_multi_ambiguous": "多项目候选, 标题命中多个项目词",
        "review_unique_mixed_signal": "唯一 assignment, 但标题也含其他项目词",
        "review_unique_weak_signal": "唯一 assignment, 但只有品牌/弱信号",
        "review_no_assignment": "无 assignment 可归属",
    }
    for key, count in counts.most_common():
        lines.append(f"- {key}: {count} ({labels.get(key, '')})")
    for key in labels:
        bucket_rows = [row for row in review_rows if review_bucket(row) == key]
        if not bucket_rows:
            continue
        lines.extend(["", f"## {key}"])
        for row in bucket_rows[:30]:
            project = row.get("recommended_project_name") or row.get("project_name") or "-"
            reason = row.get("recommended_reason") or row.get("matched_project_terms") or "-"
            title = text(row.get("title") or row.get("content_url"))[:120]
            lines.append(
                f"- evidence {row.get('evidence_id')} | {row.get('kol_name')} | "
                f"project={project} | reason={reason} | {title}"
            )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dict(counts)


def select_commit_rows(rows: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    safe_rows = [
        row
        for row in rows
        if row["decision"] == "safe_unique_assignment_project" and row.get("project_id")
    ]
    if scope == "all-safe":
        return safe_rows
    return [
        row
        for row in safe_rows
        if row.get("attribution_confidence") == "strong_title_product_match"
    ]


def write_report(
    rows: list[dict[str, Any]],
    summary: dict[str, int],
    path: Path,
    *,
    committed: bool,
    commit_scope: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(row["decision"] for row in rows)
    safe_rows = [row for row in rows if row["decision"] == "safe_unique_assignment_project"]
    platform_counts = Counter(text(row.get("platform")) or "<null>" for row in safe_rows)
    confidence_counts = Counter(text(row.get("attribution_confidence")) or "<not_scored>" for row in safe_rows)
    recommended_counts = Counter(
        text(row.get("recommended_decision")) or "<none>"
        for row in rows
        if row.get("decision") == "skip_multiple_assignment_projects"
    )
    commit_rows = select_commit_rows(rows, commit_scope)
    lines = [
        "# Evidence Project Backfill Report",
        "",
        f"mode: {'commit' if committed else 'dry-run'}",
        f"commit_scope: {commit_scope}",
        f"evidence_total: {summary['evidence_total']}",
        f"project_id_missing_before: {summary['missing_project_id']}",
        f"project_id_present_before: {summary['has_project_id']}",
        f"commit_eligible_rows: {len(commit_rows)}",
        "",
        "## Decision Counts",
    ]
    for key, count in counts.most_common():
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Safe Candidate Platforms"])
    for key, count in platform_counts.most_common():
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Attribution Confidence"])
    for key, count in confidence_counts.most_common():
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Multi-Project Candidate Recommendations"])
    for key, count in recommended_counts.most_common():
        lines.append(f"- {key}: {count}")
    recommended_rows = [
        row
        for row in rows
        if row.get("recommended_decision") == "safe_multi_candidate_title_match"
    ]
    lines.extend(["", "## Safe Multi-Project Title Match Samples"])
    for row in recommended_rows[:25]:
        title = text(row.get("title") or row.get("content_url"))[:100]
        lines.append(
            f"- evidence {row['evidence_id']} -> project {row.get('recommended_project_id')} "
            f"({row.get('recommended_project_name')}) | reason={row.get('recommended_reason')} | "
            f"{row.get('kol_name')} | {title}"
        )
    lines.extend(["", "## Mixed Product Signal Samples"])
    for row in [item for item in safe_rows if item.get("attribution_confidence") == "mixed_title_product_signal"][:20]:
        title = text(row.get("title") or row.get("content_url"))[:100]
        lines.append(
            f"- evidence {row['evidence_id']} -> {row.get('project_name')} | "
            f"matched={row.get('matched_project_terms') or '-'} | "
            f"other={row.get('other_project_terms_in_title') or '-'} | {title}"
        )
    lines.extend(["", "## Brand-Only / Weak Samples"])
    review_rows = [
        item
        for item in safe_rows
        if item.get("attribution_confidence") in {"brand_only_unique_project", "weak_unique_project_only"}
    ]
    for row in review_rows[:20]:
        title = text(row.get("title") or row.get("content_url"))[:100]
        lines.append(
            f"- {row.get('attribution_confidence')}: evidence {row['evidence_id']} -> "
            f"{row.get('project_name')} | {row.get('kol_name')} | {title}"
        )
    lines.extend(["", "## Safe Candidate Samples"])
    for row in safe_rows[:25]:
        title = text(row.get("title") or row.get("content_url"))[:90]
        lines.append(
            f"- evidence {row['evidence_id']} -> project {row.get('project_id')} "
            f"({row.get('project_name')}) | {row.get('kol_name')} | {title}"
        )
    lines.extend(
        [
            "",
            "## Rule",
            "Only rows with exactly one non-deleted, non-codex_test assignment project are eligible.",
            "Rows with zero or multiple candidate projects are intentionally skipped.",
            "Attribution confidence is review metadata only; dry-run never updates the database.",
            "Default commit scope is strong-only; use --commit-scope all-safe only after manual review.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_backfill(conn, rows: list[dict[str, Any]], *, commit_scope: str) -> int:
    commit_rows = select_commit_rows(rows, commit_scope)
    if not commit_rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            UPDATE vkpi_kol_video_evidence
            SET project_id = %s, updated_at = NOW()
            WHERE id = %s AND project_id IS NULL
            """,
            [(int(row["project_id"]), int(row["evidence_id"])) for row in commit_rows],
        )
        updated = cur.rowcount
    conn.commit()
    return int(updated or 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Apply safe project_id updates.")
    parser.add_argument(
        "--commit-scope",
        choices=["strong-only", "all-safe"],
        default="strong-only",
        help="Rows eligible during --commit. Default writes only strong title/product matches.",
    )
    parser.add_argument("--sample", type=int, default=25)
    args = parser.parse_args()

    run_stamp = stamp()
    csv_path = ARTIFACT_DIR / f"evidence_project_backfill_{'commit' if args.commit else 'dryrun'}_{run_stamp}.csv"
    report_path = ARTIFACT_DIR / f"evidence_project_backfill_{'commit' if args.commit else 'dryrun'}_{run_stamp}.md"
    review_csv_path = ARTIFACT_DIR / f"evidence_project_backfill_review_{run_stamp}.csv"
    review_md_path = ARTIFACT_DIR / f"evidence_project_backfill_review_{run_stamp}.md"

    conn = connect()
    try:
        summary = fetch_summary(conn)
        rows = fetch_candidate_rows(conn)
        all_project_terms = fetch_all_project_terms(conn)
        for row in rows:
            score_project_match(row, all_project_terms)
        updated = apply_backfill(conn, rows, commit_scope=args.commit_scope) if args.commit else 0
        write_csv(rows, csv_path)
        write_report(rows, summary, report_path, committed=args.commit, commit_scope=args.commit_scope)
        review_counts = write_review_artifacts(rows, review_csv_path, review_md_path)

        counts = Counter(row["decision"] for row in rows)
        safe_rows = [row for row in rows if row["decision"] == "safe_unique_assignment_project"]
        commit_rows = select_commit_rows(rows, args.commit_scope)
        print("=" * 72)
        print("Evidence project_id backfill")
        print("=" * 72)
        print(f"mode: {'COMMIT' if args.commit else 'DRY-RUN'}")
        print(f"commit_scope: {args.commit_scope}")
        print(f"evidence_total: {summary['evidence_total']}")
        print(f"project_id_missing_before: {summary['missing_project_id']}")
        print(f"safe_unique_assignment_project: {counts.get('safe_unique_assignment_project', 0)}")
        print(f"skip_no_assignment_project: {counts.get('skip_no_assignment_project', 0)}")
        print(f"skip_multiple_assignment_projects: {counts.get('skip_multiple_assignment_projects', 0)}")
        confidence_counts = Counter(text(row.get("attribution_confidence")) or "<not_scored>" for row in safe_rows)
        for key, count in confidence_counts.most_common():
            print(f"{key}: {count}")
        print(f"commit_eligible_rows: {len(commit_rows)}")
        if args.commit:
            print(f"updated_rows: {updated}")
        else:
            print("dry-run only: no rows were updated")
        print(f"csv: {csv_path}")
        print(f"report: {report_path}")
        print(f"review_csv: {review_csv_path}")
        print(f"review_report: {review_md_path}")
        for key, count in sorted(review_counts.items()):
            print(f"{key}: {count}")
        print()
        print(f"[sample {args.sample}]")
        for row in safe_rows[: args.sample]:
            print(
                f"  evidence={row['evidence_id']} -> project={row.get('project_id')} "
                f"{row.get('project_name')} | kol={row.get('kol_name')} | "
                f"platform={row.get('platform') or '<null>'}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
