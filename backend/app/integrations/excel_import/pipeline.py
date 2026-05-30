"""Promotion-plan Excel dry-run pipeline.

This module does not write to the database. It only parses, normalizes, and
classifies records so the operator can review counts and samples first.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.integrations.excel_import.classifiers.is_junk_row import is_junk_row, is_junk_value
from app.integrations.excel_import.classifiers.owned_vs_kol import classify_owned_vs_kol
from app.integrations.excel_import.normalizers.date import normalize_date
from app.integrations.excel_import.normalizers.handle import normalize_handle
from app.integrations.excel_import.normalizers.platform import normalize_platform
from app.integrations.excel_import.normalizers.product_model import normalize_product_model
from app.integrations.excel_import.parsers.cell_unpacker import unpack_cell
from app.integrations.excel_import.parsers.workbook_reader import rows_from_sheet
from app.integrations.excel_import.schemas import (
    ContentAssetRecord,
    KOLAssignmentRecord,
    OwnedScheduleRecord,
    ProjectRecord,
)


PROJECT_SHEET = "新品立项时间表"
ASSET_SHEET = "官方物料排期表"

PROJECT_HEADERS = {
    "推广项目名称",
    "产品型号",
    "一级产品类目",
    "二级产品类目",
    "产品发布日期",
    "价格",
    "折扣/佣金",
    "Slogan",
    "Tag",
    "官媒运营排期 (社媒组）",
    "红人媒体排期 (AF 35mm 1.7 Air) -项目归属",
}

ASSET_HEADERS = {
    "上市时间-产品型号/项目名称",
    "产品型号",
    "对接/协作人",
    "制作进度",
    "所属项目",
    "内容类型",
    "内容描述",
    "内容格式",
    "下载/预览链接",
    "尺寸规格",
    "发布状态",
    "产品发布时间",
    "制作团队",
    "官媒使用记录",
}

PLATFORM_WORDS = {
    "INSTAGRAM",
    "FACEBOOK",
    "FACEBOOK GROUP",
    "YOUTUBE",
    "TIKTOK",
    "REDDIT",
    "DISCORD",
    "X",
    "MEDIA",
}


@dataclass
class DryRunResult:
    projects: list[ProjectRecord] = field(default_factory=list)
    owned_schedule: list[OwnedScheduleRecord] = field(default_factory=list)
    kol_assignments: list[KOLAssignmentRecord] = field(default_factory=list)
    content_assets: list[ContentAssetRecord] = field(default_factory=list)
    junk_filtered: int = 0
    unknown_classify: int = 0
    classification_counts: Counter[str] = field(default_factory=Counter)

    def as_counts(self) -> dict[str, Any]:
        return {
            "projects": len(self.projects),
            "owned_schedule": len(self.owned_schedule),
            "kol_assignments": len(self.kol_assignments),
            "content_assets": len(self.content_assets),
            "junk_filtered": self.junk_filtered,
            "unknown_classify": self.unknown_classify,
            "classification_counts": dict(self.classification_counts),
        }


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _find_col(row: dict[str, Any], *needles: str) -> str:
    for key in row:
        normalized = key.replace(" ", "")
        if all(needle in normalized for needle in needles):
            return key
    return ""


def _split_hashtags(raw: str) -> list[str]:
    return [item for item in re.split(r"\s+", raw.strip()) if item.startswith("#")]


def _parse_schedule_token(token: str, fallback_product: str) -> tuple[str, str, str]:
    platform_match = re.search(r"【([^】]+)】", token)
    if platform_match:
        platform = normalize_platform(platform_match.group(1))
        rest = token[: platform_match.start()].strip(" -")
        if "-" in rest:
            product, handle = rest.rsplit("-", 1)
        else:
            product, handle = fallback_product, rest
        return normalize_product_model(product or fallback_product), platform, _text(handle)

    parts = [_text(part) for part in re.split(r"\s*-\s*", token) if _text(part)]
    if len(parts) < 2:
        return normalize_product_model(fallback_product), "unknown", ""

    platform_index = -1
    for index, part in enumerate(parts):
        if normalize_platform(part) != "unknown" or part.upper() in PLATFORM_WORDS:
            platform_index = index
            break
    if platform_index < 0:
        return normalize_product_model(parts[0] or fallback_product), "unknown", parts[-1]

    product = " - ".join(parts[:platform_index]) or fallback_product
    platform = normalize_platform(parts[platform_index])
    handle = " - ".join(parts[platform_index + 1 :])
    return normalize_product_model(product), platform, _text(handle)


def _load_owned_whitelist() -> set[tuple[str, str]]:
    try:
        from app.db.connection import get_conn

        rows = get_conn().execute(
            """
            SELECT platform, account_handle
            FROM vkpi_employee_channels
            WHERE deleted_at IS NULL
              AND account_handle IS NOT NULL
              AND account_handle != ''
            """
        ).fetchall()
    except Exception:
        return set()
    return {
        (normalize_handle(row["account_handle"]), normalize_platform(row["platform"]))
        for row in rows
        if normalize_handle(row["account_handle"])
    }


def _project_from_row(row: dict[str, Any]) -> ProjectRecord:
    return ProjectRecord(
        source_sheet=PROJECT_SHEET,
        source_row=int(row.get("__row_number") or 0),
        name=_text(row.get("推广项目名称")),
        product_model=normalize_product_model(_text(row.get("产品型号"))),
        category_l1=_text(row.get("一级产品类目")),
        category_l2=_text(row.get("二级产品类目")),
        launch_date=normalize_date(row.get("产品发布日期")),
        price_raw=_text(row.get("价格")),
        discount_terms_raw=_text(row.get("折扣/佣金")),
        slogan=_text(row.get("Slogan")),
        hashtags=_split_hashtags(_text(row.get("Tag"))),
        source_columns={key: value for key, value in row.items() if not key.startswith("__")},
    )


def _asset_from_row(row: dict[str, Any]) -> ContentAssetRecord:
    return ContentAssetRecord(
        source_sheet=ASSET_SHEET,
        source_row=int(row.get("__row_number") or 0),
        product_model=normalize_product_model(_text(row.get("产品型号"))),
        project_name=_text(row.get("所属项目")),
        owner_internal=_text(row.get("对接/协作人")),
        status=_text(row.get("制作进度")),
        content_type=_text(row.get("内容类型")),
        description=_text(row.get("内容描述")),
        format=_text(row.get("内容格式")),
        source_url=_text(row.get("下载/预览链接")),
        dimensions=_text(row.get("尺寸规格")),
        publish_status=_text(row.get("发布状态")),
        release_date=normalize_date(row.get("产品发布时间")),
        creator_handle=_text(row.get("制作团队")),
        official_usage_raw=_text(row.get("官媒使用记录")),
        source_columns={key: value for key, value in row.items() if not key.startswith("__")},
    )


def run_dry_run(path: str | Path) -> DryRunResult:
    owned_whitelist = _load_owned_whitelist()
    result = DryRunResult()

    project_rows = rows_from_sheet(path, PROJECT_SHEET, expected_headers=PROJECT_HEADERS).rows
    for row in project_rows:
        if is_junk_row(row):
            result.junk_filtered += 1
            continue
        project = _project_from_row(row)
        if not project.name:
            result.junk_filtered += 1
            continue
        result.projects.append(project)

        schedule_col = _find_col(row, "官媒运营排期")
        kol_col = _find_col(row, "红人媒体排期")
        for token in unpack_cell(row.get(schedule_col, "")):
            if is_junk_value(token):
                result.junk_filtered += 1
                continue
            planned_product, platform, handle = _parse_schedule_token(token, project.product_model)
            classification = classify_owned_vs_kol(handle, platform, owned_whitelist=owned_whitelist, source_kind="owned_schedule")
            result.classification_counts[classification] += 1
            if classification == "unknown":
                result.unknown_classify += 1
            result.owned_schedule.append(
                OwnedScheduleRecord(
                    source_sheet=PROJECT_SHEET,
                    source_row=project.source_row,
                    source_token=token,
                    project_name=project.name,
                    project_product_model=project.product_model,
                    planned_product=planned_product,
                    platform=platform,
                    account_handle=handle,
                    normalized_handle=normalize_handle(handle),
                    classification=classification,
                    source_columns=project.source_columns,
                )
            )

        for token in unpack_cell(row.get(kol_col, "")):
            if is_junk_value(token):
                result.junk_filtered += 1
                continue
            planned_product, platform, handle = _parse_schedule_token(token, project.product_model)
            classification = classify_owned_vs_kol(handle, platform, owned_whitelist=owned_whitelist, source_kind="kol_assignment")
            result.classification_counts[classification] += 1
            if classification == "unknown":
                result.unknown_classify += 1
            result.kol_assignments.append(
                KOLAssignmentRecord(
                    source_sheet=PROJECT_SHEET,
                    source_row=project.source_row,
                    source_token=token,
                    project_name=project.name,
                    project_product_model=project.product_model,
                    planned_product=planned_product,
                    platform=platform,
                    kol_handle=handle,
                    normalized_handle=normalize_handle(handle),
                    classification=classification,
                    source_columns=project.source_columns,
                )
            )

    asset_rows = rows_from_sheet(path, ASSET_SHEET, expected_headers=ASSET_HEADERS).rows
    for row in asset_rows:
        if is_junk_row(row):
            result.junk_filtered += 1
            continue
        asset = _asset_from_row(row)
        result.content_assets.append(asset)

    return result


def sample_records(records: list[Any], limit: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records[:limit]:
        if hasattr(record, "model_dump"):
            data = record.model_dump()
        else:
            data = dict(record)
        data.pop("source_columns", None)
        out.append(data)
    return out


def format_dry_run_report(result: DryRunResult) -> str:
    lines = [
        f"projects: {len(result.projects)} rows extracted",
        f"owned_schedule: {len(result.owned_schedule)} rows extracted",
        f"owned_schedule top 10 sample: {json.dumps(sample_records(result.owned_schedule), ensure_ascii=False)}",
        f"kol_assignments: {len(result.kol_assignments)} rows extracted",
        f"kol_assignments top 10 sample: {json.dumps(sample_records(result.kol_assignments), ensure_ascii=False)}",
        f"content_assets: {len(result.content_assets)} rows extracted",
        f"junk filtered: {result.junk_filtered} rows",
        f"unknown classify: {result.unknown_classify} rows",
        f"classification counts: {json.dumps(dict(result.classification_counts), ensure_ascii=False)}",
    ]
    return "\n".join(lines)
