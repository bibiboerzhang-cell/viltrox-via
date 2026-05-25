import type { IntelligenceCardStatus } from './IntelligenceCard';

export type IntelligenceActionFeedbackStatus = Extract<IntelligenceCardStatus, 'accepted' | 'snoozed' | 'done'>;

export interface IntelligenceActionFeedbackRecord {
  cardId: string;
  status: IntelligenceActionFeedbackStatus;
  updatedAt: string;
}

const ACTION_FEEDBACK_KEY = 'vkpi:employee-action-feedback-v0';

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function text(value: unknown, fallback = ''): string {
  const next = String(value ?? '').trim();
  return next || fallback;
}

function status(value: unknown): IntelligenceActionFeedbackStatus | null {
  const key = text(value);
  return key === 'accepted' || key === 'snoozed' || key === 'done' ? key : null;
}

export function readIntelligenceActionFeedback(): Record<string, IntelligenceActionFeedbackRecord> {
  if (typeof window === 'undefined') return {};
  const raw = window.localStorage.getItem(ACTION_FEEDBACK_KEY);
  if (!raw) return {};
  try {
    const parsed = asRecord(JSON.parse(raw));
    return Object.entries(parsed).reduce<Record<string, IntelligenceActionFeedbackRecord>>((acc, [cardId, value]) => {
      const row = asRecord(value);
      const nextStatus = status(row.status);
      if (!cardId || !nextStatus) return acc;
      acc[cardId] = {
        cardId,
        status: nextStatus,
        updatedAt: text(row.updatedAt, new Date().toISOString()),
      };
      return acc;
    }, {});
  } catch {
    return {};
  }
}

export function writeIntelligenceActionFeedback(cardId: string, nextStatus: IntelligenceActionFeedbackStatus): IntelligenceActionFeedbackRecord {
  const record = { cardId, status: nextStatus, updatedAt: new Date().toISOString() };
  if (typeof window === 'undefined') return record;
  const current = readIntelligenceActionFeedback();
  window.localStorage.setItem(ACTION_FEEDBACK_KEY, JSON.stringify({ ...current, [cardId]: record }));
  return record;
}
