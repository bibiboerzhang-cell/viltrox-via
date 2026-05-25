import { intelligenceStatusLabels, type IntelligenceCardModel, type IntelligenceCardStatus } from './IntelligenceCard';

interface IntelligenceActionStripProps {
  card?: IntelligenceCardModel;
  mode?: 'local' | 'read_only';
  status?: IntelligenceCardStatus;
  submitting?: boolean;
  statusHint?: string;
  onStatusChange?: (status: IntelligenceCardStatus, card: IntelligenceCardModel) => void;
}

const actionStatuses: Array<{ status: IntelligenceCardStatus; label: string }> = [
  { status: 'accepted', label: '接受' },
  { status: 'snoozed', label: '延后' },
  { status: 'rejected', label: '拒绝' },
  { status: 'done', label: '完成' },
];

export function IntelligenceActionStrip({ card, mode = 'local', status, submitting = false, statusHint, onStatusChange }: IntelligenceActionStripProps) {
  const currentStatus = status || card?.status || 'open';
  const readOnly = mode === 'read_only' || !card || submitting;

  return (
    <div className="vkpi-intelligence-action-strip">
      <div>
        <b>处理状态</b>
        <span>{submitting ? '提交中' : intelligenceStatusLabels[currentStatus]}</span>
      </div>
      <div className="vkpi-intelligence-action-strip__buttons">
        {actionStatuses.map((action) => (
          <button
            className={`vkpi-button ${currentStatus === action.status ? 'vkpi-button--primary' : ''}`}
            type="button"
            disabled={readOnly}
            key={action.status}
            onClick={() => {
              if (card) onStatusChange?.(action.status, card);
            }}
          >
            {action.label}
          </button>
        ))}
      </div>
      <small>{submitting ? '正在写入推荐反馈，请等待返回。' : statusHint || (readOnly ? '只读' : '本页状态')}</small>
    </div>
  );
}

export default IntelligenceActionStrip;
