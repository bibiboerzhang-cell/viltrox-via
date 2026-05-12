import { useState } from 'react';
import type { VkpiDashboardData, VkpiMetricEvidenceKey } from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { KpiLedgerTable } from '../tables/KpiLedgerTable';
import { ProjectTable } from '../tables/ProjectTable';
import { PageShell } from './PageShell';

interface ReportsPageProps {
  data: VkpiDashboardData;
  viewMode: 'manager' | 'employee';
  onExportPDF?: () => void;
  onExportCSV?: () => void;
  onGenerateWeeklyReport?: () => void;
  onOpenEvidence: (metric: VkpiMetricEvidenceKey, metricValueId?: number | null) => void;
  onRunKpiRollup?: (ledgerDate?: string) => Promise<void>;
}

export function ReportsPage({ data, viewMode, onExportPDF, onExportCSV, onGenerateWeeklyReport, onOpenEvidence, onRunKpiRollup }: ReportsPageProps) {
  const [ledgerDate, setLedgerDate] = useState(new Date().toISOString().slice(0, 10));
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const runRollup = async () => {
    if (!onRunKpiRollup) return;
    setBusy(true);
    try {
      await onRunKpiRollup(ledgerDate || undefined);
      setMessage('KPI 工作量已按当天真实动作、内容、短链、成本和销售重新计入。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'KPI 计入失败');
    } finally {
      setBusy(false);
    }
  };
  return (
    <PageShell title="KPI 工作量 / 报表 / 导出" description="KPI Ledger 用真实 KOL、项目阶段、短链、内容、成本和销售记录计入员工工作量，报表导出不使用假数据。">
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card"><CardHeader title="管理层周报" /><p className="vkpi-summary-text">{data.weeklySummary}</p><button className="vkpi-button vkpi-button--primary" type="button" onClick={onGenerateWeeklyReport} disabled={!onGenerateWeeklyReport}>生成周报</button></section>
        <section className="vkpi-card vkpi-action-card"><CardHeader title="文件导出" /><button className="vkpi-button" type="button" onClick={onExportPDF} disabled={!onExportPDF}>导出 PDF</button><button className="vkpi-button" type="button" onClick={onExportCSV} disabled={!onExportCSV}>导出 CSV</button></section>
        <section className="vkpi-card vkpi-action-card"><CardHeader title="证据下钻" /><button className="vkpi-button" type="button" onClick={() => onOpenEvidence('gmv')}>销售额证据</button>{viewMode === 'manager' ? <button className="vkpi-button" type="button" onClick={() => onOpenEvidence('cost')}>成本证据</button> : null}<span className="vkpi-help-text">{viewMode === 'manager' ? 'ROI 暂不作为一线指标，后续按管理层确认的计算口径再开启。' : '员工视角只展示本人销售、项目、短链和工作量证据，不展示内部成本。'}</span></section>
      </section>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="员工 KPI 工作量计入" />
        <div className="vkpi-form-grid">
          <label>计入日期<input value={ledgerDate} onChange={(event) => setLedgerDate(event.target.value)} placeholder="2026-05-07" /></label>
          <button className="vkpi-button vkpi-button--primary" type="button" disabled={busy || !onRunKpiRollup} onClick={() => void runRollup()}>按当天真实动作生成 Ledger</button>
        </div>
        {message ? <div className="vkpi-inline-message">{message}</div> : null}
      </section>
      <section className="vkpi-card vkpi-table-card"><div className="vkpi-table-card__header"><div><h2>KPI Ledger 明细</h2><span>{data.kpiLedger.length} 条</span></div></div><KpiLedgerTable rows={data.kpiLedger} /></section>
      <section className="vkpi-card vkpi-table-card"><div className="vkpi-table-card__header"><div><h2>导出基础明细</h2><span>项目 {data.projects.length} / 短链 {data.links.length} / 归因 {data.attributions.length} / 成本 {data.costs.length}</span></div></div><ProjectTable projects={data.projects} selectedProjectId={data.projects[0]?.id} viewMode={viewMode} onSelectProject={() => undefined} /></section>
    </PageShell>
  );
}
