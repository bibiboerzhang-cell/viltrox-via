// X3 顶栏全局搜索 API:GET /api/admin/vkpi/global-search?q=
// 一个关键词同时搜 KOL(名字/handle)/项目(名字)/活动(名字),后端三表各 LIKE 取 5 条。
// 只回名字/id 级轻字段;点进详情后由各自读端权限闸把关(取舍见后端 docstring)。

import { apiFetch } from "../http";
import { readSessionToken } from "../../lib/authCookieSession";

export interface GlobalSearchKol {
  id: number;
  platform: string | null;
  handle: string | null;
  display_name: string | null;
  avatar_url: string | null;
  followers: number | null;
}

export interface GlobalSearchProject {
  id: number;
  project_uid: string | null;
  project_name: string | null;
  stage: string | null;
  stage_status: string | null;
  platform: string | null;
}

export interface GlobalSearchEvent {
  id: string;
  title: string | null;
  status: string | null;
  start_date: string | null;
  end_date: string | null;
}

export type GlobalSearchSourceState = "ready" | "degraded" | "error" | "blocked";

export interface GlobalSearchSourceStatusItem {
  status: GlobalSearchSourceState;
  result_count: number;
  reason?: string;
}

export interface GlobalSearchSourceStatus {
  kols?: GlobalSearchSourceStatusItem;
  projects?: GlobalSearchSourceStatusItem;
  events?: GlobalSearchSourceStatusItem;
}

export interface GlobalSearchResult {
  q?: string;
  kols: GlobalSearchKol[];
  projects: GlobalSearchProject[];
  events: GlobalSearchEvent[];
  /** Per-source truth status. A ready source with zero rows is a real empty
   * result; degraded/error/blocked must never be presented as "no matches". */
  source_status?: GlobalSearchSourceStatus;
}

function sourceStatus(value: unknown): GlobalSearchSourceStatusItem | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const row = value as Record<string, unknown>;
  const status = String(row.status || "");
  if (!["ready", "degraded", "error", "blocked"].includes(status)) return undefined;
  const count = Number(row.result_count);
  return {
    status: status as GlobalSearchSourceState,
    result_count: Number.isFinite(count) ? Math.max(0, count) : 0,
    reason: typeof row.reason === "string" && row.reason.trim() ? row.reason : undefined,
  };
}

// S-02(2026-09-02):JS 不再持有 JWT,localStorage 里没有 token 可读。CockpitTopbar 这类
// 纯展示组件(不吃 apiToken prop)改从 lib/authCookieSession 读「当前会话占位 token」:
// 已登录 → COOKIE_SESSION_TOKEN(后端当作走 HttpOnly cookie),未登录 → 空串。函数名保留给既有引用方。
export function readStoredApiToken(): string {
  return readSessionToken();
}

/** 顶栏全局搜索:防抖后调用;signal 用于输入变化时取消上一发请求。 */
export async function globalSearch(
  q: string,
  opts: { token?: string; signal?: AbortSignal } = {},
): Promise<GlobalSearchResult> {
  const token = opts.token || readStoredApiToken();
  const res = await apiFetch<Partial<GlobalSearchResult>>(
    `/api/admin/vkpi/global-search?q=${encodeURIComponent(q)}`,
    { signal: opts.signal, timeoutMs: 10000 },
    token || undefined,
  );
  // 后端组级失败会回空数组,这里再兜一层非数组形态,前端渲染永不炸。
  return {
    q: typeof res?.q === "string" ? res.q : q,
    kols: Array.isArray(res?.kols) ? res.kols : [],
    projects: Array.isArray(res?.projects) ? res.projects : [],
    events: Array.isArray(res?.events) ? res.events : [],
    source_status: res?.source_status && typeof res.source_status === "object" && !Array.isArray(res.source_status)
      ? {
          kols: sourceStatus((res.source_status as Record<string, unknown>).kols),
          projects: sourceStatus((res.source_status as Record<string, unknown>).projects),
          events: sourceStatus((res.source_status as Record<string, unknown>).events),
        }
      : undefined,
  };
}
