"""Read-only legacy spreadsheet audit for V-KPI imports.

P2A deliberately stops before staging or main-table writes. It only parses
legacy files and produces issue rows that can be reviewed before P2B.
"""
from __future__ import annotations

import csv
import json
import re
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree


NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

CANONICAL_FIELDS = {
    "platform": {
        "platform",
        "sourceplatform",
        "channel",
        "channels",
        "socialplatform",
        "平台",
        "渠道",
        "来源平台",
    },
    "handle": {
        "handle",
        "username",
        "user",
        "account",
        "accountname",
        "kol",
        "kolname",
        "kol_name",
        "influencer",
        "creator",
        "creatorname",
        "name",
        "displayname",
        "红人",
        "达人",
        "博主",
        "账号",
        "账号名",
        "用户名",
        "红人媒体",
    },
    "profile_url": {
        "url",
        "link",
        "profile",
        "profileurl",
        "profilelink",
        "homepage",
        "homeurl",
        "accounturl",
        "video_url",
        "videourl",
        "主页",
        "主页链接",
        "账号链接",
        "链接",
        "红人视频链接",
        "回片链接",
    },
    "email": {
        "email",
        "mail",
        "contactemail",
        "contactmail",
        "邮箱",
        "联系邮箱",
        "商务邮箱",
    },
    "phone": {
        "phone",
        "mobile",
        "tel",
        "telephone",
        "whatsapp",
        "电话",
        "手机",
        "手机号",
    },
    "contact": {
        "contact",
        "contacts",
        "contactinfo",
        "contactmethod",
        "wechat",
        "weixin",
        "line",
        "telegram",
        "联系方式",
        "联系",
        "微信",
        "备注联系方式",
    },
    "product": {
        "product",
        "productname",
        "productsku",
        "sku",
        "asin",
        "lens",
        "item",
        "推广产品",
        "产品",
        "产品型号",
        "型号",
    },
    "project": {
        "project",
        "projectname",
        "campaign",
        "campaignname",
        "collaboration",
        "合作",
        "项目",
        "项目名",
        "活动",
        "推广活动",
    },
    "amount": {
        "amount",
        "cost",
        "fee",
        "price",
        "quote",
        "quotation",
        "budget",
        "payment",
        "金额",
        "费用",
        "成本",
        "报价",
        "预算",
        "付款",
    },
    "currency": {
        "currency",
        "ccy",
        "币种",
        "货币",
    },
    "status": {
        "status",
        "stage",
        "progress",
        "合作进度",
        "状态",
        "阶段",
    },
    "owner": {
        "owner",
        "staff",
        "operator",
        "assignee",
        "登记对接人",
        "对接人",
        "负责人",
        "员工",
    },
    "notes": {
        "note",
        "notes",
        "remark",
        "remarks",
        "memo",
        "备注",
        "说明",
    },
}

PLATFORM_ALIASES = {
    "ig": "instagram",
    "ins": "instagram",
    "instagram": "instagram",
    "tiktok": "tiktok",
    "tik_tok": "tiktok",
    "tik tok": "tiktok",
    "tt": "tiktok",
    "youtube": "youtube",
    "yt": "youtube",
    "youtu": "youtube",
    "xiaohongshu": "xiaohongshu",
    "xhs": "xiaohongshu",
    "小红书": "xiaohongshu",
    "bilibili": "bilibili",
    "b站": "bilibili",
    "facebook": "facebook",
    "fb": "facebook",
    "reddit": "reddit",
    "discord": "discord",
    "twitter": "x",
    "x": "x",
    "weibo": "weibo",
    "微博": "weibo",
    "website": "website",
    "blog": "website",
}

CURRENCY_SYMBOLS = {
    "$": "USD",
    "usd": "USD",
    "us$": "USD",
    "rmb": "CNY",
    "cny": "CNY",
    "¥": "CNY",
    "￥": "CNY",
    "eur": "EUR",
    "€": "EUR",
    "gbp": "GBP",
    "£": "GBP",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compact(value: Any) -> str:
    return re.sub(r"[\s_/()（）:：\\.-]+", "", _text(value).lower())


def _header_field(header: Any) -> str:
    compact = _compact(header)
    if not compact:
        return ""
    for field, aliases in CANONICAL_FIELDS.items():
        if compact in {_compact(alias) for alias in aliases}:
            return field
    for field, aliases in CANONICAL_FIELDS.items():
        if any(len(_compact(alias)) >= 4 and _compact(alias) in compact for alias in aliases):
            return field
    return ""


def _normalize_platform(value: str, fallback_text: str = "") -> str:
    raw = _text(value).lower().replace("，", ",").replace("/", ",")
    candidates = [part.strip() for part in re.split(r"[,;|]+", raw) if part.strip()]
    if raw and not candidates:
        candidates = [raw]
    for candidate in candidates:
        compact = candidate.replace("-", " ").replace("_", " ").strip()
        if compact in PLATFORM_ALIASES:
            return PLATFORM_ALIASES[compact]
        for key, platform in PLATFORM_ALIASES.items():
            if key and key in compact:
                return platform

    text = fallback_text.lower()
    host_map = {
        "instagram.com": "instagram",
        "tiktok.com": "tiktok",
        "youtube.com": "youtube",
        "youtu.be": "youtube",
        "xiaohongshu.com": "xiaohongshu",
        "bilibili.com": "bilibili",
        "facebook.com": "facebook",
        "reddit.com": "reddit",
        "twitter.com": "x",
        "x.com": "x",
        "weibo.com": "weibo",
    }
    for host, platform in host_map.items():
        if host in text:
            return platform
    return ""


def _first_url(value: str) -> str:
    match = re.search(r"https?://[^\s,，;；]+|(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}/[^\s,，;；]+", value, re.I)
    if not match:
        return ""
    url = match.group(0).strip().rstrip(").]")
    return url if re.match(r"^https?://", url, re.I) else f"https://{url}"


def _extract_handle_from_url(url: str, platform: str = "") -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url if re.match(r"^https?://", url, re.I) else f"https://{url}")
    except Exception:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    if platform == "instagram":
        return parts[0].lstrip("@")
    if platform == "tiktok":
        for part in parts:
            if part.startswith("@"):
                return part.lstrip("@")
        return parts[0].lstrip("@")
    if platform == "youtube":
        for index, part in enumerate(parts):
            if part.startswith("@"):
                return part.lstrip("@")
            if part in {"channel", "c", "user"} and index + 1 < len(parts):
                return parts[index + 1]
    if platform == "xiaohongshu":
        if "profile" in parts:
            index = parts.index("profile")
            if index + 1 < len(parts):
                return parts[index + 1]
        return parts[-1]
    if platform == "bilibili":
        if "space" in parts and parts.index("space") + 1 < len(parts):
            return parts[parts.index("space") + 1]
        return parts[-1]
    return parts[0].lstrip("@")


def _normalize_handle(value: str, platform: str = "") -> str:
    raw = _text(value)
    if not raw:
        return ""
    if "://" in raw or re.search(r"\.[a-z]{2,}/", raw, re.I):
        return _extract_handle_from_url(raw, platform)
    raw = raw.split(",")[0].split("，")[0].strip()
    raw = raw.lstrip("@").strip()
    return re.sub(r"\s+", "", raw)


def _find_email(*values: str) -> str:
    for value in values:
        match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", value, re.I)
        if match:
            return match.group(0)
    return ""


def _find_phone(value: str) -> str:
    match = re.search(r"(?:\+\d{1,3}[- ]?)?(?:\d[- ]?){7,}", value)
    return match.group(0).strip() if match else ""


def _currency_from(value: str) -> str:
    lowered = value.lower()
    for marker, currency in CURRENCY_SYMBOLS.items():
        if marker in lowered or marker in value:
            return currency
    return ""


def _parse_amount(value: str) -> tuple[float | None, str]:
    raw = _text(value)
    if not raw:
        return None, ""
    currency = _currency_from(raw)
    cleaned = raw.replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if not match:
        return None, currency
    try:
        return float(match.group(0)), currency
    except ValueError:
        return None, currency


def _cell_ref_to_index(ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", ref.upper())
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return max(index - 1, 0)


def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        text_node = cell.find(f".//{NS_MAIN}t")
        return _text(text_node.text if text_node is not None else "")
    value_node = cell.find(f"{NS_MAIN}v")
    if value_node is None:
        return ""
    value = _text(value_node.text)
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (IndexError, ValueError):
            return ""
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    strings: list[str] = []
    for item in root.findall(f"{NS_MAIN}si"):
        parts = [node.text or "" for node in item.findall(f".//{NS_MAIN}t")]
        strings.append("".join(parts))
    return strings


def _xlsx_sheets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ElementTree.fromstring(zf.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
        for rel in rels.findall(f"{NS_PACKAGE_REL}Relationship")
    }
    sheets: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{NS_MAIN}sheet"):
        name = sheet.attrib.get("name", "Sheet")
        rel_id = sheet.attrib.get(f"{NS_REL}id", "")
        target = rel_map.get(rel_id, "")
        if not target:
            continue
        path = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
        sheets.append((name, path))
    return sheets


def _read_xlsx(path: Path, *, sheet_name: str = "", max_rows: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as zf:
        shared_strings = _xlsx_shared_strings(zf)
        for name, sheet_path in _xlsx_sheets(zf):
            if sheet_name and name != sheet_name:
                continue
            root = ElementTree.fromstring(zf.read(sheet_path))
            table: list[list[str]] = []
            for row_node in root.findall(f".//{NS_MAIN}sheetData/{NS_MAIN}row"):
                values: list[str] = []
                for cell in row_node.findall(f"{NS_MAIN}c"):
                    ref = cell.attrib.get("r", "")
                    column_index = _cell_ref_to_index(ref)
                    while len(values) <= column_index:
                        values.append("")
                    values[column_index] = _xlsx_cell_value(cell, shared_strings)
                table.append(values)
                if max_rows and len(table) >= max_rows + 1:
                    break
            header_index = next((idx for idx, row in enumerate(table) if any(_text(cell) for cell in row)), -1)
            if header_index < 0:
                continue
            headers = [_text(value) or f"column_{idx + 1}" for idx, value in enumerate(table[header_index])]
            for offset, row in enumerate(table[header_index + 1 :], start=header_index + 2):
                if not any(_text(cell) for cell in row):
                    continue
                raw = {headers[idx] if idx < len(headers) else f"column_{idx + 1}": _text(value) for idx, value in enumerate(row)}
                rows.append({"sheet": name, "row_number": offset, "raw": raw})
    return rows


def _read_csv(path: Path, *, max_rows: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for offset, raw in enumerate(reader, start=2):
            if max_rows and len(rows) >= max_rows:
                break
            clean = {str(key or f"column_{idx + 1}"): _text(value) for idx, (key, value) in enumerate(raw.items())}
            if not any(clean.values()):
                continue
            rows.append({"sheet": "csv", "row_number": offset, "raw": clean})
    return rows


def read_legacy_rows(path: str | Path, *, sheet_name: str = "", max_rows: int = 0) -> list[dict[str, Any]]:
    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(str(source))
    suffix = source.suffix.lower()
    if suffix == ".csv":
        return _read_csv(source, max_rows=max_rows)
    if suffix == ".xlsx":
        return _read_xlsx(source, sheet_name=sheet_name, max_rows=max_rows)
    if suffix == ".xls":
        raise ValueError(".xls binary workbooks are not supported in P2A without an explicit parser dependency")
    raise ValueError(f"unsupported legacy file type: {suffix or '<none>'}")


def _map_fields(raw: dict[str, str]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for header, value in raw.items():
        field = _header_field(header)
        if field and _text(value) and not mapped.get(field):
            mapped[field] = _text(value)
    all_text = " ".join(_text(value) for value in raw.values())
    if not mapped.get("profile_url"):
        mapped["profile_url"] = _first_url(all_text)
    if not mapped.get("email"):
        mapped["email"] = _find_email(all_text)
    return mapped


def _raw_preview(raw: dict[str, str], limit: int = 5) -> str:
    items = [(key, value) for key, value in raw.items() if _text(value)]
    return json.dumps(dict(items[:limit]), ensure_ascii=False, default=str)


def _risk_level(issues: list[str]) -> str:
    high = {"missing_platform", "missing_handle", "invalid_amount", "negative_amount"}
    if any(issue in high for issue in issues):
        return "high"
    if issues:
        return "medium"
    return "low"


def audit_legacy_file(path: str | Path, *, sheet_name: str = "", max_rows: int = 0) -> dict[str, Any]:
    source = Path(path).expanduser()
    source_stat = source.stat()
    legacy_rows = read_legacy_rows(source, sheet_name=sheet_name, max_rows=max_rows)
    audited: list[dict[str, Any]] = []

    for item in legacy_rows:
        raw = item["raw"]
        mapped = _map_fields(raw)
        raw_text = " ".join(_text(value) for value in raw.values())
        profile_url = mapped.get("profile_url") or _first_url(raw_text)
        platform = _normalize_platform(mapped.get("platform", ""), f"{profile_url} {raw_text}")
        handle = _normalize_handle(mapped.get("handle", ""), platform) or _extract_handle_from_url(profile_url, platform)
        email = mapped.get("email") or _find_email(raw_text)
        phone = mapped.get("phone") or _find_phone(mapped.get("contact", "") or raw_text)
        contact = email or phone or mapped.get("contact", "")
        product = mapped.get("product", "")
        project = mapped.get("project", "")
        amount_raw = mapped.get("amount", "")
        amount_value, amount_currency = _parse_amount(amount_raw)
        currency = (mapped.get("currency") or amount_currency).upper()
        issues: list[str] = []

        if not platform:
            issues.append("missing_platform")
        if not handle:
            issues.append("missing_handle")
        if not contact:
            issues.append("missing_contact")
        if not product and not project:
            issues.append("missing_product_project")
        if amount_raw and amount_value is None:
            issues.append("invalid_amount")
        if amount_value is not None and amount_value < 0:
            issues.append("negative_amount")
        if amount_raw and amount_value is not None and not currency:
            issues.append("missing_currency")

        audited.append(
            {
                "sheet": item.get("sheet", ""),
                "row_number": item.get("row_number"),
                "platform": platform,
                "handle": handle,
                "dedup_key": f"{platform}:{handle.lower()}" if platform and handle else "",
                "profile_url": profile_url,
                "contact": contact,
                "email": email,
                "product": product,
                "project": project,
                "owner": mapped.get("owner", ""),
                "status": mapped.get("status", ""),
                "amount_raw": amount_raw,
                "amount_value": amount_value,
                "currency": currency,
                "issues": issues,
                "raw_preview": _raw_preview(raw),
            }
        )

    duplicates = Counter(row["dedup_key"] for row in audited if row.get("dedup_key"))
    for row in audited:
        if row.get("dedup_key") and duplicates[row["dedup_key"]] > 1:
            row["issues"].append("duplicate_kol_candidate")
        row["risk_level"] = _risk_level(row["issues"])
        row["manual_review"] = bool(row["issues"])
        row["issues_text"] = ",".join(row["issues"])

    issue_counts = Counter(issue for row in audited for issue in row["issues"])
    risk_counts = Counter(row["risk_level"] for row in audited)
    recognizable = [row for row in audited if row.get("platform") and row.get("handle")]
    duplicate_candidates = [
        {"dedup_key": key, "count": count}
        for key, count in sorted(duplicates.items())
        if key and count > 1
    ]
    return {
        "source": {
            "path": str(source),
            "filename": source.name,
            "size_bytes": source_stat.st_size,
            "modified_at": datetime.fromtimestamp(source_stat.st_mtime).isoformat(timespec="seconds"),
            "sheet": sheet_name or "",
            "max_rows": max_rows,
        },
        "summary": {
            "total_rows": len(audited),
            "recognizable_kol_rows": len(recognizable),
            "duplicate_kol_candidates": sum(item["count"] for item in duplicate_candidates),
            "duplicate_groups": len(duplicate_candidates),
            "missing_contact_rows": issue_counts.get("missing_contact", 0),
            "missing_product_project_rows": issue_counts.get("missing_product_project", 0),
            "amount_currency_issue_rows": issue_counts.get("invalid_amount", 0)
            + issue_counts.get("negative_amount", 0)
            + issue_counts.get("missing_currency", 0),
            "manual_review_rows": sum(1 for row in audited if row["manual_review"]),
            "low_risk_rows": risk_counts.get("low", 0),
            "medium_risk_rows": risk_counts.get("medium", 0),
            "high_risk_rows": risk_counts.get("high", 0),
        },
        "issue_counts": dict(issue_counts),
        "duplicate_candidates": duplicate_candidates,
        "rows": audited,
    }


CSV_FIELDS = [
    "sheet",
    "row_number",
    "risk_level",
    "manual_review",
    "platform",
    "handle",
    "profile_url",
    "contact",
    "email",
    "product",
    "project",
    "owner",
    "status",
    "amount_raw",
    "amount_value",
    "currency",
    "issues_text",
    "raw_preview",
]


def markdown_report(result: dict[str, Any]) -> str:
    source = result.get("source") or {}
    summary = result.get("summary") or {}
    issue_counts = result.get("issue_counts") or {}
    duplicates = result.get("duplicate_candidates") or []
    lines = [
        "# V-KPI P2A Legacy Excel Read-Only Audit",
        "",
        f"- Source: `{source.get('path', '')}`",
        f"- Filename: `{source.get('filename', '')}`",
        f"- Sheet filter: `{source.get('sheet') or 'all'}`",
        f"- Generated at: `{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}`",
        "",
        "## Summary",
        "",
        f"- Total rows: {summary.get('total_rows', 0)}",
        f"- Recognizable platform/handle rows: {summary.get('recognizable_kol_rows', 0)}",
        f"- Duplicate KOL candidate rows: {summary.get('duplicate_kol_candidates', 0)}",
        f"- Missing contact rows: {summary.get('missing_contact_rows', 0)}",
        f"- Missing product/project rows: {summary.get('missing_product_project_rows', 0)}",
        f"- Amount/currency issue rows: {summary.get('amount_currency_issue_rows', 0)}",
        f"- Manual review rows: {summary.get('manual_review_rows', 0)}",
        "",
        "## Risk",
        "",
        f"- Low: {summary.get('low_risk_rows', 0)}",
        f"- Medium: {summary.get('medium_risk_rows', 0)}",
        f"- High: {summary.get('high_risk_rows', 0)}",
        "",
        "## Issue Counts",
        "",
    ]
    if issue_counts:
        for key, count in sorted(issue_counts.items()):
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- none: 0")
    lines.extend(["", "## Duplicate Candidates", ""])
    if duplicates:
        for item in duplicates[:50]:
            lines.append(f"- {item.get('dedup_key')}: {item.get('count')}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## P2A Guardrail",
            "",
            "This audit is read-only. It does not write `kols`, `vkpi_projects`, `vkpi_kol_pool`, `vkpi_cost_ledger`, or `vkpi_ai_cost_ledger`.",
            "",
            "Detailed row issues are exported to the companion CSV.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_reports(result: dict[str, Any], output_dir: str | Path, *, prefix: str | None = None) -> dict[str, str]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = prefix or datetime.utcnow().strftime("%Y-%m-%d")
    md_path = out_dir / f"{date_prefix}-vkpi-legacy-excel-audit.md"
    csv_path = out_dir / f"{date_prefix}-vkpi-legacy-excel-audit.csv"
    md_path.write_text(markdown_report(result), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in result.get("rows") or []:
            writer.writerow(row)
    return {"markdown": str(md_path), "csv": str(csv_path)}
