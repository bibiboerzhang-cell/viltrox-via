import type { IntelligenceCardModel, IntelligenceCardStatus } from './IntelligenceCard';

type Row = Record<string, unknown>;

export type CandidateDecisionStatus = Extract<IntelligenceCardStatus, 'accepted' | 'rejected' | 'snoozed'>;

export interface CandidateDecisionRecord {
  schemaVersion: 'v2-discover-candidate-decision-v0';
  mode: 'local_only';
  cardId: string;
  status: CandidateDecisionStatus;
  title: string;
  summary: string;
  handle: string;
  platform: string;
  kolId?: string;
  kolPoolId?: string;
  productSku?: string;
  productName?: string;
  projectNote: string;
  query: string;
  priority: IntelligenceCardModel['priority'];
  sourceLabel: string;
  nextActionLabel: string;
  nextActionHint: string;
  evidenceCount: number;
  decidedAt: string;
  metadata: Row;
}

const CANDIDATE_DECISIONS_KEY = 'vkpi:discover:candidate-decisions';
const CANDIDATE_LATEST_DECISION_KEY = 'vkpi:discover:latest-candidate-decision';
const MAX_CANDIDATE_DECISIONS = 20;

function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

function text(value: unknown, fallback = ''): string {
  const next = String(value ?? '').trim();
  return next || fallback;
}

function normalizeStatus(status: IntelligenceCardStatus): CandidateDecisionStatus | null {
  if (status === 'accepted' || status === 'rejected' || status === 'snoozed') return status;
  return null;
}

function nextAction(status: CandidateDecisionStatus): Pick<CandidateDecisionRecord, 'nextActionLabel' | 'nextActionHint'> {
  if (status === 'accepted') {
    return {
      nextActionLabel: '加入项目 / 联系候选',
      nextActionHint: '候选已通过复核，下一步应补齐产品、联系方式和项目归属。',
    };
  }
  if (status === 'snoozed') {
    return {
      nextActionLabel: '稍后复查',
      nextActionHint: '候选暂不推进，保留搜索上下文，后续可重新打开资料。',
    };
  }
  return {
    nextActionLabel: '暂不合作',
    nextActionHint: '候选已被拒绝，本地保留原因草稿，后续可用于避免重复推荐。',
  };
}

function productField(product: Row, keys: string[]): string {
  for (const key of keys) {
    const value = text(product[key]);
    if (value) return value;
  }
  return '';
}

export function isDiscoverCandidateCard(card: IntelligenceCardModel): boolean {
  const metadata = asRecord(card.metadata);
  return card.entityType === 'discover_candidate' || text(metadata.source_kind) === 'kol' || Boolean(metadata.discover_query);
}

export function buildCandidateDecisionRecord(
  card: IntelligenceCardModel,
  status: IntelligenceCardStatus,
): CandidateDecisionRecord | null {
  const normalized = normalizeStatus(status);
  if (!normalized || !isDiscoverCandidateCard(card)) return null;

  const metadata = asRecord(card.metadata);
  const kol = asRecord(metadata.kol);
  const productFit = asRecord(metadata.product_fit);
  const action = nextAction(normalized);
  const handle = text(kol.handle || kol.display_name || card.title.split('·')[0], card.title);
  const query = text(metadata.discover_query, handle);
  const productSku = productField(productFit, ['product_sku', 'productSku', 'sku', 'launch_sku']);
  const productName = productField(productFit, ['product_name', 'productName', 'launch_name', 'name']);
  const projectNote = [
    `来源：智能中心候选复核 ${normalized}`,
    `卡片：${card.title}`,
    `证据：${card.evidence.length} refs`,
    `查询：${query}`,
    card.confidence ? `置信度：${Math.round(card.confidence * 100)}%` : '',
    card.summary,
  ].filter(Boolean).join('\n');
  return {
    schemaVersion: 'v2-discover-candidate-decision-v0',
    mode: 'local_only',
    cardId: card.id,
    status: normalized,
    title: card.title,
    summary: card.summary,
    handle,
    platform: text(kol.platform || metadata.discover_platform, 'all'),
    kolId: text(kol.id || card.entityId, '') || undefined,
    kolPoolId: text(kol.kol_pool_id, '') || undefined,
    productSku: productSku || undefined,
    productName: productName || undefined,
    projectNote,
    query,
    priority: card.priority,
    sourceLabel: card.sourceLabel,
    nextActionLabel: action.nextActionLabel,
    nextActionHint: action.nextActionHint,
    evidenceCount: card.evidence.length,
    decidedAt: new Date().toISOString(),
    metadata,
  };
}

function readStoredDecisions(): CandidateDecisionRecord[] {
  if (typeof window === 'undefined') return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(CANDIDATE_DECISIONS_KEY) || '[]');
    return Array.isArray(parsed) ? parsed.filter((item): item is CandidateDecisionRecord => Boolean(asRecord(item).cardId)) : [];
  } catch {
    return [];
  }
}

export function writeCandidateDecision(record: CandidateDecisionRecord) {
  if (typeof window === 'undefined') return;
  const next = [
    record,
    ...readStoredDecisions().filter((item) => item.cardId !== record.cardId),
  ].slice(0, MAX_CANDIDATE_DECISIONS);
  window.localStorage.setItem(CANDIDATE_DECISIONS_KEY, JSON.stringify(next));
  window.sessionStorage.setItem(CANDIDATE_LATEST_DECISION_KEY, JSON.stringify(record));
}

export function readLatestCandidateDecision(): CandidateDecisionRecord | null {
  if (typeof window === 'undefined') return null;
  const raw = window.sessionStorage.getItem(CANDIDATE_LATEST_DECISION_KEY);
  if (!raw) return null;
  try {
    const parsed = asRecord(JSON.parse(raw));
    const cardId = text(parsed.cardId);
    const status = normalizeStatus(text(parsed.status) as IntelligenceCardStatus);
    if (!cardId || !status) return null;
    return {
      schemaVersion: 'v2-discover-candidate-decision-v0',
      mode: 'local_only',
      cardId,
      status,
      title: text(parsed.title, '候选复核结果'),
      summary: text(parsed.summary, '来自智能中心的候选复核。'),
      handle: text(parsed.handle, '-'),
      platform: text(parsed.platform, 'all'),
      kolId: text(parsed.kolId, '') || undefined,
      kolPoolId: text(parsed.kolPoolId, '') || undefined,
      productSku: text(parsed.productSku, '') || undefined,
      productName: text(parsed.productName, '') || undefined,
      projectNote: text(parsed.projectNote, `来源：智能中心候选复核 ${status}`),
      query: text(parsed.query, text(parsed.handle, '')),
      priority: ['high', 'medium', 'low'].includes(text(parsed.priority))
        ? parsed.priority as IntelligenceCardModel['priority']
        : 'medium',
      sourceLabel: text(parsed.sourceLabel, 'intelligence center'),
      nextActionLabel: text(parsed.nextActionLabel, nextAction(status).nextActionLabel),
      nextActionHint: text(parsed.nextActionHint, nextAction(status).nextActionHint),
      evidenceCount: Number(parsed.evidenceCount || 0),
      decidedAt: text(parsed.decidedAt, new Date().toISOString()),
      metadata: asRecord(parsed.metadata),
    };
  } catch {
    return null;
  }
}

export function clearLatestCandidateDecision() {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(CANDIDATE_LATEST_DECISION_KEY);
}
