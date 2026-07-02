#!/usr/bin/env python3
"""Classify KOL index entries as creator/reviewer/mixed.

This writes only type metadata on vkpi_kol_profile_index_entries. It does not
touch vkpi_kol_pool, viltrox_fit_score, Qdrant payloads, or ranking logic.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
MIGRATION_NAME = "100_vkpi_kol_profile_type.sql"
COLLECTION_NAME = "vkpi_kol_profile_index_v1"
TYPE_METHOD = "rule_creator_reviewer_v1"

REVIEW_CHANNEL_RE = re.compile(
    r"(camera\s*store|camerastore|editors\s*keys|editorskeys|\bcamera\b.*\btv\b|\btv\b|\breviews?\b|\bgear\b|store|retail)",
    re.I,
)
REVIEW_RE = re.compile(
    r"(review|reviews|field[- ]?test|test(ed|ing)?|comparison|compare|vs\.?|versus|unbox|hands[- ]?on|"
    r"pixel[- ]?peep|pixel peeping|sharpness|autofocus|focus breathing|chromatic aberration|vignetting|"
    r"distortion|minimum focus|specs?|optical performance|image quality|value king|budget filmmaker|"
    r"评测|测试|对比|开箱|上手|锐度|自动对焦|色散|暗角|畸变|最近对焦|画质|性价比|数毛|盲测|横向)",
    re.I,
)
CREATOR_RE = re.compile(
    r"(portrait|portraits|portraitphotography|fashion|editorial|wedding|engagement|street photography|"
    r"street shoot|street|documentary|photoshoot|photo shoot|shoot for|model|bts|behind the scenes|"
    r"golden hour|off[- ]?camera flash|concept portrait|lookbook|campaign|cinematic|travel|landscape|"
    r"wildlife|人像|街拍|婚礼|订婚|纪实|棚拍|外拍|拍摄过程|成片|模特|写真|时尚|服装|大片|样片|第一人称|POV|精修|光影|布光)",
    re.I,
)
CREATOR_TITLE_RE = re.compile(
    r"(shot with|captured with|shoot for|in frame|portrait|photography|street|wedding|golden hour|bokeh|"
    r"offcameraflash|人像|街拍|拍摄|成片)",
    re.I,
)
REVIEW_TITLE_RE = re.compile(
    r"(review|vs|versus|test|comparison|unbox|hands-on|best|budget|tiny lens|pro lens battle|"
    r"评测|测试|对比|开箱|盘点|最佳|好用|性价比)",
    re.I,
)
PRODUCT_AS_TOOL_RE = re.compile(
    r"(title|hashtag|tags|标签|标题|未在视频中露面|画面无品牌|作品|成片|resulting images|sample images|sample photos|its resulting images)",
    re.I,
)
PRODUCT_AS_SUBJECT_RE = re.compile(
    r"(product is the absolute center|sole focus|全程围绕|绝对核心|镜头特写|hands-on shots|physically held|"
    r"实体展示|外观展示|mounted on|lens barrel|packaging|logo|brand.*visible|口播多次|详细|讨论自然深入)",
    re.I,
)


@dataclass(frozen=True)
class TypeResult:
    entry_id: int
    kol_pool_id: int
    handle: str
    display_name: str
    platform: str
    profile_type: str
    creator_type_score: Decimal
    reviewer_type_score: Decimal
    type_reason: str


def load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip() or "postgresql://viltrox2@127.0.0.1:54329/viltrox2"


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url(), row_factory=dict_row)


def apply_migration() -> None:
    migration_path = PROJECT_ROOT / "migrations" / MIGRATION_NAME
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version_key TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('viltrox_schema_migrations'))")
            cur.execute("SELECT 1 FROM schema_migrations WHERE version_key = %s", (MIGRATION_NAME,))
            if cur.fetchone():
                print(f"migration already applied: {MIGRATION_NAME}")
                return
            cur.execute(migration_path.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations(version_key) VALUES (%s)", (MIGRATION_NAME,))
        conn.commit()
    print(f"migration applied: {MIGRATION_NAME}")


def count_re(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text or ""))


def title_line(text: str) -> str:
    match = re.search(r"Evidence titles: (.*)", text or "", re.S)
    return match.group(1) if match else ""


def product_signal_text(text: str) -> str:
    match = re.search(r"Final video analysis product/brand signals: (.*?)(?: Evidence titles:|$)", text or "", re.S)
    return match.group(1) if match else ""


def normalise_score(raw_score: int) -> Decimal:
    value = min(100.0, float(raw_score) / 14.0 * 100.0)
    return Decimal(f"{value:.1f}")


def classify(row: dict[str, Any]) -> TypeResult:
    text = str(row.get("profile_text") or "")
    titles = title_line(text)
    product = product_signal_text(text)
    source_fields = row.get("source_fields_json") if isinstance(row.get("source_fields_json"), dict) else {}
    profile_deep = source_fields.get("profile_deep") if isinstance(source_fields.get("profile_deep"), dict) else {}
    clusters = " ".join(str(item) for item in (profile_deep.get("industry_cluster") or []))
    handle_display = f"{row.get('handle') or ''} {row.get('display_name') or ''}"

    reviewer_raw = 0
    creator_raw = 0
    reasons: list[str] = []

    reviewer_channel_hits = count_re(REVIEW_CHANNEL_RE, handle_display) + (1 if "retail" in clusters.lower() else 0)
    if reviewer_channel_hits:
        reviewer_raw += 4
        reasons.append("channel/cluster reviewer marker")

    review_hits = count_re(REVIEW_RE, text)
    if review_hits:
        reviewer_raw += min(6, review_hits)
        reasons.append(f"review keywords x{review_hits}")

    review_title_hits = count_re(REVIEW_TITLE_RE, titles)
    if review_title_hits:
        reviewer_raw += min(4, review_title_hits)
        reasons.append(f"review title patterns x{review_title_hits}")

    subject_hits = count_re(PRODUCT_AS_SUBJECT_RE, product)
    if subject_hits:
        reviewer_raw += min(3, subject_hits)
        reasons.append("product-as-subject signals")

    creator_hits = count_re(CREATOR_RE, text)
    if creator_hits:
        creator_raw += min(7, creator_hits)
        reasons.append(f"creator keywords x{creator_hits}")

    creator_title_hits = count_re(CREATOR_TITLE_RE, titles)
    if creator_title_hits:
        creator_raw += min(5, creator_title_hits)
        reasons.append(f"creator title patterns x{creator_title_hits}")

    tool_hits = count_re(PRODUCT_AS_TOOL_RE, product)
    if tool_hits:
        creator_raw += min(3, tool_hits)
        reasons.append("product-as-tool/sample signals")

    platform = str(row.get("platform") or "")
    if platform in {"instagram", "tiktok"} and creator_raw > 0:
        creator_raw += 1
        reasons.append("visual creator platform nudge")

    if reviewer_channel_hits and reviewer_raw >= 7 and creator_raw <= reviewer_raw + 2:
        profile_type = "reviewer"
    elif reviewer_raw >= 7 and creator_raw >= 6:
        profile_type = "mixed"
    elif reviewer_raw >= creator_raw + 3 and reviewer_raw >= 5:
        profile_type = "reviewer"
    elif creator_raw >= reviewer_raw + 2 and creator_raw >= 4:
        profile_type = "creator"
    elif reviewer_raw >= 5 and creator_raw >= 4:
        profile_type = "mixed"
    elif creator_raw >= 4:
        profile_type = "creator"
    elif reviewer_raw >= 4:
        profile_type = "reviewer"
    else:
        profile_type = "mixed"

    type_reason = f"{profile_type}: " + "; ".join(reasons[:4])
    return TypeResult(
        entry_id=int(row["entry_id"]),
        kol_pool_id=int(row["kol_pool_id"]),
        handle=str(row.get("handle") or ""),
        display_name=str(row.get("display_name") or ""),
        platform=platform,
        profile_type=profile_type,
        creator_type_score=normalise_score(creator_raw),
        reviewer_type_score=normalise_score(reviewer_raw),
        type_reason=type_reason[:500],
    )


def fetch_results() -> list[TypeResult]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id AS entry_id,
                       e.kol_pool_id,
                       p.handle,
                       p.display_name,
                       p.platform,
                       e.profile_text,
                       e.source_fields_json
                FROM vkpi_kol_profile_index_entries e
                JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
                WHERE e.collection_name = %s
                  AND e.status = 'ready'
                  AND e.method = 'vector_recall'
                ORDER BY e.kol_pool_id
                """,
                (COLLECTION_NAME,),
            )
            rows = [dict(row) for row in cur.fetchall()]
    return [classify(row) for row in rows]


def write_results(results: list[TypeResult]) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            updated = 0
            for item in results:
                cur.execute(
                    """
                    UPDATE vkpi_kol_profile_index_entries
                    SET profile_type = %s,
                        creator_type_score = %s,
                        reviewer_type_score = %s,
                        type_reason = %s,
                        type_method = %s,
                        typed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                      AND collection_name = %s
                      AND status = 'ready'
                      AND method = 'vector_recall'
                    """,
                    (
                        item.profile_type,
                        item.creator_type_score,
                        item.reviewer_type_score,
                        item.type_reason,
                        TYPE_METHOD,
                        item.entry_id,
                        COLLECTION_NAME,
                    ),
                )
                updated += cur.rowcount
        conn.commit()
    return updated


def print_report(results: list[TypeResult]) -> None:
    distribution = Counter(item.profile_type for item in results)
    print(f"count={len(results)}")
    print(
        "distribution="
        f"creator:{distribution.get('creator', 0)} "
        f"reviewer:{distribution.get('reviewer', 0)} "
        f"mixed:{distribution.get('mixed', 0)}"
    )
    sample_handles = {
        "jaysoundo",
        "thecamerastoretv",
        "zwadephoto",
        "eliinfante",
        "michaelziegann",
        "sa1go",
        "editorskeys",
        "huntercreatesthings",
    }
    sample_ids = {4123, 4044}
    print("samples:")
    printed = 0
    for item in results:
        if item.handle.lower() in sample_handles or item.kol_pool_id in sample_ids:
            print(
                f"{item.kol_pool_id}\t{item.handle}\t{item.display_name}\t{item.platform}\t"
                f"{item.profile_type}\tcreator={item.creator_type_score}\t"
                f"reviewer={item.reviewer_type_score}\t{item.type_reason}"
            )
            printed += 1
    if printed < 10:
        for item in results:
            if item.handle.lower() in sample_handles or item.kol_pool_id in sample_ids:
                continue
            print(
                f"{item.kol_pool_id}\t{item.handle}\t{item.display_name}\t{item.platform}\t"
                f"{item.profile_type}\tcreator={item.creator_type_score}\t"
                f"reviewer={item.reviewer_type_score}\t{item.type_reason}"
            )
            printed += 1
            if printed >= 10:
                break


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify KOL index profile type metadata.")
    parser.add_argument("command", choices=["apply-migration", "dry-run", "write"])
    args = parser.parse_args()
    load_dotenv()
    if args.command == "apply-migration":
        apply_migration()
        return 0
    results = fetch_results()
    if len(results) != 125:
        print(f"note: ready entries {len(results)} (cohort assumption 125 lifted 2026-07-02, full-pool ok)")
    if args.command == "write":
        updated = write_results(results)
        print(f"updated={updated}")
    print_report(results)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
