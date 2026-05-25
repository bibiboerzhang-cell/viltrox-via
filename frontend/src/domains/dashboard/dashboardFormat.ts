import type { VkpiContactLink } from '../../components/vkpi/vkpiTypes';

export interface DashboardFiltersLike {
  range?: 'today' | '7d' | '30d' | 'mtd' | 'qtd' | 'custom';
  startDate?: string;
  endDate?: string;
}

export function numberValue(value: unknown): number {
  const next = Number(value || 0);
  return Number.isFinite(next) ? next : 0;
}

export function parseJsonValue(value: unknown): unknown {
  if (Array.isArray(value) || (value && typeof value === 'object')) return value;
  const text = String(value || '').trim();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

export function objectValue(value: unknown, fallback: Record<string, unknown> = {}): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : fallback;
}

export function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function centsToUsd(value: unknown): number {
  return numberValue(value) / 100;
}

export function money(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
}

export function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function compact(value: number): string {
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

export function parseContactLinks(value: unknown): VkpiContactLink[] {
  const source = Array.isArray(value)
    ? value
    : (() => {
      const text = String(value || '').trim();
      if (!text) return [];
      try {
        const parsed = JSON.parse(text);
        return Array.isArray(parsed) ? parsed : [];
      } catch {
        return [];
      }
    })();
  const links: VkpiContactLink[] = [];
  source.forEach((item) => {
    if (typeof item === 'string') {
      const text = item.trim();
      if (text) links.push({ label: text.includes('@') && !text.startsWith('http') ? 'Email' : '链接', value: text, url: text.startsWith('http') || text.startsWith('mailto:') ? text : undefined });
      return;
    }
    if (!item || typeof item !== 'object') return;
    const row = item as Record<string, unknown>;
    const url = String(row.url || row.href || '').trim();
    const valueText = String(row.value || row.label || url || '').trim();
    if (!valueText && !url) return;
    links.push({
      label: String(row.label || (url.includes('mailto:') ? 'Email' : '链接')).trim() || '链接',
      value: valueText || url,
      url: url || undefined,
    });
  });
  return links;
}

export function dateValue(value: unknown): Date | null {
  const raw = String(value || '').trim();
  if (!raw) return null;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function durationLabel(startValue: unknown, endValue?: unknown): string {
  const start = dateValue(startValue);
  if (!start) return '-';
  const end = dateValue(endValue) || new Date();
  const diffMs = Math.max(0, end.getTime() - start.getTime());
  const hours = Math.floor(diffMs / 36e5);
  if (hours < 1) return '刚开始';
  if (hours < 24) return `${hours} 小时`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天`;
  return `${Math.floor(days / 30)} 个月 ${days % 30} 天`;
}

export function rangeLabel(filters: DashboardFiltersLike): string {
  if (filters.startDate && filters.endDate) return `${filters.startDate} - ${filters.endDate}`;
  if (filters.range === 'today') return '今天';
  if (filters.range === '30d') return '近 30 天';
  if (filters.range === 'mtd') return '本月至今';
  if (filters.range === 'qtd') return '本季度至今';
  return '近 7 天';
}

export function windowDays(filters: DashboardFiltersLike): number {
  if (filters.startDate && filters.endDate) {
    const start = dateValue(filters.startDate);
    const end = dateValue(filters.endDate);
    if (start && end) {
      return Math.max(1, Math.min(180, Math.ceil((end.getTime() - start.getTime()) / 86400000) + 1));
    }
  }
  const now = new Date();
  if (filters.range === 'today') return 1;
  if (filters.range === '30d') return 30;
  if (filters.range === 'mtd') return now.getDate();
  if (filters.range === 'qtd') {
    const quarterStartMonth = Math.floor(now.getMonth() / 3) * 3;
    const quarterStart = new Date(now.getFullYear(), quarterStartMonth, 1);
    return Math.max(1, Math.min(180, Math.ceil((now.getTime() - quarterStart.getTime()) / 86400000) + 1));
  }
  return 7;
}

export function staffWindow(filters: DashboardFiltersLike): string {
  if (filters.range === 'today') return 'today';
  if (filters.range === '30d') return '30d';
  if (filters.range === 'mtd' || filters.range === 'qtd') return 'month';
  return '7d';
}
