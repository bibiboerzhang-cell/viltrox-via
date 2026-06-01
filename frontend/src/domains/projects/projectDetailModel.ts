import type { VkpiKolOption, VkpiPageKey, VkpiProjectRow, VkpiProjectStage, VkpiStaffMember } from '../../components/vkpi/vkpiTypes';
import { primaryStageFlow, stageLabels } from '../../components/vkpi/shared/vkpiConstants';
import { currencyFormatter, numberFormatter } from '../../components/vkpi/shared/vkpiFormatters';

export const detailTabs = ['参与 KOL', '数据汇总', '物料', '费用', '合同归档', '复盘', '时间轴'] as const;
export type DetailTab = typeof detailTabs[number];
export const editPlatformOptions = ['Instagram', 'YouTube', 'TikTok', 'Facebook', 'Reddit', 'X', 'Other'] as const;

export const terminalStages = new Set<VkpiProjectStage>(['closed', 'released']);
export const cancelledStages = new Set<VkpiProjectStage>(['cancelled', 'lost', 'stalled']);
export const planningStages = new Set<VkpiProjectStage>(['invited', 'discovery']);
export const wrappingStages = new Set<VkpiProjectStage>(['content_published', 'published', 'measured']);
export const activeStages = new Set<VkpiProjectStage>(['contacted', 'replied', 'in_discussion', 'agreed', 'shipped', 'received']);
const rawStageAliases: Record<string, VkpiProjectStage> = {
  arrived: 'received',
  churned: 'closed',
  content_posted: 'published',
  device_sent: 'shipped',
  discovered: 'discovery',
  reviewed: 'measured',
};

export const stageDescriptions: Record<VkpiProjectStage, string> = {
  invited: '待从名单确认合作对象',
  discovery: '候选 KOL 已进入项目池，等待联系',
  contacted: '已发起联系，等待对方回复',
  replied: '对方已回复，需要推进合作条款',
  in_discussion: '沟通中，等待确认合作细节',
  agreed: '合作已确认，准备发货或物料',
  shipped: '样品已发货，等待物流签收',
  received: '样品已到货，需要提醒发布',
  content_published: '内容已发布，等待统计',
  published: '内容已发布，等待统计',
  measured: '数据已统计，等待关闭复盘',
  closed: '项目已关闭',
  stalled: '项目停滞，需要人工处理',
  lost: '项目已关闭',
  released: '已释放',
  cancelled: '已取消',
};

export interface TrackingState {
  courier: string;
  no: string;
  status: string;
  last: string;
  delivered: boolean;
}

export interface NoticeState {
  tone: 'info' | 'success' | 'warning';
  title: string;
  body: string;
}

export interface ConfirmAction {
  title: string;
  body: string;
  confirmLabel: string;
  confirmVariant?: 'primary' | 'danger';
  onConfirm: () => Promise<void>;
}

export interface ScreenshotTarget {
  row: VkpiProjectRow;
  from: number;
  to: number;
  stage: VkpiProjectStage;
}

export interface TaskItem {
  level: string;
  className: string;
  title: string;
  subtitle: string;
  rowId?: string;
  tab?: DetailTab;
}

export interface PlatformSummary {
  platform: string;
  kolCount: number;
  views: number;
  clicks: number;
  orders: number;
  gmv: number;
  cost: number;
  roi: number | null;
}

export interface TimelineSummary {
  label: string;
  dateKey: string;
  views: number;
  posts: number;
  orders: number;
}

export interface ProjectAnalyticsSummary {
  engagement: number;
  platformRows: PlatformSummary[];
  topRows: VkpiProjectRow[];
  timeline: TimelineSummary[];
}

export interface ProjectStatsSummary {
  views: number;
  clicks: number;
  orders: number;
  gmv: number;
  cost: number;
  roi: number | null;
  published: number;
  publishRate: number;
}

export interface ExpenseLine {
  id: string;
  kolName: string;
  kolHandle: string;
  platform: VkpiProjectRow['platform'];
  stage: VkpiProjectStage;
  amount: number;
  revenue: number;
  roi: number | null;
  status: 'recorded' | 'missing';
}

export interface StageCostSummary {
  stage: VkpiProjectStage;
  count: number;
  amount: number;
}

export interface ContractLine {
  id: string;
  kolName: string;
  kolHandle: string;
  platform: VkpiProjectRow['platform'];
  stage: VkpiProjectStage;
  statusLabel: string;
  statusClass: 'is-muted' | 'is-warning' | 'is-blue' | 'is-green' | 'is-red';
  contractType: string;
  amount: number;
  evidenceCount: number;
  nextAction: string;
}

export interface ProjectDetailViewProps {
  project: VkpiProjectRow;
  projects: VkpiProjectRow[];
  participatingRows?: VkpiProjectRow[];
  costRows?: Array<Record<string, unknown>>;
  viewMode: 'manager' | 'employee';
  onBack: () => void;
  onOpenKolProfile?: (project: VkpiProjectRow) => void | Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onUpdateProject?: (projectId: string, payload: { projectName?: string; productSku?: string; productName?: string; products?: Array<{ productSku: string; productName?: string }>; platform?: string; marketplace?: string; priority?: string; shopifyLink?: string; targetPostDate?: string; dueAt?: string; note?: string }) => Promise<void>;
  onSetFollowStatus?: (project: VkpiProjectRow, followStatus: 'active' | 'paused') => void | Promise<void>;
  onMoveProjectStage?: (projectId: string, toStage: VkpiProjectStage, note?: string, extras?: { trackingNumber?: string; sampleStatus?: string; sourceRefType?: string; sourceRefId?: string }) => Promise<void>;
  onAddProjectCost?: (payload: { projectId: string; costType: string; amountUsd: number; note?: string; sourceRef?: string; metadata?: Record<string, unknown> }) => Promise<void>;
  onUpsertProjectTerms?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onAddProjectShipment?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
  kolOptions?: VkpiKolOption[];
  onLoadAvailableKols?: (project: VkpiProjectRow) => Promise<VkpiKolOption[]>;
  onAddKolsToCampaign?: (project: VkpiProjectRow, kols: VkpiKolOption[]) => Promise<void>;
  onProjectUpdated?: () => void | Promise<void>;
  onDeleteProject?: (project: VkpiProjectRow, reason?: string, actionLabel?: string) => void | Promise<void>;
  onAdvanceProjectKol?: (projectId: string, kolRef: string, payload: Record<string, unknown>) => Promise<Record<string, unknown>>;
  onUpdateProjectKolShipping?: (projectId: string, kolRef: string, payload: Record<string, unknown>) => Promise<Record<string, unknown>>;
  onSubmitProjectKolActionStub?: (projectId: string, kolRef: string, actionKind: 'screenshot' | 'video' | 'contract', payload: Record<string, unknown>) => Promise<Record<string, unknown>>;
  onSelectPage?: (page: VkpiPageKey) => void;
  onToggleView?: (targetPage?: VkpiPageKey) => void;
}

export function statusForProject(project: VkpiProjectRow) {
  if (project.followStatus === 'paused') return '已暂停';
  if (cancelledStages.has(project.stage)) return '已取消';
  if (terminalStages.has(project.stage)) return '已结束';
  if (wrappingStages.has(project.stage)) return '收尾中';
  if (planningStages.has(project.stage)) return '规划中';
  if (activeStages.has(project.stage)) return '进行中';
  return '进行中';
}

export function canonicalStage(stage: VkpiProjectStage | string) {
  const raw = String(stage || '').trim().toLowerCase();
  return (rawStageAliases[raw] || raw) as VkpiProjectStage;
}

export function stageIndex(stage: VkpiProjectStage | string) {
  const canonical = canonicalStage(stage);
  if (canonical === 'content_published') return primaryStageFlow.indexOf('published');
  const index = primaryStageFlow.indexOf(canonical);
  if (terminalStages.has(canonical) || cancelledStages.has(canonical)) return primaryStageFlow.length - 1;
  return index >= 0 ? index : 0;
}

export function matchesProjectStageFilter(rowStage: VkpiProjectStage | string, filterStage: VkpiProjectStage | string) {
  const filter = canonicalStage(filterStage);
  if (filter === 'closed') return stageIndex(rowStage) === stageIndex('closed');
  return stageIndex(rowStage) === stageIndex(filter);
}

export function parseDays(label?: string) {
  const match = String(label || '').match(/(\d+)/);
  return match ? Number(match[1]) : 0;
}

export function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function formatNumber(value: number | null | undefined) {
  return value == null ? '-' : numberFormatter.format(value);
}

export function formatMoney(value: number | null | undefined) {
  return currencyFormatter.format(value || 0);
}

export function formatRatio(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '-';
  return `${value.toFixed(1)}x`;
}

export function formatPercent(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '-';
  return `${value.toFixed(1)}%`;
}

export function dateKeyForRow(row: VkpiProjectRow) {
  const raw = row.latestMessageAt || row.updatedAt || row.startedAt || row.createdAt;
  const parsed = raw ? new Date(raw) : new Date();
  if (Number.isNaN(parsed.getTime())) return new Date().toISOString().slice(0, 10);
  return parsed.toISOString().slice(0, 10);
}

export function shortDateLabel(dateKey: string) {
  const [, month, day] = dateKey.split('-');
  return month && day ? `${Number(month)}/${Number(day)}` : dateKey;
}

export function buildTimeline(rows: VkpiProjectRow[]) {
  const now = new Date();
  const buckets = Array.from({ length: 7 }).map((_, index) => {
    const date = new Date(now);
    date.setDate(now.getDate() - (6 - index));
    const dateKey = date.toISOString().slice(0, 10);
    return { label: shortDateLabel(dateKey), dateKey, views: 0, posts: 0, orders: 0 };
  });
  const byKey = new Map(buckets.map((bucket) => [bucket.dateKey, bucket]));
  rows.forEach((row) => {
    const key = dateKeyForRow(row);
    const bucket = byKey.get(key) || buckets[buckets.length - 1];
    bucket.views += row.views || 0;
    bucket.posts += stageIndex(row.stage) >= stageIndex('published') ? 1 : 0;
    bucket.orders += row.orders || 0;
  });
  return buckets;
}

export function buildAnalytics(rows: VkpiProjectRow[]): ProjectAnalyticsSummary {
  const platformMap = new Map<string, PlatformSummary>();
  rows.forEach((row) => {
    const key = row.platform || 'Other';
    const current = platformMap.get(key) || {
      platform: key,
      kolCount: 0,
      views: 0,
      clicks: 0,
      orders: 0,
      gmv: 0,
      cost: 0,
      roi: null,
    };
    current.kolCount += 1;
    current.views += row.views || 0;
    current.clicks += row.clicks || 0;
    current.orders += row.orders || 0;
    current.gmv += row.gmv || 0;
    current.cost += row.cost || 0;
    current.roi = current.cost ? current.gmv / current.cost : null;
    platformMap.set(key, current);
  });
  const platformRows = Array.from(platformMap.values()).sort((a, b) => b.views - a.views || b.gmv - a.gmv);
  const topRows = [...rows].sort((a, b) => ((b.views || 0) - (a.views || 0)) || ((b.gmv || 0) - (a.gmv || 0))).slice(0, 5);
  const totalViews = rows.reduce((sum, row) => sum + (row.views || 0), 0);
  const totalClicks = rows.reduce((sum, row) => sum + (row.clicks || 0), 0);
  return {
    engagement: totalViews ? (totalClicks / totalViews) * 100 : 0,
    platformRows,
    topRows,
    timeline: buildTimeline(rows),
  };
}

export function buildProjectStatsSummary(rows: VkpiProjectRow[]): ProjectStatsSummary {
  const views = rows.reduce((sum, row) => sum + (row.views || 0), 0);
  const clicks = rows.reduce((sum, row) => sum + (row.clicks || 0), 0);
  const orders = rows.reduce((sum, row) => sum + (row.orders || 0), 0);
  const gmv = rows.reduce((sum, row) => sum + (row.gmv || 0), 0);
  const cost = rows.reduce((sum, row) => sum + (row.cost || 0), 0);
  const published = rows.filter((row) => stageIndex(row.stage) >= stageIndex('published')).length;
  return {
    views,
    clicks,
    orders,
    gmv,
    cost,
    roi: cost ? gmv / cost : null,
    published,
    publishRate: rows.length ? Math.round((published / rows.length) * 100) : 0,
  };
}

export function buildProjectTaskItems(rows: VkpiProjectRow[], trackingById: Record<string, TrackingState>): TaskItem[] {
  const delivered = rows.filter((row) => {
    const tracking = trackingById[row.id] || defaultTracking(row);
    return tracking.delivered && stageIndex(row.stage) <= stageIndex('received');
  });
  const stuck = rows.find((row) => parseDays(row.stageDurationLabel) >= 7 && !terminalStages.has(row.stage));
  const replied = rows.find((row) => ['replied', 'in_discussion'].includes(row.stage));
  return [
    ...(delivered.length ? [{
      level: '高',
      className: 'is-red',
      title: `${delivered.length} 个样品已送达，提醒发布`,
      subtitle: '点击跳到物流 / 发布阶段',
      rowId: delivered[0].id,
    }] : []),
    ...(replied ? [{
      level: '高',
      className: 'is-red',
      title: '处理 3→4 回复到合作转化低',
      subtitle: '点击跳到相关 KOL',
      rowId: replied.id,
    }] : []),
    ...(stuck ? [{
      level: '中',
      className: 'is-yellow',
      title: '检查阶段停留超过 7 天',
      subtitle: `${stuck.kolHandle || stuck.kolName} · ${stuck.stageDurationLabel || '-'}`,
      rowId: stuck.id,
    }] : []),
    {
      level: '低',
      className: 'is-blue',
      title: '整理合同归档和复盘素材',
      subtitle: '点击切换到合同归档',
      tab: '合同归档' as DetailTab,
    },
  ].slice(0, 3);
}

export function buildExpenseLines(rows: VkpiProjectRow[]): ExpenseLine[] {
  return rows.map((row) => {
    const amount = row.cost || 0;
    return {
      id: row.id,
      kolName: row.kolName,
      kolHandle: row.kolHandle,
      platform: row.platform,
      stage: row.stage,
      amount,
      revenue: row.gmv || 0,
      roi: amount ? (row.gmv || 0) / amount : null,
      status: amount > 0 ? 'recorded' : 'missing',
    };
  });
}

export function buildStageCostSummary(lines: ExpenseLine[]): StageCostSummary[] {
  const grouped = new Map<VkpiProjectStage, StageCostSummary>();
  lines.forEach((line) => {
    const current = grouped.get(line.stage) || { stage: line.stage, count: 0, amount: 0 };
    current.count += 1;
    current.amount += line.amount;
    grouped.set(line.stage, current);
  });
  return Array.from(grouped.values()).sort((a, b) => stageIndex(a.stage) - stageIndex(b.stage));
}

export function contractStatusForRow(row: VkpiProjectRow): Pick<ContractLine, 'statusLabel' | 'statusClass' | 'nextAction'> {
  const index = stageIndex(row.stage);
  if (cancelledStages.has(row.stage)) {
    return { statusLabel: '异常 / 已取消', statusClass: 'is-red', nextAction: '核对是否需要保留取消凭证' };
  }
  if (index < stageIndex('replied')) {
    return { statusLabel: '未触发', statusClass: 'is-muted', nextAction: '等 KOL 回复后再确认条款' };
  }
  if (index < stageIndex('agreed')) {
    return { statusLabel: '待确认条款', statusClass: 'is-warning', nextAction: '确认报价、内容要求和授权范围' };
  }
  if (index < stageIndex('closed')) {
    return { statusLabel: '需归档', statusClass: 'is-blue', nextAction: '补合同 / 邮件确认 / 沟通截图' };
  }
  return { statusLabel: '待复核', statusClass: 'is-green', nextAction: '关闭前复核合同和关键凭证' };
}

export function buildContractLines(rows: VkpiProjectRow[]): ContractLine[] {
  return rows.map((row) => {
    const status = contractStatusForRow(row);
    return {
      id: row.id,
      kolName: row.kolName,
      kolHandle: row.kolHandle,
      platform: row.platform,
      stage: row.stage,
      statusLabel: status.statusLabel,
      statusClass: status.statusClass,
      contractType: row.cost > 0 ? '成本 / 样品合作待确认' : '合作条款待确认',
      amount: row.cost || 0,
      evidenceCount: row.stageEventCount ?? 0,
      nextAction: status.nextAction,
    };
  });
}

export function statusClass(status: string) {
  if (status === '进行中') return 'is-active';
  if (status === '规划中') return 'is-planning';
  if (status === '收尾中') return 'is-wrapping';
  if (status === '已结束') return 'is-ended';
  return 'is-cancelled';
}

export function uniqueRelatedProjects(project: VkpiProjectRow, projects: VkpiProjectRow[]) {
  const related = projects.filter((item) => item.campaign === project.campaign);
  const source = related.length ? related : [project];
  const seen = new Set<string>();
  return source.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

export function healthForRows(rows: VkpiProjectRow[]) {
  const stalled = rows.filter((row) => parseDays(row.stageDurationLabel) >= 10 && !terminalStages.has(row.stage)).length;
  const cancelled = rows.filter((row) => cancelledStages.has(row.stage)).length;
  const published = rows.filter((row) => stageIndex(row.stage) >= 6).length;
  const score = clamp(Math.round(72 + published * 4 - stalled * 9 - cancelled * 18), 35, 96);
  if (score >= 80) return { score, className: 'is-good', label: '健康' };
  if (score >= 60) return { score, className: 'is-mid', label: '关注' };
  return { score, className: 'is-bad', label: '风险' };
}

export function bottleneckForRows(rows: VkpiProjectRow[]) {
  const shippedOrReceived = rows.find((row) => ['shipped', 'received'].includes(row.stage) && parseDays(row.stageDurationLabel) >= 7);
  if (shippedOrReceived) return { from: 6, to: 7, text: '到货到发布停留偏长，建议先提醒发布排期。' };
  const replied = rows.find((row) => ['replied', 'in_discussion'].includes(row.stage));
  if (replied) return { from: 3, to: 4, text: '回复到合作转化低，建议优先确认合作条款。' };
  const stalled = rows.find((row) => parseDays(row.stageDurationLabel) >= 10);
  if (stalled) {
    const index = stageIndex(stalled.stage) + 1;
    return { from: index, to: Math.min(index + 1, 9), text: `${stageLabels[stalled.stage]}停留偏长，需要人工检查。` };
  }
  const row = rows[0];
  const index = row ? stageIndex(row.stage) + 1 : 1;
  return { from: index, to: Math.min(index + 1, 9), text: '当前没有明显阻塞，保持每日刷新。' };
}

export function stageCounts(rows: VkpiProjectRow[]) {
  const counts = new Map<number, number>();
  primaryStageFlow.forEach((_, index) => counts.set(index + 1, 0));
  rows.forEach((row) => {
    const index = stageIndex(row.stage) + 1;
    counts.set(index, (counts.get(index) || 0) + 1);
  });
  return counts;
}

export function defaultTracking(row: VkpiProjectRow): TrackingState {
  const delivered = stageIndex(row.stage) >= stageIndex('received');
  const trackingNumber = row.isFakeTracking ? '' : String(row.trackingNumber || '').trim();
  return {
    courier: row.trackingCarrier || '',
    no: trackingNumber,
    status: row.trackingStatus || (delivered ? '已送达 · 请提醒发布排期' : stageIndex(row.stage) >= stageIndex('shipped') ? '运输中 / 待查物流' : '待发货'),
    last: row.updatedAt ? '详情刷新时' : '-',
    delivered,
  };
}
