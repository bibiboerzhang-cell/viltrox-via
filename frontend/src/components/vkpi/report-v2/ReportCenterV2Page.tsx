import { useMemo, useState } from 'react';
import type {
  VkpiDashboardData,
  VkpiMetricCard,
  VkpiMetricEvidenceKey,
  VkpiPageKey,
  VkpiProjectRow,
} from '../vkpiTypes';
import { Icon } from '../shared/Icon';
import { V2ShellTopbar } from '../v2-shell/V2ShellTopbar';
import './report-center-v2.css';

interface ReportCenterV2PageProps {
  apiToken?: string;
  data: VkpiDashboardData;
  viewMode: 'manager' | 'employee';
  userName?: string;
  userRole?: string;
  userAvatar?: string;
  onExportPDF?: () => void;
  onExportCSV?: () => void;
  onGenerateWeeklyReport?: () => void;
  onSelectProject: (project: VkpiProjectRow) => void;
  onSelectPage?: (page: VkpiPageKey) => void;
  onOpenEvidence: (metric: VkpiMetricEvidenceKey, metricValueId?: number | null) => void;
  onRunKpiRollup?: (ledgerDate?: string) => Promise<void>;
  onSignOut?: () => Promise<void> | void;
}

type ReportLanguage = 'zh' | 'en';
type ReportPeriod = 'weekly' | 'monthly';
type ReportFormat = 'visual' | 'markdown';
type ReportSectionKey = 'kpiOverview' | 'attribution' | 'projects' | 'ledger' | 'risks' | 'summary';

interface ReportBuilderConfig {
  language: ReportLanguage;
  period: ReportPeriod;
  sections: Record<ReportSectionKey, boolean>;
}

const REPORT_SECTIONS: Array<{ key: ReportSectionKey; zh: string; en: string }> = [
  { key: 'kpiOverview', zh: 'KPI 总览', en: 'KPI Overview' },
  { key: 'attribution', zh: '归因闭环', en: 'Attribution' },
  { key: 'projects', zh: '项目明细', en: 'Projects' },
  { key: 'ledger', zh: 'KPI Ledger', en: 'KPI Ledger' },
  { key: 'risks', zh: '风险提醒', en: 'Risks' },
  { key: 'summary', zh: '管理摘要', en: 'Summary' },
];

const DEFAULT_REPORT_SECTIONS: Record<ReportSectionKey, boolean> = {
  kpiOverview: true,
  attribution: true,
  projects: true,
  ledger: true,
  risks: true,
  summary: true,
};

function metricByKey(metrics: VkpiMetricCard[], key: string): VkpiMetricCard | undefined {
  return metrics.find((metric) => metric.key === key || metric.label.toLowerCase().includes(key.toLowerCase()));
}

function safeCount(value: number): string {
  return value > 0 ? value.toLocaleString('en-US') : '--';
}

function metricValue(metric?: VkpiMetricCard): string {
  if (!metric) return '--';
  const clean = String(metric.value || '').trim();
  return clean && clean !== '0' && clean !== '$0' && clean !== '0.00%' ? clean : '--';
}

function sectionStatus(data: VkpiDashboardData, key: ReportSectionKey): string {
  const gmv = metricByKey(data.metrics, 'gmv');
  const roi = metricByKey(data.metrics, 'roi');
  if (key === 'kpiOverview') return data.metrics.length ? `${data.metrics.length} 指标` : '无信号';
  if (key === 'attribution') return metricValue(gmv) !== '--' || metricValue(roi) !== '--' || data.attributions.length || data.costs.length ? '有信号' : '待 Shopify / 成本';
  if (key === 'projects') return data.projects.length ? `${data.projects.length} 项` : '无信号';
  if (key === 'ledger') return data.kpiLedger.length ? `${data.kpiLedger.length} 条` : '无信号';
  if (key === 'risks') return data.alerts.length ? `${data.alerts.length} 条` : '无信号';
  return data.weeklySummary ? '已生成' : '待生成';
}

function reportLabel(language: ReportLanguage, zh: string, en: string): string {
  return language === 'zh' ? zh : en;
}

function buildReportText(data: VkpiDashboardData, viewMode: 'manager' | 'employee', config: ReportBuilderConfig): string {
  const gmv = metricByKey(data.metrics, 'gmv');
  const roi = metricByKey(data.metrics, 'roi');
  const views = metricByKey(data.metrics, 'views');
  const content = metricByKey(data.metrics, 'published_content') || metricByKey(data.metrics, '内容');
  const language = config.language;
  const isZh = language === 'zh';
  const title = isZh ? `# V-KPI ${config.period === 'weekly' ? '周报' : '月报'}` : `# V-KPI ${config.period === 'weekly' ? 'Weekly' : 'Monthly'} Report`;
  const lines = [
    title,
    '',
    isZh ? `- 视角：${viewMode === 'manager' ? '管理端' : '员工端'}` : `- View: ${viewMode}`,
    isZh ? `- 数据状态：${data.dataStatus} / ${data.dataNotice || '待接入'}` : `- Data status: ${data.dataStatus} / ${data.dataNotice || 'pending'}`,
    isZh ? `- 模板周期：${config.period === 'weekly' ? '周报' : '月报'} · 当前数据范围：${data.rangeLabel || `${data.windowDays || '--'} 天`}` : `- Template: ${config.period} · Current range: ${data.rangeLabel || `${data.windowDays || '--'} days`}`,
    '',
  ];

  if (config.sections.kpiOverview) {
    lines.push(
      isZh ? '## KPI 总览' : '## KPI Overview',
      `- ${reportLabel(language, 'GMV', 'GMV')}：${metricValue(gmv)} · ${gmv?.deltaLabel || reportLabel(language, '待 Shopify 完整闭环', 'Pending Shopify')}`,
      `- ${reportLabel(language, '平均 ROI', 'Average ROI')}：${metricValue(roi)} · ${roi?.deltaLabel || reportLabel(language, '待成本与订单归因', 'Pending cost and order attribution')}`,
      `- ${reportLabel(language, '曝光量', 'Views')}：${metricValue(views)} · ${views?.deltaLabel || reportLabel(language, '内容曝光证据', 'Content evidence')}`,
      `- ${reportLabel(language, '内容数', 'Content')}：${metricValue(content)} · ${content?.deltaLabel || reportLabel(language, '发布内容证据', 'Published content evidence')}`,
      '',
    );
  }

  if (config.sections.attribution) {
    lines.push(
      isZh ? '## 归因闭环' : '## Attribution',
      isZh ? `- 短链：${safeCount(data.links.length)} / 归因：${safeCount(data.attributions.length)} / 成本：${safeCount(data.costs.length)}` : `- Links: ${safeCount(data.links.length)} / Attributions: ${safeCount(data.attributions.length)} / Costs: ${safeCount(data.costs.length)}`,
      metricValue(gmv) === '--' ? (isZh ? '- GMV：待 Shopify 接入或无真实订单信号' : '- GMV: pending Shopify or no real order signal') : `- GMV：${metricValue(gmv)}`,
      metricValue(roi) === '--' ? (isZh ? '- ROI：待成本与订单闭环' : '- ROI: pending cost and order loop') : `- ROI：${metricValue(roi)}`,
      '',
    );
  }

  if (config.sections.projects) {
    lines.push(isZh ? '## 项目明细' : '## Projects');
    if (data.projects.length) {
      data.projects.slice(0, 8).forEach((project, index) => {
        lines.push(`- ${index + 1}. ${project.campaign || project.kolName} · ${project.platform} · ${project.stage} · GMV ${project.gmv ? `$${project.gmv.toLocaleString('en-US')}` : '--'}`);
      });
    } else {
      lines.push(isZh ? '- 暂无项目明细' : '- No project rows');
    }
    lines.push('');
  }

  if (config.sections.ledger) {
    lines.push(isZh ? '## KPI Ledger' : '## KPI Ledger');
    if (data.kpiLedger.length) {
      data.kpiLedger.slice(0, 8).forEach((row, index) => {
        lines.push(`- ${index + 1}. ${row.staffName || row.staffId || reportLabel(language, '未知员工', 'Unknown staff')} · ${row.metricLabel || row.metricKey || reportLabel(language, '动作', 'action')} · ${row.metricValue ?? '--'}`);
      });
    } else {
      lines.push(isZh ? '- 暂无 Ledger 明细' : '- No ledger rows');
    }
    lines.push('');
  }

  if (config.sections.risks) {
    lines.push(isZh ? '## 风险提醒' : '## Risks');
    if (data.alerts.length) {
      data.alerts.slice(0, 8).forEach((alert, index) => lines.push(`- ${index + 1}. ${alert.label || alert.alertKey || alert.id} · ${alert.severity}`));
    } else {
      lines.push(isZh ? '- 无信号' : '- No signal');
    }
    lines.push('');
  }

  if (config.sections.summary) {
    lines.push(isZh ? '## 管理摘要' : '## Summary', data.weeklySummary || (isZh ? '当前还没有生成真实周报。' : 'No real weekly summary has been generated yet.'));
  }

  return lines.join('\n');
}

function ReportMetric({ label, value, meta }: { label: string; value: string; meta: string }) {
  return (
    <article className="report-v2-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <em>{meta}</em>
    </article>
  );
}

export function ReportCenterV2Page({
  apiToken,
  data,
  viewMode,
  userName = 'Viltrox 员工',
  userRole = '营销运营',
  userAvatar,
  onExportPDF,
  onExportCSV,
  onGenerateWeeklyReport,
  onSelectProject,
  onSelectPage,
  onOpenEvidence,
  onRunKpiRollup,
  onSignOut,
}: ReportCenterV2PageProps) {
  const [ledgerDate, setLedgerDate] = useState(new Date().toISOString().slice(0, 10));
  const [language, setLanguage] = useState<ReportLanguage>('zh');
  const [period, setPeriod] = useState<ReportPeriod>('weekly');
  const [format, setFormat] = useState<ReportFormat>('visual');
  const [sections, setSections] = useState<Record<ReportSectionKey, boolean>>(DEFAULT_REPORT_SECTIONS);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const reportText = useMemo(() => buildReportText(data, viewMode, { language, period, sections }), [data, language, period, sections, viewMode]);
  const gmv = metricByKey(data.metrics, 'gmv');
  const roi = metricByKey(data.metrics, 'roi');
  const views = metricByKey(data.metrics, 'views');
  const content = metricByKey(data.metrics, 'published_content') || metricByKey(data.metrics, '内容');
  const selectedSectionCount = Object.values(sections).filter(Boolean).length;

  const runRollup = async () => {
    if (!onRunKpiRollup) return;
    setBusy(true);
    setMessage('');
    try {
      await onRunKpiRollup(ledgerDate || undefined);
      setMessage('KPI Ledger 已按真实动作重新计入。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'KPI 计入失败');
    } finally {
      setBusy(false);
    }
  };

  const copyReport = async () => {
    await navigator.clipboard.writeText(reportText);
    setMessage('报告文本已复制');
    window.setTimeout(() => setMessage(''), 1800);
  };

  const toggleSection = (key: ReportSectionKey) => {
    setSections((current) => ({ ...current, [key]: !current[key] }));
  };

  return (
    <div className="report-v2">
      <V2ShellTopbar
        apiToken={apiToken}
        pageTitle="Report Center"
        subtitle="真实数据 / 证据 / 周报 / KPI Ledger"
        reportLabel="Reports"
        userName={userName}
        userRole={userRole}
        userAvatar={userAvatar}
        onSelectPage={onSelectPage}
        onSignOut={onSignOut}
      />

      <header className="report-v2-header">
        <div>
          <span>Report Center</span>
          <h1>报表导出</h1>
          <p>真实数据 / 证据 / 周报 / KPI Ledger</p>
        </div>
        <div className={`report-v2-status is-${data.dataStatus}`}>
          <b>{data.dataStatus === 'live' ? '真实 API' : data.dataStatus === 'partial' ? '部分数据' : '无信号'}</b>
          <span>{data.dataNotice || '待接入'}</span>
        </div>
      </header>

      <section className="report-v2-metrics">
        <ReportMetric label="GMV" value={metricValue(gmv)} meta={gmv?.deltaLabel || '待 Shopify 完整闭环'} />
        <ReportMetric label="平均 ROI" value={metricValue(roi)} meta={roi?.deltaLabel || '待成本与订单归因'} />
        <ReportMetric label="曝光量" value={metricValue(views)} meta={views?.deltaLabel || '内容曝光证据'} />
        <ReportMetric label="内容数" value={metricValue(content)} meta={content?.deltaLabel || '发布内容证据'} />
        <ReportMetric label="项目" value={safeCount(data.projects.length)} meta="projects" />
        <ReportMetric label="KPI Ledger" value={safeCount(data.kpiLedger.length)} meta="真实动作计入" />
      </section>

      <section className="report-v2-builder">
        <aside className="report-v2-builder-panel">
          <div className="report-v2-control">
            <span>语言</span>
            <div>
              <button type="button" className={language === 'zh' ? 'is-active' : ''} onClick={() => setLanguage('zh')}>中文</button>
              <button type="button" className={language === 'en' ? 'is-active' : ''} onClick={() => setLanguage('en')}>English</button>
            </div>
          </div>
          <div className="report-v2-control">
            <span>周期</span>
            <div>
              <button type="button" className={period === 'weekly' ? 'is-active' : ''} onClick={() => setPeriod('weekly')}>周报</button>
              <button type="button" className={period === 'monthly' ? 'is-active' : ''} onClick={() => setPeriod('monthly')}>月报</button>
            </div>
          </div>
          <div className="report-v2-control">
            <span>格式</span>
            <div>
              <button type="button" className={format === 'visual' ? 'is-active' : ''} onClick={() => setFormat('visual')}>图表版</button>
              <button type="button" className={format === 'markdown' ? 'is-active' : ''} onClick={() => setFormat('markdown')}>Markdown</button>
            </div>
          </div>
          <div className="report-v2-section-picker">
            <span>包含内容</span>
            {REPORT_SECTIONS.map((section) => (
              <button key={section.key} type="button" className={sections[section.key] ? 'is-active' : ''} onClick={() => toggleSection(section.key)}>
                <b>{language === 'zh' ? section.zh : section.en}</b>
                <em>{sectionStatus(data, section.key)}</em>
              </button>
            ))}
          </div>
          <div className="report-v2-output">
            <button type="button" onClick={onGenerateWeeklyReport} disabled={!onGenerateWeeklyReport}><Icon name="spark" />生成周报</button>
            <button type="button" onClick={copyReport}><Icon name="file" />复制报告</button>
            <button type="button" onClick={onExportPDF} disabled={!onExportPDF}><Icon name="download" />导出 PDF</button>
            <button type="button" onClick={onExportCSV} disabled={!onExportCSV}><Icon name="download" />导出 CSV</button>
          </div>
          <div className="report-v2-evidence-actions">
            <button type="button" onClick={() => onOpenEvidence('gmv')}><Icon name="info" />GMV 证据</button>
            <button type="button" onClick={() => onOpenEvidence('views')}><Icon name="info" />曝光证据</button>
            {viewMode === 'manager' ? <button type="button" onClick={() => onOpenEvidence('cost')}><Icon name="info" />成本证据</button> : null}
          </div>
          {message ? <p className="report-v2-message">{message}</p> : null}
        </aside>

        <article className={`report-v2-preview is-${format}`}>
          <header>
            <span>{format === 'visual' ? '预览 · 图表版' : '预览 · Markdown'}</span>
            <b>{selectedSectionCount} / {REPORT_SECTIONS.length} sections</b>
          </header>
          {format === 'visual' ? (
            <div className="report-v2-visual">
              <div className="report-v2-visual-head">
                <span>VILTROX MARKETING</span>
                <h2>{language === 'zh' ? `V-KPI ${period === 'weekly' ? '周报' : '月报'}` : `V-KPI ${period === 'weekly' ? 'Weekly' : 'Monthly'} Report`}</h2>
                <p>{data.rangeLabel} · {data.dataStatus} · {data.dataNotice || '待接入'}</p>
              </div>
              <div className="report-v2-visual-kpis">
                <ReportMetric label="GMV" value={metricValue(gmv)} meta={gmv?.deltaLabel || '待 Shopify'} />
                <ReportMetric label="ROI" value={metricValue(roi)} meta={roi?.deltaLabel || '待成本'} />
                <ReportMetric label="曝光" value={metricValue(views)} meta={views?.deltaLabel || '证据'} />
                <ReportMetric label="内容" value={metricValue(content)} meta={content?.deltaLabel || '证据'} />
              </div>
              <div className="report-v2-visual-sections">
                {REPORT_SECTIONS.filter((section) => sections[section.key]).map((section) => (
                  <div key={section.key}>
                    <strong>{language === 'zh' ? section.zh : section.en}</strong>
                    <span>{sectionStatus(data, section.key)}</span>
                  </div>
                ))}
              </div>
              <pre>{reportText}</pre>
            </div>
          ) : <pre>{reportText}</pre>}
        </article>
      </section>

      <section className="report-v2-grid">
        <article className="report-v2-ledger">
          <header><span>KPI Ledger</span><b>{safeCount(data.kpiLedger.length)} 条</b></header>
          <div className="report-v2-ledger-runner">
            <label>计入日期<input value={ledgerDate} onChange={(event) => setLedgerDate(event.target.value)} /></label>
            <button type="button" disabled={busy || !onRunKpiRollup} onClick={() => void runRollup()}><Icon name="analytics" />{busy ? '计入中...' : '按真实动作计入'}</button>
          </div>
          <div className="report-v2-ledger-list">
            {data.kpiLedger.slice(0, 8).map((row) => (
              <div key={row.id}>
                <strong>{row.staffName || row.staffId || '未知员工'}</strong>
                <span>{row.metricLabel || row.metricKey || '动作'} · {row.metricValue ?? '--'}</span>
              </div>
            ))}
            {!data.kpiLedger.length ? <div className="is-empty">暂无 Ledger 明细</div> : null}
          </div>
        </article>
      </section>

      <section className="report-v2-table">
        <header>
          <span>导出基础明细</span>
          <b>项目 {data.projects.length} / 短链 {data.links.length} / 归因 {data.attributions.length} / 成本 {data.costs.length}</b>
        </header>
        <div>
          {data.projects.slice(0, 8).map((project) => (
            <button key={project.id} type="button" onClick={() => onSelectProject(project)}>
              <strong>{project.campaign || project.kolName}</strong>
              <span>{project.platform} · {project.stage} · GMV {project.gmv ? `$${project.gmv.toLocaleString('en-US')}` : '--'}</span>
            </button>
          ))}
          {!data.projects.length ? <p>暂无项目明细</p> : null}
        </div>
      </section>
    </div>
  );
}

export default ReportCenterV2Page;
