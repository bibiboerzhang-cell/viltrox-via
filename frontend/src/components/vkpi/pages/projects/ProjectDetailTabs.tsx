import { Component, useMemo, useRef, useState, type ErrorInfo, type ReactNode } from 'react';
import { Activity, AlertCircle, BookOpen, Boxes, Check, DollarSign, Download, Edit3, ExternalLink, Eye, FileText, Heart, ImageIcon, MessageCircle, MousePointerClick, Package, Plus, Send, Shield, ShoppingCart, Sparkles, TrendingUp, Upload, Video, X } from 'lucide-react';
import { stageLabels } from '../../shared/vkpiConstants';
import type { VkpiProjectDetail, VkpiProjectRow } from '../../vkpiTypes';
import type { VkpiAnalysisCacheEntry, VkpiProjectContract, VkpiProjectContractsResponse, VkpiProjectVideoAnalysisCacheItem, VkpiProjectVideoAnalysisCacheResponse } from '../../../../services/vkpi/projects-api';
import { formatLargeNum, formatMoneyShort, healthColor, PROJECT_STAGE_COLOR, PROJECT_STAGE_FLOW } from './projectDeliverableStyle';
import {
  bottleneckForRows,
  cancelledStages,
  healthForRows,
  stageIndex,
  type ContractLine,
  type ExpenseLine,
  type ProjectAnalyticsSummary,
  type ProjectStatsSummary,
  type StageCostSummary,
} from '../../../../domains/projects';

function centsValue(row: Record<string, unknown>) {
  if (row.amount_cents != null) return Number(row.amount_cents || 0);
  if (row.amount_usd != null) return Math.round(Number(row.amount_usd || 0) * 100);
  return 0;
}

interface CostLedgerTotals {
  contract: number;
  shipping: number;
  product: number;
  total: number;
}

function costLedgerTotals(costRows: Array<Record<string, unknown>>): CostLedgerTotals {
  return costRows.reduce<CostLedgerTotals>((totals, row) => {
    if (String(row.status || '').toLowerCase() === 'void') return totals;
    const type = String(row.cost_type || '').toLowerCase();
    const amount = centsValue(row) / 100;
    if (type === 'shipping') totals.shipping += amount;
    else if (type === 'product' || type === 'sample') totals.product += amount;
    else if (type === 'cash_fee' || type === 'contract' || type === 'creator_fee') totals.contract += amount;
    totals.total += amount;
    return totals;
  }, { contract: 0, shipping: 0, product: 0, total: 0 });
}

type MaterialSection = 'assets' | 'logistics';
type MaterialAssetStatus = 'ready' | 'draft' | 'todo';
type MaterialAssetType = 'image' | 'pdf' | 'doc' | 'script' | 'email' | 'legal';

interface MaterialAssetItem {
  id: string;
  name: string;
  type: MaterialAssetType;
  size: string;
  date: string;
  status: MaterialAssetStatus;
  aiDesc: string;
}

const ASSET_ICON: Record<MaterialAssetType, typeof FileText> = {
  image: ImageIcon,
  pdf: FileText,
  doc: FileText,
  script: Edit3,
  email: Send,
  legal: Shield,
};

const STATUS_BG: Record<MaterialAssetStatus, string> = {
  ready: 'bg-emerald-500/15 text-emerald-300',
  draft: 'bg-amber-500/15 text-amber-300',
  todo: 'bg-white/[0.05] text-slate-400',
};

const STATUS_LABEL: Record<MaterialAssetStatus, string> = {
  ready: '已就绪',
  draft: '起草中',
  todo: '待处理',
};

const PROJECT_ASSETS: MaterialAssetItem[] = [
  { id: 'a1', name: '产品图 (5 张高清)', type: 'image', status: 'ready', date: '5/15', aiDesc: '已生成 ZIP · 含 PNG + 缩略图', size: '12.4 MB' },
  { id: 'a2', name: '技术参数手册 PDF', type: 'pdf', status: 'ready', date: '5/15', aiDesc: 'LLM 已生成 8 页 · 中英双语', size: '2.1 MB' },
  { id: 'a3', name: '竞品对比表', type: 'doc', status: 'ready', date: '5/18', aiDesc: 'vs Sony GM / Sigma · 12 维度对比', size: '856 KB' },
  { id: 'a4', name: '视频脚本模板', type: 'script', status: 'draft', date: '5/22', aiDesc: 'AI 起草 · 等 review', size: '—' },
  { id: 'a5', name: 'EDM 邮件模板 (cold)', type: 'email', status: 'todo', date: '—', aiDesc: '未生成 · 点击 AI 起草', size: '—' },
  { id: 'a6', name: '法务条款 (合同附件)', type: 'legal', status: 'ready', date: '5/10', aiDesc: '标准条款 · 已审', size: '412 KB' },
];

const PROJECT_EVIDENCE_COVERAGE = {
  assigned: 725,
  total: 1003,
};

function projectEvidenceCoverageLabel() {
  const pct = Math.round((PROJECT_EVIDENCE_COVERAGE.assigned / Math.max(PROJECT_EVIDENCE_COVERAGE.total, 1)) * 100);
  return `项目归属覆盖 ${pct}% · ${PROJECT_EVIDENCE_COVERAGE.assigned}/${PROJECT_EVIDENCE_COVERAGE.total} evidence 已归属`;
}

function buildMaterialBriefText(project: VkpiProjectRow | undefined, rows: VkpiProjectRow[], stats?: ProjectStatsSummary) {
  const projectTitle = project?.campaign || project?.productName || rows[0]?.productName || '未命名项目';
  const product = project?.productName || rows[0]?.productName || projectTitle;
  const sku = project?.productSku || rows[0]?.productSku || '待补充';
  const platforms = Array.from(new Set(rows.map((row) => row.platform).filter(Boolean))).join(', ') || '待确认';
  const readyAssets = PROJECT_ASSETS.filter((asset) => asset.status === 'ready').map((asset) => asset.name).join(' / ') || '待准备';
  const draftAssets = PROJECT_ASSETS.filter((asset) => asset.status !== 'ready').map((asset) => `${asset.name}(${STATUS_LABEL[asset.status]})`).join(' / ') || '无';
  const published = stats?.published ?? rows.filter((row) => stageIndex(row.stage) >= stageIndex('published')).length;

  return [
    `# ${projectTitle} 项目 Brief`,
    '',
    `产品: ${product}`,
    `SKU: ${sku}`,
    `平台: ${platforms}`,
    `参与 KOL: ${rows.length}`,
    `已发布: ${published}`,
    `当前曝光: ${formatLargeNum(stats?.views || 0)}`,
    '',
    '## 已就绪物料',
    readyAssets,
    '',
    '## 待处理物料',
    draftAssets,
    '',
    '## 执行口径',
    '- 所有 KOL 使用同一套产品图、参数手册、竞品对比表与法务条款。',
    '- 视频脚本和 EDM 模板仍需人工 review 后再外发。',
    '- 合同、费用和交付证明以项目详情页当前记录为准。',
  ].join('\n');
}

function buildRetrospectiveDraftText(
  project: VkpiProjectRow,
  rows: VkpiProjectRow[],
  stats: ProjectStatsSummary,
  healthScore: number,
  publishedKols: VkpiProjectRow[],
  withShopify: VkpiProjectRow[],
  withoutShopify: VkpiProjectRow[],
) {
  const projectTitle = project.campaign || project.productName || '未命名项目';
  const topRows = [...publishedKols].sort((a, b) => ((b.views || 0) - (a.views || 0))).slice(0, 5);
  const topLines = topRows.length
    ? topRows.map((row, index) => `${index + 1}. ${row.kolHandle || row.kolName || 'Unknown'} · ${row.platform} · ${formatLargeNum(row.views)} 播放`).join('\n')
    : '暂无已发布内容。';

  return [
    `# ${projectTitle} 复盘草稿`,
    '',
    `健康度: ${healthScore}`,
    `参与 KOL: ${rows.length}`,
    `已发布 KOL: ${publishedKols.length}`,
    `总曝光: ${formatLargeNum(stats.views)}`,
    `总点击: ${formatLargeNum(stats.clicks)}`,
    `归因 GMV: ${formatMoneyShort(stats.gmv)}`,
    `ROI: ${stats.roi == null ? '待成本/归因补齐' : `${stats.roi.toFixed(1)}%`}`,
    '',
    '## 归因接入',
    `已接 Shopify: ${withShopify.length}`,
    `未接 Shopify: ${withoutShopify.length}`,
    '',
    '## 内容表现 Top 5',
    topLines,
    '',
    '## 下一步',
    withoutShopify.length > 0 ? '- 补齐未接 Shopify 的归因链接，避免 ROI 偏低。' : '- Shopify 归因已覆盖已发布内容，继续观察订单变化。',
    publishedKols.length < rows.length ? '- 推进未发布 KOL 到内容发布节点。' : '- 所有 KOL 已进入发布/复盘口径。',
  ].join('\n');
}

function objectValue(value: unknown): Record<string, unknown> {
  if (!value) return {};
  if (typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== 'string') return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function costRowAmount(costRows: Array<Record<string, unknown>>, row: VkpiProjectRow, type: 'shipping' | 'product' | 'contract') {
  const assignmentId = String(row.assignmentId || '').trim();
  const kolPoolId = String(row.kolPoolId || '').trim();
  return costRows.reduce((sum, costRow) => {
    if (String(costRow.status || '').toLowerCase() === 'void') return sum;
    const costType = String(costRow.cost_type || '').toLowerCase();
    if (type === 'shipping' && costType !== 'shipping') return sum;
    if (type === 'product' && !['product', 'sample'].includes(costType)) return sum;
    if (type === 'contract' && !['cash_fee', 'contract', 'creator_fee'].includes(costType)) return sum;
    const metadata = objectValue(costRow.metadata_json || costRow.metadata);
    const sourceRef = String(costRow.source_ref || '');
    const matchesAssignment = assignmentId && (
      sourceRef === `assignment_${type}:${assignmentId}`
      || sourceRef.endsWith(`:${assignmentId}`)
      || String(metadata.assignment_id || '') === assignmentId
    );
    const matchesPool = kolPoolId && String(metadata.kol_pool_id || '') === kolPoolId;
    return matchesAssignment || matchesPool ? sum + (centsValue(costRow) / 100) : sum;
  }, 0);
}

function rowProductSent(row: VkpiProjectRow): string[] {
  const dynamicRow = row as unknown as Record<string, unknown>;
  const raw = dynamicRow.productSent || dynamicRow.product_sent || dynamicRow.productsSent || dynamicRow.products;
  if (Array.isArray(raw)) {
    return raw.map((item) => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object') {
        const record = item as Record<string, unknown>;
        return String(record.name || record.productName || record.product_name || record.sku || record.productSku || '').trim();
      }
      return '';
    }).filter(Boolean);
  }
  const single = String(dynamicRow.productName || dynamicRow.product_name || dynamicRow.productSku || dynamicRow.product_sku || '').trim();
  return single ? [single] : [];
}

function productCost(productSent: string[]) {
  return productSent.length ? 0 : 0;
}

function trackingUrl(carrier: string, trackingNumber: string) {
  const normalizedCarrier = carrier.toLowerCase();
  const encoded = encodeURIComponent(trackingNumber);
  if (normalizedCarrier.includes('dhl')) return `https://www.dhl.com/us-en/home/tracking/tracking-express.html?submit=1&tracking-id=${encoded}`;
  if (normalizedCarrier.includes('fedex')) return `https://www.fedex.com/fedextrack/?trknbr=${encoded}`;
  if (normalizedCarrier.includes('ups')) return `https://www.ups.com/track?tracknum=${encoded}`;
  if (normalizedCarrier.includes('usps')) return `https://tools.usps.com/go/TrackConfirmAction?tLabels=${encoded}`;
  return `https://www.google.com/search?q=${encodeURIComponent(`${carrier} ${trackingNumber}`)}`;
}

function deliveredByStageOrStatus(row: VkpiProjectRow) {
  const status = String(row.trackingStatus || '').toLowerCase();
  return stageIndex(row.stage) >= stageIndex('received') || /delivered|signed|received|签收|已送达|已到货/.test(status);
}

function timelineDateValue(row: VkpiProjectRow) {
  return row.currentStageStartedAt || row.latestMessageAt || row.updatedAt || row.createdAt || row.startedAt || '';
}

function timelineTimestamp(value: string) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function formatTimelineDate(value: string) {
  if (!value) return '—';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const hour = String(date.getHours()).padStart(2, '0');
  const minute = String(date.getMinutes()).padStart(2, '0');
  return `${month}/${day} ${hour}:${minute}`;
}

function timelineStage(row: VkpiProjectRow) {
  const index = Math.max(0, Math.min(PROJECT_STAGE_FLOW.length - 1, stageIndex(row.stage)));
  return PROJECT_STAGE_FLOW[index];
}

function timelineEventText(row: VkpiProjectRow) {
  if (row.trackingStatus) return `✓ ${row.trackingStatus}`;
  if ((row.evidenceCount || 0) > 0) return `✓ 已归档 ${row.evidenceCount} 条证据`;
  if (row.latestMessageSource && row.latestMessageSource !== 'No reply') return `✓ ${row.latestMessageSource}`;
  return `✓ ${stageLabels[row.stage] || timelineStage(row).label}`;
}

function timelineSpecial(row: VkpiProjectRow) {
  if (row.stage === 'lost' || row.stage === 'cancelled') return 'lost';
  if (row.stage === 'stalled') return 'stalled';
  return '';
}

function timelineField(event: Record<string, unknown>, key: string) {
  return String(event[key] ?? '').trim();
}

function timelineStageLabel(stage: string) {
  return (stageLabels as Record<string, string>)[stage] || stage || '—';
}

function timelineEventTypeLabel(type: string) {
  const labels: Record<string, string> = {
    created: '创建项目',
    deleted: '删除/取消',
    stage_change: '阶段变更',
    suggestion_project_created: '建议转项目',
  };
  return labels[type] || type || '事件';
}

function timelineEventColor(toStage: string, eventType: string) {
  if (eventType === 'deleted' || toStage === 'cancelled') return '#ef4444';
  if (eventType === 'created') return '#22c55e';
  const index = Math.max(0, Math.min(PROJECT_STAGE_FLOW.length - 1, stageIndex(toStage)));
  return PROJECT_STAGE_COLOR[PROJECT_STAGE_FLOW[index].key] || '#94a3b8';
}

export function CampaignTimelineTab({
  rows,
  events = [],
}: {
  rows: VkpiProjectRow[];
  events?: VkpiProjectDetail['events'];
}) {
  const realEvents = useMemo(() => events
    .map((event, index) => {
      const fromStage = timelineField(event, 'from_stage');
      const toStage = timelineField(event, 'to_stage');
      const eventType = timelineField(event, 'event_type');
      const effectiveAt = timelineField(event, 'effective_at') || timelineField(event, 'created_at');
      return {
        id: timelineField(event, 'id') || `event-${index}`,
        date: formatTimelineDate(effectiveAt),
        timestamp: timelineTimestamp(effectiveAt),
        actor: timelineField(event, 'actor_staff_id') ? `Staff #${timelineField(event, 'actor_staff_id')}` : '系统/未知',
        fromStage,
        toStage,
        transition: `${timelineStageLabel(fromStage)} → ${timelineStageLabel(toStage)}`,
        eventType: timelineEventTypeLabel(eventType),
        note: timelineField(event, 'note'),
        source: [timelineField(event, 'source_ref_type'), timelineField(event, 'source_ref_id')].filter(Boolean).join(' #'),
        stageColor: timelineEventColor(toStage, eventType),
      };
    })
    .sort((a, b) => b.timestamp - a.timestamp || String(b.id).localeCompare(String(a.id))), [events]);

  const snapshot = useMemo(() => {
    const grouped = new Map<string, { label: string; color: string; count: number }>();
    rows.forEach((row) => {
      const stage = timelineStage(row);
      const special = timelineSpecial(row);
      const key = row.stage;
      const current = grouped.get(key) || {
        label: special === 'lost' ? '标记流失' : special === 'stalled' ? '标记停滞' : (stageLabels[row.stage] || stage.label),
        color: special === 'lost' ? '#ef4444' : special === 'stalled' ? '#fb923c' : PROJECT_STAGE_COLOR[stage.key],
        count: 0,
      };
      current.count += 1;
      grouped.set(key, current);
    });
    return Array.from(grouped.entries())
      .sort(([a], [b]) => stageIndex(a) - stageIndex(b))
      .map(([key, item]) => ({ key, ...item }));
  }, [rows]);

  return (
    <div className="p-4 space-y-3" aria-label="项目时间轴">
      <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 flex items-start gap-2.5">
        <Activity size={13} className="text-purple-300 mt-0.5 shrink-0" />
        <div className="text-[10.5px] text-slate-300">
          {realEvents.length} 条真实历史事件 · 来源 vkpi_project_stage_events · 按时间倒序
        </div>
      </div>

      {realEvents.length === 0 ? (
        <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
          <Activity size={24} className="text-slate-600 mx-auto mb-2" />
          <div className="text-[11.5px] text-slate-300">暂无真实历史事件</div>
          <div className="text-[10px] text-slate-500 mt-1">该项目尚未写入 stage_events；下方仅显示当前状态快照。</div>
        </div>
      ) : (
        <div className="space-y-2 relative">
          <div className="absolute left-3 top-2 bottom-2 w-px bg-white/[0.06]" />
          {realEvents.map((event) => (
            <div key={event.id} className="flex items-start gap-3 relative pl-1">
              <div
                className="w-5 h-5 rounded-full flex items-center justify-center shrink-0 z-10"
                style={{ background: event.stageColor || '#94a3b8', boxShadow: '0 0 0 3px #0a0a0d' }}
              >
                <Check size={10} className="text-white" />
              </div>
              <div className="flex-1 rounded-lg border border-white/[0.05] bg-white/[0.012] p-2.5">
                <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                  <span className="text-[10.5px] text-slate-400 tabular-nums font-mono">{event.date}</span>
                  <span className="text-[11px] text-white font-medium">{event.transition}</span>
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                    style={{ background: `${event.stageColor}20`, color: event.stageColor }}
                  >
                    {event.eventType}
                  </span>
                </div>
                <div className="text-[10px] text-slate-500 mt-1">{event.actor}{event.source ? ` · ${event.source}` : ''}</div>
                {event.note ? <div className="text-[10px] text-slate-400 mt-1 whitespace-pre-wrap">{event.note}</div> : null}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="rounded-lg border border-white/[0.06] bg-white/[0.012] p-3">
        <div className="flex items-center gap-2 mb-2">
          <AlertCircle size={12} className="text-slate-400" />
          <div className="text-[10.5px] text-slate-300">当前状态快照 · 非历史事件</div>
        </div>
        {snapshot.length === 0 ? (
          <div className="text-[10px] text-slate-500">暂无 KOL 当前状态。</div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {snapshot.map((item) => (
              <span
                key={item.key}
                className="text-[10px] px-2 py-1 rounded border"
                style={{ color: item.color, borderColor: `${item.color}40`, background: `${item.color}12` }}
              >
                {item.label} {item.count}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function stringValue(value: unknown) {
  return value == null ? '' : String(value);
}

function arrayValue(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [value];
    } catch {
      return value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
    }
  }
  return [];
}

function editableJson(value: unknown) {
  const list = arrayValue(value);
  if (!list.length) return '';
  if (list.some((item) => item && typeof item === 'object')) return JSON.stringify(list, null, 2);
  return list.map((item) => String(item)).join('\n');
}

function parseEditableList(value: string): unknown[] {
  const text = value.trim();
  if (!text) return [];
  if (text.startsWith('[') || text.startsWith('{')) {
    try {
      const parsed = JSON.parse(text);
      return Array.isArray(parsed) ? parsed : [parsed];
    } catch {
      // Fall through to manual line parsing.
    }
  }
  return text.split(/\n|,|，/).map((item) => item.trim()).filter(Boolean);
}

function compactList(value: unknown, fallback = '待确认') {
  const list = arrayValue(value);
  if (!list.length) return fallback;
  return list.map((item) => {
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>;
      return stringValue(record.description || record.item || record.title || record.name || JSON.stringify(record));
    }
    return stringValue(item);
  }).filter(Boolean).slice(0, 3).join(' / ') || fallback;
}

function moneyLabel(amount: unknown, currency: unknown) {
  const numeric = Number(amount);
  if (!Number.isFinite(numeric) || numeric <= 0) return '金额待确认';
  return `${String(currency || 'USD').toUpperCase()} ${numeric.toLocaleString()}`;
}

function contractLinkedRow(contract: VkpiProjectContract, rows: VkpiProjectRow[]) {
  const assignmentId = String(contract.assignment_id || '').trim();
  const kolPoolId = String(contract.kol_pool_id || '').trim();
  return rows.find((row) => assignmentId && String(row.assignmentId || '') === assignmentId)
    || rows.find((row) => kolPoolId && String(row.kolPoolId || '') === kolPoolId);
}

function contractStatusMeta(contract: VkpiProjectContract) {
  const status = String(contract.status || '').toLowerCase();
  const extraction = String(contract.extraction_status || '').toLowerCase();
  if (status === 'confirmed') return { label: '已确认', className: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/20' };
  if (extraction === 'failed' || status === 'failed') return { label: '提取失败', className: 'bg-red-500/15 text-red-300 border-red-500/20' };
  if (extraction === 'processing') return { label: 'Claude 提取中', className: 'bg-purple-500/15 text-purple-300 border-purple-500/20' };
  if (extraction === 'skipped') return { label: '已归档 · 未自动提取', className: 'bg-slate-500/15 text-slate-300 border-slate-500/20' };
  return { label: '待人工确认', className: 'bg-amber-500/15 text-amber-300 border-amber-500/20' };
}

function confidenceValue(contract: VkpiProjectContract, key: string) {
  const value = Number(contract.field_confidence_json?.[key]);
  return Number.isFinite(value) ? value : null;
}

function ConfidenceBadge({ contract, field }: { contract: VkpiProjectContract; field: string }) {
  const value = confidenceValue(contract, field);
  if (value == null) return <span className="text-[9px] text-slate-600">conf —</span>;
  const low = value < 0.7;
  return (
    <span className={`text-[9px] px-1.5 py-0.5 rounded border ${low ? 'border-amber-400/30 bg-amber-400/10 text-amber-300' : 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300'}`}>
      conf {Math.round(value * 100)}%
    </span>
  );
}

interface ContractDraft {
  fee_amount: string;
  fee_currency: string;
  contract_duration: string;
  start_date: string;
  end_date: string;
  platforms: string;
  deliverable_count: string;
  deliverables: string;
  must_include: string;
  usage_rights: string;
  exclusivity: string;
  buyout_rights: string;
  breach_terms: string;
  payment_terms: string;
}

function initialContractDraft(contract: VkpiProjectContract): ContractDraft {
  return {
    fee_amount: stringValue(contract.fee_amount),
    fee_currency: stringValue(contract.fee_currency || 'USD'),
    contract_duration: stringValue(contract.contract_duration),
    start_date: stringValue(contract.start_date),
    end_date: stringValue(contract.end_date),
    platforms: arrayValue(contract.platforms_json).map((item) => stringValue(item)).join(', '),
    deliverable_count: stringValue(contract.deliverable_count),
    deliverables: editableJson(contract.deliverables_json),
    must_include: editableJson(contract.must_include_json),
    usage_rights: stringValue(contract.usage_rights),
    exclusivity: stringValue(contract.exclusivity),
    buyout_rights: stringValue(contract.buyout_rights),
    breach_terms: stringValue(contract.breach_terms),
    payment_terms: stringValue(contract.payment_terms),
  };
}

function contractPayload(draft: ContractDraft) {
  const payload = {
    fee_amount: draft.fee_amount ? Number(draft.fee_amount) : null,
    fee_currency: draft.fee_currency.trim() || 'USD',
    contract_duration: draft.contract_duration.trim(),
    start_date: draft.start_date.trim() || null,
    end_date: draft.end_date.trim() || null,
    platforms: draft.platforms.split(/,|，|\n/).map((item) => item.trim()).filter(Boolean),
    deliverable_count: draft.deliverable_count ? Number(draft.deliverable_count) : null,
    deliverables: parseEditableList(draft.deliverables),
    must_include: parseEditableList(draft.must_include),
    usage_rights: draft.usage_rights.trim(),
    exclusivity: draft.exclusivity.trim(),
    buyout_rights: draft.buyout_rights.trim(),
    breach_terms: draft.breach_terms.trim(),
    payment_terms: draft.payment_terms.trim(),
  };
  return { ...payload, manual_overrides: payload };
}

function ContractArchiveCard({
  contract,
  row,
  busyKey,
  onSave,
  onConfirm,
  onOpen,
  onRetry,
}: {
  contract: VkpiProjectContract;
  row?: VkpiProjectRow;
  busyKey: string;
  onSave: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onConfirm: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onOpen: (contractId: number) => Promise<void>;
  onRetry: (contractId: number) => Promise<void>;
}) {
  const [draft, setDraft] = useState<ContractDraft>(() => initialContractDraft(contract));
  const meta = contractStatusMeta(contract);
  const saving = busyKey === `save:${contract.id}`;
  const confirming = busyKey === `confirm:${contract.id}`;
  const extracting = busyKey === `extract:${contract.id}` || contract.extraction_status === 'processing';
  const downloading = busyKey === `download:${contract.id}`;
  const update = (key: keyof ContractDraft, value: string) => setDraft((current) => ({ ...current, [key]: value }));

  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.018] p-3 space-y-3">
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-lg bg-purple-500/10 border border-purple-400/20 flex items-center justify-center shrink-0">
          <FileText size={18} className="text-purple-300" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <strong className="text-[13px] text-white truncate">{contract.file_name || 'Contract.pdf'}</strong>
            <span className={`text-[9.5px] px-2 py-0.5 rounded-full border ${meta.className}`}>{meta.label}</span>
            {contract.status === 'confirmed' ? <span className="text-[9.5px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-300">人工确认</span> : null}
          </div>
          <div className="text-[10.5px] text-slate-400 mt-1">
            {row?.kolHandle || row?.kolName || `KOL ${contract.kol_pool_id || '-'}`} · {row?.platform || compactList(contract.platforms_json, '平台待确认')} · {moneyLabel(contract.fee_amount, contract.fee_currency)}
          </div>
          <div className="text-[10px] text-slate-500 mt-1 truncate">
            交付: {compactList(contract.deliverables_json)} · 授权: {contract.usage_rights || contract.buyout_rights || '授权范围待确认'}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          <button className="px-2.5 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] text-[10px] text-slate-300 hover:text-white" type="button" onClick={() => void onOpen(contract.id)} disabled={downloading}>
            <ExternalLink size={11} className="inline mr-1" />查看PDF
          </button>
          <button className="px-2.5 py-1.5 rounded-md border border-purple-400/25 bg-purple-400/10 text-[10px] text-purple-200 disabled:opacity-50" type="button" onClick={() => void onRetry(contract.id)} disabled={extracting}>
            {extracting ? '提取中' : '重新提取'}
          </button>
        </div>
      </div>

      {contract.extraction_status === 'skipped' ? (
        <div className="rounded-lg border border-slate-500/20 bg-slate-500/10 p-3 text-[11px] text-slate-300">DOC/DOCX 已归档，v1 仅自动提取 PDF。可查看原文件，或上传 PDF 版本触发 Claude 条款提取。</div>
      ) : null}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {[
          ['金额', moneyLabel(contract.fee_amount, contract.fee_currency), 'fee_amount'],
          ['期限', contract.start_date || contract.end_date ? `${contract.start_date || '?'} → ${contract.end_date || '?'}` : (contract.contract_duration || '待确认'), 'contract_duration'],
          ['平台', compactList(contract.platforms_json), 'platforms'],
          ['承诺条数', contract.deliverable_count || '待确认', 'deliverable_count'],
        ].map(([label, value, field]) => (
          <div key={label} className="rounded-lg border border-white/[0.05] bg-black/20 p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[9px] text-slate-500">{label}</span>
              <ConfidenceBadge contract={contract} field={String(field)} />
            </div>
            <strong className="block mt-1 text-[12px] text-white truncate">{value}</strong>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <label className="text-[10px] text-slate-400">金额<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.fee_amount} onChange={(event) => update('fee_amount', event.target.value)} /></label>
        <label className="text-[10px] text-slate-400">币种<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.fee_currency} onChange={(event) => update('fee_currency', event.target.value)} /></label>
        <label className="text-[10px] text-slate-400">开始日期<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.start_date} onChange={(event) => update('start_date', event.target.value)} placeholder="YYYY-MM-DD" /></label>
        <label className="text-[10px] text-slate-400">结束日期<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.end_date} onChange={(event) => update('end_date', event.target.value)} placeholder="YYYY-MM-DD" /></label>
        <label className="text-[10px] text-slate-400">期限<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.contract_duration} onChange={(event) => update('contract_duration', event.target.value)} /></label>
        <label className="text-[10px] text-slate-400">平台<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.platforms} onChange={(event) => update('platforms', event.target.value)} placeholder="YouTube, Instagram" /></label>
        <label className="text-[10px] text-slate-400">承诺条数<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.deliverable_count} onChange={(event) => update('deliverable_count', event.target.value)} /></label>
        <label className="text-[10px] text-slate-400">Exclusivity<input className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white" value={draft.exclusivity} onChange={(event) => update('exclusivity', event.target.value)} /></label>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {[
          ['deliverables', 'Deliverables', draft.deliverables, 'deliverables'],
          ['must_include', '必须包含', draft.must_include, 'must_include'],
          ['usage_rights', 'Usage rights', draft.usage_rights, 'usage_rights'],
          ['buyout_rights', '买断授权', draft.buyout_rights, 'buyout_rights'],
          ['breach_terms', '违约条款', draft.breach_terms, 'breach_terms'],
          ['payment_terms', '付款条款', draft.payment_terms, 'payment_terms'],
        ].map(([key, label, value, field]) => (
          <label key={key} className="text-[10px] text-slate-400">
            <span className="flex items-center justify-between gap-2 mb-1">{label}<ConfidenceBadge contract={contract} field={String(field)} /></span>
            <textarea className="w-full min-h-[74px] rounded-md border border-white/[0.08] bg-black/20 px-2 py-1.5 text-[11px] text-white resize-y" value={String(value)} onChange={(event) => update(key as keyof ContractDraft, event.target.value)} />
          </label>
        ))}
      </div>

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="text-[10px] text-slate-500">低 confidence 字段会用黄色标出；LLM 结果默认待人工确认，不作为最终合同真值。</div>
        <div className="flex items-center gap-2">
          <button className="px-3 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] text-[11px] text-slate-300 disabled:opacity-50" type="button" onClick={() => void onSave(contract.id, contractPayload(draft))} disabled={saving || confirming}>{saving ? '保存中' : '保存修改'}</button>
          <button className="px-3 py-1.5 rounded-md bg-emerald-500/90 hover:bg-emerald-500 text-[11px] font-semibold text-white disabled:opacity-50" type="button" onClick={() => void onConfirm(contract.id, contractPayload(draft))} disabled={saving || confirming}>{confirming ? '确认中' : '确认归档'}</button>
        </div>
      </div>
    </div>
  );
}

export function CampaignContractsTab({
  rows,
  contractLines,
  contracts,
  loading,
  error,
  busyKey,
  onUploadContract,
  onSaveContract,
  onConfirmContract,
  onOpenContract,
  onRetryExtract,
}: {
  rows: VkpiProjectRow[];
  contractLines: ContractLine[];
  contracts: VkpiProjectContractsResponse | null;
  loading: boolean;
  error: string;
  busyKey: string;
  onUploadContract: (file: File, assignmentId?: string, kolPoolId?: string) => Promise<void>;
  onSaveContract: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onConfirmContract: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onOpenContract: (contractId: number) => Promise<void>;
  onRetryExtract: (contractId: number) => Promise<void>;
}) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [linkedRowId, setLinkedRowId] = useState('');
  const uploadBusy = busyKey === 'upload';
  const items = contracts?.items || [];
  const expectedRows = contractLines.filter((line) => line.statusLabel !== '未触发');
  const selectedRow = rows.find((row) => row.id === linkedRowId) || rows.find((row) => stageIndex(row.stage) >= stageIndex('agreed')) || rows[0];
  const chooseFile = (nextFile?: File | null) => {
    if (!nextFile) return;
    if (!/\.(pdf|doc|docx)$/i.test(nextFile.name)) return;
    setFile(nextFile);
  };
  const submitUpload = async () => {
    if (!file) return;
    await onUploadContract(file, selectedRow?.assignmentId, selectedRow?.kolPoolId);
    setFile(null);
  };

  return (
    <div className="p-4 space-y-4" aria-label="项目合同归档">
      <div className="rounded-xl border border-purple-500/25 bg-purple-500/[0.06] p-3 flex items-start gap-2.5">
        <Sparkles size={14} className="text-purple-300 mt-0.5 shrink-0" />
        <div className="text-[10.5px] text-slate-300">
          合同归档 v1 · PDF 上传后由 Claude Opus 自动提取条款；LLM 字段必须人工确认后才进入正式履约复盘口径。
        </div>
      </div>

      <div
        className="rounded-xl border-2 border-dashed border-white/[0.08] bg-white/[0.012] p-4"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => { event.preventDefault(); chooseFile(event.dataTransfer.files?.[0]); }}
      >
        <input ref={fileRef} type="file" accept=".pdf,.doc,.docx" className="hidden" onChange={(event) => chooseFile(event.target.files?.[0])} />
        <div className="grid md:grid-cols-[minmax(0,1fr)_220px_128px] gap-3 items-end">
          <button className="rounded-lg border border-white/[0.06] bg-black/20 p-4 text-left hover:border-purple-400/35" type="button" onClick={() => fileRef.current?.click()} disabled={uploadBusy}>
            <Upload size={20} className="text-purple-300 mb-2" />
            <strong className="block text-[12px] text-white">{file ? file.name : '拖拽或选择合同 PDF / DOCX'}</strong>
            <span className="block mt-1 text-[10.5px] text-slate-500">{file ? `${Math.round(file.size / 1024)} KB · ${/\.pdf$/i.test(file.name) ? '将自动提取' : '只存档不提取'}` : 'PDF 自动提取条款；DOCX 先存档，v1 不自动识别'}</span>
          </button>
          <label className="text-[10px] text-slate-400">关联 KOL
            <select className="mt-1 w-full rounded-md border border-white/[0.08] bg-black/30 px-2 py-2 text-[11px] text-white" value={linkedRowId} onChange={(event) => setLinkedRowId(event.target.value)}>
              <option value="">自动选择 / 项目级</option>
              {rows.map((row) => (
                <option key={row.id} value={row.id}>{row.kolHandle || row.kolName || row.id} · {stageLabels[row.stage] || row.stage}</option>
              ))}
            </select>
          </label>
          <button className="rounded-md bg-purple-500/90 hover:bg-purple-500 text-white text-[11px] font-semibold px-3 py-2 disabled:opacity-50" type="button" onClick={() => void submitUpload()} disabled={!file || uploadBusy}>
            {uploadBusy ? 'Claude正在提取条款' : '上传归档'}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2">
        {[
          ['已归档', items.length],
          ['待确认', items.filter((item) => item.status !== 'confirmed' && item.extraction_status !== 'failed').length],
          ['提取失败', items.filter((item) => item.extraction_status === 'failed').length],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-3">
            <span className="block text-[9.5px] text-slate-500">{label}</span>
            <strong className="block mt-1 text-[20px] text-white tabular-nums">{value}</strong>
          </div>
        ))}
      </div>

      {loading ? <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-4 text-[11px] text-slate-400">正在读取合同归档...</div> : null}
      {error ? <div className="rounded-lg border border-red-500/25 bg-red-500/10 p-4 text-[11px] text-red-300">{error}</div> : null}

      {!loading && !items.length ? (
        <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
          <FileText size={24} className="text-slate-600 mx-auto mb-2" />
          <div className="text-[11.5px] text-slate-400">暂无真实合同 · 当前有 {expectedRows.length} 个 KOL 已到可归档阶段</div>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((contract) => (
            <ContractArchiveCard
              key={contract.id}
              contract={contract}
              row={contractLinkedRow(contract, rows)}
              busyKey={busyKey}
              onSave={onSaveContract}
              onConfirm={onConfirmContract}
              onOpen={onOpenContract}
              onRetry={onRetryExtract}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function retrospectiveTextField(row: VkpiProjectRow, keys: string[]) {
  const dynamicRow = row as unknown as Record<string, unknown>;
  for (const key of keys) {
    const value = dynamicRow[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function retrospectiveNumberField(row: VkpiProjectRow, keys: string[]) {
  const dynamicRow = row as unknown as Record<string, unknown>;
  for (const key of keys) {
    const value = dynamicRow[key];
    if (value == null || value === '') continue;
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric;
  }
  return 0;
}

function retrospectiveVideoUrl(row: VkpiProjectRow) {
  return retrospectiveTextField(row, ['contentUrl', 'content_url', 'videoUrl', 'video_url', 'postUrl', 'post_url', 'evidenceUrl', 'evidence_url']);
}

function analyticsKolProfileUrl(row: VkpiProjectRow) {
  const explicit = retrospectiveTextField(row, ['kolProfileUrl', 'profile_url', 'channel_url']);
  if (/^https?:\/\//i.test(explicit)) return explicit;
  const handle = String(row.kolHandle || '').trim().replace(/^@/, '');
  if (!handle || /^https?:\/\//i.test(handle)) return handle;
  const platform = String(row.platform || '').toLowerCase();
  if (platform.includes('youtube')) return `https://www.youtube.com/@${handle}`;
  if (platform.includes('instagram')) return `https://www.instagram.com/${handle}`;
  if (platform.includes('tiktok')) return `https://www.tiktok.com/@${handle}`;
  if (platform === 'x' || platform.includes('twitter')) return `https://x.com/${handle}`;
  return explicit;
}

function retrospectiveVideoTitle(row: VkpiProjectRow, projectTitle: string) {
  return retrospectiveTextField(row, ['evidenceTitle', 'evidence_title', 'videoTitle', 'video_title', 'contentTitle', 'content_title', 'title'])
    || `${projectTitle} · ${row.kolHandle || row.kolName || 'KOL'}`;
}

type AnalyticsRankingSort = 'views' | 'published';

function isSpecificEvidenceUrl(value: string) {
  if (!/^https?:\/\//i.test(value)) return false;
  try {
    const parsed = new URL(value);
    const host = parsed.hostname.toLowerCase();
    const path = parsed.pathname.toLowerCase();
    if (host.includes('youtube.com')) return path.includes('/watch') || path.includes('/shorts/') || path.includes('/embed/');
    if (host.includes('youtu.be')) return path.length > 1;
    if (host.includes('instagram.com')) return path.includes('/p/') || path.includes('/reel/') || path.includes('/tv/');
    if (host.includes('tiktok.com')) return path.includes('/video/');
    if (host.includes('facebook.com')) return path.includes('/posts/') || path.includes('/videos/') || path.includes('/watch') || path.includes('/reel/') || path.includes('/photo') || path.includes('/story.php') || path.includes('/permalink.php');
    if (host.includes('x.com') || host.includes('twitter.com')) return path.includes('/status/');
    return path.length > 1;
  } catch {
    return false;
  }
}

function firstSpecificEvidenceUrl(row: VkpiProjectRow, keys: string[]) {
  const dynamicRow = row as unknown as Record<string, unknown>;
  for (const key of keys) {
    const value = dynamicRow[key];
    if (typeof value !== 'string') continue;
    const trimmed = value.trim();
    if (trimmed && isSpecificEvidenceUrl(trimmed)) return trimmed;
  }
  return '';
}

function analyticsRankingVideoUrl(row: VkpiProjectRow, sort: AnalyticsRankingSort) {
  const latestKeys = ['latestVideoUrl', 'latest_video_url', 'latestEvidenceUrl', 'latest_evidence_url'];
  const topKeys = ['contentUrl', 'content_url', 'videoUrl', 'video_url', 'postUrl', 'post_url', 'evidenceUrl', 'evidence_url'];
  return firstSpecificEvidenceUrl(row, sort === 'published' ? [...latestKeys, ...topKeys] : [...topKeys, ...latestKeys]);
}

function analyticsRankingVideoTitle(row: VkpiProjectRow, sort: AnalyticsRankingSort, projectTitle: string) {
  const latestTitle = retrospectiveTextField(row, ['latestEvidenceTitle', 'latest_evidence_title', 'latestVideoTitle', 'latest_video_title']);
  if (sort === 'published' && latestTitle) return latestTitle;
  return retrospectiveVideoTitle(row, projectTitle);
}

function analyticsRankingPublishDate(row: VkpiProjectRow, sort: AnalyticsRankingSort) {
  const latestKeys = ['latestEvidencePublishDate', 'latest_evidence_publish_date', 'latestPublishDate', 'latest_publish_date'];
  const topKeys = ['evidencePublishDate', 'evidence_publish_date', 'publishDate', 'publish_date', 'publishedAt', 'published_at'];
  return retrospectiveTextField(row, sort === 'published' ? [...latestKeys, ...topKeys] : [...topKeys, ...latestKeys]);
}

function analyticsRankingPublishTime(row: VkpiProjectRow, sort: AnalyticsRankingSort) {
  const raw = analyticsRankingPublishDate(row, sort);
  if (!raw) return Number.NEGATIVE_INFINITY;
  const time = Date.parse(raw);
  return Number.isFinite(time) ? time : Number.NEGATIVE_INFINITY;
}

function analyticsPublishDateLabel(raw: string) {
  if (!raw) return '发布时间待补';
  const time = Date.parse(raw);
  if (!Number.isFinite(time)) return '时间待确认';
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(new Date(time));
}

function retrospectiveWatchTime(row: VkpiProjectRow) {
  const direct = retrospectiveTextField(row, ['watchTime', 'watch_time', 'duration', 'durationLabel', 'duration_label']);
  if (direct) return direct;
  const seconds = retrospectiveNumberField(row, ['durationSeconds', 'duration_seconds']);
  if (!seconds) return '—';
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60).toString().padStart(2, '0');
  return `${minutes}:${rest}`;
}

function retrospectiveRowInitial(row: VkpiProjectRow) {
  return (row.kolName || row.kolHandle || '?').trim().charAt(0).toUpperCase() || '?';
}

function analyticsEvidenceCount(row: VkpiProjectRow) {
  return Math.max(
    row.evidenceCount || 0,
    retrospectiveNumberField(row, ['stageEventCount', 'evidence_count', 'videoEvidenceCount', 'video_evidence_count']),
  );
}

function analyticsHasPublishedEvidence(row: VkpiProjectRow) {
  return (
    analyticsEvidenceCount(row) > 0 ||
    (row.views || 0) > 0 ||
    Boolean(retrospectiveTextField(row, ['contentUrl', 'content_url', 'videoUrl', 'video_url', 'postUrl', 'post_url', 'evidenceUrl', 'evidence_url'])) ||
    Boolean((row as unknown as Record<string, unknown>).videoMetrics)
  );
}

function analyticsWatchTime(row: VkpiProjectRow) {
  const direct = retrospectiveTextField(row, ['watchTime', 'watch_time', 'completionRate', 'completion_rate', 'duration', 'durationLabel', 'duration_label']);
  if (direct) return direct;
  const pct = retrospectiveNumberField(row, ['watchTimePct', 'watch_time_pct', 'completionPct', 'completion_pct']);
  if (pct > 0) return `${Math.round(pct)}%`;
  // Project detail rows do not expose watch-time/completion metrics yet.
  return '—';
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function textFrom(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (Array.isArray(value)) return value.map(textFrom).filter(Boolean).join(' / ');
  const record = asRecord(value);
  for (const key of ['rationale', 'evaluation', 'summary', 'text', 'reason', 'value', 'evidence', 'flag']) {
    if (!(key in record)) continue;
    const text = textFrom(record[key]);
    if (text) return text;
  }
  return '';
}

function normaliseScore(value: unknown, fallback?: unknown) {
  const source = value ?? fallback;
  if (typeof source === 'number' && Number.isFinite(source)) return { score: Math.round(source), rationale: '', confidence: null as number | null };
  const record = asRecord(source);
  const rawScore = Number(record.score ?? record.value);
  const rawConfidence = Number(record.confidence);
  return {
    score: Number.isFinite(rawScore) ? Math.round(rawScore) : null,
    rationale: textFrom(record.rationale ?? record.evaluation ?? record.reason),
    confidence: Number.isFinite(rawConfidence) ? rawConfidence : null,
  };
}

function analysisScoreColor(score: number | null) {
  if (score == null) return '#94a3b8';
  if (score >= 80) return '#34d399';
  if (score >= 60) return '#facc15';
  return '#fb7185';
}

function finalV1Payload(entry?: VkpiAnalysisCacheEntry | null) {
  const result = asRecord(entry?.result);
  return asRecord(result.video_analysis_final_v1).layer1_visual_content ? asRecord(result.video_analysis_final_v1) : result;
}

function layerValue(layer: Record<string, unknown>, key: string, scoreFallback?: unknown) {
  const raw = layer[key];
  const directScore = normaliseScore(raw);
  const fallbackScore = normaliseScore(scoreFallback);
  const score = directScore.score != null ? directScore : fallbackScore;
  return {
    score: score.score,
    confidence: score.confidence,
    text: textFrom(raw) || score.rationale || '证据不足，等待更多分析结果。',
  };
}

function compactText(value: string, max = 150) {
  if (!value) return '暂无明确结论';
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function firstText(...values: unknown[]) {
  for (const value of values) {
    const text = textFrom(value);
    if (text) return text;
  }
  return '';
}

function normaliseRiskFlags(value: unknown) {
  if (Array.isArray(value)) {
    return value.map((item, index) => {
      const record = asRecord(item);
      const flag = textFrom(record.flag) || `risk_${index + 1}`;
      const evidence = textFrom(record.evidence);
      const severity = String(record.severity || '').toLowerCase();
      return {
        label: evidence ? `${flag}: ${evidence}` : flag,
        severity,
      };
    }).filter((item) => item.label);
  }
  const text = textFrom(value);
  return text ? [{ label: text, severity: /高|high|严重/i.test(text) ? 'high' : '' }] : [];
}

class RetrospectiveCardErrorBoundary extends Component<{ children: ReactNode; label: string }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, _info: ErrorInfo) {
    console.warn('final_v1 analysis card render failed', this.props.label, error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="rounded-lg border border-rose-400/20 bg-rose-500/[0.04] p-3 text-[10.5px] text-rose-200">
          final_v1 卡片渲染异常：{this.props.label}。该条结果仍保留在缓存中，等待字段 normalizer 补齐。
        </div>
      );
    }
    return this.props.children;
  }
}

function rowAnalysisKeys(row: VkpiProjectRow) {
  return [
    row.assignmentId ? `assignment:${row.assignmentId}` : '',
    row.kolPoolId ? `kol:${row.kolPoolId}` : '',
    row.videoUrl ? `url:${row.videoUrl}` : '',
    row.evidenceUrl ? `url:${row.evidenceUrl}` : '',
    row.latestVideoUrl ? `url:${row.latestVideoUrl}` : '',
    row.latestEvidenceUrl ? `url:${row.latestEvidenceUrl}` : '',
  ].filter(Boolean);
}

function buildAnalysisItemMap(items: VkpiProjectVideoAnalysisCacheItem[]) {
  const map = new Map<string, VkpiProjectVideoAnalysisCacheItem[]>();
  const add = (key: string, item: VkpiProjectVideoAnalysisCacheItem) => {
    const list = map.get(key) || [];
    list.push(item);
    map.set(key, list);
  };
  items.forEach((item) => {
    if (item.assignment_id != null) add(`assignment:${item.assignment_id}`, item);
    if (item.kol_pool_id != null) add(`kol:${item.kol_pool_id}`, item);
    if (item.content_url) add(`url:${item.content_url}`, item);
  });
  return map;
}

function analysisItemsForRow(row: VkpiProjectRow, map: Map<string, VkpiProjectVideoAnalysisCacheItem[]>) {
  const seen = new Set<string>();
  const items: VkpiProjectVideoAnalysisCacheItem[] = [];
  rowAnalysisKeys(row).forEach((key) => {
    (map.get(key) || []).forEach((item) => {
      const id = String(item.evidence_id ?? item.content_url ?? `${key}:${items.length}`);
      if (seen.has(id)) return;
      seen.add(id);
      items.push(item);
    });
  });
  return items;
}

function ProjectVideoAnalysisCard({
  row,
  item,
}: {
  row: VkpiProjectRow;
  item: VkpiProjectVideoAnalysisCacheItem;
}) {
  const ready = item.state === 'ready' && item.entry;
  const displayName = row.kolName || item.kol_name || item.handle || 'Unknown';
  const videoUrl = item.content_url || retrospectiveVideoUrl(row);
  const views = item.view_count ?? row.views ?? 0;
  const likes = item.like_count ?? row.likes ?? 0;
  const comments = item.comment_count ?? row.comments ?? 0;
  const payload = ready ? finalV1Payload(item.entry) : {};
  const layer2 = asRecord(payload.layer2_viewer_emotion);
  const layer3 = asRecord(payload.layer3_three_values);
  const layer6 = asRecord(payload.layer6_flags_and_scores);
  const scores = asRecord(layer6.scores);
  const contentScore = normaliseScore(scores.content_quality_score);
  const marketingScore = normaliseScore(scores.marketing_value_score ?? layer6.marketing_value_score);
  const viewerHeart = normaliseScore(layer2.viewer_heart_score ?? layer2.heart_movement_score, scores.viewer_heart_score);
  const channelValue = layerValue(layer3, 'channel_value', scores.channel_value_score);
  const assetValue = layerValue(layer3, 'asset_value', scores.asset_reuse_score);
  const productProof = layerValue(layer3, 'product_proof_value', scores.product_proof_score);
  const viewerReaction = firstText(layer2.one_sentence_viewer_reaction, layer2.one_sentence_viewer_feeling);
  const dislike = firstText(layer2.dislike_or_resistance, layer2.annoyance_or_ad_fatigue);
  const trigger = firstText(layer2.purchase_or_interest_trigger, layer2.desire_to_click_or_buy);
  const keyHook = textFrom(layer6.key_hook);
  const riskFlagTags = normaliseRiskFlags(layer6.risk_flags);
  const verdict = textFrom(layer6.final_verdict) || marketingScore.rationale || keyHook;
  const fullLayers = [
    ['layer1 画面', payload.layer1_visual_content],
    ['layer2 心动', payload.layer2_viewer_emotion],
    ['layer3 价值', payload.layer3_three_values],
    ['layer4 归因', payload.layer4_attribution],
    ['layer5 建议', payload.layer5_recommendations],
    ['layer6 评分', payload.layer6_flags_and_scores],
  ];

  if (!ready) {
    return (
      <div className="rounded-lg border border-white/[0.05] bg-white/[0.012] p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold text-slate-300 truncate">{displayName}</div>
            <div className="text-[9.5px] text-slate-500 truncate">{item.platform || row.platform} · evidence #{item.evidence_id || '-'}</div>
          </div>
          <span className="px-2 py-1 rounded bg-white/[0.05] text-slate-400 text-[10px] shrink-0">分析队列中</span>
        </div>
        <div className="mt-2 text-[10.5px] text-slate-500">Worker 正在后台跑 final_v1，结果写入缓存后这里会亮起。</div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-cyan-400/20 bg-cyan-400/[0.035] p-3 space-y-3">
      <div className="flex items-center gap-3">
        {row.kolAvatar ? (
          <img src={row.kolAvatar} alt={displayName} className="w-8 h-8 rounded-full object-cover shrink-0" />
        ) : (
          <div className="w-8 h-8 rounded-full bg-cyan-500/15 text-cyan-200 flex items-center justify-center text-[11px] font-bold shrink-0">{retrospectiveRowInitial(row)}</div>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold text-white truncate">{displayName}</span>
            <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 text-[9.5px]">已分析</span>
          </div>
          <div className="text-[9.5px] text-slate-500 truncate">{item.platform || row.platform} · 播放 {formatLargeNum(views)} · 赞 {formatLargeNum(likes)} · 评论 {formatLargeNum(comments)}</div>
        </div>
        {videoUrl ? <a href={videoUrl} target="_blank" rel="noreferrer" className="text-[10px] text-cyan-300 shrink-0">看视频</a> : null}
      </div>

      <div className="grid grid-cols-2 gap-2">
        {[
          ['内容质量', contentScore],
          ['投放价值', marketingScore],
        ].map(([label, score]) => {
          const itemScore = score as ReturnType<typeof normaliseScore>;
          return (
            <div key={label as string} className="rounded-md bg-black/30 border border-white/[0.05] px-3 py-2">
              <div className="text-[9.5px] text-slate-500 mb-1">{label as string}</div>
              <div className="text-[28px] font-bold leading-none tabular-nums" style={{ color: analysisScoreColor(itemScore.score) }}>{itemScore.score ?? '—'}</div>
            </div>
          );
        })}
      </div>
      <div className="text-[10.5px] text-slate-300 leading-relaxed">{compactText(verdict, 190)}</div>

      <div className="grid md:grid-cols-3 gap-2">
        {[
          ['渠道价值', channelValue],
          ['素材复用', assetValue],
          ['产品证明', productProof],
        ].map(([label, value]) => {
          const block = value as ReturnType<typeof layerValue>;
          return (
            <div key={label as string} className="rounded-md bg-white/[0.025] border border-white/[0.05] p-2">
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="text-[9.5px] text-slate-500">{label as string}</span>
                <span className="text-[15px] font-bold tabular-nums" style={{ color: analysisScoreColor(block.score) }}>{block.score ?? '—'}</span>
              </div>
              <div className="text-[10px] text-slate-300 leading-relaxed">{compactText(block.text, 92)}</div>
            </div>
          );
        })}
      </div>

      <div className="rounded-md bg-purple-500/[0.06] border border-purple-400/15 p-2.5">
        <div className="text-[9.5px] text-purple-300 mb-1">观众心动</div>
        <div className="italic text-[11px] text-slate-100 leading-relaxed">“{viewerReaction || '暂无一句话观众反应'}”</div>
        <div className="mt-2 flex flex-wrap gap-1.5 text-[9.5px]">
          <span className="px-1.5 py-0.5 rounded bg-white/[0.05] text-slate-300">心动 {viewerHeart.score ?? '—'}</span>
          {dislike ? <span className="px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-200">反感: {compactText(dislike, 42)}</span> : null}
          {trigger ? <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-200">种草: {compactText(trigger, 42)}</span> : null}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 text-[10px]">
        {keyHook ? <span className="px-2 py-1 rounded bg-cyan-500/10 text-cyan-200">Hook: {compactText(keyHook, 90)}</span> : null}
        {riskFlagTags.map((flag, index) => (
          <span key={`${flag.label}-${index}`} className={`px-2 py-1 rounded ${flag.severity === 'high' ? 'bg-rose-500/15 text-rose-200' : 'bg-amber-500/10 text-amber-200'}`}>
            风险: {compactText(flag.label, 90)}
          </span>
        ))}
      </div>

      <details className="rounded-md border border-white/[0.05] bg-black/20">
        <summary className="cursor-pointer px-3 py-2 text-[10.5px] text-cyan-200">展开完整6层</summary>
        <div className="p-3 grid gap-2">
          {fullLayers.map(([label, layer]) => (
            <div key={label as string}>
              <div className="text-[9.5px] text-slate-500 mb-1">{label as string}</div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-[10px] leading-relaxed text-slate-300">{JSON.stringify(layer || {}, null, 2)}</pre>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

export function CampaignRetrospectiveTab({
  project,
  rows,
  stats,
  health,
  videoAnalysisCache,
  videoAnalysisLoading,
  videoAnalysisError,
  onCopy,
  onPendingAction,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  analytics: ProjectAnalyticsSummary;
  health: ReturnType<typeof healthForRows>;
  bottleneck: ReturnType<typeof bottleneckForRows>;
  videoAnalysisCache?: VkpiProjectVideoAnalysisCacheResponse | null;
  videoAnalysisLoading?: boolean;
  videoAnalysisError?: string;
  onCopy: (text: string, label: string) => Promise<void>;
  onPendingAction: (label: string) => void;
}) {
  const projectTitle = project.campaign || project.productName || '未命名项目';
  const projectHealth = project.healthScore ?? health.score;
  const publishedKols = rows
    .filter((row) => stageIndex(row.stage) >= stageIndex('published') || (row.evidenceCount || 0) > 0 || (row.views || 0) > 0)
    .sort((a, b) => ((b.views || 0) - (a.views || 0)) || ((b.gmv || 0) - (a.gmv || 0)));
  const withShopify = publishedKols.filter((row) => Boolean(row.shopifyLink || row.clicks || row.orders || row.gmv));
  const withoutShopify = publishedKols.filter((row) => !withShopify.includes(row));
  const retrospectiveDraft = buildRetrospectiveDraftText(project, rows, stats, projectHealth, publishedKols, withShopify, withoutShopify);
  const analysisItemMap = useMemo(() => buildAnalysisItemMap(videoAnalysisCache?.items || []), [videoAnalysisCache?.items]);
  const analysisSummary = videoAnalysisCache?.summary;

  function compositeScore(row: VkpiProjectRow) {
    const hasShopify = Boolean(row.shopifyLink || row.clicks || row.orders || row.gmv);
    if (!hasShopify || !(row.views || 0)) return null;
    const shares = retrospectiveNumberField(row, ['shares', 'shareCount', 'share_count']);
    const viewsNorm = Math.min((row.views || 0) / 100000, 10) * 4;
    const engageNorm = Math.min(((row.likes || 0) + (row.comments || 0) * 5 + shares) / 5000, 10) * 2;
    const clickNorm = Math.min((row.clicks || 0) / 100, 10) * 2;
    const gmvNorm = Math.min((row.gmv || 0) / 500, 10) * 2;
    return Math.round(Math.min(100, viewsNorm + engageNorm + clickNorm + gmvNorm));
  }

  return (
    <div className="p-4 space-y-4" aria-label="项目复盘">
      <div className="rounded-lg border border-purple-500/30 bg-gradient-to-br from-purple-500/10 to-emerald-500/5 p-4">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-full bg-purple-500/20 flex items-center justify-center shrink-0">
            <BookOpen size={17} className="text-purple-300" />
          </div>
          <div className="flex-1">
            <div className="flex items-center justify-between gap-3 mb-1.5">
              <div className="text-[12px] font-semibold text-white">AI 项目复盘总结</div>
              <button
                type="button"
                className="px-2.5 py-1 rounded-md bg-purple-500/15 hover:bg-purple-500/25 text-purple-200 text-[10.5px] font-medium flex items-center gap-1 shrink-0"
                onClick={() => (onCopy ? void onCopy(retrospectiveDraft, '复盘草稿') : onPendingAction('复制复盘草稿'))}
              >
                <BookOpen size={10} />复制复盘草稿
              </button>
            </div>
            <div className="text-[10.5px] text-slate-300 leading-relaxed">
              项目 {projectTitle} · 健康度{' '}
              <span className="font-bold" style={{ color: healthColor(projectHealth) }}>{projectHealth}</span>
              {` · ${publishedKols.length}/${rows.length} 已发布。`}
              <br />
              {withShopify.length > 0 ? `${withShopify.length} 个 KOL 已接入 Shopify 归因。` : null}
              {withoutShopify.length > 0 ? (
                <span className="text-amber-400">
                  {`${withoutShopify.length} 个未接 Shopify,不参与 GMV / ROI 综合得分。`}
                </span>
              ) : null}
              {publishedKols.length === 0 ? '等待 KOL 推进到「已发布」阶段后开始复盘。' : null}
            </div>
            <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2">
              {[
                ['成品视频', analysisSummary?.evidence_count ?? 0, '#06b6d4'],
                ['已分析', analysisSummary?.ready_count ?? 0, '#10b981'],
                ['队列中', analysisSummary?.pending_count ?? 0, '#facc15'],
                ['后续维度', '沟通/合同/时效/反馈', '#a855f7'],
              ].map(([label, value, color]) => (
                <div key={label as string} className="rounded-md border border-white/[0.05] bg-black/20 px-2.5 py-2">
                  <div className="text-[9px] text-slate-500 mb-0.5">{label as string}</div>
                  <div className="text-[12px] font-semibold" style={{ color: color as string }}>{String(value)}</div>
                </div>
              ))}
            </div>
            {videoAnalysisError ? <div className="mt-2 text-[10px] text-rose-300">final_v1 缓存读取失败：{videoAnalysisError}</div> : null}
          </div>
        </div>
      </div>

      {publishedKols.length === 0 ? (
        <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
          <BookOpen size={24} className="text-slate-600 mx-auto mb-2" />
          <div className="text-[11.5px] text-slate-400">等待 KOL 推进到「已发布」阶段后开始复盘</div>
        </div>
      ) : (
        <div className="space-y-3">
          {publishedKols.map((row) => {
            const hasShopify = Boolean(row.shopifyLink || row.clicks || row.orders || row.gmv);
            const score = compositeScore(row);
            const displayName = row.kolName || row.kolHandle || 'Unknown';
            const handle = row.kolHandle || displayName;
            const videoUrl = retrospectiveVideoUrl(row);
            const videoTitle = retrospectiveVideoTitle(row, projectTitle);
            const shares = retrospectiveNumberField(row, ['shares', 'shareCount', 'share_count']);
            const analysisItems = analysisItemsForRow(row, analysisItemMap);

            return (
              <div key={row.id} className={`rounded-lg border p-4 ${hasShopify ? 'border-white/[0.06] bg-white/[0.015]' : 'border-white/[0.04] bg-white/[0.008]'}`}>
                <div className="flex items-center gap-3 mb-3">
                  {row.kolAvatar ? (
                    <img src={row.kolAvatar} alt={displayName} className="w-10 h-10 rounded-full object-cover shrink-0" />
                  ) : (
                    <div
                      className="w-10 h-10 rounded-full flex items-center justify-center text-[12px] font-bold text-white shrink-0"
                      style={{ background: hasShopify ? 'linear-gradient(135deg,#10b981,#06b6d4)' : 'linear-gradient(135deg,#64748b,#475569)' }}
                    >
                      {retrospectiveRowInitial(row)}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <div className="text-[12.5px] font-semibold text-white truncate">{displayName}</div>
                      {!hasShopify ? <span className="text-[9.5px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300">未接归因</span> : null}
                    </div>
                    <div className="text-[10px] text-slate-500 truncate">{row.platform} · {handle}</div>
                  </div>
                  {hasShopify && score !== null ? (
                    <div className="text-right shrink-0">
                      <div className="text-[10px] text-slate-500 mb-0.5">综合得分</div>
                      <div className="text-[26px] font-bold tabular-nums leading-none" style={{ color: healthColor(score) }}>{score}</div>
                    </div>
                  ) : null}
                </div>

                <div className="flex items-center gap-2 mb-3 p-2 rounded bg-black/30">
                  <div className="w-12 h-9 rounded bg-gradient-to-br from-purple-500/30 to-cyan-500/30 flex items-center justify-center shrink-0">
                    <Video size={14} className="text-white" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] text-white truncate">{videoTitle}</div>
                    <div className="text-[9.5px] text-slate-500">播放时长 {retrospectiveWatchTime(row)}</div>
                  </div>
                  {videoUrl ? (
                    <a href={videoUrl} target="_blank" rel="noreferrer" className="text-[10px] text-cyan-300 flex items-center gap-1 shrink-0">
                      <ExternalLink size={10} /> 打开
                    </a>
                  ) : (
                    <button type="button" className="text-[10px] text-cyan-300 flex items-center gap-1 shrink-0" onClick={() => onPendingAction('打开视频 / AI 分析')}>
                      <ExternalLink size={10} /> 打开
                    </button>
                  )}
                </div>

                <div className="flex items-start gap-2 mb-3 px-2.5 py-2 rounded bg-purple-500/5">
                  <Sparkles size={11} className="text-purple-300 mt-0.5 shrink-0" />
                  <div className="text-[10.5px] text-slate-300 leading-relaxed">
                    <span className="text-purple-300 font-medium">项目表现摘要: </span>
                    {hasShopify
                      ? `内容表现已汇总 · 曝光 ${formatLargeNum(row.views)} · 互动 ${formatLargeNum((row.likes || 0) + (row.comments || 0) + shares)} · Shopify 点击 ${formatLargeNum(row.clicks || 0)} · 归因 GMV ${formatMoneyShort(row.gmv)}`
                      : `内容表现已汇总 · 曝光 ${formatLargeNum(row.views)} · 互动 ${formatLargeNum((row.likes || 0) + (row.comments || 0) + shares)} · 尚未接 Shopify 归因,综合得分暂不计算`}
                  </div>
                </div>

                <div className="mb-3 rounded-lg border border-white/[0.05] bg-black/20 p-3">
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div>
                      <div className="text-[10px] text-slate-500">复盘维度 · 成品分析</div>
                      <div className="text-[11.5px] text-white font-semibold">final_v1 视频分析</div>
                    </div>
                    <span className="text-[9.5px] text-slate-500">后续可叠加沟通截图 / PDF合同 / 时效 / 市场反馈</span>
                  </div>
                  {videoAnalysisLoading ? (
                    <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3 text-[10.5px] text-slate-400">读取 final_v1 缓存...</div>
                  ) : analysisItems.length ? (
                    <div className="space-y-2">
                      {analysisItems.map((item) => (
                        <RetrospectiveCardErrorBoundary
                          key={String(item.evidence_id ?? item.content_url ?? `${row.id}:analysis`)}
                          label={`${displayName} evidence #${item.evidence_id || '-'}`}
                        >
                          <ProjectVideoAnalysisCard
                            row={row}
                            item={item}
                          />
                        </RetrospectiveCardErrorBoundary>
                      ))}
                    </div>
                  ) : (
                    <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3">
                      <div className="text-[10.5px] text-slate-400">暂无分析</div>
                      <div className="text-[9.5px] text-slate-600 mt-1">未匹配到该 KOL 的 video evidence 或 final_v1 队列记录。</div>
                    </div>
                  )}
                </div>

                <div className="grid grid-cols-3 md:grid-cols-5 gap-3">
                  {[
                    ['播放', formatLargeNum(row.views), '#06b6d4'],
                    ['点赞', formatLargeNum(row.likes || 0), '#ec4899'],
                    ['评论', formatLargeNum(row.comments || 0), '#a855f7'],
                    ['分享', formatLargeNum(shares), '#fb923c'],
                    hasShopify ? ['Shopify 点击', formatLargeNum(row.clicks || 0), '#10b981'] : ['Shopify', '—', '#64748b'],
                  ].map(([label, value, color]) => (
                    <div key={label}>
                      <div className="text-[9.5px] text-slate-500 mb-0.5">{label}</div>
                      <div className="text-[13px] font-semibold tabular-nums" style={{ color }}>{value}</div>
                    </div>
                  ))}
                </div>

                {hasShopify ? (
                  <div className="mt-3 pt-3 border-t border-white/[0.04] flex items-center gap-2 px-2 py-1.5 rounded bg-emerald-500/5">
                    <ShoppingCart size={11} className="text-emerald-300" />
                    <div className="flex-1 text-[10.5px] text-emerald-200">
                      Shopify 归因: {formatLargeNum(row.orders || 0)} 单 · GMV <span className="font-bold">{formatMoneyShort(row.gmv)}</span>
                    </div>
                    {row.shopifyLink ? (
                      <div className="text-[10px] text-slate-500 font-mono truncate max-w-[200px]">{row.shopifyLink.replace('https://', '')}</div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function CampaignAnalyticsTab({
  rows,
  stats,
}: {
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  analytics: ProjectAnalyticsSummary;
  health: ReturnType<typeof healthForRows>;
}) {
  const [rankingSort, setRankingSort] = useState<AnalyticsRankingSort>('views');
  const totalLikes = rows.reduce((sum, row) => sum + (row.likes || 0), 0);
  const totalComments = rows.reduce((sum, row) => sum + (row.comments || 0), 0);
  const publishedKols = rows.filter(analyticsHasPublishedEvidence);
  const projectTotalCost = stats.cost || 0;
  const roi = projectTotalCost > 0 ? ((stats.gmv / projectTotalCost) * 100).toFixed(1) : '—';
  const kpis: Array<[string, string, typeof Eye, string]> = [
    ['总曝光', formatLargeNum(stats.views), Eye, '#06b6d4'],
    ['总点赞', formatLargeNum(totalLikes), Heart, '#ec4899'],
    ['总评论', formatLargeNum(totalComments), MessageCircle, '#a855f7'],
    ['Shopify 点击', formatLargeNum(stats.clicks), ShoppingCart, '#fb923c'],
    ['归因 GMV', formatMoneyShort(stats.gmv), DollarSign, '#10b981'],
    ['ROI', `${roi}${roi !== '—' ? '%' : ''}`, TrendingUp, '#10b981'],
  ];
  const rankedRows = [...publishedKols]
    .sort((a, b) => {
      if (rankingSort === 'published') {
        const aTime = analyticsRankingPublishTime(a, rankingSort);
        const bTime = analyticsRankingPublishTime(b, rankingSort);
        if (aTime !== bTime) return bTime > aTime ? 1 : -1;
      }
      return ((b.views || 0) - (a.views || 0)) || ((b.gmv || 0) - (a.gmv || 0));
    });

  return (
    <div className="p-4 space-y-4" aria-label="项目数据汇总">
      <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 flex items-start gap-2.5">
        <div className="shrink-0 w-7 h-7 rounded-full bg-purple-500/20 flex items-center justify-center">
          <Sparkles size={13} className="text-purple-300" />
        </div>
        <div className="flex-1">
          <div className="text-[11px] font-medium text-purple-200 mb-0.5">AI 项目数据洞察</div>
          <div className="text-[10.5px] text-slate-300 leading-relaxed">
            {`${publishedKols.length}/${rows.length} 已发布,总曝光 ${formatLargeNum(stats.views)} · Shopify 点击 ${formatLargeNum(stats.clicks)},归因 GMV ${formatMoneyShort(stats.gmv)} · ROI ${roi}%。总成本 (当前 stats.cost 口径) ${formatMoneyShort(projectTotalCost)}`}
          </div>
          <div className="mt-1 text-[10px] text-amber-300 leading-relaxed">
            {projectEvidenceCoverageLabel()} · 曝光/点赞/评论/排名仅统计已归属当前项目的数据,Shopify / GMV / ROI 等待独立归因链路。
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        {kpis.map(([label, value, Icon, color]) => (
          <div key={label} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-3">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] text-slate-500">{label}</span>
              <Icon size={11} style={{ color }} />
            </div>
            <div className="text-[18px] font-bold tabular-nums" style={{ color }}>{value}</div>
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-4">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-[12.5px] font-semibold text-white">KOL 性能排名</h4>
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-slate-500">{publishedKols.length} 个已发布</span>
            <div className="flex items-center rounded-lg border border-white/[0.06] bg-black/20 p-0.5">
              {[
                ['views', '按播放量'],
                ['published', '按发布时间'],
              ].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setRankingSort(value as AnalyticsRankingSort)}
                  className={`rounded-md px-2 py-1 text-[10px] font-medium transition ${
                    rankingSort === value
                      ? 'bg-purple-500/25 text-purple-200'
                      : 'text-slate-500 hover:text-white'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
        {publishedKols.length === 0 ? (
          <div className="text-center py-6 text-[11px] text-slate-500">暂无已发布视频</div>
        ) : (
          <div className="space-y-2">
            {rankedRows.map((row, index) => {
              const avatarName = row.kolName || row.kolHandle || '-';
              const watchTime = analyticsWatchTime(row);
              const profileUrl = analyticsKolProfileUrl(row);
              const videoUrl = analyticsRankingVideoUrl(row, rankingSort);
              const videoTitle = analyticsRankingVideoTitle(row, rankingSort, '项目内容');
              const publishDate = analyticsRankingPublishDate(row, rankingSort);
              const avatarNode = row.kolAvatar ? (
                <img
                  src={row.kolAvatar}
                  alt={avatarName}
                  className="w-7 h-7 rounded-full object-cover shrink-0 border border-white/[0.08]"
                />
              ) : (
                <div
                  className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white shrink-0"
                  style={{ background: 'linear-gradient(135deg,#a855f7,#ec4899)' }}
                >
                  {avatarName.charAt(0).toUpperCase()}
                </div>
              );
              return (
                <div key={row.id} className="flex items-center gap-3 px-2 py-2 rounded hover:bg-white/[0.02]">
                  <div className="text-[11px] font-bold text-slate-500 w-5">#{index + 1}</div>
                  {profileUrl ? (
                    <a
                      href={profileUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="flex items-center gap-3 flex-1 min-w-0 hover:opacity-90"
                      title={`打开 ${row.kolName || row.kolHandle} 主页`}
                    >
                      {avatarNode}
                      <div className="min-w-0">
                        <div className="text-[11.5px] text-white font-medium truncate">{row.kolHandle || row.kolName}</div>
                        <div className="text-[10px] text-slate-500 truncate">{row.platform} · 完播 {watchTime}</div>
                      </div>
                    </a>
                  ) : (
                    <div className="flex items-center gap-3 flex-1 min-w-0">
                      {avatarNode}
                      <div className="min-w-0">
                        <div className="text-[11.5px] text-white font-medium truncate">{row.kolHandle || row.kolName}</div>
                        <div className="text-[10px] text-slate-500 truncate">{row.platform} · 完播 {watchTime}</div>
                      </div>
                    </div>
                  )}
                  <div className="text-right">
                    {videoUrl ? (
                      <div className="flex flex-col items-end">
                        <a
                          href={videoUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[12px] font-semibold text-white tabular-nums hover:text-cyan-300"
                          title={`打开视频: ${videoTitle}`}
                        >
                          {formatLargeNum(row.views)}
                        </a>
                        <a
                          href={videoUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[9.5px] font-medium text-cyan-300 hover:text-cyan-200"
                          title={`打开视频: ${videoTitle}`}
                        >
                          看视频
                        </a>
                      </div>
                    ) : profileUrl ? (
                      <div className="flex flex-col items-end">
                        <a
                          href={profileUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[12px] font-semibold text-slate-300 tabular-nums hover:text-cyan-300"
                          title={`数据库暂无视频 evidence,打开 ${row.kolName || row.kolHandle} 主页继续搜索`}
                        >
                          {formatLargeNum(row.views)}
                        </a>
                        <a
                          href={profileUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[9.5px] font-medium text-amber-300 hover:text-amber-200"
                          title="数据库暂无视频 evidence,先打开主页；Apify 自动搜索产品 URL 需要单独后端任务接入"
                        >
                          主页搜索
                        </a>
                      </div>
                    ) : (
                      <div className="flex flex-col items-end">
                        <div className="text-[12px] font-semibold text-slate-500 tabular-nums">{formatLargeNum(row.views)}</div>
                        <div className="text-[9.5px] font-medium text-slate-600" title="当前项目下没有视频 evidence URL,也没有可用主页">无可用入口</div>
                      </div>
                    )}
                    <div className="text-[9.5px] text-slate-500">
                      {rankingSort === 'published' ? analyticsPublishDateLabel(publishDate) : '播放'}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-[12px] font-semibold tabular-nums" style={{ color: row.shopifyLink ? '#10b981' : '#64748b' }}>
                      {row.shopifyLink ? formatMoneyShort(row.gmv) : '—'}
                    </div>
                    <div className="text-[9.5px] text-slate-500">GMV</div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

export function CampaignFinanceTab({
  rows,
  expenseLines,
  costRows,
  onOpenCostEntry,
}: {
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  expenseLines: ExpenseLine[];
  stageCosts: StageCostSummary[];
  costRows: Array<Record<string, unknown>>;
  onOpenShippingInfo: () => void;
  onOpenCostEntry?: (row: VkpiProjectRow, type?: 'cash_fee' | 'shipping' | 'product') => void;
}) {
  const ledgerTotals = costLedgerTotals(costRows);
  const expenseById = new Map(expenseLines.map((line) => [line.id, line]));
  const rowCosts = rows.map((row) => {
    const shippingFee = costRowAmount(costRows, row, 'shipping');
    const productSent = rowProductSent(row);
    const productCostAmount = costRowAmount(costRows, row, 'product') || productCost(productSent);
    const ledgerContractFee = costRowAmount(costRows, row, 'contract');
    const expenseAmount = expenseById.get(row.id)?.amount ?? row.cost ?? 0;
    const contractFee = ledgerContractFee || Math.max(expenseAmount - shippingFee - productCostAmount, 0);
    return {
      row,
      contractFee,
      shippingFee,
      productSent,
      productCost: productCostAmount,
      total: contractFee + shippingFee + productCostAmount,
      hasContract: contractFee > 0,
    };
  });
  const totalContract = ledgerTotals.contract || rowCosts.reduce((sum, item) => sum + item.contractFee, 0);
  const totalShipping = ledgerTotals.shipping || rowCosts.reduce((sum, item) => sum + item.shippingFee, 0);
  const totalProductCost = ledgerTotals.product || rowCosts.reduce((sum, item) => sum + item.productCost, 0);
  const totalProductRetail = totalProductCost;
  const totalAll = totalContract + totalShipping + totalProductCost;
  const rowsWithContract = rowCosts.filter((item) => item.contractFee > 0).length;
  const rowsWithProductCost = rowCosts.filter((item) => item.productCost > 0).length;
  const averageCost = totalAll / Math.max(rowCosts.filter((item) => item.contractFee + item.productCost > 0).length, 1);

  return (
    <div className="p-4 space-y-4" aria-label="项目费用">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          ['合同费用', totalContract, '#a855f7', '已合作阶段录入'],
          ['快递费', totalShipping, '#06b6d4', '已发货阶段录入'],
          ['产品成本', totalProductCost, '#10b981', `零售价 ${formatMoneyShort(totalProductRetail)}`],
          ['总成本', totalAll, '#fb923c', `${rows.length} 个 KOL`],
        ].map(([label, value, color, sub]) => (
          <div key={String(label)} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-3">
            <div className="text-[10px] text-slate-500 mb-1">{label}</div>
            <div className="text-[20px] font-bold tabular-nums" style={{ color: String(color) }}>{formatMoneyShort(Number(value))}</div>
            <div className="text-[9.5px] text-slate-500 mt-1">{sub}</div>
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 flex items-start gap-2.5">
        <Sparkles size={13} className="text-purple-300 mt-0.5 shrink-0" />
        <div className="text-[10.5px] text-slate-300">
          已自动从 {rowsWithContract} 份合同提取费用 + {rowsWithProductCost} 个 KOL 计入产品成本 · 平均 KOL 总成本{' '}
          <span className="text-purple-300 font-semibold">{formatMoneyShort(averageCost)}</span>
        </div>
      </div>

      <div className="rounded-lg border border-white/[0.06] bg-white/[0.015] overflow-hidden">
        <div className="px-4 py-2.5 border-b border-white/[0.05] flex items-center justify-between gap-3">
          <h4 className="text-[12px] font-semibold text-white">KOL 费用明细</h4>
          {rows[0] && onOpenCostEntry ? (
            <button
              type="button"
              className="rounded-lg border border-purple-400/30 bg-purple-500/10 px-2.5 py-1 text-[10.5px] font-semibold text-purple-200 hover:bg-purple-500/18 transition"
              onClick={() => onOpenCostEntry(rows[0], 'cash_fee')}
            >
              + 录入费用
            </button>
          ) : null}
        </div>
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-left text-[10px] text-slate-500 border-b border-white/[0.04]">
              {['KOL', '合同费', '快递费', '产品 (成本)', '小计', '状态', '操作'].map((header) => (
                <th key={header} className="px-4 py-2 font-medium">{header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rowCosts.map((item) => (
              <tr key={item.row.id} className="border-b border-white/[0.03] hover:bg-white/[0.012]">
                <td className="px-4 py-2.5">
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-bold text-white shrink-0" style={{ background: 'linear-gradient(135deg,#a855f7,#ec4899)' }}>
                      {(item.row.kolName || item.row.kolHandle || '-').charAt(0).toUpperCase()}
                    </div>
                    <div>
                      <div className="text-white text-[11px]">{item.row.kolHandle || item.row.kolName}</div>
                      <div className="text-[9.5px] text-slate-500">{item.row.platform}</div>
                    </div>
                  </div>
                </td>
                <td className="px-4 py-2.5 text-slate-300 tabular-nums">
                  {item.hasContract ? formatMoneyShort(item.contractFee) : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-2.5 text-slate-300 tabular-nums">
                  {item.shippingFee > 0 ? formatMoneyShort(item.shippingFee) : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-2.5 tabular-nums">
                  {item.productCost > 0 ? (
                    <span>
                      <span className="text-emerald-400">{formatMoneyShort(item.productCost)}</span>
                      <span className="text-[9.5px] text-slate-500 ml-1">({Math.max(item.productSent.length, 1)}件)</span>
                    </span>
                  ) : (
                    <span className="text-slate-600">—</span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-white font-semibold tabular-nums">
                  {item.total > 0 ? formatMoneyShort(item.total) : <span className="text-slate-600 font-normal">—</span>}
                </td>
                <td className="px-4 py-2.5">
                  {item.hasContract ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300">已签合同</span>
                  ) : (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-500">待签约</span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  {onOpenCostEntry ? (
                    <button
                      type="button"
                      className="rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[10px] font-medium text-slate-200 hover:border-purple-400/40 hover:text-white transition"
                      onClick={() => onOpenCostEntry(item.row, item.hasContract ? 'shipping' : 'cash_fee')}
                    >
                      录入费用
                    </button>
                  ) : (
                    <span className="text-slate-600">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function CampaignMaterialsTab({
  project,
  rows,
  stats,
  costRows = [],
  onCopy,
  onPendingAction,
}: {
  project?: VkpiProjectRow;
  rows: VkpiProjectRow[];
  stats?: ProjectStatsSummary;
  costRows?: Array<Record<string, unknown>>;
  onCopy?: (text: string, label: string) => Promise<void>;
  onPendingAction: (label: string) => void;
}) {
  const [section, setSection] = useState<MaterialSection>('assets');
  const shipped = rows.filter((row) => stageIndex(row.stage) >= stageIndex('shipped'));
  const readyAssets = PROJECT_ASSETS.filter((asset) => asset.status === 'ready').length;
  const briefText = buildMaterialBriefText(project, rows, stats);

  return (
    <div aria-label="项目物料">
      <div className="flex items-center gap-1 px-4 pt-3 border-b border-white/[0.04]" aria-label="物料子 tab">
        {[
          { key: 'assets' as const, label: '营销物料', icon: Boxes },
          { key: 'logistics' as const, label: '快递追踪 · 公开', icon: Package },
        ].map((item) => {
          const Icon = item.icon;
          const active = section === item.key;
          return (
            <button
              key={item.key}
              onClick={() => setSection(item.key)}
              className={`px-3 py-2 text-[11.5px] font-medium border-b-2 flex items-center gap-1.5 transition-all ${active ? 'text-purple-300 border-purple-500' : 'text-slate-400 border-transparent hover:text-white'}`}
              type="button"
            >
              <Icon size={11} />
              {item.label}
            </button>
          );
        })}
      </div>

      {section === 'assets' ? (
        <div className="p-4 space-y-3">
          <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 flex items-start gap-2.5">
            <Sparkles size={13} className="text-purple-300 mt-0.5 shrink-0" />
            <div className="text-[10.5px] text-slate-300 flex-1">
              项目级物料 · 所有 KOL 共享 · LLM 自动起草模板 + 你 review · <span className="text-purple-300">{readyAssets}/{PROJECT_ASSETS.length} 已就绪</span>
            </div>
            <button
              className="px-2.5 py-1 rounded-md bg-white/[0.04] hover:bg-white/[0.08] text-slate-200 text-[10.5px] font-medium flex items-center gap-1"
              type="button"
              onClick={() => (onCopy ? void onCopy(briefText, '项目 Brief') : onPendingAction('复制 Brief'))}
            >
              <FileText size={10} />复制 Brief
            </button>
            <button className="px-2.5 py-1 rounded-md bg-purple-500/90 hover:bg-purple-500 text-white text-[10.5px] font-medium flex items-center gap-1" type="button" onClick={() => onPendingAction('上传/起草')}>
              <Plus size={10} />上传/起草
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {PROJECT_ASSETS.map((asset) => {
              const Icon = ASSET_ICON[asset.type] || FileText;
              return (
                <div key={asset.id} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-3">
                  <div className="flex items-start gap-2.5 mb-2">
                    <div className="w-9 h-9 rounded-lg bg-white/[0.04] flex items-center justify-center shrink-0">
                      <Icon size={16} className="text-slate-300" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-[11.5px] text-white font-medium truncate">{asset.name}</div>
                      <div className="text-[9.5px] text-slate-500 mt-0.5">{asset.size} · {asset.date}</div>
                    </div>
                    <span className={`text-[9.5px] px-1.5 py-0.5 rounded font-medium ${STATUS_BG[asset.status]}`}>{STATUS_LABEL[asset.status]}</span>
                  </div>
                  <div className="flex items-start gap-1 mb-2 px-2 py-1 rounded bg-purple-500/[0.04]">
                    <Sparkles size={9} className="text-purple-300 shrink-0 mt-0.5" />
                    <div className="text-[9.5px] text-slate-400">{asset.aiDesc}</div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {asset.status === 'ready' ? (
                      <button className="text-[10px] text-cyan-300 hover:text-cyan-200 px-2 py-0.5 rounded bg-cyan-500/10 flex items-center gap-1" type="button" onClick={() => onPendingAction(`${asset.name} 下载`)}>
                        <Download size={9} />下载
                      </button>
                    ) : null}
                    {asset.status === 'draft' ? (
                      <button className="text-[10px] text-amber-300 hover:text-amber-200 px-2 py-0.5 rounded bg-amber-500/10 flex items-center gap-1" type="button" onClick={() => onPendingAction(`${asset.name} 审查`)}>
                        <Eye size={9} />审查
                      </button>
                    ) : null}
                    {asset.status === 'todo' ? (
                      <button className="text-[10px] text-purple-200 hover:text-white px-2 py-0.5 rounded bg-purple-500/15 flex items-center gap-1" type="button" onClick={() => onPendingAction(`${asset.name} AI 起草`)}>
                        <Sparkles size={9} />AI 起草
                      </button>
                    ) : null}
                    <button className="text-[10px] text-slate-400 hover:text-white px-2 py-0.5 rounded hover:bg-white/[0.04] flex items-center gap-1" type="button" onClick={() => onPendingAction(`${asset.name} 编辑`)}>
                      <Edit3 size={9} />编辑
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="p-4 space-y-3">
          {shipped.length === 0 ? (
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
              <Package size={24} className="text-slate-600 mx-auto mb-2" />
              <div className="text-[11.5px] text-slate-400 mb-1">暂无快递记录</div>
              <div className="text-[10.5px] text-slate-500">KOL 进入「已发货」阶段时录入快递信息自动追踪</div>
            </div>
          ) : (
            shipped.map((row) => {
                const carrier = row.trackingCarrier || '待识别快递';
                const trackingNumber = String(row.trackingNumber || '').trim();
                const tr = trackingNumber ? { carrier, no: trackingNumber } : null;
                const isDelivered = stageIndex(row.stage) >= stageIndex('received');
                const shippingCost = costRowAmount(costRows, row, 'shipping');
                const productSent = rowProductSent(row);
                const productCostAmount = costRowAmount(costRows, row, 'product') || productCost(productSent);
                const kolName = row.kolHandle || row.kolName || 'Unknown';
                const handle = row.kolName || row.kolHandle || '-';
                return (
                  <div key={row.id} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-4">
                    <div className="flex items-center gap-3 mb-3">
                      <div className="w-9 h-9 rounded-full flex items-center justify-center text-[12px] font-bold text-white shrink-0" style={{ background: 'linear-gradient(135deg,#a855f7,#06b6d4)' }}>
                        {kolName.charAt(0).toUpperCase()}
                      </div>
                      <div className="flex-1">
                        <div className="text-[12.5px] font-semibold text-white">{kolName}</div>
                        <div className="text-[10px] text-slate-500">{row.platform} · {handle}</div>
                      </div>
                      <span className={`text-[10px] px-2 py-0.5 rounded font-medium ${isDelivered ? 'bg-emerald-500/15 text-emerald-300' : 'bg-cyan-500/15 text-cyan-300'}`}>{isDelivered ? '已签收' : '在途中'}</span>
                    </div>
                    {tr ? (
                      <div className="px-3 py-2 rounded bg-black/30 mb-3 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Package size={13} className="text-cyan-400" />
                          <div>
                            <div className="text-[11px] text-white font-medium">{tr.carrier}</div>
                            <div className="text-[10px] text-slate-400 font-mono">{tr.no}</div>
                          </div>
                        </div>
                        <a className="text-[10px] text-cyan-300 hover:text-cyan-200 flex items-center gap-1" href={trackingUrl(tr.carrier, tr.no)} target="_blank" rel="noreferrer">
                          <ExternalLink size={10} />外部追踪
                        </a>
                      </div>
                    ) : null}
                    <div className="flex items-center gap-1.5 mb-2">
                      {['已发货', '在途', '派送中', '已签收'].map((step, index) => {
                        const stepDone = isDelivered ? true : index <= 1;
                        return (
                          <div key={step} className="flex-1 flex items-center gap-1">
                            <div className={`w-2 h-2 rounded-full ${stepDone ? 'bg-emerald-400' : 'bg-white/[0.08]'}`} />
                            <div className={`flex-1 text-[9.5px] ${stepDone ? 'text-emerald-300' : 'text-slate-600'}`}>{step}</div>
                            {index < 3 ? <div className={`h-px flex-1 ${stepDone && (isDelivered ? true : index + 1 <= 1) ? 'bg-emerald-400/40' : 'bg-white/[0.05]'}`} /> : null}
                          </div>
                        );
                      })}
                    </div>
                    <div className="flex items-center justify-between gap-3 pt-2 border-t border-white/[0.04]">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {productSent.map((item) => <span key={item} className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-300">{item}</span>)}
                      </div>
                      <div className="text-[10px] text-slate-400">
                        快递费 <span className="text-white tabular-nums">{formatMoneyShort(shippingCost)}</span>
                        {' · '}
                        产品成本 <span className="text-emerald-400 tabular-nums">{formatMoneyShort(productCostAmount)}</span>
                      </div>
                    </div>
                  </div>
                );
              })
          )}
        </div>
      )}
    </div>
  );
}
