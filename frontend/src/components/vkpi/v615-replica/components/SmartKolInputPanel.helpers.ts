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
