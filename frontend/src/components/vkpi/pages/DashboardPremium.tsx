import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  GlassSidebar,
  GlassToast,
  GlassTopBar,
  HeroSection,
  glassVarStyle,
} from '../glass';
import { apiFetch } from '../../../services/http';
import type { VkpiPageKey } from '../vkpiTypes';
import { IntelligenceActionStrip } from '../intelligence/IntelligenceActionStrip';
import {
  IntelligenceCard,
  type IntelligenceAction,
  type IntelligenceCardModel,
} from '../intelligence/IntelligenceCard';
import { IntelligenceDetailPanel } from '../intelligence/IntelligenceDetailPanel';
import { IntelligenceEvidenceDrawer } from '../intelligence/IntelligenceEvidenceDrawer';
import { RealWorldMap, type CountryMapPoint } from '../glass-future/RealWorldMap';
import { AccountTypeTabs } from '../dashboard/AccountTypeTabs';
import { getOfficialChannelMatrix } from '../../../domains/channels';
import {
  EMPTY_SNAPSHOT,
  buildDashboardAccountKpiCards,
  accountDisplayName,
  buildKpis,
  buildPlatforms,
  buildRegions,
  buildTrend,
  compact,
  contentTitle,
  contentUrl,
  countryFlag,
  dateLabel,
  intelligenceTabs,
  latestContent,
  localDateISO,
  mergeDashboardOfficialSummary,
  num,
  officialTotals,
  platformName,
  record,
  rows,
  dashboardAccountTypeOptions,
  settledValue,
  toneStyle,
  trendSegments,
  type DashboardAccountKpi,
  type DashboardAccountList,
  type DashboardAccountType,
  type IntelligenceTab,
  type KpiCard,
  type PanelDrawer,
  type Row,
  type Snapshot,
  type TrendSegment,
} from '../../../domains/dashboard';
import { fetchDashboardAccounts, fetchDashboardAccountKpi } from '../../../services/vkpi/dashboard-api';
import { getRecommendationFeedbackBacklog } from '../../../domains/intelligence';
import { getKolPoolCompetitorDashboard, getKolPoolSummary } from '../../../domains/kol';
import { listBrandSignals } from '../../../domains/market';
import '../glass-future/tokens.css';
import '../glass-future/background.css';
import '../glass-future/components.css';
import '../glass-future/animations.css';
import '../glass-future/responsive.css';

export interface DashboardPremiumProps {
  apiToken?: string;
  userName?: string;
  userRole?: string;
  testId?: string;
  windowDays?: number;
  embedded?: boolean;
  onSelectPage?: (page: VkpiPageKey) => void;
}

async function fetchSnapshot(apiToken: string, windowDays: number): Promise<Snapshot> {
  const failedSections: string[] = [];
  const [
    dashboardResult,
    trendResult,
    kolSummaryResult,
    kolDistributionResult,
    officialMatrixResult,
    competitorResult,
    brandSignalsResult,
    recentContentResult,
    agentsStatusResult,
    copilotBriefResult,
    tasksStatusResult,
    recommendationBacklogResult,
  ] = await Promise.allSettled([
    apiFetch<Row>(`/api/admin/vkpi/dashboard?window_days=${windowDays}`, {}, apiToken),
    apiFetch<{ rows?: Row[] }>(`/api/admin/vkpi/dashboard/revenue-trend?window_days=7`, {}, apiToken),
    getKolPoolSummary(apiToken),
    apiFetch<Row>('/api/admin/vkpi/dashboard/kol-distribution?limit=200', {}, apiToken),
    getOfficialChannelMatrix(apiToken, { limit: 20 }),
    getKolPoolCompetitorDashboard(apiToken),
    listBrandSignals(apiToken, { status: 'new', limit: 10 }),
    apiFetch<{ items?: Row[] }>('/api/admin/vkpi/dashboard/recent-content?limit=12', {}, apiToken),
    apiFetch<Row>('/api/admin/vkpi/dashboard/agents-status', {}, apiToken),
    apiFetch<Row>('/api/admin/vkpi/dashboard/copilot-brief', {}, apiToken),
    apiFetch<Row>('/api/admin/vkpi/dashboard/tasks?limit=6', {}, apiToken),
    getRecommendationFeedbackBacklog(apiToken, 12),
  ]);
  const trend = settledValue(trendResult, { rows: [] }, failedSections, 'revenue-trend');
  const brandSignals = settledValue(brandSignalsResult, { signals: [] }, failedSections, 'brand-signals');
  const recentContent = settledValue(recentContentResult, { items: [] }, failedSections, 'recent-content');
  const dashboard = settledValue(dashboardResult, {}, failedSections, 'dashboard');
  return {
    source: failedSections.length ? 'partial' : 'real',
    failedSections,
    dashboard,
    trendRows: rows(trend.rows),
    kolSummary: settledValue(kolSummaryResult, {}, failedSections, 'kol-pool-summary'),
    kolDistribution: settledValue(kolDistributionResult, {}, failedSections, 'kol-distribution'),
    officialMatrix: mergeDashboardOfficialSummary(settledValue(officialMatrixResult, {}, failedSections, 'official-channel-matrix'), dashboard),
    competitorDashboard: settledValue(competitorResult, {}, failedSections, 'competitors-dashboard'),
    brandSignals: rows(brandSignals.signals),
    recentContent: rows(recentContent.items),
    agentsStatus: settledValue(agentsStatusResult, {}, failedSections, 'agents-status'),
    copilotBrief: settledValue(copilotBriefResult, {}, failedSections, 'copilot-brief'),
    tasksStatus: settledValue(tasksStatusResult, {}, failedSections, 'tasks'),
    recommendationBacklog: settledValue(recommendationBacklogResult, {}, failedSections, 'recommendation-feedback-backlog'),
    loadedAt: new Date().toISOString(),
  };
}

function buildCards(snapshot: Snapshot, kpis: KpiCard[], taskCount: number): IntelligenceCardModel[] {
  const backlogSummary = record(snapshot.recommendationBacklog.summary);
  const missingFeedback = num(backlogSummary.missing_feedback_rows);
  const competitorTiers = record(snapshot.competitorDashboard.tier_counts);
  const riskCount = num(competitorTiers.avoid) + num(competitorTiers.caution);
  const agentRows = rows(snapshot.agentsStatus.agents);
  const activeAgents = agentRows.filter((agent) => String(agent.status) === 'active').length;
  const signalCount = snapshot.brandSignals.length;
  const failedCount = snapshot.failedSections.length;
  const actionCount = (missingFeedback ? 1 : 0) + taskCount + riskCount + failedCount;
  return [
    {
      id: 'dashboard-summary',
      type: 'brief',
      priority: actionCount || riskCount ? 'high' : 'medium',
      status: 'open',
      title: `今日待处理 ${compact(actionCount)} 项`,
      summary: `推荐待反馈 ${compact(missingFeedback)} · 风险 ${compact(riskCount)} · Agent ${activeAgents}/${agentRows.length || 7}`,
      entityType: 'mission_control_summary',
      confidence: snapshot.source === 'real' ? 0.9 : snapshot.source === 'partial' ? 0.62 : 0.25,
      freshnessLabel: dateLabel(snapshot.loadedAt),
      sourceLabel: 'dashboard summary',
      evidence: [
        { label: '推荐待反馈', source: 'recommendation-feedback-backlog', value: `${compact(missingFeedback)} missing` },
        { label: '竞品风险', source: 'competitor-dashboard', value: `${compact(riskCount)} avoid/caution` },
        { label: '任务候选', source: 'dashboard/tasks', value: `${compact(taskCount)} tasks` },
        { label: '失败 API', source: 'premium snapshot', value: failedCount ? snapshot.failedSections.join(' / ') : '无' },
      ],
      actions: [{ label: '查看证据', kind: 'primary' }, { label: '进入智能中心', kind: 'secondary' }],
    },
    {
      id: 'dashboard-market',
      type: 'market',
      priority: signalCount ? 'medium' : 'low',
      status: 'open',
      title: signalCount ? `${compact(signalCount)} 条市场/品牌信号` : '市场信号待积累',
      summary: 'Google / Reddit / Trends 后续按预算和证据链小口接入；当前只展示既有站内信号。',
      entityType: 'market_signal',
      confidence: signalCount ? 0.72 : 0.25,
      freshnessLabel: dateLabel(snapshot.loadedAt),
      sourceLabel: 'brand-signals',
      evidence: snapshot.brandSignals.slice(0, 5).map((row, index) => ({ label: `信号 ${index + 1}`, source: String(row.platform || 'brand_signal'), value: String(row.title || row.match_context || row.reason || '-').slice(0, 160) })),
      actions: [{ label: '查看证据', kind: 'primary' }, { label: '进入数据分析', kind: 'secondary' }],
    },
    {
      id: 'dashboard-system',
      type: 'system',
      priority: failedCount ? 'high' : 'medium',
      status: failedCount ? 'blocked' : 'open',
      title: failedCount ? '系统状态需复核' : '系统状态正常',
      summary: `${activeAgents}/${agentRows.length || 7} Agent 在线 · ${snapshot.source === 'real' ? '真实 API' : snapshot.source}`,
      entityType: 'system_health',
      confidence: failedCount ? 0.45 : 0.84,
      freshnessLabel: dateLabel(snapshot.loadedAt),
      sourceLabel: 'premium dashboard health',
      evidence: [
        { label: 'KPI', source: 'cards', value: kpis.map((item) => `${item.label}:${item.status}`).join(' / ') },
        { label: '失败 API', source: 'premium snapshot', value: failedCount ? snapshot.failedSections.join(' / ') : '无' },
      ],
      actions: [{ label: '查看证据', kind: 'primary' }, { label: '打开 Agent Inbox', kind: 'secondary' }],
    },
  ];
}

function DashboardKpiCard({ item, onClick }: { item: KpiCard; onClick: () => void }) {
  const style = toneStyle[item.tone];
  return (
    <button className={`glass-card kpi${item.pending ? ' is-pending' : ' is-real'}`} style={glassVarStyle({ '--ig': style.icon, '--ic': style.text })} type="button" onClick={onClick}>
      <div className="topline"><div className="icon">{item.icon}</div><span className={`tag ${item.pending ? '' : 'tag-real'}`}>{item.status}</span></div>
      <div className="label">{item.label}</div>
      <div className="value">{item.value}</div>
      <div className="meta up">{item.meta}</div>
      <svg className="spark" viewBox="0 0 120 28"><path d="M2 18 C18 14 27 18 40 13 S60 11 74 16 94 20 118 12" fill="none" stroke={style.text} strokeWidth="3" strokeLinecap="round" /></svg>
    </button>
  );
}

export function DashboardPremium({ apiToken, userName = 'Jianbo', userRole = 'Marketing Director', testId = 'vkpi-dashboard-premium', windowDays = 30, embedded = false, onSelectPage }: DashboardPremiumProps) {
  const [snapshot, setSnapshot] = useState<Snapshot>(EMPTY_SNAPSHOT);
  const [accountType, setAccountType] = useState<DashboardAccountType>('all');
  const [accountKpi, setAccountKpi] = useState<DashboardAccountKpi | null>(null);
  const [accountList, setAccountList] = useState<DashboardAccountList | null>(null);
  const [accountKpiLoading, setAccountKpiLoading] = useState(false);
  const [accountListLoading, setAccountListLoading] = useState(false);
  const [accountKpiError, setAccountKpiError] = useState('');
  const [accountListError, setAccountListError] = useState('');
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState('');
  const [trendSegment, setTrendSegment] = useState<TrendSegment>('曝光量');
  const [tab, setTab] = useState<IntelligenceTab>('today');
  const [panel, setPanel] = useState<PanelDrawer | null>(null);
  const [selectedCard, setSelectedCard] = useState<IntelligenceCardModel | null>(null);
  const [evidenceCard, setEvidenceCard] = useState<IntelligenceCardModel | null>(null);

  useEffect(() => {
    if (!apiToken) {
      setSnapshot(EMPTY_SNAPSHOT);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    fetchSnapshot(apiToken, windowDays)
      .then((next) => { if (!cancelled) setSnapshot(next); })
      .catch(() => { if (!cancelled) setSnapshot({ ...EMPTY_SNAPSHOT, source: 'partial', failedSections: ['premium-dashboard'] }); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [apiToken, windowDays]);

  useEffect(() => {
    if (!apiToken) {
      setAccountKpi(null);
      setAccountList(null);
      setAccountKpiError('');
      setAccountListError('');
      return undefined;
    }
    let cancelled = false;
    setAccountKpiLoading(true);
    setAccountListLoading(true);
    setAccountKpiError('');
    setAccountListError('');
    void fetchDashboardAccountKpi(apiToken, accountType)
      .then((payload) => { if (!cancelled) setAccountKpi(payload); })
      .catch((error) => {
        if (!cancelled) {
          setAccountKpi(null);
          setAccountKpiError(error instanceof Error ? error.message : 'Dashboard KOL API 加载失败');
        }
      })
      .finally(() => { if (!cancelled) setAccountKpiLoading(false); });
    void fetchDashboardAccounts(apiToken, accountType, { pageSize: 6 })
      .then((payload) => { if (!cancelled) setAccountList(payload); })
      .catch((error) => {
        if (!cancelled) {
          setAccountList(null);
          setAccountListError(error instanceof Error ? error.message : '账号列表 API 加载失败');
        }
      })
      .finally(() => { if (!cancelled) setAccountListLoading(false); });
    return () => { cancelled = true; };
  }, [accountType, apiToken]);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(''), 1600);
  }, []);
  const goto = useCallback((page: VkpiPageKey, label: string) => {
    if (onSelectPage) onSelectPage(page);
    else showToast(`${label} · 暂无可用路由`);
  }, [onSelectPage, showToast]);

  const fallbackKpis = useMemo(() => buildKpis(snapshot, 'official'), [snapshot]);
  const kpis = useMemo(() => {
    if (accountKpi) return buildDashboardAccountKpiCards(accountKpi);
    return apiToken ? buildDashboardAccountKpiCards(null) : fallbackKpis;
  }, [accountKpi, apiToken, fallbackKpis]);
  const trend = useMemo(() => buildTrend(snapshot.trendRows, trendSegment), [snapshot.trendRows, trendSegment]);
  const regions = useMemo(() => buildRegions(snapshot), [snapshot]);
  const mapPoints = useMemo<CountryMapPoint[]>(() => regions.filter((row) => typeof row.lat === 'number' && typeof row.lng === 'number').map((row) => ({
    code: row.code,
    name: row.label,
    count: row.count,
    lat: Number(row.lat),
    lng: Number(row.lng),
    color: '#1b6cff',
  })), [regions]);
  const platforms = useMemo(() => buildPlatforms(snapshot), [snapshot]);
  const contentRows = useMemo(() => latestContent(snapshot), [snapshot]);
  const taskRows = rows(snapshot.tasksStatus.tasks);
  const cards = useMemo(() => buildCards(snapshot, kpis, taskRows.length), [kpis, snapshot, taskRows.length]);
  const official = useMemo(() => officialTotals(snapshot.officialMatrix), [snapshot.officialMatrix]);
  const accountOption = dashboardAccountTypeOptions.find((option) => option.key === accountType) || dashboardAccountTypeOptions[0];
  const syncLabel = loading ? '加载真实 API…' : snapshot.failedSections.length ? `部分真实 · ${snapshot.failedSections.length} 项失败` : snapshot.source === 'real' ? '真实 API 已接入' : '等待真实 API';
  const topRegion = regions[0];
  const topPlatform = platforms[0];
  const competitorTiers = record(snapshot.competitorDashboard.tier_counts);
  const riskCount = num(competitorTiers.avoid) + num(competitorTiers.caution);
  const backlogSummary = record(snapshot.recommendationBacklog.summary);
  const missingFeedback = num(backlogSummary.missing_feedback_rows);
  const agentRows = rows(snapshot.agentsStatus.agents);
  const activeAgentCount = agentRows.filter((row) => String(row.status) === 'active').length;
  const intelligenceText = {
    today: {
      title: missingFeedback ? `${compact(missingFeedback)} 条推荐反馈待补齐` : '智能层已就绪，等待可执行候选',
      summary: `${riskCount} 个竞品风险 · ${activeAgentCount}/${agentRows.length || 7} Agent 在线`,
      points: ['先处理推荐反馈和项目阻塞', '红人池只做发现底座，不回到全量每日刷新', '无证据不生成建议'],
    },
    platform: {
      title: '平台建议先基于内容表现和红人分布',
      summary: topPlatform ? `${topPlatform.label} 当前样本 ${compact(topPlatform.value)}` : '平台建议等待真实平台分布',
      points: ['TikTok / Reels 看短视频题材验证', 'YouTube 看评测和对比沉淀', 'Reddit / Google 先做趋势监听'],
    },
    market: {
      title: '市场雷达 v0 使用站内品牌/竞品信号',
      summary: `${snapshot.brandSignals.length} 条品牌/竞品信号`,
      points: ['外部搜索后续按预算小口启用', 'Market Radar 输出进入机会卡', '不自动生成无来源事实'],
    },
    competitor: {
      title: '竞品预警聚合 avoid / caution / safe',
      summary: `avoid ${num(competitorTiers.avoid)} · caution ${num(competitorTiers.caution)} · safe ${num(competitorTiers.safe)}`,
      points: ['竞品关系进入候选过滤', '视频竞品理解后续小批接入', '确认前不改推荐排序'],
    },
  }[tab];
  const summaryCards = [
    { id: 'actions', title: '今日关键动作', value: compact((missingFeedback ? 1 : 0) + taskRows.length + riskCount), subtitle: '需要处理', tone: 'warn', width: '46%', page: 'intelligenceCenter' as VkpiPageKey },
    { id: 'progress', title: '目标进度', value: snapshot.source === 'real' ? '85%' : '42%', subtitle: snapshot.source === 'real' ? '真实 API' : '等待更多数据', tone: 'good', width: snapshot.source === 'real' ? '85%' : '42%', page: 'intelligenceCenter' as VkpiPageKey },
    { id: 'risk', title: '风险 / 阻塞', value: compact(riskCount + snapshot.failedSections.length), subtitle: riskCount ? '需复核' : '低风险', tone: riskCount ? 'danger' : 'good', width: riskCount ? '70%' : '20%', page: 'dataQuality' as VkpiPageKey },
    { id: 'system', title: '智能/系统状态', value: `${activeAgentCount}/${agentRows.length || 7}`, subtitle: 'Agent 在线', tone: 'good', width: agentRows.length ? `${Math.round((activeAgentCount / agentRows.length) * 100)}%` : '60%', page: 'agents' as VkpiPageKey },
  ];

  const openKpiPanel = (item: KpiCard) => setPanel({
    title: item.label,
    sourceLabel: item.status,
    rows: [
      { label: '当前值', value: item.value },
      { label: '状态', value: item.meta },
      { label: '范围', value: accountOption.label },
      { label: '更新时间', value: dateLabel(snapshot.loadedAt) },
      { label: '接口状态', value: accountKpiError || (accountKpiLoading ? '加载中' : '正常') },
      { label: '账号样本', value: accountListError || (accountListLoading ? '加载中' : `${accountList?.kols.length || 0}/${accountList?.total || 0}`) },
    ],
    actionLabel: item.pending ? '查看接入状态' : '查看数据质量',
    actionPage: item.pending ? 'settings' : 'dataQuality',
  });

  const handleCardAction = (action: IntelligenceAction, card: IntelligenceCardModel) => {
    if (action.kind === 'disabled') return;
    if (action.label.includes('证据')) setEvidenceCard(card);
    else if (action.label.includes('智能中心')) goto('intelligenceCenter', '智能中心');
    else if (action.label.includes('Agent')) goto('agents', 'Agent Inbox');
    else if (action.label.includes('数据分析')) goto('dataAnalysis', '数据分析');
  };

  const dashboardContent = (
    <>
      {!embedded ? (
        <GlassTopBar
          actions={[
            { label: localDateISO(), onClick: () => setPanel({ title: '数据窗口', sourceLabel: `近 ${windowDays} 天`, rows: [{ label: '窗口', value: `近 ${windowDays} 天` }, { label: '更新时间', value: dateLabel(snapshot.loadedAt) }], actionLabel: '进入数据质量', actionPage: 'dataQuality' }) },
            { label: syncLabel, variant: 'sync', onClick: () => setPanel({ title: 'Dashboard 数据状态', sourceLabel: snapshot.source, rows: [{ label: '失败 API', value: snapshot.failedSections.length ? snapshot.failedSections.join(' / ') : '无' }, { label: '官方账号', value: `${official.accountCount || 0}` }, { label: '内容', value: `${official.posts || 0}` }], actionLabel: '进入数据质量', actionPage: 'dataQuality' }) },
            { label: '导出', onClick: () => goto('reports', '报表导出') },
            { label: '生成周报', variant: 'primary', onClick: () => goto('reports', '生成周报') },
          ]}
        />
      ) : null}

      <HeroSection
        title="全球影响情报中枢"
        body="关键结果 / 风险 / 任务"
        missions={[]}
        control={(
          <AccountTypeTabs
            value={accountType}
            counts={accountKpi?.counts}
            loading={accountKpiLoading}
            onChange={setAccountType}
          />
        )}
        summary={(
          <div className="impact-summary-grid" aria-label="关键结果摘要">
            {summaryCards.map((card) => (
              <button className={`impact-summary-card is-${card.tone}`} key={card.id} type="button" onClick={() => goto(card.page, card.title)}>
                <span>{card.title}</span><strong>{card.value}</strong><em>{card.subtitle}</em><i style={glassVarStyle({ '--w': card.width })}></i>
              </button>
            ))}
          </div>
        )}
        brief={{
          label: '智能层 · 只读',
          title: intelligenceText.title,
          body: (
            <div className="intelligence-layer">
              <div className="intelligence-tabs" role="tablist" aria-label="Intelligence Layer tabs">
                {intelligenceTabs.map((item) => <button className={tab === item.key ? 'is-active' : ''} key={item.key} type="button" onClick={() => setTab(item.key)}>{item.label}</button>)}
              </div>
              <p>{intelligenceText.summary}</p>
              <ul>{intelligenceText.points.map((point) => <li key={point}>{point}</li>)}</ul>
              <div className="intelligence-layer-metrics">
                {cards.map((card) => <button key={card.id} type="button" onClick={() => setSelectedCard(card)}><b>{card.title.split(' ')[0]}</b><small>{card.entityType}</small></button>)}
              </div>
              <span>不触发 LLM / provider，只展示既有信号。</span>
            </div>
          ),
          primaryAction: '查看证据',
          secondaryAction: '进入智能中心',
          onPrimaryAction: () => setEvidenceCard(cards[0]),
          onSecondaryAction: () => goto('intelligenceCenter', '智能中心'),
        }}
      />

      <section className="kpis" aria-label={accountOption.detail}>
        {kpis.map((item) => <DashboardKpiCard item={item} key={item.label} onClick={() => openKpiPanel(item)} />)}
      </section>

      <section className="dashboard-account-preview glass-card" aria-label="当前口径账号样本">
        <div className="panel-head">
          <div>
            <h3>{accountOption.label} 账号样本</h3>
            <p>{accountListLoading ? '正在读取账号列表...' : accountListError || `${accountList?.total || 0} 个账号 · 来自 /api/admin/dashboard/kols`}</p>
          </div>
          <button className="link" type="button" onClick={() => goto('kolPoolV2', 'KOL Pool')}>进入 KOL Pool</button>
        </div>
        <div className="dashboard-account-preview-list">
          {accountListLoading ? Array.from({ length: 6 }).map((_, index) => (
            <span className="dashboard-account-preview-item is-loading" key={index}>
              <i></i><b>读取中</b><em>真实账号列表</em>
            </span>
          )) : accountListError ? (
            <div className="empty-real">{accountListError}</div>
          ) : accountList?.kols.length ? accountList.kols.map((row) => (
            <a className="dashboard-account-preview-item" href={row.profile_url || '#'} target="_blank" rel="noreferrer" key={row.id} onClick={(event) => { if (!row.profile_url) event.preventDefault(); }}>
              {row.avatar_url ? <img src={row.avatar_url} alt="" /> : <i>{accountDisplayName(row).slice(0, 1).toUpperCase()}</i>}
              <b>{accountDisplayName(row)}</b>
              <em>{row.platform || 'unknown'} · {compact(row.total_views || row.followers || row.posts_count)}</em>
            </a>
          )) : (
            <div className="empty-real">当前口径没有账号样本。</div>
          )}
        </div>
      </section>

      <div className="content-grid">
        <div>
          <div className="left-grid">
            <div className="glass-card panel geo-map-card">
              <div className="geo-map-head">
                <div><h3>全球 KOL 分布</h3><p>{regions.length ? `${regions.length} 国家 · ${compact(regions.reduce((sum, row) => sum + row.count, 0))} KOL` : '国家分布待接入'}</p></div>
              </div>
              <div className="geo-map-layout">
                <div className="geo-map-stage"><div className="holo-map"><RealWorldMap points={mapPoints} /><div className="map-hint">KOL 国家分布</div></div></div>
                <div className="geo-top">
                  <span>TOP 国家 · KOL 数</span>
                  <div className="geo-top-list">
                    {regions.slice(0, 5).map((row) => <button className="geo-country-card" key={row.code} type="button" onClick={() => setPanel({ title: row.label, sourceLabel: 'KOL 国家分布', rows: [{ label: '国家', value: `${countryFlag(row.code)} ${row.label}` }, { label: 'KOL', value: compact(row.count) }, { label: '占比', value: `${row.share.toFixed(1)}%` }], actionLabel: '进入红人搜索', actionPage: 'discover' })}><b><span>{countryFlag(row.code)}</span>{row.label}</b><strong>{compact(row.count)} · {row.share.toFixed(1)}%</strong><em>点击查看国家详情</em></button>)}
                  </div>
                </div>
              </div>
            </div>
            <div className="glass-card panel">
              <div className="panel-head"><h3>{trendSegment}趋势（近 7 天）</h3><div className="segment">{trendSegments.map((segment) => <button className={trendSegment === segment ? 'active' : ''} type="button" key={segment} onClick={() => setTrendSegment(segment)}>{segment}</button>)}</div></div>
              <div className="linechart"><svg viewBox="0 0 520 250" preserveAspectRatio="none"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#1b6cff" /><stop offset="1" stopColor="#1b6cff" stopOpacity="0" /></linearGradient></defs><g stroke="rgba(92,130,190,.16)"><line x1="26" y1="30" x2="500" y2="30" /><line x1="26" y1="80" x2="500" y2="80" /><line x1="26" y1="130" x2="500" y2="130" /><line x1="26" y1="180" x2="500" y2="180" /></g><path d={trend.path} fill="none" stroke="#1b6cff" strokeWidth="4" strokeLinecap="round" /><path d={trend.areaPath} fill="url(#area)" opacity=".25" /><circle cx={trend.pointX} cy={trend.pointY} r="8" fill="#1b6cff" stroke="#fff" strokeWidth="4" /></svg><div className="float-tip">{trend.tipDate}<b>{trend.tipValue}</b></div></div>
            </div>
            <div className="lower">
              <div className="glass-card mini"><div className="panel-head"><h3>平台分布</h3><button className="link" type="button" onClick={() => goto('channels', '平台分布')}>查看全部</button></div>{platforms.length ? platforms.map((row) => <div className="platform" key={row.label}><span className="picon">{row.label.slice(0, 2)}</span><div><b>{row.label}</b><div className="bar"><span style={glassVarStyle({ '--w': row.width })}></span></div></div><small>{compact(row.value)} {row.meta}</small></div>) : <div className="empty-real">暂无平台分布</div>}</div>
              <div className="glass-card mini"><div className="panel-head"><h3>内容状态</h3><span className="tag">官方矩阵</span></div><div className="donut-wrap"><div className="donut"></div><div className="donut-label"><span>总内容</span><b>{official.posts ? compact(official.posts) : '--'}</b></div></div></div>
              <div className="glass-card mini"><div className="panel-head"><h3>推荐学习</h3><button className="link" type="button" onClick={() => goto('intelligenceCenter', '智能中心')}>处理</button></div><div className="task"><div className="task-head"><b>推荐反馈</b><span className="priority mid">中</span></div><p>{missingFeedback ? `${compact(missingFeedback)} 条待补 accept / reject / snooze` : '推荐反馈暂无积压'}</p><div className="progress"><span style={glassVarStyle({ '--w': missingFeedback ? '68%' : '12%' })}></span></div></div></div>
            </div>
          </div>
          <div className="glass-card latest latest-redesign">
            <div className="panel-head latest-head"><h3>最新内容表现</h3><button className="link" type="button" onClick={() => goto('channels', '内容中心')}>进入内容中心 →</button></div>
            <div className="latest-content-list">
              {contentRows.length ? contentRows.map((row, index) => {
                const url = contentUrl(row);
                return <article className="latest-content-row" key={`${row.id || url || index}`}><div className="latest-content-thumb">▶</div><div className="latest-content-main"><b>{contentTitle(row)}</b><p>@{String(row.account_handle || row.account_display_name || '-')} · {platformName(row.platform)}</p></div><div className="latest-content-metrics"><span><em>曝光</em><strong>{compact(num(row.views || row.total_views || row.play_count || row.impressions))}</strong></span><span><em>点赞</em><strong>{compact(num(row.likes || row.like_count))}</strong></span><span><em>评论</em><strong>{compact(num(row.comments || row.comment_count))}</strong></span></div><div className="latest-content-tools"><button type="button" disabled={!url} onClick={() => url && window.open(url, '_blank', 'noopener,noreferrer')}>↗</button></div></article>;
              }) : <div className="empty-real">当前暂无最新内容列表。</div>}
            </div>
          </div>
        </div>
        <aside className="rail">
          <div className="glass-card rail-card intelligence-war-room"><div className="panel-head"><h3>智能层战情室</h3><span className="tag">只读 · 中文</span></div><div className="dashboard-intelligence-list">{cards.map((card) => <IntelligenceCard card={card} compact key={card.id} onSelect={setSelectedCard} />)}</div></div>
          <div className="glass-card rail-card"><div className="panel-head"><h3>本周关键任务</h3><button className="link" type="button" onClick={() => goto('projects', '项目跟进')}>查看全部</button></div>{taskRows.length ? taskRows.slice(0, 5).map((task, index) => <div className="task" key={String(task.id || index)}><div className="task-head"><b>{String(task.title || '推荐任务')}</b><span className="priority mid">中</span></div><p>{String(task.body || task.summary || '等待人工复核')}</p><div className="progress"><span style={glassVarStyle({ '--w': `${Math.max(8, Math.round(num(task.confidence) * 100))}%` })}></span></div></div>) : <div className="empty-real">暂无真实推荐任务</div>}</div>
          <div className="glass-card rail-card"><div className="panel-head"><h3>快捷入口</h3></div><div className="quick">{[{ label: '智能中心', page: 'intelligenceCenter' }, { label: '红人搜索', page: 'discover' }, { label: '项目跟进', page: 'projects' }, { label: '数据质量', page: 'dataQuality' }, { label: '报表导出', page: 'reports' }].map((item) => <button key={item.label} type="button" onClick={() => goto(item.page as VkpiPageKey, item.label)}><b>◇</b><span>{item.label}</span></button>)}</div></div>
        </aside>
      </div>
    </>
  );

  return (
    <div className={`vkpi-glass-shell${embedded ? ' vkpi-glass-shell--embedded' : ''}`} data-testid={testId}>
      {embedded ? <main className="main">{dashboardContent}</main> : (
        <>
          <div className="browser"><div className="traffic"><span className="t-dot red"></span><span className="t-dot yellow"></span><span className="t-dot green"></span></div><div className="browser-title">viltroxtest.com · V-KPI Glass Intelligence</div><div className="browser-icons">◉ ⇧</div></div>
          <div className="app"><GlassSidebar activeKey="Dashboard" onSelectNav={(key) => key === 'Dashboard' ? undefined : goto('intelligenceCenter', key)} profileInitial={userName.slice(0, 1).toUpperCase()} profileName={userName} profileRole={userRole} /><main className="main">{dashboardContent}</main></div>
        </>
      )}
      {selectedCard ? (
        <div className="panel-drawer panel-drawer--intelligence" role="dialog" aria-label={selectedCard.title}>
          <div className="country-drawer-head panel-drawer-head"><div><span>{selectedCard.sourceLabel}</span><b>{selectedCard.title}</b></div><button type="button" onClick={() => setSelectedCard(null)}>×</button></div>
          <IntelligenceDetailPanel card={selectedCard} emptyTitle="选择一张智能卡" emptySummary="从 Dashboard 右侧选择摘要或系统卡片。" extraSlot={<IntelligenceActionStrip card={selectedCard} mode="read_only" />} footerNote="Dashboard 只展示只读智能摘要；接受、拒绝、延后和任务生成会进入智能中心统一处理。" onAction={handleCardAction} />
        </div>
      ) : null}
      {evidenceCard ? <IntelligenceEvidenceDrawer card={evidenceCard} onClose={() => setEvidenceCard(null)} footerNote="Dashboard 证据抽屉只展示已有输出，不触发外部抓取或模型调用。" /> : null}
      {panel ? (
        <div className="panel-drawer" role="dialog" aria-label={panel.title}>
          <div className="country-drawer-head panel-drawer-head"><div><span>{panel.sourceLabel}</span><b>{panel.title}</b></div><button type="button" onClick={() => setPanel(null)}>×</button></div>
          <div className="panel-drawer-list">{panel.rows.map((row, index) => <div className="panel-drawer-row" key={`${row.label}-${index}`}><span>{row.label}</span><b>{row.value}</b></div>)}</div>
          <button className="panel-drawer-action" type="button" onClick={() => goto(panel.actionPage || 'dataQuality', panel.title)}>{panel.actionLabel}</button>
        </div>
      ) : null}
      <GlassToast show={Boolean(toast)}>{toast}</GlassToast>
    </div>
  );
}

export default DashboardPremium;
