// SmartKolInputPanel 纯函数工具(从 SmartKolInputPanel.tsx 抽出,行为不变)。
// 零 React / 零域类型依赖:格式化、URL 解析、模式探测、会话状态判定。主组件 import 回去,调用点不变。
// 红线:纯展示/解析工具,零触 viltrox_fit_score。

export type Mode = "idle" | "url" | "text";
export type Row = Record<string, unknown>;

export function cleanText(value: unknown): string {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

export function asRecord(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

// YouTube embed:复用 KOLDetailDrawer 的 youtube-nocookie 格式(B:视频结果区可播放)。
export function youtubeEmbedUrl(videoId: string): string {
  const id = String(videoId || "").trim();
  if (!id) return "";
  const origin = typeof window !== "undefined" && window.location?.origin
    ? `&origin=${encodeURIComponent(window.location.origin)}`
    : "";
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?rel=0&playsinline=1&modestbranding=1${origin}`;
}

export function display(value: unknown, fallback = "--"): string {
  const text = cleanText(value);
  return text || fallback;
}

export function numberLabel(value: unknown): string {
  const next = Number(value);
  if (!Number.isFinite(next) || next <= 0) return "";
  if (next >= 1_000_000) return `${(next / 1_000_000).toFixed(1)}M`;
  if (next >= 10_000) return `${Math.round(next / 1_000)}K`;
  if (next >= 1_000) return `${(next / 1_000).toFixed(1)}K`;
  return String(Math.round(next));
}

export function durationLabel(value: unknown): string {
  const ms = Number(value);
  if (!Number.isFinite(ms) || ms <= 0) return "";
  const totalSeconds = Math.max(1, Math.round(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function detectMode(input: string): Mode {
  const value = cleanText(input);
  if (!value) return "idle";
  try {
    const parsed = new URL(value.includes("://") ? value : `https://${value}`);
    const supportedProtocol = parsed.protocol === "http:" || parsed.protocol === "https:";
    return supportedProtocol && parsed.hostname.includes(".") ? "url" : "text";
  } catch {
    return "text";
  }
}

export function sessionIdFrom(value: unknown): number | undefined {
  const record = asRecord(value);
  const raw = record.session_id ?? record.id;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

export function terminalSessionStatus(value: unknown): boolean {
  const status = cleanText(value).toLowerCase();
  return ["ready", "partial", "failed", "done", "blocked", "cancelled", "canceled"].includes(status);
}

export function actionDescription(value: unknown): string {
  if (typeof value === "string") return value;
  const record = asRecord(value);
  return cleanText(record.description || record.label || record.code);
}

export function urlTypeLabel(value: unknown): string {
  const text = cleanText(value);
  if (text === "profile") return "Profile URL";
  if (text === "video") return "Video URL";
  if (text === "unknown") return "Unknown URL";
  return text || "--";
}

export function videoExecutionDone(status: unknown): boolean {
  return ["queued", "already_queued", "already_analyzed", "ready", "partial"].includes(cleanText(status));
}

export type ProfileUiState = "idle" | "queued" | "running" | "ready" | "partial" | "failed";

export function profileUiState(rawValue: unknown, isExecuting: boolean): ProfileUiState {
  const raw = cleanText(rawValue).toLowerCase();
  if (["ready", "already_ready", "already_fresh", "done", "executed"].includes(raw)) return "ready";
  if (raw.includes("failed") || ["error", "timeout", "unsupported", "provider_error"].includes(raw)) return "failed";
  if (["queued", "already_queued", "pending"].includes(raw)) return "queued";
  if (["running", "processing", "retrying", "in_progress"].includes(raw)) return "running";
  if (["partial", "degraded"].includes(raw)) return "partial";
  return isExecuting ? "running" : "idle";
}

// profile 自动 execute 的「已入库」只认后端 ready;queued/running/partial 分别告知,失败才给重试兜底。
export function profileStatusCopyFor(profileState: ProfileUiState): { label: string; title: string } {
  const presentation: Record<ProfileUiState, { label: string; title: string }> = {
    idle: { label: "等待资料抓取状态", title: "后端尚未返回可确认的抓取结果" },
    queued: { label: "资料抓取已排队", title: "任务已进入队列，尚未宣称资料已入库" },
    running: { label: "资料抓取进行中...", title: "worker 正在抓取，完成后才标记入库" },
    ready: { label: "资料已抓取并入库", title: "后端已明确返回 ready" },
    partial: { label: "资料部分完成，等待补齐", title: "部分资料可用，但抓取尚未全部完成" },
    failed: { label: "资料抓取失败", title: "后端返回失败，可手动重试" },
  };
  return presentation[profileState];
}

/* ============ F5 诚实状态:「数据源暂不可用」必须带原因 ============ */

const PROVIDER_GATE_REASON_LABELS: Record<string, string> = {
  budget: "预算闸",
  budget_exhausted: "预算已用尽",
  budget_denied: "预算闸拒绝",
  not_configured: "未配置",
  provider_not_configured: "未配置",
  missing_token: "未配置凭据",
  disabled: "已关闭",
  operator_disabled: "运营开关关闭",
  rate_limited: "限流中",
  provider_429: "限流中",
  provider_5xx: "上游异常",
  timeout: "上游超时",
  unsupported_platform: "平台不支持",
};

/** 从合同/诊断记录里找 provider_gate_reason(或同义键);没有就空串。 */
export function providerGateReasonOf(...records: unknown[]): string {
  for (const raw of records) {
    const row = asRecord(raw);
    const direct = cleanText(
      row.provider_gate_reason
      ?? asRecord(row.provider_gate).reason
      ?? asRecord(row.diagnostics).provider_gate_reason
      ?? asRecord(row.provider_status).reason,
    );
    if (direct) return direct;
  }
  return "";
}

/** 「数据源暂不可用」文案:有原因带原因,没有就说明「未就绪(配置/预算)」——绝不写假排队。 */
export function providerUnavailableLabel(reason: string): string {
  const key = cleanText(reason);
  if (!key) return "数据源未就绪(配置/预算)";
  const label = PROVIDER_GATE_REASON_LABELS[key.toLowerCase()] || key;
  return `数据源暂不可用(${label})`;
}

