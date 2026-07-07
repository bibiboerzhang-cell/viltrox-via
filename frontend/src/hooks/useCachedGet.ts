// 车道A(2026-07-07):keepPreviousData 取数 hook(裹 lib/apiCache 的 cachedApiFetch)。
//   首载:loading=true → 数据到达替换;
//   重挂/切回 tab:缓存有值立即返回(loading=false),过期则 refreshing=true 后台刷新,
//   新数据到达原地替换 —— 不再卸载重挂就骨架闪一遍。
import { useEffect, useState } from "react";
import { cachedApiFetch, peekApiCache, subscribeApiCache } from "../lib/apiCache";

export interface CachedGetState<T> {
  data: T | null;
  loading: boolean;
  refreshing: boolean;
  error: string;
}

export function useCachedGet<T>(
  path: string,
  opts: { ttl?: number; token?: string; timeoutMs?: number; enabled?: boolean } = {},
): CachedGetState<T> {
  const { ttl, token, timeoutMs, enabled = true } = opts;
  const [state, setState] = useState<CachedGetState<T>>(() => {
    const hit = enabled ? peekApiCache<T>(path, token) : null;
    return hit
      ? { data: hit.value, loading: false, refreshing: hit.stale, error: "" }
      : { data: null, loading: enabled, refreshing: false, error: "" };
  });

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const hit = peekApiCache<T>(path, token);
    setState(hit
      ? { data: hit.value, loading: false, refreshing: hit.stale, error: "" }
      : { data: null, loading: true, refreshing: false, error: "" });
    // SWR 后台刷新完成 → 原地替换数据并收尾 refreshing(失败时缓存层回放旧值,同样收尾)。
    const unsubscribe = subscribeApiCache<T>(path, (value) => {
      if (alive) setState({ data: value, loading: false, refreshing: false, error: "" });
    }, token);
    cachedApiFetch<T>(path, { ttlMs: ttl, timeoutMs }, token)
      .then((value) => {
        if (!alive) return;
        // stale 命中时 cachedApiFetch 先回旧值、后台仍在刷新 → refreshing 交给订阅回调收尾。
        const now = peekApiCache<T>(path, token);
        setState({ data: value, loading: false, refreshing: Boolean(now && now.stale), error: "" });
      })
      .catch((err) => {
        if (!alive) return;
        setState((prev) => ({
          data: prev.data,
          loading: false,
          refreshing: false,
          error: err instanceof Error ? err.message : "请求失败",
        }));
      });
    return () => {
      alive = false;
      unsubscribe();
    };
  }, [path, token, ttl, timeoutMs, enabled]);

  return state;
}
