import React from "react";
import {
  getMyKolPoolVideos,
  type RecoverableJobStatus,
  type VkpiKolPoolVideoRow,
  type VkpiMyKolVideoPage,
} from "../../../../services/vkpi/myKolBoard-api";

export const MY_KOL_VIDEO_PAGE_SIZE = 60;
export const MY_KOL_RECOVERY_POLL_MS = 2_000;
export const MY_KOL_RECOVERY_POLL_MAX_MS = 90_000;

const ACTIVE_JOB_STATUSES = new Set<RecoverableJobStatus>(["queued", "running", "retrying"]);

type LoadedPage = { cursor: string | null; response: VkpiMyKolVideoPage };

export function isRecoverableJobActive(status: unknown): boolean {
  return ACTIVE_JOB_STATUSES.has(String(status || "") as RecoverableJobStatus);
}

export function recoveryPageHasActiveWork(page: VkpiMyKolVideoPage | null | undefined): boolean {
  if (isRecoverableJobActive(page?.profile_crawl?.status)) return true;
  return (page?.items || []).some((video) => (
    video.final_v1?.state === "active"
    || isRecoverableJobActive(video.metric_refresh?.latest_job?.status)
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

export function useMyKolVideoRecovery({
  apiToken,
  kolPoolId,
  enabled = true,
}: {
  apiToken: string;
  kolPoolId: number | string | null | undefined;
  enabled?: boolean;
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
      const previous = pagesRef.current;
      try {
        const refreshed = await Promise.all(previous.map(async ({ cursor }) => ({
          cursor,
          response: await getMyKolPoolVideos(apiToken, String(kolPoolId), MY_KOL_VIDEO_PAGE_SIZE, cursor),
        })));
        if (!current(generation, key)) return;
        const previousProfileActive = recoveryPageHasActiveWork({ profile_crawl: previous[0]?.response.profile_crawl });
        const refreshedProfileActive = recoveryPageHasActiveWork({ profile_crawl: refreshed[0]?.response.profile_crawl });
        // A profile crawl can insert evidence at the head and invalidate later
        // offset cursors.  Once that exact job settles, keep the freshly read
        // first page and let the user load subsequent pages from its new cursor.
        const nextPages = previousProfileActive && !refreshedProfileActive ? refreshed.slice(0, 1) : refreshed;
        pagesRef.current = nextPages;
        setPages(nextPages);
      } catch {
        // A transient read failure is not a task failure; keep polling inside
        // the bounded window without replacing the last trustworthy page.
      }
      if (current(generation, key)) schedulePollingRef.current(generation, key);
    }, MY_KOL_RECOVERY_POLL_MS);
  }, [apiToken, current, kolPoolId, stopPolling]);
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
    pagesRef.current = [];
    setPages([]);
    setError("");
    if (!enabled || !apiToken || !id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const response = await getMyKolPoolVideos(apiToken, id, MY_KOL_VIDEO_PAGE_SIZE);
      if (!current(generation, key)) return;
      const nextPages = [{ cursor: null, response }];
      pagesRef.current = nextPages;
      setPages(nextPages);
      schedulePolling(generation, key);
    } catch (reason) {
      if (!current(generation, key)) return;
      setError(String((reason as Error)?.message || "视频读取失败").slice(0, 120));
    } finally {
      if (current(generation, key)) setLoading(false);
    }
  }, [apiToken, clearTimer, current, enabled, kolPoolId, schedulePolling]);

  React.useEffect(() => {
    void loadInitial();
    return () => {
      generationRef.current += 1;
      clearTimer();
    };
  }, [loadInitial, clearTimer]);

  const loadMore = React.useCallback(async () => {
    const previous = pagesRef.current;
    const cursor = previous[previous.length - 1]?.response.next_cursor || null;
    if (!cursor || loadingMore || !apiToken || !kolPoolId) return;
    const generation = generationRef.current;
    const key = targetKeyRef.current;
    setLoadingMore(true);
    try {
      const response = await getMyKolPoolVideos(apiToken, String(kolPoolId), MY_KOL_VIDEO_PAGE_SIZE, cursor);
      if (!current(generation, key)) return;
      const nextPages = [...pagesRef.current, { cursor, response }];
      pagesRef.current = nextPages;
      setPages(nextPages);
      schedulePolling(generation, key);
    } catch (reason) {
      if (current(generation, key)) setError(String((reason as Error)?.message || "加载更多失败").slice(0, 120));
    } finally {
      if (current(generation, key)) setLoadingMore(false);
    }
  }, [apiToken, current, kolPoolId, loadingMore, schedulePolling]);

  const videos = React.useMemo(() => mergeVideos(pages), [pages]);
  const first = pages[0]?.response;
  const last = pages[pages.length - 1]?.response;
  return {
    videos,
    profileCrawl: first?.profile_crawl || null,
    summary: first?.summary || null,
    total: Number(first?.total || 0),
    hasMore: Boolean(last?.has_more && last?.next_cursor),
    loading,
    loadingMore,
    error,
    polling,
    pollPaused,
    reload: loadInitial,
    loadMore,
  };
}
