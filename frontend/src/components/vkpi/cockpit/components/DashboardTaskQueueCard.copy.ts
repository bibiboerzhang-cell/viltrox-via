// 任务队列卡的「门面文案 + 纯映射」关口。
// 抽出这一层有两个目的:
// 1) 行数闸 —— DashboardTaskQueueCard.tsx 只留组件与取数,文案/类型/纯函数住这里;
// 2) 门面禁术语 —— 卡面任何可见文字都必须经过本文件的映射,
//    厂商名(Google/OpenAI/Anthropic)、模型 id(gemini-*/gpt-*/claude-*)、
//    Provider/binding/worker/fleet breaker/single_call 之类内部词一律不得出现在返回值里。
//    内部口径只进 SrcChip / 溯源弹层,不进卡面、不进 title。
import type { ProgressRecentDone, ProgressTask } from "../../../../services/vkpi/progressCenter-api";
import { humanizeLlmReason } from "../llmReasonCopy";

export interface CostOverview {
  today?: {
    apify_usd?: number;
    apify_calls?: number;
    llm_usd?: number;
    llm_calls?: number;
    total_usd?: number;
  };
  budgets?: {
    monthly_total?: {
      configured?: boolean;
      allowed?: boolean;
      hard_stopped?: boolean;
      cap_usd?: number;
      current_spend?: number;
    };
  };
}

export interface LlmTaskReadiness {
  binding?: string;
  configured?: boolean;
  production_ready?: boolean;
  runtime_authorization?: {
    allowed_by_model_readiness?: boolean;
    source?: "signed_evidence" | "operator_ack" | "blocked";
    temporary?: boolean;
  };
}

export interface LlmSystemModelsOverview {
  task_model_readiness?: Record<string, LlmTaskReadiness>;
  readiness_audit?: {
    active_scope?: {
      binding_count?: number;
      bindings?: string[];
      production_ready_count?: number;
      runtime_authorized_count?: number;
      runtime_blocked_count?: number;
    };
  };
}

/** 每条数据链路各自持有的读取态。绝不把「失败」和「空」压成同一个 null。 */
export type ReadState = "loading" | "ready" | "forbidden" | "unavailable" | "error";

export const ACTIVE_POLL_INTERVAL_MS = 10_000;
export const IDLE_POLL_INTERVAL_MS = 30_000;
/** 成本账本每次要跑 8 个只读聚合,5 分钟一拍足够;原 60 秒纯属浪费。 */
export const COST_REFRESH_INTERVAL_MS = 300_000;

const SUCCESS_LLM_STATUSES = new Set(["success", "done", "completed", "settled"]);
const BLOCKED_LLM_STATUSES = new Set([
  "blocked", "budget_blocked", "cancelled", "failed", "timeout", "triage",
  "parse_failure", "validation_failure", "all_providers_failed", "fleet_breaker_open",
  "provider_exception", "provider_http_error", "provider_blocked", "transport_error",
]);

/** 结构化识别 HTTP 状态,不依赖具体错误类,便于测试与将来换 http 层。 */
export function classifyFetchError(error: unknown): Exclude<ReadState, "loading" | "ready"> {
  const status = Number((error as { status?: unknown } | null)?.status);
  if (status === 401 || status === 403) return "forbidden";
  if (status === 502 || status === 503 || status === 504) return "unavailable";
  return "error";
}

/** 读不到时的诚实说明;绝不回落成 0 或「已核」。 */
export function readStateLabel(state: ReadState): string {
  if (state === "loading") return "读取中";
  if (state === "forbidden") return "无权限";
  if (state === "unavailable") return "暂不可读";
  if (state === "error") return "读取失败";
  return "待核";
}

export function taskTitle(task: ProgressTask | undefined): string {
  if (!task) return "暂无任务";
  const kind = String(task.kind || task.job_type || "").trim();
  const label = String(task.label || "").trim();
  if (kind && label && kind !== label) return `${kind} · ${label}`;
  return label || kind || `任务 ${task.id}`;
}

export function laneProgress(tasks: ProgressTask[]): number | null {
  // Number(null) === 0。进度端点用 null 表示“已超历史均时/无法可靠估算”,
  // 不能把它误画成 0% 后再由最小宽度伪装成 6%。
  const values = tasks
    .filter((task) => task.progress_pct !== null && task.progress_pct !== undefined)
    .map((task) => Number(task.progress_pct))
    .filter(Number.isFinite);
  if (values.length === 0) return tasks.length > 0 ? null : 0;
  return Math.round(values.reduce((sum, value) => sum + value, 0) / values.length);
}

export function nonNegativeCount(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
}

/**
 * 仅用于「按服务通道归组计数」的内部键,永远不渲染。
 * 卡面只出数量(x/y),不出厂商名 —— 这是门面禁术语红线。
 */
export function channelGroupKey(bindingOrProvider: unknown): string {
  const raw = String(bindingOrProvider || "").trim();
  return raw.split(/[/:]/, 1)[0].toLowerCase() || "unknown";
}

export function isRecentLlmSuccess(item: ProgressRecentDone): boolean {
  return !item.has_error && SUCCESS_LLM_STATUSES.has(String(item.status || "").toLowerCase());
}

export function isRecentLlmBlocked(item: ProgressRecentDone): boolean {
  const status = String(item.status || "").toLowerCase();
  return item.has_error || BLOCKED_LLM_STATUSES.has(status)
    || (Boolean(item.reason_code) && !SUCCESS_LLM_STATUSES.has(status));
}

/**
 * 最近一次成功走通的是主通道还是备用通道 —— 保留「是否成功、是否走了降级」这层判断,
 * 但不点名厂商与模型 id(原文案是 "Google · gemini-x.y",直接违反门面禁术语)。
 */
export function recentSuccessLabel(item: ProgressRecentDone | undefined): string {
  if (!item) return "窗口内无成功记录";
  const mode = String(item.fallback_mode || "").trim();
  if (mode === "provider_fallback") return "备用通道已跑通";
  if (mode === "rule_v0" || mode === "safe_fallback") return "规则降级完成";
  return item.fallback_used ? "备用通道已跑通" : "主通道已跑通";
}

export function recentBlockedLabel(item: ProgressRecentDone | undefined): string {
  if (!item) return "最近记录未见阻断";
  const reason = item.failure_reason_human
    || item.reason_code
    || item.error_category
    || item.reason_category
    || item.status;
  return humanizeLlmReason(reason, "最近一条任务未完成,请到设置页查看。").message;
}

/** 队列链路读不到时,卡头状态位的诚实说明(绝不回落成「当前无任务」)。 */
export function progressStateLabel(state: ReadState): string {
  if (state === "loading") return "队列状态读取中";
  if (state === "forbidden") return "无权查看队列";
  if (state === "unavailable") return "队列状态暂不可读";
  if (state === "error") return "队列状态读取失败";
  return "";
}

/** 泳道右侧计数位很窄(紧凑态 25px),非就绪态只能给两字以内的短标。 */
export function laneStateShortText(state: ReadState): string {
  if (state === "loading") return "…";
  if (state === "forbidden") return "无权";
  return "失败";
}

/** 卡面大 title:去掉 single_call / provider·cost scope / force_offline / fleet breaker 四个内部词。 */
export const BASE_CONFIG_TITLE = "这里只核四项基础状态:后台是否在运行、服务通道是否配好、月度总预算是否放行、模型是否已授权。"
  + "它不代表某个具体任务一定能跑通 —— 每个任务开跑前还会再单独确认一次(另有若干任务级闸门在每次开跑前单独判定)。"
  + "下方「最近」两行只覆盖近 2 小时最多 5 条记录,不是全天统计。";

export const RECENT_WINDOW_TITLE = "近 2 小时最多 5 条任务记录的只读摘要,不代表 24 小时全量统计";
