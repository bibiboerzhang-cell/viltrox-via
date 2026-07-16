"""Versioned report and data-status contracts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any


REPORT_SCHEMA_VERSION = "report.v1"
REPORT_PERIODS = ("weekly", "monthly")
REPORT_LANGUAGES = ("zh", "en")
REPORT_SECTION_KEYS = (
    "kpiOverview",
    "attribution",
    "projects",
    "ledger",
    "risks",
    "summary",
)
REPORT_FORMATS = ("visual", "markdown")
REPORT_SCOPES = ("self", "all")
REPORT_PERIOD_DAYS = {"weekly": 7, "monthly": 30}


class ReportContractError(ValueError):
    """Stable validation error shared by domain and HTTP report entrypoints."""

    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = str(code)
        self.field = str(field)
        self.message = str(message)

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


class DataStatus(str, Enum):
    """Trust state for one report value.

    A numeric zero is a value, not a status. Unknown states therefore require a
    ``None`` value so callers cannot silently turn missing data into a real zero.
    """

    REAL = "real"
    SEEDED = "seeded"
    PARTIAL = "partial"
    AWAITING_SOURCE = "awaiting_source"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"


KNOWN_DATA_STATUSES = frozenset({DataStatus.REAL, DataStatus.SEEDED, DataStatus.PARTIAL})
UNKNOWN_DATA_STATUSES = frozenset(
    {DataStatus.AWAITING_SOURCE, DataStatus.EMPTY, DataStatus.UNAVAILABLE}
)


@dataclass(frozen=True, slots=True)
class ReportMetricSpec:
    key: str
    label: str
    value_type: str = "number"
    unit: str = "count"
    label_en: str = ""

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("report metric key is required")
        if not self.label.strip():
            raise ValueError("report metric label is required")

    def label_for(self, language: str) -> str:
        return self.label_en if language == "en" and self.label_en else self.label

    def as_dict(self, *, language: str = "zh") -> dict[str, str]:
        return {
            "key": self.key,
            "label": self.label_for(language),
            "value_type": self.value_type,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class ReportMetricValue:
    spec: ReportMetricSpec
    value: int | float | str | None
    data_status: DataStatus
    source_count: int | None = None
    note: str = ""

    def __post_init__(self) -> None:
        status = self.data_status
        if not isinstance(status, DataStatus):
            status = DataStatus(str(status))
            object.__setattr__(self, "data_status", status)
        if status in UNKNOWN_DATA_STATUSES and self.value is not None:
            raise ValueError(f"{status.value} report value must be None")
        if status in KNOWN_DATA_STATUSES and self.value is None:
            raise ValueError(f"{status.value} report value must not be None")
        if self.source_count is not None and int(self.source_count) < 0:
            raise ValueError("source_count must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.spec.as_dict(),
            "value": self.value,
            "data_status": self.data_status.value,
            "source_count": self.source_count,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class ReportSpec:
    report_type: str
    title: str
    metrics: tuple[ReportMetricSpec, ...]
    schema_version: str = REPORT_SCHEMA_VERSION
    title_en: str = ""

    def __post_init__(self) -> None:
        if not self.report_type.strip():
            raise ValueError("report_type is required")
        keys = [metric.key for metric in self.metrics]
        if len(keys) != len(set(keys)):
            raise ValueError("report metric keys must be unique")

    def metric(self, key: str) -> ReportMetricSpec:
        for metric in self.metrics:
            if metric.key == key:
                return metric
        raise KeyError(f"unknown report metric: {key}")

    def title_for(self, language: str) -> str:
        return self.title_en if language == "en" and self.title_en else self.title

    def as_dict(self, *, language: str = "zh") -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_type": self.report_type,
            "title": self.title_for(language),
            "metrics": [metric.as_dict(language=language) for metric in self.metrics],
            "request_contract": {
                "periods": list(REPORT_PERIODS),
                "languages": list(REPORT_LANGUAGES),
                "sections": list(REPORT_SECTION_KEYS),
                "formats": list(REPORT_FORMATS),
                "scopes": list(REPORT_SCOPES),
            },
            "data_status_contract": {
                "known": [status.value for status in sorted(KNOWN_DATA_STATUSES, key=lambda item: item.value)],
                "unknown": [status.value for status in sorted(UNKNOWN_DATA_STATUSES, key=lambda item: item.value)],
                "zero_is_known_value": True,
                "unknown_value": None,
            },
        }


WEEKLY_REPORT_SPEC = ReportSpec(
    report_type="weekly",
    title="Viltrox Marketing 周报",
    title_en="Viltrox Marketing Weekly Report",
    metrics=(
        ReportMetricSpec("views", "播放量", label_en="Views", unit="count"),
        ReportMetricSpec("sales_cents", "本周销售额", label_en="Attributed sales", value_type="money", unit="USD_cents"),
        ReportMetricSpec("cost_cents", "成本", label_en="Cost", value_type="money", unit="USD_cents"),
        ReportMetricSpec("new_kol", "新增 KOL", label_en="New KOLs", unit="count"),
        ReportMetricSpec("published_content", "已发布内容", label_en="Published content", unit="count"),
        ReportMetricSpec("active_projects", "进行中项目", label_en="Active projects", unit="count"),
    ),
)

MONTHLY_REPORT_SPEC = ReportSpec(
    report_type="monthly",
    title="Viltrox Marketing 月报",
    title_en="Viltrox Marketing Monthly Report",
    metrics=(
        ReportMetricSpec("views", "播放量", label_en="Views", unit="count"),
        ReportMetricSpec("sales_cents", "本月销售额", label_en="Attributed sales", value_type="money", unit="USD_cents"),
        ReportMetricSpec("cost_cents", "成本", label_en="Cost", value_type="money", unit="USD_cents"),
        ReportMetricSpec("new_kol", "新增 KOL", label_en="New KOLs", unit="count"),
        ReportMetricSpec("published_content", "已发布内容", label_en="Published content", unit="count"),
        ReportMetricSpec("active_projects", "进行中项目", label_en="Active projects", unit="count"),
    ),
)


def report_spec_for(period: str) -> ReportSpec:
    if period == "monthly":
        return MONTHLY_REPORT_SPEC
    if period == "weekly":
        return WEEKLY_REPORT_SPEC
    raise KeyError(f"unknown report period: {period}")


def report_data_status(values: list[ReportMetricValue]) -> DataStatus:
    """Return the aggregate status without treating a real zero as missing."""
    if not values:
        return DataStatus.EMPTY
    known = sum(value.data_status in KNOWN_DATA_STATUSES for value in values)
    if known == len(values):
        return DataStatus.PARTIAL if any(
            value.data_status is DataStatus.PARTIAL for value in values
        ) else DataStatus.REAL
    return DataStatus.PARTIAL if known else DataStatus.AWAITING_SOURCE


REPORT_REQUEST_FIELDS = (
    "report_type",
    "period",
    "period_days",
    "date",
    "date_from",
    "date_to",
    "language",
    "sections",
    "format",
    "scope",
    "staff_id",
    "project_id",
)
_REPORT_ALIASES = {
    "report_type": ("report_type", "reportType"),
    "date_from": ("date_from", "startDate"),
    "date_to": ("date_to", "endDate"),
    "staff_id": ("staff_id", "staffId"),
    "project_id": ("project_id", "projectId"),
}
_REPORT_INPUT_FIELDS = frozenset(REPORT_REQUEST_FIELDS).union(
    alias for aliases in _REPORT_ALIASES.values() for alias in aliases
)
_MISSING = object()


def _contract_error(code: str, field: str, message: str) -> ReportContractError:
    return ReportContractError(code, message, field=field)


def _alias_value(raw: dict[str, Any], field: str) -> Any:
    aliases = _REPORT_ALIASES.get(field, (field,))
    supplied = [(alias, raw[alias]) for alias in aliases if alias in raw]
    if not supplied:
        return _MISSING
    comparable = {str(value).strip() for _alias, value in supplied}
    if len(comparable) > 1:
        raise _contract_error(
            "report_request_conflict",
            field,
            f"conflicting values supplied for {field}",
        )
    return supplied[0][1]


def _choice(value: Any, *, field: str, supported: tuple[str, ...], default: str) -> str:
    if value is _MISSING:
        return default
    clean = str(value).strip().lower()
    if clean not in supported:
        raise _contract_error(
            "report_request_unsupported_value",
            field,
            f"unsupported {field}: {clean or '<empty>'}",
        )
    return clean


def _positive_int(value: Any, *, field: str, maximum: int | None = None) -> int | None:
    if value is _MISSING:
        return None
    if isinstance(value, bool):
        parsed = 0
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    else:
        clean = str(value).strip()
        try:
            parsed = int(clean)
        except (TypeError, ValueError):
            parsed = 0
    if parsed <= 0 or (maximum is not None and parsed > maximum):
        bounds = f"1..{maximum}" if maximum is not None else "a positive integer"
        raise _contract_error(
            "report_request_invalid_integer",
            field,
            f"{field} must be {bounds}",
        )
    return parsed


def _date_value(value: Any, *, field: str) -> date | None:
    if value is _MISSING:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    clean = str(value).strip()
    try:
        return date.fromisoformat(clean)
    except ValueError:
        raise _contract_error(
            "report_request_invalid_date",
            field,
            f"{field} must use YYYY-MM-DD",
        ) from None


def _sections_value(value: Any) -> list[str]:
    if value is _MISSING:
        return list(REPORT_SECTION_KEYS)
    if not isinstance(value, (list, tuple)):
        raise _contract_error(
            "report_request_invalid_sections",
            "sections",
            "sections must be a non-empty array",
        )
    sections = [str(item).strip() for item in value]
    if not sections:
        raise _contract_error(
            "report_request_invalid_sections",
            "sections",
            "sections must be a non-empty array",
        )
    unsupported = next((item for item in sections if item not in REPORT_SECTION_KEYS), "")
    if unsupported:
        raise _contract_error(
            "report_request_unsupported_value",
            "sections",
            f"unsupported report section: {unsupported}",
        )
    if len(sections) != len(set(sections)):
        raise _contract_error(
            "report_request_duplicate_section",
            "sections",
            "sections must not contain duplicates",
        )
    return sections


def sanitize_report_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and canonicalize the versioned report request contract.

    The historical function name is retained for callers, but unsupported input
    now fails closed instead of being silently dropped or clamped.
    """
    if filters is None:
        raw: dict[str, Any] = {}
    elif isinstance(filters, dict):
        raw = dict(filters)
    else:
        raise _contract_error(
            "report_request_invalid_body",
            "body",
            "report request body must be an object",
        )

    unsupported_fields = sorted(set(raw).difference(_REPORT_INPUT_FIELDS))
    if unsupported_fields:
        field = unsupported_fields[0]
        raise _contract_error(
            "report_request_unsupported_field",
            field,
            f"unsupported report field: {field}",
        )

    report_type_raw = _alias_value(raw, "report_type")
    period_raw = raw.get("period", _MISSING)
    report_type = _choice(
        report_type_raw if report_type_raw is not _MISSING else period_raw,
        field="report_type",
        supported=REPORT_PERIODS,
        default="weekly",
    )
    period = _choice(
        period_raw if period_raw is not _MISSING else report_type_raw,
        field="period",
        supported=REPORT_PERIODS,
        default=report_type,
    )
    if report_type != period:
        raise _contract_error(
            "report_request_conflict",
            "period",
            "report_type and period must match",
        )

    period_days_raw = raw.get("period_days", _MISSING)
    period_days = _positive_int(period_days_raw, field="period_days", maximum=366)
    default_days = REPORT_PERIOD_DAYS[period]

    requested_date = _date_value(raw.get("date", _MISSING), field="date")
    date_from = _date_value(_alias_value(raw, "date_from"), field="date_from")
    date_to = _date_value(_alias_value(raw, "date_to"), field="date_to")
    if requested_date and date_to and requested_date != date_to:
        raise _contract_error(
            "report_request_conflict",
            "date",
            "date and date_to must match",
        )
    date_to = date_to or requested_date

    if date_from and date_to:
        inclusive_days = (date_to - date_from).days + 1
        if inclusive_days <= 0:
            raise _contract_error(
                "report_request_invalid_range",
                "date_from",
                "date_from must not be after date_to",
            )
        if inclusive_days > 366:
            raise _contract_error(
                "report_request_invalid_range",
                "date_from",
                "report date range must not exceed 366 days",
            )
        if period_days is not None and period_days != inclusive_days:
            raise _contract_error(
                "report_request_period_mismatch",
                "period_days",
                "period_days must match the inclusive date range",
            )
        period_days = inclusive_days
    else:
        period_days = period_days or default_days
        if date_from:
            date_to = date_from + timedelta(days=period_days - 1)
        else:
            date_to = date_to or datetime.now(timezone.utc).date()
            date_from = date_to - timedelta(days=period_days - 1)

    language = _choice(
        raw.get("language", _MISSING),
        field="language",
        supported=REPORT_LANGUAGES,
        default="zh",
    )
    report_format = _choice(
        raw.get("format", _MISSING),
        field="format",
        supported=REPORT_FORMATS,
        default="visual",
    )
    sections = _sections_value(raw.get("sections", _MISSING))

    normalized: dict[str, Any] = {
        "report_type": report_type,
        "period": period,
        "period_days": period_days,
        "date": date_to.isoformat(),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "language": language,
        "sections": sections,
        "format": report_format,
    }
    if "scope" in raw:
        normalized["scope"] = _choice(
            raw.get("scope"),
            field="scope",
            supported=REPORT_SCOPES,
            default="self",
        )
    for field in ("staff_id", "project_id"):
        value = _positive_int(_alias_value(raw, field), field=field)
        if value is not None:
            normalized[field] = value
    return normalized


def public_report_request(metadata: Any) -> dict[str, Any]:
    """Return only normalized request fields from persisted report metadata."""
    if not isinstance(metadata, dict):
        return {}
    return {field: metadata[field] for field in REPORT_REQUEST_FIELDS if field in metadata}
