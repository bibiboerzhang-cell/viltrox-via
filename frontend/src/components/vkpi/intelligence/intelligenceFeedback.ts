import type { IntelligenceCardModel, IntelligenceCardStatus } from './IntelligenceCard';
import { productRecommendationAction } from '../../../domains/products';

export type IntelligenceFeedbackAction = 'accept' | 'reject' | 'snooze' | 'done';

export interface IntelligenceFeedbackDraft {
  schemaVersion: 'v2-intelligence-feedback-v0';
  mode: 'local_only';
  cardId: string;
  cardType: string;
  cardTitle: string;
  entityType: string;
  entityId?: string;
  decisionStatus: IntelligenceCardStatus;
  feedbackAction: IntelligenceFeedbackAction;
  feedbackType: string;
  sourceLabel: string;
  evidenceCount: number;
  reason: string;
  pagePath: string;
  auditActionType: string;
  futureWriteTarget: 'vkpi_recommendation_feedback' | 'vkpi_team_feedback' | 'vkpi_memory_feedback';
  createdAt: string;
  metadata: Record<string, unknown>;
}

export interface IntelligenceFeedbackSubmitResult {
  status: 'local_only' | 'not_eligible' | 'submitted' | 'failed';
  message: string;
  endpointHint: string;
  recommendationId?: string;
  response?: Record<string, unknown>;
}

const statusToAction: Partial<Record<IntelligenceCardStatus, IntelligenceFeedbackAction>> = {
  accepted: 'accept',
  rejected: 'reject',
  snoozed: 'snooze',
  done: 'done',
};

const actionToFeedbackType: Record<IntelligenceFeedbackAction, string> = {
  accept: 'shortlist',
  reject: 'reject',
  snooze: 'snooze',
  done: 'done',
};

export function feedbackActionForStatus(status: IntelligenceCardStatus): IntelligenceFeedbackAction | null {
  return statusToAction[status] || null;
}

export function futureWriteTargetForCard(card: IntelligenceCardModel): IntelligenceFeedbackDraft['futureWriteTarget'] {
  if (card.type === 'recommendation' || card.metadata?.recommendation_id) return 'vkpi_recommendation_feedback';
  if (card.type === 'evidence' || card.type === 'kol') return 'vkpi_memory_feedback';
  return 'vkpi_team_feedback';
}

export function buildIntelligenceFeedbackDraft(
  card: IntelligenceCardModel,
  status: IntelligenceCardStatus,
  options: { pagePath: string; reason?: string } = { pagePath: 'intelligence' },
): IntelligenceFeedbackDraft {
  const action = feedbackActionForStatus(status) || 'snooze';
  const reason = options.reason || `ui_${action}`;
  return {
    schemaVersion: 'v2-intelligence-feedback-v0',
    mode: 'local_only',
    cardId: card.id,
    cardType: card.type,
    cardTitle: card.title,
    entityType: card.entityType,
    entityId: card.entityId,
    decisionStatus: status,
    feedbackAction: action,
    feedbackType: actionToFeedbackType[action],
    sourceLabel: card.sourceLabel,
    evidenceCount: card.evidence.length,
    reason,
    pagePath: options.pagePath,
    auditActionType: `intelligence_card_${action}`,
    futureWriteTarget: futureWriteTargetForCard(card),
    createdAt: new Date().toISOString(),
    metadata: {
      confidence: card.confidence,
      freshness_label: card.freshnessLabel,
      source_label: card.sourceLabel,
      evidence_refs: card.evidence.map((item) => ({
        label: item.label,
        source: item.source,
        url: item.url,
      })),
      card_metadata: card.metadata || {},
    },
  };
}

export function feedbackDraftEndpointHint(draft: IntelligenceFeedbackDraft): string {
  if (draft.futureWriteTarget === 'vkpi_recommendation_feedback') return 'productRecommendationAction(..., feedback/reject/shortlist)';
  if (draft.futureWriteTarget === 'vkpi_memory_feedback') return 'POST /api/admin/vkpi/memory/feedback';
  return 'POST /api/admin/vkpi/feedback';
}

export function recommendationIdForCard(card: IntelligenceCardModel, draft?: IntelligenceFeedbackDraft | null): string {
  const fromMetadata = card.metadata?.recommendation_id || card.metadata?.recommendationId;
  const fromDraft = draft?.metadata?.card_metadata && typeof draft.metadata.card_metadata === 'object'
    ? (draft.metadata.card_metadata as Record<string, unknown>).recommendation_id || (draft.metadata.card_metadata as Record<string, unknown>).recommendationId
    : '';
  return String(fromMetadata || fromDraft || card.entityId || '').trim();
}

function productActionForFeedback(action: IntelligenceFeedbackAction): 'shortlist' | 'reject' | 'feedback' {
  if (action === 'accept') return 'shortlist';
  if (action === 'reject') return 'reject';
  return 'feedback';
}

export async function submitRecommendationFeedbackDraft(
  token: string,
  card: IntelligenceCardModel,
  draft: IntelligenceFeedbackDraft,
): Promise<IntelligenceFeedbackSubmitResult> {
  const endpointHint = feedbackDraftEndpointHint(draft);
  const recommendationId = recommendationIdForCard(card, draft);
  if (draft.futureWriteTarget !== 'vkpi_recommendation_feedback' || card.type !== 'recommendation') {
    return {
      status: 'not_eligible',
      message: '只有 recommendation 类型卡片允许写入推荐反馈。',
      endpointHint,
    };
  }
  if (!recommendationId) {
    return {
      status: 'not_eligible',
      message: '缺少 recommendation_id；保持本地 draft，不写库。',
      endpointHint,
    };
  }
  if (!token) {
    return {
      status: 'not_eligible',
      message: '缺少 API token；保持本地 draft，不写库。',
      endpointHint,
      recommendationId,
    };
  }

  try {
    const action = productActionForFeedback(draft.feedbackAction);
    const response = await productRecommendationAction(token, recommendationId, action, {
      reason: draft.reason,
      note: `${draft.cardTitle} · ${draft.feedbackAction}`,
      source: 'intelligence_card_v0',
      intelligence_feedback: draft,
    });
    return {
      status: 'submitted',
      message: `已写入推荐反馈：${action}`,
      endpointHint,
      recommendationId,
      response,
    };
  } catch (error) {
    return {
      status: 'failed',
      message: error instanceof Error ? error.message : '推荐反馈写入失败',
      endpointHint,
      recommendationId,
    };
  }
}
