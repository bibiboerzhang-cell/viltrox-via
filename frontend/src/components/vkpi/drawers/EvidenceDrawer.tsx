import type { VkpiDrilldownResponse } from '../../../services/vkpi.lineage-api';
import type { VkpiEvidenceRow, VkpiMetricEvidenceKey } from '../vkpiTypes';
import { currencyFormatter, numberFormatter } from '../shared/vkpiFormatters';

const OWNED_TRAFFIC_KEYWORDS = ['viltrox', 'official', 'brand', 'company', 'owned', 'self', '自营', '官方', '品牌', '公司'];
const PLATFORM_TRAFFIC_LABELS: Record<string, string> = {
  instagram: 'Instagram',
  youtube: 'YouTube',
  tiktok: 'TikTok',
  facebook: 'Facebook',
  x: 'X',
  twitter: 'X',
  bilibili: 'Bilibili',
  xiaohongshu: 'XHS',
  xhs: 'XHS',
};

function rowSearchText(row: VkpiEvidenceRow) {
  return `${row.label} ${row.source} ${row.rawRef || ''}`.toLowerCase();
}

function isOwnedTrafficRow(row: VkpiEvidenceRow) {
  const text = rowSearchText(row);
  return OWNED_TRAFFIC_KEYWORDS.some((keyword) => text.includes(keyword));
}

function isKolTrafficRow(row: VkpiEvidenceRow) {
  return Boolean(row.kolName || row.ownerName || row.projectId);
}

function platformLabel(row: VkpiEvidenceRow) {
  const source = String(row.source || '').trim().toLowerCase();
  return PLATFORM_TRAFFIC_LABELS[source] || row.source || '未知平台';
}

function rowGroupKey(row: VkpiEvidenceRow, fallback: string) {
  return String(row.ownerName || row.kolName || row.projectId || platformLabel(row) || fallback).trim();
}

function groupedRows(rows: VkpiEvidenceRow[], fallback: string) {
  const groups = new Map<string, VkpiEvidenceRow[]>();
  rows.forEach((row) => {
    const key = rowGroupKey(row, fallback);
    groups.set(key, [...(groups.get(key) || []), row]);
  });
  return Array.from(groups.entries()).map(([label, groupRows]) => ({
    label,
    rows: groupRows,
    total: groupRows.reduce((sum, row) => sum + (row.amount || 0), 0),
    platforms: Array.from(new Set(groupRows.map(platformLabel))).filter(Boolean),
  }));
}

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
  const renderEvidenceRow = (row: VkpiEvidenceRow) => (
    <article key={`${row.metric}-${row.id}`}>
      <div><strong>{row.label}</strong><span>{platformLabel(row)} · {row.occurredAt || '-'}</span></div>
      <b>{formatEvidenceAmount(row)}</b>
      <p>项目：{row.projectId || '-'} · 红人：{row.kolName || '-'} · 负责人：{row.ownerName || '-'}</p>
      <em>{row.confidence || '-'} {row.rawRef && !row.rawRef.startsWith('http') ? `· ${row.rawRef}` : ''}</em>
      {row.rawRef?.startsWith('http') ? <a href={row.rawRef} target="_blank" rel="noreferrer">打开原帖 ↗</a> : null}
    </article>
  );
  const renderGroupedTrafficRows = (groupRows: VkpiEvidenceRow[], fallback: string) => {
    const groups = groupedRows(groupRows, fallback);
    return (
      <div className="vkpi-traffic-accounts">
        {groups.map((group) => (
          <details key={group.label} className="vkpi-traffic-account" open={groups.length === 1}>
            <summary>
              <span>{group.label}</span>
              <small>{group.platforms.join(' / ') || '未知平台'} · {group.rows.length} 条证据</small>
              <strong>{group.total ? numberFormatter.format(group.total) : '-'}</strong>
            </summary>
            <div className="vkpi-traffic-rows">{group.rows.map(renderEvidenceRow)}</div>
          </details>
        ))}
      </div>
    );
  };
  const renderTrafficGroup = (title: string, subtitle: string, groupRows: VkpiEvidenceRow[], emptyText: string) => {
    const hasAmount = groupRows.some((row) => row.amount != null);
    const total = groupRows.reduce((sum, row) => sum + (row.amount || 0), 0);
    return (
      <section className="vkpi-traffic-group">
        <div className="vkpi-traffic-group-head">
          <div>
            <h3>{title}</h3>
            <p>{subtitle}</p>
          </div>
          <strong>{hasAmount ? numberFormatter.format(total) : '-'}</strong>
        </div>
        {groupRows.length ? renderGroupedTrafficRows(groupRows, title) : <div className="vkpi-empty-state">{emptyText}</div>}
      </section>
    );
  };
  const renderViewsBreakdown = () => {
    const ownedRows = rows.filter(isOwnedTrafficRow);
    const kolRows = rows.filter((row) => !isOwnedTrafficRow(row) && isKolTrafficRow(row));
    const unattributedRows = rows.filter((row) => !isOwnedTrafficRow(row) && !isKolTrafficRow(row));
    const hasAnyAmount = rows.some((row) => row.amount != null);
    const totalViews = rows.reduce((sum, row) => sum + (row.amount || 0), 0);
    return (
      <div className="vkpi-traffic-breakdown">
        {loading ? <div className="vkpi-empty-state">正在加载播放量来源...</div> : (
          <>
            <section className="vkpi-traffic-summary">
              <span>播放量来源拆解</span>
              <strong>{hasAnyAmount ? numberFormatter.format(totalViews) : '-'}</strong>
              <p>按 Viltrox 自营账号、员工负责 KOL、未归因内容拆开看，不再把不同业务来源混成一个证据列表。</p>
            </section>
            {renderTrafficGroup(
              'Viltrox 自营账号流量',
              '来自官方 / 品牌 / 公司账号矩阵的内容播放量。',
              ownedRows,
              '当前没有可归入 Viltrox 自营账号的播放量证据。需要账号快照或内容记录带有官方 / 品牌账号标识。',
            )}
            {renderTrafficGroup(
              'KOL 合作流量',
              '来自项目、员工负责 KOL 或合作内容的播放量。',
              kolRows,
              '当前没有可归入 KOL 合作的播放量证据。需要内容绑定 KOL、负责人或项目。',
            )}
            {unattributedRows.length ? renderTrafficGroup(
              '未归因播放量',
              '已有播放量证据，但当前缺少自营 / KOL 归属字段。',
              unattributedRows,
              '',
            ) : null}
            {!rows.length ? <div className="vkpi-empty-state">当前播放量没有可追溯来源。请先生成 metric snapshot，或接入自营账号内容、KOL 项目内容与播放量快照。</div> : null}
          </>
        )}
      </div>
    );
  };
  return (
    <aside className="vkpi-evidence-drawer" role="dialog" aria-label={title}>
      <header><div><span>证据下钻</span><h2>{title}</h2>{sourceLabel ? <small>{sourceLabel}</small> : null}</div><button type="button" onClick={onClose}>×</button></header>
      {metric === 'views' ? renderViewsBreakdown() : (
        <div className="vkpi-evidence-list">
          {loading ? <div className="vkpi-empty-state">正在加载指标证据...</div> : rows.length ? rows.map(renderEvidenceRow) : <div className="vkpi-empty-state">当前指标没有可展示的真实证据行。请先生成 metric snapshot，或接入 Shopify / Amazon / 成本 / 内容记录。</div>}
        </div>
      )}
    </aside>
  );
}
