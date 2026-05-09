"""Read-only issue collectors for V-KPI data quality."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import scope
from app.services.vkpi.data_quality_common import (
    _active_project_filter_for_quality,
    _append_issue,
    _int,
    _issue_key,
    _project_clause,
    _safe_rows,
    _staff_clause,
    _utcnow,
    ensure_data_quality_schema,
)
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_lineage import ensure_vkpi_lineage_schema
from app.services.vkpi.schema_reconciliation import ensure_vkpi_reconciliation_schema


def _load_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _missing_token(value: Any) -> bool:
    token = str(value or "").strip().lower()
    return token in {"", "-", "unknown", "none", "null", "n/a"}


def _parse_date(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for candidate in (raw, raw[:10]):
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    return None


def list_issues(*, limit: int = 100, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    ensure_vkpi_lineage_schema()
    ensure_vkpi_reconciliation_schema()
    ensure_data_quality_schema()
    conn = get_conn()
    max_items = max(1, min(500, int(limit or 100)))
    issues: list[dict[str, Any]] = []

    # 1. Pending reconciliation queue rows: sales exist but are not yet mapped to project/KOL/staff.
    queue_staff_sql, queue_staff_params = _staff_clause("rq.assigned_to_staff_id", staff)
    rows = _safe_rows(
        conn,
        f"""
        SELECT rq.id, rq.source_platform, rq.source_ref, rq.order_id, rq.revenue_cents,
               rq.currency, rq.occurred_at, rq.product_sku, rq.status, rq.priority,
               rq.assigned_to_staff_id, rq.created_at
        FROM vkpi_reconciliation_queue rq
        WHERE COALESCE(rq.status, 'pending') IN ('pending','needs_review','assigned')
        {queue_staff_sql}
        ORDER BY rq.priority DESC, rq.created_at DESC, rq.id DESC
        LIMIT ?
        """,
        (*queue_staff_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="pending_reconciliation",
            severity="high",
            title="未匹配订单等待人工归因",
            entity_type="reconciliation_queue",
            entity_id=item.get("id"),
            staff_id=item.get("assigned_to_staff_id"),
            detail=f"{item.get('source_platform')} · {item.get('source_ref')}",
            evidence=item,
        )

    # 2. Sales attribution rows that cannot be tied to the operating chain.
    staff_sql, staff_params = _staff_clause("sa.staff_id", staff)
    rows = _safe_rows(
        conn,
        f"""
        SELECT id, source_platform, source_ref, project_id, kol_id, staff_id, revenue_cents, confidence, occurred_at
        FROM vkpi_sales_attributions sa
        WHERE (project_id IS NULL OR kol_id IS NULL OR staff_id IS NULL)
        {staff_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (*staff_params, max_items),
    )
    for row in rows:
        item = dict(row)
        _append_issue(
            issues,
            issue_type="unmatched_attribution",
            severity="high",
            title="销售归因缺少项目 / 红人 / 员工绑定",
            entity_type="sales_attribution",
            entity_id=item.get("id"),
            staff_id=item.get("staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("kol_id"),
            detail=f"{item.get('source_platform')} · {item.get('source_ref')}",
            evidence=item,
        )

    # 3. Shopify rows without a Shopify order snapshot bridge.
    rows = _safe_rows(
        conn,
        f"""
        SELECT id, source_platform, source_ref, project_id, kol_id, staff_id, revenue_cents, occurred_at
        FROM vkpi_sales_attributions sa
        WHERE LOWER(source_platform)='shopify'
          AND shopify_order_snapshot_id IS NULL
        {staff_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (*staff_params, max_items),
    )
    for row in rows:
        item = dict(row)
        _append_issue(
            issues,
            issue_type="missing_shopify_snapshot",
            severity="medium",
            title="Shopify 销售没有订单快照",
            entity_type="sales_attribution",
            entity_id=item.get("id"),
            staff_id=item.get("staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("kol_id"),
            detail=str(item.get("source_ref") or ""),
            evidence=item,
        )

    # 4. Same Shopify order snapshot credited more than once as positive sales.
    rows = _safe_rows(
        conn,
        f"""
        SELECT sa.shopify_order_snapshot_id AS snapshot_id,
               COUNT(*) AS row_count,
               COALESCE(SUM(sa.revenue_cents), 0) AS revenue_cents,
               GROUP_CONCAT(sa.id) AS attribution_ids,
               GROUP_CONCAT(sa.source_ref) AS source_refs,
               MIN(sa.project_id) AS project_id,
               MIN(sa.kol_id) AS kol_id,
               MIN(sa.staff_id) AS staff_id
        FROM vkpi_sales_attributions sa
        WHERE sa.shopify_order_snapshot_id IS NOT NULL
          AND sa.revenue_cents > 0
          AND COALESCE(sa.confidence, '') != 'refund'
          AND {_active_project_filter_for_quality('sa')}
        {staff_sql}
        GROUP BY sa.shopify_order_snapshot_id
        HAVING COUNT(*) > 1
        ORDER BY row_count DESC
        LIMIT ?
        """,
        (*staff_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="duplicate_shopify_order_credit",
            severity="high",
            title="同一 Shopify 订单被多次计入销售",
            entity_type="shopify_order_snapshot",
            entity_id=item.get("snapshot_id"),
            staff_id=item.get("staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("kol_id"),
            detail=f"{item.get('row_count')} 条归因 · {item.get('source_refs')}",
            evidence=item,
        )

    # 5. Refunded Shopify orders without a negative refund attribution.
    rows = _safe_rows(
        conn,
        f"""
        SELECT os.id, os.shopify_order_id, os.order_name, os.total_cents, os.refund_status,
               os.updated_at, sa.project_id, sa.kol_id, sa.staff_id
        FROM vkpi_shopify_order_snapshots os
        INNER JOIN vkpi_sales_attributions sa ON sa.shopify_order_snapshot_id = os.id
        WHERE LOWER(COALESCE(os.refund_status, '')) IN ('refunded','partially_refunded','partial_refund','cancelled')
          AND NOT EXISTS (
            SELECT 1 FROM vkpi_sales_attributions refund
            WHERE refund.shopify_order_snapshot_id = os.id
              AND refund.revenue_cents < 0
          )
          AND {_active_project_filter_for_quality('sa')}
        {staff_sql}
        GROUP BY os.id, os.shopify_order_id, os.order_name, os.total_cents, os.refund_status, os.updated_at, sa.project_id, sa.kol_id, sa.staff_id
        ORDER BY os.updated_at DESC, os.id DESC
        LIMIT ?
        """,
        (*staff_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="refund_not_reflected",
            severity="high",
            title="Shopify 订单已退款但销售未扣减",
            entity_type="shopify_order_snapshot",
            entity_id=item.get("id"),
            staff_id=item.get("staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("kol_id"),
            detail=f"{item.get('order_name') or item.get('shopify_order_id')} · {item.get('refund_status')}",
            evidence=item,
        )

    # 6. Amazon attribution rows missing report-level evidence needed for reconciliation.
    amazon_staff_sql, amazon_staff_params = _staff_clause("sa.staff_id", staff)
    rows = _safe_rows(
        conn,
        f"""
        SELECT id, source_platform, source_ref, project_id, link_id, kol_id, staff_id,
               amazon_campaign_id, product_sku, revenue_cents, commission_cents,
               currency, confidence, occurred_at, imported_at, evidence_json
        FROM vkpi_sales_attributions sa
        WHERE LOWER(source_platform)='amazon'
          AND COALESCE(confidence, '') NOT IN ('excluded','void','reversed')
          AND {_active_project_filter_for_quality('sa')}
        {amazon_staff_sql}
        ORDER BY imported_at DESC, id DESC
        LIMIT ?
        """,
        (*amazon_staff_params, max_items),
    )
    stale_cutoff = datetime.utcnow() - timedelta(days=8)
    for row in rows:
        item = dict(row)
        evidence = _load_json(item.get("evidence_json"))
        normalized = evidence.get("normalized") if isinstance(evidence.get("normalized"), dict) else {}
        asin = normalized.get("asin") or evidence.get("asin")
        campaign = normalized.get("campaign") or item.get("amazon_campaign_id")
        report_date = normalized.get("report_date") or item.get("occurred_at") or item.get("imported_at")
        if _missing_token(asin):
            _append_issue(
                issues,
                issue_type="amazon_missing_asin",
                severity="medium",
                title="Amazon 归因缺少 ASIN，无法稳定绑定产品",
                entity_type="sales_attribution",
                entity_id=item.get("id"),
                staff_id=item.get("staff_id"),
                project_id=item.get("project_id"),
                kol_id=item.get("kol_id"),
                detail=str(item.get("source_ref") or ""),
                evidence={**item, "normalized": normalized},
            )
        if _missing_token(campaign):
            _append_issue(
                issues,
                issue_type="amazon_missing_campaign",
                severity="medium",
                title="Amazon 归因缺少 campaign / tracking ref",
                entity_type="sales_attribution",
                entity_id=item.get("id"),
                staff_id=item.get("staff_id"),
                project_id=item.get("project_id"),
                kol_id=item.get("kol_id"),
                detail=str(item.get("source_ref") or ""),
                evidence={**item, "normalized": normalized},
            )
        parsed_report_date = _parse_date(report_date)
        if parsed_report_date and parsed_report_date < stale_cutoff:
            _append_issue(
                issues,
                issue_type="stale_amazon_report",
                severity="low",
                title="Amazon Attribution 报表超过 8 天未刷新",
                entity_type="sales_attribution",
                entity_id=item.get("id"),
                staff_id=item.get("staff_id"),
                project_id=item.get("project_id"),
                kol_id=item.get("kol_id"),
                detail=f"{item.get('source_ref')} · {report_date}",
                evidence={**item, "normalized": normalized, "report_date": report_date},
            )

    # 7. Broken / blocked short links.
    staff_sql, staff_params = _staff_clause("l.staff_id", staff)
    rows = _safe_rows(
        conn,
        f"""
        SELECT id, slug, destination_url, project_id, kol_id, staff_id, status, allowlist_status, health_status, updated_at
        FROM vkpi_links l
        WHERE COALESCE(status,'') NOT IN ('archived','deleted')
          AND (
            COALESCE(destination_url,'') = '' OR
            COALESCE(allowlist_status,'allowed') != 'allowed' OR
            COALESCE(health_status,'unknown') IN ('broken','blocked_destination','error','failed')
          )
        {staff_sql}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (*staff_params, max_items),
    )
    for row in rows:
        item = dict(row)
        _append_issue(
            issues,
            issue_type="broken_link",
            severity="high",
            title="短链目的地异常或未通过 allowlist",
            entity_type="link",
            entity_id=item.get("id"),
            staff_id=item.get("staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("kol_id"),
            detail=str(item.get("slug") or item.get("destination_url") or ""),
            evidence=item,
        )

    # 7. Active sales links missing explicit UTM configuration.
    rows = _safe_rows(
        conn,
        f"""
        SELECT id, slug, destination_url, project_id, kol_id, staff_id, link_type,
               utm_source, utm_medium, utm_campaign, utm_content, updated_at
        FROM vkpi_links l
        WHERE COALESCE(status,'') NOT IN ('archived','deleted','paused')
          AND LOWER(COALESCE(link_type, '')) IN ('shopify','amazon','ecommerce','generic')
          AND (
            TRIM(COALESCE(utm_source,'')) = '' OR
            TRIM(COALESCE(utm_medium,'')) = '' OR
            TRIM(COALESCE(utm_campaign,'')) = '' OR
            TRIM(COALESCE(utm_content,'')) = ''
          )
        {staff_sql}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (*staff_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="missing_utm",
            severity="medium",
            title="短链缺少 UTM 字段，归因证据不完整",
            entity_type="link",
            entity_id=item.get("id"),
            staff_id=item.get("staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("kol_id"),
            detail=str(item.get("slug") or item.get("destination_url") or ""),
            evidence=item,
        )

    # 8. Published projects without content URL / evidence ref.
    project_sql, project_params = _project_clause("p", staff)
    rows = _safe_rows(
        conn,
        f"""
        SELECT p.id, p.project_name, p.kol_id, p.assigned_staff_id, p.stage, p.updated_at
        FROM vkpi_projects p
        WHERE p.stage_status != 'deleted'
          AND p.stage IN ('published','measured','closed')
          AND NOT EXISTS (
            SELECT 1 FROM vkpi_project_stage_events e
            WHERE e.project_id = p.id
              AND e.to_stage IN ('published','posted')
              AND COALESCE(e.source_ref_id,'') != ''
          )
        {project_sql}
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT ?
        """,
        (*project_params, max_items),
    )
    for row in rows:
        item = dict(row)
        _append_issue(
            issues,
            issue_type="published_without_content",
            severity="medium",
            title="项目已发布但缺少内容链接证据",
            entity_type="project",
            entity_id=item.get("id"),
            staff_id=item.get("assigned_staff_id"),
            project_id=item.get("id"),
            kol_id=item.get("kol_id"),
            detail=str(item.get("project_name") or ""),
            evidence=item,
        )

    # 9. Shipped projects without any actual cost row.
    rows = _safe_rows(
        conn,
        f"""
        SELECT p.id, p.project_name, p.kol_id, p.assigned_staff_id, p.stage, p.tracking_number, p.updated_at
        FROM vkpi_projects p
        WHERE p.stage_status != 'deleted'
          AND p.stage IN ('shipped','received','published','measured','closed')
          AND NOT EXISTS (
            SELECT 1 FROM vkpi_cost_ledger c
            WHERE c.project_id = p.id AND COALESCE(c.status,'actual') != 'void'
          )
        {project_sql}
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT ?
        """,
        (*project_params, max_items),
    )
    for row in rows:
        item = dict(row)
        _append_issue(
            issues,
            issue_type="shipped_without_cost",
            severity="medium",
            title="项目已发货但没有成本记录",
            entity_type="project",
            entity_id=item.get("id"),
            staff_id=item.get("assigned_staff_id"),
            project_id=item.get("id"),
            kol_id=item.get("kol_id"),
            detail=str(item.get("project_name") or ""),
            evidence=item,
        )

    # 10. Manual attribution without evidence.
    staff_sql, staff_params = _staff_clause("sa.staff_id", staff)
    rows = _safe_rows(
        conn,
        f"""
        SELECT id, source_platform, source_ref, project_id, kol_id, staff_id, revenue_cents, evidence_json, created_at
        FROM vkpi_sales_attributions sa
        WHERE LOWER(source_platform) IN ('manual','custom')
          AND TRIM(COALESCE(evidence_json,'')) IN ('','{{}}','null')
        {staff_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (*staff_params, max_items),
    )
    for row in rows:
        item = dict(row)
        _append_issue(
            issues,
            issue_type="manual_attribution_without_evidence",
            severity="high",
            title="手动销售归因缺少证据",
            entity_type="sales_attribution",
            entity_id=item.get("id"),
            staff_id=item.get("staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("kol_id"),
            detail=str(item.get("source_ref") or ""),
            evidence=item,
        )

    # 11. Active sales rows linked to deleted projects.
    rows = _safe_rows(
        conn,
        f"""
        SELECT sa.id, sa.source_platform, sa.source_ref, sa.project_id, sa.kol_id, sa.staff_id, p.project_name
        FROM vkpi_sales_attributions sa
        INNER JOIN vkpi_projects p ON p.id = sa.project_id
        WHERE p.stage_status='deleted'
        {staff_sql}
        ORDER BY sa.created_at DESC, sa.id DESC
        LIMIT ?
        """,
        (*staff_params, max_items),
    )
    for row in rows:
        item = dict(row)
        _append_issue(
            issues,
            issue_type="deleted_project_sales",
            severity="high",
            title="已删除项目仍有销售归因",
            entity_type="sales_attribution",
            entity_id=item.get("id"),
            staff_id=item.get("staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("kol_id"),
            detail=str(item.get("project_name") or item.get("source_ref") or ""),
            evidence=item,
        )

    # 12. Active cost rows linked to deleted projects.
    cost_staff_sql, cost_staff_params = _staff_clause("c.staff_id", staff)
    rows = _safe_rows(
        conn,
        f"""
        SELECT c.id, c.project_id, c.kol_id, c.staff_id, c.cost_type, c.amount_cents, c.status,
               c.source_ref, p.project_name
        FROM vkpi_cost_ledger c
        INNER JOIN vkpi_projects p ON p.id = c.project_id
        WHERE p.stage_status='deleted'
          AND COALESCE(c.status,'actual') != 'void'
        {cost_staff_sql}
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT ?
        """,
        (*cost_staff_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="deleted_project_cost",
            severity="medium",
            title="已删除项目仍有有效成本记录",
            entity_type="cost",
            entity_id=item.get("id"),
            staff_id=item.get("staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("kol_id"),
            detail=str(item.get("project_name") or item.get("source_ref") or ""),
            evidence=item,
        )

    # 13. Duplicate KOL candidates caused by same platform URL/handle/email.
    kol_where = ""
    kol_params: list[Any] = []
    if not scope.can_view_all(staff):
        actor = scope.actor_staff_id(staff)
        if actor:
            kol_where = "WHERE (assigned_staff_id=? OR created_by_staff_id=?)"
            kol_params = [actor, actor]
        else:
            kol_where = "WHERE 1=0"
    kol_rows = _safe_rows(
        conn,
        f"""
        SELECT id, platform, channel_name, channel_url, contact_email, assigned_staff_id, created_by_staff_id, updated_at
        FROM kols
        {kol_where}
        ORDER BY updated_at DESC, id DESC
        LIMIT 2000
        """,
        tuple(kol_params),
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in kol_rows:
        platform = str(item.get("platform") or "").strip().lower()
        url = str(item.get("channel_url") or "").strip().lower().rstrip("/")
        name = str(item.get("channel_name") or "").strip().lower().lstrip("@")
        email = str(item.get("contact_email") or "").strip().lower()
        keys = []
        if url:
            keys.append(f"url:{platform}:{url}")
        if platform and name:
            keys.append(f"handle:{platform}:{name}")
        if email:
            keys.append(f"email:{email}")
        for key in keys:
            groups.setdefault(key, []).append(item)
    seen_duplicate_sets: set[str] = set()
    for key, rows_for_key in groups.items():
        ids = sorted({_int(item.get("id")) for item in rows_for_key if _int(item.get("id"))})
        if len(ids) < 2:
            continue
        digest = _issue_key(key, ",".join(map(str, ids)))
        if digest in seen_duplicate_sets:
            continue
        seen_duplicate_sets.add(digest)
        first = rows_for_key[0]
        _append_issue(
            issues,
            issue_type="duplicate_kol_candidate",
            severity="medium",
            title="疑似重复红人档案",
            entity_type="kol",
            entity_id=digest,
            staff_id=first.get("assigned_staff_id") or first.get("created_by_staff_id"),
            kol_id=ids[0],
            detail=f"{key.split(':', 1)[0]} · KOL IDs {', '.join(map(str, ids[:8]))}",
            evidence={"dedup_key": key, "kol_ids": ids, "rows": rows_for_key[:8]},
        )
        if len(seen_duplicate_sets) >= max_items:
            break

    # 14. Shopify order snapshots exist but no Shopify sync/run has refreshed recently.
    if scope.can_view_all(staff):
        latest_snapshot = conn.execute(
            "SELECT id, shopify_order_id, updated_at FROM vkpi_shopify_order_snapshots ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if latest_snapshot:
            snapshot = dict(latest_snapshot)
            stale_before = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            if str(snapshot.get("updated_at") or "") < stale_before:
                _append_issue(
                    issues,
                    issue_type="stale_shopify_snapshot",
                    severity="low",
                    title="Shopify 订单快照超过 24 小时未刷新",
                    entity_type="shopify_order_snapshot",
                    entity_id=snapshot.get("id"),
                    detail=str(snapshot.get("shopify_order_id") or ""),
                    evidence=snapshot,
                )

    # 15. Stale dashboard metric snapshot.
    stale_before = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        """
        SELECT id, run_uid, generated_at, scope_type, scope_id
        FROM vkpi_metric_runs
        WHERE trigger_source='dashboard' AND status='ready'
        ORDER BY generated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        _append_issue(
            issues,
            issue_type="missing_metric_snapshot",
            severity="medium",
            title="还没有 Dashboard 指标快照",
            entity_type="metric_run",
            detail="打开管理主控或手动运行 lineage 后会生成。",
        )
    elif str(dict(row).get("generated_at") or "") < stale_before:
        item = dict(row)
        _append_issue(
            issues,
            issue_type="stale_metric_snapshot",
            severity="medium",
            title="Dashboard 指标快照超过 24 小时未刷新",
            entity_type="metric_run",
            entity_id=item.get("id"),
            detail=str(item.get("run_uid") or ""),
            evidence=item,
        )

    # 16. Non-zero dashboard metric values must have source rows.
    metric_scope_sql = ""
    metric_scope_params: list[Any] = []
    if not scope.can_view_all(staff):
        actor = scope.actor_staff_id(staff)
        if actor:
            metric_scope_sql = " AND (mr.scope_type='staff' AND mr.scope_id=?) "
            metric_scope_params = [actor]
        else:
            metric_scope_sql = " AND 1=0 "
    rows = _safe_rows(
        conn,
        f"""
        SELECT mv.id AS metric_value_id,
               mv.metric_key,
               mv.value_numeric,
               mv.unit,
               mv.currency,
               mv.source_count,
               mv.created_at,
               mr.id AS run_id,
               mr.run_uid,
               mr.scope_type,
               mr.scope_id,
               mr.generated_at,
               COALESCE(src.actual_source_count, 0) AS actual_source_count
        FROM vkpi_metric_values mv
        INNER JOIN vkpi_metric_runs mr ON mr.id = mv.run_id
        LEFT JOIN (
            SELECT metric_value_id, COUNT(*) AS actual_source_count
            FROM vkpi_metric_sources
            GROUP BY metric_value_id
        ) src ON src.metric_value_id = mv.id
        WHERE mr.status='ready'
          AND COALESCE(mv.value_numeric, 0) != 0
          AND COALESCE(mv.source_count, 0) = 0
          AND COALESCE(src.actual_source_count, 0) = 0
        {metric_scope_sql}
        ORDER BY mv.created_at DESC, mv.id DESC
        LIMIT ?
        """,
        (*metric_scope_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="metric_without_sources",
            severity="high",
            title="Dashboard 指标有数值但没有来源行",
            entity_type="metric_value",
            entity_id=item.get("metric_value_id"),
            detail=f"{item.get('metric_key')} · {item.get('run_uid')}",
            evidence=item,
        )

    # 17. KPI ledger rows must preserve enough evidence to explain staff credit.
    kpi_staff_sql, kpi_staff_params = _staff_clause("kl.staff_id", staff)
    rows = _safe_rows(
        conn,
        f"""
        SELECT kl.id,
               kl.ledger_date,
               kl.staff_id,
               kl.kol_id,
               kl.project_id,
               kl.metric_key,
               kl.metric_value,
               kl.source_type,
               kl.source_ref,
               kl.confidence,
               kl.metadata_json,
               kl.created_at
        FROM vkpi_kpi_ledger kl
        WHERE (
            TRIM(COALESCE(kl.source_type,'')) = ''
            OR TRIM(COALESCE(kl.source_ref,'')) = ''
            OR (
                kl.metric_key IN ('workload_score','kpi_credit','roi','net_roi','net_contribution_cents')
                AND (
                    TRIM(COALESCE(kl.metadata_json,'')) IN ('','{{}}','null')
                    OR COALESCE(kl.metadata_json,'') NOT LIKE ?
                )
            )
        )
        {kpi_staff_sql}
        ORDER BY kl.created_at DESC, kl.id DESC
        LIMIT ?
        """,
        ("%formula%", *kpi_staff_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="kpi_ledger_without_evidence",
            severity="medium",
            title="员工 KPI 记录缺少来源或公式证据",
            entity_type="kpi_ledger",
            entity_id=item.get("id"),
            staff_id=item.get("staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("kol_id"),
            detail=f"{item.get('metric_key')} · {item.get('source_ref') or 'missing source'}",
            evidence=item,
        )

    # 18. Ready weekly reports must be tied to metric sources for appendix/downstream PDF evidence.
    report_scope_sql = ""
    report_scope_params: list[Any] = []
    if not scope.can_view_all(staff):
        actor = scope.actor_staff_id(staff)
        if actor:
            report_scope_sql = " AND r.scope_type='staff' AND r.scope_id=? "
            report_scope_params = [actor]
        else:
            report_scope_sql = " AND 1=0 "
    rows = _safe_rows(
        conn,
        f"""
        SELECT r.id,
               r.report_uid,
               r.report_type,
               r.period_start,
               r.period_end,
               r.scope_type,
               r.scope_id,
               r.metric_run_id,
               r.triggered_by_staff_id,
               r.triggered_at,
               r.status,
               r.summary_text,
               COALESCE(src.source_rows, 0) AS source_rows
        FROM vkpi_report_runs r
        LEFT JOIN (
            SELECT mv.run_id, COUNT(ms.id) AS source_rows
            FROM vkpi_metric_values mv
            LEFT JOIN vkpi_metric_sources ms ON ms.metric_value_id = mv.id
            GROUP BY mv.run_id
        ) src ON src.run_id = r.metric_run_id
        WHERE r.report_type='weekly'
          AND r.status='ready'
          AND (
            r.metric_run_id IS NULL
            OR COALESCE(src.source_rows, 0) = 0
          )
        {report_scope_sql}
        ORDER BY r.triggered_at DESC, r.id DESC
        LIMIT ?
        """,
        (*report_scope_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="weekly_report_without_source_appendix",
            severity="medium",
            title="周报缺少指标来源附录",
            entity_type="report_run",
            entity_id=item.get("id"),
            staff_id=item.get("triggered_by_staff_id"),
            detail=str(item.get("report_uid") or ""),
            evidence=item,
        )

    # 19. KOLs already in active workflow or attribution need real contact data.
    kol_contact_where = ""
    kol_contact_params: list[Any] = []
    if not scope.can_view_all(staff):
        actor = scope.actor_staff_id(staff)
        if actor:
            kol_contact_where = " AND (k.assigned_staff_id=? OR k.created_by_staff_id=? OR p.assigned_staff_id=? OR p.created_by_staff_id=? OR c.staff_id=?) "
            kol_contact_params = [actor, actor, actor, actor, actor]
        else:
            kol_contact_where = " AND 1=0 "
    rows = _safe_rows(
        conn,
        f"""
        SELECT k.id,
               k.platform,
               k.channel_name,
               k.channel_url,
               k.contact_email,
               k.contact_phone,
               k.contact_links_json,
               k.assigned_staff_id,
               p.id AS project_id,
               p.project_name,
               p.assigned_staff_id AS project_staff_id,
               c.staff_id AS claim_staff_id,
               COUNT(DISTINCT sa.id) AS attribution_count,
               COALESCE(SUM(sa.revenue_cents), 0) AS revenue_cents
        FROM kols k
        LEFT JOIN vkpi_projects p ON p.kol_id = k.id AND COALESCE(p.stage_status,'') != 'deleted'
        LEFT JOIN vkpi_kol_claims c ON c.kol_id = k.id AND c.status='active'
        LEFT JOIN vkpi_sales_attributions sa ON sa.kol_id = k.id AND COALESCE(sa.revenue_cents, 0) > 0
        WHERE (
            p.id IS NOT NULL
            OR c.id IS NOT NULL
            OR sa.id IS NOT NULL
        )
          AND TRIM(COALESCE(k.contact_email,'')) = ''
          AND TRIM(COALESCE(k.contact_phone,'')) = ''
          AND TRIM(COALESCE(k.contact_links_json,'')) IN ('','[]','{{}}','null')
        {kol_contact_where}
        GROUP BY k.id, k.platform, k.channel_name, k.channel_url, k.contact_email,
                 k.contact_phone, k.contact_links_json, k.assigned_staff_id,
                 p.id, p.project_name, p.assigned_staff_id, c.staff_id
        ORDER BY COALESCE(SUM(sa.revenue_cents), 0) DESC, k.id DESC
        LIMIT ?
        """,
        (*kol_contact_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="kol_missing_contact",
            severity="medium",
            title="红人已进入业务链路但没有联系方式",
            entity_type="kol",
            entity_id=item.get("id"),
            staff_id=item.get("project_staff_id") or item.get("claim_staff_id") or item.get("assigned_staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("id"),
            detail=f"{item.get('platform')} · {item.get('channel_name') or item.get('channel_url')}",
            evidence=item,
        )

    action_rows = conn.execute(
        """
        SELECT issue_id, action
        FROM vkpi_data_quality_actions
        ORDER BY created_at DESC, id DESC
        """
    ).fetchall()
    closed: set[str] = set()
    latest_actions: dict[str, str] = {}
    for row in action_rows:
        item = dict(row)
        issue_id = str(item.get("issue_id") or "")
        action = str(item.get("action") or "")
        if issue_id and issue_id not in latest_actions:
            latest_actions[issue_id] = action
        if issue_id and action in {"resolve", "ignore"} and latest_actions.get(issue_id) == action:
            closed.add(issue_id)
    issues = [item for item in issues if str(item.get("id") or "") not in closed]

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    issues.sort(key=lambda item: (severity_order.get(str(item.get("severity")), 9), str(item.get("created_at") or "")), reverse=False)
    limited = issues[:max_items]
    return {
        "status": "ok",
        "generated_at": _utcnow(),
        "count": len(limited),
        "total_count": len(issues),
        "issues": limited,
        "summary": {
            "critical": sum(1 for item in issues if item.get("severity") == "critical"),
            "high": sum(1 for item in issues if item.get("severity") == "high"),
            "medium": sum(1 for item in issues if item.get("severity") == "medium"),
            "low": sum(1 for item in issues if item.get("severity") == "low"),
            "info": sum(1 for item in issues if item.get("severity") == "info"),
        },
    }
