import { platformLabels } from '../../shared/vkpiConstants';
import { platformFromRaw } from '../../shared/vkpiDataUtils';
import type { CandidateDecisionRecord } from '../../intelligence/intelligenceCandidateDecision';
import type { DiscoverFocusPayload } from '../../intelligence/intelligenceDiscoveryFocus';

export interface DiscoveryQueueItem {
  id: string;
  type: 'recommendation' | 'project_gap' | 'brand_signal';
  priority: 'high' | 'medium' | 'low';
  title: string;
  summary: string;
  query: string;
  platform: string;
  source: string;
  evidence: string[];
}

const discoveryTypeLabels: Record<DiscoveryQueueItem['type'], string> = {
  recommendation: '推荐复核',
  project_gap: '项目补人',
  brand_signal: '趋势信号',
};

function discoverFocusPlatformLabel(focus: DiscoverFocusPayload): string {
  const platformKey = focus.platform || 'all';
  return platformKey === 'all' ? '全部平台' : platformLabels[platformFromRaw(platformKey)] || platformKey;
}

function discoverFocusKeywords(focus: DiscoverFocusPayload): string[] {
  return focus.query
    .split(/\s+/)
    .map((item) => item.trim())
    .filter((item) => item && item.length > 1)
    .slice(0, 6);
}

function discoverFocusIntent(focus: DiscoverFocusPayload): string {
  const source = `${focus.source} ${focus.sourceLabel || ''}`.toLowerCase();
  const query = focus.query.toLowerCase();
  if (source.includes('my_kol') || query.includes('camera gear')) return '找同平台、同内容方向的相似 KOL';
  if (query.includes('viltrox')) return '优先找近期提到 Viltrox / 镜头测评的人';
  if (query.includes('collaboration')) return '补项目缺口，先看适合联系的创作者';
  if (source.includes('brand_signal')) return '从品牌信号继续下钻候选';
  return '把上游任务转成可执行的红人搜索';
}

function DiscoverFocusPlan({ focus }: { focus: DiscoverFocusPayload }) {
  const keywords = discoverFocusKeywords(focus);
  const platformLabel = discoverFocusPlatformLabel(focus);
  const steps = [
    { label: '已有档案', detail: '先查 KOL Pool / 历史合作' },
    { label: '平台候选', detail: platformLabel },
    { label: '最近内容', detail: '看 posts / 样本标题' },
    { label: '证据判断', detail: '再进智能卡或项目' },
  ];
  return (
    <div className="vkpi-discover-focus-plan">
      <div className="vkpi-discover-focus-plan__why">
        <span><strong>为什么搜</strong>{discoverFocusIntent(focus)}</span>
        <span><strong>平台</strong>{platformLabel}</span>
        <span><strong>来源</strong>{focus.sourceLabel || focus.source}</span>
      </div>
      <div className="vkpi-discover-focus-plan__keywords" aria-label="搜索关键词拆解">
        {keywords.length ? keywords.map((keyword) => <em key={keyword}>{keyword}</em>) : <em>待输入关键词</em>}
      </div>
      <div className="vkpi-discover-focus-plan__flow" aria-label="发现来源链路">
        {steps.map((step, index) => (
          <span className={index === 0 ? 'is-active' : ''} key={step.label}>
            <i>{index + 1}</i>
            <b>{step.label}</b>
            <small>{step.detail}</small>
          </span>
        ))}
      </div>
    </div>
  );
}

export function DiscoverFocusBanner({
  focus,
  onRun,
  onDismiss,
}: {
  focus: DiscoverFocusPayload;
  onRun: (focus: DiscoverFocusPayload) => void;
  onDismiss: () => void;
}) {
  return (
    <section className="vkpi-discover-focus" aria-live="polite">
      <div>
        <span>{focus.sourceLabel || focus.source}</span>
        <h3>{focus.title}</h3>
        <p>{focus.summary}</p>
      </div>
      <div className="vkpi-discover-focus__query">
        <strong>{discoverFocusPlatformLabel(focus)}</strong>
        <span>{focus.query}</span>
      </div>
      <DiscoverFocusPlan focus={focus} />
      <div className="vkpi-discover-focus__actions">
        <button className="vkpi-discover-btn is-primary" type="button" onClick={() => onRun(focus)}>执行搜索</button>
        <button className="vkpi-discover-btn" type="button" onClick={onDismiss}>关闭</button>
      </div>
    </section>
  );
}

const candidateDecisionLabels: Record<CandidateDecisionRecord['status'], string> = {
  accepted: '已接受',
  rejected: '已拒绝',
  snoozed: '已延后',
};

export function CandidateDecisionBanner({
  decision,
  canOpenProject,
  canPrepareCandidate,
  preparing,
  prepareLabel,
  selectedKolId,
  onRun,
  onPrepareCandidate,
  onOpenProject,
  onDismiss,
}: {
  decision: CandidateDecisionRecord;
  canOpenProject: boolean;
  canPrepareCandidate: boolean;
  preparing: boolean;
  prepareLabel: string;
  selectedKolId?: string;
  onRun: (decision: CandidateDecisionRecord) => void;
  onPrepareCandidate: (decision: CandidateDecisionRecord) => void;
  onOpenProject: (decision: CandidateDecisionRecord) => void;
  onDismiss: () => void;
}) {
  const selectedMatchLabel = selectedKolId && decision.kolId === selectedKolId ? '已选中' : '待回搜';
  const secondaryLabel = canOpenProject ? decision.nextActionLabel : prepareLabel;
  return (
    <section className={`vkpi-discover-candidate-decision is-${decision.status}`} aria-live="polite">
      <div>
        <span>智能中心复核 · {candidateDecisionLabels[decision.status]}</span>
        <h3>{decision.title}</h3>
        <p>{decision.nextActionHint}</p>
      </div>
      <div className="vkpi-discover-candidate-decision__meta">
        <span><strong>候选</strong>{decision.handle}</span>
        <span><strong>平台</strong>{platformLabels[platformFromRaw(decision.platform)] || decision.platform}</span>
        <span><strong>{selectedMatchLabel}</strong>{decision.evidenceCount} refs</span>
      </div>
      <div className="vkpi-discover-candidate-decision__actions">
        <button className="vkpi-discover-btn is-primary" type="button" onClick={() => onRun(decision)}>回搜候选</button>
        <button
          className="vkpi-discover-btn"
          type="button"
          onClick={() => (canOpenProject ? onOpenProject(decision) : onPrepareCandidate(decision))}
          disabled={preparing || (!canOpenProject && !canPrepareCandidate)}
        >
          {preparing ? '处理中' : secondaryLabel}
        </button>
        <button className="vkpi-discover-btn" type="button" onClick={onDismiss}>关闭</button>
      </div>
    </section>
  );
}

export function DiscoveryQueuePanel({
  items,
  loading,
  message,
  onRun,
  onOpenRecommendations,
}: {
  items: DiscoveryQueueItem[];
  loading: boolean;
  message: string;
  onRun: (item: DiscoveryQueueItem) => void;
  onOpenRecommendations: () => void;
}) {
  return (
    <section className="vkpi-discover-queue" aria-label="KOL 发现队列">
      <div className="vkpi-discover-queue__header">
        <div>
          <h3>发现队列</h3>
          <span>从推荐待反馈、项目缺口、品牌/竞品信号生成；点击后进入现有真实搜索流程。</span>
        </div>
        <button className="vkpi-discover-btn" type="button" onClick={onOpenRecommendations}>看推荐表</button>
      </div>
      {message ? <div className="vkpi-discover-empty is-compact">{message}</div> : null}
      <div className="vkpi-discover-queue__grid">
        {items.length ? items.map((item) => (
          <article className={`vkpi-discover-queue-card is-${item.priority}`} key={item.id}>
            <div>
              <span>{discoveryTypeLabels[item.type]}</span>
              <em>{item.priority}</em>
            </div>
            <h4>{item.title}</h4>
            <p>{item.summary}</p>
            <div className="vkpi-discover-queue-card__evidence">
              {item.evidence.slice(0, 3).map((evidence) => <small key={evidence}>{evidence}</small>)}
            </div>
            <footer>
              <small>{item.source}</small>
              <button type="button" onClick={() => onRun(item)} disabled={loading}>
                {loading ? '读取中' : '执行搜索'}
              </button>
            </footer>
          </article>
        )) : (
          <div className="vkpi-discover-empty">暂无发现队列。当前仍可用上方数据方向或主动搜索，不生成假候选。</div>
        )}
      </div>
    </section>
  );
}
