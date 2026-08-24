import React from "react";
import {
  getMyKolPoolVideos,
  type VkpiKolPoolVideoRow,
  type VkpiMyKolVideoPage,
} from "../../../../services/vkpi/myKolBoard-api";
import { isTaskActive } from "../../../../services/vkpi/myKolVideoTasks";

// MY KOL 视频库读模型 hook(契约 my_kol_video_recovery_v1):
//   · keyset 游标分页(page.next_cursor 不透明串;published_at DESC, id DESC,后台刷新不会让游标漂)
//   · 在途恢复:页面重开即按服务端 TaskState 渲染排队/进行中/重试中;有活跃任务就按 2s 轮询已加载的
//     每一页,全部终态即停;轮询窗封顶 90s 后暂停(pollPaused)并可手动 resume —— 绝不冒充完成
//   · refresh():动作(追踪/刷新/深析)入队后立即重读已加载页,让 chip 以服务端持久任务态为准
export const MY_KOL_VIDEO_PAGE_SIZE = 60;
export const MY_KOL_RECOVERY_POLL_MS = 2_000;
export const MY_KOL_RECOVERY_POLL_MAX_MS = 90_000;

type LoadedPage = { cursor: string | null; response: VkpiMyKolVideoPage };

export function recoveryPageHasActiveWork(page: VkpiMyKolVideoPage | null | undefined): boolean {
  if (isTaskActive(page?.profile_crawl)) return true;
  return (page?.items || []).some((video) => (
    isTaskActive(video.tasks?.metric_refresh)
      || isTaskActive(video.tasks?.final_v1)
      || isTaskActive(video.tasks?.keyframe_qa)
  ));
}

function mergeVideos(pages: LoadedPage[]): VkpiKolPoolVideoRow[] {
  const seen = new Set<number>();
  const rows: VkpiKolPoolVideoRow[] = [];
  pages.forEach(({ response }) => {
    (response.items || []).forEach((video) => {
      const evidenceId = Number(video.evidence_id ?? video.id) || 0;
      if (!evidenceId || seen.has(evidenceId)) return;
      seen.add(evidenceId);
      rows.push(video);
    });
  });
  return rows;
}

function errorText(reason: unknown, fallback: string): string {
  return String((reason as { detail?: unknown; message?: unknown })?.detail || (reason as Error)?.message || fallback).slice(0, 120);
}

export function useMyKolVideoRecovery({
  apiToken,
  kolPoolId,
  enabled = true,
  pageSize = MY_KOL_VIDEO_PAGE_SIZE,
}: {
  apiToken: string;
  kolPoolId: number | string | null | undefined;
  enabled?: boolean;
  pageSize?: number;
}) {
  const targetKey = `${apiToken}:${String(kolPoolId || "")}`;
  const generationRef = React.useRef(0);
  const targetKeyRef = React.useRef(targetKey);
  targetKeyRef.current = targetKey;
  const pagesRef = React.useRef<LoadedPage[]>([]);
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const schedulePollingRef = React.useRef<(generation: number, key: string) => void>(() => undefined);
  const pollStartedAtRef = React.useRef(0);
  const pollPausedRef = React.useRef(false);
  const [pages, setPages] = React.useState<LoadedPage[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [error, setError] = React.useState("");
  const [polling, setPolling] = React.useState(false);
  const [pollPaused, setPollPaused] = React.useState(false);

  const clearTimer = React.useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  const current = React.useCallback((generation: number, key: string) => (
    generationRef.current === generation && targetKeyRef.current === key
  ), []);

  const stopPolling = React.useCallback((paused = false) => {
    clearTimer();
    pollStartedAtRef.current = 0;
    pollPausedRef.current = paused;
    setPolling(false);
    setPollPaused(paused);
  }, [clearTimer]);

  const commitPages = React.useCallback((nextPages: LoadedPage[]) => {
    pagesRef.current = nextPages;
    setPages(nextPages);
  }, []);

  /** 重读已加载的每一页(同游标):动作入队后 / 轮询 tick 用;读失败不替换上一份可信页。 */
  const rereadLoadedPages = React.useCallback(async (generation: number, key: string): Promise<boolean> => {
    const previous = pagesRef.current;
    if (!previous.length) return false;
    try {
      const refreshed = await Promise.all(previous.map(async ({ cursor }) => ({
        cursor,
        response: await getMyKolPoolVideos(apiToken, String(kolPoolId), pageSize, cursor),
      })));
      if (!current(generation, key)) return false;
      commitPages(refreshed);
      return true;
    } catch {
      return false;
    }
  }, [apiToken, commitPages, current, kolPoolId, pageSize]);

  const schedulePolling = React.useCallback((generation: number, key: string) => {
    if (!current(generation, key) || timerRef.current || pollPausedRef.current) return;
    if (!pagesRef.current.some(({ response }) => recoveryPageHasActiveWork(response))) {
      stopPolling(false);
      return;
    }
    if (!pollStartedAtRef.current) pollStartedAtRef.current = Date.now();
    setPolling(true);
    timerRef.current = setTimeout(async () => {
      timerRef.current = null;
      if (!current(generation, key)) return;
      if (Date.now() - pollStartedAtRef.current >= MY_KOL_RECOVERY_POLL_MAX_MS) {
        stopPolling(true);
        return;
      }
      await rereadLoadedPages(generation, key);
      if (current(generation, key)) schedulePollingRef.current(generation, key);
    }, MY_KOL_RECOVERY_POLL_MS);
  }, [current, rereadLoadedPages, stopPolling]);
  schedulePollingRef.current = schedulePolling;

  const loadInitial = React.useCallback(async () => {
    const id = String(kolPoolId || "");
    const key = `${apiToken}:${id}`;
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    clearTimer();
    pollStartedAtRef.current = 0;
    pollPausedRef.current = false;
    setPollPaused(false);
    setPolling(false);
    commitPages([]);
    setError("");
    if (!enabled || !apiToken || !id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const response = await getMyKolPoolVideos(apiToken, id, pageSize);
      if (!current(generation, key)) return;
      commitPages([{ cursor: null, response }]);
      schedulePolling(generation, key);
    } catch (reason) {
      if (!current(generation, key)) return;
      setError(errorText(reason, "视频读取失败"));
    } finally {
      if (current(generation, key)) setLoading(false);
    }
  }, [apiToken, clearTimer, commitPages, current, enabled, kolPoolId, pageSize, schedulePolling]);

  React.useEffect(() => {
    void loadInitial();
    return () => {
      generationRef.current += 1;
      clearTimer();
    };
  }, [loadInitial, clearTimer]);

  const loadMore = React.useCallback(async () => {
    const previous = pagesRef.current;
    const last = previous[previous.length - 1]?.response;
    const cursor = (last?.page?.has_more ? last?.page?.next_cursor : null) || null;
    if (!cursor || loadingMore || !apiToken || !kolPoolId) return;
    const generation = generationRef.current;
    const key = targetKeyRef.current;
    setLoadingMore(true);
    try {
      const response = await getMyKolPoolVideos(apiToken, String(kolPoolId), pageSize, cursor);
      if (!current(generation, key)) return;
      commitPages([...pagesRef.current, { cursor, response }]);
      schedulePolling(generation, key);
    } catch (reason) {
      if (current(generation, key)) setError(errorText(reason, "加载更多失败"));
    } finally {
      if (current(generation, key)) setLoadingMore(false);
    }
  }, [apiToken, commitPages, current, kolPoolId, loadingMore, pageSize, schedulePolling]);

  /** 动作入队后调用:重读已加载页并按需重新起轮询(暂停态也会被新动作唤醒)。 */
  const refresh = React.useCallback(async () => {
    const generation = generationRef.current;
    const key = targetKeyRef.current;
    if (!pagesRef.current.length) {
      await loadInitial();
      return;
    }
    clearTimer();
    pollPausedRef.current = false;
    pollStartedAtRef.current = 0;
    setPollPaused(false);
    await rereadLoadedPages(generation, key);
    if (current(generation, key)) schedulePolling(generation, key);
  }, [clearTimer, current, loadInitial, rereadLoadedPages, schedulePolling]);

  const resumePolling = React.useCallback(() => {
    pollPausedRef.current = false;
    pollStartedAtRef.current = 0;
    setPollPaused(false);
    schedulePolling(generationRef.current, targetKeyRef.current);
  }, [schedulePolling]);

  const videos = React.useMemo(() => mergeVideos(pages), [pages]);
  const first = pages[0]?.response;
  const last = pages[pages.length - 1]?.response;
  return {
    videos,
    profileCrawl: first?.profile_crawl || null,
    summary: first?.summary || null,
    total: Number(first?.total ?? first?.summary?.total ?? 0) || 0,
    hasMore: Boolean(last?.page?.has_more && last?.page?.next_cursor),
    loaded: pages.length > 0,
    loading,
    loadingMore,
    error,
    polling,
    pollPaused,
    reload: loadInitial,
    refresh,
    resumePolling,
    loadMore,
  };
}
