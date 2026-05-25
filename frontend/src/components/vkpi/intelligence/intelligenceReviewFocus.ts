import type { IntelligenceCardModel } from './IntelligenceCard';

type Row = Record<string, unknown>;

const INTELLIGENCE_REVIEW_CARD_KEY = 'vkpi:intelligence-center:review-card';

function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

function text(value: unknown, fallback = ''): string {
  const next = String(value ?? '').trim();
  return next || fallback;
}

function evidenceItems(value: unknown): IntelligenceCardModel['evidence'] {
  return Array.isArray(value)
    ? value.filter((item): item is IntelligenceCardModel['evidence'][number] => {
      const row = asRecord(item);
      return Boolean(text(row.label) && text(row.source));
    })
    : [];
}

export function writeIntelligenceReviewCard(card: IntelligenceCardModel) {
  if (typeof window === 'undefined') return;
  window.sessionStorage.setItem(INTELLIGENCE_REVIEW_CARD_KEY, JSON.stringify({
    ...card,
    metadata: {
      ...(card.metadata || {}),
      review_focus_created_at: new Date().toISOString(),
    },
  }));
}

export function readIntelligenceReviewCard(): IntelligenceCardModel | null {
  if (typeof window === 'undefined') return null;
  const raw = window.sessionStorage.getItem(INTELLIGENCE_REVIEW_CARD_KEY);
  if (!raw) return null;
  try {
    const parsed = asRecord(JSON.parse(raw));
    const id = text(parsed.id);
    const title = text(parsed.title);
    if (!id || !title) return null;
    return {
      id,
      type: ['brief', 'recommendation', 'evidence', 'sync', 'market', 'competitor', 'kol', 'repair', 'system'].includes(text(parsed.type))
        ? parsed.type as IntelligenceCardModel['type']
        : 'recommendation',
      priority: ['high', 'medium', 'low'].includes(text(parsed.priority))
        ? parsed.priority as IntelligenceCardModel['priority']
        : 'medium',
      status: ['open', 'accepted', 'rejected', 'snoozed', 'done', 'blocked'].includes(text(parsed.status))
        ? parsed.status as IntelligenceCardModel['status']
        : 'open',
      title,
      summary: text(parsed.summary, '从 Discover 带入的候选，等待人工复核。'),
      entityType: text(parsed.entityType, 'discover_candidate'),
      entityId: text(parsed.entityId, '') || undefined,
      confidence: typeof parsed.confidence === 'number' ? parsed.confidence : undefined,
      freshnessLabel: text(parsed.freshnessLabel, 'discover'),
      sourceLabel: text(parsed.sourceLabel, 'discover review'),
      evidence: evidenceItems(parsed.evidence),
      actions: Array.isArray(parsed.actions) ? parsed.actions as IntelligenceCardModel['actions'] : [
        { label: '查看证据', kind: 'primary' },
      ],
      metadata: asRecord(parsed.metadata),
    };
  } catch {
    return null;
  }
}

export function clearIntelligenceReviewCard() {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(INTELLIGENCE_REVIEW_CARD_KEY);
}
