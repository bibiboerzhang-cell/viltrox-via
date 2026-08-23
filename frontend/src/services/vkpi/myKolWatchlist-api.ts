import { apiFetch } from "../http";

// MY KOL 观察清单(波 C·C3 前端 / C5 后端 my_kol_watchlist_overview_v1):
//   GET /api/admin/vkpi/my-kol/watch-overview                  —— 本人全部分组各一张汇总卡
//   GET /api/admin/vkpi/my-kol/groups/{group_id}/watch-overview —— 组内逐 KOL 观察行
// 分组 = 既有员工分组(staff-groups),组内 KOL = permissions.shared_kol_pool_ids;不造新表。
// 全部字段按「缺席不渲染」读:后端版本没有某键 → 前端当 undefined,绝不编数。

export type WatchTrackingState = "tracked" | "untracked" | "no_videos";
export type WatchDeltaReason = "no_tracked_videos" | "insufficient_samples" | string;

export interface WatchDeepAnalysis {
  completed?: number;
  in_progress?: number;
  failed?: number;
  not_requested?: number;
  scope_videos?: number;
  scope_limit?: number;
  completion_ratio?: number | null;
}

export interface WatchLensFamily {
  lens_key?: string;
  display_name?: string;
  category_main?: string;
  resolution?: string;
  mentions?: number;
  videos?: number;
}

export interface WatchKolRow {
  kol_pool_id: number;
  display_name?: string;
  handle?: string;
  platform?: string;
  profile_url?: string;
  tracking_state?: WatchTrackingState;
  video_evidence_total?: number;
  tracked_videos?: number;
  tracked_paused?: number;
  last_snapshot_at?: string | null;
  last_metric_attempt_at?: string | null;
  snapshot_samples?: number;
  views_delta_7d?: number | null;
  views_delta_7d_reason?: WatchDeltaReason | null;
  views_delta_7d_videos?: number;
  views_delta_7d_status?: "ready" | "partial" | null;
  deep_analysis?: WatchDeepAnalysis;
  open_failures?: number;
  open_failures_breakdown?: { deep_analysis?: number; metric_refresh?: number };
  lens_families?: WatchLensFamily[];
  latest_video_published_at?: string | null;
  last_activity_at?: string | null;
}

export interface WatchSummary {
  kol_count?: number;
  tracked_count?: number;
  untracked_count?: number;
  no_videos_count?: number;
  tracked_videos_total?: number;
  tracked_paused_total?: number;
  last_snapshot_at?: string | null;
  views_delta_7d?: number | null;
  views_delta_7d_reason?: WatchDeltaReason | null;
  views_delta_7d_kols?: number;
  deep_analysis?: WatchDeepAnalysis;
  open_failures?: number;
  last_activity_at?: string | null;
  empty_reason?: string | null;
}

export interface WatchGroupHead {
  group_id: string;
  name?: string;
  description?: string;
  member_count?: number;
  updated_at?: string | null;
}

export interface WatchGroupCard extends WatchGroupHead {
  kol_ids?: number[];
  summary?: WatchSummary;
}

export interface WatchSchedulerGate {
  enabled?: boolean | null;
  [key: string]: unknown;
}

export interface VkpiMyKolWatchOverviewResponse {
  contract?: string;
  read_only?: boolean;
  generated_at?: string | null;
  groups?: WatchGroupCard[];
  totals?: WatchSummary & {
    group_count?: number;
    groups_with_kols?: number;
    favorites_not_in_any_group?: number | null;
  };
  missing_kol_ids?: number[];
  tracked_truncated?: boolean;
  scheduler?: WatchSchedulerGate;
  window?: string;
  empty_reason?: "no_groups" | "no_kols_in_groups" | string | null;
}

export interface VkpiMyKolGroupWatchOverviewResponse {
  contract?: string;
  read_only?: boolean;
  generated_at?: string | null;
  group?: WatchGroupHead;
  summary?: WatchSummary;
  items?: WatchKolRow[];
  kol_ids_configured?: number;
  kols_truncated?: boolean;
  missing_kol_ids?: number[];
  tracked_truncated?: boolean;
  scheduler?: WatchSchedulerGate;
  window?: string;
  empty_reason?: string | null;
}

export async function getMyKolWatchOverview(token: string) {
  return apiFetch<VkpiMyKolWatchOverviewResponse>("/api/admin/vkpi/my-kol/watch-overview", {}, token);
}

export async function getMyKolGroupWatchOverview(token: string, groupId: string) {
  return apiFetch<VkpiMyKolGroupWatchOverviewResponse>(
    `/api/admin/vkpi/my-kol/groups/${encodeURIComponent(groupId)}/watch-overview`,
    {},
    token,
  );
}

/** 7d 播放增量文案:有值 → 「+1,234」;null → 按 reason 诚实空(不编数)。 */
export function watchDeltaText(value: number | null | undefined, reason?: string | null): string {
  if (value != null && Number.isFinite(Number(value))) {
    const n = Number(value);
    return `${n > 0 ? "+" : ""}${n.toLocaleString()}`;
  }
  if (reason === "no_tracked_videos") return "未登记追踪";
  if (reason === "insufficient_samples") return "样本不足";
  return "待积累";
}

/** 深析完成比文案:scope_videos=0 → 「无视频」;否则「completed/scope_videos」。 */
export function watchDeepText(deep: WatchDeepAnalysis | undefined): string {
  if (!deep) return "—";
  const scope = Number(deep.scope_videos) || 0;
  if (scope <= 0) return "无视频";
  return `${Number(deep.completed) || 0}/${scope}`;
}
