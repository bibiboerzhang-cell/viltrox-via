import React from "react";

import {
  getMyKolRecentVideos,
  type VkpiRecentVideoItem,
  type VkpiRecentVideosGroup,
} from "../../../../services/vkpi/myKolBoard-api";
import { isTaskActive } from "../../../../services/vkpi/myKolVideoTasks";

// 内容墙统一读链:全部/单 KOL × 全部/7/15/30 天都走同一服务端
// keyset 筛选。首屏的「全部 KOL × 全部时间」可复用 board-ext 已取回的
// recent_videos,继续页只请求单组端点,不重算看板其他组。
const POLL_MS = 2_000;
const POLL_MAX_MS = 90_000;
const MAX_FULL_QUERY_PAGES = 500;

type LoadedPage = { cursor: string | null; group: VkpiRecentVideosGroup };

function evidenceId(video: VkpiRecentVideoItem): number {
  return Number(video.evidence_id ?? video.id) || 0;
}

function mergeItems(pages: LoadedPage[]): VkpiRecentVideoItem[] {
  const rows: VkpiRecentVideoItem[] = [];
  const seen = new Set<number>();
  pages.forEach(({ group }) => {
    (group.items || []).forEach((video) => {
      const id = evidenceId(video);
      if (!id || seen.has(id)) return;
      seen.add(id);
      rows.push(video);
    });
  });
  return rows;
}

function hasActiveWork(items: VkpiRecentVideoItem[]): boolean {
  return items.some((video) => (
    isTaskActive(video.tasks?.metric_refresh)
    || isTaskActive(video.tasks?.final_v1)
    || isTaskActive(video.tasks?.keyframe_qa)
  ));
}

function errorText(reason: unknown, fallback: string): string {
  return String(
    (reason as { detail?: unknown; message?: unknown })?.detail
      || (reason as Error)?.message
      || fallback,
  ).slice(0, 160);
}

export function useMyKolContentWallPages({
  apiToken,
  initialGroup,
  kolPoolId,
  days,
}: {
  apiToken: string;
  initialGroup: VkpiRecentVideosGroup;
  kolPoolId: number;
  days: number;
}) {
  const targetKey = `${apiToken}:${kolPoolId}:${days}`;
  const targetKeyRef = React.useRef(targetKey);
  targetKeyRef.current = targetKey;
  const generationRef = React.useRef(0);
  const pagesRef = React.useRef<LoadedPage[]>([]);
  const requestBusyRef = React.useRef(false);
  const pollStartedAtRef = React.useRef(0);
  const [pages, setPages] = React.useState<LoadedPage[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [loadingAll, setLoadingAll] = React.useState(false);
  const [error, setError] = React.useState("");
  const [pollPaused, setPollPaused] = React.useState(false);

  const current = React.useCallback((generation: number, key: string) => (
    generationRef.current === generation && targetKeyRef.current === key
  ), []);

  const commitPages = React.useCallback((next: LoadedPage[]) => {
    pagesRef.current = next;
    setPages(next);
  }, []);

  const requestPage = React.useCallback((cursor: string | null, since?: string | null) => (
    getMyKolRecentVideos(apiToken, {
      days,
      kolPoolId: kolPoolId || undefined,
      cursor,
      since,
    })
  ), [apiToken, days, kolPoolId]);

  const loadInitial = React.useCallback(async () => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    const key = targetKeyRef.current;
    requestBusyRef.current = false;
    pollStartedAtRef.current = 0;
    setPollPaused(false);
    setError("");
    setLoadingMore(false);
    setLoadingAll(false);

    // 看板首请求已包含这一页;只有筛选改变时才走单组端点。
    if (kolPoolId === 0 && days === 0 && initialGroup && typeof initialGroup === "object") {
      commitPages([{ cursor: null, group: initialGroup }]);
      setLoading(false);
      return;
    }
    commitPages([]);
    if (!apiToken) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const group = await requestPage(null);
      if (!current(generation, key)) return;
      commitPages([{ cursor: null, group }]);
    } catch (reason) {
      if (current(generation, key)) setError(errorText(reason, "内容墙读取失败"));
    } finally {
      if (current(generation, key)) setLoading(false);
    }
  }, [apiToken, commitPages, current, days, initialGroup, kolPoolId, requestPage]);

  React.useEffect(() => {
    void loadInitial();
    return () => {
      generationRef.current += 1;
      requestBusyRef.current = false;
    };
  }, [loadInitial]);

  const loadMore = React.useCallback(async () => {
    const previous = pagesRef.current;
    const last = previous[previous.length - 1]?.group;
    const cursor = last?.page?.has_more ? String(last.page.next_cursor || "") : "";
    if (!cursor || requestBusyRef.current || !apiToken) return;
    const generation = generationRef.current;
    const key = targetKeyRef.current;
    const since = pagesRef.current[0]?.group.filters?.since;
    requestBusyRef.current = true;
    setLoadingMore(true);
    setError("");
    try {
      const group = await requestPage(cursor, since);
      if (!current(generation, key)) return;
      commitPages([...pagesRef.current, { cursor, group }]);
    } catch (reason) {
      if (current(generation, key)) setError(errorText(reason, "加载更多失败"));
    } finally {
      requestBusyRef.current = false;
      if (current(generation, key)) setLoadingMore(false);
    }
  }, [apiToken, commitPages, current, requestPage]);

  /** 把当前组合筛选沿 keyset 走到真末页。不使用 offset,不猜总数。 */
  const loadAll = React.useCallback(async () => {
    if (requestBusyRef.current || !apiToken || !pagesRef.current.length) return;
    const generation = generationRef.current;
    const key = targetKeyRef.current;
    requestBusyRef.current = true;
    setLoadingAll(true);
    setError("");
    const accumulated = [...pagesRef.current];
    const seenCursors = new Set(accumulated.map((page) => page.cursor).filter(Boolean));
    const since = accumulated[0]?.group.filters?.since;
    try {
      for (let pageNumber = accumulated.length; pageNumber < MAX_FULL_QUERY_PAGES; pageNumber += 1) {
        const last = accumulated[accumulated.length - 1]?.group;
        const cursor = last?.page?.has_more ? String(last.page.next_cursor || "") : "";
        if (!cursor) break;
        if (seenCursors.has(cursor)) throw new Error("服务端返回重复游标,已停止全量查询");
        seenCursors.add(cursor);
        const group = await requestPage(cursor, since);
        if (!current(generation, key)) return;
        accumulated.push({ cursor, group });
        commitPages([...accumulated]);
      }
      const last = accumulated[accumulated.length - 1]?.group;
      if (last?.page?.has_more && last.page.next_cursor) {
        throw new Error(`全量查询已达安全上限 ${MAX_FULL_QUERY_PAGES} 页,请缩小时间范围`);
      }
    } catch (reason) {
      if (current(generation, key)) setError(errorText(reason, "全量查询失败"));
    } finally {
      requestBusyRef.current = false;
      if (current(generation, key)) setLoadingAll(false);
    }
  }, [apiToken, commitPages, current, requestPage]);

  /** 动作入队后从新首页游标顺序重走已加载深度;失败不覆盖可信数据。 */
  const refresh = React.useCallback(async () => {
    if (requestBusyRef.current || !apiToken || !pagesRef.current.length) return false;
    const generation = generationRef.current;
    const key = targetKeyRef.current;
    const pageCount = pagesRef.current.length;
    // A refresh starts a genuinely rolling window. Only pages after the new
    // first page reuse its server-issued anchor so the keyset walk is stable.
    let since: string | null | undefined;
    requestBusyRef.current = true;
    try {
      const refreshed: LoadedPage[] = [];
      let cursor: string | null = null;
      for (let index = 0; index < pageCount; index += 1) {
        const group = await requestPage(cursor, since);
        if (!current(generation, key)) return false;
        if (index === 0) since = group.filters?.since || since;
        refreshed.push({ cursor, group });
        cursor = group.page?.has_more ? String(group.page.next_cursor || "") : "";
        if (!cursor) break;
      }
      if (!current(generation, key)) return false;
      commitPages(refreshed);
      return true;
    } catch {
      return false;
    } finally {
      requestBusyRef.current = false;
    }
  }, [apiToken, commitPages, current, requestPage]);

  /** 周期轮询只重读含活跃任务的页，避免每 2s 重走整条全量游标。 */
  const refreshActivePages = React.useCallback(async () => {
    if (requestBusyRef.current || !apiToken || !pagesRef.current.length) return false;
    const previous = [...pagesRef.current];
    const indexes = previous.flatMap((page, index) => (
      hasActiveWork(page.group.items || []) ? [index] : []
    ));
    if (!indexes.length) return false;
    const generation = generationRef.current;
    const key = targetKeyRef.current;
    const since = previous[0]?.group.filters?.since;
    requestBusyRef.current = true;
    try {
      const refreshed = [...previous];
      for (const index of indexes) {
        const page = previous[index];
        const group = await requestPage(page.cursor, since);
        if (!current(generation, key)) return false;
        refreshed[index] = { cursor: page.cursor, group };
      }
      commitPages(refreshed);
      return true;
    } catch {
      return false;
    } finally {
      requestBusyRef.current = false;
    }
  }, [apiToken, commitPages, current, requestPage]);

  const items = React.useMemo(() => mergeItems(pages), [pages]);
  const active = React.useMemo(() => hasActiveWork(items), [items]);
  const firstFilters = pages[0]?.group.filters;
  const loadedForTarget = Boolean(
    pages.length
    && Number(firstFilters?.days ?? 0) === Number(days || 0)
    && Number(firstFilters?.kol_pool_id ?? 0) === Number(kolPoolId || 0),
  );

  // 单 KOL 任务态保留原有自动恢复语义:2s 重读已加载页,90s 后暂停并
  // 让用户手动继续。全部 KOL 视图不轮询,防止放大全团队读负载。
  React.useEffect(() => {
    if (!kolPoolId || !active || pollPaused || loading || loadingAll || loadingMore) return undefined;
    if (!pollStartedAtRef.current) pollStartedAtRef.current = Date.now();
    if (Date.now() - pollStartedAtRef.current >= POLL_MAX_MS) {
      setPollPaused(true);
      return undefined;
    }
    const timer = setInterval(() => {
      if (Date.now() - pollStartedAtRef.current >= POLL_MAX_MS) {
        setPollPaused(true);
        return;
      }
      void refreshActivePages();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [active, kolPoolId, loading, loadingAll, loadingMore, pages, pollPaused, refreshActivePages]);

  const resumePolling = React.useCallback(() => {
    pollStartedAtRef.current = 0;
    setPollPaused(false);
    void refreshActivePages();
  }, [refreshActivePages]);

  const last = pages[pages.length - 1]?.group;
  return {
    items,
    loadedForTarget,
    hasMore: Boolean(last?.page?.has_more && last.page.next_cursor),
    loaded: pages.length > 0,
    loading,
    loadingMore,
    loadingAll,
    error,
    polling: Boolean(kolPoolId && active && !pollPaused),
    pollPaused,
    loadMore,
    loadAll,
    reload: loadInitial,
    refresh,
    resumePolling,
  };
}
