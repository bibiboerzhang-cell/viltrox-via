import { useEffect, useMemo, useState } from 'react';
import { BarChart3, BookOpen, ExternalLink, RefreshCw, ShoppingCart, Sparkles, Video } from 'lucide-react';
import { runSkill, type SkillRunResult } from '../../../../../services/vkpi/skills-api';
import { formatLargeNum, formatMoneyShort, healthColor } from '../projectDeliverableStyle';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import type { VkpiProjectRetrospectiveResult, VkpiProjectVideoAnalysisCacheResponse } from '../../../../../services/vkpi/projects-api';
import { healthForRows, stageIndex, type ProjectStatsSummary } from '../../../../../domains/projects';
import { retrospectiveNumberField, retrospectiveVideoTitle } from '../ProjectDetailTabs.shared';
import {
  analysisItemsForRow,
  buildAnalysisItemLookup,
  buildAnalysisItemMap,
  buildRetrospectiveDraftText,
  qaItemForAnalysis,
  retrospectiveRowInitial,
  retrospectiveVideoUrl,
  retrospectiveWatchTime,
} from './CampaignRetrospectiveTab.helpers';
import { ProjectVideoAnalysisCard, RetrospectiveCardErrorBoundary } from './CampaignRetrospectiveTab.Sections';

export function CampaignRetrospectiveTab({
  project,
  rows,
  stats,
  health,
  apiToken,
  videoAnalysisCache,
  videoQaCache,
  videoAnalysisLoading,
  videoAnalysisError,
  videoQaError,
  videoAnalysisAutoRefreshStopped,
  onRefreshVideoAnalysis,
  retrospective,
  retrospectiveLastJob,
  retrospectiveGenerating,
  onGenerateRetrospective,
  onCopy,
  onPendingAction,
}: {
  project: VkpiProjectRow;
  apiToken?: string;
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  health: ReturnType<typeof healthForRows>;
  videoAnalysisCache?: VkpiProjectVideoAnalysisCacheResponse | null;
  videoQaCache?: VkpiProjectVideoAnalysisCacheResponse | null;
  videoAnalysisLoading?: boolean;
  videoAnalysisError?: string;
  videoQaError?: string;
  videoAnalysisAutoRefreshStopped?: string;
  onRefreshVideoAnalysis?: () => void;
  retrospective?: { result?: VkpiProjectRetrospectiveResult; model?: string; updated_at?: string } | null;
  retrospectiveLastJob?: { status?: string; last_error?: string | null } | null;
  retrospectiveGenerating?: boolean;
  onGenerateRetrospective?: () => void | Promise<void>;
  onCopy: (text: string, label: string) => Promise<void>;
  onPendingAction: (label: string) => void;
}) {
  const projectTitle = project.campaign || project.productName || '未命名项目';
  const projectHealth = project.healthScore ?? health.score;
  const publishedKols = rows
    .filter((row) => stageIndex(row.stage) >= stageIndex('published') || (row.evidenceCount || 0) > 0 || (row.views || 0) > 0)
    .sort((a, b) => ((b.views || 0) - (a.views || 0)) || ((b.gmv || 0) - (a.gmv || 0)));
  const withShopify = publishedKols.filter((row) => Boolean(row.shopifyLink || row.clicks || row.orders || row.gmv));
  const withoutShopify = publishedKols.filter((row) => !withShopify.includes(row));
  const retrospectiveDraft = buildRetrospectiveDraftText(project, rows, stats, projectHealth, publishedKols, withShopify, withoutShopify);
  const analysisItemMap = useMemo(() => buildAnalysisItemMap(videoAnalysisCache?.items || []), [videoAnalysisCache?.items]);
  const qaItemLookup = useMemo(() => buildAnalysisItemLookup(videoQaCache?.items || []), [videoQaCache?.items]);
  const analysisSummary = videoAnalysisCache?.summary;

  function compositeScore(row: VkpiProjectRow) {
    const hasShopify = Boolean(row.shopifyLink || row.clicks || row.orders || row.gmv);
    if (!hasShopify || !(row.views || 0)) return null;
    const shares = retrospectiveNumberField(row, ['shares', 'shareCount', 'share_count']);
    const viewsNorm = Math.min((row.views || 0) / 100000, 10) * 4;
    const engageNorm = Math.min(((row.likes || 0) + (row.comments || 0) * 5 + shares) / 5000, 10) * 2;
    const clickNorm = Math.min((row.clicks || 0) / 100, 10) * 2;
    const gmvNorm = Math.min((row.gmv || 0) / 500, 10) * 2;
    return Math.round(Math.min(100, viewsNorm + engageNorm + clickNorm + gmvNorm));
  }

  const retroResult = retrospective?.result;
  const retroProvenance = retroResult?.provenance;
  const retroFailed = !retroResult && (retrospectiveLastJob?.status === 'failed' || retrospectiveLastJob?.status === 'blocked');

  // N2 Skill 触发:跑 roi_review skill 给本项目算 ROI + 下一步建议(默认走规则,不烧 LLM;红线零触 fit)。
  const [roiResult, setRoiResult] = useState<SkillRunResult | null>(null);
  const [roiBusy, setRoiBusy] = useState(false);
  const [roiError, setRoiError] = useState('');
  const projectIdNum = Number(project.id);
  const canRunRoiSkill = Boolean(apiToken) && Number.isFinite(projectIdNum) && projectIdNum > 0;

  useEffect(() => {
    setRoiResult(null);
    setRoiError('');
    setRoiBusy(false);
  }, [project.id]);

  function handleRunRoiSkill() {
    if (!apiToken || !canRunRoiSkill || roiBusy) return;
    setRoiBusy(true);
    setRoiError('');
    setRoiResult(null);
    void runSkill(apiToken, 'roi_review', { project_id: projectIdNum })
      .then((res) => {
        setRoiResult(res && typeof res === 'object' ? res : null);
        const out = (res && typeof res.output === 'object' ? res.output : null) as Record<string, unknown> | null;
        const status = out && typeof out.status === 'string' ? out.status : '';
        if (status === 'invalid_input' || status === 'not_found') {
          setRoiError(status === 'not_found' ? '未找到该项目的归因数据。' : '入参无效,无法运行 ROI 复盘。');
        }
      })
      .catch((err: unknown) => {
        // 人话化:/skills/{id}/run 是本地分支功能,线上后端多半没有该路由(404)→ 如实告知,不甩原始报错。
        const status = (err as any) && typeof (err as any).status === 'number' ? (err as any).status : 0;
        const raw = err instanceof Error ? err.message : '';
        setRoiError(
          status === 404 || /404|not found/i.test(raw)
            ? 'Skill 后端未上线(本地分支功能),ROI 复盘暂不可用。'
            : (raw || '跑 Skill 失败,请稍后重试。'),
        );
      })
      .finally(() => setRoiBusy(false));
  }

  return (
    <div className="p-4 space-y-4" aria-label="项目复盘">
      <div className="rounded-lg border border-purple-500/30 bg-gradient-to-br from-purple-500/10 to-emerald-500/5 p-4">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-full bg-purple-500/20 flex items-center justify-center shrink-0">
            <BookOpen size={17} className="text-purple-300" />
          </div>
          <div className="flex-1">
            <div className="flex items-center justify-between gap-3 mb-1.5">
              <div className="flex items-center gap-2">
                <div className="text-[12px] font-semibold text-white">项目复盘总结</div>
                {retroResult ? (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-200" title="由 LLM 聚合项目下所有成品视频分析生成；分值类指标未定标，仅供参考">AI 聚合 · 未定标</span>
                ) : (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-white/[0.06] text-slate-400" title="尚未生成项目级 AI 复盘，下方为前端模板草稿(非 AI 产物)">模板 · 非 AI</span>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {onGenerateRetrospective ? (
                  <button
                    type="button"
                    className="px-2.5 py-1 rounded-md bg-emerald-500/90 hover:bg-emerald-500 text-white text-[10.5px] font-medium flex items-center gap-1 disabled:opacity-50"
                    onClick={() => void onGenerateRetrospective()}
                    disabled={Boolean(retrospectiveGenerating)}
                  >
                    <Sparkles size={10} />{retrospectiveGenerating ? '生成中…' : retroResult ? '重新生成' : '生成项目复盘'}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="px-2.5 py-1 rounded-md bg-purple-500/15 hover:bg-purple-500/25 text-purple-200 text-[10.5px] font-medium flex items-center gap-1"
                  onClick={() => (onCopy ? void onCopy(retroResult?.insight_text || retrospectiveDraft, retroResult ? '复盘聚合' : '复盘草稿') : onPendingAction('复制复盘'))}
                >
                  <BookOpen size={10} />复制
                </button>
              </div>
            </div>
            {retroResult ? (
              <div className="space-y-2">
                <div className="text-[10.5px] text-slate-200 leading-relaxed whitespace-pre-wrap">{retroResult.insight_text}</div>
                {(retroResult.highlights?.length || retroResult.risks?.length || retroResult.next_steps?.length) ? (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                    {([['亮点', retroResult.highlights, '#10b981'], ['风险', retroResult.risks, '#fb7185'], ['下一步', retroResult.next_steps, '#a855f7']] as const).map(([label, items, color]) => (
                      <div key={label} className="rounded-md border border-white/[0.05] bg-black/20 p-2">
                        <div className="text-[9px] mb-1" style={{ color }}>{label}</div>
                        <ul className="space-y-0.5">
                          {(items || []).map((it, i) => <li key={i} className="text-[10px] text-slate-300 leading-snug">· {it}</li>)}
                          {!(items || []).length ? <li className="text-[10px] text-slate-600">—</li> : null}
                        </ul>
                      </div>
                    ))}
                  </div>
                ) : null}
                <div className="text-[9px] text-slate-500">
                  来源:聚合 {retroProvenance?.video_count ?? 0} 个成品视频(按曝光 Top-{retroProvenance?.top_n ?? 15}) · 模型 {retroProvenance?.model || retrospective?.model || '-'} · {retroProvenance?.generated_at || retrospective?.updated_at || ''}
                </div>
              </div>
            ) : (
            <div className="text-[10.5px] text-slate-300 leading-relaxed">
              {retroFailed ? <span className="text-amber-400">上次生成未完成{retrospectiveLastJob?.last_error ? `(${retrospectiveLastJob.last_error})` : ''},可点「生成项目复盘」重试。<br /></span> : null}
              <span className="text-slate-500">以下为模板草稿(非 AI):</span> 项目 {projectTitle} · 健康度{' '}
              <span className="font-bold" style={{ color: healthColor(projectHealth) }}>{projectHealth}</span>
              {` · ${publishedKols.length}/${rows.length} 已发布。`}
              <br />
              {withShopify.length > 0 ? `${withShopify.length} 个 KOL 已接入 Shopify 归因。` : null}
              {withoutShopify.length > 0 ? (
                <span className="text-amber-400">
                  {`${withoutShopify.length} 个未接 Shopify,不参与 GMV / ROI 综合得分。`}
                </span>
              ) : null}
              {publishedKols.length === 0 ? '等待 KOL 推进到「已发布」阶段后开始复盘。' : null}
            </div>
            )}
            <div className="mt-3 grid grid-cols-2 md:grid-cols-6 gap-2">
              {[
                ['成品视频', analysisSummary?.evidence_count ?? 0, '#06b6d4'],
                ['已分析', analysisSummary?.ready_count ?? 0, '#10b981'],
                ['处理中', analysisSummary?.pending_count ?? 0, '#facc15'],
                ['质量待复核', analysisSummary?.quality_incomplete_count ?? analysisSummary?.state_counts?.quality_incomplete ?? 0, '#fb7185'],
                ['历史待核验', analysisSummary?.legacy_unverified_count ?? analysisSummary?.state_counts?.legacy_unverified ?? 0, '#f59e0b'],
                ['后续维度', '沟通/合同/时效/反馈', '#a855f7'],
              ].map(([label, value, color]) => (
                <div key={label as string} className="rounded-md border border-white/[0.05] bg-black/20 px-2.5 py-2">
                  <div className="text-[9px] text-slate-500 mb-0.5">{label as string}</div>
                  <div className="text-[12px] font-semibold" style={{ color: color as string }}>{String(value)}</div>
                </div>
              ))}
            </div>
            {videoAnalysisError ? <div className="mt-2 text-[10px] text-rose-300">final_v1 缓存读取失败：{videoAnalysisError}</div> : null}
            {videoQaError ? <div className="mt-1 text-[10px] text-amber-300">关键帧 QA 读取失败：{videoQaError}</div> : null}
            <div className="mt-2 flex items-center gap-2">
              {onRefreshVideoAnalysis ? (
                <button
                  type="button"
                  onClick={onRefreshVideoAnalysis}
                  className="inline-flex items-center gap-1 rounded border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[10px] text-slate-300 hover:text-white"
                  title="只重新读取缓存与任务状态，不会新建分析任务"
                >
                  <RefreshCw size={10} /> 刷新状态
                </button>
              ) : null}
              <span className="text-[9.5px] text-slate-500">仅刷新状态，不会触发新分析。</span>
            </div>
            {videoAnalysisAutoRefreshStopped ? <div className="mt-1 text-[10px] text-amber-300">{videoAnalysisAutoRefreshStopped}</div> : null}

            {canRunRoiSkill ? (
              <RoiReviewSkillBlock result={roiResult} busy={roiBusy} error={roiError} onRun={handleRunRoiSkill} />
            ) : null}
          </div>
        </div>
      </div>

      {publishedKols.length === 0 ? (
        <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
          <BookOpen size={24} className="text-slate-600 mx-auto mb-2" />
          <div className="text-[11.5px] text-slate-400">等待 KOL 推进到「已发布」阶段后开始复盘</div>
        </div>
      ) : (
        <div className="space-y-3">
          {publishedKols.map((row) => {
            const hasShopify = Boolean(row.shopifyLink || row.clicks || row.orders || row.gmv);
            const score = compositeScore(row);
            const displayName = row.kolName || row.kolHandle || 'Unknown';
            const handle = row.kolHandle || displayName;
            const videoUrl = retrospectiveVideoUrl(row);
            const videoTitle = retrospectiveVideoTitle(row, projectTitle);
            const shares = retrospectiveNumberField(row, ['shares', 'shareCount', 'share_count']);
            const analysisItems = analysisItemsForRow(row, analysisItemMap);

            return (
              <div key={row.id} className={`rounded-lg border p-4 ${hasShopify ? 'border-white/[0.06] bg-white/[0.015]' : 'border-white/[0.04] bg-white/[0.008]'}`}>
                <div className="flex items-center gap-3 mb-3">
                  {row.kolAvatar ? (
                    <img src={row.kolAvatar} alt={displayName} className="w-10 h-10 rounded-full object-cover shrink-0" />
                  ) : (
                    <div
                      className="w-10 h-10 rounded-full flex items-center justify-center text-[12px] font-bold text-white shrink-0"
                      style={{ background: hasShopify ? 'linear-gradient(135deg,#10b981,#06b6d4)' : 'linear-gradient(135deg,#64748b,#475569)' }}
                    >
                      {retrospectiveRowInitial(row)}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <div className="text-[12.5px] font-semibold text-white truncate">{displayName}</div>
                      {!hasShopify ? <span className="text-[9.5px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300">未接归因</span> : null}
                    </div>
                    <div className="text-[10px] text-slate-500 truncate">{row.platform} · {handle}</div>
                  </div>
                  {hasShopify && score !== null ? (
                    <div className="text-right shrink-0">
                      <div className="text-[10px] text-slate-500 mb-0.5">综合得分</div>
                      <div className="text-[26px] font-bold tabular-nums leading-none" style={{ color: healthColor(score) }}>{score}</div>
                    </div>
                  ) : null}
                </div>

                <div className="flex items-center gap-2 mb-3 p-2 rounded bg-black/30">
                  <div className="w-12 h-9 rounded bg-gradient-to-br from-purple-500/30 to-cyan-500/30 flex items-center justify-center shrink-0">
                    <Video size={14} className="text-white" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] text-white truncate">{videoTitle}</div>
                    <div className="text-[9.5px] text-slate-500">播放时长 {retrospectiveWatchTime(row)}</div>
                  </div>
                  {videoUrl ? (
                    <a href={videoUrl} target="_blank" rel="noreferrer" className="text-[10px] text-cyan-300 flex items-center gap-1 shrink-0">
                      <ExternalLink size={10} /> 打开
                    </a>
                  ) : (
                    <span className="text-[10px] text-slate-600 flex items-center gap-1 shrink-0" title="暂无视频链接">
                      <ExternalLink size={10} /> 打开
                    </span>
                  )}
                </div>

                <div className="flex items-start gap-2 mb-3 px-2.5 py-2 rounded bg-purple-500/5">
                  <Sparkles size={11} className="text-purple-300 mt-0.5 shrink-0" />
                  <div className="text-[10.5px] text-slate-300 leading-relaxed">
                    <span className="text-purple-300 font-medium">项目表现摘要: </span>
                    {hasShopify
                      ? `内容表现已汇总 · 曝光 ${formatLargeNum(row.views)} · 互动 ${formatLargeNum((row.likes || 0) + (row.comments || 0) + shares)} · Shopify 点击 ${formatLargeNum(row.clicks || 0)} · 归因 GMV ${formatMoneyShort(row.gmv)}`
                      : `内容表现已汇总 · 曝光 ${formatLargeNum(row.views)} · 互动 ${formatLargeNum((row.likes || 0) + (row.comments || 0) + shares)} · 尚未接 Shopify 归因,综合得分暂不计算`}
                  </div>
                </div>

                <div className="mb-3 rounded-lg border border-white/[0.05] bg-black/20 p-3">
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div>
                      <div className="text-[10px] text-slate-500">复盘维度 · 成品分析</div>
                      <div className="text-[11.5px] text-white font-semibold">final_v1 视频分析</div>
                    </div>
                    <span className="text-[9.5px] text-slate-500">后续可叠加沟通截图 / PDF合同 / 时效 / 市场反馈</span>
                  </div>
                  {videoAnalysisLoading ? (
                    <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3 text-[10.5px] text-slate-400">读取 final_v1 缓存...</div>
                  ) : analysisItems.length ? (
                    <div className="space-y-2">
                      {analysisItems.map((item) => (
                        <RetrospectiveCardErrorBoundary
                          key={String(item.evidence_id ?? item.content_url ?? `${row.id}:analysis`)}
                          label={`${displayName} evidence #${item.evidence_id || '-'}`}
                        >
                          <ProjectVideoAnalysisCard
                            row={row}
                            item={item}
                            qaItem={qaItemForAnalysis(item, qaItemLookup)}
                          />
                        </RetrospectiveCardErrorBoundary>
                      ))}
                    </div>
                  ) : (
                    <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3">
                      <div className="text-[10.5px] text-slate-400">暂无分析</div>
                      <div className="text-[9.5px] text-slate-600 mt-1">未匹配到该 KOL 的 video evidence 或 final_v1 队列记录。</div>
                    </div>
                  )}
                </div>

                <div className="grid grid-cols-3 md:grid-cols-5 gap-3">
                  {[
                    ['播放', formatLargeNum(row.views), '#06b6d4'],
                    ['点赞', formatLargeNum(row.likes || 0), '#ec4899'],
                    ['评论', formatLargeNum(row.comments || 0), '#a855f7'],
                    ['分享', formatLargeNum(shares), '#fb923c'],
                    hasShopify ? ['Shopify 点击', formatLargeNum(row.clicks || 0), '#10b981'] : ['Shopify', '—', '#64748b'],
                  ].map(([label, value, color]) => (
                    <div key={label}>
                      <div className="text-[9.5px] text-slate-500 mb-0.5">{label}</div>
                      <div className="text-[13px] font-semibold tabular-nums" style={{ color }}>{value}</div>
                    </div>
                  ))}
                </div>

                {hasShopify ? (
                  <div className="mt-3 pt-3 border-t border-white/[0.04] flex items-center gap-2 px-2 py-1.5 rounded bg-emerald-500/5">
                    <ShoppingCart size={11} className="text-emerald-300" />
                    <div className="flex-1 text-[10.5px] text-emerald-200">
                      Shopify 归因: {formatLargeNum(row.orders || 0)} 单 · GMV <span className="font-bold">{formatMoneyShort(row.gmv)}</span>
                    </div>
                    {row.shopifyLink ? (
                      <div className="text-[10px] text-slate-500 font-mono truncate max-w-[200px]">{row.shopifyLink.replace('https://', '')}</div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── N2 跑 Skill 区块:roi_review 触发按钮 + loading/错误/结果三态渲染 ──
function RoiReviewSkillBlock({ result, busy, error, onRun }: {
  result: SkillRunResult | null;
  busy: boolean;
  error: string;
  onRun: () => void;
}) {
  const output = (result && typeof result.output === 'object' ? result.output : null) as Record<string, unknown> | null;
  const roi = (output && typeof output.roi === 'object' ? output.roi : null) as Record<string, unknown> | null;
  const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const centsToYuan = (cents: number | null): string => (cents == null ? '—' : `¥${(cents / 100).toLocaleString('en-US', { maximumFractionDigits: 0 })}`);
  const spend = roi ? num(roi.spend_cents) : null;
  const gmv = roi ? num(roi.attributed_gmv_cents) : null;
  const roiRatio = roi ? num(roi.roi_ratio) : null;
  const labels = output && Array.isArray(output.outcome_labels) ? output.outcome_labels.map((x) => String(x)).filter(Boolean) : [];
  const nextAction = output && typeof output.next_action === 'string' ? output.next_action : '';
  const confidence = output ? num(output.confidence) : null;
  const missingData = output ? output.missing_data === true : false;
  const status = output && typeof output.status === 'string' ? output.status : '';
  const isError = status === 'invalid_input' || status === 'not_found';
  const showResult = Boolean(result) && Boolean(output) && !isError;

  return (
    <div className="mt-3 rounded-lg border border-cyan-500/25 bg-cyan-500/[0.04] p-3">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <BarChart3 size={13} className="text-cyan-300" />
          <div className="text-[11.5px] font-semibold text-white">ROI 复盘 · Skill</div>
          {result?.skill_run_id != null ? (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-white/[0.06] text-slate-400">run #{result.skill_run_id}</span>
          ) : null}
        </div>
        <button
          type="button"
          className="px-2.5 py-1 rounded-md bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-200 text-[10.5px] font-medium flex items-center gap-1 disabled:opacity-50"
          onClick={onRun}
          disabled={busy}
        >
          <Sparkles size={10} />{busy ? '跑 Skill 中…' : showResult ? '重新运行' : '跑 Skill·ROI 复盘'}
        </button>
      </div>
      {error ? <div className="text-[10px] text-rose-300">{error}</div> : null}
      {showResult ? (
        <div className="space-y-2">
          <div className="grid grid-cols-3 gap-2">
            {([['花费', centsToYuan(spend), '#fb923c'], ['归因 GMV', centsToYuan(gmv), '#10b981'], ['ROI', roiRatio == null ? '—' : `${(roiRatio * 100).toFixed(0)}%`, '#06b6d4']] as const).map(([label, value, color]) => (
              <div key={label} className="rounded-md border border-white/[0.05] bg-black/20 px-2.5 py-2">
                <div className="text-[9px] text-slate-500 mb-0.5">{label}</div>
                <div className="text-[12px] font-semibold tabular-nums" style={{ color }}>{value}</div>
              </div>
            ))}
          </div>
          {nextAction ? (
            <div className="text-[10.5px] text-slate-200">
              <span className="text-cyan-300 font-medium">下一步建议: </span>
              {nextAction}
              {confidence != null ? <span className="text-slate-500"> · 置信度 {(confidence * 100).toFixed(0)}%</span> : null}
            </div>
          ) : null}
          {labels.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {labels.map((label) => (
                <span key={label} className="text-[9.5px] px-1.5 py-0.5 rounded bg-white/[0.06] text-slate-300">{label}</span>
              ))}
            </div>
          ) : null}
          {missingData ? <div className="text-[9.5px] text-amber-300">该项目暂无完整归因数据(无真实 GMV),结果仅供参考。</div> : null}
        </div>
      ) : null}
    </div>
  );
}
