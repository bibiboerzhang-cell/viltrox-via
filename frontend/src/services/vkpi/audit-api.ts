import { apiFetch } from "../http";
import type { VkpiAuditOverview } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

export async function getAuditOverview(
  token: string,
  options: { limit?: number; eventCategory?: string; staffId?: string; days?: number } = {},
): Promise<VkpiAuditOverview> {
  const params = new URLSearchParams({
    limit: String(options.limit || 100),
    days: String(options.days || 7),
  });
  if (options.eventCategory) params.set("event_category", options.eventCategory);
  if (options.staffId) params.set("staff_id", options.staffId);
  const response = await apiFetch<Row>(`/api/admin/vkpi/audit/overview?${params.toString()}`, {}, token);
  const summary = (response.summary || {}) as Row;
  const events = Array.isArray(response.events) ? response.events as Row[] : [];
  return {
    summary: {
      days: numberValue(summary.days || options.days || 7),
      sensitiveAccessCount: numberValue(summary.sensitive_access_count),
      exportCount: numberValue(summary.export_count),
      settingsChangeCount: numberValue(summary.settings_change_count),
      attributionAdjustmentCount: numberValue(summary.attribution_adjustment_count),
      businessEventCount: numberValue(summary.business_event_count),
      eventCount: numberValue(summary.event_count || events.length),
      byCategory: (Array.isArray(summary.by_category) ? summary.by_category as Row[] : []).map((row) => ({
        label: String(row.label || ""),
        count: numberValue(row.count),
      })),
      byAction: (Array.isArray(summary.by_action) ? summary.by_action as Row[] : []).map((row) => ({
        label: String(row.label || ""),
        count: numberValue(row.count),
      })),
    },
    events: events.map((row) => ({
      id: String(row.id || `${row.event_category || "event"}-${row.event_id || ""}`),
      eventId: row.event_id as string | number | undefined,
      eventCategory: String(row.event_category || "business"),
      action: String(row.action || ""),
      staffId: row.staff_id ? String(row.staff_id) : undefined,
      staffName: String(row.staff_name || row.staff_email || row.staff_id || "-"),
      staffEmail: String(row.staff_email || ""),
      targetType: String(row.target_type || ""),
      targetId: String(row.target_id || ""),
      detail: String(row.detail || ""),
      ip: String(row.ip || ""),
      userAgent: String(row.user_agent || ""),
      occurredAt: String(row.occurred_at || ""),
      metadata: (row.metadata && typeof row.metadata === "object" ? row.metadata : {}) as Record<string, unknown>,
    })),
  };
}
