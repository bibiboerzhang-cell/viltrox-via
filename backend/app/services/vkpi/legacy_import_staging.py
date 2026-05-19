"""Stage legacy V-KPI Excel rows into P2B import staging tables."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.legacy_import_audit import (
    _compact,
    _extract_handle_from_url,
    _find_email,
    _find_phone,
    _first_url,
    _map_fields,
    _normalize_handle,
    _normalize_platform,
    _parse_amount,
    _read_csv,
    _text,
    _xlsx_sheets,
)


ACTIVE_BATCH_STATUSES = ("staging", "staged", "committing", "committed")

PIPELINE_ORDER = [
    "kol_profiles",
    "cooperations",
    "launch_plans",
    "official_content",
    "official_materials",
    "product_costs",
    "risk_watchlist",
    "voc_alerts",
]

PIPELINE_TABLES = {
    "kol_profiles": "vkpi_legacy_kol_profiles_staging",
    "cooperations": "vkpi_legacy_cooperations_staging",
    "launch_plans": "vkpi_legacy_launch_plans_staging",
    "official_content": "vkpi_legacy_official_content_staging",
    "official_materials": "vkpi_legacy_official_materials_staging",
    "product_costs": "vkpi_legacy_product_costs_staging",
    "risk_watchlist": "vkpi_legacy_risk_watchlist_staging",
    "voc_alerts": "vkpi_legacy_voc_alerts_staging",
}

STAGING_COLUMNS = {
    "kol_profiles": [
        "import_batch_id",
        "row_uid",
        "source_sheet",
        "source_row",
        "platform",
        "normalized_platform",
        "handle",
        "normalized_handle",
        "dedup_key",
        "display_name",
        "country",
        "region",
        "category",
        "email",
        "phone",
        "address",
        "notes",
        "contact_missing",
        "contact_visibility_level",
        "contains_pii",
        "duplicate_in_batch",
        "review_status",
        "review_reason_json",
        "import_action",
        "row_hash",
        "raw_row_json",
        "validation_json",
    ],
    "cooperations": [
        "import_batch_id",
        "row_uid",
        "source_sheet",
        "source_row",
        "platform",
        "normalized_platform",
        "handle",
        "normalized_handle",
        "dedup_key",
        "display_name",
        "product",
        "project",
        "status",
        "cooperation_date",
        "cost_amount",
        "cost_currency",
        "content_link",
        "result",
        "notes",
        "unmatched_kol_review",
        "review_status",
        "review_reason_json",
        "import_action",
        "row_hash",
        "raw_row_json",
        "validation_json",
    ],
    "launch_plans": [
        "import_batch_id",
        "row_uid",
        "source_sheet",
        "source_row",
        "launch_name",
        "product_sku",
        "product_name",
        "category_primary",
        "category_secondary",
        "launch_date",
        "target_region",
        "target_platforms_json",
        "campaign_owner",
        "official_material_ref",
        "kol_plan_ref",
        "product_page_url",
        "status",
        "notes",
        "review_status",
        "review_reason_json",
        "import_action",
        "row_hash",
        "raw_row_json",
        "validation_json",
    ],
    "official_content": [
        "import_batch_id",
        "row_uid",
        "source_sheet",
        "source_row",
        "official_account",
        "platform",
        "normalized_platform",
        "publish_date",
        "content_type",
        "title",
        "product",
        "link",
        "status",
        "owner",
        "notes",
        "review_status",
        "review_reason_json",
        "import_action",
        "row_hash",
        "raw_row_json",
        "validation_json",
    ],
    "official_materials": [
        "import_batch_id",
        "row_uid",
        "source_sheet",
        "source_row",
        "launch_ref",
        "product_sku",
        "product_name",
        "owner",
        "production_status",
        "project",
        "content_type",
        "content_description",
        "reference_doc",
        "content_format",
        "request_date",
        "target_delivery_date",
        "asset_link",
        "size_spec",
        "publish_status",
        "product_publish_date",
        "production_team",
        "budget_amount",
        "budget_currency",
        "official_usage_ref",
        "parent_ref",
        "notes",
        "review_status",
        "review_reason_json",
        "import_action",
        "row_hash",
        "raw_row_json",
        "validation_json",
    ],
    "product_costs": [
        "import_batch_id",
        "row_uid",
        "source_sheet",
        "source_row",
        "sku",
        "product_name",
        "cost",
        "currency",
        "region",
        "effective_date",
        "notes",
        "review_status",
        "review_reason_json",
        "import_action",
        "row_hash",
        "raw_row_json",
        "validation_json",
    ],
    "risk_watchlist": [
        "import_batch_id",
        "row_uid",
        "source_sheet",
        "source_row",
        "platform",
        "normalized_platform",
        "handle",
        "normalized_handle",
        "dedup_key",
        "display_name",
        "risk_type",
        "risk_reason",
        "severity",
        "evidence",
        "status",
        "notes",
        "risk_only",
        "review_status",
        "review_reason_json",
        "import_action",
        "row_hash",
        "raw_row_json",
        "validation_json",
    ],
    "voc_alerts": [
        "import_batch_id",
        "row_uid",
        "source_sheet",
        "source_row",
        "platform",
        "normalized_platform",
        "product",
        "issue_type",
        "sentiment",
        "content",
        "link",
        "evidence",
        "issue_date",
        "severity",
        "status",
        "owner",
        "notes",
        "review_status",
        "review_reason_json",
        "import_action",
        "row_hash",
        "raw_row_json",
        "validation_json",
    ],
}

PIPELINE_DEFAULTS = {
    "kol_profiles": {
        "platform": "",
        "normalized_platform": "",
        "handle": "",
        "normalized_handle": "",
        "dedup_key": "",
        "display_name": "",
        "country": "",
        "region": "",
        "category": "",
        "email": "",
        "phone": "",
        "address": "",
        "notes": "",
        "contact_missing": False,
        "contact_visibility_level": "restricted",
        "contains_pii": True,
        "duplicate_in_batch": False,
    },
    "cooperations": {
        "platform": "",
        "normalized_platform": "",
        "handle": "",
        "normalized_handle": "",
        "dedup_key": "",
        "display_name": "",
        "product": "",
        "project": "",
        "status": "",
        "cost_currency": "",
        "content_link": "",
        "result": "",
        "notes": "",
        "unmatched_kol_review": False,
    },
    "launch_plans": {
        "launch_name": "",
        "product_sku": "",
        "product_name": "",
        "category_primary": "",
        "category_secondary": "",
        "target_region": "",
        "target_platforms_json": "[]",
        "campaign_owner": "",
        "official_material_ref": "",
        "kol_plan_ref": "",
        "product_page_url": "",
        "status": "planned",
        "notes": "",
    },
    "official_content": {
        "official_account": "",
        "platform": "",
        "normalized_platform": "",
        "content_type": "",
        "title": "",
        "product": "",
        "link": "",
        "status": "",
        "owner": "",
        "notes": "",
    },
    "official_materials": {
        "launch_ref": "",
        "product_sku": "",
        "product_name": "",
        "owner": "",
        "production_status": "",
        "project": "",
        "content_type": "",
        "content_description": "",
        "reference_doc": "",
        "content_format": "",
        "asset_link": "",
        "size_spec": "",
        "publish_status": "",
        "production_team": "",
        "budget_currency": "",
        "official_usage_ref": "",
        "parent_ref": "",
        "notes": "",
    },
    "product_costs": {
        "sku": "",
        "product_name": "",
        "currency": "",
        "region": "",
        "notes": "",
    },
    "risk_watchlist": {
        "platform": "",
        "normalized_platform": "",
        "handle": "",
        "normalized_handle": "",
        "dedup_key": "",
        "display_name": "",
        "risk_type": "",
        "risk_reason": "",
        "severity": "medium",
        "evidence": "",
        "status": "open",
        "notes": "",
        "risk_only": True,
    },
    "voc_alerts": {
        "platform": "",
        "normalized_platform": "",
        "product": "",
        "issue_type": "",
        "sentiment": "",
        "content": "",
        "link": "",
        "evidence": "",
        "severity": "medium",
        "status": "open",
        "owner": "",
        "notes": "",
    },
}


class FileAlreadyImported(RuntimeError):
    """Raised when an active staging batch already owns the same file hash."""


def ensure_legacy_staging_schema() -> None:
    """Apply Postgres migrations without invoking unrelated runtime seeders."""
    from app.db.connection import _run_postgres_migrations, is_postgres_runtime

    if not is_postgres_runtime():
        raise RuntimeError("P2B staging CLI requires the Postgres runtime migrations")
    _run_postgres_migrations()


@dataclass
class StageRecord:
    pipeline: str
    source_sheet: str
    source_row: int
    raw: dict[str, str]
    values: dict[str, Any]
    review_reasons: list[str] = field(default_factory=list)

    @property
    def table(self) -> str:
        return PIPELINE_TABLES[self.pipeline]


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_legacy_sheet(sheet_name: str) -> str:
    name = _text(sheet_name)
    if name == "【红人媒体数据建档与管理】":
        return "kol_profiles"
    if name == "新品立项时间表":
        return "launch_plans"
    if name == "官媒运营排片表":
        return "official_content"
    if name == "官方物料排期表":
        return "official_materials"
    if name == "产品成本信息表":
        return "product_costs"
    if name == "【红人媒体观察名单】":
        return "risk_watchlist"
    if name == "海外舆情监控表":
        return "voc_alerts"
    return "cooperations" if name else ""


def _sheet_names(path: Path) -> list[str]:
    if path.suffix.lower() != ".xlsx":
        return ["csv"]
    with zipfile.ZipFile(path) as zf:
        return [name for name, _sheet_path in _xlsx_sheets(zf)]


def _read_sheet_rows(path: Path, sheet_name: str, *, max_rows: int = 0) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        return _read_csv(path, max_rows=max_rows)
    from app.services.vkpi.legacy_import_audit import _read_xlsx

    return _read_xlsx(path, sheet_name=sheet_name, max_rows=max_rows)


def _pick(raw: dict[str, str], *headers: str) -> str:
    wanted = [_compact(header) for header in headers if _compact(header)]
    if not wanted:
        return ""
    for header, value in raw.items():
        if _compact(header) in wanted and _text(value):
            return _text(value)
    for header, value in raw.items():
        compact = _compact(header)
        if _text(value) and any(item and (item in compact or compact in item) for item in wanted):
            return _text(value)
    return ""


def _first_nonempty(*values: str) -> str:
    for value in values:
        if _text(value):
            return _text(value)
    return ""


def _split_name_platform(value: str) -> tuple[str, str]:
    matches = re.findall(r"([^-\n\r]+?)\s*[-－]\s*【([^】]+)】", _text(value))
    if matches:
        name, platform = matches[-1]
        return _text(name), _text(platform)
    bracket = re.search(r"【([^】]+)】", _text(value))
    return "", _text(bracket.group(1)) if bracket else ""


def _is_placeholder_token(value: str) -> bool:
    raw = _text(value)
    return not raw or raw.startswith("<|") or raw.endswith("|>")


def _split_product_platform_account(value: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in _text(value).split("-") if part.strip()]
    if not parts:
        return "", "", ""

    platform = ""
    platform_index = -1
    for index, part in enumerate(parts):
        candidate = _stage_platform(part)
        if candidate:
            platform = candidate
            platform_index = index
            break

    account = ""
    for index in range(len(parts) - 1, -1, -1):
        if index == platform_index or _is_placeholder_token(parts[index]):
            continue
        account = parts[index]
        break

    product = ""
    for index, part in enumerate(parts):
        if index == platform_index or part == account or _is_placeholder_token(part):
            continue
        product = part
        break
    return product, platform, account


def _stage_platform(value: str, fallback_text: str = "") -> str:
    normalized = _normalize_platform(value, fallback_text)
    if normalized:
        return normalized
    raw = f"{value} {fallback_text}".lower()
    if "media" in raw or "媒体" in raw:
        return "media"
    if "official" in raw or "官媒" in raw:
        return "official"
    return ""


def _parse_excel_date(value: str, *, datetime_value: bool = False) -> str | None:
    raw = _text(value)
    if not raw:
        return None
    date_match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", raw)
    if date_match:
        year, month, day = (int(part) for part in date_match.groups())
        parsed = datetime(year, month, day)
        return parsed.isoformat(timespec="seconds") if datetime_value else parsed.date().isoformat()
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        try:
            parsed = datetime(1899, 12, 30) + timedelta(days=float(raw))
            return parsed.isoformat(timespec="seconds") if datetime_value else parsed.date().isoformat()
        except Exception:
            return None
    return None


def _common_identity(raw: dict[str, str]) -> dict[str, str]:
    mapped = _map_fields(raw)
    raw_text = " ".join(_text(value) for value in raw.values())
    account_text = _first_nonempty(
        _pick(raw, "账号/媒体名称", "红人/媒体", "红人姓名/账号名", "红人媒体名称-平台", "项目-红人"),
        mapped.get("handle", ""),
    )
    display_hint, platform_hint = _split_name_platform(account_text)
    profile_url = _first_nonempty(
        mapped.get("profile_url", ""),
        _pick(raw, "主页链接", "频道/主页链接", "红人视频链接", "发布链接", "内容发布链接", "回片链接"),
        _first_url(raw_text),
    )
    platform = _stage_platform(
        _first_nonempty(mapped.get("platform", ""), _pick(raw, "平台", "发布平台", "舆情来源平台"), platform_hint),
        f"{profile_url} {raw_text}",
    )
    normalized_handle = _extract_handle_from_url(profile_url, platform)
    if not normalized_handle:
        normalized_handle = _normalize_handle(display_hint or account_text, platform)
    handle = normalized_handle or _text(display_hint or account_text)
    display_name = _first_nonempty(
        _pick(raw, "红人/编辑姓名", "账号/媒体名称", "红人/媒体", "红人姓名/账号名"),
        display_hint,
        handle,
    )
    normalized_platform = platform
    return {
        "platform": platform,
        "normalized_platform": normalized_platform,
        "handle": handle,
        "normalized_handle": normalized_handle,
        "dedup_key": f"{normalized_platform}:{normalized_handle.lower()}" if normalized_platform and normalized_handle else "",
        "display_name": display_name,
        "profile_url": profile_url,
        "email": _first_nonempty(mapped.get("email", ""), _find_email(raw_text)),
        "phone": _first_nonempty(mapped.get("phone", ""), _find_phone(raw_text)),
    }


def _base_values(record: StageRecord, batch_uid: str) -> dict[str, Any]:
    raw_json = json_dumps(record.raw)
    row_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
    values = {**PIPELINE_DEFAULTS.get(record.pipeline, {}), **record.values}
    values.update(
        {
            "row_uid": f"{batch_uid}:{record.pipeline}:{record.source_sheet}:{record.source_row}:{row_hash[:12]}",
            "source_sheet": record.source_sheet,
            "source_row": record.source_row,
            "review_status": "needs_review" if record.review_reasons else values.get("review_status", "ready"),
            "review_reason_json": json_dumps(record.review_reasons),
            "import_action": "stage_only",
            "row_hash": row_hash,
            "raw_row_json": raw_json,
            "validation_json": json_dumps({"review_reasons": record.review_reasons}),
        }
    )
    return values


def _validation_record(pipeline: str, item: dict[str, Any], reason: str) -> StageRecord:
    raw = item.get("raw") or {}
    return StageRecord(
        pipeline=pipeline,
        source_sheet=str(item.get("sheet", "")),
        source_row=int(item.get("row_number") or 0),
        raw=raw,
        values={},
        review_reasons=[reason],
    )


def _build_kol_profile(item: dict[str, Any]) -> StageRecord:
    raw = item["raw"]
    identity = _common_identity(raw)
    address = _pick(raw, "地址")
    values = {
        **{key: identity[key] for key in ["platform", "normalized_platform", "handle", "normalized_handle", "dedup_key", "display_name"]},
        "country": _pick(raw, "国家", "国家/地区"),
        "region": _pick(raw, "州/省(自动提取)", "州/省", "区域", "地区"),
        "category": _pick(raw, "频道/主页标签", "频道内容标签", "类目", "一级类目"),
        "email": identity["email"],
        "phone": identity["phone"],
        "address": address,
        "notes": _pick(raw, "备注", "投放排期/历史", "说明"),
        "contact_missing": not bool(identity["email"] or identity["phone"] or address),
        "contact_visibility_level": "restricted",
        "contains_pii": True,
        "duplicate_in_batch": False,
    }
    reasons: list[str] = []
    if not identity["platform"]:
        reasons.append("missing_platform")
    if not identity["normalized_handle"]:
        reasons.append("missing_handle")
    return StageRecord("kol_profiles", item["sheet"], int(item["row_number"]), raw, values, reasons)


def _build_cooperation(item: dict[str, Any]) -> StageRecord:
    raw = item["raw"]
    identity = _common_identity(raw)
    cost_raw = _first_nonempty(_pick(raw, "预算报价", "预算", "费用", "金额", "报价"), _pick(raw, "合作内容详情"))
    cost_amount, cost_currency = _parse_amount(cost_raw)
    values = {
        **{key: identity[key] for key in ["platform", "normalized_platform", "handle", "normalized_handle", "dedup_key", "display_name"]},
        "product": _first_nonempty(_pick(raw, "推广产品", "产品型号", "卡口"), item["sheet"]),
        "project": _pick(raw, "项目归属", "项目-红人", "推广项目名称"),
        "status": _pick(raw, "合作进度", "状态", "制作进度"),
        "cooperation_date": _parse_excel_date(_pick(raw, "创建日期", "发布时间", "发片时间", "预计/实际发布时间", "预计回片时间")),
        "cost_amount": cost_amount,
        "cost_currency": cost_currency,
        "content_link": _first_nonempty(_pick(raw, "内容发布链接", "回片链接", "红人视频链接", "发布链接"), identity["profile_url"]),
        "result": _pick(raw, "结果", "回片状态", "合作内容详情", "审批意见", "【审核意见】"),
        "notes": _pick(raw, "备注", "合作详情", "合作内容详情"),
        "unmatched_kol_review": False,
    }
    reasons: list[str] = []
    if not identity["platform"] or not identity["normalized_handle"]:
        reasons.append("missing_kol_identity")
    return StageRecord("cooperations", item["sheet"], int(item["row_number"]), raw, values, reasons)


def _build_launch_plan(item: dict[str, Any]) -> StageRecord:
    raw = item["raw"]
    values = {
        "launch_name": _pick(raw, "推广项目名称", "项目名称"),
        "product_sku": _pick(raw, "产品型号", "SKU", "sku"),
        "product_name": _pick(raw, "产品型号", "产品名称", "推广项目名称"),
        "category_primary": _pick(raw, "一级产品类目", "一级类目"),
        "category_secondary": _pick(raw, "二级产品类目", "二级类目"),
        "launch_date": _parse_excel_date(_pick(raw, "产品发布日期", "上市时间", "发布日期")),
        "target_region": _pick(raw, "目标区域", "市场", "地区"),
        "target_platforms_json": json_dumps([value for value in [_pick(raw, "目标平台", "平台")] if value]),
        "campaign_owner": _pick(raw, "负责人", "对接人", "对接/协作人"),
        "official_material_ref": _pick(raw, "官媒运营排期", "官媒运营排期 (社媒组）", "品牌宣发物料"),
        "kol_plan_ref": _pick(raw, "红人推广计划", "KOL计划", "红人计划"),
        "product_page_url": _first_nonempty(_pick(raw, "网页链接", "产品链接", "关键信息/网页链接"), _first_url(" ".join(raw.values()))),
        "status": _pick(raw, "状态") or "planned",
        "notes": _pick(raw, "备注", "关键信息/网页链接", "Slogan", "Tag"),
    }
    reasons = []
    if not values["launch_name"] and not values["product_name"]:
        reasons.append("missing_launch_identity")
    return StageRecord("launch_plans", item["sheet"], int(item["row_number"]), raw, values, reasons)


def _build_official_content(item: dict[str, Any]) -> StageRecord:
    raw = item["raw"]
    product_platform_account = _pick(raw, "产品-平台-账号")
    product_hint, platform_hint, account_hint = _split_product_platform_account(product_platform_account)
    account = _first_nonempty(_pick(raw, "账号名称", "官方账号"), account_hint)
    platform = _stage_platform(_first_nonempty(_pick(raw, "发布平台", "平台"), platform_hint), product_platform_account)
    values = {
        "official_account": account,
        "platform": platform,
        "normalized_platform": platform,
        "publish_date": _parse_excel_date(_pick(raw, "预计/实际发布时间", "发布时间", "产品发布时间"), datetime_value=True),
        "content_type": _pick(raw, "内容类型", "内容格式"),
        "title": _pick(raw, "内容概述", "内容描述", "标题"),
        "product": _first_nonempty(_pick(raw, "产品型号"), product_hint),
        "link": _pick(raw, "发布链接", "下载/预览链接"),
        "status": _pick(raw, "状态", "发布状态", "制作进度"),
        "owner": _pick(raw, "运营人/发帖人", "对接/协作人", "负责人"),
        "notes": _pick(raw, "备注", "关键词/tag", "原创内容引用来源"),
    }
    reasons = []
    if not account:
        reasons.append("missing_official_account")
    if not platform:
        reasons.append("missing_platform")
    return StageRecord("official_content", item["sheet"], int(item["row_number"]), raw, values, reasons)


def _build_official_material(item: dict[str, Any]) -> StageRecord:
    raw = item["raw"]
    budget_amount, budget_currency = _parse_amount(_pick(raw, "预算"))
    values = {
        "launch_ref": _pick(raw, "上市时间-产品型号/项目名称"),
        "product_sku": _pick(raw, "产品型号"),
        "product_name": _pick(raw, "产品型号"),
        "owner": _pick(raw, "对接/协作人"),
        "production_status": _pick(raw, "制作进度"),
        "project": _pick(raw, "所属项目"),
        "content_type": _pick(raw, "内容类型"),
        "content_description": _pick(raw, "内容描述"),
        "reference_doc": _pick(raw, "参考文档"),
        "content_format": _pick(raw, "内容格式"),
        "request_date": _parse_excel_date(_pick(raw, "提需时间")),
        "target_delivery_date": _parse_excel_date(_pick(raw, "目标交付时间")),
        "asset_link": _pick(raw, "下载/预览链接"),
        "size_spec": _pick(raw, "尺寸规格"),
        "publish_status": _pick(raw, "发布状态"),
        "product_publish_date": _parse_excel_date(_pick(raw, "产品发布时间")),
        "production_team": _pick(raw, "制作团队"),
        "budget_amount": budget_amount,
        "budget_currency": budget_currency,
        "official_usage_ref": _pick(raw, "官媒使用记录"),
        "parent_ref": _pick(raw, "父记录"),
        "notes": _pick(raw, "备注", "参考文档"),
    }
    reasons = []
    if not values["product_name"] and not values["launch_ref"]:
        reasons.append("missing_product_identity")
    if not values["content_description"] and not values["asset_link"]:
        reasons.append("missing_material_content")
    return StageRecord("official_materials", item["sheet"], int(item["row_number"]), raw, values, reasons)


def _build_product_cost(item: dict[str, Any]) -> StageRecord:
    raw = item["raw"]
    cost_raw = _pick(raw, "采购成本(CNY)", "成本", "cost")
    cost_amount, cost_currency = _parse_amount(cost_raw)
    values = {
        "sku": _pick(raw, "产品型号", "SKU", "sku"),
        "product_name": _pick(raw, "产品型号", "产品名称"),
        "cost": cost_amount,
        "currency": cost_currency or ("CNY" if "CNY" in "".join(raw.keys()).upper() else ""),
        "region": _pick(raw, "区域", "地区"),
        "effective_date": _parse_excel_date(_pick(raw, "生效日期", "日期")),
        "notes": _pick(raw, "备注", "父记录", "SourceID"),
    }
    reasons = []
    if not values["sku"] and not values["product_name"]:
        reasons.append("missing_product_identity")
    if cost_raw and cost_amount is None:
        reasons.append("invalid_cost")
    return StageRecord("product_costs", item["sheet"], int(item["row_number"]), raw, values, reasons)


def _build_risk_watchlist(item: dict[str, Any]) -> StageRecord:
    raw = item["raw"]
    identity = _common_identity(raw)
    risk_type = _pick(raw, "风险类型")
    reason = _pick(raw, "备注/建议", "风险原因", "备注")
    severity = "high" if any(marker in f"{risk_type} {reason}" for marker in ["断联", "未回片", "欺诈", "严重"]) else "medium"
    values = {
        **{key: identity[key] for key in ["platform", "normalized_platform", "handle", "normalized_handle", "dedup_key", "display_name"]},
        "risk_type": risk_type,
        "risk_reason": reason,
        "severity": severity,
        "evidence": _pick(raw, "辅证资料(截图/合同)", "内容发布链接", "证据", "evidence"),
        "status": _pick(raw, "回片状态", "状态") or "open",
        "notes": _pick(raw, "备注/建议", "历史对接人", "更新人"),
        "risk_only": True,
    }
    reasons = []
    if not identity["platform"] or not identity["normalized_handle"]:
        reasons.append("missing_kol_identity")
    return StageRecord("risk_watchlist", item["sheet"], int(item["row_number"]), raw, values, reasons)


def _is_risk_marker_row(raw: dict[str, str]) -> bool:
    return not _pick(raw, "红人姓名/账号名") and not _pick(raw, "频道/主页链接")


def _build_voc_alert(item: dict[str, Any]) -> StageRecord:
    raw = item["raw"]
    platform = _stage_platform(_pick(raw, "舆情来源平台", "平台"), " ".join(raw.values()))
    sentiment = _pick(raw, "舆情性质", "情绪", "sentiment")
    issue_type = _pick(raw, "舆情类型", "类型", "issue_type")
    content = _first_nonempty(_pick(raw, "原文", "舆情概述", "内容"), _pick(raw, "性质-类型-型号-平台"))
    values = {
        "platform": platform,
        "normalized_platform": platform,
        "product": _pick(raw, "相关产品型号", "产品型号", "产品"),
        "issue_type": issue_type,
        "sentiment": sentiment,
        "content": content,
        "link": _pick(raw, "链接", "原文链接", "发布链接"),
        "evidence": _pick(raw, "截图", "证据"),
        "issue_date": _parse_excel_date(_pick(raw, "日期", "反馈日期")),
        "severity": "high" if any(marker in f"{sentiment} {issue_type} {content}" for marker in ["投诉", "负面", "严重"]) else "medium",
        "status": _pick(raw, "状态") or "open",
        "owner": _pick(raw, "舆情反馈人", "owner", "负责人"),
        "notes": _pick(raw, "备注(对内)", "官方回复口径(对外)", "备注"),
    }
    reasons = []
    if not content:
        reasons.append("missing_voc_content")
    return StageRecord("voc_alerts", item["sheet"], int(item["row_number"]), raw, values, reasons)


BUILDERS = {
    "kol_profiles": _build_kol_profile,
    "cooperations": _build_cooperation,
    "launch_plans": _build_launch_plan,
    "official_content": _build_official_content,
    "official_materials": _build_official_material,
    "product_costs": _build_product_cost,
    "risk_watchlist": _build_risk_watchlist,
    "voc_alerts": _build_voc_alert,
}


def _prepare_records(path: Path, *, sheet_name: str = "", max_rows: int = 0) -> tuple[list[StageRecord], list[dict[str, Any]]]:
    records: list[StageRecord] = []
    skipped_sheets: list[dict[str, Any]] = []
    sheets = [sheet_name] if sheet_name else _sheet_names(path)
    for sheet in sheets:
        try:
            rows = _read_sheet_rows(path, sheet, max_rows=max_rows)
        except Exception as exc:
            skipped_sheets.append({"sheet": sheet, "rows": 0, "reason": f"parse_error:{exc}"})
            continue
        pipeline = classify_legacy_sheet(sheet)
        if not pipeline:
            skipped_sheets.append({"sheet": sheet, "rows": len(rows), "reason": "unmapped_sheet"})
            continue
        if not rows:
            skipped_sheets.append({"sheet": sheet, "rows": 0, "reason": "empty_sheet"})
            continue
        builder = BUILDERS[pipeline]
        for item in rows:
            if pipeline == "risk_watchlist" and _is_risk_marker_row(item.get("raw") or {}):
                continue
            try:
                records.append(builder(item))
            except Exception as exc:
                records.append(_validation_record(pipeline, item, f"row_parse_error:{exc}"))

    kol_dedup_counts = Counter(record.values.get("dedup_key", "") for record in records if record.pipeline == "kol_profiles")
    kol_dedup_keys = {key for key, count in kol_dedup_counts.items() if key}
    for record in records:
        dedup_key = record.values.get("dedup_key", "")
        if record.pipeline == "kol_profiles" and dedup_key and kol_dedup_counts[dedup_key] > 1:
            record.values["duplicate_in_batch"] = True
        if record.pipeline == "cooperations" and dedup_key and dedup_key not in kol_dedup_keys:
            record.values["unmatched_kol_review"] = True
            if "unmatched_kol_review" not in record.review_reasons:
                record.review_reasons.append("unmatched_kol_review")
    return records, skipped_sheets


def _insert_row(conn: Any, table: str, columns: list[str], values: dict[str, Any]) -> int:
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    params = [values.get(column) for column in columns]
    row = conn.execute(f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) RETURNING id", params).fetchone()
    return int(row["id"])


def _insert_review_queue(conn: Any, import_batch_id: int, record: StageRecord, staging_id: int) -> None:
    if not record.review_reasons:
        return
    review_type = "unmatched_kol_review" if record.review_reasons == ["unmatched_kol_review"] else "validation_error"
    severity = "high" if any(reason.startswith("missing_") or reason.startswith("row_parse_error") for reason in record.review_reasons) else "medium"
    conn.execute(
        """
        INSERT INTO vkpi_legacy_import_review_queue (
          import_batch_id, pipeline, staging_table, staging_id, source_sheet,
          source_row, review_type, severity, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            import_batch_id,
            record.pipeline,
            record.table,
            staging_id,
            record.source_sheet,
            record.source_row,
            review_type,
            severity,
            json_dumps({"review_reasons": record.review_reasons, "raw": record.raw}),
        ),
    )


def _active_hash_lookup(conn: Any, sha256: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT batch_uid, status, parsed_at
        FROM vkpi_legacy_import_batches
        WHERE source_file_sha256 = ?
          AND status IN ('staging', 'staged', 'committing', 'committed')
        LIMIT 1
        """,
        (sha256,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def stage_legacy_file(
    path: str | Path,
    *,
    batch_label: str = "",
    sheet_name: str = "",
    max_rows: int = 0,
) -> dict[str, Any]:
    source = Path(path).expanduser()
    source_stat = source.stat()
    source_hash = file_sha256(source)
    batch_uid = f"vkpi_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:12]}"
    records, skipped_sheets = _prepare_records(source, sheet_name=sheet_name, max_rows=max_rows)
    pipeline_counts = Counter(record.pipeline for record in records)
    total_rows = sum(pipeline_counts.values()) + sum(int(item.get("rows") or 0) for item in skipped_sheets)

    conn = get_conn()
    import_batch_id = 0
    try:
        existing = _active_hash_lookup(conn, source_hash)
        if existing:
            raise FileAlreadyImported(
                f"File already imported as batch {existing['batch_uid']} (status: {existing['status']})"
            )
        metadata = {
            "batch_label": batch_label,
            "skipped_sheets": skipped_sheets,
            "pipeline_counts": {key: int(pipeline_counts.get(key, 0)) for key in PIPELINE_ORDER},
        }
        row = conn.execute(
            """
            INSERT INTO vkpi_legacy_import_batches (
              batch_uid, source_file_name, source_file_sha256,
              source_file_size_bytes, source_workbook_path, status,
              total_rows, contains_pii, metadata_json, rollback_until,
              rollback_policy
            ) VALUES (?, ?, ?, ?, ?, 'staging', ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                batch_uid,
                source.name,
                source_hash,
                int(source_stat.st_size),
                str(source),
                int(total_rows),
                True,
                json_dumps(metadata),
                (datetime.utcnow() + timedelta(minutes=30)).isoformat(timespec="seconds"),
                "manual_30m",
            ),
        ).fetchone()
        import_batch_id = int(row["id"])
        review_queue_rows = 0
        for record in records:
            base_values = _base_values(record, batch_uid)
            base_values["import_batch_id"] = import_batch_id
            columns = STAGING_COLUMNS[record.pipeline]
            staging_id = _insert_row(conn, record.table, columns, base_values)
            if record.review_reasons:
                _insert_review_queue(conn, import_batch_id, record, staging_id)
                review_queue_rows += 1

        unmatched_count = sum(1 for record in records if "unmatched_kol_review" in record.review_reasons)
        validation_error_count = review_queue_rows - unmatched_count
        metadata.update(
            {
                "review_queue_rows": review_queue_rows,
                "unmatched_count": unmatched_count,
                "validation_error_count": validation_error_count,
            }
        )
        conn.execute(
            """
            UPDATE vkpi_legacy_import_batches
            SET status='staged',
                staging_rows=?,
                review_rows=?,
                metadata_json=?,
                parsed_at=?,
                updated_at=?
            WHERE id=?
            """,
            (
                int(sum(pipeline_counts.values())),
                int(review_queue_rows),
                json_dumps(metadata),
                datetime.utcnow().isoformat(timespec="seconds"),
                datetime.utcnow().isoformat(timespec="seconds"),
                import_batch_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO vkpi_legacy_import_logs (
              import_batch_id, action, status, detail, row_count, metadata_json
            ) VALUES (?, 'stage_legacy_file', 'ok', ?, ?, ?)
            """,
            (
                import_batch_id,
                f"staged {sum(pipeline_counts.values())} rows from {source.name}",
                int(sum(pipeline_counts.values())),
                json_dumps(metadata),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return inspect_batch(batch_uid)


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row.items()) if hasattr(row, "items") else dict(row)


def inspect_batch(batch_uid: str) -> dict[str, Any]:
    conn = get_conn()
    batch_row = conn.execute("SELECT * FROM vkpi_legacy_import_batches WHERE batch_uid=?", (batch_uid,)).fetchone()
    if not batch_row:
        raise ValueError(f"batch not found: {batch_uid}")
    batch = _row_to_dict(batch_row)
    import_batch_id = int(batch["id"])
    pipeline_counts: dict[str, int] = {}
    for pipeline in PIPELINE_ORDER:
        table = PIPELINE_TABLES[pipeline]
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE import_batch_id=?", (import_batch_id,)).fetchone()
        pipeline_counts[pipeline] = int(row["n"] if row else 0)
    review_queue_rows = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_legacy_import_review_queue WHERE import_batch_id=?",
            (import_batch_id,),
        ).fetchone()["n"]
    )
    unmatched_count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_legacy_import_review_queue
            WHERE import_batch_id=? AND review_type='unmatched_kol_review'
            """,
            (import_batch_id,),
        ).fetchone()["n"]
    )
    validation_error_count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_legacy_import_review_queue
            WHERE import_batch_id=? AND review_type='validation_error'
            """,
            (import_batch_id,),
        ).fetchone()["n"]
    )
    committed_refs_count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_legacy_import_committed_refs
            WHERE import_batch_id=? AND rollback_status='not_rolled_back'
            """,
            (import_batch_id,),
        ).fetchone()["n"]
    )
    try:
        metadata = json.loads(batch.get("metadata_json") or "{}")
    except Exception:
        metadata = {}
    skipped_sheets = metadata.get("skipped_sheets") or []
    return {
        "batch_uid": batch_uid,
        "status": batch.get("status", ""),
        "pipeline_counts": pipeline_counts,
        "review_queue_rows": review_queue_rows,
        "skipped_sheets": skipped_sheets,
        "skipped_rows": sum(int(item.get("rows") or 0) for item in skipped_sheets if isinstance(item, dict)),
        "unmatched_count": unmatched_count,
        "validation_error_count": validation_error_count,
        "committed_refs_count": committed_refs_count,
        "source_file_sha256": batch.get("source_file_sha256", ""),
    }


def rollback_staging_batch(batch_uid: str) -> dict[str, Any]:
    conn = get_conn()
    batch_row = conn.execute(
        "SELECT id, status FROM vkpi_legacy_import_batches WHERE batch_uid=?",
        (batch_uid,),
    ).fetchone()
    if not batch_row:
        raise ValueError(f"batch not found: {batch_uid}")
    import_batch_id = int(batch_row["id"])
    committed_refs = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_legacy_import_committed_refs WHERE import_batch_id=?",
            (import_batch_id,),
        ).fetchone()["n"]
    )
    if committed_refs:
        raise RuntimeError("batch has committed refs; P2D main-table rollback is required")
    try:
        deleted_rows = 0
        for pipeline in PIPELINE_ORDER:
            table = PIPELINE_TABLES[pipeline]
            cursor = conn.execute(f"DELETE FROM {table} WHERE import_batch_id=?", (import_batch_id,))
            deleted_rows += int(getattr(cursor, "rowcount", 0) or 0)
        conn.execute("DELETE FROM vkpi_legacy_import_review_queue WHERE import_batch_id=?", (import_batch_id,))
        conn.execute(
            """
            UPDATE vkpi_legacy_import_batches
            SET status='rolled_back',
                rolled_back_rows=?,
                rolled_back_at=?,
                updated_at=?
            WHERE id=?
            """,
            (
                deleted_rows,
                datetime.utcnow().isoformat(timespec="seconds"),
                datetime.utcnow().isoformat(timespec="seconds"),
                import_batch_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO vkpi_legacy_import_logs (
              import_batch_id, action, status, detail, row_count, metadata_json
            ) VALUES (?, 'rollback_staging_batch', 'ok', ?, ?, ?)
            """,
            (
                import_batch_id,
                "cleared staging rows before main-table commit",
                deleted_rows,
                json_dumps({"batch_uid": batch_uid}),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"batch_uid": batch_uid, "status": "rolled_back", "rolled_back_rows": deleted_rows}


def format_batch_summary(result: dict[str, Any]) -> str:
    lines = [f"batch_uid={result.get('batch_uid', '')}"]
    pipeline_counts = result.get("pipeline_counts") or {}
    for pipeline in PIPELINE_ORDER:
        lines.append(f"pipeline.{pipeline}={int(pipeline_counts.get(pipeline, 0))}")
    lines.append(f"review_queue_rows={int(result.get('review_queue_rows', 0))}")
    lines.append(f"skipped={int(result.get('skipped_rows', 0))} rows / {len(result.get('skipped_sheets') or [])} sheets")
    lines.append(f"unmatched={int(result.get('unmatched_count', 0))}")
    lines.append(f"validation_error={int(result.get('validation_error_count', 0))}")
    lines.append(f"committed_refs={int(result.get('committed_refs_count', 0))}")
    return "\n".join(lines)
