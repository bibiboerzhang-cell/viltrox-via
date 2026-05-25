import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  fetchIntelligenceAgentsInbox,
  type IntelligenceAgentInboxItem,
} from '../../../domains/intelligence';
import {
  getRecommendationFeedbackBacklog,
} from '../../../services/vkpi/intelligence-api';
import {
  getCompetitorBrainReviewSuggestions,
  getMarketExternalDailyPlanV0,
  getMarketIntelligenceCardsV0,
  getMarketIntelligenceV0,
  listCompetitorBrainSignals,
} from '../../../services/vkpi/market-api';
import { IntelligenceActionStrip } from '../intelligence/IntelligenceActionStrip';
import { IntelligenceCard, type IntelligenceAction, type IntelligenceCardModel, type IntelligenceCardStatus } from '../intelligence/IntelligenceCard';
import { IntelligenceDetailPanel } from '../intelligence/IntelligenceDetailPanel';
import { IntelligenceEvidenceDrawer } from '../intelligence/IntelligenceEvidenceDrawer';
import { IntelligenceFeedbackDraftPanel } from '../intelligence/IntelligenceFeedbackDraftPanel';
import { IntelligenceTaskDraftPanel } from '../intelligence/IntelligenceTaskDraftPanel';
import { buildCandidateDecisionRecord, writeCandidateDecision } from '../intelligence/intelligenceCandidateDecision';
import { clearIntelligenceReviewCard, readIntelligenceReviewCard } from '../intelligence/intelligenceReviewFocus';
import {
  buildIntelligenceFeedbackDraft,
  recommendationIdForCard,
  submitRecommendationFeedbackDraft,
  type IntelligenceFeedbackDraft,
  type IntelligenceFeedbackSubmitResult,
} from '../intelligence/intelligenceFeedback';
import { buildIntelligenceTaskDraft, type IntelligenceTaskDraft } from '../intelligence/intelligenceTaskDraft';
import { CompetitorReviewSuggestionPanel } from './intelligence-center/CompetitorReviewSuggestionPanel';
import {
  asList,
  asRecord,
  cardFromAgent,
  cardFromDailyPlanGroup,
  cardFromMarketSignal,
  cardFromRecommendationBacklog,
  clearIntelligenceFocus,
  externalIntelligenceSources,
  formatTime,
  normalizeApiCard,
  numberText,
  numberValue,
  platformAdvicePlans,
  platformCardFromPlan,
  platformPlanForCard,
  readIntelligenceFocus,
  sourceCardFromPlan,
  sourceForCard,
  text,
  type Row,
} from './intelligence-center/IntelligenceCenterModels';
import {
  DailyPlanPanel,
  IntelligenceSourceBoard,
  PlatformAdviceMatrix,
  writePlatformDiscoverFocus,
  writePlatformRepairFocus,
  writeSourceRepairFocus,
} from './intelligence-center/IntelligenceCenterPlanningPanels';
import type { WorkspacePageProps } from './WorkspacePage';

export function IntelligenceCenterPage({ apiToken, viewMode }: WorkspacePageProps) {
  const [agents, setAgents] = useState<IntelligenceAgentInboxItem[]>([]);
  const [market, setMarket] = useState<Row>({});
  const [marketCardsPayload, setMarketCardsPayload] = useState<Row>({});
  const [dailyPlan, setDailyPlan] = useState<Row>({});
  const [competitorSignalPayload, setCompetitorSignalPayload] = useState<Row>({});
  const [competitorSuggestionPayload, setCompetitorSuggestionPayload] = useState<Row>({});
  const [recommendationBacklog, setRecommendationBacklog] = useState<Row>({});
  const [selectedId, setSelectedId] = useState('');
  const [evidenceDrawer, setEvidenceDrawer] = useState<IntelligenceCardModel | null>(null);
  const [statusOverrides, setStatusOverrides] = useState<Record<string, IntelligenceCardStatus>>({});
  const [feedbackDrafts, setFeedbackDrafts] = useState<Record<string, IntelligenceFeedbackDraft>>({});
  const [feedbackSubmitResults, setFeedbackSubmitResults] = useState<Record<string, IntelligenceFeedbackSubmitResult>>({});
  const [taskDrafts, setTaskDrafts] = useState<Record<string, IntelligenceTaskDraft>>({});
  const [feedbackWriteEnabled, setFeedbackWriteEnabled] = useState(false);
  const [feedbackSubmittingId, setFeedbackSubmittingId] = useState('');
  const [recommendationBacklogRefreshing, setRecommendationBacklogRefreshing] = useState(false);
  const [recommendationBacklogRefreshedAt, setRecommendationBacklogRefreshedAt] = useState('');
  const [recommendationBacklogNotice, setRecommendationBacklogNotice] = useState('');
  const [incomingFocus, setIncomingFocus] = useState<Row>({});
  const [localReviewCards, setLocalReviewCards] = useState<IntelligenceCardModel[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setIncomingFocus(readIntelligenceFocus());
    clearIntelligenceFocus();
    const reviewCard = readIntelligenceReviewCard();
    if (reviewCard) {
      setLocalReviewCards([reviewCard]);
      setSelectedId(reviewCard.id);
      setRecommendationBacklogNotice('已从红人搜索带入候选复核卡；当前为本地 review，不会自动写库。');
      clearIntelligenceReviewCard();
    }
  }, []);

  const reloadRecommendationBacklog = useCallback(async (reason: 'manual' | 'feedback_submit' = 'manual') => {
    if (!apiToken) return;
    setRecommendationBacklogRefreshing(true);
    try {
      const nextBacklog = await getRecommendationFeedbackBacklog(apiToken, 12);
      setRecommendationBacklog(nextBacklog);
      setRecommendationBacklogRefreshedAt(new Date().toISOString());
      setRecommendationBacklogNotice(
        reason === 'feedback_submit'
          ? '推荐反馈已写入，backlog 已重新读取。'
          : '推荐 backlog 已刷新。',
      );
    } catch (nextError) {
      setRecommendationBacklogNotice(
        nextError instanceof Error
          ? `backlog 刷新失败：${nextError.message}`
          : 'backlog 刷新失败，请稍后重试。',
      );
    } finally {
      setRecommendationBacklogRefreshing(false);
    }
  }, [apiToken]);

  useEffect(() => {
    let alive = true;
    if (!apiToken) {
      setAgents([]);
      setMarket({});
      setMarketCardsPayload({});
      setDailyPlan({});
      setCompetitorSignalPayload({});
      setCompetitorSuggestionPayload({});
      setRecommendationBacklog({});
      setRecommendationBacklogRefreshedAt('');
      setRecommendationBacklogNotice('');
      setError('当前前端没有登录 token。');
      return () => { alive = false; };
    }
    setLoading(true);
    setError('');
    Promise.allSettled([
      fetchIntelligenceAgentsInbox(apiToken, { limit: 30 }),
      getMarketIntelligenceV0(apiToken, 120),
      getMarketIntelligenceCardsV0(apiToken, { limit: 40, brandLimit: 5, includeLatestLlmArtifact: true, includeLatestExternalSmoke: true }),
      getMarketExternalDailyPlanV0(apiToken, { maxHttpCalls: 6, limitPerSource: 5 }),
      listCompetitorBrainSignals(apiToken, { reviewStatus: 'pending_review', limit: 24 }),
      getCompetitorBrainReviewSuggestions(apiToken, { reviewStatus: 'pending_review', limit: 24 }),
      getRecommendationFeedbackBacklog(apiToken, 12),
    ])
      .then(([agentResult, marketResult, marketCardsResult, dailyPlanResult, competitorSignalResult, competitorSuggestionResult, recommendationResult]) => {
        if (!alive) return;
        if (agentResult.status === 'fulfilled') setAgents(agentResult.value.items || []);
        if (marketResult.status === 'fulfilled') setMarket(marketResult.value);
        if (marketCardsResult.status === 'fulfilled') setMarketCardsPayload(marketCardsResult.value);
        if (dailyPlanResult.status === 'fulfilled') setDailyPlan(dailyPlanResult.value);
        if (competitorSignalResult.status === 'fulfilled') setCompetitorSignalPayload(competitorSignalResult.value);
        if (competitorSuggestionResult.status === 'fulfilled') setCompetitorSuggestionPayload(competitorSuggestionResult.value);
        if (recommendationResult.status === 'fulfilled') {
          setRecommendationBacklog(recommendationResult.value);
          setRecommendationBacklogRefreshedAt(new Date().toISOString());
        }
        const failures = [agentResult, marketResult, marketCardsResult, dailyPlanResult, competitorSignalResult, competitorSuggestionResult, recommendationResult].filter((result) => result.status === 'rejected');
        setError(failures.length ? '部分智能数据读取失败；页面仍展示已返回的只读结果。' : '');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => { alive = false; };
  }, [apiToken]);

  const latestBrief = useMemo(() => agents.find((item) => item.agent_id === 'brief'), [agents]);
  const latestRecommendation = useMemo(() => agents.find((item) => item.agent_id === 'recommendation'), [agents]);
  const latestEvidence = useMemo(() => agents.find((item) => item.agent_id === 'evidence'), [agents]);
  const latestSync = useMemo(() => agents.find((item) => item.agent_id === 'sync_sentinel'), [agents]);
  const marketSummary = useMemo(() => asRecord(market.summary), [market]);
  const marketCardSummary = useMemo(() => asRecord(marketCardsPayload.summary), [marketCardsPayload]);
  const dailyPlanSummary = useMemo(() => asRecord(dailyPlan.summary), [dailyPlan]);
  const dailyPlanGroups = useMemo(() => asList(dailyPlan.groups), [dailyPlan]);
  const dailyPlanCards = useMemo(() => dailyPlanGroups.map(cardFromDailyPlanGroup), [dailyPlanGroups]);
  const pendingCompetitorSignals = useMemo(() => asList(competitorSignalPayload.signals), [competitorSignalPayload]);
  const pendingCompetitorCards = useMemo(
    () => pendingCompetitorSignals.slice(0, 12).map((item, index) => cardFromMarketSignal(item, index, 'competitor')),
    [pendingCompetitorSignals],
  );
  const competitorReviewSuggestions = useMemo(() => asList(competitorSuggestionPayload.suggestions), [competitorSuggestionPayload]);
  const competitorReviewSuggestionMap = useMemo(() => {
    const next = new Map<string, Row>();
    competitorReviewSuggestions.forEach((suggestion) => {
      const signalId = text(suggestion.signal_id, '');
      const signalUid = text(suggestion.signal_uid, '');
      if (signalId) next.set(`id:${signalId}`, suggestion);
      if (signalUid) next.set(`uid:${signalUid}`, suggestion);
    });
    return next;
  }, [competitorReviewSuggestions]);
  const hotBrands = useMemo(() => asList(market.hot_brands).slice(0, 4), [market]);
  const launchCandidates = useMemo(() => asList(market.launch_candidates).slice(0, 4), [market]);
  const highPriority = useMemo(() => asList(market.high_priority).slice(0, 4), [market]);
  const marketApiCards = useMemo(() => asList(marketCardsPayload.cards).map(normalizeApiCard), [marketCardsPayload]);
  const recommendationItems = useMemo(() => asList(recommendationBacklog.items).slice(0, 8), [recommendationBacklog]);
  const recommendationSummary = useMemo(() => asRecord(recommendationBacklog.summary), [recommendationBacklog]);

  const cards = useMemo<IntelligenceCardModel[]>(() => {
    const realRecommendationCards = recommendationItems.map(cardFromRecommendationBacklog);
    const sourcePlanCards = externalIntelligenceSources.map(sourceCardFromPlan);
    const platformPlanCards = platformAdvicePlans.map(platformCardFromPlan);
    const baseCards = [
      ...localReviewCards,
      ...pendingCompetitorCards,
      ...marketApiCards,
      ...dailyPlanCards,
      ...realRecommendationCards,
      cardFromAgent(latestBrief, {
        id: 'brief-waiting',
        type: 'brief',
        title: '等待 Brief Agent 输出',
        summary: '当前先使用 runtime/ops 的只读 Agent 输出；外部趋势和 Gemini 视频分析还未在本页自动触发。',
        entityType: 'brief_agent',
      }),
      ...(realRecommendationCards.length ? [] : [cardFromAgent(latestRecommendation, {
        id: 'recommendation-waiting',
        type: 'recommendation',
        title: '等待 Recommendation Agent 输出',
        summary: '推荐卡会在后续进入 accept / reject / snooze 工作流。',
        entityType: 'recommendation_agent',
      })]),
      cardFromAgent(latestEvidence, {
        id: 'evidence-waiting',
        type: 'evidence',
        title: '等待 Evidence Agent 输出',
        summary: '每张 IntelligenceCard 后续都必须能打开 Evidence Drawer。',
        entityType: 'evidence_agent',
      }),
      cardFromAgent(latestSync, {
        id: 'sync-waiting',
        type: 'sync',
        title: '等待 Sync Sentinel 输出',
        summary: '同步、预算、open alerts 后续会进入系统卡。',
        entityType: 'sync_sentinel',
      }),
      ...sourcePlanCards,
      ...platformPlanCards,
      ...(marketApiCards.length ? [] : launchCandidates.map((item, index) => cardFromMarketSignal(item, index, 'market'))),
      ...(marketApiCards.length ? [] : highPriority.map((item, index) => cardFromMarketSignal(item, index, 'competitor'))),
    ];
    return baseCards.map((card) => ({ ...card, status: statusOverrides[card.id] || card.status }));
  }, [dailyPlanCards, highPriority, latestBrief, latestEvidence, latestRecommendation, latestSync, launchCandidates, localReviewCards, marketApiCards, pendingCompetitorCards, recommendationItems, statusOverrides]);
  const selected = useMemo(() => cards.find((card) => card.id === selectedId) || cards[0], [cards, selectedId]);

  useEffect(() => {
    const focusType = text(incomingFocus.focusType, '');
    if (!focusType || !cards.length) return;
    if (focusType !== 'recommendation_backlog') return;
    const targetRecommendationId = text(incomingFocus.recommendationId, '');
    const target = cards.find((card) => (
      card.type === 'recommendation'
      && (
        targetRecommendationId
          ? recommendationIdForCard(card) === targetRecommendationId
          : card.id.startsWith('recommendation-backlog-')
      )
    ));
    if (!target) return;
    setSelectedId(target.id);
    setRecommendationBacklogNotice('已从员工工作台定位到推荐待反馈；打开写库开关后可提交 accept / reject / snooze。');
    setIncomingFocus({});
  }, [cards, incomingFocus]);

  const selectedFeedbackDraft = selected ? feedbackDrafts[selected.id] : null;
  const selectedSubmitResult = selected ? feedbackSubmitResults[selected.id] : null;
  const selectedTaskDraft = selected ? taskDrafts[selected.id] : null;
  const selectedFeedbackSubmitting = Boolean(selected && feedbackSubmittingId === selected.id);
  const selectedCanWriteFeedback = Boolean(
    feedbackWriteEnabled
    && apiToken
    && selected
    && selected.type === 'recommendation'
    && recommendationIdForCard(selected, selectedFeedbackDraft),
  );
  const selectedFeedbackStateLabel = selectedFeedbackSubmitting
    ? '提交中'
    : selectedCanWriteFeedback
      ? '可写入推荐反馈'
      : feedbackWriteEnabled
        ? '等待 recommendation_id'
        : '本地 draft';
  const recommendationMissingCount = numberValue(recommendationSummary.missing_feedback_rows);
  const recommendationBacklogEmpty = Boolean(
    apiToken
    && recommendationBacklogRefreshedAt
    && !recommendationBacklogRefreshing
    && !recommendationItems.length
    && recommendationMissingCount === 0,
  );
  const marketCardsCount = numberValue(marketCardSummary.card_count || marketApiCards.length);
  const marketCardsRunId = text(marketCardSummary.source_run_id, '-');
  const marketCardsScope = marketCardSummary.source_scope_auto_resolved ? '自动定位最新真实 run' : '固定 run / 手动范围';
  const marketCardsLlmState = marketCardSummary.llm_card_included ? '已纳入可用 LLM 摘要' : '未纳入 LLM 摘要';
  const dailyPlanCalls = numberValue(dailyPlanSummary.planned_http_calls);
  const dailyPlanItems = numberValue(dailyPlanSummary.planned_item_limit);
  const pendingCompetitorCount = numberValue(competitorSignalPayload.count || pendingCompetitorSignals.length);
  const pendingCompetitorBrands = Array.from(new Set(
    pendingCompetitorSignals.map((item) => text(item.normalized_brand || item.brand, '')).filter(Boolean),
  )).slice(0, 3);
  const selectedRaw = selected ? asRecord(selected.metadata?.raw) : {};
  const selectedCompetitorSuggestion = selected?.type === 'competitor'
    ? competitorReviewSuggestionMap.get(`id:${text(selected.entityId || selectedRaw.id, '')}`)
      || competitorReviewSuggestionMap.get(`uid:${text(selected.metadata?.signal_uid || selectedRaw.signal_uid, '')}`)
    : null;
  const suggestionCounts = asRecord(competitorSuggestionPayload.suggested_actions);
  const updateCardStatus = useCallback((status: IntelligenceCardStatus, card: IntelligenceCardModel) => {
    const draft = buildIntelligenceFeedbackDraft(card, status, { pagePath: 'intelligenceCenter' });
    const candidateDecision = buildCandidateDecisionRecord(card, status);
    setStatusOverrides((current) => ({ ...current, [card.id]: status }));
    setFeedbackDrafts((current) => ({ ...current, [card.id]: draft }));
    setFeedbackSubmitResults((current) => {
      const next = { ...current };
      delete next[card.id];
      return next;
    });
    setTaskDrafts((current) => {
      const next = { ...current };
      if (status === 'accepted') {
        next[card.id] = buildIntelligenceTaskDraft(card, { pagePath: 'intelligenceCenter' });
      } else {
        delete next[card.id];
      }
      return next;
    });
    if (candidateDecision) {
      writeCandidateDecision(candidateDecision);
      setRecommendationBacklogNotice(
        status === 'accepted'
          ? 'Discover 候选已接受；已生成本地候选执行记录，可从任务 Draft 进入项目或红人搜索继续处理。'
          : status === 'snoozed'
            ? 'Discover 候选已延后；本地保留复查记录，回到红人搜索可继续查看。'
            : 'Discover 候选已拒绝；本地保留拒绝记录，后续避免重复推进。',
      );
    }
    if (!feedbackWriteEnabled || !apiToken || card.type !== 'recommendation' || !recommendationIdForCard(card, draft)) {
      return;
    }
    setFeedbackSubmittingId(card.id);
    submitRecommendationFeedbackDraft(apiToken, card, draft)
      .then((result) => {
        setFeedbackSubmitResults((current) => ({ ...current, [card.id]: result }));
        if (result.status === 'submitted') {
          void reloadRecommendationBacklog('feedback_submit');
        } else {
          setRecommendationBacklogNotice(result.message);
        }
      })
      .finally(() => setFeedbackSubmittingId((current) => (current === card.id ? '' : current)));
  }, [apiToken, feedbackWriteEnabled, reloadRecommendationBacklog]);
  const handleAction = useCallback((action: IntelligenceAction, card: IntelligenceCardModel) => {
    if (action.kind === 'disabled') return;
    if (action.label.includes('证据')) {
      setEvidenceDrawer(card);
      return;
    }
    if (action.label.includes('发现红人')) {
      const plan = platformPlanForCard(card);
      if (plan) {
        writePlatformDiscoverFocus(plan);
        setRecommendationBacklogNotice(`${plan.platform} 发现任务已带入红人搜索；只填入查询和平台，不触发外部抓取。`);
        if (typeof window !== 'undefined') window.location.hash = 'discover';
        return;
      }
    }
    if (action.label.includes('接入前检查')) {
      const source = sourceForCard(card);
      const platformPlan = platformPlanForCard(card);
      if (source) {
        writeSourceRepairFocus(source);
        setRecommendationBacklogNotice(`${source.title} 已生成接入前检查；修复中心只做本地 dry-run。`);
        if (typeof window !== 'undefined') window.location.hash = 'repairCenter';
        return;
      }
      if (platformPlan) {
        writePlatformRepairFocus(platformPlan);
        setRecommendationBacklogNotice(`${platformPlan.platform} 平台建议已生成接入前检查；修复中心只做本地 dry-run。`);
        if (typeof window !== 'undefined') window.location.hash = 'repairCenter';
        return;
      }
    }
    setSelectedId(card.id);
  }, []);

  return (
    <div className="vkpi-agents-shell">
      <section className="vkpi-agents-hero">
        <div>
          <span>V2 Intelligence Layer</span>
          <h1>智能中心</h1>
          <p>优先展示已落库、带来源、通过质量闸门的市场情报卡；接入规划降级为底部参考。</p>
        </div>
        <div className="vkpi-agents-hero__meta">
          <b>{marketApiCards.length || cards.length}</b>
          <small>{loading ? '读取中' : '真实情报卡'}</small>
        </div>
      </section>

      {error ? <div className="vkpi-inline-message is-warning">{error}</div> : null}

      <section className="vkpi-intelligence-runtime-strip" aria-label="真实情报运行摘要">
        <span><b>{marketCardsRunId}</b><small>market run</small></span>
        <span><b>{marketCardsCount}</b><small>真实卡片</small></span>
        <span><b>{pendingCompetitorCount}</b><small>待确认竞品预警</small></span>
        <span><b>{dailyPlanCalls ? `${dailyPlanCalls} / ${dailyPlanItems}` : '-'}</b><small>今日外部计划</small></span>
        <span><b>{marketCardsScope}</b><small>{marketCardsLlmState}</small></span>
      </section>

      {pendingCompetitorCount ? (
        <div className="vkpi-inline-message">
          已接入 {pendingCompetitorCount} 条待确认竞品预警
          {pendingCompetitorBrands.length ? `：${pendingCompetitorBrands.join(' / ')}` : ''}
          。这些卡片只代表候选信号，确认前不作为最终结论。
        </div>
      ) : null}

      {dailyPlanGroups.length ? <DailyPlanPanel plan={dailyPlan} /> : null}

      <label className="vkpi-intelligence-write-toggle">
        <span>
          推荐反馈写库开关
          <small>
            默认关闭；仅 recommendation 卡且存在 recommendation_id 时写入已有推荐反馈 API。
            {recommendationBacklogRefreshedAt ? ` 最近刷新 ${formatTime(recommendationBacklogRefreshedAt)}` : ''}
          </small>
        </span>
        <div className="vkpi-intelligence-write-toggle__controls">
          <button
            className="vkpi-button"
            disabled={!apiToken || recommendationBacklogRefreshing}
            type="button"
            onClick={() => { void reloadRecommendationBacklog('manual'); }}
          >
            {recommendationBacklogRefreshing ? '刷新中' : '刷新 backlog'}
          </button>
          <input
            checked={feedbackWriteEnabled}
            type="checkbox"
            onChange={(event) => setFeedbackWriteEnabled(event.target.checked)}
          />
        </div>
      </label>

      {recommendationBacklogNotice ? <div className="vkpi-inline-message">{recommendationBacklogNotice}</div> : null}
      {recommendationBacklogEmpty ? <div className="vkpi-inline-message">当前 recommendation backlog 已清空；员工侧会显示“推荐反馈已清”。</div> : null}

      <section className="vkpi-agents-grid">
        <div className="vkpi-agents-list">
          {cards.map((card) => (
            <IntelligenceCard
              card={card}
              key={card.id}
              selected={selected?.id === card.id}
              onSelect={(next) => setSelectedId(next.id)}
            />
          ))}
        </div>

        <IntelligenceDetailPanel
          card={selected}
          emptyTitle="选择一张智能卡"
          emptySummary="从左侧选择 Brief、推荐、证据、市场或竞品卡片。"
          summarySlot={(
            <div className="vkpi-agent-summary-grid">
              {hotBrands.map((brand) => (
                <span key={text(brand.brand)}><b>{text(brand.brand)}</b>{numberText(brand.count)} signals</span>
              ))}
              <span><b>竞品待确认</b>{numberText(pendingCompetitorCount)} 条</span>
              <span><b>审核建议</b>{numberText(suggestionCounts.ready)} ready / {numberText(suggestionCounts.ignored)} ignore</span>
              <span><b>推荐 backlog</b>{numberText(recommendationSummary.missing_feedback_rows)} 待反馈</span>
              <span><b>feedback</b>{selectedFeedbackStateLabel}</span>
              <span><b>backlog</b>{recommendationBacklogRefreshing ? '刷新中' : recommendationBacklogRefreshedAt ? formatTime(recommendationBacklogRefreshedAt) : '未读取'}</span>
              <span><b>task</b>{selectedTaskDraft ? '草稿已生成' : '接受后生成'}</span>
            </div>
          )}
          extraSlot={(
            <>
              <IntelligenceActionStrip
                card={selected}
                onStatusChange={updateCardStatus}
                status={selectedFeedbackSubmitting ? selected?.status : undefined}
                statusHint={selectedFeedbackStateLabel}
                submitting={selectedFeedbackSubmitting}
              />
              <IntelligenceFeedbackDraftPanel
                draft={selectedFeedbackDraft}
                submitResult={selectedSubmitResult}
                submitting={selectedFeedbackSubmitting}
                writeEnabled={selectedCanWriteFeedback}
              />
              {selected?.type === 'competitor' ? (
                <CompetitorReviewSuggestionPanel suggestion={selectedCompetitorSuggestion} />
              ) : null}
              <IntelligenceTaskDraftPanel draft={selectedTaskDraft} viewMode={viewMode} />
            </>
          )}
          footerNote="本页只读展示现有 Agent 和 Market v0 输出；外部搜索、Gemini 视频分析、任务生成会在后续阶段按预算和审计接入。"
          onAction={handleAction}
        />
      </section>

      <section className="vkpi-intelligence-planning-section" aria-label="后续智能层接入规划">
        <div className="vkpi-intelligence-planning-section__head">
          <span>后续接入规划</span>
          <p>这些是趋势源、视频理解和平台建议的接入路线，不代表已经触发外部抓取或批量 LLM。</p>
        </div>
        <IntelligenceSourceBoard />
        <PlatformAdviceMatrix />
      </section>

      {evidenceDrawer ? (
        <IntelligenceEvidenceDrawer
          card={evidenceDrawer}
          onClose={() => setEvidenceDrawer(null)}
        />
      ) : null}
    </div>
  );
}

export default IntelligenceCenterPage;
