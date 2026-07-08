import { useMemo, useState } from 'react';
import { exportVkpiReport, generateWeeklyReport, rows, type DashboardScope, type KpiCard, type Row, type Snapshot } from '../../../../domains/dashboard';
import { buildMissionReportHtml, buildMissionReportMarkdown } from './reportBuilder';

interface V2ReportPanelProps {
  open: boolean;
  onClose: () => void;
  snapshot: Snapshot;
  alerts: Row[];
  kpis: KpiCard[];
  scopeLabel: string;
  windowDays: number;
  generatedBy: string;
  apiToken?: string;
  scope: DashboardScope;
}

export function V2ReportPanel({
  open,
  onClose,
  snapshot,
  alerts,
  kpis,
  scopeLabel,
  windowDays,
  generatedBy,
  apiToken,
  scope,
}: V2ReportPanelProps) {
  const [status, setStatus] = useState('');
  const [downloadUrl, setDownloadUrl] = useState('');
  const [exporting, setExporting] = useState<'weekly' | 'pdf' | 'csv' | null>(null);
  const input = useMemo(() => ({
    snapshot,
    alerts,
    kpis,
    scopeLabel,
    windowDays,
    generatedBy,
  }), [alerts, generatedBy, kpis, scopeLabel, snapshot, windowDays]);
  const markdown = useMemo(() => buildMissionReportMarkdown(input), [input]);
  const html = useMemo(() => buildMissionReportHtml(input), [input]);

  if (!open) return null;

  const copyReport = async () => {
    await navigator.clipboard.writeText(markdown);
    setStatus('已复制 Markdown');
    window.setTimeout(() => setStatus(''), 1800);
  };

  const reportRowCount = kpis.length + alerts.length + rows(snapshot.tasksStatus.tasks).length;

  const downloadHtml = () => {
    if (reportRowCount === 0) {
      setStatus('本次筛选无数据');
      window.setTimeout(() => setStatus(''), 1800);
      return;
    }
    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    const stamp = new Date().toISOString().slice(0, 19).replace(/[-:]/g, '').replace('T', '-');
    anchor.download = `vkpi-mission-control-${scope}-${windowDays}d-${stamp}.html`;
    anchor.click();
    URL.revokeObjectURL(url);
    setStatus('已生成 HTML');
    window.setTimeout(() => setStatus(''), 1800);
  };

  const printReport = () => {
    const printWindow = window.open('', '_blank', 'noopener,noreferrer,width=980,height=760');
    if (!printWindow) {
      setStatus('浏览器阻止了打印窗口');
      return;
    }
    printWindow.document.write(html);
    printWindow.document.close();
    printWindow.focus();
    printWindow.print();
  };

  const generateBackendWeekly = async () => {
    if (!apiToken) {
      setStatus('未登录，不能调用后端周报');
      return;
    }
    if (exporting !== null) return;
    setExporting('weekly');
    setStatus('后端生成中...');
    setDownloadUrl('');
    try {
      const result = await generateWeeklyReport(apiToken, { range: '30d', scope: 'all' });
      const href = result.downloadUrl || result.download_url || '';
      setDownloadUrl(href);
      setStatus(result.status ? `后端状态：${result.status}` : '后端周报已生成');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : '后端周报生成失败');
    } finally {
      setExporting(null);
    }
  };

  const exportBackend = async (format: 'pdf' | 'csv') => {
    if (!apiToken) {
      setStatus('未登录，不能调用后端导出');
      return;
    }
    if (exporting !== null) return;
    setExporting(format);
    setStatus(`${format.toUpperCase()} 导出中...`);
    setDownloadUrl('');
    try {
      const result = await exportVkpiReport(apiToken, { reportType: 'weekly', format, range: '30d', scope: 'all', includeEstimated: scope !== 'official' });
      const href = result.downloadUrl || result.download_url || '';
      setDownloadUrl(href);
      setStatus(result.status ? `导出状态：${result.status}` : `${format.toUpperCase()} 已生成`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : `${format.toUpperCase()} 导出失败`);
    } finally {
      setExporting(null);
    }
  };

  return (
    <div className="mission-report-layer" role="dialog" aria-modal="true" aria-label="V-KPI 周报">
      <div className="mission-report-backdrop" onClick={onClose} />
      <aside className="mission-report-panel">
        <header className="mission-report-panel__header">
          <div>
            <span>Report</span>
            <h2>管理主控周报</h2>
          </div>
          <button className="mission-icon-button" type="button" onClick={onClose} aria-label="关闭周报">×</button>
        </header>

        <div className="mission-report-panel__actions">
          <button type="button" onClick={copyReport}>复制 Markdown</button>
          <button type="button" onClick={downloadHtml}>导出 HTML</button>
          <button type="button" onClick={printReport}>打印 / PDF</button>
          <button type="button" onClick={() => void generateBackendWeekly()} disabled={!apiToken || exporting !== null}>{exporting === 'weekly' ? '生成中...' : '后端生成周报'}</button>
          <button type="button" onClick={() => void exportBackend('pdf')} disabled={!apiToken || exporting !== null}>{exporting === 'pdf' ? 'PDF 导出中...' : '后端 PDF'}</button>
          <button type="button" onClick={() => void exportBackend('csv')} disabled={!apiToken || exporting !== null}>{exporting === 'csv' ? 'CSV 导出中...' : '后端 CSV'}</button>
          {status ? <span>{status}</span> : null}
          {downloadUrl ? <a href={downloadUrl} target="_blank" rel="noreferrer">打开文件</a> : null}
        </div>

        <pre className="mission-report-preview">{markdown}</pre>
      </aside>
    </div>
  );
}
