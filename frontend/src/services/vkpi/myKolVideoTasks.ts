// MY KOL 视频库任务状态契约 my_kol_video_recovery_v1(纯函数 + 类型;零网络)。
//   后端:GET /api/admin/vkpi/my-kol/{kol_pool_id}/videos(backend/app/domains/kol/my_kol_video_recovery.py)
//   两层真值:TaskState.status = apify_jobs 里最新持久任务态(排队/进行中/重试中/已阻断/失败/已完成/未发起);
//            TaskState.data   = 现在落库的数据(有/过期/无 + 新鲜度 + 更新时间)。
//   任务态 ≠ 数据新鲜度:活跃任务晚于数据时 status 取任务态、data.superseded_by_job=true,
//   旧结果仍可见但门面要标「重测中/重分析中 · 上次结果可见」。
//   门面文案禁内部词(job/apify/final_v1/provider 不上卡面);阻断/失败只给诚实等级,不泄 raw error。
import { formatLocal, relativeFromNow } from "../../components/vkpi/lib/timeLocal";

export const MY_KOL_VIDEO_RECOVERY_CONTRACT = "my_kol_video_recovery_v1";

export type VkpiTaskStatus = "queued" | "running" | "retrying" | "blocked" | "failed" | "ready" | "legacy_unverified" | "not_requested";
export type VkpiTaskDataStatus = "ready" | "stale" | "legacy_unverified" | "none";
export type VkpiTaskFreshness = "fresh" | "stale" | "never" | "unavailable";
export type VkpiTrackingStatus = "tracked" | "failed" | "stale" | "insufficient_history" | "unavailable";

export interface VkpiTaskData {
  status: VkpiTaskDataStatus;
  freshness: VkpiTaskFreshness;
  updated_at: string | null;
  superseded_by_job: boolean;
  /** 仅 metric_refresh.data 带:快照层追踪态 / 样本数 / 尝试次数 */
  tracking_status?: VkpiTrackingStatus | string;
  sample_count?: number;
  attempt_count?: number;
  cache_reuse_status?: string;
  revalidation_required?: boolean;
  claim_status?: string;
}

export interface VkpiTaskState {
  status: VkpiTaskStatus;
  job_id: number | null;
  requested_at: string | null;
  updated_at: string | null;
  data: VkpiTaskData;
  /** 失败可读(优化波 B · O→F 契约):失败/阻断项才有;缺席 = 旧服务端,门面不编故事。 */
  failure_category?: string;
  failure_reason_human?: string;
  /** ETA 新口径(活跃车道数 × 队列位置 × p50);只在排队/进行中且服务端给了才有。 */
  eta_seconds?: number | null;
  cache_reuse_status?: string;
  revalidation_required?: boolean;
  claim_status?: string;
  terminal?: boolean;
}

export interface VkpiVideoTasks {
  metric_refresh: VkpiTaskState;
  final_v1: VkpiTaskState;
  keyframe_qa?: VkpiTaskState;
}

export interface VkpiVideoPageInfo {
  limit: number;
  returned: number;
  has_more: boolean;
  next_cursor: string | null;
  cursor_kind: "published_at_id" | string;
  order: "published_at_desc_id_desc" | string;
}

export const ACTIVE_TASK_STATUSES: ReadonlySet<VkpiTaskStatus> = new Set(["queued", "running", "retrying"]);

export function isTaskActive(state: Pick<VkpiTaskState, "status"> | null | undefined): boolean {
  return ACTIVE_TASK_STATUSES.has(String(state?.status || "") as VkpiTaskStatus);
}

const EMPTY_DATA: VkpiTaskData = { status: "none", freshness: "unavailable", updated_at: null, superseded_by_job: false };

/** 容错归一:缺席/旧形状一律落「未发起 + 数据不可用」,绝不把缺席当完成。 */
export function normalizeTaskState(raw: unknown): VkpiTaskState {
  const source = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const status = String(source.status || "not_requested");
  const data = (source.data && typeof source.data === "object" ? source.data : {}) as Record<string, unknown>;
  const dataStatus = String(data.status || "none");
  const freshness = String(data.freshness || "unavailable");
  // 失败可读 / ETA:任务态顶层优先,data 层兜底;缺席不补默认值(前端按「没有」处理)。
  const failureCategory = String(source.failure_category ?? data.failure_category ?? "").trim();
  const failureReasonHuman = String(source.failure_reason_human ?? data.failure_reason_human ?? "").trim();
  const etaRaw = source.eta_seconds ?? data.eta_seconds;
  const etaSeconds = etaRaw == null ? null : Number(etaRaw);
  return {
    status: (["queued", "running", "retrying", "blocked", "failed", "ready", "legacy_unverified", "not_requested"].includes(status) ? status : "failed") as VkpiTaskStatus,
    job_id: Number.isFinite(Number(source.job_id)) && source.job_id != null ? Number(source.job_id) : null,
    requested_at: source.requested_at ? String(source.requested_at) : null,
    updated_at: source.updated_at ? String(source.updated_at) : null,
    data: {
      ...EMPTY_DATA,
      status: (["ready", "stale", "legacy_unverified", "none"].includes(dataStatus) ? dataStatus : "none") as VkpiTaskDataStatus,
      freshness: (["fresh", "stale", "never", "unavailable"].includes(freshness) ? freshness : "unavailable") as VkpiTaskFreshness,
      updated_at: data.updated_at ? String(data.updated_at) : null,
      superseded_by_job: Boolean(data.superseded_by_job),
      ...(data.tracking_status != null ? { tracking_status: String(data.tracking_status) } : {}),
      ...(data.sample_count != null ? { sample_count: Math.max(0, Number(data.sample_count) || 0) } : {}),
      ...(data.attempt_count != null ? { attempt_count: Math.max(0, Number(data.attempt_count) || 0) } : {}),
      ...(data.cache_reuse_status != null ? { cache_reuse_status: String(data.cache_reuse_status) } : {}),
      ...(data.revalidation_required != null ? { revalidation_required: Boolean(data.revalidation_required) } : {}),
      ...(data.claim_status != null ? { claim_status: String(data.claim_status) } : {}),
    },
    ...(failureCategory ? { failure_category: failureCategory } : {}),
    ...(failureReasonHuman ? { failure_reason_human: failureReasonHuman } : {}),
    ...(etaRaw != null && Number.isFinite(etaSeconds) ? { eta_seconds: etaSeconds } : {}),
    ...(source.cache_reuse_status != null ? { cache_reuse_status: String(source.cache_reuse_status) } : {}),
    ...(source.revalidation_required != null ? { revalidation_required: Boolean(source.revalidation_required) } : {}),
    ...(source.claim_status != null ? { claim_status: String(source.claim_status) } : {}),
    ...(source.terminal != null ? { terminal: Boolean(source.terminal) } : {}),
  };
}

export function normalizeVideoTasks(raw: unknown): VkpiVideoTasks | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as Record<string, unknown>;
  return {
    metric_refresh: normalizeTaskState(source.metric_refresh),
    final_v1: normalizeTaskState(source.final_v1),
    keyframe_qa: normalizeTaskState(source.keyframe_qa),
  };
}

/* ============ 门面文案(中文源串;组件用 t() 包一层进英文词典) ============ */

export type TaskChipTone = "pending" | "active" | "blocked" | "failed" | "ready" | "idle";

export interface TaskChip {
  /** 门面短标签,如「排队中」「重测中 · 上次结果可见」 */
  label: string;
  tone: TaskChipTone;
  /** 悬浮说明(诚实原因等级 / 时间戳) */
  title: string;
}

export type VideoTaskKind = "metric" | "analysis" | "review";

const TASK_STATUS_LABEL: Record<VkpiTaskStatus, string> = {
  queued: "排队中",
  running: "进行中",
  retrying: "重试中",
  blocked: "已阻断",
  failed: "失败",
  ready: "已完成",
  legacy_unverified: "旧结果待复核",
  not_requested: "未发起",
};

const TASK_KIND_NOUN: Record<VideoTaskKind, string> = { metric: "播放追踪", analysis: "深析", review: "关键帧复核" };
const SUPERSEDED_LABEL: Record<VideoTaskKind, string> = { metric: "重测中 · 上次结果可见", analysis: "重分析中 · 上次结果可见", review: "重新复核中 · 上次结果可见" };

function stamp(value: string | null | undefined): string {
  if (!value) return "";
  const abs = formatLocal(value);
  const rel = relativeFromNow(value);
  return abs ? `${rel ? `${rel} · ` : ""}${abs}` : "";
}

/** 阻断/失败的诚实原因等级:契约不下发 raw error,只能按可观测事实分级。 */
function failureLevel(kind: VideoTaskKind, state: VkpiTaskState): string {
  const attempts = Number(state.data.attempt_count || 0);
  // 新契约有人话原因就直接说(悬浮说明);旧分级文案只在缺席时兜底。
  const human = String(state.failure_reason_human || "").trim();
  if (human) return `${TASK_KIND_NOUN[kind]}${state.status === "blocked" ? "已阻断" : "失败"} · ${human}`;
  if (state.status === "blocked") {
    return `${TASK_KIND_NOUN[kind]}已阻断 · 需人工处理(权限、额度或资料缺失之一;不会自动重跑)`;
  }
  if (kind === "metric" && attempts > 0) return `${TASK_KIND_NOUN[kind]}失败 · 已尝试 ${attempts} 次 · 可重新发起`;
  if (state.data.status !== "none") return `${TASK_KIND_NOUN[kind]}失败 · 上次结果仍可见 · 可重新发起`;
  return `${TASK_KIND_NOUN[kind]}失败 · 尚无可用结果 · 可重新发起`;
}

/** 任务态 chip(第一层):活跃 / 阻断 / 失败 / 完成 / 未发起;活跃且晚于数据 → 「重测中/重分析中 · 上次结果可见」。 */
export function taskChip(kind: VideoTaskKind, state: VkpiTaskState): TaskChip {
  const noun = TASK_KIND_NOUN[kind];
  const requested = stamp(state.requested_at);
  const updated = stamp(state.updated_at);
  if (isTaskActive(state)) {
    const superseded = state.data.superseded_by_job && state.data.status !== "none";
    return {
      label: superseded ? SUPERSEDED_LABEL[kind] : `${noun}${TASK_STATUS_LABEL[state.status]}`,
      tone: "active",
      title: `${noun}${TASK_STATUS_LABEL[state.status]}${requested ? ` · 发起于 ${requested}` : ""}${superseded ? " · 旧结果在新结果落地前保持可见" : ""}`,
    };
  }
  if (state.status === "blocked") return { label: `${noun}已阻断`, tone: "blocked", title: failureLevel(kind, state) };
  if (state.status === "failed") return { label: `${noun}失败`, tone: "failed", title: `${failureLevel(kind, state)}${updated ? ` · ${updated}` : ""}` };
  if (state.status === "legacy_unverified") return {
    label: `${noun}旧结果待复核`, tone: "pending",
    title: `${noun}旧结果仍可查看,但缺少当前版本的完整来源与质量证明;本页不会自动付费重跑`,
  };
  if (state.status === "ready") return { label: `${noun}已完成`, tone: "ready", title: `${noun}已完成${updated ? ` · ${updated}` : ""}` };
  return { label: `${noun}未发起`, tone: "idle", title: `还没有发起过${noun}` };
}

/** 数据新鲜度(第二层):实测于 xx 前 / 已过期 / 从未测 / 暂不可用。 */
export function freshnessText(kind: VideoTaskKind, state: VkpiTaskState): { label: string; title: string } {
  const verb = kind === "metric" ? "实测" : kind === "analysis" ? "分析" : "复核";
  const at = state.data.updated_at;
  const rel = at ? relativeFromNow(at) : "";
  const abs = at ? formatLocal(at) : "";
  if (state.data.status === "legacy_unverified") return { label: "旧结果仅供查看", title: "旧分析未通过当前复用校验,需显式复核后才能作为已完成结果" };
  if (state.data.freshness === "fresh" && at) return { label: `${verb}于 ${rel || abs}`, title: `${verb}于 ${abs}(按浏览器时区)` };
  if (state.data.freshness === "stale") return { label: at ? `已过期 · 上次${verb} ${rel || abs}` : "已过期", title: at ? `上次${verb} ${abs};可重新发起` : "结果已过期;可重新发起" };
  if (state.data.freshness === "never") return { label: `从未${verb}`, title: `还没有${verb}结果` };
  if (at) return { label: `上次${verb} ${rel || abs} · 趋势待积累`, title: `有过一次读数(${abs}),但持续追踪尚未建立;点「追踪播放」开始记录走势` };
  return { label: `${verb}数据暂不可用`, title: "读模型缺席(旧库未迁移或字段缺失),不代表 0" };
}

export const TASK_CHIP_TONE_CLASS: Record<TaskChipTone, string> = {
  active: "border-accent bg-accent-soft text-accent",
  pending: "border-warn bg-warn-soft text-warn",
  blocked: "border-warn bg-warn-soft text-warn",
  failed: "border-crit bg-crit-soft text-crit",
  ready: "border-good bg-good-soft text-good",
  idle: "border-line text-muted",
};
