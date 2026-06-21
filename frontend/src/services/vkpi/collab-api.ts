// #24 协作设置服务:ShareModal「共同目标 + 提醒规则」· per-resource(project/event)· vkpi_collab_settings。
import { apiFetch, jsonBody } from "../http";

const BASE = "/api/admin/vkpi/collab-settings";

export function getCollabSettings(token: string, kind: string, targetId: string) {
  const p = new URLSearchParams({ kind, target_id: String(targetId) });
  return apiFetch<{ shared_goal: string; reminder_rule: string }>(`${BASE}?${p.toString()}`, {}, token);
}

export function saveCollabSettings(token: string, kind: string, targetId: string, sharedGoal: string, reminderRule: string) {
  return apiFetch<Record<string, any>>(
    BASE,
    { method: "PATCH", body: jsonBody({ kind, target_id: String(targetId), shared_goal: sharedGoal, reminder_rule: reminderRule }) },
    token,
  );
}
