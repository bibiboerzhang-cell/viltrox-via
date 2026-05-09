import type { VkpiDrilldownResponse } from '../../../services/vkpi.lineage-api';
import type { VkpiEvidenceRow, VkpiMetricEvidenceKey } from '../vkpiTypes';
import { currencyFormatter, numberFormatter } from '../shared/vkpiFormatters';

export function EvidenceDrawer({
  metric,
  rows,
  lineageInfo,
  usedFallback,
  loading,
  onClose,
}: {
  metric: VkpiMetricEvidenceKey;
  rows: VkpiEvidenceRow[];
  lineageInfo?: VkpiDrilldownResponse['run'] | null;
  usedFallback?: boolean;
  loading?: boolean;
  onClose: () => void;
}) {
  const titleMap: Record<string, string> = {
    gmv: '销售额证据',
    cost: '成本证据',
    roi: '销售 / 成本复核',
    net_contribution: '销售 / 成本证据',
    new_kol: '新增 KOL 证据',
    published_content: '已发布内容证据',
    valid_clicks: '有效点击证据',
    views: '播放量证据',
    active_projects: '进行中项目证据',
    alerts: '提醒证据',
  };
  const title = titleMap[metric] || '指标证据';
  const formatEvidenceAmount = (row: VkpiEvidenceRow) => {
    const amount = row.amount;
    if (amount == null) return '-';
    if (row.amountUnit === 'currency' || metric === 'gmv' || metric === 'cost' || metric === 'net_contribution') return currencyFormatter.format(amount);
    if (row.amountUnit === 'ratio' || metric === 'roi') return `${amount.toFixed(2)}x`;
    return numberFormatter.format(amount);
  };
  const sourceLabel = lineageInfo
    ? `快照 ${lineageInfo.uid} · ${lineageInfo.period_start?.slice(0, 10)} → ${lineageInfo.period_end?.slice(0, 10)} · ${lineageInfo.definition_version}`
    : usedFallback
      ? '尚未找到可用快照证据'
      : '';
  return (
    <aside className="vkpi-evidence-drawer" role="dialog" aria-label={title}>
      <header><div><span>证据下钻</span><h2>{title}</h2>{sourceLabel ? <small>{sourceLabel}</small> : null}</div><button type="button" onClick={onClose}>×</button></header>
      <div className="vkpi-evidence-list">
        {loading ? <div className="vkpi-empty-state">正在加载指标证据...</div> : rows.length ? rows.map((row) => <article key={`${row.metric}-${row.id}`}><div><strong>{row.label}</strong><span>{row.source} · {row.occurredAt || '-'}</span></div><b>{formatEvidenceAmount(row)}</b><p>项目：{row.projectId || '-'} · 红人：{row.kolName || '-'} · 负责人：{row.ownerName || '-'}</p><em>{row.confidence || '-'} {row.rawRef ? `· ${row.rawRef}` : ''}</em></article>) : <div className="vkpi-empty-state">当前指标没有可展示的真实证据行。请先生成 metric snapshot，或接入 Shopify / Amazon / 成本 / 内容记录。</div>}
      </div>
    </aside>
  );
}
