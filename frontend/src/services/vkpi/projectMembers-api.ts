import { apiFetch, jsonBody } from "../http";

// 项目成员分享(后端 CRUD 已就绪;仅 owner/admin 可增删,非授权调用方拿到 403)。
//   GET    /api/admin/vkpi/projects/{project_id}/members
//   POST   /api/admin/vkpi/projects/{project_id}/members   body {staff_id, role}
//   DELETE /api/admin/vkpi/projects/{project_id}/members/{staff_id}

export type VkpiMemberRole = "viewer" | "editor";

export interface VkpiProjectMember {
  staff_id: number | string;
  role: VkpiMemberRole | string;
  name?: string | null;
  email?: string | null;
  added_at?: string | null;
  added_by?: number | string | null;
}

export interface VkpiProjectMembersResponse {
  project_id?: number | string;
  members?: VkpiProjectMember[];
  items?: VkpiProjectMember[];
}

/** 解包 {members} | {items} | 裸数组 → 成员数组(后端形态容差)。 */
export function unwrapMembers<T = VkpiProjectMember>(res: unknown): T[] {
  if (Array.isArray(res)) return res as T[];
  if (res && typeof res === "object") {
    const r = res as Record<string, unknown>;
    if (Array.isArray(r.members)) return r.members as T[];
    if (Array.isArray(r.items)) return r.items as T[];
  }
  return [];
}

export async function listProjectMembers(token: string, projectId: string) {
  return apiFetch<VkpiProjectMembersResponse | VkpiProjectMember[]>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/members?_ts=${Date.now()}`,
    { cache: "no-store" },
    token,
  );
}

export async function addProjectMember(
  token: string,
  projectId: string,
  staffId: string | number,
  role: VkpiMemberRole = "viewer",
) {
  return apiFetch<VkpiProjectMember | { ok?: boolean }>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/members`,
    { method: "POST", body: jsonBody({ staff_id: staffId, role }) },
    token,
  );
}

export async function removeProjectMember(token: string, projectId: string, staffId: string | number) {
  return apiFetch<{ ok?: boolean; deleted?: boolean }>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(String(staffId))}`,
    { method: "DELETE" },
    token,
  );
}
