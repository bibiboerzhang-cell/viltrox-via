// Ask ⌘K「最近」本机留痕:localStorage vkpi:ask-recent ≤10 条,按 (kind,id) 去重,最新在前。
// 只存候选契约本身(label/detail/action),不存任何结果数据。

import type { AskCandidate } from "./askGrammar";

export const ASK_RECENT_STORAGE_KEY = "vkpi:ask-recent";
export const ASK_RECENT_LIMIT = 10;

export interface AskRecentEntry {
  kind: AskCandidate["kind"];
  id: string;
  label: string;
  detail: string;
  action: AskCandidate["action"];
  at: number;
}

function storage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

function isEntry(value: unknown): value is AskRecentEntry {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  return typeof row.id === "string" && typeof row.label === "string" && typeof row.kind === "string"
    && Boolean(row.action) && typeof row.action === "object";
}

export function readAskRecent(): AskRecentEntry[] {
  const store = storage();
  if (!store) return [];
  try {
    const parsed = JSON.parse(store.getItem(ASK_RECENT_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter(isEntry).slice(0, ASK_RECENT_LIMIT) : [];
  } catch {
    return [];
  }
}

export function pushAskRecent(candidate: AskCandidate, now = Date.now()): AskRecentEntry[] {
  const kind = candidate.origin || candidate.kind;
  const id = candidate.id.startsWith("recent:") ? candidate.id.slice("recent:".length) : candidate.id;
  const entry: AskRecentEntry = {
    kind,
    id,
    label: candidate.label,
    detail: candidate.detail,
    action: candidate.action,
    at: now,
  };
  const next = [entry, ...readAskRecent().filter((item) => item.id !== id)].slice(0, ASK_RECENT_LIMIT);
  const store = storage();
  if (store) {
    try { store.setItem(ASK_RECENT_STORAGE_KEY, JSON.stringify(next)); } catch { /* 配额满/隐私模式:留痕失败不影响动作 */ }
  }
  return next;
}

export function recentCandidate(entry: AskRecentEntry): AskCandidate {
  return {
    kind: "recent",
    origin: entry.kind,
    id: `recent:${entry.id}`,
    label: entry.label,
    detail: entry.detail,
    action: entry.action,
  };
}
