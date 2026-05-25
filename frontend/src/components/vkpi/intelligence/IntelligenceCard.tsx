export type IntelligenceCardType = 'brief' | 'recommendation' | 'evidence' | 'sync' | 'market' | 'competitor' | 'kol' | 'repair' | 'system';
export type IntelligenceCardPriority = 'high' | 'medium' | 'low';
export type IntelligenceCardStatus = 'open' | 'accepted' | 'rejected' | 'snoozed' | 'done' | 'blocked';

export interface IntelligenceEvidenceItem {
  label: string;
  source: string;
  value?: string;
  url?: string;
}

export interface IntelligenceAction {
  label: string;
  kind: 'primary' | 'secondary' | 'disabled';
}

export interface IntelligenceCardModel {
  id: string;
  type: IntelligenceCardType;
  priority: IntelligenceCardPriority;
  status: IntelligenceCardStatus;
  title: string;
  summary: string;
  entityType: string;
  entityId?: string;
  confidence?: number;
  freshnessLabel?: string;
  sourceLabel: string;
  evidence: IntelligenceEvidenceItem[];
  actions: IntelligenceAction[];
  metadata?: Record<string, unknown>;
}

interface IntelligenceCardProps {
  card: IntelligenceCardModel;
  selected?: boolean;
  compact?: boolean;
  onSelect?: (card: IntelligenceCardModel) => void;
}

const typeLabels: Record<IntelligenceCardType, string> = {
  brief: '今日判断',
  recommendation: '推荐',
  evidence: '证据',
  sync: '同步',
  market: '市场',
  competitor: '竞品',
  kol: 'KOL',
  repair: '修复',
  system: '系统',
};

const priorityLabels: Record<IntelligenceCardPriority, string> = {
  high: '高',
  medium: '中',
  low: '低',
};

export const intelligenceStatusLabels: Record<IntelligenceCardStatus, string> = {
  open: '待处理',
  accepted: '已接受',
  rejected: '已拒绝',
  snoozed: '已延后',
  done: '已完成',
  blocked: '受阻',
};

export function IntelligenceCard({ card, selected = false, compact = false, onSelect }: IntelligenceCardProps) {
  const confidence = typeof card.confidence === 'number' ? `${Math.round(card.confidence * 100)}%` : '证据驱动';
  return (
    <article
      className={`vkpi-intelligence-card is-${card.priority} ${selected ? 'is-selected' : ''} ${compact ? 'is-compact' : ''}`}
      role="button"
      tabIndex={0}
      onClick={() => onSelect?.(card)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') onSelect?.(card);
      }}
    >
      <div className="vkpi-intelligence-card__top">
        <span>{typeLabels[card.type]}</span>
        <em>{priorityLabels[card.priority]}</em>
      </div>
      <h3>{card.title}</h3>
      <p>{card.summary}</p>
      <div className="vkpi-intelligence-card__meta">
        <span>{card.entityType}</span>
        <span>{confidence}</span>
        <span>{card.freshnessLabel || card.sourceLabel}</span>
      </div>
      <div className="vkpi-intelligence-card__footer">
        <small>{intelligenceStatusLabels[card.status]}</small>
        <small>{card.evidence.length} 条证据</small>
      </div>
    </article>
  );
}
