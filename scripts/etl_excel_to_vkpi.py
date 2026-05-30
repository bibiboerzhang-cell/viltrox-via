#!/usr/bin/env python3
"""Import Viltrox promotion-plan Excel into Project/KOL/video evidence tables.

Default mode is dry-run. --commit writes in one serial transaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

try:
    import pandas as pd
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
    from rapidfuzz import fuzz, process
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency. Install into the project venv: "
        ".venv/bin/python -m pip install 'pandas<3' python-calamine rapidfuzz psycopg2-binary"
    ) from exc


DEFAULT_EXCEL = Path("/Users/bibiboer/Downloads/海外市场推广计划表-Viltrox.xlsx")
SKIP_SHEETS = {
    "新品立项时间表",
    "官方物料排期表",
    "官媒运营排片表",
    "【红人媒体数据建档与管理】",
    "【红人媒体观察名单】",
    "海外舆情监控表",
    "产品成本信息表",
    "_",
    "(6.10) Viltrox Frame the Game",
}

KOL_COLS = ("红人/媒体",)
STAGE_COLS = ("合作进度", "合作状态")
STAFF_COLS = ("登记/对接人", "对接人")
PLATFORM_COLS = ("平台",)
COUNTRY_COLS = ("国家",)
CREATED_AT_COLS = ("创建日期",)
EVIDENCE_URL_COLS = ("回片链接",)
PUBLISHED_URL_COLS = ("内容发布链接",)
CHANNEL_URL_COLS = ("红人视频链接", "红人主页链接")
SKU_COLS = ("推广产品",)
AFFILIATE_COLS = ("Affiliate ID",)

STAGE_MAP = {
    "目标-待联系": "discovered",
    "已联系-待回复": "contacted",
    "已回复-沟通中": "replied",
    "确认合作-待寄样": "agreed",
    "已寄样-待回片": "device_sent",
    "已回片": "content_posted",
    "终审通过": "reviewed",
    "合作中止": "churned",
}
PRIORITY = [
    "reviewed",
    "content_posted",
    "arrived",
    "device_sent",
    "agreed",
    "replied",
    "contacted",
    "discovered",
    "churned",
]
STAGE_SCORE = {stage: len(PRIORITY) - index for index, stage in enumerate(PRIORITY)}
FUZZY_MEDIUM_ALLOWLIST: set[tuple[str, int]] = set()
FUZZY_MEDIUM_REJECTLIST: set[tuple[str, str]] = {
    ("photographyblogmarkgoldstein", "reviews"),
}
MEDIA_HANDLES = {
    "35mmc",
    "admiringlight",
    "amateurphotographer",
    "cameralabs",
    "digitalcameraworld",
    "dpreview",
    "fstoppers",
    "imaging-resource",
    "kenrockwell",
    "lensrentals",
    "lensreviewer",
    "lensvid",
    "lenstip",
    "mirrorlessons",
    "opticallimits",
    "petapixel",
    "phillipreeve",
    "photographyblog",
    "photographylife",
    "photographyreview",
    "sonyalpha",
    "thephoblographer",
}
EVIDENCE_BLACKLIST = (
    "geni.us",
    "linktr.ee",
    "bit.ly",
    "amzn.to",
    "amazon.",
    "viltrox.com",
    "viltroxstore.",
    "tinyurl.com",
    "t.co",
)
VIDEO_PLATFORMS = (
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "tiktok.com",
    "facebook.com",
    "vimeo.com",
    "twitter.com",
    "x.com",
    "bilibili.com",
)
MEDIA_DOMAINS = (
    "digitalcameraworld.com",
    "opticallimits.com",
    "phillipreeve.net",
    "sonyalpha.blog",
    "photographylife.com",
    "35mmc.com",
    "fstoppers.com",
    "thephoblographer.com",
    "kenrockwell.com",
    "nikon-fotografie.de",
    "cameralabs.com",
    "petapixel.com",
    "dpreview.com",
    "amateurphotographer.com",
    "lensvid.com",
    "mirrorlessons.com",
    "admiringlight.com",
    "photographyblog.com",
    "macfilos.com",
    "lensrentals.com",
    "lenstip.com",
    "slrlounge.com",
    "pcmag.com",
    "nytimes.com",
    "photoreview.com.au",
    "imaging-resource.com",
)


@dataclass
class PoolRecord:
    id: int
    handle: str
    display_name: str
    platform: str


@dataclass
class ExcelRow:
    sheet: str
    excel_row: int
    kol_name: str
    stage_raw: str
    stage: str
    staff_name: str
    platform: str
    country: str
    created_at: datetime
    video_cell: str
    published_cell: str
    channel_url: str
    sku: str
    affiliate_id: str
    source_columns: dict[str, str]


@dataclass
class MatchResult:
    pool_id: int
    confidence: str
    matched_via: str
    score: float | None = None


@dataclass
class AssignmentPlan:
    sheet: str
    project_key: str
    project_id: int | None
    kol_pool_id: int
    stage: str
    stage_raw: str
    staff_id: int | None
    platform: str
    country: str
    created_at: datetime
    source_ref: str
    tracking_number: str | None
    is_placeholder_tracking: bool
    metadata: dict[str, Any]
    rows: list[ExcelRow] = field(default_factory=list)


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return str(value).strip() == ""


def text(value: Any) -> str:
    if is_blank(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def row_value(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in row and not is_blank(row.get(alias)):
            return row.get(alias)
    return None


def normalize_platform(value: str) -> str:
    raw = value.strip().lower().replace("，", ",")
    if not raw:
        return ""
    if "instagram" in raw or raw == "ig":
        return "instagram"
    if "youtube" in raw or raw in {"yt", "youtu"}:
        return "youtube"
    if "tiktok" in raw or "tik tok" in raw:
        return "tiktok"
    if "facebook" in raw:
        return "facebook"
    if raw in {"x", "twitter"}:
        return "x"
    if "media" in raw:
        return "media"
    return raw.split(",", 1)[0].strip()


def normalize_name(value: str) -> str:
    return re.sub(r"[\s\.\-_]+", "", value.lower())


def clean_excel_kol_name(excel_name: str) -> str:
    return re.split(r"\s*-?\s*【", excel_name)[0].strip()


def platform_from_kol_name(excel_name: str, fallback: str) -> str:
    tags = [tag.strip().lower() for tag in re.findall(r"【([^】]*)】", excel_name)]
    tag_blob = " ".join(tags)
    if "workshop" in tag_blob:
        return "workshop"
    if "media" in tag_blob:
        return "media"
    if "instagram" in tag_blob:
        return "instagram"
    if "youtube" in tag_blob:
        return "youtube"
    if "tiktok" in tag_blob:
        return "tiktok"
    normalized = normalize_platform(fallback)
    return normalized or "unknown"


def classify_account_type(handle: str, platform: str) -> str:
    lowered = handle.lower()
    normalized = normalize_name(handle)
    if "viltrox" in lowered or "唯卓仕" in handle:
        return "company"
    if platform == "media" or any(marker in lowered or marker in normalized for marker in MEDIA_HANDLES):
        return "media"
    return "kol"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        slug = hashlib.md5(value.encode("utf-8")).hexdigest()[:12]
    return slug


def project_uid(sheet: str) -> str:
    return "EXCEL-" + slugify(sheet)[:30]


def project_name(sheet: str) -> str:
    return re.sub(r"^\(\d{1,2}\.\d{1,2}\)\s*", "", sheet).strip() or sheet


def parse_created_at(value: Any, today: date) -> datetime:
    if is_blank(value):
        return datetime.combine(today, time.min)
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return datetime.combine(today, time.min)
    parsed_dt = parsed.to_pydatetime()
    if parsed_dt.date() > today:
        return datetime.combine(today, time.min)
    return parsed_dt.replace(tzinfo=None)


def map_stage(value: str) -> str:
    raw = value.strip()
    if raw in STAGE_MAP:
        return STAGE_MAP[raw]
    for marker, stage in STAGE_MAP.items():
        if marker and marker in raw:
            return stage
    return "discovered"


def extract_urls(value: str) -> list[str]:
    urls = re.findall(r"https?://[^\s,，;；]+", value or "", flags=re.I)
    cleaned: list[str] = []
    for url in urls:
        url = url.rstrip(").。】]>\u3002")
        if url and url not in cleaned:
            cleaned.append(url)
    return cleaned


def classify_evidence_url(url: str) -> tuple[str | None, str]:
    if not url.lower().startswith(("http://", "https://")):
        return None, "invalid_format"
    url_lower = url.lower()
    if any(marker in url_lower for marker in EVIDENCE_BLACKLIST):
        return None, "blacklisted"
    if any(marker in url_lower for marker in VIDEO_PLATFORMS):
        return "video", "video_platform"
    if any(marker in url_lower for marker in MEDIA_DOMAINS):
        return "media_article", "media_domain"
    return None, "unknown_domain"


def load_excel(path: Path, today: date) -> tuple[list[str], list[str], dict[str, list[ExcelRow]]]:
    xls = pd.ExcelFile(path, engine="calamine")
    skipped = [sheet for sheet in xls.sheet_names if sheet in SKIP_SHEETS]
    empty_products: list[str] = []
    rows_by_sheet: dict[str, list[ExcelRow]] = {}

    for sheet in xls.sheet_names:
        if sheet in SKIP_SHEETS:
            continue
        df = pd.read_excel(path, sheet_name=sheet, engine="calamine", dtype=object)
        df.columns = [str(col).strip() for col in df.columns]
        parsed_rows: list[ExcelRow] = []
        for index, series in df.iterrows():
            raw = {str(key).strip(): value for key, value in series.to_dict().items()}
            kol_name = text(row_value(raw, KOL_COLS))
            if not kol_name:
                continue
            stage_raw = text(row_value(raw, STAGE_COLS))
            platform = normalize_platform(text(row_value(raw, PLATFORM_COLS)))
            created_at = parse_created_at(row_value(raw, CREATED_AT_COLS), today)
            parsed_rows.append(
                ExcelRow(
                    sheet=sheet,
                    excel_row=int(index) + 2,
                    kol_name=kol_name,
                    stage_raw=stage_raw,
                    stage=map_stage(stage_raw),
                    staff_name=text(row_value(raw, STAFF_COLS)),
                    platform=platform,
                    country=text(row_value(raw, COUNTRY_COLS)),
                    created_at=created_at,
                    video_cell=text(row_value(raw, EVIDENCE_URL_COLS)),
                    published_cell=text(row_value(raw, PUBLISHED_URL_COLS)),
                    channel_url=text(row_value(raw, CHANNEL_URL_COLS)),
                    sku=text(row_value(raw, SKU_COLS)),
                    affiliate_id=text(row_value(raw, AFFILIATE_COLS)),
                    source_columns={key: text(value) for key, value in raw.items() if not is_blank(value)},
                )
            )
        if parsed_rows:
            rows_by_sheet[sheet] = parsed_rows
        else:
            empty_products.append(sheet)

    return skipped, empty_products, rows_by_sheet


def connect():
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("DATABASE_URL is required. Source .env or set DATABASE_URL.")
    return psycopg2.connect(dsn)


def fetch_pool_records(conn) -> list[PoolRecord]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, handle, display_name, platform FROM vkpi_kol_pool ORDER BY id")
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


def fetch_pool_details(conn, pool_ids: set[int]) -> dict[int, dict[str, Any]]:
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


def fetch_project_ids_by_uid(conn, project_uids: list[str]) -> dict[str, int]:
    if not project_uids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT project_uid, id FROM vkpi_projects WHERE project_uid = ANY(%s)",
            (project_uids,),
        )
        return {text(row[0]): int(row[1]) for row in cur.fetchall()}


def match_kol_to_pool(excel_name: str, pool_records: list[PoolRecord]) -> MatchResult | None:
    cleaned = clean_excel_kol_name(excel_name)
    norm = normalize_name(cleaned)
    for pool in pool_records:
        if norm == normalize_name(pool.handle) or norm == normalize_name(pool.display_name):
            return MatchResult(pool_id=pool.id, confidence="exact", matched_via=pool.handle)

    candidates = [(f"{p.handle}|{p.display_name or ''}", p.id, p.handle) for p in pool_records]
    names_only = [candidate[0] for candidate in candidates]
    best = process.extractOne(cleaned, names_only, scorer=fuzz.token_sort_ratio)
    if not best:
        return None
    score = float(best[1])
    index = int(best[2])
    if score >= 90:
        return MatchResult(candidates[index][1], "fuzzy_high", candidates[index][2], score)
    if score >= 75:
        return MatchResult(candidates[index][1], "fuzzy_medium", candidates[index][2], score)
    return None


def build_project_plans(
    rows_by_sheet: dict[str, list[ExcelRow]],
    staff_map: dict[str, int],
    workshop_by_sheet: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    now = datetime.now().replace(microsecond=0)
    for sheet, rows in rows_by_sheet.items():
        platform = Counter(row.platform for row in rows if row.platform).most_common(1)
        staff = Counter(row.staff_name for row in rows if row.staff_name).most_common(1)
        staff_name = staff[0][0] if staff else ""
        product_sku = next((row.sku for row in rows if row.sku), "")
        plans.append(
            {
                "sheet": sheet,
                "project_uid": project_uid(sheet),
                "project_name": project_name(sheet),
                "product_sku": product_sku,
                "product_name": project_name(sheet),
                "platform": platform[0][0] if platform else "",
                "assigned_staff_id": staff_map.get(normalize_name(staff_name)),
                "created_by_staff_id": staff_map.get(normalize_name(staff_name)),
                "source_type": "excel_promo_plan",
                "metadata_json": json.dumps(
                    {
                        "excel_sheet": sheet,
                        "source_ref": f"excel:{sheet}",
                        "imported_at": now.isoformat(),
                        "kol_count": len(rows),
                        "skipped_workshop_rows": (workshop_by_sheet or {}).get(sheet, []),
                    },
                    ensure_ascii=False,
                ),
            }
        )
    return plans


def merge_assignments(
    rows_by_sheet: dict[str, list[ExcelRow]],
    pool_records: list[PoolRecord],
    staff_map: dict[str, int],
) -> tuple[list[AssignmentPlan], dict[str, Any]]:
    stats = Counter()
    unmatched: list[dict[str, Any]] = []
    fuzzy_medium: list[dict[str, Any]] = []
    workshop_skipped: list[dict[str, Any]] = []
    new_pool_plans: list[dict[str, Any]] = []
    new_pool_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    used_pool_uids: set[str] = set()
    raw_plans: list[AssignmentPlan] = []

    for sheet, rows in rows_by_sheet.items():
        for row in rows:
            stats["total_rows"] += 1
            match = match_kol_to_pool(row.kol_name, pool_records)
            if not match:
                stats["unmatched"] += 1
                platform = platform_from_kol_name(row.kol_name, row.platform)
                item = {"name": row.kol_name, "sheet": sheet, "row": row.excel_row, "platform": platform}
                unmatched.append(item)
                if platform == "workshop":
                    stats["unmatched_workshop_skipped"] += 1
                    workshop_skipped.append(item)
                    continue

                handle = clean_excel_kol_name(row.kol_name) or row.kol_name
                key = (platform, handle)
                plan = new_pool_by_key.get(key)
                if not plan:
                    temp_id = -len(new_pool_plans) - 1
                    base_uid = "EXCEL-NEW-" + slugify(handle)[:30]
                    pool_uid = base_uid
                    if pool_uid in used_pool_uids:
                        suffix = hashlib.md5(f"{platform}:{handle}".encode("utf-8")).hexdigest()[:6]
                        pool_uid = f"{base_uid[:23]}-{suffix}"
                    used_pool_uids.add(pool_uid)
                    account_type = classify_account_type(handle, platform)
                    plan = {
                        "temp_id": temp_id,
                        "pool_uid": pool_uid,
                        "handle": handle,
                        "display_name": handle,
                        "platform": platform,
                        "source_type": "excel_promo_plan_new",
                        "source_ref": f"excel:{sheet}:{row.excel_row}",
                        "sync_status": "imported",
                        "dashboard_account_type": account_type,
                        "dashboard_tier": None,
                        "followers": None,
                    }
                    new_pool_by_key[key] = plan
                    new_pool_plans.append(plan)
                    stats[f"new_pool_{account_type}"] += 1
                stats["unmatched_new_pool_rows"] += 1
                match = MatchResult(pool_id=int(plan["temp_id"]), confidence="new_pool", matched_via=handle)
            stats[match.confidence] += 1
            if match.confidence == "fuzzy_medium":
                reject_key = (normalize_name(clean_excel_kol_name(row.kol_name)), normalize_name(match.matched_via))
                if reject_key in FUZZY_MEDIUM_REJECTLIST:
                    stats["fuzzy_medium_rejected"] += 1
                    continue
                fuzzy_medium.append(
                    {
                        "name": row.kol_name,
                        "matched_via": match.matched_via,
                        "score": match.score or 0,
                        "sheet": sheet,
                        "row": row.excel_row,
                    }
                )
                if (row.kol_name, match.pool_id) not in FUZZY_MEDIUM_ALLOWLIST:
                    stats["fuzzy_medium_pending_review"] += 1
                    continue

            staff_id = staff_map.get(normalize_name(row.staff_name))
            is_placeholder = row.stage in {"content_posted", "reviewed"}
            tracking = None
            if is_placeholder:
                tracking = f"UPS-FAKE-{str(match.pool_id)[-4:]}-PENDING-{row.created_at:%m%d}"
            raw_plans.append(
                AssignmentPlan(
                    sheet=sheet,
                    project_key=project_uid(sheet),
                    project_id=None,
                    kol_pool_id=match.pool_id,
                    stage=row.stage,
                    stage_raw=row.stage_raw,
                    staff_id=staff_id,
                    platform=row.platform,
                    country=row.country,
                    created_at=row.created_at,
                    source_ref=f"excel:{sheet}:{row.excel_row}",
                    tracking_number=tracking,
                    is_placeholder_tracking=bool(tracking),
                    metadata={
                        "excel_sheet": sheet,
                        "excel_row": row.excel_row,
                        "kol_name": row.kol_name,
                        "match_confidence": match.confidence,
                        "matched_via": match.matched_via,
                        "match_score": match.score,
                        "platform": row.platform,
                        "country": row.country,
                        "affiliate_id": row.affiliate_id,
                        "channel_url": row.channel_url,
                    },
                    rows=[row],
                )
            )

    grouped: dict[tuple[str, int], list[AssignmentPlan]] = defaultdict(list)
    for plan in raw_plans:
        grouped[(plan.project_key, plan.kol_pool_id)].append(plan)

    merged: list[AssignmentPlan] = []
    duplicate_extra_rows = 0
    duplicate_groups = 0
    for plans in grouped.values():
        if len(plans) == 1:
            merged.append(plans[0])
            continue
        duplicate_groups += 1
        duplicate_extra_rows += len(plans) - 1
        keep = max(plans, key=lambda item: STAGE_SCORE.get(item.stage, 0))
        all_rows = [row for plan in plans for row in plan.rows]
        keep.rows = all_rows
        keep.metadata = {
            **keep.metadata,
            "merged_from_excel_rows": [row.excel_row for row in all_rows],
            "all_stages_seen": sorted({row.stage for row in all_rows}),
            "all_platforms": sorted({row.platform for row in all_rows if row.platform}),
            "all_products": sorted({row.sku for row in all_rows if row.sku}),
            "merge_note": f"项目内 {len(plans)} 条重复合并, 取最高 stage",
        }
        merged.append(keep)

    report = {
        "stats": stats,
        "unmatched": unmatched,
        "fuzzy_medium": fuzzy_medium,
        "workshop_skipped": workshop_skipped,
        "new_pool_plans": new_pool_plans,
        "raw_matched_count": len(raw_plans),
        "merged_count": len(merged),
        "duplicate_extra_rows": duplicate_extra_rows,
        "duplicate_groups": duplicate_groups,
    }
    return merged, report


def build_evidence_plans(assignments: list[AssignmentPlan]) -> tuple[list[dict[str, Any]], Counter]:
    evidence: list[dict[str, Any]] = []
    stats: Counter = Counter()
    seen_urls: set[str] = set()
    assignment_by_row = {(row.sheet, row.excel_row): plan for plan in assignments for row in plan.rows}
    for (sheet, excel_row), plan in assignment_by_row.items():
        row = next(row for row in plan.rows if row.sheet == sheet and row.excel_row == excel_row)
        for column_name, cell, source in (
            ("回片链接", row.video_cell, "excel_huipian"),
            ("内容发布链接", row.published_cell, "excel_published"),
        ):
            if not cell:
                continue
            urls = extract_urls(cell)
            if not urls:
                stats[(column_name, "invalid_format")] += 1
                continue
            for url in urls:
                evidence_type, reason = classify_evidence_url(url)
                stats[(column_name, reason)] += 1
                if not evidence_type:
                    continue
                if url in seen_urls:
                    stats[(column_name, "duplicate_url")] += 1
                    continue
                seen_urls.add(url)
                evidence.append(
                    {
                        "kol_pool_id": plan.kol_pool_id,
                        "project_key": plan.project_key,
                        "project_id": None,
                        "content_url": url,
                        "platform": row.platform or plan.platform,
                        "source": source,
                        "source_ref": f"excel:{sheet}:{excel_row}:{column_name}",
                        "confidence": "high",
                        "evidence_type": evidence_type,
                        "posted_at": row.created_at.date(),
                        "created_at": row.created_at,
                    }
                )
    return evidence, stats


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


def apply_new_pools(cur, new_pool_plans: list[dict[str, Any]]) -> dict[int, int]:
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
    parts = " ".join(f"WHEN '{stage}' THEN {score}" for stage, score in STAGE_SCORE.items())
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


def top_unmatched(unmatched: list[dict[str, Any]], limit: int = 30) -> list[tuple[str, str, int]]:
    grouped: Counter[tuple[str, str]] = Counter((item["name"], item["sheet"]) for item in unmatched)
    return [(name, sheet, count) for (name, sheet), count in grouped.most_common(limit)]


def top_fuzzy_medium(fuzzy_medium: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    return sorted(fuzzy_medium, key=lambda item: item["score"])[:limit]


def print_report(
    *,
    skipped: list[str],
    empty_products: list[str],
    rows_by_sheet: dict[str, list[ExcelRow]],
    projects: list[dict[str, Any]],
    match_report: dict[str, Any],
    assignments: list[AssignmentPlan],
    evidence: list[dict[str, Any]],
    evidence_stats: Counter,
    existing_evidence_urls: set[str],
    active_pool_ids: set[int],
    pool_details: dict[int, dict[str, Any]],
    mode: str,
) -> None:
    stats: Counter = match_report["stats"]
    new_pool_plans: list[dict[str, Any]] = match_report["new_pool_plans"]
    evidence_pool_ids = {row["kol_pool_id"] for row in evidence}
    published_pool_ids = {
        plan.kol_pool_id for plan in assignments if plan.stage in {"content_posted", "reviewed"}
    }
    need_scrape_pool_ids = published_pool_ids - evidence_pool_ids
    placeholder_count = sum(1 for plan in assignments if plan.is_placeholder_tracking)
    active_roster = len(evidence_pool_ids)

    evidence_by_source = Counter(row["source"] for row in evidence)
    new_evidence = [row for row in evidence if row["content_url"] not in existing_evidence_urls]
    new_evidence_by_type = Counter(row["evidence_type"] for row in new_evidence)
    new_active_pool_ids = {
        int(row["kol_pool_id"])
        for row in new_evidence
        if int(row["kol_pool_id"]) > 0 and int(row["kol_pool_id"]) not in active_pool_ids
    }
    new_media_articles_by_pool: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in new_evidence:
        pool_id = int(row["kol_pool_id"])
        if pool_id in new_active_pool_ids and row["evidence_type"] == "media_article":
            new_media_articles_by_pool[pool_id].append(row)
    print("=" * 60)
    print(f"ETL {'Commit' if mode == 'commit' else 'Dry-Run'} 报告 (V2)")
    print("=" * 60)
    print("\n[Projects]")
    print(f"  {len(rows_by_sheet)} sheet -> {len(projects)} project")
    print(f"  跳过 sheet: {len(skipped)}")
    if empty_products:
        print(f"  空产品 sheet: {len(empty_products)} 个 ({', '.join(empty_products)})")

    print("\n[KOL 匹配]")
    print(f"  exact:           {stats['exact']}")
    print(f"  fuzzy_high:      {stats['fuzzy_high']}")
    print(f"  fuzzy_medium:    {stats['fuzzy_medium']} (全部 reject: {stats['fuzzy_medium_rejected']})")
    print(f"  unmatched (将新建 pool):  {stats['unmatched_new_pool_rows']}")
    print(f"  unmatched (WORKSHOP 跳过): {stats['unmatched_workshop_skipped']}")

    print("\n[匹配失败 top 30 (Excel 名)]")
    unmatched_top = top_unmatched(match_report["unmatched"])
    if unmatched_top:
        for index, (name, sheet, count) in enumerate(unmatched_top, 1):
            print(f"  {index}. {name}  (项目: {project_name(sheet)}, sheet出现 {count} 次)")
    else:
        print("  无")

    print("\n[fuzzy_medium top 20]")
    fuzzy_top = top_fuzzy_medium(match_report["fuzzy_medium"])
    if fuzzy_top:
        for index, item in enumerate(fuzzy_top, 1):
            print(
                f"  {index}. Excel: \"{item['name']}\" -> Pool: \"{item['matched_via']}\" "
                f"(score={item['score']:.1f}, 项目: {project_name(item['sheet'])}, row={item['row']})"
            )
    else:
        print("  无")

    print("\n[新建 pool]")
    print(f"  将新建 vkpi_kol_pool 记录: {len(new_pool_plans)}")
    print(f"  覆盖 unmatched 行数: {stats['unmatched_new_pool_rows']}")
    print(f"  - account_type=kol:     {stats['new_pool_kol']}")
    print(f"  - account_type=media:   {stats['new_pool_media']}")
    print(f"  - account_type=company: {stats['new_pool_company']}")

    print("\n[Assignments]")
    print(
        f"  合并前: {stats['exact']} + {stats['unmatched_new_pool_rows']} = "
        f"{stats['exact'] + stats['fuzzy_high'] + stats['unmatched_new_pool_rows']}"
    )
    print(
        f"  合并后: {match_report['merged_count']} "
        f"(合并 {match_report['duplicate_extra_rows']} 条, {match_report['duplicate_groups']} 组)"
    )
    print(f"  placeholder UPS 单号: {placeholder_count} 条")

    print("\n[Video Evidence]")
    print(f"  回片链接 -> valid evidence:        {evidence_by_source['excel_huipian']}")
    print(f"  回片链接 -> blacklisted:           {evidence_stats[('回片链接', 'blacklisted')]}")
    print(f"  回片链接 -> unknown_domain:        {evidence_stats[('回片链接', 'unknown_domain')]}")
    print(f"  回片链接 -> media_domain:          {evidence_stats[('回片链接', 'media_domain')]}")
    print(f"  内容发布链接 -> valid evidence:     {evidence_by_source['excel_published']}")
    print(f"  内容发布链接 -> blacklisted:        {evidence_stats[('内容发布链接', 'blacklisted')]}")
    print(f"  内容发布链接 -> unknown_domain:     {evidence_stats[('内容发布链接', 'unknown_domain')]}")
    print(f"  内容发布链接 -> media_domain:       {evidence_stats[('内容发布链接', 'media_domain')]}")
    print(f"  合计 valid evidence URL:           {len(evidence)}")

    print("\n[Fix 1 增量预测]")
    print(f"  video evidence 新增:        {new_evidence_by_type['video']}")
    print(f"  media_article 新增:         {new_evidence_by_type['media_article']}")
    print(f"  预计新增 active KOL:        {len(new_active_pool_ids)}")
    new_media_articles = [row for row in new_evidence if row["evidence_type"] == "media_article"]
    if new_media_articles:
        print("\n[media_article 新增候选样本 top 5]")
        for index, row in enumerate(new_media_articles[:5], 1):
            print(f"  {index}. {row['content_url']} ({row['source_ref']})")

    if new_active_pool_ids:
        print(f"\n[新 active KOL 候选 {len(new_active_pool_ids)} 人]")
        print("display_name | dashboard_account_type | tier | 新增 media_article 数 | 样本 1 URL")
        for pool_id in sorted(new_active_pool_ids, key=lambda value: text(pool_details.get(value, {}).get("display_name") or pool_details.get(value, {}).get("handle")).lower()):
            detail = pool_details.get(pool_id, {})
            articles = new_media_articles_by_pool.get(pool_id, [])
            display_name = text(detail.get("display_name")) or text(detail.get("handle")) or str(pool_id)
            account_type = text(detail.get("dashboard_account_type")) or "-"
            tier = text(detail.get("dashboard_tier")) or "-"
            sample_url = articles[0]["content_url"] if articles else "-"
            print(f"{display_name} | {account_type} | {tier} | {len(articles)} | {sample_url}")

    print("\n[needs_scrape]")
    print(f"  已合作但无视频证据: {len(need_scrape_pool_ids)} 个 KOL")

    print("\n" + "=" * 60)
    print("预测最终 KPI:")
    print(f"  vkpi_projects 新增/更新: {len(projects)}")
    print(f"  vkpi_kol_pool 新增: {len(new_pool_plans)}")
    print(f"  vkpi_project_kol_assignments 新增/更新: {len(assignments)}")
    print(f"  vkpi_kol_video_evidence 新增: {len(evidence)}")
    print(f"  has_video_evidence=TRUE: {active_roster} (= Active Roster)")
    print(f"  needs_scrape=TRUE:        {len(need_scrape_pool_ids)}")
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", default=str(DEFAULT_EXCEL))
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--evidence-only", action="store_true", help="only insert evidence rows; do not touch projects, pool, or assignments")
    args = parser.parse_args()

    excel_path = Path(args.excel).expanduser()
    if not excel_path.exists():
        raise SystemExit(f"Excel not found: {excel_path}")

    today = date.today()
    skipped, empty_products, rows_by_sheet = load_excel(excel_path, today)
    with connect() as conn:
        pool_records = fetch_pool_records(conn)
        staff_map = fetch_staff_map(conn)
        assignments, match_report = merge_assignments(rows_by_sheet, pool_records, staff_map)
        workshop_by_sheet: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in match_report["workshop_skipped"]:
            workshop_by_sheet[item["sheet"]].append({"row": item["row"], "name": item["name"]})
        projects = build_project_plans(rows_by_sheet, staff_map, workshop_by_sheet)
        evidence, evidence_stats = build_evidence_plans(assignments)
        existing_evidence_urls = fetch_existing_evidence_urls(conn)
        active_pool_ids = fetch_active_pool_ids(conn)
        new_evidence_pool_ids = {
            int(row["kol_pool_id"])
            for row in evidence
            if int(row["kol_pool_id"]) > 0
            and row["content_url"] not in existing_evidence_urls
            and int(row["kol_pool_id"]) not in active_pool_ids
        }
        pool_details = fetch_pool_details(conn, new_evidence_pool_ids)

        if args.commit:
            with conn.cursor() as cur:
                project_uid_to_sheet = {project["project_uid"]: project["sheet"] for project in projects}
                if args.evidence_only:
                    sheet_to_id = fetch_project_ids_by_uid(conn, [project["project_uid"] for project in projects])
                    sheet_to_id = {project_uid_to_sheet[uid]: project_id for uid, project_id in sheet_to_id.items()}
                else:
                    sheet_to_id = apply_projects(cur, projects)
                    temp_to_real = apply_new_pools(cur, match_report["new_pool_plans"])
                    for plan in assignments:
                        plan.project_id = sheet_to_id[plan.sheet]
                        if plan.kol_pool_id < 0:
                            plan.kol_pool_id = temp_to_real[plan.kol_pool_id]
                        if plan.is_placeholder_tracking:
                            plan.tracking_number = f"UPS-FAKE-{str(plan.kol_pool_id)[-4:]}-{str(plan.project_id)[-4:]}-{plan.created_at:%m%d}"
                for row in evidence:
                    if row["kol_pool_id"] < 0:
                        continue
                    row["project_id"] = sheet_to_id.get(project_uid_to_sheet[row["project_key"]])
                if not args.evidence_only:
                    apply_assignments(cur, assignments)
                apply_evidence(cur, evidence)
                if not args.evidence_only:
                    needs_scrape_count = apply_needs_scrape(cur)
                    print(f"commit needs_scrape rows updated: {needs_scrape_count}")

        print_report(
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
