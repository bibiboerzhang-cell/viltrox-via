import { formatLargeNum, formatMoneyShort } from '../projectDeliverableStyle';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import type { VkpiAnalysisCacheEntry, VkpiProjectVideoAnalysisCacheItem } from '../../../../../services/vkpi/projects-api';
import type { ProjectStatsSummary } from '../../../../../domains/projects';
import { retrospectiveNumberField, retrospectiveTextField } from '../ProjectDetailTabs.shared';

export function buildRetrospectiveDraftText(
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

export function retrospectiveVideoUrl(row: VkpiProjectRow) {
  return retrospectiveTextField(row, ['contentUrl', 'content_url', 'videoUrl', 'video_url', 'postUrl', 'post_url', 'evidenceUrl', 'evidence_url']);
}

export function retrospectiveWatchTime(row: VkpiProjectRow) {
  const direct = retrospectiveTextField(row, ['watchTime', 'watch_time', 'duration', 'durationLabel', 'duration_label']);
  if (direct) return direct;
  const seconds = retrospectiveNumberField(row, ['durationSeconds', 'duration_seconds']);
  if (!seconds) return '—';
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60).toString().padStart(2, '0');
  return `${minutes}:${rest}`;
}

export function retrospectiveRowInitial(row: VkpiProjectRow) {
  return (row.kolName || row.kolHandle || '?').trim().charAt(0).toUpperCase() || '?';
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function textFrom(value: unknown): string {
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

export function normaliseScore(value: unknown, fallback?: unknown) {
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

export function analysisScoreColor(score: number | null) {
  if (score == null) return '#94a3b8';
  if (score >= 80) return '#34d399';
  if (score >= 60) return '#facc15';
  return '#fb7185';
}

export function finalV1Payload(entry?: VkpiAnalysisCacheEntry | null) {
  const result = asRecord(entry?.result);
  return asRecord(result.video_analysis_final_v1).layer1_visual_content ? asRecord(result.video_analysis_final_v1) : result;
}

export function layerValue(layer: Record<string, unknown>, key: string, scoreFallback?: unknown) {
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

export function compactText(value: string, max = 150) {
  if (!value) return '暂无明确结论';
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

export function firstText(...values: unknown[]) {
  for (const value of values) {
    const text = textFrom(value);
    if (text) return text;
  }
  return '';
}

export function normaliseRiskFlags(value: unknown) {
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

export const QA_CHECK_LABELS: Record<string, string> = {
  product_identity: '型号',
  brand_exposure: '品牌',
  competitor_context: '竞品',
  inscription_or_model_text: '铭文',
  title_image_consistency: '标题画面',
};

export function finalV1QaPayload(entry?: VkpiAnalysisCacheEntry | null) {
  const result = asRecord(entry?.result);
  const direct = asRecord(result.final_v1_keyframe_qa);
  if (Object.keys(direct).length) return direct;
  const nested = asRecord(asRecord(result.video_analysis_final_v1_keyframe_qa).final_v1_keyframe_qa);
  if (Object.keys(nested).length) return nested;
  if ('qa_pass' in result || 'checks' in result || 'issues' in result || 'score_correction' in result) return result;
  return {};
}

export function qaBoolean(value: unknown) {
  if (typeof value === 'boolean') return value;
  const text = String(value ?? '').trim().toLowerCase();
  if (['true', '1', 'yes', 'pass', 'passed'].includes(text)) return true;
  if (['false', '0', 'no', 'fail', 'failed'].includes(text)) return false;
  return null;
}

export function qaStatusLabel(value: unknown) {
  const status = textFrom(value).toLowerCase();
  if (status === 'pass') return '通过';
  if (status === 'warn') return '提醒';
  if (status === 'fail') return '异常';
  if (status === 'unknown') return '未知';
  return status || '未知';
}

export function qaStatusClass(value: unknown) {
  const status = textFrom(value).toLowerCase();
  if (status === 'pass') return 'bg-emerald-500/10 text-emerald-200 border-emerald-400/15';
  if (status === 'fail') return 'bg-rose-500/12 text-rose-200 border-rose-400/20';
  if (status === 'warn') return 'bg-amber-500/10 text-amber-200 border-amber-400/20';
  return 'bg-slate-500/10 text-slate-300 border-white/[0.06]';
}

export function qaCheckTags(checks: unknown) {
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

export function qaIssueItems(value: unknown) {
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

export function qaScoreCorrectionText(value: unknown) {
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

export function buildAnalysisItemLookup(items: VkpiProjectVideoAnalysisCacheItem[]) {
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

export function qaItemForAnalysis(item: VkpiProjectVideoAnalysisCacheItem, map: Map<string, VkpiProjectVideoAnalysisCacheItem>) {
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

export function rowAnalysisKeys(row: VkpiProjectRow) {
  return [
    row.assignmentId ? `assignment:${row.assignmentId}` : '',
    row.kolPoolId ? `kol:${row.kolPoolId}` : '',
    row.videoUrl ? `url:${row.videoUrl}` : '',
    row.evidenceUrl ? `url:${row.evidenceUrl}` : '',
    row.latestVideoUrl ? `url:${row.latestVideoUrl}` : '',
    row.latestEvidenceUrl ? `url:${row.latestEvidenceUrl}` : '',
  ].filter(Boolean);
}

export function buildAnalysisItemMap(items: VkpiProjectVideoAnalysisCacheItem[]) {
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

export function analysisItemsForRow(row: VkpiProjectRow, map: Map<string, VkpiProjectVideoAnalysisCacheItem[]>) {
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
