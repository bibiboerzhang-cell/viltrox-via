// 车道A(2026-07-07):GET 请求内存缓存层。目标是「切 tab 组件重挂不再全量重拉 + 并发去重」:
//   ① in-flight 去重:同 URL(+token)并发只发一次,大家等同一个 Promise;
//   ② 内存 TTL 缓存:默认 45s,可按调用传 ttlMs;
//   ③ stale-while-revalidate:命中过期缓存先回旧值,后台刷新完成经 subscribeApiCache 通知;
//   ④ clearApiCache():登出/身份切换全清(useAuth.clearLocalSession / logoutCockpit 已接线);
//   ⑤ 只缓存 GET;非 GET、显式 ttlMs<=0、带外部 AbortSignal(中止语义无法跨调用方共享)一律直透。
// 注意:底层走 services/http 的 apiFetch(而非 lib/api 直连)—— 与全库 api 层同一 mock seam,
// 既有单测 vi.mock("../http") / vi.mock("services/http") 能同时拦到缓存层的真实取数。
import { apiFetch } from "../services/http";

const DEFAULT_TTL_MS = 45_000;

export type CachedFetchInit = RequestInit & {
  timeoutMs?: number;
  /** 缓存 TTL(毫秒),默认 45s;<=0 视为关缓存直透。 */
  ttlMs?: number;
  /** true = 跳过缓存读强制取新(仍去重并发、仍写缓存)。手动「刷新」/写后重拉/轮询保鲜用。 */
  forceRefresh?: boolean;
};

interface CacheEntry {
  value: unknown;
  savedAt: number;
  ttlMs: number;
}

type Listener = (value: unknown) => void;

const cacheStore = new Map<string, CacheEntry>();
const inflightStore = new Map<string, Promise<unknown>>();
const listenerStore = new Map<string, Set<Listener>>();

function cacheKey(path: string, token?: string): string {
  // token 进 key:不同身份绝不共享缓存(登出/切号另有 clearApiCache 兜底)。
  return `${token || ""}::${path}`;
}

function isGetRequest(init: RequestInit): boolean {
  return !init.method || String(init.method).toUpperCase() === "GET";
}

/** 同步窥视缓存(不发请求):命中回 {value, savedAt, stale},未命中 null。keepPreviousData 首帧用。 */
export function peekApiCache<T>(
  path: string,
  token?: string,
): { value: T; savedAt: number; stale: boolean } | null {
  const entry = cacheStore.get(cacheKey(path, token));
  if (!entry) return null;
  return {
    value: entry.value as T,
    savedAt: entry.savedAt,
    stale: Date.now() - entry.savedAt > entry.ttlMs,
  };
}

/** 订阅某 path 的缓存更新(SWR 后台刷新完成时回调新值);返回退订函数。 */
export function subscribeApiCache<T>(path: string, listener: (value: T) => void, token?: string): () => void {
  const key = cacheKey(path, token);
  let set = listenerStore.get(key);
  if (!set) {
    set = new Set();
    listenerStore.set(key, set);
  }
  const wrapped = listener as Listener;
  set.add(wrapped);
  return () => {
    const current = listenerStore.get(key);
    if (!current) return;
    current.delete(wrapped);
    if (current.size === 0) listenerStore.delete(key);
  };
}

function notifyListeners(key: string, value: unknown) {
  const set = listenerStore.get(key);
  if (!set) return;
  for (const listener of Array.from(set)) {
    try {
      listener(value);
    } catch {
      // 订阅方回调异常不拖垮取数链。
    }
  }
}

/** 登出/身份切换全清。订阅关系保留(组件卸载自行退订),不会向订阅方回放已清数据。 */
export function clearApiCache() {
  cacheStore.clear();
  inflightStore.clear();
}

function startRequest<T>(
  key: string,
  path: string,
  init: RequestInit & { timeoutMs?: number },
  token: string | undefined,
  ttlMs: number,
): Promise<T> {
  const existing = inflightStore.get(key);
  if (existing) return existing as Promise<T>;
  // 类型含 undefined:finally 里(异步执行时已赋值)读取 request,躲开 TS2454 的静态误报。
  let request: Promise<T> | undefined;
  request = (async () => {
    try {
      const value = await apiFetch<T>(path, init, token);
      cacheStore.set(key, { value, savedAt: Date.now(), ttlMs });
      notifyListeners(key, value);
      return value;
    } catch (error) {
      // SWR 后台刷新失败:回放旧值让订阅方结束 refreshing 态(数据不变,不假装成功);
      // 首次取数(无旧值)则正常向调用方抛错。
      const staleEntry = cacheStore.get(key);
      if (staleEntry) notifyListeners(key, staleEntry.value);
      throw error;
    } finally {
      if (inflightStore.get(key) === request) inflightStore.delete(key);
    }
  })();
  inflightStore.set(key, request);
  return request;
}

/**
 * 带缓存的 apiFetch(签名对齐 apiFetch,多 ttlMs/forceRefresh 两个开关)。
 * 只缓存 GET;POST 等写操作原样直透,永不缓存。
 */
export async function cachedApiFetch<T>(path: string, init: CachedFetchInit = {}, token?: string): Promise<T> {
  const { ttlMs = DEFAULT_TTL_MS, forceRefresh = false, ...requestInit } = init;
  if (!isGetRequest(requestInit) || ttlMs <= 0 || requestInit.signal) {
    return apiFetch<T>(path, requestInit, token);
  }
  const key = cacheKey(path, token);
  if (!forceRefresh) {
    const entry = cacheStore.get(key);
    if (entry) {
      const age = Date.now() - entry.savedAt;
      if (age <= ttlMs) return entry.value as T;
      // stale-while-revalidate:先回旧值不阻塞渲染;后台刷新完成经 subscribeApiCache 通知。
      startRequest<T>(key, path, requestInit, token, ttlMs).catch(() => {});
      return entry.value as T;
    }
  }
  return startRequest<T>(key, path, requestInit, token, ttlMs);
}
