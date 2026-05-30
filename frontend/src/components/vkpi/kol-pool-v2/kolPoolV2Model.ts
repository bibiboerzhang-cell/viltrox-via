import type {
  VkpiKolPoolFreshness,
  VkpiKolPoolItem,
  VkpiKolPoolRefreshState,
} from '../../../domains/kol';

export type KolPoolV2Item = VkpiKolPoolItem & {
  audience_type?: string | null;
  country?: string | null;
  device_primary?: string | null;
  device_upgrade_window?: string | null;
  fit_score_delta_7d?: number | null;
  raw_engagement_rate?: number | null;
  real_engagement_rate?: number | null;
  real_followers_pct?: number | null;
  trend_score?: number | null;
  trend_topic?: string | null;
  freshness?: VkpiKolPoolFreshness;
  refresh?: VkpiKolPoolRefreshState;
};

export type MainClass = 'qualified' | 'legacy';
export type FreshnessSubstate = 'fresh' | 'stale' | 'warming';
export type CandidateClass =
  | 'qualified-fresh'
  | 'qualified-stale'
  | 'qualified-warming'
  | 'legacy-fresh'
  | 'legacy-stale'
  | 'legacy-warming';

export interface CandidateClassInfo {
  label: string;
  short: string;
  tone: 'green' | 'amber' | 'blue' | 'slate' | 'orange' | 'violet';
}

export const CANDIDATE_CLASS_INFO: Record<CandidateClass, CandidateClassInfo> = {
  'qualified-fresh': { label: '已签约 · 数据新', short: 'Qualified', tone: 'green' },
  'qualified-stale': { label: '已签约 · 数据旧', short: 'Q · Stale', tone: 'amber' },
  'qualified-warming': { label: '已签约 · 刷新中', short: 'Q · Warming', tone: 'blue' },
  'legacy-fresh': { label: '候选池 · 数据新', short: 'Legacy', tone: 'slate' },
  'legacy-stale': { label: '候选池 · 数据旧', short: 'L · Stale', tone: 'orange' },
  'legacy-warming': { label: '候选池 · 刷新中', short: 'L · Warming', tone: 'violet' },
};

export function deriveMainClass(item: KolPoolV2Item): MainClass {
  return item.linked_main_kol_id ? 'qualified' : 'legacy';
}

export function deriveFreshness(item: KolPoolV2Item): FreshnessSubstate {
  if (item.refresh?.triggered || String(item.sync_status || '').toLowerCase() === 'syncing') return 'warming';
  if (item.freshness?.needs_refresh) return 'stale';
  return 'fresh';
}

export function deriveCandidateClass(item: KolPoolV2Item): CandidateClass {
  return `${deriveMainClass(item)}-${deriveFreshness(item)}` as CandidateClass;
}

export function numberValue(value: unknown): number | null {
  if (value === undefined || value === null || value === '') return null;
  const next = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(next) ? next : null;
}

export function textValue(value: unknown, fallback = '无信号'): string {
  const clean = String(value ?? '').replace(/\s+/g, ' ').trim();
  return clean || fallback;
}

export function formatCount(value: unknown): string {
  const next = numberValue(value);
  if (next === null) return '--';
  if (next >= 1_000_000) return `${(next / 1_000_000).toFixed(1)}M`;
  if (next >= 10_000) return `${(next / 1_000).toFixed(0)}K`;
  if (next >= 1_000) return `${(next / 1_000).toFixed(1)}K`;
  return Math.round(next).toLocaleString('en-US');
}

export function formatPercent(value: unknown): string {
  const next = numberValue(value);
  return next === null ? '--' : `${next.toFixed(2)}%`;
}

export function formatScore(value: unknown): string {
  const next = numberValue(value);
  return next === null ? '--' : `${next.toFixed(1)}`;
}

export function kolDisplayName(item: KolPoolV2Item): string {
  return textValue(item.display_name || item.handle || item.pool_uid || item.id, '未命名 KOL');
}

export function dataGaps(item: KolPoolV2Item): string[] {
  const gaps: string[] = [];
  if (!item.avatar_url) gaps.push('头像');
  if (numberValue(item.avg_views) === null) gaps.push('平均播放');
  if (numberValue(item.engagement_rate ?? item.real_engagement_rate) === null) gaps.push('互动率');
  if (numberValue(item.viltrox_fit_score) === null) gaps.push('适配度');
  return gaps;
}

export function summarizeKolPool(items: KolPoolV2Item[]) {
  const qualified = items.filter((item) => deriveMainClass(item) === 'qualified').length;
  const complete = items.filter((item) => !dataGaps(item).length).length;
  const fitRows = items.map((item) => numberValue(item.viltrox_fit_score)).filter((value): value is number => value !== null);
  const avgFit = fitRows.length ? fitRows.reduce((sum, value) => sum + value, 0) / fitRows.length : null;
  return {
    total: items.length,
    qualified,
    legacy: items.length - qualified,
    complete,
    missing: items.length - complete,
    avgFit,
  };
}
