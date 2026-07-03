import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

export async function listEmployeeChannels(token: string, viewAsStaffId?: string) {
  const suffix = viewAsStaffId ? `?view_as_staff_id=${encodeURIComponent(viewAsStaffId)}` : "";
  return apiFetch<{ channels?: Row[] }>(`/api/marketing/channels${suffix}`, {}, token);
}

export async function bindEmployeeChannel(token: string, payload: Row, viewAsStaffId?: string) {
  const suffix = viewAsStaffId ? `?view_as_staff_id=${encodeURIComponent(viewAsStaffId)}` : "";
  return apiFetch<Row>(`/api/marketing/channels${suffix}`, { method: "POST", body: jsonBody(payload) }, token);
}

export async function syncEmployeeChannel(token: string, channelId: string) {
  const response = await apiFetch<Row>(
    `/api/marketing/channels/${encodeURIComponent(channelId)}/sync-now`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
  if (response.task_id && !response.message) return { ...response, message: "同步任务已加入队列。" };
  return response;
}

export async function getOfficialChannelMatrix(
  token: string,
  filters: { limit?: number; viewAsStaffId?: string } = {},
) {
  const params = new URLSearchParams();
  params.set("limit", String(filters.limit ?? 20));
  if (filters.viewAsStaffId) params.set("view_as_staff_id", filters.viewAsStaffId);
  return apiFetch<{ platforms?: Row[]; account_count?: number; post_count?: number; total_views?: number; staff_managed?: Row[] }>(
    `/api/marketing/channels/official-matrix?${params.toString()}`,
    { timeoutMs: 7000 },
    token,
  );
}

export async function getOfficialChannelGapReport(
  token: string,
  filters: { limit?: number; viewAsStaffId?: string } = {},
) {
  const params = new URLSearchParams();
  params.set("limit", String(filters.limit ?? 50));
  if (filters.viewAsStaffId) params.set("view_as_staff_id", filters.viewAsStaffId);
  return apiFetch<{ summary?: Row; accounts?: Row[]; platforms?: Row[] }>(
    `/api/marketing/channels/official-gap-report?${params.toString()}`,
    {},
    token,
  );
}

export async function getOfficialChannelPosts(
  token: string,
  channelId: number | string,
  filters: { page?: number; limit?: number; sort?: string; direction?: string; window?: string } = {},
) {
  const params = new URLSearchParams();
  params.set("page", String(filters.page ?? 1));
  params.set("limit", String(filters.limit ?? 10));
  params.set("sort", filters.sort || "latest");
  params.set("direction", filters.direction || "desc");
  params.set("window", filters.window || "all");
  return apiFetch<{ account?: Row; posts?: Row[]; pagination?: Row; sort?: string; source?: string }>(
    `/api/marketing/channels/${encodeURIComponent(String(channelId))}/posts?${params.toString()}`,
    { timeoutMs: 8000 },
    token,
  );
}

export async function getChannelPostComments(
  token: string,
  channelId: number | string,
  filters: { postId?: string; url?: string; limit?: number } = {},
) {
  const params = new URLSearchParams();
  params.set("post_id", filters.postId || "");
  if (filters.url) params.set("url", filters.url);
  params.set("limit", String(filters.limit ?? 50));
  return apiFetch<Row>(
    `/api/marketing/channels/${encodeURIComponent(String(channelId))}/post-comments?${params.toString()}`,
    { timeoutMs: 8000 },
    token,
  );
}

// 每日官号分析报告:读最新一份(播放/评论/画质/趋势/建议),与「立即生成」手动触发。
export async function getOfficialDailyReport(token: string, channelId: number | string) {
  return apiFetch<Row>(
    `/api/admin/vkpi/channels/official-daily-report?channel_id=${encodeURIComponent(String(channelId))}`,
    { timeoutMs: 8000 },
    token,
  );
}

export async function runOfficialDailyReport(token: string, channelId: number | string) {
  return apiFetch<Row>(
    `/api/admin/vkpi/channels/official-daily-report/run?channel_id=${encodeURIComponent(String(channelId))}`,
    { method: "POST", timeoutMs: 120000 },
    token,
  );
}

export async function collectChannelPostComments(
  token: string,
  channelId: number | string,
  payload: { postId?: string; url?: string; limit?: number } = {},
) {
  return apiFetch<Row>(
    `/api/marketing/channels/${encodeURIComponent(String(channelId))}/post-comments/collect`,
    {
      method: "POST",
      body: jsonBody({
        post_id: payload.postId || "",
        url: payload.url || "",
        limit: payload.limit ?? 100,
      }),
    },
    token,
  );
}

export async function getRedditChannelAssessment(token: string, channelId: number | string) {
  return apiFetch<Row>(
    `/api/marketing/channels/${encodeURIComponent(String(channelId))}/reddit-assessment`,
    {},
    token,
  );
}

export async function enqueueVideoCacheTask(
  token: string,
  payload: {
    platform: string;
    videoId: string;
    sourceUrl: string;
    channelId?: string | number;
    forceRefresh?: boolean;
  },
) {
  const response = await apiFetch<Row>(
    "/api/admin/vkpi/tasks",
    {
      method: "POST",
      body: jsonBody({
        task_type: "vkpi_video_cache",
        params: {
          platform: payload.platform,
          video_id: payload.videoId,
          source_url: payload.sourceUrl,
          channel_id: payload.channelId,
          force_refresh: Boolean(payload.forceRefresh),
        },
        priority: 3,
        timeout_seconds: 300,
      }),
    },
    token,
  );
  return { task_id: String(response.task_id || "") };
}

// C7-A3 官方账号分配表:GET 全量分配 + 成员下拉选项;PUT 指派/清除。
// 后端 /api/admin/vkpi/channels/assignments(读全员开放,can_manage 标记写权限);
// PUT 仅 owner/管理层可用(403 由后端硬拦),staff_id 传 null/0 = 清除分配。
export interface VkpiChannelAssignmentRow {
  id?: number;
  channel_id?: number;
  staff_id?: number;
  role?: string;
  assigned_at?: string;
  assigned_by_staff_id?: number | null;
  staff_name?: string;
  staff_email?: string;
  platform?: string;
  handle?: string;
  display_name?: string;
  official?: boolean;
}

export interface VkpiChannelAssignmentStaffOption {
  id?: number;
  name?: string;
  email?: string;
  role?: string;
}

export interface VkpiChannelAssignmentsResponse {
  available?: boolean;
  can_manage?: boolean;
  me_staff_id?: number | null;
  assignments?: VkpiChannelAssignmentRow[];
  staff_options?: VkpiChannelAssignmentStaffOption[];
}

export async function getChannelAssignments(token: string) {
  return apiFetch<VkpiChannelAssignmentsResponse>(
    "/api/admin/vkpi/channels/assignments",
    { timeoutMs: 8000 },
    token,
  );
}

export async function putChannelAssignment(
  token: string,
  channelId: number | string,
  staffId: number | null,
  role = "owner",
) {
  return apiFetch<{ ok?: boolean; channel_id?: number; role?: string; staff_id?: number | null; assigned_at?: string; cleared?: boolean }>(
    `/api/admin/vkpi/channels/${encodeURIComponent(String(channelId))}/assignment`,
    { method: "PUT", body: jsonBody({ staff_id: staffId, role }) },
    token,
  );
}
