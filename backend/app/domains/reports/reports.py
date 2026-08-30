"""V-KPI structured report generation."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import alerts
from app.domains import costs
from app.domains import attribution
from app.domains import lineage as metric_lineage
from app.domains.dashboard import decision_dashboard
from app.domains.staff import decision_staff
from app.domains.access import scope
from app.domains.projects import workflow
from app.domains.reports import pdf_renderer
from app.domains.reports import render_recovery
from app.domains.reports import weekly_context as __weekly_context
from app.domains.reports.contracts import (
    DataStatus,
    ReportContractError,
    ReportMetricValue,
    REPORT_SECTION_KEYS,
    public_report_request,
    report_data_status,
    report_spec_for,
    sanitize_report_filters,
)
from app.domains.reports.report_appendices import (
    _compact_snapshot,
    _component_summary,
    _format_kpi_value,
    _kpi_source_appendix,
    _safe_source_ref,
    _source_appendix,
)
from app.domains.reports.report_helpers import (
    _build_weekly_prompt,
    _first_int,
    _format_alerts_for_prompt,
    _format_funnel_for_prompt,
    _format_kpis_for_prompt,
    _format_metric_value,
    _format_staff_for_prompt,
    _generate_ai_summary,
    _int_or_none,
    _json,
    _load_json,
    _localized,
    _metric_label,
    _money_cents,
    _staff_name,
    _uid,
    _utcnow,
)
from app.domains.reports.report_rendering import _markdown_cell, _render_markdown_report
from app.domains.reports.report_failure_recovery import (
    cleanup_generated_report_files as _cleanup_generated_report_files,
    delete_partial_report_file_rows as _delete_partial_report_file_rows,
    persist_failed_report as _persist_failed_report,
)
from app.platform.db.schema import ensure_vkpi_schema
from app.domains.lineage import ensure_vkpi_lineage_schema
from app.domains.reports.schema import ensure_vkpi_reports_schema

logger = get_logger(__name__)


def _report_scope_id(filters: dict[str, Any], staff: dict[str, Any] | None) -> int | None:
    """Resolve the explicit report scope without trusting a frontend staff id."""
    requested_scope = str(filters.get("scope") or "")
    requested_staff_id = _int_or_none(filters.get("staff_id"))
    actor_id = workflow.staff_id(staff) or scope.actor_staff_id(staff)
    if requested_scope == "self":
        if not actor_id:
            raise scope.ScopeDenied("report scope denied")
        if requested_staff_id and requested_staff_id != actor_id:
            raise ReportContractError(
                "report_request_scope_conflict",
                "scope=self cannot target another staff member",
                field="staff_id",
            )
        return int(actor_id)
    if requested_scope == "all":
        if requested_staff_id:
            raise ReportContractError(
                "report_request_scope_conflict",
                "scope=all must not include staff_id",
                field="staff_id",
            )
        if not scope.can_view_all(staff):
            raise scope.ScopeDenied("report scope denied")
        return None
    return scope.effective_staff_id(staff, requested_staff_id)


def _period(period_days: int, *, date_from: str = "", date_to: str = "") -> tuple[str, str]:
    if date_from and date_to:
        start_day = date.fromisoformat(date_from)
        end_day = date.fromisoformat(date_to)
        start = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc)
        end = datetime.combine(end_day, datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc)
        return start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, min(366, int(period_days or 7))))
    return start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_moment(value: Any) -> datetime | None:
    """Best-effort parse of a DB timestamp/date into an aware UTC datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip().replace("Z", "+00:00").replace(" ", "T", 1)
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            try:
                moment = datetime.fromisoformat(text[:10])
            except ValueError:
                return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _in_window(value: Any, start: datetime | None, end: datetime | None) -> bool:
    """True when value falls inside [start, end]. Undatable rows are excluded so the
    weekly KPI cards match the reported period instead of counting all history."""
    if start is None or end is None:
        return True
    moment = _parse_moment(value)
    if moment is None:
        return False
    inclusive_end = end + timedelta(seconds=1) if end.microsecond == 0 else end
    return start <= moment < inclusive_end


def _is_current_report_date(value: Any) -> bool:
    clean = str(value or "")
    return clean in {
        datetime.now(timezone.utc).date().isoformat(),
        datetime.now().date().isoformat(),
    }


def _maybe_metric_run(
    period_days: int,
    staff: dict[str, Any] | None,
    scoped_staff_id: int | None,
    *,
    report_type: str = "weekly",
) -> int | None:
    try:
        ensure_vkpi_lineage_schema()
        result = metric_lineage.generate_run(
            period_days=period_days,
            scope_type="staff" if scoped_staff_id else "all",
            scope_id=scoped_staff_id,
            trigger_source=f"{report_type}_report",
            generated_by_staff_id=workflow.staff_id(staff) or None,
            metadata={"source": "reports.generate_weekly_report", "report_type": report_type},
        )
        return int(result.get("run_id") or 0) or None
    except Exception:
        return None


def _rollback_report_transaction(conn: Any) -> None:
    """Reset SQLite or PostgreSQL transaction state before recovery writes."""
    try:
        conn.rollback()
    except Exception as exc:  # pragma: no cover - a dead connection cannot be recovered here
        logger.warning("vkpi report rollback failed: %s", type(exc).__name__)


def rollback_current_report_transaction() -> None:
    """Reset the request DB connection after a non-critical audit failure."""
    try:
        conn = get_conn()
    except Exception as exc:  # pragma: no cover - audit outage may include DB acquisition failure
        logger.warning("vkpi report audit rollback connection unavailable: %s", type(exc).__name__)
        return
    _rollback_report_transaction(conn)


def _metric_payload(metric: ReportMetricValue, *, language: str = "zh") -> dict[str, Any]:
    payload = metric.as_dict()
    payload["label"] = metric.spec.label_for(language)
    raw_value = payload.pop("value")
    if metric.spec.value_type == "money":
        display_value = _money_cents(raw_value, language=language)
    else:
        display_value = _localized(language, "未知", "Unknown") if raw_value is None else f"{int(raw_value):,}"
    return {**payload, "value": display_value, "raw_value": raw_value}


def build_weekly_context(
    period_days: int | None = None,
    *,
    staff: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    report_uid: str = "",
) -> dict[str, Any]:
    return __weekly_context.build_weekly_context_impl(
        period_days,
        staff=staff,
        filters=filters,
        report_uid=report_uid,
        deps=__weekly_context.WeeklyContextDependencies(
            ensure_schema=ensure_vkpi_schema,
            sanitize_filters=sanitize_report_filters,
            report_spec_for=report_spec_for,
            period=_period,
            parse_moment=_parse_moment,
            report_scope_id=_report_scope_id,
            is_current_report_date=_is_current_report_date,
            dashboard=decision_dashboard.dashboard,
            staff_kpi=decision_staff.staff_kpi,
            list_projects=workflow.list_projects,
            list_attributions=attribution.list_attributions,
            list_costs=costs.list_costs,
            list_alerts=alerts.list_alerts,
            assert_project_access=scope.assert_project_access,
            in_window=_in_window,
            int_or_none=_int_or_none,
            first_int=_first_int,
            localized=_localized,
            money_cents=_money_cents,
            metric_payload=_metric_payload,
            kpi_source_appendix=_kpi_source_appendix,
            utcnow=_utcnow,
            staff_name=_staff_name,
            metric_value_factory=ReportMetricValue,
            report_data_status=report_data_status,
            data_status=DataStatus,
        ),
    )


def generate_weekly_report(
    *,
    period_days: int | None = None,
    staff: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    render_pdf: bool = True,
) -> dict[str, Any]:
    ensure_vkpi_reports_schema()
    conn = get_conn()
    scope.assert_legacy_default_organization(staff, conn, feature="report")
    raw_filters = dict(filters) if isinstance(filters, dict) else filters
    if isinstance(raw_filters, dict) and period_days is not None and "period_days" not in raw_filters:
        raw_filters["period_days"] = period_days
    safe_filters = sanitize_report_filters(raw_filters)
    if period_days is not None:
        requested_period_days = _int_or_none(period_days)
        if requested_period_days is None:
            raise ReportContractError(
                "report_request_invalid_integer",
                "period_days must be 1..366",
                field="period_days",
            )
        if requested_period_days != int(safe_filters["period_days"]):
            raise ReportContractError(
                "report_request_period_mismatch",
                "period_days must match the normalized report request",
                field="period_days",
            )
    effective_period_days = int(safe_filters["period_days"])
    actor_id = workflow.staff_id(staff) or None
    scoped_staff_id = _report_scope_id(safe_filters, staff)
    report_type = str(safe_filters["report_type"])
    report_uid = _uid(report_type)
    context = build_weekly_context(
        period_days=effective_period_days,
        staff=staff,
        filters=safe_filters,
        report_uid=report_uid,
    )
    selected_sections = set(safe_filters["sections"])
    current_window = _is_current_report_date(safe_filters["date_to"])
    metric_run_id = (
        _maybe_metric_run(
            effective_period_days,
            staff,
            scoped_staff_id,
            report_type=report_type,
        )
        if current_window and "attribution" in selected_sections
        else None
    )
    context["metric_run_id"] = metric_run_id
    context["source_appendix"] = (
        _source_appendix(metric_run_id) if "attribution" in selected_sections else []
    )
    if "summary" in selected_sections:
        ai_summary = _generate_ai_summary(context, staff=staff)
        if ai_summary:
            context["summary_text"] = ai_summary
    report_metadata = {
        **safe_filters,
        "_report_contract": {
            "schema_version": context["report_spec"]["schema_version"],
            "data_status": context["data_status"],
            "request": dict(safe_filters),
            "effective_scope": "staff" if scoped_staff_id else "all",
            "effective_staff_id": scoped_staff_id,
            "model_policy": context.get("model_policy"),
        },
    }
    if render_pdf:
        expected_files = (
            [
                {"format": "markdown", "name": f"{report_uid}.md"},
                {"format": "pdf", "name": f"{report_uid}.pdf"},
            ]
            if safe_filters["format"] == "markdown"
            else [{"format": "pdf", "name": f"{report_uid}.pdf"}]
        )
        report_metadata = render_recovery.with_report_render_protocol(
            report_metadata,
            render_recovery.new_report_render_protocol(report_uid, expected_files),
        )
    initial_metadata_json = _json(report_metadata)
    report_run_id = 0
    try:
        conn.execute(
            """
            INSERT INTO vkpi_report_runs
                (report_uid, report_type, period_start, period_end, scope_type, scope_id, metric_run_id,
                 triggered_by_staff_id, triggered_at, status, summary_text, metadata_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                report_uid,
                report_type,
                context["period_start"],
                context["period_end"],
                "staff" if scoped_staff_id else "all",
                scoped_staff_id,
                metric_run_id,
                actor_id,
                _utcnow(),
                "rendering" if render_pdf else "ready",
                context["summary_text"],
                initial_metadata_json,
            ),
        )
        report_row = conn.execute(
            "SELECT id FROM vkpi_report_runs WHERE report_uid=?",
            (report_uid,),
        ).fetchone()
        if not report_row:
            raise RuntimeError("report run insert was not observable")
        report_run_id = int(report_row["id"])
        conn.commit()
    except Exception:
        _rollback_report_transaction(conn)
        raise
    file_info: dict[str, Any] = {}
    if render_pdf:
        stored_files: list[tuple[str, dict[str, Any]]] = []
        completion_published = False
        completion_publish_started = False
        try:
            if safe_filters["format"] == "markdown":
                markdown_stored = pdf_renderer.store_bytes(
                    _render_markdown_report(context).encode("utf-8"),
                    filename=f"{report_uid}.md",
                )
                stored_files.append(("markdown", markdown_stored))
                pdf_stored = pdf_renderer.render_and_store_pdf(
                    context,
                    filename=f"{report_uid}.pdf",
                )
                stored_files.append(("pdf", pdf_stored))
            else:
                pdf_stored = pdf_renderer.render_and_store_pdf(
                    context,
                    filename=f"{report_uid}.pdf",
                )
                stored_files.append(("pdf", pdf_stored))
            primary_format, primary_stored = stored_files[0]
            file_info = {key: value for key, value in primary_stored.items() if key != "html"}
            file_info["file_format"] = primary_format
            for stored_format, stored in stored_files:
                stored_info = {key: value for key, value in stored.items() if key != "html"}
                stored_download_url = (
                    f"/api/admin/vkpi/reports/files/{report_run_id}/download?format={stored_format}"
                )
                conn.execute(
                    """
                    INSERT INTO vkpi_report_files
                        (report_run_id, file_format, file_path, file_size_bytes, download_url, sha256_hex, created_at)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        report_run_id,
                        stored_format,
                        stored_info["file_path"],
                        stored_info["file_size_bytes"],
                        stored_download_url,
                        stored_info["sha256_hex"],
                        _utcnow(),
                    ),
                )
            # From this point onward publication outcome may be ambiguous (for
            # example, a signal after link+fsync but before the caller receives
            # the return value).  Preserve artifacts on every later failure.
            completion_publish_started = True
            _completion, stored_manifest = render_recovery.publish_report_completion_manifest(
                report_metadata,
                stored_files,
            )
            completion_published = True
            ready_metadata = render_recovery.metadata_with_published_manifest(
                report_metadata,
                stored_manifest,
            )
            ready_result = conn.execute(
                render_recovery.terminal_ready_cas_sql(),
                (_json(ready_metadata), report_run_id, initial_metadata_json),
            )
            if getattr(ready_result, "rowcount", None) != 1:
                raise render_recovery.ReportReadyCasConflict(
                    "report rendering attempt lost terminal ready CAS"
                )
            conn.commit()
            file_info["download_url"] = (
                f"/api/admin/vkpi/reports/files/{report_run_id}/download?format={primary_format}"
            )
        except Exception as exc:
            # PostgreSQL rejects every statement after an error until rollback.
            # Before the final manifest exists, this attempt is provably
            # incomplete and owns its cleanup.  Once the manifest is durable,
            # preserve all artifacts: a commit result may be ambiguous and the
            # read-only reconciler must inspect the exact crash state.
            _rollback_report_transaction(conn)
            if not completion_publish_started:
                _cleanup_generated_report_files(report_uid, stored_files)
                _delete_partial_report_file_rows(conn, report_run_id)
                _persist_failed_report(
                    conn,
                    report_run_id,
                    exc,
                    expected_metadata_json=initial_metadata_json,
                )
            else:
                logger.error(
                    "vkpi report completion publication started; reconciliation required | "
                    "run_id=%s published=%s error=%s",
                    report_run_id,
                    completion_published,
                    type(exc).__name__,
                )
            raise
    return {
        "report_run_id": report_run_id,
        "report_uid": report_uid,
        "report_type": report_type,
        "period_start": context["period_start"],
        "period_end": context["period_end"],
        "data_status": context["data_status"],
        "request": dict(safe_filters),
        "status": "ready",
        "download_url": file_info.get("download_url", ""),
        "downloadUrl": file_info.get("download_url", ""),
        "summary_text": context["summary_text"],
        "context": context,
        "file": file_info,
    }


def _assert_report_access(report: dict[str, Any], staff: dict[str, Any] | None) -> None:
    if scope.can_view_all(staff, domain="export"):
        return
    actor_id = scope.actor_staff_id(staff)
    if not actor_id:
        raise scope.ScopeDenied("report scope denied")
    permitted_ids = {
        int(value)
        for value in (report.get("triggered_by_staff_id"), report.get("scope_id"))
        if _int_or_none(value)
    }
    if actor_id not in permitted_ids:
        raise scope.ScopeDenied("report scope denied")


def _archive_metadata(report: dict[str, Any]) -> dict[str, Any]:
    metadata = _load_json(report.get("metadata_json"))
    if not isinstance(metadata, dict):
        metadata = {}
    archive = metadata.get("_archive")
    return archive if isinstance(archive, dict) else {}


def _truth_invalidation_metadata(report: dict[str, Any]) -> dict[str, Any]:
    invalidated_at = report.get("truth_invalidated_at")
    reason = str(report.get("truth_invalidation_reason") or "").strip()
    if invalidated_at or reason or report.get("truth_invalidation_migration"):
        return {
            "invalidated_at": invalidated_at,
            "reason": reason or "financial_truth_invalidated",
            "migration": report.get("truth_invalidation_migration"),
            "restorable": bool(report.get("truth_restorable")),
        }
    metadata = _load_json(report.get("metadata_json"))
    if not isinstance(metadata, dict):
        return {}
    invalidation = metadata.get("_truth_invalidation")
    return invalidation if isinstance(invalidation, dict) else {}


def _report_history_item(report: dict[str, Any]) -> dict[str, Any]:
    from app.domains.reports.report_history import report_history_item_impl

    return report_history_item_impl(report, globals())


def list_reports(
    limit: int = 50,
    report_type: str = "",
    *,
    staff: dict[str, Any] | None = None,
    staff_id: int | None = None,
    archived: bool = False,
) -> dict[str, Any]:
    ensure_vkpi_reports_schema()
    conn = get_conn()
    scope.assert_legacy_default_organization(staff, conn, feature="report")
    if not scope.actor_staff_id(staff) and not scope.can_view_all(staff, domain="export"):
        raise scope.ScopeDenied("report scope denied")
    where_parts: list[str] = ["status='archived'" if archived else "status<>'archived'"]
    params: list[Any] = []
    if report_type:
        where_parts.append("report_type=?")
        params.append(report_type)
    scoped_staff_id = scope.effective_staff_id(staff, staff_id, domain="export")
    if scoped_staff_id:
        where_parts.append("(triggered_by_staff_id=? OR scope_id=?)")
        params.extend([int(scoped_staff_id), int(scoped_staff_id)])
    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    rows = conn.execute(
        f"""
        SELECT id, report_uid, report_type, period_start, period_end, scope_type,
               scope_id, metric_run_id, triggered_by_staff_id, triggered_at,
               status, error_message, summary_text, metadata_json,
               truth_invalidated_at, truth_invalidation_reason,
               truth_invalidation_migration, truth_restorable
        FROM vkpi_report_runs
        {where}
        ORDER BY triggered_at DESC, id DESC
        LIMIT ?
        """,
        (*params, max(1, min(200, int(limit or 50)))),
    ).fetchall()
    items = [_report_history_item(dict(row)) for row in rows]
    return {"reports": items, "count": len(items), "archived": bool(archived)}


def archive_report(
    report_run_id: int,
    *,
    staff: dict[str, Any] | None,
    reason: str = "user_archived",
) -> dict[str, Any]:
    """Soft-archive one terminal report without deleting its evidence or files."""
    ensure_vkpi_reports_schema()
    conn = get_conn()
    scope.assert_legacy_default_organization(staff, conn, feature="report")
    row = conn.execute("SELECT * FROM vkpi_report_runs WHERE id=?", (int(report_run_id),)).fetchone()
    if not row:
        raise LookupError("report not found")
    report = dict(row)
    _assert_report_access(report, staff)
    if str(report.get("status") or "") == "archived":
        return {"status": "archived", "report_run_id": int(report_run_id), **_archive_metadata(report)}
    previous_status = str(report.get("status") or "")
    if previous_status not in {"ready", "failed"}:
        raise ValueError("active report cannot be archived")
    actor_id = scope.actor_staff_id(staff)
    if not actor_id:
        raise scope.ScopeDenied("report scope denied")
    metadata = _load_json(report.get("metadata_json"))
    if not isinstance(metadata, dict):
        metadata = {}
    archive = {
        "archived_at": _utcnow(),
        "archived_by_staff_id": actor_id,
        "reason": str(reason or "user_archived").strip()[:160] or "user_archived",
        "previous_status": previous_status,
    }
    metadata["_archive"] = archive
    conn.execute(
        "UPDATE vkpi_report_runs SET status='archived', metadata_json=? WHERE id=? AND status=?",
        (_json(metadata), int(report_run_id), previous_status),
    )
    conn.commit()
    return {"status": "archived", "report_run_id": int(report_run_id), **archive}


def restore_report(report_run_id: int, *, staff: dict[str, Any] | None) -> dict[str, Any]:
    """Restore a soft-archived report to its previous terminal state."""
    ensure_vkpi_reports_schema()
    conn = get_conn()
    scope.assert_legacy_default_organization(staff, conn, feature="report")
    row = conn.execute("SELECT * FROM vkpi_report_runs WHERE id=?", (int(report_run_id),)).fetchone()
    if not row:
        raise LookupError("report not found")
    report = dict(row)
    _assert_report_access(report, staff)
    if str(report.get("status") or "") != "archived":
        raise ValueError("report is not archived")
    if _truth_invalidation_metadata(report):
        raise ValueError("truth-invalidated report cannot be restored")
    metadata = _load_json(report.get("metadata_json"))
    if not isinstance(metadata, dict):
        metadata = {}
    archive = metadata.pop("_archive", {})
    previous_status = str(archive.get("previous_status") or "ready") if isinstance(archive, dict) else "ready"
    if previous_status not in {"ready", "failed"}:
        previous_status = "ready"
    history = metadata.get("_archive_history")
    if not isinstance(history, list):
        history = []
    if isinstance(archive, dict) and archive:
        history.append({**archive, "restored_at": _utcnow(), "restored_by_staff_id": scope.actor_staff_id(staff)})
    metadata["_archive_history"] = history[-20:]
    conn.execute(
        "UPDATE vkpi_report_runs SET status=?, metadata_json=? WHERE id=? AND status='archived'",
        (previous_status, _json(metadata), int(report_run_id)),
    )
    conn.commit()
    return {"status": "restored", "report_run_id": int(report_run_id), "report_status": previous_status}


def report_file(report_run_id: int, file_format: str = "pdf", *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_reports_schema()
    clean_format = str(file_format or "").strip().lower()
    if clean_format == "md":
        clean_format = "markdown"
    if clean_format not in {"pdf", "markdown", "html", "csv", "xlsx"}:
        raise LookupError("report file not found")
    conn = get_conn()
    scope.assert_legacy_default_organization(staff, conn, feature="report")
    report = conn.execute("SELECT * FROM vkpi_report_runs WHERE id=?", (int(report_run_id),)).fetchone()
    if not report:
        raise LookupError("report file not found")
    report_data = dict(report)
    _assert_report_access(report_data, staff)
    if str(report_data.get("status") or "") == "archived":
        raise LookupError("report is archived")
    if str(report_data.get("status") or "") != "ready":
        raise LookupError("report is not ready")
    row = conn.execute(
        "SELECT * FROM vkpi_report_files WHERE report_run_id=? AND file_format=? ORDER BY id DESC LIMIT 1",
        (int(report_run_id), clean_format),
    ).fetchone()
    if not row:
        raise LookupError("report file not found")
    return dict(row)


def record_report_download(report_file_id: int, *, staff: dict[str, Any] | None = None) -> None:
    """Count one validated download response initiation, not full delivery."""
    conn = get_conn()
    scope.assert_legacy_default_organization(staff, conn, feature="report")
    actor_id = workflow.staff_id(staff) or None
    downloaded_at = _utcnow()
    result = conn.execute(
        """
        UPDATE vkpi_report_files
        SET download_count=COALESCE(download_count, 0)+1,
            last_downloaded_at=?,
            last_downloaded_by_staff_id=?
        WHERE id=?
          AND EXISTS (
              SELECT 1 FROM vkpi_report_runs r
              WHERE r.id=vkpi_report_files.report_run_id AND r.status='ready'
          )
        """,
        (downloaded_at, actor_id, int(report_file_id)),
    )
    if getattr(result, "rowcount", None) == 0:
        _rollback_report_transaction(conn)
        raise LookupError("report file not ready")
    conn.commit()
