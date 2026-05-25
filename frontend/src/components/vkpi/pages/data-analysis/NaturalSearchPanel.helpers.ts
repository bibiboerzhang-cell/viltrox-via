import type { Row } from './utils/types';

export interface NaturalSearchPanelProps {
  apiToken?: string;
  onMessage: (message: string) => void;
}

export interface NaturalSearchHistoryItem {
  id: string;
  query: string;
  resultCount: number;
  status: string;
  searchedAt: string;
}

export interface SearchProgressState {
  visible: boolean;
  percent: number;
  message: string;
  activeKey: string;
  doneKeys: string[];
  errorKey?: string;
}

export interface RecentContentCard {
  id: string;
  title: string;
  url: string;
  platform: string;
  handle: string;
  sourceTitle: string;
  publishedAt: string;
  views: string;
  likes: string;
  comments: string;
}

export const NATURAL_SEARCH_HISTORY_KEY = 'vkpi:natural-search-history:v1';
export const MAX_SEARCH_HISTORY = 8;
export const SEARCH_REVEAL_BATCH_SIZE = 6;
export const RECENT_CONTENT_CARD_LIMIT = 8;

export const DECISION_OPTIONS = [
  { key: 'contact', label: '可联系', detail: '证据足够，适合进入沟通或项目跟进。', severity: 'medium' },
  { key: 'watch', label: '可观察', detail: '保留候选，等待更多内容或业务场景。', severity: 'low' },
  { key: 'caution', label: '谨慎', detail: '存在数据缺口或竞品风险，联系前需要复核。', severity: 'high' },
  { key: 'avoid', label: '避开', detail: '当前证据不支持推进，避免进入联系名单。', severity: 'high' },
] as const;

export const SEARCH_PROGRESS_STEPS = [
  { key: 'query', label: '解析查询' },
  { key: 'pool', label: '查 KOL / Memory' },
  { key: 'evidence', label: '整理证据' },
  { key: 'render', label: '显示结果' },
] as const;

export const IDLE_PROGRESS: SearchProgressState = {
  visible: false,
  percent: 0,
  message: '',
  activeKey: '',
  doneKeys: [],
};

export function compactText(value: unknown, fallback = '-'): string {
  const text = String(value ?? '').trim();
  return text || fallback;
}

export function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

export function asRecordArray(value: unknown): Row[] {
  return Array.isArray(value) ? value.map(asRecord).filter((row) => Object.keys(row).length > 0) : [];
}

export function numberValue(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

export function firstText(...values: unknown[]): string {
  for (const value of values) {
    const text = String(value ?? '').trim();
    if (text) return text;
  }
  return '';
}

export function formatCount(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return '';
  return new Intl.NumberFormat('en', { notation: numeric >= 10000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(numeric);
}

export function formatPostDate(value: unknown): string {
  const text = firstText(value);
  if (!text) return '';
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text.slice(0, 10);
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function initials(value: unknown): string {
  const text = compactText(value, '?');
  return text.slice(0, 1).toUpperCase();
}

export function loadSearchHistory(): NaturalSearchHistoryItem[] {
  if (typeof window === 'undefined') return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(NATURAL_SEARCH_HISTORY_KEY) || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.map((item) => {
      const row = asRecord(item);
      const query = firstText(row.query);
      if (!query) return null;
      return {
        id: firstText(row.id, query.toLowerCase()),
        query,
        resultCount: Number(row.resultCount) || 0,
        status: firstText(row.status, '只读'),
        searchedAt: firstText(row.searchedAt, new Date().toISOString()),
      };
    }).filter(Boolean).slice(0, MAX_SEARCH_HISTORY) as NaturalSearchHistoryItem[];
  } catch {
    return [];
  }
}

export function saveSearchHistory(items: NaturalSearchHistoryItem[]) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(NATURAL_SEARCH_HISTORY_KEY, JSON.stringify(items.slice(0, MAX_SEARCH_HISTORY)));
  } catch {
    // Search history is convenience-only; private-mode storage failures should not block search.
  }
}

export function historyAge(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  const deltaSeconds = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 1000));
  if (deltaSeconds < 60) return '刚刚';
  const minutes = Math.floor(deltaSeconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return `${days} 天前`;
}

export function recentPostsForItem(item: Row, evidence = asRecord(item.evidence)): Row[] {
  const fromItem = asRecordArray(item.recent_posts);
  if (fromItem.length) return fromItem;
  return asRecordArray(evidence.recent_posts);
}

export function kolPoolIdForItem(item: Row): string {
  const evidence = asRecord(item.evidence);
  if (firstText(item.source_table) !== 'vkpi_kol_pool') return '';
  return firstText(item.source_id, item.kol_pool_id, evidence.id, evidence.kol_pool_id);
}

export function evidenceRows(card: Row | null): Row[] {
  return asRecordArray(card?.evidence_index).filter((row) => firstText(row.section));
}

export function cardSectionPayload(card: Row | null, section: unknown): Row {
  if (!card) return {};
  return asRecord(card[firstText(section)]);
}

export function evidenceSectionLabel(value: unknown): string {
  const section = firstText(value);
  const labels: Record<string, string> = {
    freshness: 'Freshness',
    dimensions11: '11D',
    competitors: 'Competitors',
    brand_signal: 'Brand Signal',
    video_analysis: 'Video',
    memory_card: 'Memory',
    product_fit: 'Product Fit',
    comment_intelligence: 'Comment',
  };
  return labels[section] || section || 'Evidence';
}

export function evidencePayloadItems(payload: Row): Row[] {
  for (const key of ['evidence', 'signals', 'top', 'recent_posts', 'recent_cooperations', 'relations']) {
    const rows = asRecordArray(payload[key]);
    if (rows.length) return rows;
  }
  return [];
}
