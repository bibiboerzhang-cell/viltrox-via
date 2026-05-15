import { platformDisplay as sharedPlatformDisplay } from '../../../shared/vkpiDataUtils';

/**
 * 平台名称归一化
 * youtube/yt → youtube
 * instagram/ig → instagram
 * 等等
 */
export function normalizePlatform(value: unknown): string {
  const raw = String(value || 'other').trim().toLowerCase();
  if (raw.includes('youtube') || raw === 'yt') return 'youtube';
  if (raw.includes('instagram') || raw === 'ig') return 'instagram';
  if (raw.includes('tiktok') || raw === 'tt') return 'tiktok';
  if (raw.includes('facebook') || raw === 'fb') return 'facebook';
  if (raw.includes('linkedin')) return 'linkedin';
  if (raw.includes('xiaohongshu') || raw.includes('xhs') || raw.includes('小红书')) return 'xhs';
  if (raw.includes('bilibili')) return 'bilibili';
  if (raw === 'x' || raw.includes('twitter')) return 'x';
  if (raw.includes('threads')) return 'threads';
  if (raw.includes('reddit')) return 'reddit';
  return raw || 'other';
}

/** 平台显示名称 - 复用系统已有 sharedPlatformDisplay */
export function platformDisplay(value: unknown): string {
  const raw = String(value || '').trim();
  if (!raw) return 'Other';
  return sharedPlatformDisplay(raw);
}

/** 平台图标首字母(用于头像 fallback) */
export function platformInitial(value: unknown): string {
  const normalized = normalizePlatform(value);
  const marks: Record<string, string> = {
    instagram: '◎',
    youtube: '▶',
    tiktok: '♪',
    facebook: 'f',
    x: '𝕏',
    xhs: '红',
    bilibili: 'b',
    reddit: 'r',
    threads: '@',
  };
  return marks[normalized] || platformDisplay(value).slice(0, 1).toUpperCase();
}

/** 平台 CSS 类名 */
export function platformClass(value: unknown): string {
  return `da-platform-${normalizePlatform(value)}`;
}

/** 紧凑数字格式化 (1.2K / 3.4M) */
export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

/** 友好日期 */
export function prettyDate(value: unknown): string {
  const raw = String(value || '').trim();
  if (!raw) return '待同步';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date);
}

/** 短日期 */
export function shortDate(value: unknown): string {
  const raw = String(value || '').trim();
  if (!raw) return '-';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' }).format(date);
}
