import { feedbackDraftEndpointHint, type IntelligenceFeedbackDraft, type IntelligenceFeedbackSubmitResult } from './intelligenceFeedback';

interface IntelligenceFeedbackDraftPanelProps {
  draft?: IntelligenceFeedbackDraft | null;
  submitResult?: IntelligenceFeedbackSubmitResult | null;
  writeEnabled?: boolean;
  submitting?: boolean;
}

const actionLabels: Record<string, string> = {
  accept: '接受',
  reject: '拒绝',
  snooze: '延后',
  done: '完成',
};

function feedbackWriteStatus(
  submitResult?: IntelligenceFeedbackSubmitResult | null,
  writeEnabled = false,
  submitting = false,
): string {
  if (submitting) return '提交中';
  if (submitResult?.status === 'submitted') return '已写库';
  if (submitResult?.status === 'failed') return '写库失败，保留本地 draft';
  if (submitResult?.status === 'not_eligible') return '不符合写库条件，保留本地 draft';
  if (writeEnabled) return '写库开关已开；重新选择状态会提交';
  return '仅本地 draft，未写库';
}

export function IntelligenceFeedbackDraftPanel({ draft, submitResult, writeEnabled = false, submitting = false }: IntelligenceFeedbackDraftPanelProps) {
  if (!draft) {
    return (
      <div className="vkpi-intelligence-feedback-draft">
        <b>反馈写库</b>
        <p>尚未选择处理动作。选择接受、拒绝、延后或完成后会生成 feedback draft；写库开关打开时会提交到推荐反馈 API。</p>
      </div>
    );
  }

  const writeStatus = feedbackWriteStatus(submitResult, writeEnabled, submitting);

  return (
    <div className="vkpi-intelligence-feedback-draft">
      <b>反馈 Draft</b>
      <p>
        {actionLabels[draft.feedbackAction] || draft.feedbackAction} · {draft.futureWriteTarget} ·
        {writeStatus}
      </p>
      <div>
        <span><strong>card</strong>{draft.cardId}</span>
        <span><strong>type</strong>{draft.feedbackType}</span>
        <span><strong>audit</strong>{draft.auditActionType}</span>
        <span><strong>endpoint</strong>{feedbackDraftEndpointHint(draft)}</span>
      </div>
      {submitResult ? <p>{submitResult.message}</p> : null}
    </div>
  );
}

export default IntelligenceFeedbackDraftPanel;
