import { useMemo, useState } from 'react';
import { Activity, AlertCircle, BookOpen, Boxes, Check, DollarSign, Download, Edit3, ExternalLink, Eye, FileText, Heart, ImageIcon, MessageCircle, MousePointerClick, Package, Plus, Send, Shield, ShoppingCart, Sparkles, TrendingUp, Upload, Video, X } from 'lucide-react';
import { stageLabels } from '../../shared/vkpiConstants';
import type { VkpiProjectRow } from '../../vkpiTypes';
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

export function CampaignTimelineTab({
  rows,
}: {
  rows: VkpiProjectRow[];
}) {
  const events = useMemo(() => rows
    .map((row) => {
      const stage = timelineStage(row);
      const special = timelineSpecial(row);
      return {
        id: row.id,
        date: formatTimelineDate(timelineDateValue(row)),
        timestamp: timelineTimestamp(timelineDateValue(row)),
        kol: row.kolHandle || row.kolName || 'Unknown',
        stageLabel: stageLabels[row.stage] || stage.label,
        stageColor: special === 'lost' ? '#ef4444' : special === 'stalled' ? '#fb923c' : PROJECT_STAGE_COLOR[stage.key],
        special,
        ai: timelineEventText(row),
        reason: special ? (row.bottleneck || row.currentFocus || '需要人工确认') : '',
      };
    })
    .sort((a, b) => b.timestamp - a.timestamp || b.date.localeCompare(a.date)), [rows]);

  return (
    <div className="p-4 space-y-3" aria-label="项目时间轴">
      <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 flex items-start gap-2.5">
        <Activity size={13} className="text-purple-300 mt-0.5 shrink-0" />
        <div className="text-[10.5px] text-slate-300">
          {events.length} 条事件 · 按时间倒序 · 所有 KOL 推进记录在此聚合
        </div>
      </div>

      {events.length === 0 ? (
        <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
          <Activity size={24} className="text-slate-600 mx-auto mb-2" />
          <div className="text-[11.5px] text-slate-400">暂无事件 · 推进 KOL 后自动归档到时间轴</div>
        </div>
      ) : (
        <div className="space-y-2 relative">
          <div className="absolute left-3 top-2 bottom-2 w-px bg-white/[0.06]" />
          {events.map((event) => (
            <div key={event.id} className="flex items-start gap-3 relative pl-1">
              <div
                className="w-5 h-5 rounded-full flex items-center justify-center shrink-0 z-10"
                style={{ background: event.stageColor || '#94a3b8', boxShadow: '0 0 0 3px #0a0a0d' }}
              >
                {event.special === 'lost' ? (
                  <X size={10} className="text-white" />
                ) : event.special === 'stalled' ? (
                  <AlertCircle size={10} className="text-white" />
                ) : (
                  <Check size={10} className="text-white" />
                )}
              </div>
              <div className="flex-1 rounded-lg border border-white/[0.05] bg-white/[0.012] p-2.5">
                <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                  <span className="text-[10.5px] text-slate-400 tabular-nums font-mono">{event.date}</span>
                  <span className="text-[11px] text-white font-medium">{event.kol}</span>
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                    style={{ background: `${event.stageColor}20`, color: event.stageColor }}
                  >
                    {event.special === 'lost' ? '标记流失' : event.special === 'stalled' ? '标记停滞' : event.stageLabel}
                  </span>
                </div>
                {event.ai ? <div className="text-[10px] text-slate-400 mt-1">{event.ai}</div> : null}
                {event.reason ? <div className="text-[10px] text-slate-400 mt-1">原因: {event.reason}</div> : null}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function CampaignContractsTab({
  contractLines,
  onPendingAction,
}: {
  rows: VkpiProjectRow[];
  contractLines: ContractLine[];
  onPendingAction: (label: string) => void;
}) {
  const withContract = contractLines.filter((line) => line.statusLabel !== '未触发');

  return (
    <div className="p-4 space-y-4" aria-label="项目合同归档">
      <button
        type="button"
        onClick={() => onPendingAction('合同上传')}
        className="rounded-lg border-2 border-dashed border-white/[0.08] bg-white/[0.01] p-6 text-center hover:border-purple-500/40 transition-colors cursor-pointer w-full"
      >
        <Upload size={22} className="text-slate-500 mx-auto mb-2" />
        <div className="text-[12px] text-white font-medium mb-1">拖拽上传合同 PDF</div>
        <div className="text-[10.5px] text-slate-500 mb-2">支持 PDF / DOCX · 上传后 LLM 自动解析 KOL / 费用 / 期限 / deliverables</div>
        <span className="px-3 py-1 rounded-md bg-purple-500/90 hover:bg-purple-500 text-white text-[11px] font-medium inline-flex">选择文件</span>
      </button>

      <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 flex items-start gap-2.5">
        <Sparkles size={13} className="text-purple-300 mt-0.5 shrink-0" />
        <div className="text-[10.5px] text-slate-300">
          已归档 {withContract.length} 份合同 · LLM 自动提取关键字段并同步到「费用」 · 解析中 0 份
        </div>
      </div>

      {withContract.length === 0 ? (
        <div className="rounded-lg border border-white/[0.06] bg-white/[0.01] p-8 text-center">
          <FileText size={24} className="text-slate-600 mx-auto mb-2" />
          <div className="text-[11.5px] text-slate-400">暂无合同 · 在 4→5 已合作阶段上传 PDF</div>
        </div>
      ) : (
        <div className="space-y-2">
          {withContract.map((line) => (
            <div key={line.id} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-3 flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-purple-500/10 flex items-center justify-center shrink-0">
                <FileText size={18} className="text-purple-300" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <div className="text-[12px] font-semibold text-white truncate">{`${line.kolHandle || line.kolName || 'KOL'}_Contract.pdf`}</div>
                  <span className="text-[9.5px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300">已解析</span>
                </div>
                <div className="text-[10.5px] text-slate-400 truncate">{line.kolName || line.kolHandle} · {line.platform} · {stageLabels[line.stage]}</div>
                <div className="flex items-center gap-3 mt-1 text-[10px] text-slate-500">
                  <span>期限: <span className="text-slate-300">{line.contractType || '待确认'}</span></span>
                  <span className="truncate">{line.nextAction}</span>
                </div>
              </div>
              <div className="shrink-0 text-right">
                <div className="text-[13px] font-bold text-white tabular-nums">{formatMoneyShort(line.amount)}</div>
                <button
                  className="text-[10px] text-purple-300 hover:text-purple-200 mt-0.5 flex items-center gap-0.5"
                  type="button"
                  onClick={() => onPendingAction('查看 PDF')}
                >
                  <ExternalLink size={9} />查看 PDF
                </button>
              </div>
            </div>
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

function retrospectiveVideoTitle(row: VkpiProjectRow, projectTitle: string) {
  return retrospectiveTextField(row, ['videoTitle', 'video_title', 'contentTitle', 'content_title', 'title'])
    || `${projectTitle} · ${row.kolHandle || row.kolName || 'KOL'}`;
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

export function CampaignRetrospectiveTab({
  project,
  rows,
  stats,
  health,
  onCopy,
  onPendingAction,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  analytics: ProjectAnalyticsSummary;
  health: ReturnType<typeof healthForRows>;
  bottleneck: ReturnType<typeof bottleneckForRows>;
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

            return (
              <div key={row.id} className={`rounded-lg border p-4 ${hasShopify ? 'border-white/[0.06] bg-white/[0.015]' : 'border-white/[0.04] bg-white/[0.008] opacity-70'}`}>
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
                    <span className="text-purple-300 font-medium">AI 画面分析: </span>
                    {hasShopify
                      ? `内容表现已汇总 · 曝光 ${formatLargeNum(row.views)} · 互动 ${formatLargeNum((row.likes || 0) + (row.comments || 0) + shares)} · Shopify 点击 ${formatLargeNum(row.clicks || 0)} · 归因 GMV ${formatMoneyShort(row.gmv)}`
                      : `内容表现已汇总 · 曝光 ${formatLargeNum(row.views)} · 互动 ${formatLargeNum((row.likes || 0) + (row.comments || 0) + shares)} · 尚未接 Shopify 归因,综合得分暂不计算`}
                  </div>
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
  analytics,
}: {
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  analytics: ProjectAnalyticsSummary;
  health: ReturnType<typeof healthForRows>;
}) {
  const maxTopViews = Math.max(...analytics.topRows.map((row) => row.views || 0), 1);
  const totalLikes = rows.reduce((sum, row) => sum + (row.likes || 0), 0);
  const totalComments = rows.reduce((sum, row) => sum + (row.comments || 0), 0);
  const publishedKols = rows.filter((row) => stageIndex(row.stage) >= stageIndex('published'));
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
    .sort((a, b) => ((b.views || 0) - (a.views || 0)) || ((b.gmv || 0) - (a.gmv || 0)));

  return (
    <div className="p-4 space-y-4" aria-label="项目数据汇总">
      <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 flex items-start gap-2.5">
        <div className="shrink-0 w-7 h-7 rounded-full bg-purple-500/20 flex items-center justify-center">
          <Sparkles size={13} className="text-purple-300" />
        </div>
        <div className="flex-1">
          <div className="text-[11px] font-medium text-purple-200 mb-0.5">AI 项目数据洞察</div>
          <div className="text-[10.5px] text-slate-300 leading-relaxed">
            {`${publishedKols.length}/${rows.length} 已发布,总曝光 ${formatLargeNum(stats.views)} · Shopify 点击 ${formatLargeNum(stats.clicks)},归因 GMV ${formatMoneyShort(stats.gmv)} · ROI ${roi}%。总成本 (含产品) ${formatMoneyShort(projectTotalCost)}`}
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
          <span className="text-[10px] text-slate-500">{publishedKols.length} 个已发布</span>
        </div>
        {publishedKols.length === 0 ? (
          <div className="text-center py-6 text-[11px] text-slate-500">暂无已发布视频</div>
        ) : (
          <div className="space-y-2">
            {rankedRows.map((row, index) => {
              const avatarName = row.kolName || row.kolHandle || '-';
              return (
                <div key={row.id} className="flex items-center gap-3 px-2 py-2 rounded hover:bg-white/[0.02]">
                  <div className="text-[11px] font-bold text-slate-500 w-5">#{index + 1}</div>
                  <div
                    className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white shrink-0"
                    style={{ background: 'linear-gradient(135deg,#a855f7,#ec4899)' }}
                  >
                    {avatarName.charAt(0).toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[11.5px] text-white font-medium">{row.kolHandle || row.kolName}</div>
                    <div className="text-[10px] text-slate-500">{row.platform} · 完播 {row.views ? '—' : '—'}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-[12px] font-semibold text-white tabular-nums">{formatLargeNum(row.views)}</div>
                    <div className="text-[9.5px] text-slate-500">播放</div>
                  </div>
                  <div className="text-right">
                    <div className="text-[12px] font-semibold tabular-nums" style={{ color: row.shopifyLink ? '#10b981' : '#64748b' }}>
                      {row.shopifyLink ? formatMoneyShort(row.gmv) : '—'}
                    </div>
                    <div className="text-[9.5px] text-slate-500">GMV</div>
                  </div>
                  <div className="hidden">
                    <span style={{ width: `${Math.max(6, Math.round(((row.views || 0) / maxTopViews) * 100))}%` }} />
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
