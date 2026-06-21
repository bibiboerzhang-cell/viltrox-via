// #18 发布审批服务:PublishPreviewModal 三按钮 → 后端 vkpi_publish_approvals(按 source_table+source_id 锁定真来源)。
import { apiFetch, jsonBody } from "../http";

const BASE = "/api/admin/vkpi/publish";

function payload(sourceTable: string, sourceId: string, extra: Record<string, any> = {}) {
  return { source_table: sourceTable, source_id: String(sourceId), ...extra };
}

export function approvePublish(token: string, sourceTable: string, sourceId: string, meta: Record<string, any> = {}) {
  return apiFetch<Record<string, any>>(`${BASE}/approve`, { method: "POST", body: jsonBody(payload(sourceTable, sourceId, meta)) }, token);
}

export function reschedulePublish(token: string, sourceTable: string, sourceId: string, scheduledPublishAt: string, meta: Record<string, any> = {}) {
  return apiFetch<Record<string, any>>(`${BASE}/reschedule`, { method: "POST", body: jsonBody(payload(sourceTable, sourceId, { ...meta, scheduled_publish_at: scheduledPublishAt })) }, token);
}

export function remindPublishKol(token: string, sourceTable: string, sourceId: string, meta: Record<string, any> = {}) {
  return apiFetch<Record<string, any>>(`${BASE}/remind`, { method: "POST", body: jsonBody(payload(sourceTable, sourceId, meta)) }, token);
}
