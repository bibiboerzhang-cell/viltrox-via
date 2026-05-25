import {
  numberValue,
  text,
  type Row,
} from './IntelligenceCenterModels';

function actionLabel(value: unknown): string {
  const key = text(value, 'pending_review');
  if (key === 'ready') return '建议确认 ready';
  if (key === 'ignored') return '建议忽略 ignored';
  if (key === 'rejected') return '建议拒绝 rejected';
  if (key === 'pending_review') return '继续人工复核';
  return key;
}

export function CompetitorReviewSuggestionPanel({ suggestion }: { suggestion?: Row | null }) {
  if (!suggestion) {
    return (
      <div className="vkpi-competitor-review-suggestion">
        <b>竞品审核建议</b>
        <p>当前卡片没有匹配到只读审核建议；仍可查看上方证据链，后续再人工确认。</p>
      </div>
    );
  }
  const confidence = numberValue(suggestion.confidence);
  const reasons = Array.isArray(suggestion.reasons) ? suggestion.reasons.map((item) => text(item, '')).filter(Boolean) : [];
  return (
    <div className="vkpi-competitor-review-suggestion">
      <div className="vkpi-competitor-review-suggestion__head">
        <b>竞品审核建议</b>
        <em>{actionLabel(suggestion.suggested_action)}</em>
      </div>
      <p>只读建议，未写库。确认前不影响推荐排序、风险等级或运营结论。</p>
      <div>
        <span><strong>confidence</strong>{confidence ? `${Math.round(confidence * 100)}%` : '-'}</span>
        <span><strong>brand</strong>{text(suggestion.brand, '-')}</span>
        <span><strong>type</strong>{text(suggestion.signal_type, '-')}</span>
        <span><strong>source</strong>{text(suggestion.source_table, '-')}:{text(suggestion.source_id, '-')}</span>
      </div>
      <div className="vkpi-competitor-review-suggestion__reasons">
        <strong>原因</strong>
        {reasons.length ? reasons.map((reason) => <span key={reason}>{reason}</span>) : <span>暂无原因</span>}
      </div>
    </div>
  );
}
