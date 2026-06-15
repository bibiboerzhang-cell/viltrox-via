import { apiFetch, jsonBody } from "../http";
import type { VkpiMemberRole, VkpiProjectMember } from "./projectMembers-api";

// 活动分享成员(后端 share-members CRUD 已就绪;仅 owner/admin 可增删,非授权拿 403)。
// 注:与 events-api.ts 里旧的 add/removeEventMember(/members,body user_id)不同——
// 这里走的是 /share-members(body staff_id + role),才是带角色的分享口径。
//   GET    /api/admin/vkpi/events/{event_id}/share-members
//   POST   /api/admin/vkpi/events/{event_id}/share-members   body {staff_id, role}
//   DELETE /api/admin/vkpi/events/{event_id}/share-members/{staff_id}

export type VkpiEventMember = VkpiProjectMember;

export interface VkpiEventMembersResponse {
  event_id?: number | string;
  members?: VkpiEventMember[];
  items?: VkpiEventMember[];
}

export async function listEventMembers(token: string, eventId: string) {
  return apiFetch<VkpiEventMembersResponse | VkpiEventMember[]>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/share-members?_ts=${Date.now()}`,
    { cache: "no-store" },
    token,
  );
}

export async function addEventMember(
  token: string,
  eventId: string,
  staffId: string | number,
  role: VkpiMemberRole = "viewer",
) {
  return apiFetch<VkpiEventMember | { ok?: boolean }>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/share-members`,
    { method: "POST", body: jsonBody({ staff_id: staffId, role }) },
    token,
  );
}

export async function removeEventMember(token: string, eventId: string, staffId: string | number) {
  return apiFetch<{ ok?: boolean; deleted?: boolean }>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/share-members/${encodeURIComponent(String(staffId))}`,
    { method: "DELETE" },
    token,
  );
}
