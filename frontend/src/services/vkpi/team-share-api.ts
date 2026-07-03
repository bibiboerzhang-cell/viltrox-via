// C2 团队共享管理 API:KOL 共享关系总列表 + 按 share id 撤销(TeamModal「共享管理」区用)。
// 后端(backend/app/api/routers/vkpi_my_kol.py):
//   GET    /api/admin/vkpi/my-kol/shares            —— 管理层看全部;普通成员只看自己发出+收到
//   DELETE /api/admin/vkpi/my-kol/shares/{share_id} —— 权限=分享人本人或管理层
// 表:vkpi_kol_pool_members(迁移 159;shared_via_group_id 见迁移 161)。
// 与 kol-api.ts 的 per-KOL 弹窗端点(share/unshare/members)互补,不替代。
import { apiFetch } from "../http";

export interface VkpiTeamKolShare {
  id: number | null;
  kol_pool_id: number | null;
  to_staff_id: number | null;
  to_name: string;
  to_email: string;
  from_staff_id: number | null;
  from_name: string; // shared_by 可空容旧:空串=未知分享人
  from_email: string;
  kol_name: string;
  platform: string;
  handle: string;
  created_at: string | null;
  shared_via_group_id: number | null; // 非 null = 分组共享展开产生(撤销后组重算可能恢复)
  shared_goal: string; // 协作设置(vkpi_collab_settings kind='kol');未设=空串
  reminder_rule: string;
  can_revoke: boolean; // 后端已按「分享人本人或管理层」算好
}

export interface VkpiTeamKolSharesResponse {
  items?: VkpiTeamKolShare[];
  count?: number;
  scope_all?: boolean; // true=管理层全量视角
}

export function listTeamKolShares(token: string, limit = 200) {
  return apiFetch<VkpiTeamKolSharesResponse>(
    `/api/admin/vkpi/my-kol/shares?limit=${encodeURIComponent(String(limit))}&_ts=${Date.now()}`,
    { cache: "no-store" },
    token,
  );
}

export function revokeTeamKolShare(token: string, shareId: number | string) {
  return apiFetch<{ status?: string; id?: number; note?: string }>(
    `/api/admin/vkpi/my-kol/shares/${encodeURIComponent(String(shareId))}`,
    { method: "DELETE" },
    token,
  );
}
