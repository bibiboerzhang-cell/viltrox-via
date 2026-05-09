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
import type {
  VkpiDashboardData,
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
  onDownloadReportPDF?: () => void;
  onExportPDF?: () => void;
}

function isEvidenceMetric(key: string): key is VkpiMetricEvidenceKey {
  return ['gmv', 'cost', 'roi', 'new_kol', 'published_content', 'valid_clicks', 'net_contribution', 'views', 'active_projects', 'alerts'].includes(key);
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
  onDownloadReportPDF,
  onExportPDF,
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
              <div className="vkpi-pagination" aria-label="表格分页">
                <button type="button">‹</button>
                <button className="is-active" type="button">1</button>
                <button type="button">2</button>
                <button type="button">3</button>
                <button type="button">4</button>
                <span>…</span>
                <button type="button">{Math.ceil(filteredProjects.length / 5)}</button>
                <button type="button">›</button>
              </div>
            ) : <span />}
            <button className="vkpi-mini-button" type="button">每页 5 条</button>
          </footer>
        </section>
      </section>

      <aside className="vkpi-right-rail" aria-label="提醒和详情">
        <AlertsPanel alerts={data.alerts} />
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
