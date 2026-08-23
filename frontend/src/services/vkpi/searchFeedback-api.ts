// F4 最小标注(优化波 B · L→F 契约,冻结):
//   POST /api/vkpi/recommendations/search-feedback
//   body {source:"discovery_wall"|"kol_detail", kol_pool_id, session_item_id?, verdict:"up"|"down",
//         reason?: "not_relevant"|"wrong_region"|"too_small"|"brand_official"|"duplicate"|"other"}
//   → 幂等落 vkpi_recommendation_feedback,返回 {ok, feedback_id}。
// 本地乐观态 + 去重:同一 (source, kol_pool_id) 只保留最后一次判定;提交中不重复发;失败回滚。
// 外部 store(useSyncExternalStore):发现墙卡片 / KOL 详情头 / 搜索质量卡都能订阅同一份标注计数,
// 不靠父层 prop 透传,也不因标注态变化重渲染整面结果墙(selector 只取自己那条)。

import { useSyncExternalStore } from "react";

import { apiFetch, jsonBody } from "../http";

export type SearchFeedbackSource = "discovery_wall" | "kol_detail";
export type SearchFeedbackVerdict = "up" | "down";
export type SearchFeedbackReason = "not_relevant" | "wrong_region" | "too_small" | "brand_official" | "duplicate" | "other";

export const SEARCH_FEEDBACK_REASONS: ReadonlyArray<{ key: SearchFeedbackReason; label: string }> = [
  { key: "not_relevant", label: "内容不相关" },
  { key: "wrong_region", label: "地区不对" },
  { key: "too_small", label: "体量太小" },
  { key: "brand_official", label: "品牌官方账号" },
  { key: "duplicate", label: "重复" },
  { key: "other", label: "其他" },
];

export interface SearchFeedbackPayload {
  source: SearchFeedbackSource;
  kol_pool_id: number;
  session_item_id?: number;
  verdict: SearchFeedbackVerdict;
  reason?: SearchFeedbackReason;
}

export interface SearchFeedbackResponse {
  ok?: boolean;
  feedback_id?: number | string | null;
  /** 服务端若回当前会话/全局已标注数,质量卡直接用;缺席则用本地计数。 */
  labeled_count?: number | null;
}

const PRIMARY_PATH = "/api/vkpi/recommendations/search-feedback";
// 仓库所有 vkpi 路由家族都挂 /api/admin/vkpi;契约写的是 /api/vkpi。主路径 404 时回退一次,免得两边口径
// 差一个前缀就把标注全吞掉(回退只对 404,其余错误照实抛)。
const FALLBACK_PATH = "/api/admin/vkpi/recommendations/search-feedback";

function isNotFound(error: unknown): boolean {
  const status = Number((error as { status?: unknown })?.status);
  if (status === 404) return true;
  return /\b404\b/.test(String((error as { message?: unknown })?.message || ""));
}

export async function submitSearchFeedback(token: string, payload: SearchFeedbackPayload): Promise<SearchFeedbackResponse> {
  const body = jsonBody({
    source: payload.source,
    kol_pool_id: payload.kol_pool_id,
    ...(payload.session_item_id ? { session_item_id: payload.session_item_id } : {}),
    verdict: payload.verdict,
    ...(payload.verdict === "down" && payload.reason ? { reason: payload.reason } : {}),
  });
  try {
    return await apiFetch<SearchFeedbackResponse>(PRIMARY_PATH, { method: "POST", body }, token);
  } catch (error) {
    if (!isNotFound(error)) throw error;
    return apiFetch<SearchFeedbackResponse>(FALLBACK_PATH, { method: "POST", body }, token);
  }
}

/* ============ 本地乐观态 store ============ */

export interface SearchFeedbackEntry {
  verdict: SearchFeedbackVerdict;
  reason?: SearchFeedbackReason;
  status: "pending" | "saved" | "error";
  feedback_id?: number | string | null;
  error?: string;
}

export interface SearchFeedbackSnapshot {
  entries: Readonly<Record<string, SearchFeedbackEntry>>;
  /** 服务端最近一次回报的已标注数(缺席 null) */
  serverLabeledCount: number | null;
}

type Listener = () => void;

let snapshot: SearchFeedbackSnapshot = { entries: {}, serverLabeledCount: null };
const listeners = new Set<Listener>();
const inFlight = new Map<string, Promise<SearchFeedbackEntry>>();

export function searchFeedbackKey(source: SearchFeedbackSource, kolPoolId: number | string): string {
  return `${source}:${String(kolPoolId)}`;
}

function emit(next: SearchFeedbackSnapshot) {
  snapshot = next;
  listeners.forEach((listener) => listener());
}

function setEntry(key: string, entry: SearchFeedbackEntry | null, serverLabeledCount?: number | null) {
  const entries = { ...snapshot.entries };
  if (entry) entries[key] = entry;
  else delete entries[key];
  emit({
    entries,
    serverLabeledCount: serverLabeledCount === undefined ? snapshot.serverLabeledCount : serverLabeledCount,
  });
}

export function subscribeSearchFeedback(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getSearchFeedbackSnapshot(): SearchFeedbackSnapshot {
  return snapshot;
}

/** 测试/登出用:清空本地标注态。 */
export function resetSearchFeedbackStore(): void {
  inFlight.clear();
  emit({ entries: {}, serverLabeledCount: null });
}

/** 已标注数:服务端给了就用服务端;否则数本地已保存条数(pending 也算,失败不算)。 */
export function labeledCountOf(state: SearchFeedbackSnapshot): number {
  if (state.serverLabeledCount != null && Number.isFinite(state.serverLabeledCount)) return Math.max(0, state.serverLabeledCount);
  return Object.values(state.entries).filter((entry) => entry.status !== "error").length;
}

/**
 * 乐观提交 + 去重:
 *  - 同 key 同 verdict 同 reason 且已保存/提交中 → 直接返回现有(不重复打接口);
 *  - 否则先写 pending 乐观态,成功写 saved(+feedback_id),失败写 error 并保留上一次已保存态(若有)。
 */
export function recordSearchFeedback(token: string, payload: SearchFeedbackPayload): Promise<SearchFeedbackEntry> {
  const key = searchFeedbackKey(payload.source, payload.kol_pool_id);
  const current = snapshot.entries[key];
  const same = current
    && current.verdict === payload.verdict
    && (current.reason || undefined) === (payload.reason || undefined)
    && current.status !== "error";
  if (same) {
    const pending = inFlight.get(key);
    return pending ?? Promise.resolve(current);
  }
  if (!token) {
    const entry: SearchFeedbackEntry = { verdict: payload.verdict, reason: payload.reason, status: "error", error: "未登录" };
    setEntry(key, entry);
    return Promise.resolve(entry);
  }
  const previous = current && current.status === "saved" ? current : null;
  setEntry(key, { verdict: payload.verdict, reason: payload.reason, status: "pending" });
  const request = submitSearchFeedback(token, payload)
    .then((response) => {
      const entry: SearchFeedbackEntry = {
        verdict: payload.verdict,
        reason: payload.reason,
        status: "saved",
        feedback_id: response?.feedback_id ?? null,
      };
      const serverCount = response?.labeled_count;
      setEntry(key, entry, serverCount == null ? undefined : Number(serverCount));
      return entry;
    })
    .catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error || "提交失败");
      // 失败回滚到上一次已保存态(若有),否则保留 error 态让用户重试。
      const entry: SearchFeedbackEntry = previous
        ? { ...previous }
        : { verdict: payload.verdict, reason: payload.reason, status: "error", error: message };
      setEntry(key, entry);
      return entry;
    })
    .finally(() => {
      if (inFlight.get(key) === request) inFlight.delete(key);
    });
  inFlight.set(key, request);
  return request;
}

/** 订阅单条标注(selector 只取自己那条;别的卡变化不触发重渲染)。 */
export function useSearchFeedbackEntry(source: SearchFeedbackSource, kolPoolId: number | string | null | undefined): SearchFeedbackEntry | null {
  const key = kolPoolId ? searchFeedbackKey(source, kolPoolId) : "";
  return useSyncExternalStore(
    subscribeSearchFeedback,
    () => (key ? snapshot.entries[key] ?? null : null),
    () => null,
  );
}

/** 订阅已标注数(搜索质量卡)。 */
export function useSearchFeedbackLabeledCount(): number {
  return useSyncExternalStore(
    subscribeSearchFeedback,
    () => labeledCountOf(snapshot),
    () => 0,
  );
}
