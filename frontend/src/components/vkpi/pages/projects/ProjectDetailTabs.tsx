import { Component, useEffect, useMemo, useRef, useState, type ErrorInfo, type ReactNode } from 'react';
import { Activity, AlertCircle, BookOpen, Boxes, Check, DollarSign, ExternalLink, Eye, FileText, Heart, MessageCircle, Package, ShoppingCart, Sparkles, TrendingUp, Upload, Video } from 'lucide-react';
import { stageLabels } from '../../shared/vkpiConstants';
import { ProjectEvidenceForms } from '../../drawers/ProjectEvidenceForms';
import { ProjectTimeline } from '../../v615-replica/components/ProjectTimeline';
import type { VkpiProjectDetail, VkpiProjectRow } from '../../vkpiTypes';
import type { VkpiAnalysisCacheEntry, VkpiProjectContract, VkpiProjectContractsResponse, VkpiProjectRetrospectiveResult, VkpiProjectVideoAnalysisCacheItem, VkpiProjectVideoAnalysisCacheResponse } from '../../../../services/vkpi/projects-api';
import { formatLargeNum, formatMoneyShort, healthColor, PROJECT_STAGE_COLOR, PROJECT_STAGE_FLOW } from './projectDeliverableStyle';
import {
  healthForRows,
  stageIndex,
  type ContractLine,
  type ExpenseLine,
  type ProjectStatsSummary,
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

function buildMaterialBriefText(project: VkpiProjectRow | undefined, rows: VkpiProjectRow[], stats?: ProjectStatsSummary) {
  const projectTitle = project?.campaign || project?.productName || rows[0]?.productName || '未命名项目';
  const product = project?.productName || rows[0]?.productName || projectTitle;
  const sku = project?.productSku || rows[0]?.productSku || '待补充';
  const platforms = Array.from(new Set(rows.map((row) => row.platform).filter(Boolean))).join(', ') || '待确认';
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
    '## 执行口径',
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
    // stats.roi 是比值(gmv/cost);同页 KPI 口径显示百分数,这里同样 ×100,不再把比值直接当百分数。
    `ROI: ${stats.roi == null ? '待成本/归因补齐' : `${(stats.roi * 100).toFixed(1)}%`}`,
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

function productCost(productSent: string[], unitCosts: Record<string, number> = {}) {
  if (!productSent.length) return 0;
  return productSent.reduce((sum, item) => {
    const key = String(item || '').trim();
    return sum + (Number(unitCosts[key]) || Number(unitCosts[key.toLowerCase()]) || 0);
  }, 0);
}

function trackingUrl(carrier: string, trackingNumber: string) {
  const normalizedCarrier = carrier.toLowerCase();
  const encoded = encodeURIComponent(trackingNumber);
  if (normalizedCarrier.includes('dhl')) return `https://www.dhl.com/us-en/home/tracking/tracking-express.html?submit=1&tracking-id=${encoded}`;
  if (normalizedCarrier.includes('fedex')) return `https://www.fedex.com/fedextrack/?trknbr=${encoded}`;
  if (normalizedCarrier.includes('ups')) return `https://www.ups.com/track?tracknum=${encoded}`;
  if (normalizedCarrier.includes('usps')) return `https://tools.usps.com/go/TrackConfirmAction?tLabels=${encoded}`;
  // 17track 免费聚合页:覆盖国内外承运商,比裸 Google 搜索稳定(2026-06-12 免费查单裁令)
  return `https://t.17track.net/zh-cn#nums=${encoded}`;
}

function deliveredByStageOrStatus(row: VkpiProjectRow) {
  const status = String(row.trackingStatus || '').toLowerCase();
  return stageIndex(row.stage) >= stageIndex('received') || /delivered|signed|received|签收|已送达|已到货/.test(status);
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
  apiToken,
  projectId,
}: {
  rows: VkpiProjectRow[];
  events?: VkpiProjectDetail['events'];
  apiToken?: string;
  projectId?: string;
}) {
  // W2 履约时间线:projectId 优先取传入,否则从 rows 派生(VkpiProjectRow.projectId)。
  // apiToken 若未透传则 ProjectTimeline 自动降级为只读阶段骨架(不报错)。
  const timelineProjectId = projectId || rows[0]?.projectId || '';
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
      {/* W2 履约时间线(只读):建→选→寄→签→观察→发布→复盘。渲染在真实事件流之上。 */}
      <ProjectTimeline apiToken={apiToken} projectId={timelineProjectId} />

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

function deliverableLabel(record: Record<string, unknown>): string {
  // 提取出的 deliverable 对象键是 content_type/platform/quantity/deadline/notes(合同提取 schema)。
  const head = stringValue(
    record.description || record.item || record.title || record.name || record.content_type || record.platform,
  );
  const qty = record.quantity != null && stringValue(record.quantity) ? `×${stringValue(record.quantity)}` : '';
  const platform = head !== stringValue(record.platform) ? stringValue(record.platform) : '';
  return [head, platform, qty].filter(Boolean).join(' · ') || stringValue(record.notes) || '交付项';
}

function compactList(value: unknown, fallback = '待确认') {
  const list = arrayValue(value);
  if (!list.length) return fallback;
  return list.map((item) => {
    if (item && typeof item === 'object') {
      return deliverableLabel(item as Record<string, unknown>);
    }
    return stringValue(item);
  }).filter(Boolean).slice(0, 3).join(' / ') || fallback;
}

// 把 deliverables 对象数组整理成人类可读的结构(不再把 JSON 代码塞进输入框)。
function summarizeDeliverables(value: unknown): { line: string; notes: string }[] {
  return arrayValue(value).map((item) => {
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>;
      const parts: string[] = [];
      const type = stringValue(record.content_type || record.type || record.title || record.name);
      const platform = stringValue(record.platform);
      const qty = record.quantity != null && stringValue(record.quantity) ? `×${stringValue(record.quantity)}` : '';
      const deadline = stringValue(record.deadline);
      if (type) parts.push(type);
      if (platform) parts.push(platform);
      if (qty) parts.push(qty);
      if (deadline) parts.push(`截止 ${deadline}`);
      return { line: parts.join(' · ') || '交付项', notes: stringValue(record.notes || record.description) };
    }
    return { line: stringValue(item), notes: '' };
  }).filter((d) => d.line || d.notes);
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
  cancellation_terms: string;
  revision_terms: string;
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
    cancellation_terms: stringValue(contract.cancellation_terms),
    revision_terms: stringValue(contract.revision_terms),
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
    cancellation_terms: draft.cancellation_terms.trim(),
    revision_terms: draft.revision_terms.trim(),
  };
  return { ...payload, manual_overrides: payload };
}

// GEN-/草稿行判定:生成器产出(文件名 GEN- 前缀)或 status=draft,可挂签署版。
function isGeneratedDraftContract(contract: VkpiProjectContract) {
  return /^GEN-/i.test(String(contract.file_name || '')) || String(contract.status || '') === 'draft';
}

function ContractArchiveCard({
  contract,
  row,
  busyKey,
  onSave,
  onConfirm,
  onOpen,
  onRetry,
  onDelete,
  onUploadSignedVersion,
}: {
  contract: VkpiProjectContract;
  row?: VkpiProjectRow;
  busyKey: string;
  onSave: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onConfirm: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onOpen: (contractId: number) => Promise<void>;
  onRetry: (contractId: number) => Promise<void>;
  onDelete?: (contractId: number, fileName?: string) => void;
  onUploadSignedVersion?: (contract: VkpiProjectContract) => void;
}) {
  const [draft, setDraft] = useState<ContractDraft>(() => initialContractDraft(contract));
  // 用户是否手改过本表单:手改后不让后台轮询/重提取的回填覆盖编辑中的内容。
  const editedRef = useRef(false);
  // 提取结果指纹:仅当服务端真值变化时变(纯本地编辑期间稳定,不会触发回填)。
  const extractionSig = useMemo(
    () =>
      JSON.stringify([
        contract.fee_amount, contract.fee_currency, contract.contract_duration,
        contract.start_date, contract.end_date, contract.platforms_json,
        contract.deliverable_count, contract.deliverables_json, contract.must_include_json,
        contract.usage_rights, contract.exclusivity, contract.buyout_rights,
        contract.breach_terms, contract.payment_terms, contract.cancellation_terms, contract.revision_terms,
      ]),
    [
      contract.fee_amount, contract.fee_currency, contract.contract_duration,
      contract.start_date, contract.end_date, contract.platforms_json,
      contract.deliverable_count, contract.deliverables_json, contract.must_include_json,
      contract.usage_rights, contract.exclusivity, contract.buyout_rights,
      contract.breach_terms, contract.payment_terms, contract.cancellation_terms, contract.revision_terms,
    ],
  );
  // 重新提取开始(processing)时清掉脏标记,让新结果可以回填。
  useEffect(() => {
    if (contract.extraction_status === 'processing') editedRef.current = false;
  }, [contract.extraction_status]);
  // 提取真值变化(上传后异步提取完成 / 重新提取)→ 未手改则把结果灌进表单。
  // 修复:此前用 useState 初始化函数只在挂载跑一次,processing 时挂载→提取完成后表单永远空。
  useEffect(() => {
    if (editedRef.current) return;
    setDraft(initialContractDraft(contract));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extractionSig]);
  const meta = contractStatusMeta(contract);
  // 一次解析存变量,避免渲染里对同一份 deliverables_json 双调 summarizeDeliverables。
  const deliverableSummaries = summarizeDeliverables(contract.deliverables_json);
  const saving = busyKey === `save:${contract.id}`;
  const confirming = busyKey === `confirm:${contract.id}`;
  const extracting = busyKey === `extract:${contract.id}` || contract.extraction_status === 'processing';
  const downloading = busyKey === `download:${contract.id}`;
  const deleting = busyKey === `delete:${contract.id}`;
  const update = (key: keyof ContractDraft, value: string) => {
    editedRef.current = true;
    setDraft((current) => ({ ...current, [key]: value }));
  };

  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.018] p-3 space-y-3">
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-lg bg-purple-500/10 border border-purple-400/20 flex items-center justify-center shrink-0">
          <FileText size={18} className="text-purple-300" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <strong className="text-[13px] text-white truncate">{contract.file_name || '合同文件'}</strong>
            <span className={`text-[9.5px] px-2 py-0.5 rounded-full border ${meta.className}`}>{meta.label}</span>
            {contract.status === 'confirmed' ? <span className="text-[9.5px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-300">人工确认</span> : null}
            {contract.signed_version_of ? (
              <span className="text-[9.5px] px-2 py-0.5 rounded-full border border-emerald-400/30 bg-emerald-400/10 text-emerald-300" title={`本文件是合同 #${contract.signed_version_of} 的签署版`}>
                签署版 · 关联 #{contract.signed_version_of}
              </span>
            ) : null}
            {contract.superseded_by ? (
              <span className="text-[9.5px] px-2 py-0.5 rounded-full border border-slate-400/25 bg-slate-400/10 text-slate-300" title={`该草稿已有签署版,见合同 #${contract.superseded_by}`}>
                已有签署版 #{contract.superseded_by}
              </span>
            ) : null}
          </div>
          <div className="text-[10.5px] text-slate-400 mt-1">
            {row?.kolHandle || row?.kolName || `KOL ${contract.kol_pool_id || '-'}`} · {row?.platform || compactList(contract.platforms_json, '平台待确认')} · {moneyLabel(contract.fee_amount, contract.fee_currency)}
          </div>
          <div className="text-[10px] text-slate-500 mt-1 truncate">
            交付: {compactList(contract.deliverables_json)} · 授权: {contract.usage_rights || contract.buyout_rights || '授权范围待确认'}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          {onUploadSignedVersion && isGeneratedDraftContract(contract) && !contract.superseded_by ? (
            <button
              className="px-2.5 py-1.5 rounded-md border border-emerald-400/30 bg-emerald-400/10 text-[10px] text-emerald-200 hover:bg-emerald-400/20"
              type="button"
              onClick={() => onUploadSignedVersion(contract)}
              title="签署完成后上传签署版,与本草稿双记录留痕"
            >
              上传签署版
            </button>
          ) : null}
          <button className="px-2.5 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] text-[10px] text-slate-300 hover:text-white" type="button" onClick={() => void onOpen(contract.id)} disabled={downloading}>
            <ExternalLink size={11} className="inline mr-1" />查看PDF
          </button>
          <button className="px-2.5 py-1.5 rounded-md border border-purple-400/25 bg-purple-400/10 text-[10px] text-purple-200 disabled:opacity-50" type="button" onClick={() => void onRetry(contract.id)} disabled={extracting || contract.extraction_status === 'skipped'} title={contract.extraction_status === 'skipped' ? 'DOC/DOCX v1 仅归档不提取，请上传 PDF 版本触发条款提取' : undefined}>
            {extracting ? '提取中' : '重新提取'}
          </button>
          {onDelete ? (
            <button className="px-2.5 py-1.5 rounded-md border border-red-400/25 bg-red-400/10 text-[10px] text-red-300 hover:text-red-200 disabled:opacity-50" type="button" onClick={() => onDelete(contract.id, contract.file_name || undefined)} disabled={deleting} title="删除该合同归档（原文件与提取结果一并删除）">
              {deleting ? '删除中' : '删除'}
            </button>
          ) : null}
        </div>
      </div>

      {contract.extraction_status === 'skipped' ? (
        <div className="rounded-lg border border-slate-500/20 bg-slate-500/10 p-3 text-[11px] text-slate-300">DOC/DOCX 已归档，v1 仅自动提取 PDF。可查看原文件，或上传 PDF 版本触发 Claude 条款提取。</div>
      ) : null}
      {contract.extraction_status === 'failed' ? (
        <div className="rounded-lg border border-red-500/25 bg-red-500/10 p-3 text-[11px] text-red-300">
          提取失败{(contract.raw_extracted_json as Record<string, unknown> | undefined)?.error ? `：${String((contract.raw_extracted_json as Record<string, unknown>).error).slice(0, 200)}` : ''}。可点「重新提取」重新入队。
        </div>
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

      <div className="text-[10px] text-slate-400">
        <span className="flex items-center justify-between gap-2 mb-1">Deliverables 交付物<ConfidenceBadge contract={contract} field="deliverables" /></span>
        <div className="w-full rounded-md border border-white/[0.08] bg-black/20 px-2.5 py-2 space-y-2">
          {deliverableSummaries.length ? (
            deliverableSummaries.map((d, index) => (
              <div key={index} className="flex gap-2">
                <span className="text-[10px] text-purple-300/70 mt-0.5 tabular-nums shrink-0">{index + 1}.</span>
                <div className="min-w-0">
                  <div className="text-[11px] text-white font-medium">{d.line}</div>
                  {d.notes && <div className="text-[10px] text-slate-400 mt-0.5 leading-relaxed">{d.notes}</div>}
                </div>
              </div>
            ))
          ) : (
            <div className="text-[11px] text-slate-500">—</div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {[
          ['must_include', '必须包含', draft.must_include, 'must_include'],
          ['usage_rights', 'Usage rights', draft.usage_rights, 'usage_rights'],
          ['buyout_rights', '买断授权', draft.buyout_rights, 'buyout_rights'],
          ['breach_terms', '违约条款', draft.breach_terms, 'breach_terms'],
          ['payment_terms', '付款条款', draft.payment_terms, 'payment_terms'],
          ['cancellation_terms', '解约条款', draft.cancellation_terms, 'cancellation_terms'],
          ['revision_terms', '返工/修改条款', draft.revision_terms, 'revision_terms'],
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
  onDeleteContract,
}: {
  rows: VkpiProjectRow[];
  contractLines: ContractLine[];
  contracts: VkpiProjectContractsResponse | null;
  loading: boolean;
  error: string;
  busyKey: string;
  onUploadContract: (file: File, assignmentId?: string, kolPoolId?: string, relatedContractId?: number) => Promise<void>;
  onSaveContract: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onConfirmContract: (contractId: number, payload: Record<string, unknown>) => Promise<void>;
  onOpenContract: (contractId: number) => Promise<void>;
  onRetryExtract: (contractId: number) => Promise<void>;
  onDeleteContract?: (contractId: number, fileName?: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const uploadZoneRef = useRef<HTMLDivElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState('');
  const [linkedRowId, setLinkedRowId] = useState('');
  // 签署版上传:点 GEN-/草稿行「上传签署版」后记录被关联草稿,复用本上传表单携带 related_contract_id。
  const [relatedContract, setRelatedContract] = useState<VkpiProjectContract | null>(null);
  const uploadBusy = busyKey === 'upload';
  const items = contracts?.items || [];
  const expectedRows = contractLines.filter((line) => line.statusLabel !== '未触发');
  const selectedRow = rows.find((row) => row.id === linkedRowId) || rows.find((row) => stageIndex(row.stage) >= stageIndex('agreed')) || rows[0];
  const chooseFile = (nextFile?: File | null) => {
    if (!nextFile) return;
    // 非法类型不再静默吞掉:给出明确提示,让用户知道为什么没选上。
    if (!/\.(pdf|doc|docx)$/i.test(nextFile.name)) {
      setFileError(`「${nextFile.name}」类型不支持,仅接受 PDF / DOC / DOCX 合同文件。`);
      return;
    }
    setFileError('');
    setFile(nextFile);
  };
  const startSignedVersionUpload = (contract: VkpiProjectContract) => {
    setRelatedContract(contract);
    uploadZoneRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    fileRef.current?.click();
  };
  const submitUpload = async () => {
    if (!file) return;
    await onUploadContract(file, selectedRow?.assignmentId, selectedRow?.kolPoolId, relatedContract?.id);
    setFile(null);
    setRelatedContract(null);
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
        ref={uploadZoneRef}
        className={`rounded-xl border-2 border-dashed ${relatedContract ? 'border-emerald-400/40 bg-emerald-400/[0.04]' : 'border-white/[0.08] bg-white/[0.012]'} p-4`}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => { event.preventDefault(); chooseFile(event.dataTransfer.files?.[0]); }}
      >
        <input ref={fileRef} type="file" accept=".pdf,.doc,.docx" className="hidden" onChange={(event) => { chooseFile(event.target.files?.[0]); event.target.value = ''; }} />
        {relatedContract ? (
          <div className="mb-3 flex items-center gap-2 flex-wrap text-[10.5px]">
            <span className="px-2 py-1 rounded-md border border-emerald-400/30 bg-emerald-400/10 text-emerald-200">
              签署版上传 · 将关联草稿 #{relatedContract.id}{relatedContract.file_name ? `(${relatedContract.file_name})` : ''}
            </span>
            <button className="px-2 py-1 rounded-md border border-white/[0.08] text-slate-300 hover:text-white" type="button" onClick={() => setRelatedContract(null)}>
              取消关联(改为普通归档)
            </button>
          </div>
        ) : null}
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
            {uploadBusy ? 'Claude正在提取条款' : relatedContract ? '上传签署版' : '上传归档'}
          </button>
        </div>
        {fileError ? <div className="mt-2 text-[10.5px] text-red-300">{fileError}</div> : null}
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
              onDelete={onDeleteContract}
              onUploadSignedVersion={startSignedVersionUpload}
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

const QA_CHECK_LABELS: Record<string, string> = {
  product_identity: '型号',
  brand_exposure: '品牌',
  competitor_context: '竞品',
  inscription_or_model_text: '铭文',
  title_image_consistency: '标题画面',
};

function finalV1QaPayload(entry?: VkpiAnalysisCacheEntry | null) {
  const result = asRecord(entry?.result);
  const direct = asRecord(result.final_v1_keyframe_qa);
  if (Object.keys(direct).length) return direct;
  const nested = asRecord(asRecord(result.video_analysis_final_v1_keyframe_qa).final_v1_keyframe_qa);
  if (Object.keys(nested).length) return nested;
  if ('qa_pass' in result || 'checks' in result || 'issues' in result || 'score_correction' in result) return result;
  return {};
}

function qaBoolean(value: unknown) {
  if (typeof value === 'boolean') return value;
  const text = String(value ?? '').trim().toLowerCase();
  if (['true', '1', 'yes', 'pass', 'passed'].includes(text)) return true;
  if (['false', '0', 'no', 'fail', 'failed'].includes(text)) return false;
  return null;
}

function qaStatusLabel(value: unknown) {
  const status = textFrom(value).toLowerCase();
  if (status === 'pass') return '通过';
  if (status === 'warn') return '提醒';
  if (status === 'fail') return '异常';
  if (status === 'unknown') return '未知';
  return status || '未知';
}

function qaStatusClass(value: unknown) {
  const status = textFrom(value).toLowerCase();
  if (status === 'pass') return 'bg-emerald-500/10 text-emerald-200 border-emerald-400/15';
  if (status === 'fail') return 'bg-rose-500/12 text-rose-200 border-rose-400/20';
  if (status === 'warn') return 'bg-amber-500/10 text-amber-200 border-amber-400/20';
  return 'bg-slate-500/10 text-slate-300 border-white/[0.06]';
}

function qaCheckTags(checks: unknown) {
  return Object.entries(asRecord(checks)).map(([key, value]) => {
    const record = asRecord(value);
    const status = textFrom(record.status) || 'unknown';
    const detail = firstText(record.issues, record.evidence, record.observed_products, record.observed_text, record.observed_brand_signals, record.observed_competitors);
    return {
      key,
      label: QA_CHECK_LABELS[key] || key.replace(/_/g, ' '),
      status,
      detail,
    };
  });
}

function qaIssueItems(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.map((item, index) => {
    const record = asRecord(item);
    const type = textFrom(record.type) || `issue_${index + 1}`;
    const severity = textFrom(record.severity) || 'info';
    const timestamp = textFrom(record.timestamp);
    const evidence = textFrom(record.evidence);
    const correction = textFrom(record.correction);
    const label = [timestamp, type.replace(/_/g, ' ')].filter(Boolean).join(' · ');
    return {
      key: `${label || type}-${index}`,
      label: label || type,
      severity,
      evidence,
      correction,
    };
  }).filter((item) => item.label || item.evidence || item.correction);
}

function qaScoreCorrectionText(value: unknown) {
  const correction = asRecord(value);
  if (!Object.keys(correction).length) return '';
  const apply = qaBoolean(correction.apply);
  const delta = Number(correction.marketing_value_delta);
  const corrected = Number(correction.corrected_marketing_value_score);
  const rationale = textFrom(correction.rationale);
  const parts: string[] = [];
  parts.push(apply ? '建议纠偏' : '不建议调分');
  if (Number.isFinite(delta) && delta !== 0) parts.push(`营销分 ${delta > 0 ? '+' : ''}${delta}`);
  if (Number.isFinite(corrected)) parts.push(`纠偏后 ${Math.round(corrected)}`);
  if (rationale) parts.push(rationale);
  return parts.join(' · ');
}

function buildAnalysisItemLookup(items: VkpiProjectVideoAnalysisCacheItem[]) {
  const map = new Map<string, VkpiProjectVideoAnalysisCacheItem>();
  const add = (key: string, item: VkpiProjectVideoAnalysisCacheItem) => {
    if (!key || map.has(key)) return;
    map.set(key, item);
  };
  items.forEach((item) => {
    if (item.evidence_id != null) add(`evidence:${item.evidence_id}`, item);
    if (item.content_url) add(`url:${item.content_url}`, item);
    if (item.entry?.target_id) add(`target:${item.entry.target_id}`, item);
  });
  return map;
}

function qaItemForAnalysis(item: VkpiProjectVideoAnalysisCacheItem, map: Map<string, VkpiProjectVideoAnalysisCacheItem>) {
  const keys = [
    item.evidence_id != null ? `evidence:${item.evidence_id}` : '',
    item.content_url ? `url:${item.content_url}` : '',
    item.entry?.target_id ? `target:${item.entry.target_id}` : '',
  ].filter(Boolean);
  for (const key of keys) {
    const match = map.get(key);
    if (match?.state === 'ready' && match.entry) return match;
  }
  return null;
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
  qaItem,
}: {
  row: VkpiProjectRow;
  item: VkpiProjectVideoAnalysisCacheItem;
  qaItem?: VkpiProjectVideoAnalysisCacheItem | null;
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
  const qaPayload = qaItem?.state === 'ready' && qaItem.entry ? finalV1QaPayload(qaItem.entry) : {};
  const qaHasPayload = Object.keys(qaPayload).length > 0;
  const qaResultRecord = asRecord(qaItem?.entry?.result);
  const qaPass = qaBoolean(qaPayload.qa_pass ?? qaResultRecord.qa_pass);
  const qaBadgeText = qaPass === false ? '需复核' : qaPass === true ? '通过' : '未定';
  const qaSummary = textFrom(qaPayload.summary);
  const qaConfidence = Number(qaPayload.confidence);
  const qaChecks = qaCheckTags(qaPayload.checks);
  const qaIssues = qaIssueItems(qaPayload.issues);
  const qaCorrection = qaScoreCorrectionText(qaPayload.score_correction);
  const qaAction = textFrom(qaPayload.recommended_review_action);
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

      {qaHasPayload ? (
        <div className={`rounded-md border p-2.5 ${qaPass === false ? 'border-rose-400/20 bg-rose-500/[0.045]' : 'border-emerald-400/15 bg-emerald-500/[0.035]'}`}>
          <div className="flex items-center justify-between gap-2 mb-2">
            <div className="flex items-center gap-2 min-w-0">
              <span className={`px-2 py-1 rounded text-[9.5px] font-medium ${qaPass === false ? 'bg-rose-500/15 text-rose-200' : 'bg-emerald-500/15 text-emerald-200'}`}>
                关键帧 QA {qaBadgeText}
              </span>
              {Number.isFinite(qaConfidence) ? <span className="text-[9.5px] text-slate-500">置信 {Math.round(qaConfidence * 100)}%</span> : null}
            </div>
            {qaAction ? <span className="text-[9.5px] text-slate-500 shrink-0">{qaAction.replace(/_/g, ' ')}</span> : null}
          </div>
          {qaSummary ? <div className="text-[10.5px] text-slate-200 leading-relaxed mb-2">{compactText(qaSummary, 180)}</div> : null}
          {qaChecks.length ? (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {qaChecks.map((check) => (
                <span key={check.key} className={`px-2 py-1 rounded border text-[9.5px] ${qaStatusClass(check.status)}`} title={check.detail || undefined}>
                  {check.label}: {qaStatusLabel(check.status)}
                </span>
              ))}
            </div>
          ) : null}
          {qaIssues.length ? (
            <div className="space-y-1.5 mb-2">
              {qaIssues.slice(0, 3).map((issue) => (
                <div key={issue.key} className="rounded bg-black/20 border border-white/[0.05] px-2 py-1.5 text-[10px] text-slate-300">
                  <span className="text-amber-200">{issue.label}</span>
                  {issue.evidence ? <span> · {compactText(issue.evidence, 110)}</span> : null}
                  {issue.correction ? <span className="text-cyan-200"> · {compactText(issue.correction, 90)}</span> : null}
                </div>
              ))}
            </div>
          ) : null}
          {qaCorrection ? <div className="text-[10px] text-slate-400">纠偏建议: {compactText(qaCorrection, 190)}</div> : null}
        </div>
      ) : null}

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
  videoQaCache,
  videoAnalysisLoading,
  videoAnalysisError,
  videoQaError,
  retrospective,
  retrospectiveLastJob,
  retrospectiveGenerating,
  onGenerateRetrospective,
  onCopy,
  onPendingAction,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  health: ReturnType<typeof healthForRows>;
  videoAnalysisCache?: VkpiProjectVideoAnalysisCacheResponse | null;
  videoQaCache?: VkpiProjectVideoAnalysisCacheResponse | null;
  videoAnalysisLoading?: boolean;
  videoAnalysisError?: string;
  videoQaError?: string;
  retrospective?: { result?: VkpiProjectRetrospectiveResult; model?: string; updated_at?: string } | null;
  retrospectiveLastJob?: { status?: string; last_error?: string | null } | null;
  retrospectiveGenerating?: boolean;
  onGenerateRetrospective?: () => void | Promise<void>;
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
  const qaItemLookup = useMemo(() => buildAnalysisItemLookup(videoQaCache?.items || []), [videoQaCache?.items]);
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

  const retroResult = retrospective?.result;
  const retroProvenance = retroResult?.provenance;
  const retroFailed = !retroResult && (retrospectiveLastJob?.status === 'failed' || retrospectiveLastJob?.status === 'blocked');

  return (
    <div className="p-4 space-y-4" aria-label="项目复盘">
      <div className="rounded-lg border border-purple-500/30 bg-gradient-to-br from-purple-500/10 to-emerald-500/5 p-4">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-full bg-purple-500/20 flex items-center justify-center shrink-0">
            <BookOpen size={17} className="text-purple-300" />
          </div>
          <div className="flex-1">
            <div className="flex items-center justify-between gap-3 mb-1.5">
              <div className="flex items-center gap-2">
                <div className="text-[12px] font-semibold text-white">项目复盘总结</div>
                {retroResult ? (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-200" title="由 LLM 聚合项目下所有成品视频分析生成；分值类指标未定标，仅供参考">AI 聚合 · 未定标</span>
                ) : (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-white/[0.06] text-slate-400" title="尚未生成项目级 AI 复盘，下方为前端模板草稿(非 AI 产物)">模板 · 非 AI</span>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {onGenerateRetrospective ? (
                  <button
                    type="button"
                    className="px-2.5 py-1 rounded-md bg-emerald-500/90 hover:bg-emerald-500 text-white text-[10.5px] font-medium flex items-center gap-1 disabled:opacity-50"
                    onClick={() => void onGenerateRetrospective()}
                    disabled={Boolean(retrospectiveGenerating)}
                  >
                    <Sparkles size={10} />{retrospectiveGenerating ? '生成中…' : retroResult ? '重新生成' : '生成项目复盘'}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="px-2.5 py-1 rounded-md bg-purple-500/15 hover:bg-purple-500/25 text-purple-200 text-[10.5px] font-medium flex items-center gap-1"
                  onClick={() => (onCopy ? void onCopy(retroResult?.insight_text || retrospectiveDraft, retroResult ? '复盘聚合' : '复盘草稿') : onPendingAction('复制复盘'))}
                >
                  <BookOpen size={10} />复制
                </button>
              </div>
            </div>
            {retroResult ? (
              <div className="space-y-2">
                <div className="text-[10.5px] text-slate-200 leading-relaxed whitespace-pre-wrap">{retroResult.insight_text}</div>
                {(retroResult.highlights?.length || retroResult.risks?.length || retroResult.next_steps?.length) ? (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                    {([['亮点', retroResult.highlights, '#10b981'], ['风险', retroResult.risks, '#fb7185'], ['下一步', retroResult.next_steps, '#a855f7']] as const).map(([label, items, color]) => (
                      <div key={label} className="rounded-md border border-white/[0.05] bg-black/20 p-2">
                        <div className="text-[9px] mb-1" style={{ color }}>{label}</div>
                        <ul className="space-y-0.5">
                          {(items || []).map((it, i) => <li key={i} className="text-[10px] text-slate-300 leading-snug">· {it}</li>)}
                          {!(items || []).length ? <li className="text-[10px] text-slate-600">—</li> : null}
                        </ul>
                      </div>
                    ))}
                  </div>
                ) : null}
                <div className="text-[9px] text-slate-500">
                  来源:聚合 {retroProvenance?.video_count ?? 0} 个成品视频(按曝光 Top-{retroProvenance?.top_n ?? 15}) · 模型 {retroProvenance?.model || retrospective?.model || '-'} · {retroProvenance?.generated_at || retrospective?.updated_at || ''}
                </div>
              </div>
            ) : (
            <div className="text-[10.5px] text-slate-300 leading-relaxed">
              {retroFailed ? <span className="text-amber-400">上次生成未完成{retrospectiveLastJob?.last_error ? `(${retrospectiveLastJob.last_error})` : ''},可点「生成项目复盘」重试。<br /></span> : null}
              <span className="text-slate-500">以下为模板草稿(非 AI):</span> 项目 {projectTitle} · 健康度{' '}
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
            )}
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
            {videoQaError ? <div className="mt-1 text-[10px] text-amber-300">关键帧 QA 读取失败：{videoQaError}</div> : null}
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
                    <span className="text-[10px] text-slate-600 flex items-center gap-1 shrink-0" title="暂无视频链接">
                      <ExternalLink size={10} /> 打开
                    </span>
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
                            qaItem={qaItemForAnalysis(item, qaItemLookup)}
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
}) {
  const [rankingSort, setRankingSort] = useState<AnalyticsRankingSort>('views');
  const totalLikes = rows.reduce((sum, row) => sum + (row.likes || 0), 0);
  const totalComments = rows.reduce((sum, row) => sum + (row.comments || 0), 0);
  const publishedKols = rows.filter(analyticsHasPublishedEvidence);
  const projectTotalCost = stats.cost || 0;
  // null=归因链路不存在(显"—");0=有链路但值为零(显 $0/0)。
  const roi = stats.gmv != null && projectTotalCost > 0 ? ((stats.gmv / projectTotalCost) * 100).toFixed(1) : '—';
  const kpis: Array<[string, string, typeof Eye, string]> = [
    ['总曝光', formatLargeNum(stats.views), Eye, '#06b6d4'],
    ['总点赞', formatLargeNum(totalLikes), Heart, '#ec4899'],
    ['总评论', formatLargeNum(totalComments), MessageCircle, '#a855f7'],
    ['Shopify 点击', stats.clicks == null ? '—' : formatLargeNum(stats.clicks), ShoppingCart, '#fb923c'],
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
            曝光/点赞/评论/排名仅统计已归属当前项目的数据,Shopify / GMV / ROI 等待独立归因链路。
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
  productUnitCosts = {},
  onOpenShippingInfo,
  onOpenCostEntry,
}: {
  rows: VkpiProjectRow[];
  expenseLines: ExpenseLine[];
  costRows: Array<Record<string, unknown>>;
  productUnitCosts?: Record<string, number>;
  onOpenShippingInfo: () => void;
  onOpenCostEntry?: (row: VkpiProjectRow, type?: 'cash_fee' | 'shipping' | 'product') => void;
}) {
  const ledgerTotals = costLedgerTotals(costRows);
  const expenseById = new Map(expenseLines.map((line) => [line.id, line]));
  const rowCosts = rows.map((row) => {
    const shippingFee = costRowAmount(costRows, row, 'shipping');
    const productSent = rowProductSent(row);
    const ledgerProductCost = costRowAmount(costRows, row, 'product');
    const estimatedProductCost = productCost(productSent, productUnitCosts);
    const productCostAmount = ledgerProductCost || estimatedProductCost;
    const productCostIsEstimate = !ledgerProductCost && estimatedProductCost > 0;
    const ledgerContractFee = costRowAmount(costRows, row, 'contract');
    const expenseAmount = expenseById.get(row.id)?.amount ?? row.cost ?? 0;
    const contractFee = ledgerContractFee || Math.max(expenseAmount - shippingFee - productCostAmount, 0);
    // 残差推算的合同费要打"估"——与产品成本估算同口径,不冒充账本真值(扫描 #10)。
    const contractFeeIsEstimate = !ledgerContractFee && contractFee > 0;
    return {
      row,
      contractFee,
      contractFeeIsEstimate,
      shippingFee,
      productSent,
      productCost: productCostAmount,
      productCostIsEstimate,
      total: contractFee + shippingFee + productCostAmount,
      hasContract: contractFee > 0,
    };
  });
  const totalContract = ledgerTotals.contract || rowCosts.reduce((sum, item) => sum + item.contractFee, 0);
  const totalShipping = ledgerTotals.shipping || rowCosts.reduce((sum, item) => sum + item.shippingFee, 0);
  const totalProductCost = ledgerTotals.product || rowCosts.reduce((sum, item) => sum + item.productCost, 0);
  const totalAll = totalContract + totalShipping + totalProductCost;
  // 横幅口径:账本真值与倒推估算分开计数,不再合并冒充"已从合同提取"。
  const ledgerContractRows = rowCosts.filter((item) => item.hasContract && !item.contractFeeIsEstimate).length;
  const estimatedContractRows = rowCosts.filter((item) => item.contractFeeIsEstimate).length;
  const rowsWithProductCost = rowCosts.filter((item) => item.productCost > 0).length;
  const averageCost = totalAll / Math.max(rowCosts.filter((item) => item.contractFee + item.productCost > 0).length, 1);

  return (
    <div className="p-4 space-y-4" aria-label="项目费用">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          ['合同费用', totalContract, '#a855f7', '已合作阶段录入'],
          ['快递费', totalShipping, '#06b6d4', '已发货阶段录入'],
          // 子标签曾标"零售价"但贴的是成本数——这里只有产品成本口径,如实标注。
          ['产品成本', totalProductCost, '#10b981', '按产品成本计 · 零售价未录入'],
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
          合同费用:账本入账 {ledgerContractRows} 份 · 估算 {estimatedContractRows} 份 + {rowsWithProductCost} 个 KOL 计入产品成本 · 平均 KOL 总成本{' '}
          <span className="text-purple-300 font-semibold">{formatMoneyShort(averageCost)}</span>
        </div>
      </div>

      <div className="rounded-lg border border-white/[0.06] bg-white/[0.015] overflow-hidden">
        <div className="px-4 py-2.5 border-b border-white/[0.05] flex items-center justify-between gap-3">
          <h4 className="text-[12px] font-semibold text-white">KOL 费用明细</h4>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="rounded-lg border border-cyan-400/30 bg-cyan-500/10 px-2.5 py-1 text-[10.5px] font-semibold text-cyan-200 hover:bg-cyan-500/20 transition"
              onClick={onOpenShippingInfo}
            >
              录入快递
            </button>
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
                  {item.hasContract ? (
                    <span>
                      {formatMoneyShort(item.contractFee)}
                      {item.contractFeeIsEstimate ? (
                        <span className="text-[9px] text-amber-300/80 ml-1" title="按总支出倒推估算(成本账本暂无该 KOL 签约费行;合同确认归档后自动入账)">估</span>
                      ) : null}
                    </span>
                  ) : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-2.5 text-slate-300 tabular-nums">
                  {item.shippingFee > 0 ? formatMoneyShort(item.shippingFee) : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-2.5 tabular-nums">
                  {item.productCost > 0 ? (
                    <span>
                      <span className="text-emerald-400">{formatMoneyShort(item.productCost)}</span>
                      {item.productCostIsEstimate ? (
                        <span className="text-[9px] text-amber-300/80 ml-1" title="按 SKU 成本目录单价估算(成本账本暂无该 KOL 产品成本行)">估</span>
                      ) : null}
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
                  {item.hasContract && !item.contractFeeIsEstimate ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300">已签合同</span>
                  ) : item.hasContract ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300/80" title="费用为倒推估算,账本暂无签约费行">费用估算</span>
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
  productUnitCosts = {},
  onCopy,
  onPendingAction,
  projectId,
  onUpsertTerms,
  onAddShipment,
  onUploadEvidenceFile,
}: {
  project?: VkpiProjectRow;
  rows: VkpiProjectRow[];
  stats?: ProjectStatsSummary;
  costRows?: Array<Record<string, unknown>>;
  productUnitCosts?: Record<string, number>;
  onCopy?: (text: string, label: string) => Promise<void>;
  onPendingAction: (label: string) => void;
  projectId?: string;
  onUpsertTerms?: (payload: Record<string, unknown>) => Promise<void>;
  onAddShipment?: (payload: Record<string, unknown>) => Promise<void>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
}) {
  const [section, setSection] = useState<MaterialSection>('assets');
  const shipped = rows.filter((row) => stageIndex(row.stage) >= stageIndex('shipped'));
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
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-3 flex items-start gap-2.5">
            <FileText size={13} className="text-slate-300 mt-0.5 shrink-0" />
            <div className="text-[10.5px] text-slate-300 flex-1">
              项目 Brief 汇总当前项目的产品 / SKU / 平台 / KOL / 曝光,供对外沟通使用。物料库（产品图、参数手册、脚本等）管理尚未接入。
            </div>
            <button
              className="px-2.5 py-1 rounded-md bg-white/[0.04] hover:bg-white/[0.08] text-slate-200 text-[10.5px] font-medium flex items-center gap-1"
              type="button"
              onClick={() => (onCopy ? void onCopy(briefText, '项目 Brief') : onPendingAction('复制 Brief'))}
            >
              <FileText size={10} />复制 Brief
            </button>
          </div>

          {projectId && (onUpsertTerms || onAddShipment) ? (
            <div className="vkpi-campaign-evidence-forms rounded-lg border border-white/[0.06] bg-white/[0.015] p-4">
              <ProjectEvidenceForms
                projectId={projectId}
                onUpsertTerms={onUpsertTerms}
                onAddShipment={onAddShipment}
                onUploadEvidenceFile={onUploadEvidenceFile}
              />
              <div className="mt-2 text-[10px] text-slate-500">
                条款附件 / 物流凭证支持真实文件上传(落 evidence 存储)。消息记录与发布内容两个表单的写入接口待接入,按钮已禁用。
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
              <Boxes size={24} className="text-slate-600 mx-auto mb-2" />
              <div className="text-[11.5px] text-slate-400 mb-1">物料库尚未接入</div>
              <div className="text-[10.5px] text-slate-500">产品图 / 参数手册 / 脚本等物料管理与 LLM 起草将在后续版本上线</div>
            </div>
          )}
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
                // 签收判定与时间轴同口径:阶段 >= 已到货,或 trackingStatus 真值含签收关键字。
                const isDelivered = deliveredByStageOrStatus(row);
                const realTrackingStatus = String(row.trackingStatus || '').trim();
                const shippingCost = costRowAmount(costRows, row, 'shipping');
                const productSent = rowProductSent(row);
                const ledgerProductCost = costRowAmount(costRows, row, 'product');
                const productCostAmount = ledgerProductCost || productCost(productSent, productUnitCosts);
                // 与财务 tab 同口径:估算回退值打"估",不冒充账本真值(扫描 #9)。
                const productCostIsEstimate = !ledgerProductCost && productCostAmount > 0;
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
                    {/* 物流节点没有逐段真值:有 trackingStatus(17track 等)显示真值,否则只显两态(在途/已签收)并注明按阶段推断,不再画四段假进度。 */}
                    <div className="flex items-center gap-1.5 mb-2">
                      {realTrackingStatus ? (
                        <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-300">物流状态:{realTrackingStatus}</span>
                      ) : (
                        <>
                          {['在途', '已签收'].map((step, index) => {
                            const stepDone = isDelivered || index === 0;
                            return (
                              <div key={step} className="flex-1 flex items-center gap-1">
                                <div className={`w-2 h-2 rounded-full ${stepDone ? 'bg-emerald-400' : 'bg-white/[0.08]'}`} />
                                <div className={`flex-1 text-[9.5px] ${stepDone ? 'text-emerald-300' : 'text-slate-600'}`}>{step}</div>
                                {index < 1 ? <div className={`h-px flex-1 ${isDelivered ? 'bg-emerald-400/40' : 'bg-white/[0.05]'}`} /> : null}
                              </div>
                            );
                          })}
                          <span className="text-[9px] text-slate-600 shrink-0">按阶段推断</span>
                        </>
                      )}
                    </div>
                    <div className="flex items-center justify-between gap-3 pt-2 border-t border-white/[0.04]">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {productSent.map((item) => <span key={item} className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-300">{item}</span>)}
                      </div>
                      <div className="text-[10px] text-slate-400">
                        快递费 <span className="text-white tabular-nums">{formatMoneyShort(shippingCost)}</span>
                        {' · '}
                        产品成本 <span className="text-emerald-400 tabular-nums">{formatMoneyShort(productCostAmount)}</span>
                        {productCostIsEstimate ? (
                          <span className="text-[9px] text-amber-300/80 ml-1" title="按 SKU 成本目录单价估算(成本账本暂无该 KOL 产品成本行)">估</span>
                        ) : null}
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
