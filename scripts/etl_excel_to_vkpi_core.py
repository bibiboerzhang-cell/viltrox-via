"""Pure Excel parsing, normalization, matching, and planning for V-KPI ETL."""

from __future__ import annotations

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
    if platform == "media" or any(
        marker in lowered or marker in normalized for marker in MEDIA_HANDLES
    ):
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


def load_excel(
    path: Path, today: date
) -> tuple[list[str], list[str], dict[str, list[ExcelRow]]]:
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
                    source_columns={
                        key: text(value)
                        for key, value in raw.items()
                        if not is_blank(value)
                    },
                )
            )
        if parsed_rows:
            rows_by_sheet[sheet] = parsed_rows
        else:
            empty_products.append(sheet)

    return skipped, empty_products, rows_by_sheet


def match_kol_to_pool(
    excel_name: str, pool_records: list[PoolRecord]
) -> MatchResult | None:
    cleaned = clean_excel_kol_name(excel_name)
    norm = normalize_name(cleaned)
    for pool in pool_records:
        if norm == normalize_name(pool.handle) or norm == normalize_name(
            pool.display_name
        ):
            return MatchResult(
                pool_id=pool.id, confidence="exact", matched_via=pool.handle
            )

    candidates = [
        (f"{pool.handle}|{pool.display_name or ''}", pool.id, pool.handle)
        for pool in pool_records
    ]
    names_only = [candidate[0] for candidate in candidates]
    best = process.extractOne(cleaned, names_only, scorer=fuzz.token_sort_ratio)
    if not best:
        return None
    score = float(best[1])
    index = int(best[2])
    if score >= 90:
        return MatchResult(
            candidates[index][1], "fuzzy_high", candidates[index][2], score
        )
    if score >= 75:
        return MatchResult(
            candidates[index][1], "fuzzy_medium", candidates[index][2], score
        )
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
                        "skipped_workshop_rows": (workshop_by_sheet or {}).get(
                            sheet, []
                        ),
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
                item = {
                    "name": row.kol_name,
                    "sheet": sheet,
                    "row": row.excel_row,
                    "platform": platform,
                }
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
                        suffix = hashlib.md5(
                            f"{platform}:{handle}".encode("utf-8")
                        ).hexdigest()[:6]
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
                match = MatchResult(
                    pool_id=int(plan["temp_id"]),
                    confidence="new_pool",
                    matched_via=handle,
                )
            stats[match.confidence] += 1
            if match.confidence == "fuzzy_medium":
                reject_key = (
                    normalize_name(clean_excel_kol_name(row.kol_name)),
                    normalize_name(match.matched_via),
                )
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
                tracking = (
                    f"UPS-FAKE-{str(match.pool_id)[-4:]}-PENDING-"
                    f"{row.created_at:%m%d}"
                )
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
            "all_platforms": sorted(
                {row.platform for row in all_rows if row.platform}
            ),
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


def build_evidence_plans(
    assignments: list[AssignmentPlan],
) -> tuple[list[dict[str, Any]], Counter]:
    evidence: list[dict[str, Any]] = []
    stats: Counter = Counter()
    seen_urls: set[str] = set()
    assignment_by_row = {
        (row.sheet, row.excel_row): plan
        for plan in assignments
        for row in plan.rows
    }
    for (sheet, excel_row), plan in assignment_by_row.items():
        row = next(
            row
            for row in plan.rows
            if row.sheet == sheet and row.excel_row == excel_row
        )
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
