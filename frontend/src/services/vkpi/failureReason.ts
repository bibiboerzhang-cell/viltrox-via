// 失败可读 + ETA 新口径(优化波 B · F3/F7;纯函数,零网络)。
//   契约(O→F,冻结):账号级进度 / my-kol videos / 进度中心的每个失败项多两字段
//     failure_category: download | authorization | budget | model | provider | unknown
//     failure_reason_human: 中文一句话(后端已人话化;英文 locale 用 category 映射的英文短句)
//   ETA 只认 eta_seconds(新口径 = 活跃车道数 × 队列位置 × p50);旧 estimated_remaining_seconds
//   不再当 ETA 渲染;字段缺失 → 不显示(绝不编排队文案)。
//   门面禁内部词:这里产出的文案不得出现 job/apify/provider/final_v1 等词。

export type FailureCategory = "download" | "authorization" | "budget" | "model" | "provider" | "unknown";

export type FailureAction = "reissue_from_my_kol" | "check_budget" | "auto_retry" | null;

/** 后端封闭动作码(failure_next_step / stage reason.next_step):回答「下一步能做什么」。 */
export type FailureNextStep =
  | "retry"
  | "switch_source"
  | "check_budget"
  | "reissue_from_my_kol"
  | "wait_auto_retry"
  | "none";

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

/** 后端稳定机器码 → 英文一句(后端只给中文;类别兜底对档案/评论/受众三段太粗)。 */
const EN_BY_CODE: ReadonlyArray<[RegExp, string]> = [
  [/url_unknown_unsupported|unsupported_or_unresolved_url/, "This profile link cannot be crawled automatically; only YouTube, Instagram and TikTok profiles are supported."],
  [/cn_platform_video_only/, "This platform can only be analysed one post at a time; a profile link will not work."],
  [/url_unknown_needs_human_choice/, "This link points to more than one account; someone needs to confirm which one."],
  [/unsupported_platform/, "This platform does not support this analysis yet; try a different account or link."],
  [/non_video_post|image_post_no_video|no_downloadable_url/, "This post contains no video, so there is nothing to analyse."],
  [/no_commenters|no_comments\b/, "These posts have no usable comments."],
  [/no_posts/, "No posts have been collected for this account yet; run an account analysis first."],
  [/comments_job_not_ready|pending_comments/, "Comments are still being collected; this will continue automatically."],
  [/comments_collect_failed/, "Comments could not be collected for this account; you can try again."],
  [/deep_crawl_not_executed|profile_crawl_not_executed/, "The account analysis never actually started; you can try again."],
  [/insufficient_evidence|no_ready_video_analysis|content_fit_not_ready/, "There is not enough material yet; add a video analysis first."],
];

function failureCodeOf(source: unknown): string {
  const row = (source && typeof source === "object" ? source : {}) as Record<string, unknown>;
  return String(row.failure_code ?? "").trim().toLowerCase();
}

/** 中文 locale:优先后端人话;缺失时按类别兜底。英文 locale:先按机器码,再按类别(后端只给中文)。 */
export function failureReasonForLocale(source: unknown, lang: "zh" | "en" | string): string {
  const category = failureCategoryOf(source);
  if (lang === "en") {
    const code = failureCodeOf(source);
    const matched = code ? EN_BY_CODE.find(([pattern]) => pattern.test(code)) : undefined;
    return matched ? matched[1] : EN_REASON[category];
  }
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

const NEXT_STEPS: ReadonlySet<string> = new Set([
  "retry", "switch_source", "check_budget", "reissue_from_my_kol", "wait_auto_retry", "none",
]);

/** 封闭词表之外一律 null(绝不把自由文本当动作码渲染)。 */
export function normalizeFailureNextStep(raw: unknown): FailureNextStep | null {
  const text = String(raw ?? "").trim().toLowerCase();
  return NEXT_STEPS.has(text) ? (text as FailureNextStep) : null;
}

/** 从失败项 / stage reason 读下一步动作码;缺失或不认识 → null。 */
export function failureNextStepOf(source: unknown): FailureNextStep | null {
  const row = (source && typeof source === "object" ? source : {}) as Record<string, unknown>;
  return normalizeFailureNextStep(row.failure_next_step ?? row.next_step);
}

/** 「本段对这个对象本就不适用」(链接不支持 / 内容里没有视频 / 帖子下没评论);重试也不会变。 */
export function failureNotApplicableOf(source: unknown): boolean {
  const row = (source && typeof source === "object" ? source : {}) as Record<string, unknown>;
  return row.failure_not_applicable === true;
}

/** 动作码 → (既有 FailureAction, 中文提示, 英文提示)。FailureAction 不扩集:
 *  failureGuidance.tsx 的 TONE 是穷举 Record,扩集会让它编译不过,而那个文件不在本刀名下。
 *  没有对应按钮的动作只出提示 —— 宁可无按钮,不给假按钮。 */
const NEXT_STEP_GUIDANCE: Record<FailureNextStep, { action: FailureAction; zh: string; en: string }> = {
  retry: { action: null, zh: "可以再试一次", en: "You can try again." },
  switch_source: { action: null, zh: "换一个支持的账号主页链接再试", en: "Try a different supported profile link." },
  check_budget: { action: "check_budget", zh: "额度恢复后可再次发起;不会自动重试", en: "Retry once the allowance resets; this will not retry itself." },
  reissue_from_my_kol: { action: "reissue_from_my_kol", zh: "请由收藏负责人在 MY KOL 重新发起", en: "The owner needs to re-issue this from MY KOL." },
  wait_auto_retry: { action: "auto_retry", zh: "稍后自动继续,无需手动操作", en: "This continues automatically; nothing to do." },
  none: { action: null, zh: "这一条本来就没有可分析的内容,不用重试", en: "There is nothing to analyse here; retrying will not help." },
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
  const nextStep = failureNextStepOf(source);
  // 后端给了明确的下一步就照它走(类别只说"哪一类坏了",动作码才说"你现在能做什么");
  // 没给就退回按类别的老口径,老数据一字不变。
  if (!nextStep) {
    return { category, reason: failureReasonForLocale(source, lang), hint: guidance.hint, action: guidance.action, actionLabel: guidance.actionLabel };
  }
  const step = NEXT_STEP_GUIDANCE[nextStep];
  return {
    category,
    reason: failureReasonForLocale(source, lang),
    // t() 对未登记的 key 原样返回,所以英文 locale 直接给英文串,避免门面漏中文。
    hint: lang === "en" ? step.en : step.zh,
    action: step.action,
    actionLabel: step.action === "reissue_from_my_kol" ? guidance.actionLabel || "从 MY KOL 重新发起" : "",
  };
}

/* ============ 段级「为什么没有数据」 ============ */

/** 后端 stage reason 的封闭词表(search_progress_projection.STAGE_NOT_REQUESTED_REASONS)。 */
export type StageNotRequestedReason = "no_candidates" | "upstream_incomplete" | "stage_not_selected";

const NOT_REQUESTED_EN: Record<StageNotRequestedReason, string> = {
  no_candidates: "This search returned no accounts, so there is nothing to analyse here — widen the filters and search again.",
  upstream_incomplete: "The account details are not complete yet; this part starts once they are.",
  stage_not_selected: "This part was not selected for this run; it can be added separately.",
};

/** 一段 tile 的原因文案:失败段走人话/英文短句,未请求段走封闭词表。没有原因 → ""(诚实空态)。 */
export function stageReasonForLocale(reason: unknown, lang: "zh" | "en" | string = "zh"): string {
  const row = (reason && typeof reason === "object" ? reason : {}) as Record<string, unknown>;
  const notRequested = String(row.not_requested_reason ?? "").trim();
  if (notRequested) {
    if (lang !== "en") return String(row.human ?? "").trim();
    return NOT_REQUESTED_EN[notRequested as StageNotRequestedReason] ?? "";
  }
  if (!hasReadableFailure(row)) return "";
  return failureReasonForLocale(row, lang);
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
