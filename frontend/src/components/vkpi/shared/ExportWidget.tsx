import type { VkpiDashboardData } from '../vkpiTypes';
import { Icon } from './Icon';

export function ExportWidget({ report, onDownloadPDF }: { report: VkpiDashboardData['exportReport']; onDownloadPDF?: () => void }) {
  const statusLabel = report.status === 'Ready' ? '已就绪' : report.status === 'Failed' ? '失败' : '生成中';
  return (
    <section className="vkpi-card vkpi-panel-card">
      <div className="vkpi-card__header">
        <h2>报表与导出</h2>
      </div>
      <div className="vkpi-export-row">
        <div className="vkpi-pdf-icon">PDF</div>
        <div>
          <strong>{report.title}</strong>
          <span>{report.generatedAt}</span>
        </div>
        <em className={`is-${report.status.toLowerCase()}`}>{statusLabel}</em>
      </div>
      <button className="vkpi-button" type="button" onClick={onDownloadPDF}>
        <Icon name="download" />
        下载 PDF
      </button>
      <button className="vkpi-link-button" type="button">查看全部报表</button>
    </section>
  );
}
