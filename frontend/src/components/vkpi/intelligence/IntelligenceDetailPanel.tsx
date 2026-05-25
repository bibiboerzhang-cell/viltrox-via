import type { ReactNode } from 'react';
import type { IntelligenceAction, IntelligenceCardModel } from './IntelligenceCard';

interface DetailMetaItem {
  label: string;
  value: string;
}

interface IntelligenceDetailPanelProps {
  card?: IntelligenceCardModel;
  eyebrow?: string;
  emptyTitle: string;
  emptySummary: string;
  metaItems?: DetailMetaItem[];
  summarySlot?: ReactNode;
  extraSlot?: ReactNode;
  footerNote?: string;
  onAction?: (action: IntelligenceAction, card: IntelligenceCardModel) => void;
}

function confidenceLabel(card?: IntelligenceCardModel): string {
  if (typeof card?.confidence === 'number') return `${Math.round(card.confidence * 100)}%`;
  return 'evidence';
}

function defaultMetaItems(card?: IntelligenceCardModel): DetailMetaItem[] {
  return [
    { label: '对象', value: card?.entityType || '-' },
    { label: '来源', value: card?.sourceLabel || '-' },
    { label: '新鲜度', value: card?.freshnessLabel || '-' },
    { label: '置信度', value: confidenceLabel(card) },
  ];
}

export function IntelligenceDetailPanel({
  card,
  eyebrow,
  emptyTitle,
  emptySummary,
  metaItems,
  summarySlot,
  extraSlot,
  footerNote,
  onAction,
}: IntelligenceDetailPanelProps) {
  const resolvedMeta = metaItems || defaultMetaItems(card);
  const title = card?.title || emptyTitle;
  const summary = card?.summary || emptySummary;

  return (
    <aside className="vkpi-agent-detail">
      <div className="vkpi-agent-detail__head">
        <span className="vkpi-agent-status is-active">{eyebrow || card?.type || 'intelligence'}</span>
        <h2>{title}</h2>
        <p>{summary}</p>
      </div>

      <div className="vkpi-agent-detail__meta">
        {resolvedMeta.map((item) => (
          <span key={`${item.label}-${item.value}`}>
            <b>{item.label}</b>
            {item.value}
          </span>
        ))}
      </div>

      {summarySlot}

      <div className="vkpi-agent-next vkpi-intelligence-detail__evidence">
        <b>证据链</b>
        {card?.evidence.length ? card.evidence.map((item) => (
          <article key={`${item.label}-${item.source}-${item.value || item.url || ''}`}>
            <strong>{item.label}</strong>
            <span>{item.source} · {item.value || '-'}</span>
            {item.url ? <a href={item.url} target="_blank" rel="noreferrer">打开来源</a> : null}
          </article>
        )) : <p>暂无证据。后续卡片必须补 evidence refs。</p>}
      </div>

      {extraSlot}

      <div className="vkpi-intelligence-detail__actions">
        {(card?.actions || []).map((action) => (
          <button
            className={`vkpi-button ${action.kind === 'primary' ? 'vkpi-button--primary' : ''}`}
            type="button"
            disabled={action.kind === 'disabled' || !card}
            key={action.label}
            onClick={() => {
              if (card) onAction?.(action, card);
            }}
          >
            {action.label}
          </button>
        ))}
      </div>

      {footerNote ? <p className="vkpi-help-text">{footerNote}</p> : null}
    </aside>
  );
}

export default IntelligenceDetailPanel;
