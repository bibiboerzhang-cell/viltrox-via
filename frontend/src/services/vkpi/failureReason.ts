// 失败可读 + ETA 新口径(优化波 B · F3/F7;纯函数,零网络)。
//   契约(O→F,冻结):账号级进度 / my-kol videos / 进度中心的每个失败项多两字段
//     failure_category: download | authorization | budget | model | provider | unknown
//     failure_reason_human: 中文一句话(后端已人话化;英文 locale 用 category 映射的英文短句)
//   ETA 只认 eta_seconds(新口径 = 活跃车道数 × 队列位置 × p50);旧 estimated_remaining_seconds
//   不再当 ETA 渲染;字段缺失 → 不显示(绝不编排队文案)。
//   门面禁内部词:这里产出的文案不得出现 job/apify/provider/final_v1 等词。

export type FailureCategory = "download" | "authorization" | "budget" | "model" | "provider" | "unknown";

export type FailureAction = "reissue_from_my_kol" | "check_budget" | "auto_retry" | null;

export interface FailureGuidanceCopy {
  category: FailureCategory;
  /** 失败原因(中文源串;英文 locale 由 failureReasonForLocale 决定) */
  reason: string;
  /** 按类别的提示(中文源串,可进 t()) */
  hint: string;
  /** 按类别的动作 */
  action: FailureAction;
  /** 动作按钮文案(中文源串,可进 t());无动作为空 */
  actionLabel: string;
}

const CATEGORIES: ReadonlySet<string> = new Set(["download", "authorization", "budget", "model", "provider", "unknown"]);

/** 旧错误类别(last_error_category / error_category)→ 新六类的保守映射;认不出一律 unknown。 */
const LEGACY_CATEGORY_HINTS: ReadonlyArray<[RegExp, FailureCategory]> = [
  [/download|media|yt-?dlp|fetch_failed|no_media|transcode/i, "download"],
  [/auth|permission|forbidden|403|scope|not_allowed|paid_action|staff/i, "authorization"],
  [/budget|quota_exceeded|spend|cost_cap/i, "budget"],
  [/model|schema|invalid_response|json|thinking|output/i, "model"],
  [/provider|429|5xx|timeout|transport|rate_limit|unavailable|gateway/i, "provider"],
];

export function normalizeFailureCategory(raw: unknown): FailureCategory {
  const text = String(raw ?? "").trim().toLowerCase();
  if (!text) return "unknown";
  if (CATEGORIES.has(text)) return text as FailureCategory;
  for (const [pattern, category] of LEGACY_CATEGORY_HINTS) {
    if (pattern.test(text)) return category;
  }
  return "unknown";
}

/** 从任意失败项对象读类别:新字段优先,旧字段保守映射。 */
export function failureCategoryOf(source: unknown): FailureCategory {
  const row = (source && typeof source === "object" ? source : {}) as Record<string, unknown>;
  const explicit = String(row.failure_category ?? "").trim();
  if (explicit) return normalizeFailureCategory(explicit);
  return normalizeFailureCategory(row.last_error_category ?? row.error_category ?? row.reason_category ?? "");
}

/** 从任意失败项对象读人话原因(仅新字段;旧字段不冒充人话)。 */
export function failureReasonHumanOf(source: unknown): string {
  const row = (source && typeof source === "object" ? source : {}) as Record<string, unknown>;
  return String(row.failure_reason_human ?? "").trim();
}

const EN_REASON: Record<FailureCategory, string> = {
  download: "The video could not be downloaded for analysis.",
  authorization: "This request was not authorised for the current account.",
  budget: "The analysis budget for this period is used up.",
  model: "The analysis model did not return a usable result.",
  provider: "The upstream data source was temporarily unavailable.",
  unknown: "The analysis did not finish; the cause has not been classified yet.",
};

const ZH_REASON: Record<FailureCategory, string> = {
  download: "视频下载失败,无法进入分析",
  authorization: "当前账号无权发起这项分析",
  budget: "本期分析预算已用完",
  model: "分析模型未返回可用结果",
  provider: "上游数据源暂时不可用",
  unknown: "分析未完成,原因尚未归类",
};

/** 中文 locale:优先后端人话;缺失时按类别兜底。英文 locale:按类别映射英文短句(后端只给中文)。 */
export function failureReasonForLocale(source: unknown, lang: "zh" | "en" | string): string {
  const category = failureCategoryOf(source);
  if (lang === "en") return EN_REASON[category];
  return failureReasonHumanOf(source) || ZH_REASON[category];
}

const GUIDANCE: Record<FailureCategory, { hint: string; action: FailureAction; actionLabel: string }> = {
  authorization: { hint: "请由收藏负责人在 MY KOL 重新发起", action: "reissue_from_my_kol", actionLabel: "从 MY KOL 重新发起" },
  budget: { hint: "预算恢复后可再次发起;不会自动重试", action: "check_budget", actionLabel: "" },
  download: { hint: "稍后自动重试,无需手动操作", action: "auto_retry", actionLabel: "" },
  provider: { hint: "稍后自动重试,无需手动操作", action: "auto_retry", actionLabel: "" },
  model: { hint: "已记录,可稍后重新发起", action: null, actionLabel: "" },
  unknown: { hint: "可稍后重新发起;持续失败请反馈", action: null, actionLabel: "" },
};

/** 是否为「失败/阻断」终态(任务态口径,多家族共用)。 */
export function isFailureStatus(status: unknown): boolean {
  const text = String(status ?? "").trim().toLowerCase();
  return ["failed", "blocked", "error", "triage", "timeout", "cancelled", "canceled", "prefilter_rejected"].includes(text);
}

/** 有新字段(类别或人话)才算「失败可读」;旧数据不硬编故事。 */
export function hasReadableFailure(source: unknown): boolean {
  const row = (source && typeof source === "object" ? source : {}) as Record<string, unknown>;
  return Boolean(String(row.failure_category ?? "").trim() || String(row.failure_reason_human ?? "").trim());
}

export function failureGuidance(source: unknown, lang: "zh" | "en" | string = "zh"): FailureGuidanceCopy {
  const category = failureCategoryOf(source);
  const guidance = GUIDANCE[category];
  return {
    category,
    reason: failureReasonForLocale(source, lang),
    hint: guidance.hint,
    action: guidance.action,
    actionLabel: guidance.actionLabel,
  };
}

/* ============ ETA 新口径 ============ */

/** 只认 eta_seconds;非有限数 / <=0 → null(不显示)。 */
export function etaSecondsOf(source: unknown): number | null {
  const row = (source && typeof source === "object" ? source : {}) as Record<string, unknown>;
  if (!Object.prototype.hasOwnProperty.call(row, "eta_seconds")) return null;
  const value = Number(row.eta_seconds);
  if (!Number.isFinite(value) || value <= 0) return null;
  return value;
}

/** 人性化 ETA:<60s 秒,<60min 分钟,其余小时;返回中文源串(t() 可译)。null → ""。 */
export function etaLabel(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 60) return `约 ${Math.max(1, Math.round(seconds))} 秒`;
  if (seconds < 3600) return `约 ${Math.max(1, Math.round(seconds / 60))} 分钟`;
  const hours = seconds / 3600;
  return hours < 10 ? `约 ${hours.toFixed(1).replace(/\.0$/, "")} 小时` : `约 ${Math.round(hours)} 小时`;
}

/** 英文 locale 的 ETA 文案(同口径)。 */
export function etaLabelEn(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 60) return `~${Math.max(1, Math.round(seconds))} s`;
  if (seconds < 3600) return `~${Math.max(1, Math.round(seconds / 60))} min`;
  const hours = seconds / 3600;
  return hours < 10 ? `~${hours.toFixed(1).replace(/\.0$/, "")} h` : `~${Math.round(hours)} h`;
}

export function etaLabelForLocale(seconds: number | null | undefined, lang: "zh" | "en" | string): string {
  return lang === "en" ? etaLabelEn(seconds) : etaLabel(seconds);
}
