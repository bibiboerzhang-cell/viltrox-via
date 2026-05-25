import {
  buildTaskBridgeFocus,
  taskDraftBridgeTargets,
  taskDraftEndpointHint,
  writeTaskBridgeFocus,
  type IntelligenceTaskDraft,
} from './intelligenceTaskDraft';
import { buildDiscoverFocusFromTaskDraft, writeDiscoverFocus } from './intelligenceDiscoveryFocus';

interface IntelligenceTaskDraftPanelProps {
  draft?: IntelligenceTaskDraft | null;
  viewMode?: 'manager' | 'employee';
}

const workstreamLabels: Record<string, string> = {
  recommendation_review: '推荐复核',
  market_watch: '市场跟进',
  evidence_review: '证据复核',
  sync_review: '同步复核',
  repair_review: '修复复核',
  brief_followup: '简报跟进',
};

export function IntelligenceTaskDraftPanel({ draft, viewMode = 'manager' }: IntelligenceTaskDraftPanelProps) {
  if (!draft) {
    return (
      <div className="vkpi-intelligence-task-draft">
        <b>任务 Draft</b>
        <p>点击“接受”后会生成本地任务草稿。当前阶段不会自动创建真实任务。</p>
      </div>
    );
  }

  const bridgeTargets = taskDraftBridgeTargets(draft, viewMode);

  return (
    <div className="vkpi-intelligence-task-draft">
      <b>任务 Draft</b>
      <p>{draft.title}</p>
      <div className="vkpi-intelligence-task-draft__objective">
        <strong>目标</strong>
        <span>{draft.objective}</span>
      </div>
      <div>
        <span><strong>owner</strong>{draft.ownerRole}</span>
        <span><strong>workstream</strong>{workstreamLabels[draft.workstream] || draft.workstream}</span>
        <span><strong>due</strong>{draft.duePolicy}</span>
        <span><strong>target</strong>{draft.futureWriteTarget}</span>
        <span><strong>evidence</strong>{draft.evidenceRefs.length} refs</span>
        <span><strong>endpoint</strong>{taskDraftEndpointHint(draft)}</span>
      </div>
      <div className="vkpi-intelligence-task-bridge">
        <strong>执行入口</strong>
        {bridgeTargets.map((target) => (
          <a
            className={`vkpi-button ${target.primary ? 'vkpi-button--primary' : ''}`}
            href={`#${target.page}`}
            key={target.page}
            onClick={() => {
              writeTaskBridgeFocus(buildTaskBridgeFocus(draft, target));
              if (target.page === 'discover') writeDiscoverFocus(buildDiscoverFocusFromTaskDraft(draft, target));
            }}
            title={target.intent}
          >
            {target.label}
          </a>
        ))}
      </div>
    </div>
  );
}

export default IntelligenceTaskDraftPanel;
