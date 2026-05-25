import type { IntelligenceTaskBridgeTarget, IntelligenceTaskDraft } from './intelligenceTaskDraft';

type Row = Record<string, unknown>;

export interface DiscoverFocusPayload {
  source: string;
  title: string;
  summary: string;
  query: string;
  platform?: string;
  sourceLabel?: string;
  createdAt: string;
}

export const DISCOVER_FOCUS_KEY = 'vkpi:discover:focus';

function text(value: unknown, fallback = ''): string {
  const next = String(value ?? '').trim();
  return next || fallback;
}

function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

export function normalizeDiscoverPlatform(value: unknown): string {
  const key = text(value, 'all').toLowerCase();
  if (!key || key === '-' || key === 'platform') return 'all';
  if (key.includes('youtube')) return 'youtube';
  if (key.includes('instagram') || key === 'ig') return 'instagram';
  if (key.includes('tiktok') || key.includes('douyin')) return 'tiktok';
  if (key.includes('facebook') || key === 'fb') return 'facebook';
  if (key.includes('reddit')) return 'reddit';
  if (key === 'x' || key.includes('twitter')) return 'x';
  if (key.includes('bilibili')) return 'bilibili';
  if (key.includes('xhs') || key.includes('xiaohongshu') || key.includes('rednote')) return 'xhs';
  return key;
}

export function writeDiscoverFocus(payload: Omit<DiscoverFocusPayload, 'createdAt'> & { createdAt?: string }) {
  if (typeof window === 'undefined') return;
  const query = text(payload.query);
  if (!query) return;
  window.sessionStorage.setItem(DISCOVER_FOCUS_KEY, JSON.stringify({
    ...payload,
    query,
    platform: normalizeDiscoverPlatform(payload.platform),
    createdAt: payload.createdAt || new Date().toISOString(),
  }));
}

export function readDiscoverFocus(): DiscoverFocusPayload | null {
  if (typeof window === 'undefined') return null;
  const raw = window.sessionStorage.getItem(DISCOVER_FOCUS_KEY);
  if (!raw) return null;
  try {
    const parsed = asRecord(JSON.parse(raw));
    const query = text(parsed.query);
    if (!query) return null;
    return {
      source: text(parsed.source, 'intelligence'),
      title: text(parsed.title, query),
      summary: text(parsed.summary, '从智能入口带入的发现任务。'),
      query,
      platform: normalizeDiscoverPlatform(parsed.platform),
      sourceLabel: text(parsed.sourceLabel, ''),
      createdAt: text(parsed.createdAt, new Date().toISOString()),
    };
  } catch {
    return null;
  }
}

export function clearDiscoverFocus() {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(DISCOVER_FOCUS_KEY);
}

export function buildDiscoverFocusFromTaskDraft(
  draft: IntelligenceTaskDraft,
  target: IntelligenceTaskBridgeTarget,
): Omit<DiscoverFocusPayload, 'createdAt'> {
  const cardMetadata = asRecord(draft.metadata.card_metadata);
  const kol = asRecord(cardMetadata.kol);
  const launch = asRecord(cardMetadata.launch);
  const handle = text(kol.handle || kol.display_name || draft.entityId, '');
  const product = text(launch.product_sku || launch.product_name || launch.name, '');
  const query = [handle, product].filter(Boolean).join(' ') || draft.title;
  return {
    source: 'intelligence_task_draft',
    title: draft.title,
    summary: target.intent || draft.objective,
    query,
    platform: normalizeDiscoverPlatform(kol.platform),
    sourceLabel: draft.sourceLabel,
  };
}
