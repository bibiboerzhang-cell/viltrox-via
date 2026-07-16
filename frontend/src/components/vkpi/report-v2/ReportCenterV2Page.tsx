import { useCallback, useEffect, useMemo, useState } from 'react';
import { Archive, Eye, RefreshCw, RotateCcw } from 'lucide-react';
import type {
  VkpiDashboardData,
  VkpiMetricCard,
  VkpiMetricEvidenceKey,
  VkpiPageKey,
  VkpiProjectRow,
} from '../vkpiTypes';
import {
  VKPI_REPORT_SECTION_KEYS,
  archiveVkpiReport,
  createVkpiReportExport,
  downloadVkpiFile,
  generateVkpiReport,
  listVkpiReports,
  reportApiErrorMessage,
  reportApiErrorStatus,
  reportModelPolicyLabel,
  restoreVkpiReport,
  vkpiReportDownloadPath,
  type VkpiGeneratedReport,
  type VkpiReportExportFormat,
  type VkpiReportGenerateConfig,
  type VkpiReportHistoryItem,
  type VkpiReportLanguage,
  type VkpiReportLayout,
  type VkpiReportPeriod,
  type VkpiReportScope,
  type VkpiReportSectionKey,
} from '../../../services/vkpi/reports-api';
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

interface ReportPreview {
  report: VkpiReportHistoryItem;
  generated?: VkpiGeneratedReport;
}

interface ReportNotice {
  tone: 'success' | 'error' | 'permission' | 'info';
  text: string;
}

const REPORT_SECTIONS: Array<{ key: VkpiReportSectionKey; zh: string; en: string }> = [
  { key: 'kpiOverview', zh: 'KPI 总览', en: 'KPI Overview' },
  { key: 'attribution', zh: '归因闭环', en: 'Attribution' },
  { key: 'projects', zh: '项目明细', en: 'Projects' },
  { key: 'ledger', zh: 'KPI Ledger', en: 'KPI Ledger' },
  { key: 'risks', zh: '风险提醒', en: 'Risks' },
  { key: 'summary', zh: '管理摘要', en: 'Summary' },
];

const DEFAULT_REPORT_SECTIONS = Object.fromEntries(
  VKPI_REPORT_SECTION_KEYS.map((key) => [key, true]),
) as Record<VkpiReportSectionKey, boolean>;

const UNKNOWN_DATA_STATUSES = new Set(['', 'unknown', 'awaiting_source', 'empty', 'unavailable']);

function todayInputValue(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}

function metricByKey(metrics: VkpiMetricCard[], key: string): VkpiMetricCard | undefined {
  return metrics.find((metric) => metric.key === key || metric.label.toLowerCase().includes(key.toLowerCase()));
}

function metricValue(metric?: VkpiMetricCard): string {
  if (!metric || metric.deltaLabel.includes('未生成快照')) return '待数据';
  const clean = String(metric.value ?? '').trim();
  if (!clean || ['--', '—', '未知', 'null', 'n/a'].includes(clean.toLowerCase())) return '待数据';
  return clean;
}

function countValue(value: number, dataStatus: VkpiDashboardData['dataStatus']): string {
  if (value === 0 && dataStatus === 'empty') return '待数据';
  return value.toLocaleString('en-US');
}

function sectionStatus(data: VkpiDashboardData, key: VkpiReportSectionKey): string {
  const gmv = metricByKey(data.metrics, 'gmv');
  const roi = metricByKey(data.metrics, 'roi');
  if (key === 'kpiOverview') {
    const known = data.metrics.filter((metric) => metricValue(metric) !== '待数据').length;
    return known ? `${known} 指标` : '待数据';
  }
  if (key === 'attribution') {
    if (metricValue(gmv) !== '待数据' || metricValue(roi) !== '待数据' || data.attributions.length || data.costs.length) return '有信号';
    return data.dataStatus === 'empty' ? '待数据' : '0 条';
  }
  if (key === 'projects') return data.projects.length ? `${data.projects.length} 项` : data.dataStatus === 'empty' ? '待数据' : '0 项';
  if (key === 'ledger') return data.kpiLedger.length ? `${data.kpiLedger.length} 条` : data.dataStatus === 'empty' ? '待数据' : '0 条';
  if (key === 'risks') return data.alerts.length ? `${data.alerts.length} 条` : data.dataStatus === 'empty' ? '待数据' : '0 条';
  return data.weeklySummary && data.dataStatus !== 'empty' ? '有摘要' : '待生成';
}

function serverMetricValue(metric: VkpiGeneratedReport['metrics'][number]): string {
  if (UNKNOWN_DATA_STATUSES.has(metric.dataStatus.toLowerCase()) || metric.rawValue === null) return '待数据';
  if (metric.value === null || String(metric.value).trim() === '') return '待数据';
  return String(metric.value);
}

function reportDataStatusLabel(status: string): string {
  const normalized = status.toLowerCase();
  if (UNKNOWN_DATA_STATUSES.has(normalized)) return '待数据';
  if (normalized === 'real') return '真实数据';
  if (normalized === 'partial') return '部分数据';
  if (normalized === 'seeded') return '种子数据';
  return status || '待数据';
}

function reportStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    ready: '已就绪',
    failed: '生成失败',
    rendering: '生成中',
    queued: '排队中',
    archived: '已归档',
  };
  return labels[status.toLowerCase()] || status || '状态未知';
}

function formatMoment(value: string): string {
  if (!value) return '待数据';
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return value.slice(0, 16).replace('T', ' ');
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(moment);
}

function periodRange(report: VkpiReportHistoryItem): string {
  const start = report.periodStart.slice(0, 10);
  const end = report.periodEnd.slice(0, 10);
  if (!start && !end) return '日期待数据';
  return start && end ? `${start} 至 ${end}` : start || end;
}

function reportTypeLabel(type: string): string {
  return type.toLowerCase() === 'monthly' ? '月报' : type.toLowerCase() === 'weekly' ? '周报' : type || '报告';
}

function projectGmv(project: VkpiProjectRow): string {
  return project.gmv === null ? '待数据' : `$${project.gmv.toLocaleString('en-US')}`;
}

function ledgerMetricValue(value: unknown): string {
  return value === null || value === undefined || value === '' ? '待数据' : String(value);
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
  userName = 'Viltrox 成员',
  userRole = '营销运营',
  userAvatar,
  onSelectProject,
  onSelectPage,
  onOpenEvidence,
  onRunKpiRollup,
  onSignOut,
}: ReportCenterV2PageProps) {
  const today = useMemo(todayInputValue, []);
  const [ledgerDate, setLedgerDate] = useState(today);
  const [reportDate, setReportDate] = useState(today);
  const [language, setLanguage] = useState<VkpiReportLanguage>('zh');
  const [period, setPeriod] = useState<VkpiReportPeriod>('weekly');
  const [format, setFormat] = useState<VkpiReportLayout>('visual');
  const [scope, setScope] = useState<VkpiReportScope>(viewMode === 'manager' ? 'all' : 'self');
  const [sections, setSections] = useState<Record<VkpiReportSectionKey, boolean>>(DEFAULT_REPORT_SECTIONS);
  const [notice, setNotice] = useState<ReportNotice | null>(null);
  const [rollupBusy, setRollupBusy] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [exporting, setExporting] = useState<VkpiReportExportFormat | null>(null);
  const [historyArchived, setHistoryArchived] = useState(false);
  const [history, setHistory] = useState<VkpiReportHistoryItem[]>([]);
  const [historyCount, setHistoryCount] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [historyBusyId, setHistoryBusyId] = useState<number | null>(null);
  const [archiveArmedId, setArchiveArmedId] = useState<number | null>(null);
  const [preview, setPreview] = useState<ReportPreview | null>(null);

  const gmv = metricByKey(data.metrics, 'gmv');
  const roi = metricByKey(data.metrics, 'roi');
  const views = metricByKey(data.metrics, 'views');
  const content = metricByKey(data.metrics, 'published_content') || metricByKey(data.metrics, '内容');
  const selectedSectionKeys = useMemo(
    () => REPORT_SECTIONS.filter((section) => sections[section.key]).map((section) => section.key),
    [sections],
  );
  const config = useMemo<VkpiReportGenerateConfig>(() => ({
    period,
    date: reportDate,
    language,
    sections: selectedSectionKeys,
    format,
    scope,
  }), [format, language, period, reportDate, scope, selectedSectionKeys]);

  useEffect(() => {
    if (viewMode === 'employee') setScope('self');
  }, [viewMode]);

  const setFailure = useCallback((error: unknown, fallback: string) => {
    const status = reportApiErrorStatus(error);
    setNotice({
      tone: status === 401 || status === 403 ? 'permission' : 'error',
      text: reportApiErrorMessage(error, fallback),
    });
  }, []);

  const refreshHistory = useCallback(async (archived: boolean) => {
    if (!apiToken) {
      setHistory([]);
      setHistoryCount(0);
      setHistoryError('缺少登录凭证，无法读取报告历史。');
      return [] as VkpiReportHistoryItem[];
    }
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const result = await listVkpiReports(apiToken, archived);
      setHistory(result.reports);
      setHistoryCount(result.count);
      return result.reports;
    } catch (error) {
      setHistory([]);
      setHistoryCount(0);
      setHistoryError(reportApiErrorMessage(error, '报告历史加载失败'));
      return [] as VkpiReportHistoryItem[];
    } finally {
      setHistoryLoading(false);
    }
  }, [apiToken]);

  useEffect(() => {
    void refreshHistory(historyArchived);
  }, [historyArchived, refreshHistory]);

  const runRollup = async () => {
    if (!onRunKpiRollup) return;
    setRollupBusy(true);
    setNotice(null);
    try {
      await onRunKpiRollup(ledgerDate || undefined);
      setNotice({ tone: 'success', text: 'KPI Ledger 已按真实动作重新计入。' });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : 'KPI 计入失败' });
    } finally {
      setRollupBusy(false);
    }
  };

  const toggleSection = (key: VkpiReportSectionKey) => {
    setPreview(null);
    setSections((current) => ({ ...current, [key]: !current[key] }));
  };

  const createPreview = (result: VkpiGeneratedReport): ReportPreview => ({
    generated: result,
    report: {
      id: result.reportId ?? 0,
      reportUid: result.reportUid || `report-${result.reportId || reportDate}`,
      reportType: result.reportType || period,
      periodStart: result.periodStart,
      periodEnd: result.periodEnd || reportDate,
      scopeType: scope,
      scopeId: null,
      triggeredAt: new Date().toISOString(),
      status: result.status,
      summary: result.summary,
      dataStatus: result.dataStatus,
      schemaVersion: '',
      archivedAt: '',
      archiveReason: '',
      truthInvalidated: false,
      truthInvalidationReason: '',
      modelPolicy: result.modelPolicy,
      claimLevel: result.claimLevel,
    },
  });

  const runGenerate = async () => {
    if (!apiToken) {
      setNotice({ tone: 'permission', text: '缺少登录凭证，无法生成报告。' });
      return;
    }
    if (!selectedSectionKeys.length) {
      setNotice({ tone: 'error', text: '至少选择一个报告章节。' });
      return;
    }
    setGenerating(true);
    setNotice({ tone: 'info', text: '正在由服务端生成报告…' });
    try {
      const result = await generateVkpiReport(apiToken, config);
      setPreview(createPreview(result));
      setHistoryArchived(false);
      const nextHistory = await refreshHistory(false);
      const persisted = nextHistory.find((item) => item.id === result.reportId);
      if (persisted) setPreview({ report: persisted, generated: result });
      if (result.status.toLowerCase() !== 'ready') {
        setNotice({ tone: 'error', text: `报告接口返回“${reportStatusLabel(result.status)}”，未确认生成成功。` });
      } else if (!result.downloadUrl) {
        setNotice({ tone: 'info', text: `报告记录已生成，但接口没有返回下载链接。${reportModelPolicyLabel(result.modelPolicy, result.claimLevel)}` });
      } else {
        setNotice({ tone: 'success', text: `报告已由服务端生成，文件可下载。${reportModelPolicyLabel(result.modelPolicy, result.claimLevel)}` });
      }
    } catch (error) {
      setFailure(error, '报告生成失败');
    } finally {
      setGenerating(false);
    }
  };

  const runExport = async (exportFormat: VkpiReportExportFormat) => {
    if (!apiToken || exporting) return;
    if (!selectedSectionKeys.length) {
      setNotice({ tone: 'error', text: '至少选择一个报告章节。' });
      return;
    }
    setExporting(exportFormat);
    setNotice({ tone: 'info', text: `正在生成 ${exportFormat.toUpperCase()} 导出…` });
    try {
      const result = await createVkpiReportExport(apiToken, exportFormat, config);
      if (!result.downloadUrl) {
        setNotice({ tone: 'info', text: `${exportFormat.toUpperCase()} 导出任务已提交，但接口没有返回下载链接。` });
        return;
      }
      await downloadVkpiFile(apiToken, result.downloadUrl, `vkpi-${period}-${reportDate}.${exportFormat}`);
      setNotice({ tone: 'success', text: `${exportFormat.toUpperCase()} 导出已下载。` });
    } catch (error) {
      setFailure(error, `${exportFormat.toUpperCase()} 导出失败`);
    } finally {
      setExporting(null);
    }
  };

  const copyServerSummary = async () => {
    const summary = preview?.report.summary.trim();
    if (!summary) {
      setNotice({ tone: 'info', text: '当前没有服务端摘要可复制。' });
      return;
    }
    await navigator.clipboard.writeText(summary);
    setNotice({ tone: 'success', text: '服务端报告摘要已复制。' });
  };

  const downloadReport = async (report: VkpiReportHistoryItem, downloadUrl = '') => {
    if (!apiToken || report.status.toLowerCase() !== 'ready' || report.id < 1) return;
    setHistoryBusyId(report.id);
    setNotice({ tone: 'info', text: '正在下载报告文件…' });
    try {
      await downloadVkpiFile(
        apiToken,
        downloadUrl || vkpiReportDownloadPath(report.id, 'pdf'),
        `${report.reportUid || `report-${report.id}`}.pdf`,
      );
      setNotice({ tone: 'success', text: '报告文件已下载。' });
    } catch (error) {
      setFailure(error, '报告下载失败');
    } finally {
      setHistoryBusyId(null);
    }
  };

  const archiveReport = async (report: VkpiReportHistoryItem) => {
    if (!apiToken) return;
    if (archiveArmedId !== report.id) {
      setArchiveArmedId(report.id);
      return;
    }
    setHistoryBusyId(report.id);
    setArchiveArmedId(null);
    try {
      await archiveVkpiReport(apiToken, report.id);
      if (preview?.report.id === report.id) setPreview(null);
      await refreshHistory(false);
      setNotice({ tone: 'success', text: '报告已软归档，可在“已归档”中恢复。' });
    } catch (error) {
      setFailure(error, '报告归档失败');
    } finally {
      setHistoryBusyId(null);
    }
  };

  const restoreReport = async (report: VkpiReportHistoryItem) => {
    if (!apiToken) return;
    setHistoryBusyId(report.id);
    try {
      await restoreVkpiReport(apiToken, report.id);
      if (preview?.report.id === report.id) setPreview(null);
      await refreshHistory(true);
      setNotice({ tone: 'success', text: '报告已恢复到当前历史。' });
    } catch (error) {
      setFailure(error, '报告恢复失败');
    } finally {
      setHistoryBusyId(null);
    }
  };

  const previewSummary = preview?.report.summary.trim()
    || (preview?.report.status.toLowerCase() === 'failed'
      ? '报告生成失败，服务端没有产出摘要或下载文件。'
      : '服务端没有返回报告摘要。');

  return (
    <div className="report-v2">
      <V2ShellTopbar
        apiToken={apiToken}
        pageTitle="Report Center"
        subtitle="真实数据 / 证据 / 报告历史 / KPI Ledger"
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
          <p>统一生成 / 真实历史 / 可恢复归档 / 鉴权下载</p>
        </div>
        <div className={`report-v2-status is-${data.dataStatus}`}>
          <b>{data.dataStatus === 'live' ? '真实 API' : data.dataStatus === 'partial' ? '部分数据' : '待数据'}</b>
          <span>{data.dataNotice || '数据状态待确认'}</span>
        </div>
      </header>

      <section className="report-v2-metrics">
        <ReportMetric label="GMV" value={metricValue(gmv)} meta={gmv?.deltaLabel || '待 Shopify 完整闭环'} />
        <ReportMetric label="平均 ROI" value={metricValue(roi)} meta={roi?.deltaLabel || '待成本与订单归因'} />
        <ReportMetric label="曝光量" value={metricValue(views)} meta={views?.deltaLabel || '内容曝光证据'} />
        <ReportMetric label="内容数" value={metricValue(content)} meta={content?.deltaLabel || '发布内容证据'} />
        <ReportMetric label="项目" value={countValue(data.projects.length, data.dataStatus)} meta="projects" />
        <ReportMetric label="KPI Ledger" value={countValue(data.kpiLedger.length, data.dataStatus)} meta="真实动作计入" />
      </section>

      <section className="report-v2-builder">
        <aside className="report-v2-builder-panel">
          <div className="report-v2-control">
            <span>语言</span>
            <div>
              <button type="button" className={language === 'zh' ? 'is-active' : ''} onClick={() => { setLanguage('zh'); setPreview(null); }}>中文</button>
              <button type="button" className={language === 'en' ? 'is-active' : ''} onClick={() => { setLanguage('en'); setPreview(null); }}>English</button>
            </div>
          </div>
          <div className="report-v2-control">
            <span>周期</span>
            <div>
              <button type="button" className={period === 'weekly' ? 'is-active' : ''} onClick={() => { setPeriod('weekly'); setPreview(null); }}>周报</button>
              <button type="button" className={period === 'monthly' ? 'is-active' : ''} onClick={() => { setPeriod('monthly'); setPreview(null); }}>月报</button>
            </div>
          </div>
          <label className="report-v2-date-control">
            <span>截止日期</span>
            <input aria-label="报告截止日期" type="date" max={today} value={reportDate} onChange={(event) => { setReportDate(event.target.value); setPreview(null); }} />
          </label>
          <div className="report-v2-control">
            <span>版式</span>
            <div>
              <button type="button" className={format === 'visual' ? 'is-active' : ''} onClick={() => { setFormat('visual'); setPreview(null); }}>图表版</button>
              <button type="button" className={format === 'markdown' ? 'is-active' : ''} onClick={() => { setFormat('markdown'); setPreview(null); }}>Markdown</button>
            </div>
          </div>
          <div className="report-v2-control">
            <span>数据范围</span>
            <div>
              <button type="button" className={scope === 'self' ? 'is-active' : ''} onClick={() => { setScope('self'); setPreview(null); }}>仅本人</button>
              <button type="button" className={scope === 'all' ? 'is-active' : ''} disabled={viewMode !== 'manager'} onClick={() => { setScope('all'); setPreview(null); }}>全部可见数据</button>
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
            <button type="button" className="is-primary" onClick={() => void runGenerate()} disabled={generating || exporting !== null || !apiToken}><Icon name="spark" />{generating ? '生成中…' : '生成报告'}</button>
            <button type="button" onClick={() => void copyServerSummary()} disabled={!preview?.report.summary}><Icon name="file" />复制摘要</button>
            <button type="button" onClick={() => void runExport('pdf')} disabled={generating || exporting !== null || !apiToken}><Icon name="download" />{exporting === 'pdf' ? '导出中…' : '导出 PDF'}</button>
            <button type="button" onClick={() => void runExport('csv')} disabled={generating || exporting !== null || !apiToken}><Icon name="table" />{exporting === 'csv' ? '导出中…' : '导出 CSV'}</button>
            <button type="button" onClick={() => void runExport('xlsx')} disabled={generating || exporting !== null || !apiToken}><Icon name="table" />{exporting === 'xlsx' ? '导出中…' : '导出 XLSX'}</button>
            {preview?.report.status.toLowerCase() === 'ready' && preview.report.id > 0 ? (
              <button type="button" onClick={() => void downloadReport(preview.report, preview.generated?.downloadUrl)} disabled={historyBusyId === preview.report.id}><Icon name="download" />下载报告</button>
            ) : null}
          </div>
          <div className="report-v2-evidence-actions">
            <button type="button" onClick={() => onOpenEvidence('gmv')}><Icon name="info" />GMV 证据</button>
            <button type="button" onClick={() => onOpenEvidence('views')}><Icon name="info" />曝光证据</button>
            {viewMode === 'manager' ? <button type="button" onClick={() => onOpenEvidence('cost')}><Icon name="info" />成本证据</button> : null}
          </div>
          {notice ? <p className={`report-v2-message is-${notice.tone}`} role={notice.tone === 'error' || notice.tone === 'permission' ? 'alert' : 'status'}>{notice.text}</p> : null}
        </aside>

        <article className={`report-v2-preview is-${format}`}>
          <header>
            <span>{preview ? '服务端报告' : '提交前配置'}</span>
            <b>{preview ? reportStatusLabel(preview.report.status) : `${selectedSectionKeys.length} / ${REPORT_SECTIONS.length} sections`}</b>
          </header>
          {preview ? (
            <div className="report-v2-server-preview">
              <div className="report-v2-visual-head">
                <span>SERVER REPORT</span>
                <h2>{reportTypeLabel(preview.report.reportType)} · {preview.report.reportUid}</h2>
                <p>{periodRange(preview.report)} · {reportDataStatusLabel(preview.report.dataStatus)} · {reportModelPolicyLabel(preview.generated?.modelPolicy ?? preview.report.modelPolicy, preview.generated?.claimLevel ?? preview.report.claimLevel)} · {formatMoment(preview.report.triggeredAt)}</p>
              </div>
              {preview.generated?.metrics.length ? (
                <div className="report-v2-server-metrics">
                  {preview.generated.metrics.map((metric) => (
                    <ReportMetric key={metric.key} label={metric.label || metric.key} value={serverMetricValue(metric)} meta={metric.note || reportDataStatusLabel(metric.dataStatus)} />
                  ))}
                </div>
              ) : null}
              <pre>{previewSummary}</pre>
            </div>
          ) : (
            <div className="report-v2-visual">
              <div className="report-v2-visual-head">
                <span>VILTROX MARKETING</span>
                <h2>{language === 'zh' ? `V-KPI ${period === 'weekly' ? '周报' : '月报'}` : `V-KPI ${period === 'weekly' ? 'Weekly' : 'Monthly'} Report`}</h2>
                <p>{reportDate} · {scope === 'all' ? '全部可见数据' : '仅本人'} · {format === 'visual' ? '图表版' : 'Markdown'}</p>
              </div>
              <div className="report-v2-visual-kpis">
                <ReportMetric label="GMV" value={metricValue(gmv)} meta={gmv?.deltaLabel || '待 Shopify'} />
                <ReportMetric label="ROI" value={metricValue(roi)} meta={roi?.deltaLabel || '待成本'} />
                <ReportMetric label="曝光" value={metricValue(views)} meta={views?.deltaLabel || '待证据'} />
                <ReportMetric label="内容" value={metricValue(content)} meta={content?.deltaLabel || '待证据'} />
              </div>
              <div className="report-v2-visual-sections">
                {REPORT_SECTIONS.filter((section) => sections[section.key]).map((section) => (
                  <div key={section.key}>
                    <strong>{language === 'zh' ? section.zh : section.en}</strong>
                    <span>{sectionStatus(data, section.key)}</span>
                  </div>
                ))}
              </div>
              <p className="report-v2-config-note">此处只核对提交配置和当前数据状态；报告正文由服务端生成后显示。</p>
            </div>
          )}
        </article>
      </section>

      <section className="report-v2-history">
        <header>
          <div>
            <span>报告历史</span>
            <b>{historyArchived ? '已归档' : '当前报告'} · {historyCount}</b>
          </div>
          <div className="report-v2-history-toolbar">
            <div role="group" aria-label="报告历史范围">
              <button type="button" className={!historyArchived ? 'is-active' : ''} onClick={() => { setHistoryArchived(false); setArchiveArmedId(null); }}>当前</button>
              <button type="button" className={historyArchived ? 'is-active' : ''} onClick={() => { setHistoryArchived(true); setArchiveArmedId(null); }}>已归档</button>
            </div>
            <button type="button" className="is-icon" aria-label="刷新报告历史" title="刷新报告历史" onClick={() => void refreshHistory(historyArchived)} disabled={historyLoading}>
              <RefreshCw size={16} aria-hidden="true" />
            </button>
          </div>
        </header>
        {historyError ? <p className="report-v2-history-error" role="alert">{historyError}</p> : null}
        <div className="report-v2-history-list" aria-busy={historyLoading}>
          {historyLoading && !history.length ? <p className="is-empty">正在读取真实报告历史…</p> : null}
          {!historyLoading && !history.length && !historyError ? <p className="is-empty">{historyArchived ? '没有已归档报告' : '尚未生成真实报告'}</p> : null}
          {history.map((report) => {
            const busy = historyBusyId === report.id;
            const isFailed = report.status.toLowerCase() === 'failed';
            return (
              <article key={report.id} className={preview?.report.id === report.id ? 'is-selected' : ''}>
                <div className="report-v2-history-main">
                  <div>
                    <strong>{reportTypeLabel(report.reportType)} · {report.reportUid}</strong>
                    <span>{periodRange(report)} · {formatMoment(report.triggeredAt)}</span>
                  </div>
                  <div className="report-v2-history-badges">
                    <b className={`is-${report.status.toLowerCase()}`}>{reportStatusLabel(report.status)}</b>
                    <b className={UNKNOWN_DATA_STATUSES.has(report.dataStatus.toLowerCase()) ? 'is-pending' : 'is-data'}>{reportDataStatusLabel(report.dataStatus)}</b>
                    <b className="is-pending">{reportModelPolicyLabel(report.modelPolicy, report.claimLevel)}</b>
                  </div>
                </div>
                <p>{report.truthInvalidated ? '该历史报告已因真实业务口径升级撤销，仅保留审计记录，不可恢复或下载。' : report.summary || (isFailed ? '生成失败，服务端没有产出摘要。' : '服务端没有返回摘要。')}</p>
                <div className="report-v2-history-actions">
                  <button type="button" onClick={() => { setPreview({ report }); setArchiveArmedId(null); }}><Eye size={15} aria-hidden="true" />重开</button>
                  {!historyArchived && report.status.toLowerCase() === 'ready' ? (
                    <button type="button" onClick={() => void downloadReport(report)} disabled={busy}><Icon name="download" />{busy ? '下载中…' : '下载'}</button>
                  ) : null}
                  {!historyArchived && ['ready', 'failed'].includes(report.status.toLowerCase()) ? (
                    <button type="button" className={archiveArmedId === report.id ? 'is-danger-armed' : 'is-danger'} onClick={() => void archiveReport(report)} disabled={busy}>
                      <Archive size={15} aria-hidden="true" />{archiveArmedId === report.id ? '确认归档' : '归档'}
                    </button>
                  ) : null}
                  {historyArchived && !report.truthInvalidated ? (
                    <button type="button" onClick={() => void restoreReport(report)} disabled={busy}><RotateCcw size={15} aria-hidden="true" />{busy ? '恢复中…' : '恢复'}</button>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <section className="report-v2-grid">
        <article className="report-v2-ledger">
          <header><span>KPI Ledger</span><b>{countValue(data.kpiLedger.length, data.dataStatus)} 条</b></header>
          <div className="report-v2-ledger-runner">
            <label>计入日期<input type="date" value={ledgerDate} onChange={(event) => setLedgerDate(event.target.value)} /></label>
            <button type="button" disabled={rollupBusy || !onRunKpiRollup} onClick={() => void runRollup()}><Icon name="analytics" />{rollupBusy ? '计入中…' : '按真实动作计入'}</button>
          </div>
          <div className="report-v2-ledger-list">
            {data.kpiLedger.slice(0, 8).map((row) => (
              <div key={row.id}>
                <strong>{row.staffName || row.staffId || '未知成员'}</strong>
                <span>{row.metricLabel || row.metricKey || '动作'} · {ledgerMetricValue(row.metricValue)}</span>
              </div>
            ))}
            {!data.kpiLedger.length ? <div className="is-empty">{data.dataStatus === 'empty' ? 'KPI Ledger 待数据' : '本周期 0 条 Ledger 明细'}</div> : null}
          </div>
        </article>
      </section>

      <section className="report-v2-table">
        <header>
          <span>导出基础明细</span>
          <b>项目 {countValue(data.projects.length, data.dataStatus)} / 短链 {countValue(data.links.length, data.dataStatus)} / 归因 {countValue(data.attributions.length, data.dataStatus)} / 成本 {countValue(data.costs.length, data.dataStatus)}</b>
        </header>
        <div>
          {data.projects.slice(0, 8).map((project) => (
            <button key={project.id} type="button" onClick={() => onSelectProject(project)}>
              <strong>{project.campaign || project.kolName}</strong>
              <span>{project.platform} · {project.stage} · GMV {projectGmv(project)}</span>
            </button>
          ))}
          {!data.projects.length ? <p>{data.dataStatus === 'empty' ? '项目明细待数据' : '本周期 0 个项目'}</p> : null}
        </div>
      </section>
    </div>
  );
}

export default ReportCenterV2Page;
