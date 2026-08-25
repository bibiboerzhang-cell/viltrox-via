import { useCallback, useEffect, useRef, useState } from 'react';
import { getGoaffproSummary, type GoaffproSummaryTotals } from '../../../../services/vkpi/goaffpro-api';
import {
  generateProjectRetrospective,
  getProjectContracts,
  getProjectRetrospective,
  getProjectVideoAnalysisCacheMulti,
  type VkpiProjectContractsResponse,
  type VkpiProjectRetrospectiveResponse,
  type VkpiProjectVideoAnalysisCacheResponse,
} from '../../../../services/vkpi/projects-api';
import type { NoticeState } from '../../../../domains/projects';

type SetNotice = (notice: NoticeState | null) => void;

type ProjectVideoCacheItem = VkpiProjectVideoAnalysisCacheResponse['items'][number];

const VIDEO_ANALYSIS_FAST_POLL_MS = 2500;
const VIDEO_ANALYSIS_BACKOFF_POLL_MS = 10000;
const VIDEO_ANALYSIS_BACKOFF_AFTER_MS = 5 * 60 * 1000;
const VIDEO_ANALYSIS_AUTO_REFRESH_LIMIT_MS = 30 * 60 * 1000;
const VIDEO_ANALYSIS_INITIAL_READ_ATTEMPTS = 3;
const ACTIVE_VIDEO_ANALYSIS_STATES = new Set(['queued', 'running', 'retrying', 'processing']);

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function isEmptyCacheValue(value: unknown) {
  if (value == null || value === '') return true;
  if (Array.isArray(value)) return value.length === 0;
  if (isPlainRecord(value)) return Object.keys(value).length === 0;
  return false;
}

function mergeRicherCacheValue(previous: unknown, incoming: unknown): unknown {
  if (isEmptyCacheValue(incoming)) return previous;
  if (isEmptyCacheValue(previous)) return incoming;
  if (Array.isArray(previous) && Array.isArray(incoming)) {
    return incoming.length >= previous.length ? incoming : previous;
  }
  if (isPlainRecord(previous) && isPlainRecord(incoming)) {
    const merged: Record<string, unknown> = { ...previous };
    Object.entries(incoming).forEach(([key, value]) => {
      merged[key] = mergeRicherCacheValue(previous[key], value);
    });
    return merged;
  }
  return incoming;
}

function mergeProjectVideoCacheItem(previous: ProjectVideoCacheItem, incoming: ProjectVideoCacheItem) {
  const { entry: _previousEntry, ...previousMeta } = previous;
  const { entry: _incomingEntry, ...incomingMeta } = incoming;
  const merged = mergeRicherCacheValue(previousMeta, incomingMeta) as ProjectVideoCacheItem;
  // 运行态字段来自当前服务端快照；null 也代表任务已经离开 active 集合，不能 keep-richer。
  merged.active_job = incoming.active_job ?? null;
  merged.terminal_reason = incoming.terminal_reason ?? null;
  if (incoming.state === 'ready') {
    // ready 分析是一个完整版本，绝不与旧分析 entry 深合并成不存在的混合版本。
    merged.entry = incoming.entry;
    return merged;
  }
  if (incoming.state === 'quality_incomplete' || incoming.state === 'legacy_unverified') {
    // 显式质量终态是当前权威快照；不得用上一版 ready 覆盖或深合并成假结果。
    merged.state = incoming.state;
    merged.entry = incoming.entry ?? null;
    return merged;
  }
  if (previous.state === 'ready') {
    merged.state = 'ready';
    merged.entry = previous.entry;
  } else {
    merged.entry = incoming.entry ?? previous.entry;
  }
  return merged;
}

function matchingPreviousItemIndex(previousItems: ProjectVideoCacheItem[], incoming: ProjectVideoCacheItem) {
  const incomingId = incoming.evidence_id;
  if (incomingId != null) {
    const exactId = previousItems.findIndex((item) => item.evidence_id != null && String(item.evidence_id) === String(incomingId));
    if (exactId >= 0) return exactId;
  }
  const incomingUrl = String(incoming.content_url || '').trim();
  if (!incomingUrl) return -1;
  return previousItems.findIndex((item) => {
    const sameUrl = String(item.content_url || '').trim() === incomingUrl;
    // 两边都有 evidence_id 时，URL 相同也不能跨证据合并。
    return sameUrl && (incomingId == null || item.evidence_id == null);
  });
}

/**
 * 轮询返回可能短暂缺字段或把已完成项重新标成 pending；按 evidence/url 合并，
 * 确保已展示的完整分析不会倒退或闪空。
 */
export function mergeProjectVideoAnalysisCache(
  previous: VkpiProjectVideoAnalysisCacheResponse | null,
  incoming: VkpiProjectVideoAnalysisCacheResponse | null,
) {
  if (!incoming) return previous;
  if (!previous) return incoming;

  const previousItems = previous.items;

  // incoming 是当前 evidence 的权威快照；旧快照里已不存在的项不能继续保留为 active。
  const items = incoming.items.map((item) => {
    const existingIndex = matchingPreviousItemIndex(previousItems, item);
    return existingIndex < 0 ? item : mergeProjectVideoCacheItem(previousItems[existingIndex], item);
  });

  const readyCount = items.filter((item) => item.state === 'ready').length;
  const activeCount = items.filter((item) => {
    const activeStatus = String(item.active_job?.status || '').toLowerCase();
    return ACTIVE_VIDEO_ANALYSIS_STATES.has(activeStatus);
  }).length;
  const stateCounts = items.reduce<Record<string, number>>((counts, item) => {
    counts[item.state] = (counts[item.state] || 0) + 1;
    return counts;
  }, {});
  return {
    ...previous,
    ...incoming,
    items,
    summary: {
      ...incoming.summary,
      evidence_count: Math.max(incoming.summary.evidence_count, items.length),
      ready_count: readyCount,
      pending_count: activeCount,
      active_count: activeCount,
      not_requested_count: stateCounts.not_requested || 0,
      failed_count: stateCounts.failed || 0,
      quality_incomplete_count: stateCounts.quality_incomplete || 0,
      legacy_unverified_count: stateCounts.legacy_unverified || 0,
      unsupported_count: stateCounts.unsupported || 0,
      state_counts: stateCounts,
    },
  };
}

export function projectVideoAnalysisPollDelay(elapsedMs: number) {
  return elapsedMs < VIDEO_ANALYSIS_BACKOFF_AFTER_MS
    ? VIDEO_ANALYSIS_FAST_POLL_MS
    : VIDEO_ANALYSIS_BACKOFF_POLL_MS;
}

function hasActiveVideoAnalysis(...caches: Array<VkpiProjectVideoAnalysisCacheResponse | null>) {
  return caches.some((cache) => (cache?.items || []).some((item) => {
    const status = String(item.active_job?.status || '').toLowerCase();
    return ACTIVE_VIDEO_ANALYSIS_STATES.has(status);
  }));
}

export function useProjectVideoAnalysisCache(apiToken: string | undefined, projectId: string) {
  const [videoAnalysisCache, setVideoAnalysisCache] = useState<VkpiProjectVideoAnalysisCacheResponse | null>(null);
  const [videoQaCache, setVideoQaCache] = useState<VkpiProjectVideoAnalysisCacheResponse | null>(null);
  const [videoAnalysisLoading, setVideoAnalysisLoading] = useState(false);
  const [videoAnalysisError, setVideoAnalysisError] = useState('');
  const [videoQaError, setVideoQaError] = useState('');
  const [videoAnalysisAutoRefreshStopped, setVideoAnalysisAutoRefreshStopped] = useState('');
  const manualRefreshRef = useRef<() => void>(() => undefined);
  const refreshVideoAnalysisCache = useCallback(() => manualRefreshRef.current(), []);

  useEffect(() => {
    setVideoAnalysisCache(null);
    setVideoQaCache(null);
    setVideoAnalysisError('');
    setVideoQaError('');
    setVideoAnalysisAutoRefreshStopped('');
    if (!apiToken || !projectId) {
      setVideoAnalysisLoading(false);
      manualRefreshRef.current = () => undefined;
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    let pollStartedAt = 0;
    let readAttempts = 0;
    let requestInFlight = false;
    let currentAnalysis: VkpiProjectVideoAnalysisCacheResponse | null = null;
    let currentQa: VkpiProjectVideoAnalysisCacheResponse | null = null;

    const clearTimer = () => {
      if (timer != null) window.clearTimeout(timer);
      timer = undefined;
    };

    const scheduleNext = () => {
      if (cancelled || !hasActiveVideoAnalysis(currentAnalysis, currentQa)) return;
      if (!pollStartedAt) pollStartedAt = Date.now();
      const elapsed = Date.now() - pollStartedAt;
      if (elapsed >= VIDEO_ANALYSIS_AUTO_REFRESH_LIMIT_MS) {
        setVideoAnalysisAutoRefreshStopped('自动刷新已在 30 分钟后停止；任务可能仍在排队，可点“刷新状态”再次读取。');
        return;
      }
      const delay = projectVideoAnalysisPollDelay(elapsed);
      timer = window.setTimeout(() => { void load(false); }, delay);
    };

    const load = async (initial: boolean) => {
      if (requestInFlight) return;
      requestInFlight = true;
      if (initial) setVideoAnalysisLoading(true);
      readAttempts += 1;
      try {
        // final_v1 + keyframe_qa 合并为一次请求；只要任一路仍 pending 就继续刷新。
        const payload = await getProjectVideoAnalysisCacheMulti(apiToken, projectId, ['video_analysis_final_v1', 'video_analysis_final_v1_keyframe_qa']);
        if (cancelled) return;
        currentAnalysis = mergeProjectVideoAnalysisCache(
          currentAnalysis,
          payload.by_method?.['video_analysis_final_v1'] ?? null,
        );
        currentQa = mergeProjectVideoAnalysisCache(
          currentQa,
          payload.by_method?.['video_analysis_final_v1_keyframe_qa'] ?? null,
        );
        setVideoAnalysisCache(currentAnalysis);
        setVideoQaCache(currentQa);
        setVideoAnalysisError('');
        setVideoQaError('');
        readAttempts = 0;
        if (hasActiveVideoAnalysis(currentAnalysis, currentQa)) scheduleNext();
        else {
          pollStartedAt = 0;
          setVideoAnalysisAutoRefreshStopped('');
        }
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : '视频分析读取失败';
        setVideoAnalysisError(message);
        setVideoQaError(message);
        // 首屏读取最多尝试 3 次；已有真实 active_job 时则按自动刷新截止线继续。
        if (!currentAnalysis && !currentQa && readAttempts < VIDEO_ANALYSIS_INITIAL_READ_ATTEMPTS) {
          timer = window.setTimeout(() => { void load(false); }, VIDEO_ANALYSIS_FAST_POLL_MS);
        } else {
          scheduleNext();
        }
      } finally {
        requestInFlight = false;
        if (!cancelled && initial) setVideoAnalysisLoading(false);
      }
    };

    manualRefreshRef.current = () => {
      clearTimer();
      pollStartedAt = 0;
      readAttempts = 0;
      setVideoAnalysisAutoRefreshStopped('');
      void load(false);
    };
    void load(true);
    return () => {
      cancelled = true;
      clearTimer();
      manualRefreshRef.current = () => undefined;
    };
  }, [apiToken, projectId]);

  return {
    videoAnalysisCache,
    videoQaCache,
    videoAnalysisLoading,
    videoAnalysisError,
    videoQaError,
    videoAnalysisAutoRefreshStopped,
    refreshVideoAnalysisCache,
  };
}

export function useProjectContracts(apiToken: string | undefined, projectId: string, setNotice: SetNotice) {
  const [contractsPayload, setContractsPayload] = useState<VkpiProjectContractsResponse | null>(null);
  const [contractsLoading, setContractsLoading] = useState(false);
  const [contractsError, setContractsError] = useState('');
  const contractPollStartRef = useRef(0);
  const contractStallWarnedRef = useRef(false);
  const contractPollStoppedRef = useRef(false);

  const loadContracts = async () => {
    if (!apiToken || !projectId) {
      setContractsPayload(null);
      return;
    }
    setContractsLoading(true);
    setContractsError('');
    try {
      setContractsPayload(await getProjectContracts(apiToken, projectId));
    } catch (error) {
      setContractsError(error instanceof Error ? error.message : '合同列表读取失败');
    } finally {
      setContractsLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    setContractsPayload(null);
    setContractsError('');
    if (!apiToken || !projectId) {
      setContractsLoading(false);
      return;
    }
    setContractsLoading(true);
    getProjectContracts(apiToken, projectId)
      .then((payload) => {
        if (!cancelled) setContractsPayload(payload);
      })
      .catch((error) => {
        if (!cancelled) setContractsError(error instanceof Error ? error.message : '合同列表读取失败');
      })
      .finally(() => {
        if (!cancelled) setContractsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, projectId]);

  // 合同提取已改异步(worker 执行):有 processing 行时轮询刷新,直至 ready/failed。
  // m2 轮询契约:前 5 分钟每 2.5s → 之后退避到 10s → 30 分钟封顶停止自动刷新。
  // m1 卡滞文案:worker 是串行队列,排队久 ≠ worker 挂了,文案不做误报断言。
  useEffect(() => {
    if (!apiToken || !projectId) return undefined;
    const hasProcessing = (contractsPayload?.items || []).some((item) => item.extraction_status === 'processing');
    if (!hasProcessing) {
      contractStallWarnedRef.current = false;
      contractPollStoppedRef.current = false;
      contractPollStartRef.current = 0;
      return undefined;
    }
    if (!contractPollStartRef.current) contractPollStartRef.current = Date.now();
    const elapsed = Date.now() - contractPollStartRef.current;
    if (elapsed > 30 * 60 * 1000) {
      if (!contractPollStoppedRef.current) {
        contractPollStoppedRef.current = true;
        setNotice({
          tone: 'warning',
          title: '已暂停合同提取自动刷新',
          body: '排队超过 30 分钟。请确认 worker 是否在运行；恢复后可点「重新提取」重新入队，或刷新页面查看最新状态。',
        });
      }
      return undefined;
    }
    const intervalMs = elapsed < 5 * 60 * 1000 ? 2500 : 10000;
    const timer = window.setInterval(async () => {
      try {
        const payload = await getProjectContracts(apiToken, projectId);
        setContractsPayload(payload);
        if (!contractStallWarnedRef.current && Date.now() - contractPollStartRef.current > 180000) {
          contractStallWarnedRef.current = true;
          setNotice({
            tone: 'info',
            title: '合同提取仍在队列中',
            body: 'worker 串行处理任务,前方可能有长视频分析在跑(见左侧任务泳道)。超过 30 分钟仍未完成时再检查 worker 运行状态。',
          });
        }
      } catch {
        // 轮询单次失败不打断:保留上次列表,下个周期重试;持续无进展由 30 分钟封顶提示兜底
      }
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [apiToken, projectId, contractsPayload, setNotice]);

  return { contractsPayload, setContractsPayload, contractsLoading, contractsError, loadContracts };
}

export function useProjectRetrospective(apiToken: string | undefined, projectId: string, setNotice: SetNotice) {
  const [retrospective, setRetrospective] = useState<VkpiProjectRetrospectiveResponse | null>(null);
  const [retroBusy, setRetroBusy] = useState(false);

  // 复盘项目级聚合(LLM):打开时读一次;生成中由 active_job 驱动轮询,ready/failed 停。
  useEffect(() => {
    let cancelled = false;
    setRetrospective(null);
    if (!apiToken || !projectId) return undefined;
    getProjectRetrospective(apiToken, projectId)
      .then((payload) => { if (!cancelled) setRetrospective(payload); })
      .catch(() => { /* 读失败不阻断:复盘卡降级到模板占位 */ });
    return () => { cancelled = true; };
  }, [apiToken, projectId]);

  useEffect(() => {
    if (!apiToken || !projectId) return undefined;
    const activeJob = retrospective?.active_job;
    if (!activeJob) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const payload = await getProjectRetrospective(apiToken, projectId);
        setRetrospective(payload);
        if (!payload.active_job) {
          setRetroBusy(false);
          const failed = String(payload.last_job?.status || '') === 'failed' || String(payload.last_job?.status || '') === 'blocked';
          if (failed && !payload.retrospective?.result) {
            setNotice({ tone: 'warning', title: '项目复盘生成未完成', body: payload.last_job?.last_error ? `原因:${payload.last_job.last_error}` : '生成失败,请稍后重试或检查 worker。' });
          }
        }
      } catch {
        // 单次轮询失败保留上次状态,下个周期重试
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [apiToken, projectId, retrospective?.active_job, setNotice]);

  const generateRetrospective = async () => {
    if (!apiToken || !projectId) return;
    setRetroBusy(true);
    try {
      const result = await generateProjectRetrospective(apiToken, projectId);
      const already = String(result?.status || '').startsWith('already');
      setNotice({ tone: 'success', title: already ? '复盘已在生成队列中' : '项目复盘已入队', body: '完成后此处自动更新;进度见左侧任务泳道「复盘聚合 · 总结中」。' });
      setRetrospective(await getProjectRetrospective(apiToken, projectId));
    } catch (error) {
      setRetroBusy(false);
      setNotice({ tone: 'warning', title: '复盘生成入队失败', body: error instanceof Error ? error.message : '请稍后重试。' });
    }
  };

  return { retrospective, retroBusy, generateRetrospective };
}

export function useProjectGoaffpro(apiToken: string | undefined, projectId: string) {
  // GOAFFPRO 已是归因真源:拉本项目下已建链 KOL 的实时点击/订单/GMV,覆盖卡片(短链点击/归因销售/ROI)。
  const [goaffTotals, setGoaffTotals] = useState<GoaffproSummaryTotals | null>(null);
  const [goaffByKol, setGoaffByKol] = useState<Record<string, { clicks: number; orders: number; gmv: number }>>({});

  useEffect(() => {
    if (!apiToken || !projectId) { setGoaffTotals(null); setGoaffByKol({}); return; }
    let cancelled = false;
    getGoaffproSummary(apiToken, { projectId })
      .then((res) => {
        if (cancelled) return;
        setGoaffTotals(res.totals ?? null);
        const map: Record<string, { clicks: number; orders: number; gmv: number }> = {};
        (res.items || []).forEach((it) => {
          if (it.kol_pool_id != null) map[String(it.kol_pool_id)] = { clicks: it.clicks ?? 0, orders: it.orders ?? 0, gmv: it.gmv_usd ?? 0 };
        });
        setGoaffByKol(map);
      })
      .catch(() => { if (!cancelled) { setGoaffTotals(null); setGoaffByKol({}); } });
    return () => { cancelled = true; };
  }, [apiToken, projectId]);

  return { goaffTotals, goaffByKol };
}
