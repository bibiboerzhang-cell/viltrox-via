import type {
  ProgressRecentDone,
  ProgressTask,
} from "../../../../services/vkpi/progressCenter-api";
import { humanizeLlmReason } from "../llmReasonCopy";

export type AnalysisTaskLike = Partial<ProgressTask & ProgressRecentDone>;

export type AnalysisTaskFamily =
  | "profile"
  | "video"
  | "comments"
  | "audience"
  | "fit"
  | "qa"
  | "advisor"
  | "llm"
  | "generic";

export interface AnalysisStageStep {
  stage: "queued" | "search" | "thinking" | "summarizing";
  label: string;
}

const FLOWS: Record<AnalysisTaskFamily, AnalysisStageStep[]> = {
  profile: [
    { stage: "queued", label: "队列" },
    { stage: "search", label: "账号抓取" },
    { stage: "thinking", label: "证据分析" },
    { stage: "summarizing", label: "档案入库" },
  ],
  video: [
    { stage: "queued", label: "队列" },
    { stage: "search", label: "媒体解析" },
    { stage: "thinking", label: "模型深析" },
    { stage: "summarizing", label: "分镜落库" },
  ],
  comments: [
    { stage: "queued", label: "队列" },
    { stage: "search", label: "评论采集" },
    { stage: "thinking", label: "情绪/主题" },
    { stage: "summarizing", label: "样本入库" },
  ],
  audience: [
    { stage: "queued", label: "队列" },
    { stage: "search", label: "评论样本" },
    { stage: "thinking", label: "受众推断" },
    { stage: "summarizing", label: "画像落库" },
  ],
  fit: [
    { stage: "queued", label: "队列" },
    { stage: "search", label: "证据聚合" },
    { stage: "thinking", label: "内容契合" },
    { stage: "summarizing", label: "结论落库" },
  ],
  qa: [
    { stage: "queued", label: "队列" },
    { stage: "search", label: "媒体抽帧" },
    { stage: "thinking", label: "模型复核" },
    { stage: "summarizing", label: "QA 落库" },
  ],
  advisor: [
    { stage: "queued", label: "请求准备" },
    { stage: "search", label: "知识检索" },
    { stage: "thinking", label: "顾问分析" },
    { stage: "summarizing", label: "会话留存" },
  ],
  llm: [
    { stage: "queued", label: "调用准备" },
    { stage: "search", label: "上下文读取" },
    { stage: "thinking", label: "模型分析" },
    { stage: "summarizing", label: "结果落库" },
  ],
  generic: [
    { stage: "queued", label: "队列中" },
    { stage: "search", label: "抓取" },
    { stage: "thinking", label: "分析" },
    { stage: "summarizing", label: "落库" },
  ],
};

const TASK_BINDING_LABELS: Record<string, string> = {
  audit_pre_filter: "提报预筛",
  audit_video_analysis: "视频 AI 分析",
  audit_vision_fallback: "视觉回退分析",
  audit_deep_score: "深度评分",
  deepsight_strategy: "策略洞察",
  deepsight_market_empath: "市场共情",
  deepsight_opportunity: "机会洞察",
  via_chat: "AI 顾问对话",
  via_persona_summary: "用户记忆摘要",
  kol_audience_analysis: "KOL 受众分析",
  kol_content_fit_analysis: "KOL 内容契合",
  kol_product_fit_reason: "KOL 产品推荐理由",
  kol_outreach_pack: "KOL 外联包",
};

/**
 * 门面口径:任务绑定只出已登记的人话标签。
 * 未登记的绑定不再把机器码原样上脸(红线2),回落成中性说法;
 * 原始 binding 值由调用方放进 title/溯源层,信息不丢。
 */
export function analysisTaskBindingLabel(task: AnalysisTaskLike): string {
  const binding = String(task.task_binding || "").trim();
  if (!binding) return "";
  return TASK_BINDING_LABELS[binding] || "其他分析任务";
}

/** 溯源层用:原始任务绑定标识(只进 title/详情,不上卡面)。 */
export function analysisTaskBindingTrace(task: AnalysisTaskLike): string {
  return String(task.task_binding || "").trim();
}

export function analysisTaskFamily(task: AnalysisTaskLike): AnalysisTaskFamily {
  const haystack = [task.kind, task.job_type, task.purpose]
    .map((value) => String(value || "").toLowerCase())
    .join(" ");
  if (/keyframe[_\s-]?qa|视频qa|qa复核|video[_\s-]?qa/.test(haystack)) return "qa";
  if (/marketing[_\s-]?advisor|营销顾问|advisor/.test(haystack)) return "advisor";
  if (/audience|受众/.test(haystack)) return "audience";
  if (/content[_\s-]?fit|内容契合/.test(haystack)) return "fit";
  if (/comment|评论|sentiment/.test(haystack)) return "comments";
  if (/profile[_\s-]?deep[_\s-]?crawl|账号分析|账号沉淀|account[_\s-]?dossier/.test(haystack)) return "profile";
  if (/final[_\s-]?v1|video[_\s-]?analysis|video深析|视频深析/.test(haystack)) return "video";
  if (["llm_calls", "llm_reservations"].includes(String(task.source || "")) || /llm|gemini|claude|openai|模型/.test(haystack)) return "llm";
  return "generic";
}

export function analysisStageFlow(
  task: AnalysisTaskLike,
  fallback: Array<{ stage: string; label: string }> = [],
): Array<{ stage: string; label: string }> {
  const family = analysisTaskFamily(task);
  if (family !== "generic") return FLOWS[family];
  return fallback.length ? fallback : FLOWS.generic;
}

export function analysisTerminalCopy(item: AnalysisTaskLike): {
  label: string;
  detail: string;
  tone: "ready" | "warn" | "failed";
} {
  const status = String(item.status || "").toLowerCase();
  const fallbackMode = String(item.fallback_mode || "");
  const rawReason = item.reason_code || item.error_category;
  // F3 失败可读:后端已给人话原因(failure_reason_human)时直接采用,不再从机器码猜。
  const humanReason = String(item.failure_reason_human || "").trim();
  if (status === "done" && fallbackMode === "provider_fallback") {
    return { label: "回退模型后完成", detail: "首选模型未形成结果，已由已登记回退模型完成。", tone: "warn" };
  }
  if (status === "done") return { label: "完成", detail: "", tone: "ready" };
  if (status === "retrying") {
    return { label: "等待重试", detail: "本轮尚未完成，任务仍在重试队列中。", tone: "warn" };
  }
  if (status === "blocked") {
    const reason = humanReason
      ? { message: humanReason, code: "" }
      : humanizeLlmReason(rawReason, "任务被就绪、预算或权限闸门阻止，未继续执行。");
    return {
      label: fallbackMode === "rule_v0" ? "已阻塞 · 规则回退" : "已阻塞",
      detail: fallbackMode === "rule_v0"
        ? `${reason.message} 仅保留规则降级输出，不冒充模型结论。`
        : reason.message,
      tone: "warn",
    };
  }
  if (status === "cancelled" || status === "canceled") {
    return { label: "已取消", detail: "任务已取消，未继续执行。", tone: "warn" };
  }
  if (status === "partial_done") {
    return { label: "部分完成", detail: "已有部分结果落库，其余步骤未完成。", tone: "warn" };
  }
  if (status === "prefilter_rejected") {
    return { label: "预筛未通过", detail: "任务未通过前置条件检查，没有进入正式分析。", tone: "warn" };
  }
  if (status === "triage") {
    return { label: "待人工排查", detail: "自动重试已停止，需要人工确认失败原因后再处理。", tone: "warn" };
  }
  if (status === "timeout") {
    const reason = humanizeLlmReason(rawReason || "timeout", "任务执行超时，可按策略重试。");
    return { label: "超时", detail: reason.message, tone: "failed" };
  }
  if (fallbackMode === "rule_v0") {
    return {
      label: "规则回退",
      detail: "生产模型未形成可用结果；仅保留规则降级输出，不冒充模型结论。",
      tone: "failed",
    };
  }
  const reason = humanReason
    ? { message: humanReason, code: "" }
    : humanizeLlmReason(rawReason, "任务未形成可用结果；可在任务中心查看并按策略重试。");
  return { label: "失败", detail: reason.message, tone: "failed" };
}

/**
 * 门面口径:只出「走的是哪条通道」这一层业务语义,不出厂商名与模型标识(红线2)。
 * 信息不丢——具体厂商/模型由 analysisProviderTrace 供给 title/溯源层。
 */
export function analysisChannelLabel(item: AnalysisTaskLike): string {
  const mode = String(item.fallback_mode || "").trim();
  if (mode === "provider_fallback") return "备用通道";
  if (mode === "rule_v0" || mode === "safe_fallback") return "规则降级";
  return String(item.provider || "").trim() ? "主通道" : "";
}

/** 溯源层用:原始厂商/模型标识(只进 title/详情,不上卡面)。 */
export function analysisProviderTrace(item: AnalysisTaskLike): string {
  const provider = String(item.provider || "").trim();
  const model = String(item.model || "").trim();
  return [provider && `服务 ${provider}`, model && `模型 ${model}`].filter(Boolean).join(" · ");
}
