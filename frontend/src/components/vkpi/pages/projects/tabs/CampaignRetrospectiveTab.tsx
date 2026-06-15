import { Component, useMemo, type ErrorInfo, type ReactNode } from 'react';
import { BookOpen, ExternalLink, ShoppingCart, Sparkles, Video } from 'lucide-react';
import { formatLargeNum, formatMoneyShort, healthColor } from '../projectDeliverableStyle';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import type { VkpiAnalysisCacheEntry, VkpiProjectRetrospectiveResult, VkpiProjectVideoAnalysisCacheItem, VkpiProjectVideoAnalysisCacheResponse } from '../../../../../services/vkpi/projects-api';
import { healthForRows, stageIndex, type ProjectStatsSummary } from '../../../../../domains/projects';
import { retrospectiveNumberField, retrospectiveTextField, retrospectiveVideoTitle } from '../ProjectDetailTabs.shared';

function buildRetrospectiveDraftText(
  project: VkpiProjectRow,
  rows: VkpiProjectRow[],
  stats: ProjectStatsSummary,
  healthScore: number,
  publishedKols: VkpiProjectRow[],
  withShopify: VkpiProjectRow[],
  withoutShopify: VkpiProjectRow[],
) {
  const projectTitle = project.campaign || project.productName || '未命名项目';
  const topRows = [...publishedKols].sort((a, b) => ((b.views || 0) - (a.views || 0))).slice(0, 5);
  const topLines = topRows.length
    ? topRows.map((row, index) => `${index + 1}. ${row.kolHandle || row.kolName || 'Unknown'} · ${row.platform} · ${formatLargeNum(row.views)} 播放`).join('\n')
    : '暂无已发布内容。';

  return [
    `# ${projectTitle} 复盘草稿`,
    '',
    `健康度: ${healthScore}`,
    `参与 KOL: ${rows.length}`,
    `已发布 KOL: ${publishedKols.length}`,
    `总曝光: ${formatLargeNum(stats.views)}`,
    `总点击: ${formatLargeNum(stats.clicks)}`,
    `归因 GMV: ${formatMoneyShort(stats.gmv)}`,
    // stats.roi 是比值(gmv/cost);同页 KPI 口径显示百分数,这里同样 ×100,不再把比值直接当百分数。
    `ROI: ${stats.roi == null ? '待成本/归因补齐' : `${(stats.roi * 100).toFixed(1)}%`}`,
    '',
    '## 归因接入',
    `已接 Shopify: ${withShopify.length}`,
    `未接 Shopify: ${withoutShopify.length}`,
    '',
    '## 内容表现 Top 5',
    topLines,
    '',
    '## 下一步',
    withoutShopify.length > 0 ? '- 补齐未接 Shopify 的归因链接，避免 ROI 偏低。' : '- Shopify 归因已覆盖已发布内容，继续观察订单变化。',
    publishedKols.length < rows.length ? '- 推进未发布 KOL 到内容发布节点。' : '- 所有 KOL 已进入发布/复盘口径。',
  ].join('\n');
}

function retrospectiveVideoUrl(row: VkpiProjectRow) {
  return retrospectiveTextField(row, ['contentUrl', 'content_url', 'videoUrl', 'video_url', 'postUrl', 'post_url', 'evidenceUrl', 'evidence_url']);
}

function retrospectiveWatchTime(row: VkpiProjectRow) {
  const direct = retrospectiveTextField(row, ['watchTime', 'watch_time', 'duration', 'durationLabel', 'duration_label']);
  if (direct) return direct;
  const seconds = retrospectiveNumberField(row, ['durationSeconds', 'duration_seconds']);
  if (!seconds) return '—';
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60).toString().padStart(2, '0');
  return `${minutes}:${rest}`;
}

function retrospectiveRowInitial(row: VkpiProjectRow) {
  return (row.kolName || row.kolHandle || '?').trim().charAt(0).toUpperCase() || '?';
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function textFrom(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (Array.isArray(value)) return value.map(textFrom).filter(Boolean).join(' / ');
  const record = asRecord(value);
  for (const key of ['rationale', 'evaluation', 'summary', 'text', 'reason', 'value', 'evidence', 'flag']) {
    if (!(key in record)) continue;
    const text = textFrom(record[key]);
    if (text) return text;
  }
  return '';
}

function normaliseScore(value: unknown, fallback?: unknown) {
  const source = value ?? fallback;
  if (typeof source === 'number' && Number.isFinite(source)) return { score: Math.round(source), rationale: '', confidence: null as number | null };
  const record = asRecord(source);
  const rawScore = Number(record.score ?? record.value);
  const rawConfidence = Number(record.confidence);
  return {
    score: Number.isFinite(rawScore) ? Math.round(rawScore) : null,
    rationale: textFrom(record.rationale ?? record.evaluation ?? record.reason),
    confidence: Number.isFinite(rawConfidence) ? rawConfidence : null,
  };
}

function analysisScoreColor(score: number | null) {
  if (score == null) return '#94a3b8';
  if (score >= 80) return '#34d399';
  if (score >= 60) return '#facc15';
  return '#fb7185';
}

function finalV1Payload(entry?: VkpiAnalysisCacheEntry | null) {
  const result = asRecord(entry?.result);
  return asRecord(result.video_analysis_final_v1).layer1_visual_content ? asRecord(result.video_analysis_final_v1) : result;
}

function layerValue(layer: Record<string, unknown>, key: string, scoreFallback?: unknown) {
  const raw = layer[key];
  const directScore = normaliseScore(raw);
  const fallbackScore = normaliseScore(scoreFallback);
  const score = directScore.score != null ? directScore : fallbackScore;
  return {
    score: score.score,
    confidence: score.confidence,
    text: textFrom(raw) || score.rationale || '证据不足，等待更多分析结果。',
  };
}

function compactText(value: string, max = 150) {
  if (!value) return '暂无明确结论';
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function firstText(...values: unknown[]) {
  for (const value of values) {
    const text = textFrom(value);
    if (text) return text;
  }
  return '';
}

function normaliseRiskFlags(value: unknown) {
  if (Array.isArray(value)) {
    return value.map((item, index) => {
      const record = asRecord(item);
      const flag = textFrom(record.flag) || `risk_${index + 1}`;
      const evidence = textFrom(record.evidence);
      const severity = String(record.severity || '').toLowerCase();
      return {
        label: evidence ? `${flag}: ${evidence}` : flag,
        severity,
      };
    }).filter((item) => item.label);
  }
  const text = textFrom(value);
  return text ? [{ label: text, severity: /高|high|严重/i.test(text) ? 'high' : '' }] : [];
}

const QA_CHECK_LABELS: Record<string, string> = {
  product_identity: '型号',
  brand_exposure: '品牌',
  competitor_context: '竞品',
  inscription_or_model_text: '铭文',
  title_image_consistency: '标题画面',
};

function finalV1QaPayload(entry?: VkpiAnalysisCacheEntry | null) {
  const result = asRecord(entry?.result);
  const direct = asRecord(result.final_v1_keyframe_qa);
  if (Object.keys(direct).length) return direct;
  const nested = asRecord(asRecord(result.video_analysis_final_v1_keyframe_qa).final_v1_keyframe_qa);
  if (Object.keys(nested).length) return nested;
  if ('qa_pass' in result || 'checks' in result || 'issues' in result || 'score_correction' in result) return result;
  return {};
}

function qaBoolean(value: unknown) {
  if (typeof value === 'boolean') return value;
  const text = String(value ?? '').trim().toLowerCase();
  if (['true', '1', 'yes', 'pass', 'passed'].includes(text)) return true;
  if (['false', '0', 'no', 'fail', 'failed'].includes(text)) return false;
  return null;
}

function qaStatusLabel(value: unknown) {
  const status = textFrom(value).toLowerCase();
  if (status === 'pass') return '通过';
  if (status === 'warn') return '提醒';
  if (status === 'fail') return '异常';
  if (status === 'unknown') return '未知';
  return status || '未知';
}

function qaStatusClass(value: unknown) {
  const status = textFrom(value).toLowerCase();
  if (status === 'pass') return 'bg-emerald-500/10 text-emerald-200 border-emerald-400/15';
  if (status === 'fail') return 'bg-rose-500/12 text-rose-200 border-rose-400/20';
  if (status === 'warn') return 'bg-amber-500/10 text-amber-200 border-amber-400/20';
  return 'bg-slate-500/10 text-slate-300 border-white/[0.06]';
}

function qaCheckTags(checks: unknown) {
  return Object.entries(asRecord(checks)).map(([key, value]) => {
    const record = asRecord(value);
    const status = textFrom(record.status) || 'unknown';
    const detail = firstText(record.issues, record.evidence, record.observed_products, record.observed_text, record.observed_brand_signals, record.observed_competitors);
    return {
      key,
      label: QA_CHECK_LABELS[key] || key.replace(/_/g, ' '),
      status,
      detail,
    };
  });
}

function qaIssueItems(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.map((item, index) => {
    const record = asRecord(item);
    const type = textFrom(record.type) || `issue_${index + 1}`;
    const severity = textFrom(record.severity) || 'info';
    const timestamp = textFrom(record.timestamp);
    const evidence = textFrom(record.evidence);
    const correction = textFrom(record.correction);
    const label = [timestamp, type.replace(/_/g, ' ')].filter(Boolean).join(' · ');
    return {
      key: `${label || type}-${index}`,
      label: label || type,
      severity,
      evidence,
      correction,
    };
  }).filter((item) => item.label || item.evidence || item.correction);
}

function qaScoreCorrectionText(value: unknown) {
  const correction = asRecord(value);
  if (!Object.keys(correction).length) return '';
  const apply = qaBoolean(correction.apply);
  const delta = Number(correction.marketing_value_delta);
  const corrected = Number(correction.corrected_marketing_value_score);
  const rationale = textFrom(correction.rationale);
  const parts: string[] = [];
  parts.push(apply ? '建议纠偏' : '不建议调分');
  if (Number.isFinite(delta) && delta !== 0) parts.push(`营销分 ${delta > 0 ? '+' : ''}${delta}`);
  if (Number.isFinite(corrected)) parts.push(`纠偏后 ${Math.round(corrected)}`);
  if (rationale) parts.push(rationale);
  return parts.join(' · ');
}

function buildAnalysisItemLookup(items: VkpiProjectVideoAnalysisCacheItem[]) {
  const map = new Map<string, VkpiProjectVideoAnalysisCacheItem>();
  const add = (key: string, item: VkpiProjectVideoAnalysisCacheItem) => {
    if (!key || map.has(key)) return;
    map.set(key, item);
  };
  items.forEach((item) => {
    if (item.evidence_id != null) add(`evidence:${item.evidence_id}`, item);
    if (item.content_url) add(`url:${item.content_url}`, item);
    if (item.entry?.target_id) add(`target:${item.entry.target_id}`, item);
  });
  return map;
}

function qaItemForAnalysis(item: VkpiProjectVideoAnalysisCacheItem, map: Map<string, VkpiProjectVideoAnalysisCacheItem>) {
  const keys = [
    item.evidence_id != null ? `evidence:${item.evidence_id}` : '',
    item.content_url ? `url:${item.content_url}` : '',
    item.entry?.target_id ? `target:${item.entry.target_id}` : '',
  ].filter(Boolean);
  for (const key of keys) {
    const match = map.get(key);
    if (match?.state === 'ready' && match.entry) return match;
  }
  return null;
}

class RetrospectiveCardErrorBoundary extends Component<{ children: ReactNode; label: string }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, _info: ErrorInfo) {
    console.warn('final_v1 analysis card render failed', this.props.label, error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="rounded-lg border border-rose-400/20 bg-rose-500/[0.04] p-3 text-[10.5px] text-rose-200">
          final_v1 卡片渲染异常：{this.props.label}。该条结果仍保留在缓存中，等待字段 normalizer 补齐。
        </div>
      );
    }
    return this.props.children;
  }
}

function rowAnalysisKeys(row: VkpiProjectRow) {
  return [
    row.assignmentId ? `assignment:${row.assignmentId}` : '',
    row.kolPoolId ? `kol:${row.kolPoolId}` : '',
    row.videoUrl ? `url:${row.videoUrl}` : '',
    row.evidenceUrl ? `url:${row.evidenceUrl}` : '',
    row.latestVideoUrl ? `url:${row.latestVideoUrl}` : '',
    row.latestEvidenceUrl ? `url:${row.latestEvidenceUrl}` : '',
  ].filter(Boolean);
}

function buildAnalysisItemMap(items: VkpiProjectVideoAnalysisCacheItem[]) {
  const map = new Map<string, VkpiProjectVideoAnalysisCacheItem[]>();
  const add = (key: string, item: VkpiProjectVideoAnalysisCacheItem) => {
    const list = map.get(key) || [];
    list.push(item);
    map.set(key, list);
  };
  items.forEach((item) => {
    if (item.assignment_id != null) add(`assignment:${item.assignment_id}`, item);
    if (item.kol_pool_id != null) add(`kol:${item.kol_pool_id}`, item);
    if (item.content_url) add(`url:${item.content_url}`, item);
  });
  return map;
}

function analysisItemsForRow(row: VkpiProjectRow, map: Map<string, VkpiProjectVideoAnalysisCacheItem[]>) {
  const seen = new Set<string>();
  const items: VkpiProjectVideoAnalysisCacheItem[] = [];
  rowAnalysisKeys(row).forEach((key) => {
    (map.get(key) || []).forEach((item) => {
      const id = String(item.evidence_id ?? item.content_url ?? `${key}:${items.length}`);
      if (seen.has(id)) return;
      seen.add(id);
      items.push(item);
    });
  });
  return items;
}

function ProjectVideoAnalysisCard({
  row,
  item,
  qaItem,
}: {
  row: VkpiProjectRow;
  item: VkpiProjectVideoAnalysisCacheItem;
  qaItem?: VkpiProjectVideoAnalysisCacheItem | null;
}) {
  const ready = item.state === 'ready' && item.entry;
  const displayName = row.kolName || item.kol_name || item.handle || 'Unknown';
  const videoUrl = item.content_url || retrospectiveVideoUrl(row);
  const views = item.view_count ?? row.views ?? 0;
  const likes = item.like_count ?? row.likes ?? 0;
  const comments = item.comment_count ?? row.comments ?? 0;
  const payload = ready ? finalV1Payload(item.entry) : {};
  const layer2 = asRecord(payload.layer2_viewer_emotion);
  const layer3 = asRecord(payload.layer3_three_values);
  const layer6 = asRecord(payload.layer6_flags_and_scores);
  const scores = asRecord(layer6.scores);
  const contentScore = normaliseScore(scores.content_quality_score);
  const marketingScore = normaliseScore(scores.marketing_value_score ?? layer6.marketing_value_score);
  const viewerHeart = normaliseScore(layer2.viewer_heart_score ?? layer2.heart_movement_score, scores.viewer_heart_score);
  const channelValue = layerValue(layer3, 'channel_value', scores.channel_value_score);
  const assetValue = layerValue(layer3, 'asset_value', scores.asset_reuse_score);
  const productProof = layerValue(layer3, 'product_proof_value', scores.product_proof_score);
  const viewerReaction = firstText(layer2.one_sentence_viewer_reaction, layer2.one_sentence_viewer_feeling);
  const dislike = firstText(layer2.dislike_or_resistance, layer2.annoyance_or_ad_fatigue);
  const trigger = firstText(layer2.purchase_or_interest_trigger, layer2.desire_to_click_or_buy);
  const keyHook = textFrom(layer6.key_hook);
  const riskFlagTags = normaliseRiskFlags(layer6.risk_flags);
  const verdict = textFrom(layer6.final_verdict) || marketingScore.rationale || keyHook;
  const qaPayload = qaItem?.state === 'ready' && qaItem.entry ? finalV1QaPayload(qaItem.entry) : {};
  const qaHasPayload = Object.keys(qaPayload).length > 0;
  const qaResultRecord = asRecord(qaItem?.entry?.result);
  const qaPass = qaBoolean(qaPayload.qa_pass ?? qaResultRecord.qa_pass);
  const qaBadgeText = qaPass === false ? '需复核' : qaPass === true ? '通过' : '未定';
  const qaSummary = textFrom(qaPayload.summary);
  const qaConfidence = Number(qaPayload.confidence);
  const qaChecks = qaCheckTags(qaPayload.checks);
  const qaIssues = qaIssueItems(qaPayload.issues);
  const qaCorrection = qaScoreCorrectionText(qaPayload.score_correction);
  const qaAction = textFrom(qaPayload.recommended_review_action);
  const fullLayers = [
    ['layer1 画面', payload.layer1_visual_content],
    ['layer2 心动', payload.layer2_viewer_emotion],
    ['layer3 价值', payload.layer3_three_values],
    ['layer4 归因', payload.layer4_attribution],
    ['layer5 建议', payload.layer5_recommendations],
    ['layer6 评分', payload.layer6_flags_and_scores],
  ];

  if (!ready) {
    return (
      <div className="rounded-lg border border-white/[0.05] bg-white/[0.012] p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold text-slate-300 truncate">{displayName}</div>
            <div className="text-[9.5px] text-slate-500 truncate">{item.platform || row.platform} · evidence #{item.evidence_id || '-'}</div>
          </div>
          <span className="px-2 py-1 rounded bg-white/[0.05] text-slate-400 text-[10px] shrink-0">分析队列中</span>
        </div>
        <div className="mt-2 text-[10.5px] text-slate-500">Worker 正在后台跑 final_v1，结果写入缓存后这里会亮起。</div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-cyan-400/20 bg-cyan-400/[0.035] p-3 space-y-3">
      <div className="flex items-center gap-3">
        {row.kolAvatar ? (
          <img src={row.kolAvatar} alt={displayName} className="w-8 h-8 rounded-full object-cover shrink-0" />
        ) : (
          <div className="w-8 h-8 rounded-full bg-cyan-500/15 text-cyan-200 flex items-center justify-center text-[11px] font-bold shrink-0">{retrospectiveRowInitial(row)}</div>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold text-white truncate">{displayName}</span>
            <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 text-[9.5px]">已分析</span>
          </div>
          <div className="text-[9.5px] text-slate-500 truncate">{item.platform || row.platform} · 播放 {formatLargeNum(views)} · 赞 {formatLargeNum(likes)} · 评论 {formatLargeNum(comments)}</div>
        </div>
        {videoUrl ? <a href={videoUrl} target="_blank" rel="noreferrer" className="text-[10px] text-cyan-300 shrink-0">看视频</a> : null}
      </div>

      <div className="grid grid-cols-2 gap-2">
        {[
          ['内容质量', contentScore],
          ['投放价值', marketingScore],
        ].map(([label, score]) => {
          const itemScore = score as ReturnType<typeof normaliseScore>;
          return (
            <div key={label as string} className="rounded-md bg-black/30 border border-white/[0.05] px-3 py-2">
              <div className="text-[9.5px] text-slate-500 mb-1">{label as string}</div>
              <div className="text-[28px] font-bold leading-none tabular-nums" style={{ color: analysisScoreColor(itemScore.score) }}>{itemScore.score ?? '—'}</div>
            </div>
          );
        })}
      </div>
      <div className="text-[10.5px] text-slate-300 leading-relaxed">{compactText(verdict, 190)}</div>

      <div className="grid md:grid-cols-3 gap-2">
        {[
          ['渠道价值', channelValue],
          ['素材复用', assetValue],
          ['产品证明', productProof],
        ].map(([label, value]) => {
          const block = value as ReturnType<typeof layerValue>;
          return (
            <div key={label as string} className="rounded-md bg-white/[0.025] border border-white/[0.05] p-2">
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="text-[9.5px] text-slate-500">{label as string}</span>
                <span className="text-[15px] font-bold tabular-nums" style={{ color: analysisScoreColor(block.score) }}>{block.score ?? '—'}</span>
              </div>
              <div className="text-[10px] text-slate-300 leading-relaxed">{compactText(block.text, 92)}</div>
            </div>
          );
        })}
      </div>

      <div className="rounded-md bg-purple-500/[0.06] border border-purple-400/15 p-2.5">
        <div className="text-[9.5px] text-purple-300 mb-1">观众心动</div>
        <div className="italic text-[11px] text-slate-100 leading-relaxed">“{viewerReaction || '暂无一句话观众反应'}”</div>
        <div className="mt-2 flex flex-wrap gap-1.5 text-[9.5px]">
          <span className="px-1.5 py-0.5 rounded bg-white/[0.05] text-slate-300">心动 {viewerHeart.score ?? '—'}</span>
          {dislike ? <span className="px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-200">反感: {compactText(dislike, 42)}</span> : null}
          {trigger ? <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-200">种草: {compactText(trigger, 42)}</span> : null}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 text-[10px]">
        {keyHook ? <span className="px-2 py-1 rounded bg-cyan-500/10 text-cyan-200">Hook: {compactText(keyHook, 90)}</span> : null}
        {riskFlagTags.map((flag, index) => (
          <span key={`${flag.label}-${index}`} className={`px-2 py-1 rounded ${flag.severity === 'high' ? 'bg-rose-500/15 text-rose-200' : 'bg-amber-500/10 text-amber-200'}`}>
            风险: {compactText(flag.label, 90)}
          </span>
        ))}
      </div>

      {qaHasPayload ? (
        <div className={`rounded-md border p-2.5 ${qaPass === false ? 'border-rose-400/20 bg-rose-500/[0.045]' : 'border-emerald-400/15 bg-emerald-500/[0.035]'}`}>
          <div className="flex items-center justify-between gap-2 mb-2">
            <div className="flex items-center gap-2 min-w-0">
              <span className={`px-2 py-1 rounded text-[9.5px] font-medium ${qaPass === false ? 'bg-rose-500/15 text-rose-200' : 'bg-emerald-500/15 text-emerald-200'}`}>
                关键帧 QA {qaBadgeText}
              </span>
              {Number.isFinite(qaConfidence) ? <span className="text-[9.5px] text-slate-500">置信 {Math.round(qaConfidence * 100)}%</span> : null}
            </div>
            {qaAction ? <span className="text-[9.5px] text-slate-500 shrink-0">{qaAction.replace(/_/g, ' ')}</span> : null}
          </div>
          {qaSummary ? <div className="text-[10.5px] text-slate-200 leading-relaxed mb-2">{compactText(qaSummary, 180)}</div> : null}
          {qaChecks.length ? (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {qaChecks.map((check) => (
                <span key={check.key} className={`px-2 py-1 rounded border text-[9.5px] ${qaStatusClass(check.status)}`} title={check.detail || undefined}>
                  {check.label}: {qaStatusLabel(check.status)}
                </span>
              ))}
            </div>
          ) : null}
          {qaIssues.length ? (
            <div className="space-y-1.5 mb-2">
              {qaIssues.slice(0, 3).map((issue) => (
                <div key={issue.key} className="rounded bg-black/20 border border-white/[0.05] px-2 py-1.5 text-[10px] text-slate-300">
                  <span className="text-amber-200">{issue.label}</span>
                  {issue.evidence ? <span> · {compactText(issue.evidence, 110)}</span> : null}
                  {issue.correction ? <span className="text-cyan-200"> · {compactText(issue.correction, 90)}</span> : null}
                </div>
              ))}
            </div>
          ) : null}
          {qaCorrection ? <div className="text-[10px] text-slate-400">纠偏建议: {compactText(qaCorrection, 190)}</div> : null}
        </div>
      ) : null}

      <details className="rounded-md border border-white/[0.05] bg-black/20">
        <summary className="cursor-pointer px-3 py-2 text-[10.5px] text-cyan-200">展开完整6层</summary>
        <div className="p-3 grid gap-2">
          {fullLayers.map(([label, layer]) => (
            <div key={label as string}>
              <div className="text-[9.5px] text-slate-500 mb-1">{label as string}</div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-[10px] leading-relaxed text-slate-300">{JSON.stringify(layer || {}, null, 2)}</pre>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

export function CampaignRetrospectiveTab({
  project,
  rows,
  stats,
  health,
  videoAnalysisCache,
  videoQaCache,
  videoAnalysisLoading,
  videoAnalysisError,
  videoQaError,
  retrospective,
  retrospectiveLastJob,
  retrospectiveGenerating,
  onGenerateRetrospective,
  onCopy,
  onPendingAction,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  health: ReturnType<typeof healthForRows>;
  videoAnalysisCache?: VkpiProjectVideoAnalysisCacheResponse | null;
  videoQaCache?: VkpiProjectVideoAnalysisCacheResponse | null;
  videoAnalysisLoading?: boolean;
  videoAnalysisError?: string;
  videoQaError?: string;
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
            <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2">
              {[
                ['成品视频', analysisSummary?.evidence_count ?? 0, '#06b6d4'],
                ['已分析', analysisSummary?.ready_count ?? 0, '#10b981'],
                ['队列中', analysisSummary?.pending_count ?? 0, '#facc15'],
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
