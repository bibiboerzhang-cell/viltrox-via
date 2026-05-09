import React, { useEffect, useState } from 'react';
import type { VkpiDashboardData, VkpiMetricEvidenceKey } from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { CostLedgerTable } from '../tables/CostLedgerTable';
import { currencyFormatter } from '../shared/vkpiFormatters';
import { PageShell } from './PageShell';
import { getMarketingCostDetail } from '../../../services/vkpi.ui-api';
import { safeNumber, textValue } from '../shared/vkpiDataUtils';

interface CostsPageProps {
  data: VkpiDashboardData;
  viewMode: 'manager' | 'employee';
  onUpdateCost?: (costId: string, payload: { costType?: string; amountUsd?: number; note?: string; sourceRef?: string }) => Promise<void>;
  onApproveCost?: (costId: string, note?: string) => Promise<void>;
  onVoidCost?: (costId: string, reason?: string) => Promise<void>;
  onOpenEvidence: (metricKey: VkpiMetricEvidenceKey) => void;
  apiToken?: string;
}

export function CostsPage({ data, viewMode, onUpdateCost, onApproveCost, onVoidCost, onOpenEvidence, apiToken }: CostsPageProps) {
  const [costId, setCostId] = useState(data.costs[0]?.id || '');
  const [costType, setCostType] = useState('');
  const [amountUsd, setAmountUsd] = useState('');
  const [note, setNote] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const selectedCost = data.costs.find((row) => row.id === costId);
  const visibleCosts = viewMode === 'manager' ? data.costs : [];
  const loadDetail = async (id: string) => {
    if (!apiToken || !id) return;
    setDetailLoading(true);
    setDetailError('');
    try {
      setDetail(await getMarketingCostDetail(apiToken, id) as unknown as Record<string, unknown>);
    } catch (error) {
      setDetailError(error instanceof Error ? error.message : '成本详情加载失败');
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };
  useEffect(() => {
    if (viewMode === 'manager' && costId && apiToken) void loadDetail(costId);
  }, [costId, apiToken, viewMode]);
  const submitUpdate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!costId || !onUpdateCost) return;
    setBusy(true);
    try {
      await onUpdateCost(costId, {
        costType: costType || undefined,
        amountUsd: amountUsd ? Number(amountUsd) : undefined,
        note: note || undefined,
      });
      await loadDetail(costId);
      setMessage('成本记录已更新。');
      setCostType('');
      setAmountUsd('');
      setNote('');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '成本更新失败');
    } finally {
      setBusy(false);
    }
  };
  const approve = async () => {
    if (!costId || !onApproveCost) return;
    setBusy(true);
    try {
      await onApproveCost(costId, note || '管理层审核');
      await loadDetail(costId);
      setMessage('成本记录已审核。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '成本审核失败');
    } finally {
      setBusy(false);
    }
  };
  const voidRow = async () => {
    if (!costId || !onVoidCost) return;
    setBusy(true);
    try {
      await onVoidCost(costId, note || '管理层作废');
      await loadDetail(costId);
      setMessage('成本记录已作废。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '成本作废失败');
    } finally {
      setBusy(false);
    }
  };
  if (viewMode !== 'manager') {
    return <PageShell title="成本台" description="员工视角隐藏镜头单价、内部成本和财务明细。"><section className="vkpi-card"><div className="vkpi-empty-state">当前视角无权查看成本台。</div></section></PageShell>;
  }
  return (
    <PageShell title="成本台" description="管理层审核、修正、作废成本记录。镜头成本由 SKU 目录和发货动作自动计入，员工只登记快递 / 推广费。">
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="成本记录操作" />
          <form className="vkpi-form-stack" onSubmit={submitUpdate}>
            <select value={costId} onChange={(event) => setCostId(event.target.value)}>
              <option value="">选择成本记录</option>
              {visibleCosts.map((row) => <option key={row.id} value={row.id}>{row.id} · {row.projectName || row.projectId || '-'} · {row.costType} · {currencyFormatter.format(row.amount)}</option>)}
            </select>
            <select value={costType} onChange={(event) => setCostType(event.target.value)}>
              <option value="">不改类型</option>
              <option value="shipping">快递 / 物流</option>
              <option value="cash_fee">推广费用</option>
              <option value="product">镜头成本</option>
              <option value="sample">样品</option>
              <option value="customs_tax">关税 / 税</option>
              <option value="other">其他</option>
            </select>
            <input value={amountUsd} onChange={(event) => setAmountUsd(event.target.value)} placeholder="新金额 USD（留空不改）" inputMode="decimal" />
            <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="备注 / 审核理由 / 作废原因" />
            <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !costId || !onUpdateCost}>更新成本</button>
          </form>
          <div className="vkpi-row-actions">
            <button className="vkpi-button" type="button" disabled={busy || !costId || !onApproveCost} onClick={() => void approve()}>审核</button>
            <button className="vkpi-button" type="button" disabled={busy || !costId || !onVoidCost || selectedCost?.status === 'void'} onClick={() => void voidRow()}>作废</button>
          </div>
          {message ? <div className="vkpi-inline-message">{message}</div> : null}
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="成本详情 / 审计" />
          {detailLoading ? <div className="vkpi-empty-state">正在加载成本详情...</div> : null}
          {detailError ? <div className="vkpi-empty-state">{detailError}</div> : null}
          {detail ? (
            <div className="vkpi-detail-stack">
              <InfoRow label="成本" value={currencyFormatter.format(safeNumber((detail.cost as Record<string, unknown>)?.amount_cents) / 100)} />
              <InfoRow label="状态" value={textValue((detail.cost as Record<string, unknown>)?.status, '-')} />
              <InfoRow label="项目" value={textValue((detail.project as Record<string, unknown>)?.project_name || (detail.cost as Record<string, unknown>)?.project_id, '-')} />
              <InfoRow label="红人" value={textValue((detail.kol as Record<string, unknown>)?.channel_name || (detail.cost as Record<string, unknown>)?.kol_id, '-')} />
              <InfoRow label="负责人" value={textValue((detail.owner as Record<string, unknown>)?.name || (detail.cost as Record<string, unknown>)?.staff_id, '-')} />
              <InfoRow label="审核 / 作废" value={`${textValue((detail.cost as Record<string, unknown>)?.approved_at, '-')} / ${textValue((detail.cost as Record<string, unknown>)?.voided_at, '-')}`} />
              <div className="vkpi-inline-list">
                <strong>审计记录</strong>
                {Array.isArray(detail.audit_events) && detail.audit_events.length ? detail.audit_events.slice(0, 6).map((row) => {
                  const item = row as Record<string, unknown>;
                  return <span key={String(item.id || item.created_at)}>{textValue(item.action_type, 'audit')} · {textValue(item.created_at, '-')}</span>;
                }) : <span>暂无成本审计记录。</span>}
              </div>
            </div>
          ) : <div className="vkpi-empty-state">选择成本记录后查看详情、项目、红人、负责人和审计记录。</div>}
        </section>
        <button className="vkpi-card vkpi-drill-card" type="button" onClick={() => onOpenEvidence('cost')}>
          <span>成本 source rows</span>
          <strong>{data.metrics.find((metric) => metric.key === 'cost')?.sourceCount || 0}</strong>
          <em>点击查看 metric lineage 证据</em>
        </button>
      </section>
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header"><div><h2>成本明细</h2><span>{visibleCosts.length} 条</span></div></div>
        <CostLedgerTable rows={visibleCosts} onSelect={setCostId} />
      </section>
    </PageShell>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return <div className="vkpi-info-row"><span>{label}</span><strong>{value}</strong></div>;
}
