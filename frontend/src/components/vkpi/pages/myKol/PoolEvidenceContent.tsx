import { useEffect, useMemo, useRef, useState } from 'react';
import type { VkpiKolOption, VkpiProjectRow } from '../../vkpiTypes';
import type { PostPreview } from '../channels/myKolMatrixTypes';
import { safeNumber } from '../../shared/vkpiDataUtils';
import { EmployeeKolContentLayer } from './EmployeeKolContentLayer';

// C4-full(裁决重做,2026-06-12;2026-06-12 复审令拆出独立文件,行为升级三处):
// Pool 收藏行的右侧内容层——evidence 视频走 /kol-pool/{id}/videos,渲染复用
// EmployeeKolContentLayer(图2 同款)。升级:①账号分析/评论采集改 job 终态轮询
// (不再靠 videos.length 猜完成,失败/纯补缓存也能正确回执)②Gemini 深析限批 5 条
// (配额保护)。

function compactNumber(value: number | null | undefined) {
  const next = safeNumber(value);
  if (!next) return '—';
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: next >= 1000 ? 1 : 0 }).format(next);
}

type JobTerminal = { state: 'done' | 'blocked' | 'gone'; error?: string };

// 轮询 apify_jobs 终态(经 task-queue/compact):done/blocked 为准;
// 任务滑出 recent 窗口(10min)按"已结束"处理(gone)。
async function waitJobTerminal(
  apiToken: string,
  jobId: number,
  cancelled: { current: boolean },
  { intervalMs = 6000, maxTries = 30 } = {},
): Promise<JobTerminal | null> {
  const { getTaskQueueCompact } = await import('../../../../services/vkpi/tasks-api');
  let seen = false;
  for (let i = 0; i < maxTries; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    if (cancelled.current) return null;
    try {
      const resp = await getTaskQueueCompact(apiToken, { limit: 50, recentMinutes: 10 }) as unknown as {
        active?: Array<Record<string, unknown>>; recent?: Array<Record<string, unknown>>;
      };
      const all = [...(resp.active || []), ...(resp.recent || [])];
      const item = all.find((task) => String(task.id) === String(jobId));
      if (!item) {
        if (seen) return { state: 'gone' };
        if (i >= 2) return { state: 'gone' };
        continue;
      }
      seen = true;
      const status = String(item.status || '');
      if (status === 'done') return { state: 'done' };
      if (status === 'blocked' || status === 'failed') return { state: 'blocked', error: String(item.error || status).slice(0, 140) };
    } catch { /* 单次轮询失败不终止 */ }
  }
  return { state: 'gone' };
}

export function PoolEvidenceContent({ apiToken, kol, poolId, viltroxOnly, projects = [] }: { apiToken?: string; kol?: VkpiKolOption; poolId: number; viltroxOnly?: boolean; projects?: VkpiProjectRow[] }) {
  const cancelledRef = useRef(false);
  useEffect(() => {
    cancelledRef.current = false;
    return () => { cancelledRef.current = true; };
  }, [poolId]);

  const [refreshTick, setRefreshTick] = useState(0);
  const [videos, setVideos] = useState<Array<Record<string, unknown>>>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');
  useEffect(() => {
    if (!apiToken || !poolId) return;
    let cancelled = false;
    setState((s) => (s === 'ready' ? s : 'loading'));
    import('../../../../domains/kol').then(({ listKolPoolVideos }) =>
      listKolPoolVideos(apiToken, poolId, 200).then((resp) => {
        if (cancelled) return;
        setVideos((resp.items || []) as Array<Record<string, unknown>>);
        setState('ready');
      }),
    ).catch((err) => {
      if (cancelled) return;
      setState('error');
      setError(String((err as Error)?.message || '视频读取失败').slice(0, 120));
    });
    return () => { cancelled = true; };
  }, [apiToken, poolId, refreshTick]);

  // ① 账号分析:job 终态轮询回执(复审令:不再用 videos.length 猜——
  // worker 只补 cache/只更新已有行/任务失败时数量不变,旧法必误判)。
  const [analyzeState, setAnalyzeState] = useState<'idle' | 'busy' | 'queued' | 'error'>('idle');
  const [analyzeMsg, setAnalyzeMsg] = useState('');
  const startAccountAnalysis = () => {
    if (!apiToken || !kol?.profileUrl || analyzeState === 'busy') return;
    setAnalyzeState('busy');
    import('../../../../domains/kol').then(({ enqueueKolProfileDeepCrawl }) =>
      enqueueKolProfileDeepCrawl(apiToken, String(kol.profileUrl), poolId).then((resp) => {
        setAnalyzeState('queued');
        setAnalyzeMsg(resp.status === 'already_queued' ? '该账号分析已在队列中——完成后此处自动回执。' : '已入队(泳道「账号分析」)——完成后此处自动回执。');
        const jobId = Number((resp as Record<string, unknown>).job_id) || 0;
        if (!jobId) return;
        void waitJobTerminal(apiToken, jobId, cancelledRef).then((terminal) => {
          if (!terminal || cancelledRef.current) return;
          if (terminal.state === 'blocked') {
            setAnalyzeState('error');
            setAnalyzeMsg(`账号分析被拦:${terminal.error || '原因见泳道'}`);
            return;
          }
          setAnalyzeState('idle');
          setRefreshTick((t) => t + 1);
          setAnalyzeMsg(terminal.state === 'done' ? '✓ 账号分析完成——列表已刷新(视频/缩略图/R2 状态以最新为准)。' : '账号分析已结束(出泳道窗口)——列表已刷新。');
        });
      }),
    ).catch((err) => {
      setAnalyzeState('error');
      setAnalyzeMsg(String((err as Error)?.message || '发起失败').slice(0, 120));
    });
  };

  const totalViews = videos.reduce((sum, v) => sum + safeNumber(v.view_count as number), 0);
  const poolPosts = useMemo<PostPreview[]>(() => videos.map((v, i) => {
    const title = String(v.title || v.video_title || '').trim();
    const analyzed = Boolean(v.has_final_v1_cache);
    const llmDetected = v.llm_viltrox_detected;
    const isViltrox = analyzed && llmDetected != null
      ? Boolean(llmDetected)
      : /viltrox/i.test(`${title} ${String(v.content_url || '')}`);
    const gear = Array.isArray(v.llm_viltrox_products) ? (v.llm_viltrox_products as string[]).filter(Boolean) : [];
    const competitors = Array.isArray(v.llm_competitor_mentions) ? (v.llm_competitor_mentions as string[]).filter(Boolean) : [];
    return {
      id: `evidence:${String(v.evidence_id ?? v.id ?? i)}`,
      snapshotId: '',
      title: title || '未命名视频',
      url: String(v.content_url || ''),
      mediaUrl: '',
      videoUrl: String(v.cached_video_url || ''),
      imageUrl: String(v.cached_thumbnail_url || v.best_thumbnail || v.thumbnail_url || ''),
      imageUrls: [],
      mediaUrls: [],
      views: safeNumber(v.view_count as number),
      likes: safeNumber(v.like_count as number),
      comments: safeNumber(v.comment_count as number),
      shares: safeNumber(v.share_count as number),
      publishedAt: String(v.publish_date || v.posted_at || ''),
      contentType: 'video',
      brandMentions: isViltrox ? ['viltrox'] : [],
      competitorMentions: competitors,
      gearMentions: gear,
      rawText: analyzed ? `${title} · 已深析` : title,
    };
  }), [videos]);
  const anyViltrox = poolPosts.some((p) => p.brandMentions.length > 0);
  const analyzedCount = videos.filter((v) => Boolean(v.has_final_v1_cache)).length;
  const unanalyzed = videos.filter((v) => !v.has_final_v1_cache);

  // ② Gemini 深析:限批 5 条/次(复审令:200 条一键全入会放大配额与队列压力)。
  const DEEP_BATCH = 5;
  const [deepState, setDeepState] = useState<'idle' | 'busy' | 'queued'>('idle');
  const startDeepAnalysis = () => {
    if (!apiToken || !unanalyzed.length || deepState === 'busy') return;
    setDeepState('busy');
    const batch = unanalyzed.slice(0, DEEP_BATCH);
    import('../../../../services/vkpi/kolPool-api').then(async ({ enqueueVideoAnalysis }) => {
      let queued = 0;
      for (const v of batch) {
        try {
          await enqueueVideoAnalysis(apiToken, poolId, Number(v.evidence_id ?? v.id));
          queued += 1;
        } catch { /* 单条失败不阻断(已入队/预算拒绝等),计数如实 */ }
      }
      setDeepState('queued');
      const rest = unanalyzed.length - batch.length;
      setAnalyzeMsg(`已入队 ${queued}/${batch.length} 条 视频深析(思考中泳道)${rest > 0 ? `;剩 ${rest} 条未析,本批析完再点继续(配额保护)` : ''}。`);
    }).catch(() => {
      setDeepState('idle');
      setAnalyzeMsg('深析入队失败——请重试或看泳道。');
    });
  };
  useEffect(() => {
    if (deepState !== 'queued') return;
    if (!unanalyzed.length) { setDeepState('idle'); return; }
    let ticks = 0;
    const timer = window.setInterval(() => {
      ticks += 1;
      if (ticks > 60) { window.clearInterval(timer); setDeepState('idle'); return; }
      setRefreshTick((t) => t + 1);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [deepState, unanalyzed.length]);

  // ③ 评论采集:job 终态轮询回执(复审令:此前只报"已入队",完成/失败无下文)。
  const [commentsState, setCommentsState] = useState<'idle' | 'busy' | 'queued'>('idle');
  const poolCommentsFetcher = useMemo(() => (post: PostPreview) => {
    const evidenceId = Number(String(post.id).split(':')[1] || 0);
    if (!apiToken || !evidenceId) return Promise.resolve({ rows: [] as Array<Record<string, unknown>>, total: 0 });
    return import('../../../../services/vkpi/kolPool-api').then(({ listKolPoolVideoComments }) =>
      listKolPoolVideoComments(apiToken, poolId, evidenceId, 200).then((resp) => ({
        rows: (resp.items || []) as Array<Record<string, unknown>>,
        total: Number((resp.page as { total?: number } | undefined)?.total || (resp.items || []).length),
      })),
    );
  }, [apiToken, poolId]);
  const startCommentsCollect = () => {
    if (!apiToken || commentsState === 'busy') return;
    setCommentsState('busy');
    import('../../../../services/vkpi/kolPool-api').then(({ enqueueKolPoolCommentsCollect }) =>
      enqueueKolPoolCommentsCollect(apiToken, poolId).then((resp) => {
        setCommentsState('queued');
        setAnalyzeMsg(resp.status === 'already_queued' ? '评论采集已在队列中——完成后此处自动回执。' : '评论采集已入队(泳道「评论采集」)——完成后此处自动回执。');
        const jobId = Number((resp as Record<string, unknown>).job_id) || 0;
        if (!jobId) return;
        void waitJobTerminal(apiToken, jobId, cancelledRef).then((terminal) => {
          if (!terminal || cancelledRef.current) return;
          setCommentsState('idle');
          if (terminal.state === 'blocked') {
            setAnalyzeMsg(`评论采集被拦:${terminal.error || '原因见泳道'}`);
            return;
          }
          setRefreshTick((t) => t + 1);
          setAnalyzeMsg('✓ 评论采集完成——点视频卡「评论明细」即见正文。');
        });
      }),
    ).catch((err) => {
      setCommentsState('idle');
      setAnalyzeMsg(String((err as Error)?.message || '评论采集入队失败').slice(0, 120));
    });
  };

  return (
    <div className="mykol-content-layer" style={{ minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
        <div style={{ fontSize: 10, color: '#8b94a3' }}>Pool 收藏 · 已采集 {videos.length} 条 · 合计播放 {totalViews.toLocaleString()}</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {kol?.profileUrl && /^https?:\/\/[^ ]*(youtube\.com|instagram\.com|tiktok\.com)/i.test(String(kol.profileUrl)) ? (
            <button type="button" onClick={startAccountAnalysis} disabled={analyzeState === 'busy' || analyzeState === 'queued'}
              style={{ fontSize: 10, color: analyzeState === 'queued' ? '#86efac' : '#c4b5fd', background: 'rgba(168,85,247,0.12)', border: '1px solid rgba(168,85,247,0.3)', borderRadius: 6, padding: '3px 8px', cursor: 'pointer' }}>
              {analyzeState === 'busy' ? '发起中…' : analyzeState === 'queued' ? '分析中…' : '账号分析 · 采集视频'}
            </button>
          ) : kol?.profileUrl ? (
            <span style={{ fontSize: 10, color: '#5b6472' }} title={String(kol.profileUrl)}>主页 URL 非 YT/IG/TT,无法账号分析</span>
          ) : null}
          {unanalyzed.length ? (
            <button type="button" onClick={startDeepAnalysis} disabled={deepState !== 'idle'}
              style={{ fontSize: 10, color: deepState === 'queued' ? '#86efac' : '#fbcfe8', background: 'rgba(236,72,153,0.10)', border: '1px solid rgba(236,72,153,0.3)', borderRadius: 6, padding: '3px 8px', cursor: 'pointer' }}>
              {deepState === 'busy' ? '入队中…' : deepState === 'queued' ? `深析中 ${analyzedCount}/${videos.length}` : `视频深析 · 前 ${Math.min(DEEP_BATCH, unanalyzed.length)}/${unanalyzed.length} 条`}
            </button>
          ) : videos.length ? (
            <span style={{ fontSize: 10, color: '#86efac' }}>✓ 全部已深析</span>
          ) : null}
          {videos.length ? (
            <button type="button" onClick={startCommentsCollect} disabled={commentsState !== 'idle'}
              style={{ fontSize: 10, color: commentsState === 'queued' ? '#86efac' : '#93c5fd', background: 'rgba(59,130,246,0.10)', border: '1px solid rgba(59,130,246,0.3)', borderRadius: 6, padding: '3px 8px', cursor: 'pointer' }}>
              {commentsState === 'busy' ? '入队中…' : commentsState === 'queued' ? '采集中…' : '采集评论'}
            </button>
          ) : null}
          {kol?.profileUrl ? <a href={kol.profileUrl} target="_blank" rel="noreferrer" style={{ fontSize: 10, color: '#67e8f9' }}>打开主页</a> : null}
        </div>
      </div>
      {analyzeMsg ? (
        <div style={{ border: analyzeState === 'error' ? '1px solid rgba(244,63,94,0.3)' : '1px solid rgba(16,185,129,0.25)', background: analyzeState === 'error' ? 'rgba(244,63,94,0.08)' : 'rgba(16,185,129,0.06)', borderRadius: 8, padding: '6px 10px', fontSize: 10, color: analyzeState === 'error' ? '#fca5a5' : '#bbf7d0', marginBottom: 8 }}>{analyzeMsg}</div>
      ) : null}
      {projects.length ? (
        <div style={{ border: '1px solid rgba(255,255,255,0.07)', borderRadius: 8, padding: '8px 10px', marginBottom: 8 }}>
          <div style={{ fontSize: 10, color: '#8b94a3', marginBottom: 6 }}>合作项目结果(来自 Projects 映射)</div>
          {projects.slice(0, 4).map((proj) => (
            <div key={proj.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 10.5, color: '#cbd5e1', padding: '2px 0' }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{proj.campaign}</span>
              <span style={{ flexShrink: 0, color: '#8b94a3' }}>阶段 {proj.stage} · 曝光 {compactNumber(proj.views)} · 证据 {proj.evidenceCount ?? proj.stageEventCount ?? 0}</span>
            </div>
          ))}
          {projects.length > 4 ? <div style={{ fontSize: 9.5, color: '#5b6472' }}>…其余 {projects.length - 4} 个项目在 Projects 查看</div> : null}
        </div>
      ) : null}
      {state === 'error' ? (
        <div style={{ border: '1px solid rgba(244,63,94,0.3)', background: 'rgba(244,63,94,0.08)', borderRadius: 8, padding: '8px 12px', fontSize: 11, color: '#fca5a5' }}>
          视频读取失败:{error} —— 请刷新或报值班。
        </div>
      ) : state === 'loading' ? (
        <div style={{ fontSize: 11, color: '#5b6472', padding: '12px 0' }}>视频载入中…</div>
      ) : !videos.length ? (
        <div style={{ fontSize: 11, color: '#5b6472', padding: '12px 0' }}>该 KOL 暂无已采集视频——点右上「账号分析 · 采集视频」即可补采(泳道可见进度,完成后此处自动回执)。</div>
      ) : (
        <EmployeeKolContentLayer
          apiToken={apiToken}
          kol={kol}
          projects={projects}
          viltroxOnly={Boolean(viltroxOnly)}
          postsOverride={poolPosts}
          analyzedCount={analyzedCount}
          subtitle={`Pool 收藏 · 视频深析 ${analyzedCount}/${videos.length}${Boolean(viltroxOnly) && !anyViltrox ? ` · 无 Viltrox 内容(共 ${videos.length} 条已采集 / ${analyzedCount} 条已分析)` : ''}`}
          commentsFetcher={poolCommentsFetcher}
        />
      )}
    </div>
  );
}
