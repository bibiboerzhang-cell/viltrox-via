import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

/** member_ids 写库前强制转 number(UI 传的是 String(staff.id)),只在存在时改写。 */
function coerceMembers<T extends { member_ids?: (string | number)[] }>(payload: T): T {
  if (!payload || payload.member_ids == null) return payload;
  return { ...payload, member_ids: (payload.member_ids || []).map((x) => Number(x)) };
}

// 员工分组(staff-groups)前端 API + 适配器。后端前缀 /api/admin/vkpi/staff-groups。
// member_ids 是 bigint staff id 的 jsonb 数组(对齐 vkpi_events.team_ids)。
// GET 需 require_tab("vkpi","read");增删改需 ("vkpi","write")。

export interface VkpiStaffGroupPermissions {
  shared_projects?: string[];
  shared_kol_pool?: string;
  kpi_goal?: string;
  reminder_rule?: string;
}

export interface VkpiStaffGroup {
  id: string;
  name: string;
  description: string;
  member_ids: number[];
  permissions: VkpiStaffGroupPermissions;
  created_by?: number | null;
  created_at?: string;
  updated_at?: string;
}

export interface VkpiStaffGroupCreatePayload {
  name: string;
  description?: string;
  member_ids?: number[];
  permissions?: VkpiStaffGroupPermissions;
}

export interface VkpiStaffGroupUpdatePayload {
  name?: string;
  description?: string;
  member_ids?: number[];
  permissions?: VkpiStaffGroupPermissions;
}

export async function listStaffGroups(
  token: string,
): Promise<{ items?: VkpiStaffGroup[] }> {
  return apiFetch<{ items?: VkpiStaffGroup[] }>(
    "/api/admin/vkpi/staff-groups",
    {},
    token,
  );
}

export async function createStaffGroup(
  token: string,
  payload: VkpiStaffGroupCreatePayload,
): Promise<{ item?: VkpiStaffGroup } | VkpiStaffGroup> {
  return apiFetch<{ item?: VkpiStaffGroup } | VkpiStaffGroup>(
    "/api/admin/vkpi/staff-groups",
    {
      method: "POST",
      body: jsonBody(coerceMembers(payload)),
    },
    token,
  );
}

export async function updateStaffGroup(
  token: string,
  groupId: string,
  payload: VkpiStaffGroupUpdatePayload,
): Promise<{ item?: VkpiStaffGroup } | VkpiStaffGroup> {
  return apiFetch<{ item?: VkpiStaffGroup } | VkpiStaffGroup>(
    `/api/admin/vkpi/staff-groups/${encodeURIComponent(groupId)}`,
    {
      method: "PATCH",
      body: jsonBody(coerceMembers(payload)),
    },
    token,
  );
}

export async function deleteStaffGroup(
  token: string,
  groupId: string,
): Promise<{ ok: boolean; id: string }> {
  return apiFetch<{ ok: boolean; id: string }>(
    `/api/admin/vkpi/staff-groups/${encodeURIComponent(groupId)}`,
    {
      method: "DELETE",
    },
    token,
  );
}

export async function addGroupMember(
  token: string,
  groupId: string,
  staffId: string | number,
): Promise<{ item?: VkpiStaffGroup } | VkpiStaffGroup> {
  return apiFetch<{ item?: VkpiStaffGroup } | VkpiStaffGroup>(
    `/api/admin/vkpi/staff-groups/${encodeURIComponent(groupId)}/members`,
    {
      method: "POST",
      body: jsonBody({ staff_id: Number(staffId) }),
    },
    token,
  );
}

export async function removeGroupMember(
  token: string,
  groupId: string,
  staffId: string | number,
): Promise<{ item?: VkpiStaffGroup } | VkpiStaffGroup> {
  return apiFetch<{ item?: VkpiStaffGroup } | VkpiStaffGroup>(
    `/api/admin/vkpi/staff-groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(String(staffId))}`,
    {
      method: "DELETE",
    },
    token,
  );
}

// ── Adapter:后端 snake/nested ↔ 前端 camel ────────────────────────────────

// UI 侧保持 snake_case(与 TeamModal/EditGroupModal/集成层一致);member_ids 字符串化
// 以便与 UiStaff.id(String(staff.id))做勾选比对。
export interface UiGroup {
  id: string;
  name: string;
  description: string;
  member_ids: string[];
  permissions: {
    shared_projects: string[];
    shared_kol_pool: string;
    kpi_goal: string;
    reminder_rule: string;
  };
  created_by?: string;
  created_at?: string;
  updated_at?: string;
}

/** 解包 {item} | {group} | 裸对象 → 单实体 row。 */
export function unwrapItem<T = VkpiStaffGroup>(res: unknown): T | null {
  if (!res || typeof res !== "object") return null;
  const r = res as Row;
  return (r.item ?? r.group ?? r) as T;
}

/** 后端 group row → UI 形态(member_ids 字符串化以对齐 UiStaff.id;permissions 字段补全)。 */
export function toUiGroup(row: VkpiStaffGroup): UiGroup {
  const perms = (row.permissions || {}) as VkpiStaffGroupPermissions;
  return {
    id: String(row.id),
    name: row.name ?? "",
    description: row.description ?? "",
    member_ids: (Array.isArray(row.member_ids) ? row.member_ids : []).map(String),
    permissions: {
      shared_projects: Array.isArray(perms.shared_projects) ? perms.shared_projects.map(String) : [],
      shared_kol_pool: perms.shared_kol_pool ?? "",
      kpi_goal: perms.kpi_goal ?? "",
      reminder_rule: perms.reminder_rule ?? "",
    },
    created_by: row.created_by != null ? String(row.created_by) : undefined,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}
