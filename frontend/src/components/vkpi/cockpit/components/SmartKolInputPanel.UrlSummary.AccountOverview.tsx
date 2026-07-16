import { Activity, Eye, Heart, Loader2, MessageCircle, Video } from "lucide-react";

import { type VkpiKolPoolDetailBundleResponse } from "../../../../services/vkpi/kolPool-api";
import { analysisScoreColor } from "./KOLVideoAnalysisPanel";
import { sceneTimelineRowsLocal } from "./SmartKolInputPanel.derivers";
import { asRecord, cleanText, numberLabel, type Row } from "./SmartKolInputPanel.helpers";
import {
  dateLabel,
  firstSafeHttpUrl,
  safeCachedVideoUrl,
  safeHttpUrl,
  VideoPoster,
} from "./SmartKolInputPanel.UrlSummary.shared";

function dateTimeLabel(value: unknown): string {
  const raw = cleanText(value);
  if (!raw) return "";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function percentLabel(value: unknown): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) return "—";
  const percent = parsed > 0 && parsed <= 1 ? parsed * 100 : parsed;
  return `${percent.toFixed(percent >= 10 ? 1 : 2)}%`;
}

function exactImageCacheUrl(value: unknown): string {
  const raw = cleanText(value);
  if (/^\/api\/vkpi-media\/image-cache\/[a-f0-9]{64}$/i.test(raw)) return raw;
  return safeHttpUrl(raw);
}

function accountAnalysisPreview(bundle: VkpiKolPoolDetailBundleResponse | null): {
  title: string;
  summary: string;
  contentScore: number | null;
  marketingScore: number | null;
  scenes: Array<{ timestamp: string; what: string }>;
} | null {
  const items = Array.isArray(bundle?.video_analysis?.items) ? bundle.video_analysis.items : [];
  for (const item of items) {
    const entry = asRecord(item.final_entry);
    if (cleanText(entry.status).toLowerCase() !== "ready") continue;
    const result = asRecord(entry.result);
    const nested = asRecord(result.video_analysis_final_v1);
    const payload = Object.keys(nested).length ? nested : result;
    const layer1 = asRecord(payload.layer1_visual_content);
    const layer6 = asRecord(payload.layer6_flags_and_scores);
    const scores = asRecord(layer6.scores);
    const content = Number(scores.content_quality_score);
    const marketing = Number(scores.marketing_value_score ?? layer6.marketing_value_score);
    const video = asRecord(item.video);
    const scenes = sceneTimelineRowsLocal(layer1.scene_timeline)
      .slice(0, 3)
      .map((row) => ({ timestamp: row.timestamp, what: row.what }));
    return {
      title: cleanText(video.title || video.video_title || video.content_url) || "已分析视频",
      summary: cleanText(layer1.content_summary),
      contentScore: Number.isFinite(content) ? content : null,
      marketingScore: Number.isFinite(marketing) ? marketing : null,
      scenes,
    };
  }
  return null;
}

/**
 * 账号 URL 的只读内联概览。数据只来自 detail-bundle（池档案、视频证据、分析缓存）；
 * 该接口明确 provider_calls=false / llm_calls=false / write_db=false，不会因为打开结果而烧额度。
 */
export function AccountUrlInlineOverview({
  item,
  bundle,
  dossier,
  recommendation,
  loading,
  error,
  freshness,
}: {
  item: Row;
  bundle: VkpiKolPoolDetailBundleResponse | null;
  dossier: Row;
  recommendation: Row;
  loading: boolean;
  error: string;
  freshness: Row;
}) {
  const dossierVideos = Array.isArray(dossier.videos) ? dossier.videos.map((video) => asRecord(video)) : [];
  const itemVideos = Array.isArray(item.video_evidence) ? item.video_evidence.map((video) => asRecord(video)) : [];
  const videos = (dossierVideos.length ? dossierVideos : itemVideos).slice(0, 6);
  const coverage = asRecord(dossier.coverage);
  const analysisSummary = asRecord(bundle?.video_analysis?.summary);
  const totalVideoCount = Math.max(dossierVideos.length, itemVideos.length, Number(coverage.video_evidence_count) || 0);
  const readyCount = Math.max(0, Number(coverage.analyzed_final_v1_count ?? analysisSummary.ready_count) || 0);
  const pendingCount = Math.max(0, Number(analysisSummary.pending_count) || 0, totalVideoCount - readyCount);
  const qaReadyCount = Math.max(0, Number(coverage.qa_count ?? analysisSummary.qa_ready_count) || 0);
  const llmDeep = asRecord(bundle?.llm_deep_analysis);
  const llmCount = Math.max(0, Number(coverage.deep_result_count) || (
    cleanText(llmDeep.status).toLowerCase() === "ready" ? Math.max(1, Number(llmDeep.count) || 0) : 0
  ));
  const analysisPreview = accountAnalysisPreview(bundle);
  const judgment = asRecord(dossier.judgment);
  const dossierVerdict = cleanText(judgment.one_line_verdict);
  const rawFitScore = item.viltrox_fit_score == null || (typeof item.viltrox_fit_score === "string" && !item.viltrox_fit_score.trim())
    ? Number.NaN
    : Number(item.viltrox_fit_score);
  const fitScore = Number.isFinite(rawFitScore) ? Math.max(0, Math.min(100, Math.round(rawFitScore))) : null;
  const fitReason = cleanText(item.viltrox_fit_reason);
  const dataGrade = cleanText(recommendation.data_grade).toUpperCase();
  const dataGradeScore = Number(recommendation.data_grade_score);
  const whyRecommended = cleanText(recommendation.why_recommended);
  const recommendationSignals = asRecord(recommendation.signals);
  const evidenceSignalParts = [
    Number(recommendationSignals.videos) > 0 ? `视频证据 ${Number(recommendationSignals.videos)}` : "",
    Number(recommendationSignals.analyses) > 0 ? `深析 ${Number(recommendationSignals.analyses)}` : "",
    Number(recommendationSignals.projects) > 0 ? `合作项目 ${Number(recommendationSignals.projects)}` : "",
  ].filter(Boolean);
  const audience = asRecord(item.audience_estimated);
  const audienceSample = Math.max(0, Number(audience.sample_size) || 0);
  const audienceComments = Math.max(0, Number(audience.comments_scanned) || 0);
  const audienceConfidence = Number(audience.confidence);
  const updatedAt = dateTimeLabel(item.last_seen_at || item.updated_at || freshness.last_refresh_at);
  type MetricAggregate = { sum: number; count: number };
  const sumMetric = (key: string): MetricAggregate => dossierVideos.reduce<MetricAggregate>((acc, video) => {
    const value = Number(video[key]);
    return Number.isFinite(value) && value >= 0
      ? { sum: acc.sum + value, count: acc.count + 1 }
      : acc;
  }, { sum: 0, count: 0 });
  const sampledViews = sumMetric("view_count");
  const sampledLikes = sumMetric("like_count");
  const sampledComments = sumMetric("comment_count");
  const sampleMetricsComplete = sampledViews.count > 0
    && sampledLikes.count === sampledViews.count
    && sampledComments.count === sampledViews.count;
  const sampleEngagement = sampleMetricsComplete && sampledViews.sum > 0
    ? ((sampledLikes.sum + sampledComments.sum) / sampledViews.sum) * 100
    : null;
  const avgFromSample = (metric: { sum: number; count: number }, fallback: unknown) => metric.count > 0
    ? metric.sum / metric.count
    : fallback;
  const hasVideoMetricSample = sampledViews.count > 0 || sampledLikes.count > 0 || sampledComments.count > 0;
  const metricRows = [
    { label: "样本总播放", value: sampledViews.count ? numberLabel(sampledViews.sum) : "—", icon: Video, color: "text-cyan-300" },
    { label: hasVideoMetricSample ? "样本均播放" : "账号均播放", value: numberLabel(avgFromSample(sampledViews, item.avg_views)), icon: Eye, color: "text-sky-300" },
    { label: hasVideoMetricSample ? "样本均点赞" : "账号均点赞", value: numberLabel(avgFromSample(sampledLikes, item.avg_likes)), icon: Heart, color: "text-rose-300" },
    { label: hasVideoMetricSample ? "样本均评论" : "账号均评论", value: numberLabel(avgFromSample(sampledComments, item.avg_comments)), icon: MessageCircle, color: "text-violet-300" },
    {
      label: sampleEngagement != null ? "样本互动率" : "账号互动率",
      value: sampleEngagement != null
        ? `${sampleEngagement.toFixed(2)}%`
        : item.engagement_rate == null ? "—" : percentLabel(item.engagement_rate),
      icon: Activity,
      color: "text-emerald-300",
    },
  ];

  return (
    <section className="mt-2 overflow-hidden rounded-lg border border-white/[0.07] bg-black/15" data-testid="account-url-inline-overview">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] px-3 py-2">
        <div>
          <div className="text-[10.5px] font-medium text-slate-200">已入库数据概览</div>
          <div className="mt-0.5 text-[9px] text-slate-500">档案 + 视频证据 + 已有分析缓存直接展示</div>
        </div>
        <div className="text-right text-[9px] text-slate-500">
          <div>{updatedAt ? `数据更新 ${updatedAt}` : "更新时间未记录"}</div>
          <div className="mt-0.5 text-emerald-200/65">本次只读 · 不触发新抓取或 AI</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-px bg-white/[0.04] sm:grid-cols-5">
        {metricRows.map((metric) => {
          const Icon = metric.icon;
          return (
            <div key={metric.label} className="bg-slate-950/55 px-3 py-2.5">
              <div className="flex items-center gap-1 text-[9px] text-slate-500"><Icon size={10} className={metric.color} /> {metric.label}</div>
              <div className="mt-1 text-[15px] font-semibold tabular-nums text-slate-100">{metric.value || "—"}</div>
            </div>
          );
        })}
      </div>
      {hasVideoMetricSample ? (
        <div className="border-t border-white/[0.04] bg-slate-950/35 px-3 py-1.5 text-[8.5px] text-slate-500">
          KPI 口径：已入库 {dossierVideos.length} 条视频样本聚合 · 非平台全量实时值
        </div>
      ) : null}

      <section className="border-t border-white/[0.05] bg-cyan-950/[0.08] px-3 py-2.5" data-testid="account-decision-summary">
        <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="text-[10px] font-medium text-slate-200">账号分析与推荐证据</div>
            <div className="mt-0.5 text-[8.5px] text-slate-500">推荐分、数据完整度与证据覆盖分别展示，不互相冒充</div>
          </div>
          <div className="text-right text-[8.5px] text-slate-500">{updatedAt ? `证据截至 ${updatedAt}` : "证据时间未记录"}</div>
        </div>
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          {[
            ["基础档案", "已入库", "text-emerald-200"],
            ["视频证据", totalVideoCount ? `${totalVideoCount} 条` : "待补", totalVideoCount ? "text-emerald-200" : "text-slate-500"],
            ["视频深析", readyCount ? `${readyCount} 条就绪` : pendingCount ? `${pendingCount} 条待处理` : "未形成", readyCount ? "text-emerald-200" : "text-amber-200"],
            ["账号结论", llmCount || fitScore != null ? "已有结论" : "待分析", llmCount || fitScore != null ? "text-emerald-200" : "text-slate-500"],
          ].map(([label, value, color]) => (
            <div key={String(label)} className="rounded border border-white/[0.05] bg-black/20 px-2 py-2">
              <div className="text-[8px] text-slate-600">{String(label)}</div>
              <div className={`mt-0.5 text-[10px] font-medium ${String(color)}`}>{String(value)}</div>
            </div>
          ))}
        </div>
        <div className="mt-2 grid gap-px overflow-hidden rounded-md border border-white/[0.05] bg-white/[0.04] sm:grid-cols-2">
          <div className="bg-slate-950/55 px-3 py-2.5">
            <div className="text-[8.5px] text-slate-500">账号 Fit（库内模型/规则）</div>
            <div className="mt-1 text-[20px] font-bold tabular-nums" style={{ color: analysisScoreColor(fitScore) }}>{fitScore ?? "未形成"}</div>
            <div className="mt-1 line-clamp-2 text-[8px] text-slate-600">{fitReason || "尚无库内账号 Fit；不从视频分或完整度推导"}</div>
          </div>
          <div className="bg-slate-950/55 px-3 py-2.5">
            <div className="text-[8.5px] text-slate-500">数据可信度档（非 Fit）</div>
            <div className="mt-1 text-[20px] font-bold text-cyan-100">{dataGrade || "—"}</div>
            <div className="mt-1 text-[8px] text-slate-600">{Number.isFinite(dataGradeScore) ? `${dataGradeScore}/4 完整度` : "等待推荐卡证据"}</div>
          </div>
        </div>
        <div className="mt-2 rounded border border-amber-300/10 bg-amber-400/[0.035] px-2.5 py-1.5 text-[8.5px] text-amber-100/75" role="note">
          业务 outcome 未验证；账号 Fit、视频模型分和数据完整度不代表真实投放或销售效果。
        </div>
        {(whyRecommended || evidenceSignalParts.length || dossierVerdict) ? (
          <div className="mt-2 rounded border border-white/[0.05] bg-black/15 px-2.5 py-2 text-[8.5px] leading-relaxed text-slate-500">
            {whyRecommended ? <div><span className="text-slate-400">数据证据:</span> {whyRecommended}</div> : null}
            {evidenceSignalParts.length ? <div className="mt-0.5">证据沉淀: {evidenceSignalParts.join(" · ")}</div> : null}
            {dossierVerdict ? <div className="mt-0.5"><span className="text-slate-400">账号结论:</span> {dossierVerdict}</div> : null}
          </div>
        ) : null}
      </section>

      {loading ? (
        <div className="flex items-center gap-2 px-3 py-4 text-[10.5px] text-cyan-100" role="status">
          <Loader2 size={12} className="animate-spin" /> 正在读取已入库视频与分析…
        </div>
      ) : error ? (
        <div className="mx-3 my-3 rounded-md border border-amber-300/15 bg-amber-400/[0.06] px-2.5 py-2 text-[10px] text-amber-100">
          基础档案已显示；视频与分析摘要暂时读取失败。{error}
        </div>
      ) : (
        <>
          <div className="grid gap-2 border-t border-white/[0.05] p-3 lg:grid-cols-[minmax(0,1.45fr)_minmax(250px,0.55fr)]">
            <div className="min-w-0">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="inline-flex items-center gap-1 text-[10px] font-medium text-slate-300"><Video size={11} className="text-cyan-300" /> 最新视频证据</span>
                <span className="text-[9px] text-slate-500">已入库样本 {totalVideoCount} 条 · 当前展示 {videos.length} 条</span>
              </div>
              {videos.length ? (
                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {videos.map((video, index) => {
                    const evidenceId = cleanText(video.evidence_id || video.id || index);
                    const title = cleanText(video.title || video.video_title || video.content_url) || `视频 ${index + 1}`;
                    const sourceUrl = safeHttpUrl(video.content_url);
                    const posterSource = exactImageCacheUrl(video.cached_thumbnail_url)
                      || firstSafeHttpUrl(video.best_thumbnail, video.thumbnail_url);
                    const views = numberLabel(video.view_count);
                    const published = dateLabel(video.publish_date || video.posted_at);
                    const videoAnalysis = asRecord(video.analysis);
                    const analyzed = Boolean(video.has_final_v1_cache || videoAnalysis.has_final_v1);
                    const qaReady = Boolean(video.has_keyframe_qa_cache || videoAnalysis.has_qa);
                    return (
                      <article key={evidenceId || `${title}-${index}`} className="overflow-hidden rounded-md border border-white/[0.06] bg-slate-950/45">
                        <div className="relative aspect-video bg-black/50">
                          <VideoPoster source={posterSource} title={title} />
                          <div className="absolute left-1.5 top-1.5 flex flex-wrap gap-1">
                            {analyzed ? <span className="rounded bg-emerald-950/90 px-1.5 py-0.5 text-[8px] text-emerald-200">AI 已分析</span> : null}
                            {qaReady ? <span className="rounded bg-cyan-950/90 px-1.5 py-0.5 text-[8px] text-cyan-100">QA 已复核</span> : null}
                            {safeCachedVideoUrl(video.cached_video_url) ? <span className="rounded bg-sky-950/90 px-1.5 py-0.5 text-[8px] text-sky-100">R2 已缓存</span> : null}
                          </div>
                        </div>
                        <div className="p-2">
                          {sourceUrl ? (
                            <a href={sourceUrl} target="_blank" rel="noreferrer noopener" className="line-clamp-2 text-[10px] font-medium leading-snug text-slate-200 hover:text-cyan-100 hover:underline">{title}</a>
                          ) : (
                            <div className="line-clamp-2 text-[10px] font-medium leading-snug text-slate-200">{title}</div>
                          )}
                          <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[8.5px] text-slate-500">
                            {views ? <span>{views} 播放</span> : null}
                            {published ? <span>{published}</span> : null}
                            {!views && !published ? <span>表现数据待补</span> : null}
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-md border border-dashed border-white/[0.07] px-3 py-6 text-center text-[10px] text-slate-500">
                  尚无已入库视频证据；可保留基础档案，需要时再发现历史视频。
                </div>
              )}
            </div>

            <aside className="rounded-md border border-white/[0.06] bg-slate-950/35 p-2.5">
              <div className="mb-2 text-[10px] font-medium text-slate-300">分析就绪度</div>
              <div className="grid grid-cols-2 gap-1.5">
                {[
                  ["深析就绪", readyCount, "text-emerald-200"],
                  ["尚未深析", pendingCount, "text-amber-200"],
                  ["关键帧 QA", qaReadyCount, "text-cyan-200"],
                  ["账号结论", llmCount, "text-violet-200"],
                ].map(([label, count, color]) => (
                  <div key={String(label)} className="rounded border border-white/[0.05] bg-black/20 px-2 py-2">
                    <div className={`text-[14px] font-semibold tabular-nums ${String(color)}`}>{Number(count)}</div>
                    <div className="mt-0.5 text-[8.5px] text-slate-500">{String(label)}</div>
                  </div>
                ))}
              </div>
              {analysisPreview ? (
                <div className="mt-2 rounded-md border border-emerald-300/10 bg-emerald-400/[0.035] px-2.5 py-2">
                  <div className="text-[9px] font-medium text-emerald-100">已完成分析速览</div>
                  <div className="mt-1 line-clamp-2 text-[9.5px] text-slate-300">{analysisPreview.title}</div>
                  {(analysisPreview.contentScore != null || analysisPreview.marketingScore != null) ? (
                    <div className="mt-1.5 flex gap-1.5 text-[8.5px]">
                      {analysisPreview.contentScore != null ? <span className="rounded bg-black/25 px-1.5 py-0.5 text-cyan-100">内容 {analysisPreview.contentScore}</span> : null}
                      {analysisPreview.marketingScore != null ? <span className="rounded bg-black/25 px-1.5 py-0.5 text-violet-100">投放 {analysisPreview.marketingScore}</span> : null}
                    </div>
                  ) : null}
                  {analysisPreview.summary ? <p className="mt-1.5 line-clamp-3 text-[9px] leading-relaxed text-slate-400">{analysisPreview.summary}</p> : null}
                  {analysisPreview.scenes.length ? (
                    <div className="mt-1.5 space-y-1 border-t border-white/[0.05] pt-1.5">
                      {analysisPreview.scenes.map((scene, index) => (
                        <div key={`${scene.timestamp}-${index}`} className="flex gap-1.5 text-[8.5px]">
                          <span className="shrink-0 font-mono text-cyan-200">{scene.timestamp || "—"}</span>
                          <span className="line-clamp-1 text-slate-400">{scene.what || "—"}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="mt-2 rounded-md border border-dashed border-white/[0.07] px-2.5 py-3 text-[9px] leading-relaxed text-slate-500">
                  {pendingCount > 0
                    ? "基础数据已展示；其余 AI 视频深析尚未就绪（可能未提交或仍在处理）。"
                    : "当前尚无已完成的 AI 视频深析；不用等待也可直接查看基础数据。"}
                </div>
              )}
              {dossierVerdict && !analysisPreview?.summary ? (
                <div className="mt-2 rounded-md border border-violet-300/10 bg-violet-400/[0.035] px-2.5 py-2">
                  <div className="text-[8.5px] text-violet-200">已有账号结论</div>
                  <p className="mt-1 text-[9px] leading-relaxed text-slate-400">{dossierVerdict}</p>
                </div>
              ) : null}
              {audienceSample > 0 ? (
                <div className="mt-2 rounded-md border border-amber-300/10 bg-amber-400/[0.035] px-2.5 py-2">
                  <div className="text-[8.5px] font-medium text-amber-100">评论者样本估算（非全体粉丝）</div>
                  <div className="mt-1 flex flex-wrap gap-x-2 text-[9px] text-slate-400">
                    <span>样本 {audienceSample}</span>
                    {audienceComments ? <span>扫描评论 {audienceComments}</span> : null}
                    {Number.isFinite(audienceConfidence) ? <span>置信 {Math.round(audienceConfidence * 100)}%</span> : null}
                  </div>
                </div>
              ) : null}
            </aside>
          </div>
        </>
      )}
    </section>
  );
}
