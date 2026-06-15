import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';
import { AlertsPanel } from '../charts/AlertsPanel';
import { ContentPerformance } from '../charts/ContentPerformance';
import { DonutChart } from '../charts/DonutChart';
import { FunnelChart } from '../charts/FunnelChart';
import { Leaderboard } from '../charts/Leaderboard';
import { ProductRoiChart } from '../charts/ProductRoiChart';
import { TrendChart } from '../charts/TrendChart';
import { WeeklySummary } from '../charts/WeeklySummary';
import { KolDetailPanel } from '../panels/KolDetailPanel';
import { CardHeader } from '../shared/CardHeader';
import { ExportWidget } from '../shared/ExportWidget';
import { Icon } from '../shared/Icon';
import { MetricCard } from '../shared/MetricCard';
import { ProjectTable } from '../tables/ProjectTable';
import { getRecommendationFeedbackBacklog } from '../../../domains/intelligence';
import { listBrandSignals } from '../../../domains/market';
import { IntelligenceCard, intelligenceStatusLabels, type IntelligenceAction, type IntelligenceCardModel } from '../intelligence/IntelligenceCard';
import { writeDiscoverFocus } from '../intelligence/intelligenceDiscoveryFocus';
import { writeProjectFocus } from '../intelligence/intelligenceProjectFocus';
import { clearProjectActionFocus, readProjectActionFocus, type ProjectActionFocusPayload } from '../intelligence/intelligenceProjectActionFocus';
import {
  readIntelligenceActionFeedback,
  writeIntelligenceActionFeedback,
  type IntelligenceActionFeedbackRecord,
  type IntelligenceActionFeedbackStatus,
} from '../intelligence/intelligenceActionFeedback';
import { IntelligenceDetailPanel } from '../intelligence/IntelligenceDetailPanel';
import { IntelligenceEvidenceDrawer } from '../intelligence/IntelligenceEvidenceDrawer';
import type {
  VkpiDashboardData,
  VkpiAlertItem,
  VkpiKolDetail,
  VkpiLeaderboardItem,
  VkpiMetricEvidenceKey,
  VkpiPageKey,
  VkpiProjectRow,
  VkpiStaffMember,
} from '../vkpiTypes';
import {
  applyActionFeedback,
  asRecord,
  buildEmployeeActionBuckets,
  buildEmployeeIntelligenceCards,
  buildProjectActionFocusCard,
  employeeProjectActionCount,
  isEvidenceMetric,
  priorityLabel,
  storeIntelligenceFocus,
  text,
} from './CommandCenter.helpers';
import type { Row } from './CommandCenter.helpers';

interface CommandCenterProps {
  data: VkpiDashboardData;
  visibleMetrics: VkpiDashboardData['metrics'];
  filteredProjects: VkpiProjectRow[];
  visibleProjects: VkpiProjectRow[];
  visibleEnd: number;
  selectedProject?: VkpiProjectRow;
  selectedKolForPanel: VkpiKolDetail;
  query: string;
  viewMode: 'manager' | 'employee';
  onQueryChange: (query: string) => void;
  onOpenMetricEvidence: (metric: VkpiMetricEvidenceKey, metricValueId?: number | null) => void;
  onSelectProject: (project: VkpiProjectRow) => void;
  onOpenKolProfile: (project: VkpiProjectRow) => void | Promise<void>;
  onOpenStaffProfile: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onCopyShortLink?: (slug: string) => void;
  alerts?: VkpiAlertItem[];
  onResolveAlert?: (alertId: string) => void | Promise<void>;
  onOpenAlert?: (alertId: string) => void | Promise<void>;
  onDownloadReportPDF?: () => void;
  onExportPDF?: () => void;
  onSelectPage?: (page: VkpiPageKey) => void;
  apiToken?: string;
}

function ActionVisualPanel({ card }: { card: IntelligenceCardModel }) {
  const confidence = Math.max(10, Math.min(100, Math.round((card.confidence || 0.5) * 100)));
  const evidenceScore = Math.min(100, Math.max(16, card.evidence.length * 25));
  const statusStep = card.status === 'done' ? 3 : card.status === 'accepted' || card.status === 'snoozed' ? 2 : 1;
  const steps = ['看证据', '去执行', '回流结果'];
  return (
    <div className="vkpi-intelligence-visual">
      <div className="vkpi-intelligence-visual__head">
        <div>
          <b>图文 / 动态演示</b>
          <span>把文字判断拆成证据、执行、反馈三步。</span>
        </div>
        <em>{intelligenceStatusLabels[card.status]}</em>
      </div>
      <div className="vkpi-intelligence-visual__flow" aria-label="动作流程">
        {steps.map((step, index) => {
          const order = index + 1;
          return (
            <span
              className={`${order < statusStep ? 'is-done' : ''} ${order === statusStep ? 'is-active' : ''}`}
              key={step}
            >
              <i>{order}</i>
              {step}
            </span>
          );
        })}
      </div>
      <div className="vkpi-intelligence-visual__grid">
        <span>
          <b>优先级</b>
          <strong>{priorityLabel(card.priority)}</strong>
        </span>
        <span>
          <b>证据数</b>
          <strong>{card.evidence.length} 条</strong>
        </span>
        <span style={{ '--score': `${confidence}%` } as CSSProperties}>
          <b>置信度</b>
          <strong>{confidence}%</strong>
          <i />
        </span>
        <span style={{ '--score': `${evidenceScore}%` } as CSSProperties}>
          <b>证据覆盖</b>
          <strong>{evidenceScore}%</strong>
          <i />
        </span>
      </div>
    </div>
  );
}

function signalTypeLabel(value: unknown): string {
  const key = text(value).toLowerCase();
  if (key === 'mention_viltrox') return 'Viltrox 提及';
  if (key === 'show_product') return '产品 / SKU';
  if (key === 'comment_mention') return '评论提及';
  if (key === 'mention_competitor') return '竞品提及';
  return key || '品牌信号';
}

function BrandSignalSummary({ apiToken }: { apiToken?: string }) {
  const [signals, setSignals] = useState<Array<Record<string, unknown>>>([]);
  const [schemaReady, setSchemaReady] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    if (!apiToken) return undefined;
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const response = await listBrandSignals(apiToken, { status: 'new', limit: 6 });
        if (!cancelled) {
          setSignals(response.signals || []);
          setSchemaReady(response.schema_ready !== false);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : '品牌信号读取失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  const competitorCount = useMemo(() => signals.filter((item) => text(item.brand_role).toLowerCase() === 'competitor').length, [signals]);
  const selfCount = signals.length - competitorCount;

  if (!apiToken) return null;

  return (
    <section className="vkpi-card vkpi-panel-card vkpi-brand-signal-panel">
      <div className="vkpi-card__header">
        <div>
          <h2>品牌信号</h2>
          <span>{loading ? '读取中' : schemaReady ? `${signals.length} 条未处理` : '未建表'}</span>
        </div>
        <a className="vkpi-link-button" href="#dataQuality">查看详情</a>
      </div>
      {error ? (
        <div className="vkpi-empty-state">{error}</div>
      ) : !schemaReady ? (
        <div className="vkpi-empty-state">品牌信号表还没有创建；先运行扫描或迁移。</div>
      ) : signals.length ? (
        <>
          <div className="vkpi-brand-signal-panel__summary">
            <span className="is-self">机会 {selfCount}</span>
            <span className={competitorCount ? 'is-risk' : ''}>竞品 {competitorCount}</span>
          </div>
          <div className="vkpi-alert-list">
            {signals.slice(0, 3).map((signal) => {
              const isCompetitor = text(signal.brand_role).toLowerCase() === 'competitor';
              return (
                <article className={`vkpi-alert ${isCompetitor ? 'is-danger' : 'is-info'}`} key={String(signal.id || signal.signal_uid)}>
                  <i />
                  <span>
                    {text(signal.brand_name, 'brand')}
                    <small>{signalTypeLabel(signal.signal_type)} · {text(signal.platform, '-')}</small>
                  </span>
                  <div className="vkpi-alert-actions"><strong>{text(signal.signal_strength, '-')}</strong></div>
                </article>
              );
            })}
          </div>
        </>
      ) : (
        <div className="vkpi-empty-state">当前没有未处理 Viltrox / 竞品信号。</div>
      )}
    </section>
  );
}

function EmployeeWorkdayHero({
  mustActionCount,
  watchActionCount,
  evidenceActionCount,
  projectActionCount,
  riskActionCount,
}: {
  mustActionCount: number;
  watchActionCount: number;
  evidenceActionCount: number;
  projectActionCount: number;
  riskActionCount: number;
}) {
  const totalActions = mustActionCount + watchActionCount + evidenceActionCount;
  const focusScore = Math.max(12, Math.min(100, Math.round(((mustActionCount * 1.4) + projectActionCount + evidenceActionCount + riskActionCount) * 14)));
  const flowSteps = [
    { label: '发现 KOL', hint: '找新候选' },
    { label: '联系 / 寄样', hint: '推进项目' },
    { label: '发布内容', hint: '跟进素材' },
    { label: '结果回流', hint: '反馈学习' },
  ];

  return (
    <section className="vkpi-employee-workday" aria-label="我的今日执行区">
      <div className="vkpi-employee-workday__copy">
        <span>EMPLOYEE VIEW</span>
        <h1>我的今日工作台</h1>
        <p>
          先处理 {mustActionCount} 个必做动作、{projectActionCount} 个可推进项目和 {evidenceActionCount} 个待补证据；
          搜索不是唯一入口，KOL/账号、智能卡和项目会一起把候选推到你面前。
        </p>
        <div className="vkpi-employee-workday__actions">
          <a className="vkpi-button vkpi-button--primary" href="#intelligenceCenter">处理智能动作</a>
          <a className="vkpi-button" href="#discover">发现 KOL</a>
          <a className="vkpi-button" href="#projects">进入项目</a>
        </div>
      </div>
      <div className="vkpi-employee-workday__visual" style={{ '--score': `${focusScore}%` } as CSSProperties}>
        <div className="vkpi-employee-workday__flow">
          {flowSteps.map((step, index) => (
            <span className={index === 0 ? 'is-active' : ''} key={step.label}>
              <i>{index + 1}</i>
              <b>{step.label}</b>
              <em>{step.hint}</em>
            </span>
          ))}
        </div>
        <div className="vkpi-employee-workday__meter">
          <span><b>{focusScore}%</b> 今日动作密度</span>
          <i />
        </div>
      </div>
      <div className="vkpi-employee-workday__stats">
        <span><b>{mustActionCount}</b><em>必做</em></span>
        <span><b>{projectActionCount}</b><em>项目</em></span>
        <span><b>{evidenceActionCount}</b><em>补证据</em></span>
        <span><b>{riskActionCount}</b><em>风险</em></span>
        <span><b>{totalActions}</b><em>行动卡</em></span>
      </div>
    </section>
  );
}

export function CommandCenter({
  data,
  visibleMetrics,
  filteredProjects,
  visibleProjects,
  visibleEnd,
  selectedProject,
  selectedKolForPanel,
  query,
  viewMode,
  onQueryChange,
  onOpenMetricEvidence,
  onSelectProject,
  onOpenKolProfile,
  onOpenStaffProfile,
  onCopyShortLink,
  alerts,
  onResolveAlert,
  onOpenAlert,
  onDownloadReportPDF,
  onExportPDF,
  onSelectPage,
  apiToken,
}: CommandCenterProps) {
  const [recommendationBacklog, setRecommendationBacklog] = useState<Row>({});
  const [projectActionFocus, setProjectActionFocus] = useState<ProjectActionFocusPayload | null>(null);
  const [actionFeedback, setActionFeedback] = useState<Record<string, IntelligenceActionFeedbackRecord>>({});
  const [intelligenceDrawer, setIntelligenceDrawer] = useState<IntelligenceCardModel | null>(null);
  const [evidenceDrawer, setEvidenceDrawer] = useState<IntelligenceCardModel | null>(null);
  const [intelligenceError, setIntelligenceError] = useState('');
  const handleOpenStaffFromLeaderboard = viewMode === 'manager'
    ? (item: VkpiLeaderboardItem) => {
      if (item.staffId) void onOpenStaffProfile(item.staffId, { name: item.name, avatarUrl: item.avatar });
    }
    : undefined;
  const employeeAlerts = alerts || data.alerts;
  const employeeIntelligenceCards = useMemo(
    () => {
      const cards = buildEmployeeIntelligenceCards({
        recommendationBacklog,
        projects: filteredProjects,
        alerts: employeeAlerts,
      });
      const nextCards = projectActionFocus ? [buildProjectActionFocusCard(projectActionFocus), ...cards] : cards;
      return nextCards.map((card) => applyActionFeedback(card, actionFeedback[card.id]));
    },
    [actionFeedback, employeeAlerts, filteredProjects, projectActionFocus, recommendationBacklog],
  );
  const employeeActionBuckets = useMemo(
    () => buildEmployeeActionBuckets(employeeIntelligenceCards),
    [employeeIntelligenceCards],
  );
  const mustActionCount = employeeActionBuckets.find((bucket) => bucket.key === 'must')?.cards.length || 0;
  const watchActionCount = employeeActionBuckets.find((bucket) => bucket.key === 'watch')?.cards.length || 0;
  const evidenceActionCount = employeeActionBuckets.find((bucket) => bucket.key === 'evidence')?.cards.length || 0;
  const employeeProjectCount = employeeProjectActionCount(filteredProjects);
  const employeeRiskCount = employeeAlerts.filter((alert) => alert.severity === 'danger' || alert.severity === 'warning').length;

  useEffect(() => {
    if (viewMode === 'employee') setActionFeedback(readIntelligenceActionFeedback());
  }, [viewMode]);

  const updateActionFeedback = useCallback((card: IntelligenceCardModel, nextStatus: IntelligenceActionFeedbackStatus) => {
    const feedback = writeIntelligenceActionFeedback(card.id, nextStatus);
    setActionFeedback((current) => ({ ...current, [card.id]: feedback }));
    setIntelligenceDrawer((current) => current?.id === card.id ? applyActionFeedback(current, feedback) : current);
  }, []);

  useEffect(() => {
    if (viewMode !== 'employee') {
      setProjectActionFocus(null);
      return;
    }
    const focus = readProjectActionFocus();
    if (focus) {
      clearProjectActionFocus();
      setProjectActionFocus(focus);
    }
  }, [viewMode]);

  useEffect(() => {
    let cancelled = false;
    if (!apiToken || viewMode !== 'employee') {
      setRecommendationBacklog({});
      setIntelligenceError('');
      return () => { cancelled = true; };
    }
    getRecommendationFeedbackBacklog(apiToken, 6)
      .then((response) => {
        if (!cancelled) setRecommendationBacklog(response);
      })
      .catch((error) => {
        if (!cancelled) setIntelligenceError(error instanceof Error ? error.message : '推荐 backlog 读取失败');
      });
    return () => { cancelled = true; };
  }, [apiToken, viewMode]);

  const handleIntelligenceAction = useCallback((action: IntelligenceAction, card: IntelligenceCardModel) => {
    if (action.kind === 'disabled') return;
    if (action.label.includes('证据')) {
      setEvidenceDrawer(card);
      return;
    }
    if (action.label.includes('接受')) {
      updateActionFeedback(card, 'accepted');
      return;
    }
    if (action.label.includes('延后')) {
      updateActionFeedback(card, 'snoozed');
      return;
    }
    if (action.label.includes('完成')) {
      updateActionFeedback(card, 'done');
      return;
    }
    if (action.label.includes('智能中心')) {
      storeIntelligenceFocus({
        source: 'employee_workbench',
        focusType: card.metadata?.focus_type || 'recommendation_backlog',
        recommendationId: card.metadata?.recommendation_id,
        cardId: card.id,
      });
      onSelectPage?.('intelligenceCenter');
      return;
    }
    if (action.label.includes('项目跟进')) {
      const projectId = text(card.metadata?.project_id || card.entityId, '');
      const projectName = text(card.metadata?.project_name || card.title, '项目跟进');
      writeProjectFocus({
        source: 'intelligence_task',
        projectId: projectId || undefined,
        projectName,
        kolHandle: text(card.metadata?.kol_handle, '') || undefined,
        summary: card.summary,
      });
      onSelectPage?.('projects');
      return;
    }
    if (action.label.includes('红人搜索') || action.label.includes('相似 KOL')) {
      writeDiscoverFocus({
        source: 'employee_workbench',
        title: card.title,
        summary: card.summary,
        query: text(card.metadata?.discover_query, card.title),
        platform: text(card.metadata?.discover_platform, 'all'),
        sourceLabel: card.sourceLabel,
      });
      onSelectPage?.('discover');
      return;
    }
    if (action.label.includes('数据质量')) onSelectPage?.('dataQuality');
  }, [onSelectPage, updateActionFeedback]);

  return (
    <>
      <section className="vkpi-main-column" aria-label={viewMode === 'manager' ? '管理主控' : '我的工作台'}>
        {viewMode === 'employee' ? (
          <EmployeeWorkdayHero
            mustActionCount={mustActionCount}
            watchActionCount={watchActionCount}
            evidenceActionCount={evidenceActionCount}
            projectActionCount={employeeProjectCount}
            riskActionCount={employeeRiskCount}
          />
        ) : null}

        <section className="vkpi-metrics-grid" aria-label="核心指标">
          {visibleMetrics.map((metric) => {
            const evidenceKey = isEvidenceMetric(metric.key) ? metric.key : null;
            return (
              <MetricCard
                key={metric.key}
                metric={metric}
                onClick={evidenceKey ? () => onOpenMetricEvidence(evidenceKey, metric.metricValueId) : undefined}
              />
            );
          })}
        </section>

        <section className="vkpi-card-grid vkpi-card-grid--top">
          <div className="vkpi-card vkpi-card--wide">
            <CardHeader title="播放量 / 销售趋势" action="每日" />
            <TrendChart points={data.revenueTrend} />
          </div>
          <div className="vkpi-card">
            <CardHeader title="KOL 合作漏斗" />
            <FunnelChart items={data.funnel} />
          </div>
          <div className="vkpi-card">
            <CardHeader title={viewMode === 'manager' ? '成员贡献榜（按销售额）' : '我的贡献概览'} />
            <Leaderboard items={data.staffLeaderboard} onOpenStaff={handleOpenStaffFromLeaderboard} />
          </div>
        </section>

        <section className="vkpi-card-grid vkpi-card-grid--middle">
          {viewMode === 'manager' ? (
            <div className="vkpi-card">
              <CardHeader title="产品成本 / 销售对比" />
              <ProductRoiChart items={data.productRoi} />
            </div>
          ) : (
            <div className="vkpi-card">
              <CardHeader title="我的项目进度" />
              <FunnelChart items={data.funnel} />
            </div>
          )}
          <div className="vkpi-card">
            <CardHeader title="平台销售占比" />
            <DonutChart items={data.platformShare} centerLabel={data.metrics.find((item) => item.key === 'gmv')?.value || '$0'} />
          </div>
          <div className="vkpi-card">
            <CardHeader title="内容表现（播放量 / 点击）" />
            <ContentPerformance items={data.contentTypePerformance} />
          </div>
        </section>

        <section className="vkpi-card vkpi-table-card">
          <div className="vkpi-table-card__header">
            <div>
              <h2>{viewMode === 'manager' ? '成员 / KOL 管理' : '我的 KOL / 项目'}</h2>
              <span>{filteredProjects.length} 条</span>
            </div>
            <div className="vkpi-table-tools">
              <label className="vkpi-table-search">
                <Icon name="search" />
                <input
                  value={query}
                  onChange={(event) => onQueryChange(event.target.value)}
                  placeholder="在表格内搜索..."
                />
              </label>
            </div>
          </div>

          <ProjectTable
            projects={visibleProjects}
            selectedProjectId={selectedProject?.id}
            viewMode={viewMode}
            onSelectProject={onSelectProject}
            onOpenKolProfile={onOpenKolProfile}
            onOpenStaffProfile={viewMode === 'manager' ? onOpenStaffProfile : undefined}
          />

          <footer className="vkpi-table-footer">
            <span>显示 {filteredProjects.length ? 1 : 0} - {visibleEnd}，共 {filteredProjects.length} 条</span>
            {filteredProjects.length > 5 ? (
              <span className="vkpi-table-footer__note">当前显示前 5 条；完整分页留待下一轮接入。</span>
            ) : <span />}
            <span className="vkpi-table-footer__note">每页 5 条</span>
          </footer>
        </section>
      </section>

      <aside className="vkpi-right-rail" aria-label="提醒和详情">
        {viewMode === 'employee' ? (
          <section className="vkpi-card vkpi-panel-card vkpi-employee-intelligence">
            <div className="vkpi-card__header">
              <div>
                <h2>今日行动卡</h2>
                <span>{intelligenceError ? '读取失败' : `${mustActionCount} 必做 · ${evidenceActionCount} 待补证据`}</span>
              </div>
              <a className="vkpi-link-button" href="#projects">去执行</a>
            </div>
            {intelligenceError ? (
              <div className="vkpi-inline-message is-error">推荐 backlog 读取失败：{intelligenceError}</div>
            ) : null}
            <div className="vkpi-employee-action-buckets">
              {employeeActionBuckets.map((bucket) => (
                <section className={`vkpi-employee-action-bucket is-${bucket.key}`} key={bucket.key}>
                  <div>
                    <b>{bucket.title}</b>
                    <span>{bucket.cards.length} 项</span>
                  </div>
                  <p>{bucket.hint}</p>
                  <div className="dashboard-intelligence-list">
                    {bucket.cards.length ? bucket.cards.map((card) => (
                      <IntelligenceCard
                        card={card}
                        compact
                        key={card.id}
                        onSelect={setIntelligenceDrawer}
                      />
                    )) : <small>当前没有这一类动作。</small>}
                  </div>
                </section>
              ))}
            </div>
          </section>
        ) : null}
        <BrandSignalSummary apiToken={viewMode === 'manager' ? apiToken : undefined} />
        <AlertsPanel alerts={alerts || data.alerts} onResolveAlert={onResolveAlert} onOpenAlert={onOpenAlert} />
        <WeeklySummary summary={data.weeklySummary} />
        <ExportWidget report={data.exportReport} onDownloadPDF={onDownloadReportPDF || onExportPDF} />
        <KolDetailPanel
          kol={selectedKolForPanel}
          selectedProject={selectedProject}
          onCopyShortLink={onCopyShortLink}
        />
      </aside>
      {intelligenceDrawer ? (
        <div className="panel-drawer panel-drawer--intelligence" role="dialog" aria-label={intelligenceDrawer.title}>
          <div className="country-drawer-head">
            <div><span>{intelligenceDrawer.sourceLabel}</span><b>{intelligenceDrawer.title}</b></div>
            <button type="button" onClick={() => setIntelligenceDrawer(null)}>×</button>
          </div>
          <IntelligenceDetailPanel
            card={intelligenceDrawer}
            emptyTitle="选择一张行动卡"
            emptySummary="从今日行动卡选择推荐、项目或风险。"
            extraSlot={<ActionVisualPanel card={intelligenceDrawer} />}
            footerNote="我的工作台只展示只读行动入口；写 feedback 和任务创建仍在智能中心或项目流程里完成。"
            onAction={handleIntelligenceAction}
          />
        </div>
      ) : null}
      {evidenceDrawer ? (
        <IntelligenceEvidenceDrawer
          card={evidenceDrawer}
          onClose={() => setEvidenceDrawer(null)}
          footerNote="我的行动卡只展示已有 evidence refs，不触发外部抓取或模型调用。"
        />
      ) : null}
    </>
  );
}
