import type { VkpiPlatform } from '../vkpiTypes';
import { ENRICHABLE_PLATFORMS } from './KolPoolPanel.types';
import type { KolPoolIntelligenceCard, KolPoolItem, KolPoolRefreshState } from './KolPoolPanel.types';

export function refreshStateLabel(refresh: KolPoolRefreshState): string {
  if (refresh.triggered) return '旧数据已返回，后台刷新已排队。';
  if (refresh.reason === 'on_demand_refresh_disabled') return '数据新鲜度已检查，按需刷新尚未启用。';
  if (refresh.reason === 'fresh') return '数据仍在新鲜度窗口内。';
  if (refresh.reason === 'job_queue_unavailable') return '数据较旧，但后台队列当前不可用。';
  if (refresh.reason === 'not_enqueueable') return refresh.message || '该账号暂不支持按需刷新。';
  if (refresh.reason === 'not_requested') return '已读取现有记录，未请求后台刷新。';
  return refresh.message || `刷新状态: ${refresh.reason || 'unknown'}`;
}

export function getDataGaps(item: KolPoolItem): string[] {
  const gaps: string[] = [];
  if (!item.avatar_url) gaps.push('头像');
  if (!hasNumber(item.avg_views)) gaps.push('平均播放');
  if (!hasNumber(item.engagement_rate)) gaps.push('互动率');
  if (!hasNumber(item.viltrox_fit_score)) gaps.push('适配度');
  return gaps;
}

export function canEnrich(item: KolPoolItem): boolean {
  return ENRICHABLE_PLATFORMS.has(String(item.platform || '').toLowerCase());
}

export function summarizeCoverage(items: KolPoolItem[]) {
  const avatar = items.filter((item) => Boolean(item.avatar_url)).length;
  const avgViews = items.filter((item) => hasNumber(item.avg_views)).length;
  const engagement = items.filter((item) => hasNumber(item.engagement_rate)).length;
  const fit = items.filter((item) => hasNumber(item.viltrox_fit_score)).length;
  const complete = items.filter((item) => getDataGaps(item).length === 0).length;
  return {
    total: items.length,
    avatar,
    avgViews,
    engagement,
    fit,
    complete,
    missing: items.length - complete,
  };
}

export function decisionProfile(item: KolPoolItem): { score: number; label: string; reason: string; nextAction: string; tone: string } {
  const gaps = getDataGaps(item);
  const fit = numberValue(item.viltrox_fit_score);
  const followers = numberValue(item.followers);
  const avgViews = numberValue(item.avg_views);
  const engagement = numberValue(item.engagement_rate);
  const dataScore = Math.max(0, 40 - gaps.length * 10);
  const fitScore = fit === null ? 0 : Math.min(30, fit * 0.3);
  const scaleScore = Math.min(15, Math.log10(Math.max(1, followers || 0) + 1) * 3);
  const actionScore = Math.min(15, (avgViews ? Math.log10(avgViews + 1) * 2 : 0) + (engagement ? engagement * 1.2 : 0));
  const score = Math.round(dataScore + fitScore + scaleScore + actionScore);
  if (gaps.length >= 2) {
    return {
      score,
      label: '先补数据',
      reason: `缺 ${gaps.join(' / ')}，现在不适合直接决策。`,
      nextAction: canEnrich(item) ? '补齐数据' : '人工补字段',
      tone: 'is-warn',
    };
  }
  if (!item.linked_main_kol_id && score >= 70) {
    return {
      score,
      label: '可入主表',
      reason: '核心指标足够，适合进入主表后做项目/沟通跟进。',
      nextAction: '自动入主表',
      tone: 'is-ok',
    };
  }
  if (score >= 60) {
    return {
      score,
      label: '人工复核',
      reason: '指标基本可判断，建议打开主页检查内容风格和粉丝真实性。',
      nextAction: '打开主页',
      tone: 'is-neutral',
    };
  }
  return {
    score,
    label: '低优先级',
    reason: '当前指标不足以支持优先推进，可保留观察或等待负责人指定。',
    nextAction: '保留观察',
    tone: 'is-muted',
  };
}

export function candidatePriority(item: KolPoolItem): { label: string; reason: string; tone: string } {
  const gaps = getDataGaps(item);
  if (gaps.length) {
    return {
      label: `先补齐 ${gaps.join(' / ')}`,
      reason: '当前数据还不足以判断合作优先级；建议先跑真实补齐，再决定是否链接到主表。',
      tone: 'is-warn',
    };
  }
  const score = numberValue(item.viltrox_fit_score);
  const engagement = numberValue(item.engagement_rate);
  if ((score !== null && score >= 70) || (engagement !== null && engagement >= 3)) {
    return {
      label: '可进入人工复核',
      reason: '核心指标已经齐全，适合打开平台主页复核内容风格、粉丝真实性和产品匹配。',
      tone: 'is-ok',
    };
  }
  return {
    label: '数据完整但优先级一般',
    reason: '可保留在候选池，除非负责人或产品线强匹配，否则不建议立即推进项目。',
    tone: 'is-neutral',
  };
}

export function metricReadiness(item: KolPoolItem): Array<{ label: string; value: string; reason: string; ready: boolean }> {
  const followers = numberValue(item.followers);
  const avgViews = numberValue(item.avg_views);
  const engagement = numberValue(item.engagement_rate);
  const fit = numberValue(item.viltrox_fit_score);
  return [
    {
      label: '规模',
      value: formatNumber(item.followers),
      reason: followers === null ? '缺粉丝数据' : followers >= 10_000 ? '规模可参考' : '规模较小，适合作长尾观察',
      ready: followers !== null,
    },
    {
      label: '内容表现',
      value: formatNumber(item.avg_views),
      reason: avgViews === null ? '缺平均播放' : avgViews >= 5_000 ? '有内容验证' : '播放规模偏低',
      ready: avgViews !== null,
    },
    {
      label: '互动',
      value: formatPercent(item.engagement_rate),
      reason: engagement === null ? '缺互动率' : engagement >= 2 ? '互动可用' : '互动偏弱',
      ready: engagement !== null,
    },
    {
      label: '产品适配',
      value: formatScoreValue(item.viltrox_fit_score),
      reason: fit === null ? '缺适配评分' : fit >= 70 ? '适配度较高' : '适配度一般，需要人工看内容',
      ready: fit !== null,
    },
  ];
}

export function mergeItems(current: KolPoolItem[], updates: KolPoolItem[]): KolPoolItem[] {
  const byId = new Map(updates.map((item) => [item.id, item]));
  return current.map((item) => {
    const update = byId.get(item.id);
    return update ? { ...item, ...update } : item;
  });
}

export function parseMaybeJson(value: unknown): Record<string, unknown> {
  if (!value) return {};
  if (typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== 'string') return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function parseMaybeList(value: unknown): string[] {
  if (!value) return [];
  if (Array.isArray(value)) return value.map((item) => stringifyValue(item)).filter(Boolean);
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed.map((item) => stringifyValue(item)).filter(Boolean);
    } catch {
      return value.split(/[;,，、\n]/).map((item) => item.trim()).filter(Boolean);
    }
  }
  return [stringifyValue(value)].filter(Boolean);
}

export function collectList(jsonValue: unknown, raw: Record<string, unknown>, keys: string[]): string[] {
  const values = parseMaybeList(jsonValue);
  for (const key of keys) values.push(...parseMaybeList(raw[key]));
  return Array.from(new Set(values.map((item) => item.trim()).filter(Boolean))).slice(0, 8);
}

export function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function getString(raw: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = raw[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

export function stringifyValue(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const preferred = record.name || record.label || record.value || record.product || record.owner || record.note;
    if (preferred) return stringifyValue(preferred);
    return JSON.stringify(value);
  }
  return String(value);
}

export function numberValue(value: unknown): number | null {
  if (value === undefined || value === null || value === '') return null;
  const next = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(next) ? next : null;
}

export function hasNumber(value: unknown): boolean {
  return numberValue(value) !== null;
}

export function formatNumber(value: unknown): string {
  const next = numberValue(value);
  if (next === null) return '—';
  if (next >= 1_000_000) return `${(next / 1_000_000).toFixed(1)}M`;
  if (next >= 1_000) return `${(next / 1_000).toFixed(1)}K`;
  return String(Math.round(next));
}

export function formatPercent(value: unknown): string {
  const next = numberValue(value);
  if (next === null) return '—';
  return `${next.toFixed(2)}%`;
}

export function formatScore(value: unknown) {
  const next = numberValue(value);
  return next === null ? '—' : <span className="vkpi-chip">{next.toFixed(1)}</span>;
}

export function formatScoreValue(value: unknown): string {
  const next = numberValue(value);
  return next === null ? '—' : `${next.toFixed(1)}/100`;
}

export function statusLabel(value: unknown): string {
  const status = stringifyValue(value || 'unknown').toLowerCase();
  if (status === 'ready') return 'ready';
  if (status === 'empty') return '暂无证据';
  if (status === 'skipped') return '未启用';
  if (status === 'unavailable') return '不可用';
  return status || 'unknown';
}

export function readinessLabel(value: unknown): string {
  const status = stringifyValue(value || 'partial').toLowerCase();
  if (status === 'ready') return '证据就绪';
  if (status === 'partial') return '部分证据';
  return status || 'unknown';
}

export function memoryCardLabel(memoryCard: Record<string, unknown>): string {
  const sourceType = stringifyValue(memoryCard.source_type);
  const sourceRef = stringifyValue(memoryCard.source_ref);
  if (sourceType && sourceRef) return `${sourceType} · ${sourceRef}`;
  if (sourceType) return sourceType;
  return statusLabel(memoryCard.status);
}

export function formatConfidenceValue(value: unknown): string {
  const next = confidenceValue(value);
  if (next === null) return '—';
  return `${Math.round(next * 100)}%`;
}

export function arrayRecords(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.map(recordValue).filter((row) => Object.keys(row).length > 0);
}

export function intelligenceSectionPayload(card: KolPoolIntelligenceCard, section: string): Record<string, unknown> {
  const key = section.toLowerCase();
  if (key === 'freshness') return recordValue(card.freshness);
  if (key === 'dimensions11') return recordValue(card.dimensions11);
  if (key === 'competitors') return recordValue(card.competitors);
  if (key === 'brand_signal') return recordValue(card.brand_signal);
  if (key === 'comment_intelligence') return recordValue(card.comment_intelligence);
  if (key === 'video_analysis') return recordValue(card.video_analysis);
  if (key === 'memory_card') return recordValue(card.memory_card);
  if (key === 'product_fit') return recordValue(card.product_fit);
  return {};
}

export function evidenceSectionLabel(section: string): string {
  const labels: Record<string, string> = {
    freshness: 'Freshness',
    dimensions11: '11D Confidence',
    competitors: 'Competitors',
    brand_signal: 'Brand Signal',
    comment_intelligence: 'Comment Intelligence',
    video_analysis: 'Video Analysis',
    memory_card: 'Memory Card',
    product_fit: 'Product Fit',
  };
  return labels[section] || section || 'Evidence';
}

export function statusClass(value: unknown): string {
  const status = stringifyValue(value || '').toLowerCase();
  return status === 'ready' || status === 'empty' ? 'is-ok' : 'is-missing';
}

export function confidenceValue(value: unknown): number | null {
  const next = numberValue(value);
  if (next === null) return null;
  return Math.max(0, Math.min(1, next));
}

export function confidenceLabel(value: unknown): string {
  const next = confidenceValue(value);
  return next === null ? '无证据' : `${Math.round(next * 100)}% confidence`;
}

export function blockMetricSummary(block: Record<string, unknown>, keys: string[]): string {
  const parts = keys.map((key) => {
    const value = numberValue(block[key]);
    if (value === null) return `${dimensionMetricLabel(key)}: 无证据`;
    return `${dimensionMetricLabel(key)}: ${Math.round(value)}`;
  });
  return parts.join(' / ');
}

export function confidenceMetricSummary(block: Record<string, unknown>, keys: string[]): string {
  return keys.map((key) => `${dimensionMetricLabel(key)}: ${confidenceLabel(block[key])}`).join(' / ');
}

export function dimensionMetricLabel(key: string): string {
  const labels: Record<string, string> = {
    posting_frequency_score: '频率',
    content_diversity_score: '多样',
    followers_tier_score: '粉丝',
    engagement_quality_score: '互动',
    growth_velocity_score: '增长',
    cooperation_history_score: '合作',
    contact_reachability_score: '触达',
    competitor_risk_score: '竞品风险',
    industry_cluster: '垂类',
    product_fit: '产品',
  };
  return labels[key] || key;
}

export function dimensionsConfidenceRows(dimensions: Record<string, unknown>): Array<{ key: string; label: string; confidenceLabel: string; detail: string; ready: boolean }> {
  const confidence = recordValue(dimensions.confidence);
  const blocks = recordValue(dimensions.blocks);
  const block1 = recordValue(blocks.block1_content);
  const block2 = recordValue(blocks.block2_performance);
  const block3 = recordValue(blocks.block3_business);
  const block4 = recordValue(blocks.block4_specialty);
  const rows = [
    {
      key: 'block1_content',
      label: '内容证据',
      confidence: confidence.block1_content,
      detail: blockMetricSummary(block1, ['posting_frequency_score', 'content_diversity_score']),
    },
    {
      key: 'block2_performance',
      label: '表现证据',
      confidence: confidence.block2_performance,
      detail: blockMetricSummary(block2, ['followers_tier_score', 'engagement_quality_score', 'growth_velocity_score']),
    },
    {
      key: 'block3_business',
      label: '商务证据',
      confidence: confidence.block3_business,
      detail: blockMetricSummary(block3, ['cooperation_history_score', 'contact_reachability_score', 'competitor_risk_score']),
    },
    {
      key: 'block4_specialty',
      label: '专长/Product',
      confidence: confidence.block4_specialty,
      detail: confidenceMetricSummary(recordValue(block4.confidence), ['industry_cluster', 'product_fit']),
    },
  ];
  return rows.map((row) => {
    const conf = confidenceValue(row.confidence);
    return {
      key: row.key,
      label: row.label,
      confidenceLabel: confidenceLabel(row.confidence),
      detail: row.detail,
      ready: conf !== null && conf >= 0.5,
    };
  });
}

export function toVkpiPlatform(platform: string): VkpiPlatform {
  const normalized = String(platform || '').toLowerCase();
  const map: Record<string, VkpiPlatform> = {
    instagram: 'Instagram',
    tiktok: 'TikTok',
    youtube: 'YouTube',
    xiaohongshu: 'XHS',
    xhs: 'XHS',
    x: 'X',
    twitter: 'X',
    bilibili: 'Bilibili',
    facebook: 'Facebook',
    reddit: 'Reddit',
    threads: 'Threads',
    pinterest: 'Pinterest',
    website: 'Website',
  };
  return map[normalized] || 'Other';
}
