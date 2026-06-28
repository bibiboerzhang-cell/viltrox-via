import { useEffect, useRef, useState } from 'react';
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

export function useProjectVideoAnalysisCache(apiToken: string | undefined, projectId: string) {
  const [videoAnalysisCache, setVideoAnalysisCache] = useState<VkpiProjectVideoAnalysisCacheResponse | null>(null);
  const [videoQaCache, setVideoQaCache] = useState<VkpiProjectVideoAnalysisCacheResponse | null>(null);
  const [videoAnalysisLoading, setVideoAnalysisLoading] = useState(false);
  const [videoAnalysisError, setVideoAnalysisError] = useState('');
  const [videoQaError, setVideoQaError] = useState('');

  useEffect(() => {
    setVideoAnalysisCache(null);
    setVideoQaCache(null);
    setVideoAnalysisError('');
    setVideoQaError('');
    if (!apiToken || !projectId) {
      setVideoAnalysisLoading(false);
      return;
    }
    let cancelled = false;
    setVideoAnalysisLoading(true);
    // 批5:final_v1 + keyframe_qa 合并为一次请求(后端 by_method 拆分),省一个往返。
    getProjectVideoAnalysisCacheMulti(apiToken, projectId, ['video_analysis_final_v1', 'video_analysis_final_v1_keyframe_qa'])
      .then((payload) => {
        if (cancelled) return;
        setVideoAnalysisCache(payload.by_method?.['video_analysis_final_v1'] ?? null);
        setVideoQaCache(payload.by_method?.['video_analysis_final_v1_keyframe_qa'] ?? null);
      })
      .catch((error) => {
        if (cancelled) return;
        setVideoAnalysisError(error instanceof Error ? error.message : 'final_v1 分析读取失败');
        setVideoQaError(error instanceof Error ? error.message : '关键帧 QA 读取失败');
      })
      .finally(() => {
        if (!cancelled) setVideoAnalysisLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, projectId]);

  return { videoAnalysisCache, videoQaCache, videoAnalysisLoading, videoAnalysisError, videoQaError };
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
