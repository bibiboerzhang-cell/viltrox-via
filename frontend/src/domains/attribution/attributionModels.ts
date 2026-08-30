import type {
  VkpiAttributionRow,
  VkpiCostRow,
  VkpiLinkRow,
} from '../../components/vkpi/vkpiTypes';
import {
  centsToUsd,
  numberValue,
} from '../dashboard/dashboardFormat';
import { platformLabel } from '../dashboard/dashboardPlatform';

type Row = Record<string, unknown>;

export function buildLinks(rows: Row[]): VkpiLinkRow[] {
  return rows.map((row) => ({
    id: String(row.id || row.link_uid || row.slug || ''),
    slug: String(row.slug || ''),
    destination: String(row.destination_url || ''),
    platform: platformLabel(row.platform),
    projectId: row.project_id ? String(row.project_id) : undefined,
    projectName: String(row.project_name || ''),
    kolName: String(row.kol_name || ''),
    ownerName: String(row.staff_name || row.staff_id || ''),
    clicks: numberValue(row.click_count),
    validClicks: numberValue(row.valid_click_count || row.click_count),
    botClicks: numberValue(row.bot_click_count),
    orders: numberValue(row.order_count || row.orders),
    gmv: centsToUsd(row.revenue_cents || row.gmv_cents),
    status: String(row.status || 'unknown'),
    healthStatus: String(row.health_status || 'unknown'),
    updatedAt: String(row.updated_at || row.created_at || '-'),
  })).filter((row) => row.id || row.slug);
}

export function buildAttributions(rows: Row[]): VkpiAttributionRow[] {
  return rows.map((row) => ({
    id: String(row.id || row.source_ref || ''),
    source: String(row.source_platform || row.source || 'manual'),
    sourceRef: String(row.source_ref || ''),
    projectId: row.project_id ? String(row.project_id) : undefined,
    linkId: row.link_id ? String(row.link_id) : undefined,
    kolId: row.kol_id ? String(row.kol_id) : undefined,
    staffId: row.staff_id ? String(row.staff_id) : undefined,
    productSku: String(row.product_sku || ''),
    orderId: row.order_id ? String(row.order_id) : undefined,
    revenue: centsToUsd(row.revenue_cents),
    commission: centsToUsd(row.commission_cents),
    confidence: String(row.confidence || ''),
    occurredAt: String(row.occurred_at || row.imported_at || row.created_at || '-'),
  })).filter((row) => row.id || row.sourceRef);
}

export function buildCosts(rows: Row[]): VkpiCostRow[] {
  return rows.map((row) => ({
    id: String(row.id || `${row.project_id || ''}-${row.incurred_at || ''}`),
    projectId: row.project_id ? String(row.project_id) : undefined,
    kolId: row.kol_id ? String(row.kol_id) : undefined,
    staffId: row.staff_id ? String(row.staff_id) : undefined,
    costType: String(row.cost_type || 'other'),
    amount: centsToUsd(row.amount_cents),
    currency: String(row.currency || 'USD'),
    status: String(row.status || 'actual'),
    incurredAt: String(row.incurred_at || row.created_at || '-'),
    sourceRef: String(row.source_ref || ''),
    note: String(row.note || ''),
    projectName: String(row.project_name || ''),
    productSku: String(row.product_sku || ''),
    kolName: String(row.kol_name || ''),
    staffName: String(row.staff_name || row.staff_id || ''),
    approvedByStaffId: row.approved_by_staff_id ? String(row.approved_by_staff_id) : undefined,
    approvedAt: String(row.approved_at || ''),
    voidedByStaffId: row.voided_by_staff_id ? String(row.voided_by_staff_id) : undefined,
    voidedAt: String(row.voided_at || ''),
    updatedAt: String(row.updated_at || row.created_at || ''),
  })).filter((row) => row.id);
}
