import { useEffect, useMemo, useState } from 'react';
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
import { listBrandSignals } from '../../../services/vkpi.ui-api';
import type {
  VkpiDashboardData,
  VkpiAlertItem,
  VkpiKolDetail,
  VkpiLeaderboardItem,
  VkpiMetricEvidenceKey,
  VkpiProjectRow,
  VkpiStaffMember,
} from '../vkpiTypes';

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
  apiToken?: string;
}

function isEvidenceMetric(key: string): key is VkpiMetricEvidenceKey {
  return ['gmv', 'cost', 'roi', 'new_kol', 'published_content', 'valid_clicks', 'net_contribution', 'views', 'active_projects', 'alerts'].includes(key);
}

function text(value: unknown, fallback = ''): string {
  const next = String(value ?? '').trim();
  return next || fallback;
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
  apiToken,
}: CommandCenterProps) {
  const handleOpenStaffFromLeaderboard = viewMode === 'manager'
    ? (item: VkpiLeaderboardItem) => {
      if (item.staffId) void onOpenStaffProfile(item.staffId, { name: item.name, avatarUrl: item.avatar });
    }
    : undefined;

  return (
    <>
      <section className="vkpi-main-column" aria-label={viewMode === 'manager' ? '管理主控' : '员工工作台'}>
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
            <CardHeader title={viewMode === 'manager' ? '员工贡献榜（按销售额）' : '我的贡献概览'} />
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
              <h2>{viewMode === 'manager' ? '员工 / KOL 管理' : '我的 KOL / 项目'}</h2>
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
    </>
  );
}
