"""Evidence appendices for structured reports."""
from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.db.connection import get_conn
from app.domains import business_truth
from app.domains.lineage import ensure_vkpi_lineage_schema
from app.domains.reports.contracts import DataStatus
from app.domains.reports.report_helpers import (
    _format_metric_value,
    _load_json,
    _metric_label,
    _money_cents,
)
from app.domains.staff import kpi_ledger
from app.platform.db.schema import ensure_vkpi_schema


def _compact_snapshot(snapshot: Any) -> str:
    data = snapshot if isinstance(snapshot, dict) else _load_json(snapshot)
    if not isinstance(data, dict) or not data:
        return "-"
    preferred = [
        "project_name",
        "kol_name",
        "staff_name",
        "product_sku",
        "title",
        "status",
    ]
    parts: list[str] = []
    for key in preferred:
        value = data.get(key)
        if value not in (None, ""):
            parts.append(f"{key}: {value}")
        if len(parts) >= 3:
            break
    if parts:
        return " · ".join(parts)
    return "(details withheld)"


def _safe_source_ref(value: Any) -> str:
    """Keep public URL paths useful while hiding tokens and opaque identifiers."""
    text = str(value or "").strip()
    if not text:
        return "-"
    parsed = urlsplit(text)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, "", ""))[:240]
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"ref:{digest}"


def _source_appendix(metric_run_id: int | None, *, limit_per_metric: int = 8) -> list[dict[str, Any]]:
    if not metric_run_id:
        return []
    ensure_vkpi_lineage_schema()
    conn = get_conn()
    values = conn.execute(
        """
        SELECT id, metric_key, value_numeric, currency, unit, source_count,
               data_status, confidence, is_partial
        FROM vkpi_metric_values
        WHERE run_id=?
        ORDER BY
            CASE metric_key
                WHEN 'views' THEN 1
                WHEN 'gmv' THEN 2
                WHEN 'cost' THEN 3
                WHEN 'net_contribution' THEN 4
                WHEN 'roi' THEN 5
                WHEN 'new_kol' THEN 6
                WHEN 'published_content' THEN 7
                WHEN 'valid_clicks' THEN 8
                ELSE 50
            END,
            metric_key
        """,
        (int(metric_run_id),),
    ).fetchall()
    appendix: list[dict[str, Any]] = []
    for value in values:
        value_data = dict(value)
        source_count = int(value_data.get("source_count") or 0)
        stored_status = str(value_data.get("data_status") or "").strip().lower()
        is_unavailable = stored_status in {"unavailable", "stale"}
        raw_value = value_data.get("value_numeric") if source_count > 0 and not is_unavailable else None
        metric_status = (
            DataStatus.UNAVAILABLE
            if is_unavailable
            else DataStatus.REAL if raw_value is not None else DataStatus.AWAITING_SOURCE
        )
        rows = [] if is_unavailable else conn.execute(
            """
            SELECT s.id, s.source_type, s.source_id, s.contribution_amount,
                   s.contribution_percent, s.evidence_type, s.evidence_ref,
                   s.project_id, s.kol_id, s.staff_id, s.occurred_at, s.snapshot_json,
                   p.project_name, p.project_uid,
                   k.channel_name AS kol_name, k.platform AS kol_platform,
                   u.name AS staff_name
            FROM vkpi_metric_sources s
            LEFT JOIN vkpi_projects p ON p.id = s.project_id
            LEFT JOIN kols k ON k.id = s.kol_id
            LEFT JOIN staff st ON st.id = s.staff_id
            LEFT JOIN users u ON u.id = st.user_id
            WHERE s.metric_value_id=?
            ORDER BY ABS(COALESCE(s.contribution_amount, 0)) DESC, s.occurred_at DESC, s.id DESC
            LIMIT ?
            """,
            (int(value_data["id"]), max(1, min(25, int(limit_per_metric or 8)))),
        ).fetchall()
        appendix.append({
            "metric_key": value_data.get("metric_key"),
            "metric_label": _metric_label(str(value_data.get("metric_key") or "")),
            "value": _format_metric_value(
                str(value_data.get("metric_key") or ""),
                raw_value,
                str(value_data.get("unit") or ""),
                str(value_data.get("currency") or ""),
            ),
            "raw_value": raw_value,
            "data_status": metric_status.value,
            "confidence": value_data.get("confidence"),
            "is_partial": bool(value_data.get("is_partial")),
            "source_count": 0 if is_unavailable else source_count,
            "retained_source_count": source_count if is_unavailable else 0,
            "rows": [
                {
                    "source_type": str(row["source_type"] or "-"),
                    "source_id": row["source_id"],
                    "amount": _format_metric_value(
                        str(value_data.get("metric_key") or ""),
                        row["contribution_amount"],
                        str(value_data.get("unit") or ""),
                        str(value_data.get("currency") or ""),
                    ),
                    "percent": f"{float(row['contribution_percent'] or 0):.1f}%",
                    "project": row["project_name"] or row["project_uid"] or "-",
                    "kol": row["kol_name"] or "-",
                    "staff": row["staff_name"] or (f"Staff #{row['staff_id']}" if row["staff_id"] else "-"),
                    "evidence": row["evidence_type"] or _safe_source_ref(row["evidence_ref"]),
                    "occurred_at": row["occurred_at"] or "-",
                    "snapshot": _compact_snapshot(row["snapshot_json"]),
                }
                for row in rows
            ],
        })
    return appendix


def _format_kpi_value(metric_key: str, value: Any) -> str:
    if value in (None, ""):
        return "未知"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "未知"
    if metric_key.endswith("_cents"):
        return _money_cents(numeric)
    if metric_key in {"roi", "net_roi", "recommendation_roi"}:
        return f"{numeric:.2f}x"
    return f"{numeric:,.2f}".rstrip("0").rstrip(".")


def _component_summary(components: Any, *, limit: int = 5) -> str:
    if not isinstance(components, list) or not components:
        return ""
    parts: list[str] = []
    for item in components[: max(1, int(limit or 5))]:
        if not isinstance(item, dict):
            continue
        label = item.get("metric_label") or item.get("metric_key") or "指标"
        parts.append(f"{label}: {_format_kpi_value('', item.get('contribution'))}")
    return " / ".join(parts)


def _kpi_source_appendix(
    start_date: str,
    end_date: str = "",
    *,
    scoped_staff_id: int | None = None,
    limit: int = 120,
) -> dict[str, Any]:
    ensure_vkpi_schema()
    conn = get_conn()
    where = (
        "WHERE kl.ledger_date >= ? AND "
        + business_truth.current_kpi_ledger_sql("kl")
    )
    params: list[Any] = [start_date[:10]]
    if end_date:
        where += " AND kl.ledger_date <= ?"
        params.append(end_date[:10])
    if scoped_staff_id:
        where += " AND kl.staff_id=?"
        params.append(int(scoped_staff_id))
    grouped = conn.execute(
        f"""
        SELECT kl.metric_key,
               COUNT(*) AS source_count,
               COALESCE(SUM(kl.metric_value), 0) AS total_value,
               MIN(kl.ledger_date) AS first_date,
               MAX(kl.ledger_date) AS last_date,
               MAX(kl.confidence) AS confidence
        FROM vkpi_kpi_ledger kl
        {where}
        GROUP BY kl.metric_key
        ORDER BY
            CASE
                WHEN kl.metric_key IN ('kpi_credit', 'workload_score') THEN 0
                WHEN substr(kl.metric_key, 1, 15)='recommendation_' THEN 1
                ELSE 2
            END,
            total_value DESC,
            kl.metric_key ASC
        LIMIT 80
        """,
        tuple(params),
    ).fetchall()
    source_rows = conn.execute(
        f"""
        SELECT kl.id, kl.ledger_date, kl.staff_id, kl.kol_id, kl.project_id,
               kl.metric_key, kl.metric_value, kl.source_type, kl.source_ref,
               kl.confidence, kl.metadata_json, kl.created_at,
               p.project_name, p.project_uid, p.product_sku,
               k.channel_name AS kol_name, k.platform AS kol_platform,
               u.name AS staff_name
        FROM vkpi_kpi_ledger kl
        LEFT JOIN vkpi_projects p ON p.id = kl.project_id
        LEFT JOIN kols k ON k.id = kl.kol_id
        LEFT JOIN staff st ON st.id = kl.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        {where}
        ORDER BY
            CASE
                WHEN kl.metric_key IN ('kpi_credit', 'workload_score') THEN 0
                WHEN substr(kl.metric_key, 1, 15)='recommendation_' THEN 1
                ELSE 2
            END,
            kl.ledger_date DESC,
            kl.id DESC
        LIMIT ?
        """,
        (*params, max(1, min(300, int(limit or 120)))),
    ).fetchall()
    grouped_rows: list[dict[str, Any]] = []
    for row in grouped:
        item = dict(row)
        key = str(item.get("metric_key") or "")
        item["metric_label"] = kpi_ledger.METRIC_LABELS.get(key, key)
        item["formatted_total"] = _format_kpi_value(key, item.get("total_value"))
        item["is_recommendation_metric"] = key.startswith("recommendation_")
        grouped_rows.append(item)
    detail_rows: list[dict[str, Any]] = []
    for row in source_rows:
        item = dict(row)
        key = str(item.get("metric_key") or "")
        metadata = _load_json(item.get("metadata_json"))
        components = metadata.get("components") if isinstance(metadata, dict) else []
        detail_rows.append({
            "id": item.get("id"),
            "ledger_date": item.get("ledger_date"),
            "metric_key": key,
            "metric_label": kpi_ledger.METRIC_LABELS.get(key, key),
            "value": _format_kpi_value(key, item.get("metric_value")),
            "source_type": item.get("source_type") or "-",
            "source_ref": _safe_source_ref(item.get("source_ref")),
            "confidence": item.get("confidence") or "-",
            "project": item.get("project_name") or item.get("project_uid") or "-",
            "kol": item.get("kol_name") or "-",
            "staff": item.get("staff_name") or (f"Staff #{item['staff_id']}" if item.get("staff_id") else "-"),
            "formula": metadata.get("formula") if isinstance(metadata, dict) else "",
            "component_summary": _component_summary(components),
            "recommendation_id": metadata.get("recommendation_id") if isinstance(metadata, dict) else None,
            "outcome_id": metadata.get("outcome_id") if isinstance(metadata, dict) else None,
            "launch_id": metadata.get("launch_id") if isinstance(metadata, dict) else None,
        })
    return {
        "start": start_date[:10],
        "end": end_date[:10] if end_date else "",
        "staff_id": scoped_staff_id,
        "grouped": grouped_rows,
        "source_rows": detail_rows,
        "source_count": len(detail_rows),
    }


__all__ = [
    "_compact_snapshot",
    "_component_summary",
    "_format_kpi_value",
    "_kpi_source_appendix",
    "_safe_source_ref",
    "_source_appendix",
]
