import { Fragment, useEffect, useMemo, useState } from 'react';
import type { VkpiKolOption, VkpiProjectRow, VkpiProjectStage, VkpiStaffMember } from '../../vkpiTypes';
import { Avatar } from '../../shared/Avatar';
import { PlatformPill } from '../../shared/PlatformPill';
import { primaryStageFlow, stageLabels } from '../../shared/vkpiConstants';
import { currencyFormatter, numberFormatter } from '../../shared/vkpiFormatters';
import { nextProjectStage, shortDateTime } from '../../shared/vkpiDataUtils';

const detailTabs = ['参与 KOL', '数据汇总', '物料', '费用', '合同归档', '复盘'] as const;
type DetailTab = typeof detailTabs[number];
const editPlatformOptions = ['Instagram', 'YouTube', 'TikTok', 'Facebook', 'Reddit', 'X', 'Other'] as const;

const terminalStages = new Set<VkpiProjectStage>(['closed', 'released']);
const cancelledStages = new Set<VkpiProjectStage>(['cancelled', 'lost', 'stalled']);
const planningStages = new Set<VkpiProjectStage>(['invited', 'discovery']);
const wrappingStages = new Set<VkpiProjectStage>(['content_published', 'published', 'measured']);
const activeStages = new Set<VkpiProjectStage>(['contacted', 'replied', 'in_discussion', 'agreed', 'shipped', 'received']);

const stageDescriptions: Record<VkpiProjectStage, string> = {
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
  lost: '合作流失',
  released: '已释放',
  cancelled: '已取消',
};

interface TrackingState {
  courier: string;
  no: string;
  status: string;
  last: string;
  delivered: boolean;
}

interface NoticeState {
  tone: 'info' | 'success' | 'warning';
  title: string;
  body: string;
}

interface ConfirmAction {
  title: string;
  body: string;
  confirmLabel: string;
  confirmVariant?: 'primary' | 'danger';
  onConfirm: () => Promise<void>;
}

interface ScreenshotTarget {
  row: VkpiProjectRow;
  from: number;
  to: number;
  stage: VkpiProjectStage;
}

interface TaskItem {
  level: string;
  className: string;
  title: string;
  subtitle: string;
  rowId?: string;
  tab?: DetailTab;
}

interface PlatformSummary {
  platform: string;
  kolCount: number;
  views: number;
  clicks: number;
  orders: number;
  gmv: number;
  cost: number;
  roi: number | null;
}

interface TimelineSummary {
  label: string;
  dateKey: string;
  views: number;
  posts: number;
  orders: number;
}

interface ProjectAnalyticsSummary {
  engagement: number;
  platformRows: PlatformSummary[];
  topRows: VkpiProjectRow[];
  timeline: TimelineSummary[];
}

interface ProjectStatsSummary {
  views: number;
  clicks: number;
  orders: number;
  gmv: number;
  cost: number;
  roi: number | null;
  published: number;
  publishRate: number;
}

interface ExpenseLine {
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

interface StageCostSummary {
  stage: VkpiProjectStage;
  count: number;
  amount: number;
}

interface ContractLine {
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

interface ProjectDetailViewProps {
  project: VkpiProjectRow;
  projects: VkpiProjectRow[];
  viewMode: 'manager' | 'employee';
  onBack: () => void;
  onOpenKolProfile?: (project: VkpiProjectRow) => void | Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onUpdateProject?: (projectId: string, payload: { projectName?: string; productSku?: string; productName?: string; products?: Array<{ productSku: string; productName?: string }>; platform?: string; marketplace?: string; priority?: string; shopifyLink?: string; targetPostDate?: string; dueAt?: string; note?: string }) => Promise<void>;
  onMoveProjectStage?: (projectId: string, toStage: VkpiProjectStage, note?: string, extras?: { trackingNumber?: string; sampleStatus?: string; sourceRefType?: string; sourceRefId?: string }) => Promise<void>;
  onUpsertProjectTerms?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onAddProjectShipment?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
  kolOptions?: VkpiKolOption[];
  onAddKolsToCampaign?: (project: VkpiProjectRow, kols: VkpiKolOption[]) => Promise<void>;
  onProjectUpdated?: () => void | Promise<void>;
  onDeleteProject?: (project: VkpiProjectRow, reason?: string, actionLabel?: string) => void | Promise<void>;
}

function statusForProject(project: VkpiProjectRow) {
  if (cancelledStages.has(project.stage)) return '已取消';
  if (terminalStages.has(project.stage)) return '已结束';
  if (wrappingStages.has(project.stage)) return '收尾中';
  if (planningStages.has(project.stage)) return '规划中';
  if (activeStages.has(project.stage)) return '进行中';
  return '进行中';
}

function stageIndex(stage: VkpiProjectStage) {
  if (stage === 'content_published') return primaryStageFlow.indexOf('published');
  const index = primaryStageFlow.indexOf(stage);
  if (terminalStages.has(stage) || cancelledStages.has(stage)) return primaryStageFlow.length - 1;
  return index >= 0 ? index : 0;
}

function parseDays(label?: string) {
  const match = String(label || '').match(/(\d+)/);
  return match ? Number(match[1]) : 0;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function formatNumber(value: number | null | undefined) {
  return value == null ? '-' : numberFormatter.format(value);
}

function formatMoney(value: number | null | undefined) {
  return currencyFormatter.format(value || 0);
}

function formatRatio(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '-';
  return `${value.toFixed(1)}x`;
}

function formatPercent(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '-';
  return `${value.toFixed(1)}%`;
}

function dateKeyForRow(row: VkpiProjectRow) {
  const raw = row.latestMessageAt || row.updatedAt || row.startedAt || row.createdAt;
  const parsed = raw ? new Date(raw) : new Date();
  if (Number.isNaN(parsed.getTime())) return new Date().toISOString().slice(0, 10);
  return parsed.toISOString().slice(0, 10);
}

function shortDateLabel(dateKey: string) {
  const [, month, day] = dateKey.split('-');
  return month && day ? `${Number(month)}/${Number(day)}` : dateKey;
}

function buildTimeline(rows: VkpiProjectRow[]) {
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

function buildAnalytics(rows: VkpiProjectRow[]): ProjectAnalyticsSummary {
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

function buildExpenseLines(rows: VkpiProjectRow[]): ExpenseLine[] {
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

function buildStageCostSummary(lines: ExpenseLine[]): StageCostSummary[] {
  const grouped = new Map<VkpiProjectStage, StageCostSummary>();
  lines.forEach((line) => {
    const current = grouped.get(line.stage) || { stage: line.stage, count: 0, amount: 0 };
    current.count += 1;
    current.amount += line.amount;
    grouped.set(line.stage, current);
  });
  return Array.from(grouped.values()).sort((a, b) => stageIndex(a.stage) - stageIndex(b.stage));
}

function contractStatusForRow(row: VkpiProjectRow): Pick<ContractLine, 'statusLabel' | 'statusClass' | 'nextAction'> {
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

function buildContractLines(rows: VkpiProjectRow[]): ContractLine[] {
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

function statusClass(status: string) {
  if (status === '进行中') return 'is-active';
  if (status === '规划中') return 'is-planning';
  if (status === '收尾中') return 'is-wrapping';
  if (status === '已结束') return 'is-ended';
  return 'is-cancelled';
}

function uniqueRelatedProjects(project: VkpiProjectRow, projects: VkpiProjectRow[]) {
  const related = projects.filter((item) => item.campaign === project.campaign);
  const source = related.length ? related : [project];
  const seen = new Set<string>();
  return source.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

function healthForRows(rows: VkpiProjectRow[]) {
  const stalled = rows.filter((row) => parseDays(row.stageDurationLabel) >= 10 && !terminalStages.has(row.stage)).length;
  const cancelled = rows.filter((row) => cancelledStages.has(row.stage)).length;
  const published = rows.filter((row) => stageIndex(row.stage) >= 6).length;
  const score = clamp(Math.round(72 + published * 4 - stalled * 9 - cancelled * 18), 35, 96);
  if (score >= 80) return { score, className: 'is-good', label: '健康' };
  if (score >= 60) return { score, className: 'is-mid', label: '关注' };
  return { score, className: 'is-bad', label: '风险' };
}

function bottleneckForRows(rows: VkpiProjectRow[]) {
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

function stageCounts(rows: VkpiProjectRow[]) {
  const counts = new Map<number, number>();
  primaryStageFlow.forEach((_, index) => counts.set(index + 1, 0));
  rows.forEach((row) => {
    const index = stageIndex(row.stage) + 1;
    counts.set(index, (counts.get(index) || 0) + 1);
  });
  return counts;
}

function defaultTracking(row: VkpiProjectRow): TrackingState {
  const delivered = stageIndex(row.stage) >= stageIndex('received');
  return {
    courier: 'SF Express',
    no: '',
    status: delivered ? '已送达 · 请提醒发布排期' : stageIndex(row.stage) >= stageIndex('shipped') ? '运输中 / 待查物流' : '待发货',
    last: delivered ? '今天 09:15' : '-',
    delivered,
  };
}

export function ProjectDetailView({
  project,
  projects,
  viewMode,
  onBack,
  onOpenKolProfile,
  onOpenStaffProfile,
  onUpdateProject,
  onMoveProjectStage,
  onUpsertProjectTerms,
  onAddProjectShipment,
  onUploadEvidenceFile,
  kolOptions = [],
  onAddKolsToCampaign,
  onProjectUpdated,
  onDeleteProject,
}: ProjectDetailViewProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>('参与 KOL');
  const [expandedRows, setExpandedRows] = useState<Set<string>>(() => new Set([project.id]));
  const [trackingById, setTrackingById] = useState<Record<string, TrackingState>>({});
  const [stageOverrides, setStageOverrides] = useState<Record<string, VkpiProjectStage>>({});
  const [evidenceOverrides, setEvidenceOverrides] = useState<Record<string, number>>({});
  const [shopifyLinks, setShopifyLinks] = useState<Record<string, string>>({});
  const [savingShopifyRowId, setSavingShopifyRowId] = useState('');
  const [savingShipmentRowId, setSavingShipmentRowId] = useState('');
  const [tableQuery, setTableQuery] = useState('');
  const [tableStage, setTableStage] = useState('全部阶段');
  const [tablePlatform, setTablePlatform] = useState('全部平台');
  const [addKolOpen, setAddKolOpen] = useState(false);
  const [addingKols, setAddingKols] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editingProject, setEditingProject] = useState(false);
  const [screenshotTarget, setScreenshotTarget] = useState<ScreenshotTarget | null>(null);
  const [movingRowId, setMovingRowId] = useState('');
  const [notice, setNotice] = useState<NoticeState | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [confirmingAction, setConfirmingAction] = useState(false);
  const [taskReminderOpen, setTaskReminderOpen] = useState(false);
  const [dismissedReminderKey, setDismissedReminderKey] = useState('');

  useEffect(() => {
    setStageOverrides({});
    setMovingRowId('');
  }, [project.id, project.stage]);

  const baseRows = useMemo(() => uniqueRelatedProjects(project, projects), [project, projects]);
  const rows = useMemo(() => baseRows.map((row) => {
    const overrideStage = stageOverrides[row.id];
    if (!overrideStage) return row;
    return {
      ...row,
      stage: overrideStage,
      latestMessageAt: new Date().toISOString(),
      currentStageStartedAt: new Date().toISOString(),
      stageDurationLabel: '刚刚',
    };
  }), [baseRows, stageOverrides]);
  const stats = useMemo<ProjectStatsSummary>(() => {
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
  }, [rows]);
  const analytics = useMemo(() => buildAnalytics(rows), [rows]);
  const expenseLines = useMemo(() => buildExpenseLines(rows), [rows]);
  const stageCosts = useMemo(() => buildStageCostSummary(expenseLines), [expenseLines]);
  const contractLines = useMemo(() => buildContractLines(rows), [rows]);
  const health = useMemo(() => healthForRows(rows), [rows]);
  const bottleneck = useMemo(() => bottleneckForRows(rows), [rows]);
  const counts = useMemo(() => stageCounts(rows), [rows]);
  const campaignStatus = statusForProject(project);
  const ownerFallback = { name: project.ownerName, avatarUrl: project.ownerAvatar };
  const platformOptions = useMemo(() => Array.from(new Set(rows.map((row) => row.platform))).filter(Boolean), [rows]);
  const filteredRows = useMemo(() => {
    const query = tableQuery.trim().toLowerCase();
    return rows.filter((row) => {
      const matchesQuery = !query || [row.kolHandle, row.kolName, row.platform, row.campaign].some((value) => String(value || '').toLowerCase().includes(query));
      const matchesStage = tableStage === '全部阶段' || row.stage === tableStage;
      const matchesPlatform = tablePlatform === '全部平台' || row.platform === tablePlatform;
      return matchesQuery && matchesStage && matchesPlatform;
    });
  }, [rows, tablePlatform, tableQuery, tableStage]);
  const taskItems = useMemo<TaskItem[]>(() => {
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
  }, [rows, trackingById]);
  const reminderTasks = useMemo(
    () => taskItems.filter((item) => item.className === 'is-red' || item.className === 'is-yellow'),
    [taskItems],
  );
  const reminderKey = useMemo(
    () => [project.id, ...reminderTasks.map((item) => `${item.level}:${item.title}:${item.rowId || item.tab || ''}`)].join('|'),
    [project.id, reminderTasks],
  );

  useEffect(() => {
    if (!reminderTasks.length) {
      setTaskReminderOpen(false);
      return;
    }
    if (dismissedReminderKey !== reminderKey) setTaskReminderOpen(true);
  }, [dismissedReminderKey, reminderKey, reminderTasks.length]);

  const toggleRow = (rowId: string) => {
    setExpandedRows((current) => {
      const next = new Set(current);
      if (next.has(rowId)) next.delete(rowId);
      else next.add(rowId);
      return next;
    });
  };

  const trackingForRow = (row: VkpiProjectRow) => trackingById[row.id] || defaultTracking(row);
  const evidenceCountForRow = (row: VkpiProjectRow) => evidenceOverrides[row.id] ?? row.stageEventCount ?? 0;
  const shopifyLinkForRow = (row: VkpiProjectRow) => shopifyLinks[row.id] ?? row.shopifyLink ?? '';

  const moveRowStage = (row: VkpiProjectRow) => {
    if (!onMoveProjectStage) {
      setNotice({ tone: 'warning', title: '无法推进', body: '当前账号没有阶段推进入口，或父层没有传入阶段 API。' });
      return;
    }
    const nextStage = nextProjectStage(row.stage);
    if (!nextStage) {
      setNotice({ tone: 'info', title: '无需推进', body: `${row.kolHandle || row.kolName} 已在最终阶段。` });
      return;
    }
    const previousStage = row.stage;
    setConfirmAction({
      title: '确认推进阶段？',
      body: `将「${row.kolHandle || row.kolName}」从「${stageLabels[previousStage]}」推进到「${stageLabels[nextStage]}」。确认后会调用真实阶段 API，并刷新当前项目详情。`,
      confirmLabel: '确认推进',
      confirmVariant: 'primary',
      onConfirm: async () => {
        setMovingRowId(row.id);
        setStageOverrides((current) => ({ ...current, [row.id]: nextStage }));
        try {
          await onMoveProjectStage(row.id, nextStage, `详情页手动推进：${stageLabels[previousStage]} → ${stageLabels[nextStage]}`);
          setNotice({
            tone: 'success',
            title: '阶段已推进',
            body: `${row.kolHandle || row.kolName} 已进入「${stageLabels[nextStage]}」。`,
          });
          await onProjectUpdated?.();
          setStageOverrides((current) => {
            const next = { ...current };
            delete next[row.id];
            return next;
          });
        } catch (error) {
          setStageOverrides((current) => {
            const next = { ...current };
            delete next[row.id];
            return next;
          });
          setNotice({
            tone: 'warning',
            title: '推进失败',
            body: error instanceof Error ? error.message : '阶段推进失败，已回退到原阶段。',
          });
        } finally {
          setMovingRowId('');
        }
      },
    });
  };

  const openScreenshotModal = (target: ScreenshotTarget) => {
    if (!onUploadEvidenceFile) {
      setNotice({ tone: 'warning', title: '无法上传截图', body: '当前没有传入证据上传接口，请先确认父层 onUploadEvidenceFile。' });
      return;
    }
    setScreenshotTarget(target);
  };

  const completeScreenshotUpload = async (file: File, note: string) => {
    if (!screenshotTarget || !onUploadEvidenceFile) return;
    await onUploadEvidenceFile(file, {
      entityType: 'project_stage',
      entityId: screenshotTarget.row.id,
      purpose: `stage_${screenshotTarget.from}_${screenshotTarget.to}_screenshot${note.trim() ? `:${note.trim()}` : ''}`,
    });
    setEvidenceOverrides((current) => ({
      ...current,
      [screenshotTarget.row.id]: evidenceCountForRow(screenshotTarget.row) + 1,
    }));
    setScreenshotTarget(null);
    setNotice({
      tone: 'success',
      title: '截图已上传',
      body: `${screenshotTarget.row.kolHandle || screenshotTarget.row.kolName} 的 ${screenshotTarget.from}→${screenshotTarget.to} 阶段截图已保存。`,
    });
    await onProjectUpdated?.();
  };

  const saveShopifyLink = async (row: VkpiProjectRow) => {
    if (!onUpsertProjectTerms) {
      setNotice({ tone: 'warning', title: '无法保存链接', body: '当前没有传入项目条款保存接口。' });
      return;
    }
    const link = shopifyLinkForRow(row).trim();
    if (!link) {
      setNotice({ tone: 'warning', title: '需要链接', body: '请输入 Shopify 商品链接或带 ref 的归因链接。' });
      return;
    }
    if (!/^https?:\/\//i.test(link)) {
      setNotice({ tone: 'warning', title: '链接格式不对', body: '链接需要以 http:// 或 https:// 开头。' });
      return;
    }
    setSavingShopifyRowId(row.id);
    try {
      await onUpsertProjectTerms(row.id, {
        shopify_url: link,
        shopify_link: link,
        note: `Shopify 归因链接：${link}`,
      });
      setNotice({ tone: 'success', title: '链接已保存', body: `${row.kolHandle || row.kolName} 的 Shopify 归因链接已保存。` });
      await onProjectUpdated?.();
    } catch (error) {
      setNotice({ tone: 'warning', title: '保存失败', body: error instanceof Error ? error.message : 'Shopify 链接保存失败。' });
    } finally {
      setSavingShopifyRowId('');
    }
  };

  const saveShipment = async (row: VkpiProjectRow) => {
    if (!onAddProjectShipment) {
      setNotice({ tone: 'warning', title: '无法保存物流', body: '当前没有传入物流保存接口。' });
      return;
    }
    const tracking = trackingForRow(row);
    const carrier = tracking.courier.trim();
    const trackingNumber = tracking.no.trim();
    if (!trackingNumber) {
      setNotice({ tone: 'warning', title: '需要快递单号', body: '先输入 tracking no.，再保存物流。' });
      return;
    }
    setSavingShipmentRowId(row.id);
    try {
      await onAddProjectShipment(row.id, {
        carrier: carrier || 'SF Express',
        tracking_number: trackingNumber,
        shipping_status: 'shipped',
        note: '项目详情页保存物流单号，等待后续物流追踪刷新。',
      });
      setTrackingById((current) => ({
        ...current,
        [row.id]: {
          ...tracking,
          courier: carrier || 'SF Express',
          no: trackingNumber,
          status: '已保存，等待物流追踪',
          last: '刚刚',
          delivered: false,
        },
      }));
      setNotice({
        tone: 'success',
        title: '物流已保存',
        body: `${row.kolHandle || row.kolName} 的 ${carrier || 'SF Express'} · ${trackingNumber} 已写入项目物流。`,
      });
      await onProjectUpdated?.();
    } catch (error) {
      setNotice({ tone: 'warning', title: '物流保存失败', body: error instanceof Error ? error.message : '物流保存失败。' });
    } finally {
      setSavingShipmentRowId('');
    }
  };

  const updateTracking = (row: VkpiProjectRow, key: 'courier' | 'no', value: string) => {
    setTrackingById((current) => ({
      ...current,
      [row.id]: {
        ...(current[row.id] || defaultTracking(row)),
        [key]: value,
      },
    }));
  };

  const jumpToTask = (rowId?: string, tab?: DetailTab) => {
    if (tab) setActiveTab(tab);
    if (rowId) {
      setActiveTab('参与 KOL');
      setExpandedRows((current) => new Set(current).add(rowId));
    }
    window.setTimeout(() => {
      document.getElementById(rowId ? `vkpi-project-row-${rowId}` : 'vkpi-project-participation')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 0);
    setNotice({
      tone: 'info',
      title: '已跳到相关筛选',
      body: rowId ? '已展开对应 KOL 的阶段推进信息。' : `已切换到「${tab || '参与 KOL'}」。`,
    });
  };

  const dismissTaskReminder = () => {
    setDismissedReminderKey(reminderKey);
    setTaskReminderOpen(false);
  };

  const jumpFromTaskReminder = (item: TaskItem) => {
    dismissTaskReminder();
    jumpToTask(item.rowId, item.tab);
  };

  const copyMaterialText = async (text: string, label: string) => {
    const content = text.trim();
    if (!content) {
      setNotice({ tone: 'warning', title: '没有可复制内容', body: `${label} 还没有可用内容。` });
      return;
    }
    try {
      await navigator.clipboard.writeText(content);
      setNotice({ tone: 'success', title: '已复制', body: `${label} 已复制到剪贴板。` });
    } catch (error) {
      setNotice({ tone: 'warning', title: '复制失败', body: error instanceof Error ? error.message : '浏览器没有允许剪贴板写入。' });
    }
  };

  const addSelectedKols = async (selectedKols: VkpiKolOption[]) => {
    if (!onAddKolsToCampaign) {
      setNotice({ tone: 'warning', title: '无法添加 KOL', body: '当前没有传入项目创建接口，不能追加 KOL。' });
      return;
    }
    setAddingKols(true);
    try {
      await onAddKolsToCampaign(project, selectedKols);
      setAddKolOpen(false);
      setNotice({
        tone: 'success',
        title: 'KOL 已添加',
        body: `已向「${project.campaign || '当前推广'}」追加 ${selectedKols.length} 个 KOL。`,
      });
      await onProjectUpdated?.();
    } catch (error) {
      setNotice({ tone: 'warning', title: '添加失败', body: error instanceof Error ? error.message : 'KOL 添加失败。' });
    } finally {
      setAddingKols(false);
    }
  };

  const updateProjectProfile = async (payload: { projectName?: string; productSku?: string; productName?: string; platform?: string; marketplace?: string; priority?: string; shopifyLink?: string; targetPostDate?: string; dueAt?: string; note?: string }) => {
    if (!onUpdateProject) {
      setNotice({ tone: 'warning', title: '无法编辑', body: '当前没有传入项目编辑接口。' });
      return;
    }
    setEditingProject(true);
    try {
      await onUpdateProject(project.id, payload);
      setEditOpen(false);
      setNotice({ tone: 'success', title: '项目已更新', body: '推广基础信息已保存，并重新读取真实项目详情。' });
      await onProjectUpdated?.();
    } catch (error) {
      setNotice({ tone: 'warning', title: '编辑失败', body: error instanceof Error ? error.message : '项目编辑失败。' });
    } finally {
      setEditingProject(false);
    }
  };

  const runConfirmAction = async () => {
    if (!confirmAction) return;
    setConfirmingAction(true);
    try {
      await confirmAction.onConfirm();
      setConfirmAction(null);
    } finally {
      setConfirmingAction(false);
    }
  };

  const cancelProject = () => {
    if (!onDeleteProject) {
      setNotice({ tone: 'warning', title: '无法取消', body: '当前没有传入取消 / 删除项目接口。' });
      return;
    }
    setConfirmAction({
      title: '确认取消推广？',
      body: `取消后「${project.campaign || '当前推广'}」会退出正常推进列表，但保留现有阶段、物流、费用和证据记录。`,
      confirmLabel: '确认取消',
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await onDeleteProject(project, `取消推广：${project.campaign}`, '取消推广');
          onBack();
        } catch (error) {
          setNotice({ tone: 'warning', title: '取消失败', body: error instanceof Error ? error.message : '取消推广失败。' });
        }
      },
    });
  };

  const deleteProject = () => {
    if (!onDeleteProject) return;
    setConfirmAction({
      title: '确认删除项目？',
      body: `删除会把「${project.campaign || '当前推广'}」从项目看板移除。请确认这不是误触。`,
      confirmLabel: '确认删除',
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await onDeleteProject(project, `删除项目：${project.campaign}`, '删除项目');
          onBack();
        } catch (error) {
          setNotice({ tone: 'warning', title: '删除失败', body: error instanceof Error ? error.message : '项目删除失败。' });
        }
      },
    });
  };

  return (
    <section className="vkpi-campaign-detail" aria-label="项目详情">
      <button className="vkpi-campaign-back" type="button" onClick={onBack}>← 返回项目列表</button>

      <div className="vkpi-campaign-detail-hero">
        <div>
          <div className="vkpi-campaign-detail-top">
            <div>
              <span className="vkpi-campaign-eyebrow">VILTROX MARKETING · PROJECT DETAIL</span>
              <h2>{project.campaign || '未命名推广'}</h2>
            </div>
            <span className={`vkpi-campaign-status ${statusClass(campaignStatus)}`}>{campaignStatus}</span>
          </div>
          <div className="vkpi-campaign-meta">
            <span>产品 {project.campaign || '-'}</span>
            <span>最近更新 {shortDateTime(project.updatedAt || project.latestMessageAt)}</span>
            <span>预算 {formatMoney(stats.cost)} · 销售 {formatMoney(stats.gmv)}</span>
            <button
              type="button"
              disabled={!project.ownerId || !onOpenStaffProfile}
              onClick={() => project.ownerId && onOpenStaffProfile?.(project.ownerId, ownerFallback)}
            >
              负责人 {project.ownerName || '-'}
            </button>
            <span>{viewMode === 'manager' ? '上市推广' : '员工跟进'}</span>
          </div>
          <div className="vkpi-campaign-hero-actions">
            <button type="button" onClick={() => setEditOpen(true)} disabled={!onUpdateProject}>编辑</button>
            <button className="is-danger" type="button" onClick={cancelProject}>取消推广</button>
            {onDeleteProject ? <button className="is-danger" type="button" onClick={deleteProject}>删除</button> : null}
            <button className="is-primary" type="button" onClick={() => setAddKolOpen(true)}>+ 添加 KOL</button>
          </div>
        </div>
        <div className="vkpi-campaign-score-card">
          <div className="vkpi-campaign-score-row">
            <div>
              <span>健康度评分</span>
              <strong className={health.className}>{health.score}</strong>
            </div>
            <em className={health.className}>{health.label}</em>
          </div>
          <div className="vkpi-campaign-score-tooltip">
            <div>• 阶段停留越久，健康度越低</div>
            <div>• 已发布和已统计会提高评分</div>
            <div>• 取消 / 停滞项目会降低评分</div>
          </div>
          <div className="vkpi-campaign-bottleneck"><b>{bottleneck.from}→{bottleneck.to}</b> {bottleneck.text}</div>
        </div>
      </div>

      <div className="vkpi-campaign-kpis" aria-label="项目 KPI">
        <div><span>参与 KOL</span><strong>{rows.length}</strong><em>当前项目行</em></div>
        <div><span>总曝光</span><strong>{formatNumber(stats.views)}</strong><em>自动汇总</em></div>
        <div><span>已发布内容</span><strong>{stats.published}</strong><em>发布率 {stats.publishRate}%</em></div>
        <div><span>短链点击</span><strong>{formatNumber(stats.clicks)}</strong><em>{formatNumber(stats.orders)} 单</em></div>
        <div><span>归因销售</span><strong>{formatMoney(stats.gmv)}</strong><em>现有 GMV</em></div>
        <div><span>ROI</span><strong className="is-green">{formatRatio(stats.roi)}</strong><em>成本 {formatMoney(stats.cost)}</em></div>
      </div>

      <div className="vkpi-campaign-panel">
        <div className="vkpi-campaign-panel-head">
          <h3>KOL 进度漏斗</h3>
          <span className="vkpi-campaign-pulse">每日刷新</span>
        </div>
        <div className="vkpi-campaign-funnel">
          {primaryStageFlow.map((stage, index) => {
            const stageNumber = index + 1;
            const count = counts.get(stageNumber) || 0;
            const nextCount = counts.get(stageNumber + 1) || 0;
            const rate = count && stageNumber < 9 ? `${Math.round(Math.min(nextCount / count, 1) * 100)}%` : '—';
            const average = rows
              .filter((row) => stageIndex(row.stage) === index)
              .map((row) => parseDays(row.stageDurationLabel))
              .filter(Boolean);
            const avgDays = average.length ? `${Math.round(average.reduce((sum, day) => sum + day, 0) / average.length)} 天` : '-';
            return (
              <div className={`vkpi-campaign-stage ${stageNumber === bottleneck.from ? 'is-now' : ''}`} key={stage}>
                <span>{stageNumber}. {stageLabels[stage]}</span>
                <strong>{count}</strong>
                <em>平均 {avgDays}</em>
                <small>→ {rate}</small>
              </div>
            );
          })}
        </div>
        <div className="vkpi-campaign-alert">当前瓶颈：<b>{bottleneck.from}→{bottleneck.to}</b> {bottleneck.text}</div>
      </div>

      <div className="vkpi-campaign-tabs" aria-label="项目详情 tabs">
        {detailTabs.map((tab) => (
          <button key={tab} className={activeTab === tab ? 'is-active' : ''} type="button" onClick={() => setActiveTab(tab)}>
            {tab}
          </button>
        ))}
      </div>

      {activeTab === '参与 KOL' ? (
        <div className="vkpi-campaign-table-card" id="vkpi-project-participation">
          <div className="vkpi-campaign-table-toolbar">
            <input value={tableQuery} onChange={(event) => setTableQuery(event.target.value)} placeholder="搜索 KOL handle / 平台" />
            <select value={tableStage} onChange={(event) => setTableStage(event.target.value)}>
              <option>全部阶段</option>
              {primaryStageFlow.map((stage, index) => <option key={stage} value={stage}>{index + 1}. {stageLabels[stage]}</option>)}
            </select>
            <select value={tablePlatform} onChange={(event) => setTablePlatform(event.target.value)}>
              <option>全部平台</option>
              {platformOptions.map((platform) => <option key={platform}>{platform}</option>)}
            </select>
            <button type="button" onClick={() => setAddKolOpen(true)}>+ 添加 KOL</button>
          </div>
          <div className="vkpi-campaign-table-scroll">
            <table className="vkpi-campaign-table">
              <thead>
                <tr>
                  <th />
                  <th>KOL</th>
                  <th>平台</th>
                  <th>加入</th>
                  <th>当前阶段</th>
                  <th>停留</th>
                  <th>已发布</th>
                  <th>曝光</th>
                  <th>归因$</th>
                  <th>证据</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row) => {
                  const rowOpen = expandedRows.has(row.id);
                  const rowStageNumber = stageIndex(row.stage) + 1;
                  return (
                    <Fragment key={row.id}>
                      <tr className="vkpi-campaign-kol-row" id={`vkpi-project-row-${row.id}`} onClick={() => toggleRow(row.id)}>
                        <td><span className={`vkpi-campaign-tri ${rowOpen ? 'is-open' : ''}`}>▶</span></td>
                        <td>
                          <button
                            className="vkpi-campaign-kol-cell"
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              if (onOpenKolProfile) void onOpenKolProfile(row);
                            }}
                          >
                            <Avatar name={row.kolName} src={row.kolAvatar} size="sm" />
                            <span><b>{row.kolHandle || row.kolName}</b><small>{row.kolName || '-'}</small></span>
                          </button>
                        </td>
                        <td><PlatformPill platform={row.platform} /></td>
                        <td>{shortDateTime(row.startedAt || row.createdAt || row.latestMessageAt)}</td>
                        <td><span className="vkpi-campaign-stage-pill">{rowStageNumber}. {stageLabels[row.stage]}</span></td>
                        <td>{row.stageDurationLabel || '-'}</td>
                        <td>{stageIndex(row.stage) >= stageIndex('published') ? '1 / 1' : '0 / 1'}</td>
                        <td><b>{formatNumber(row.views)}</b></td>
                        <td><b>{formatMoney(row.gmv)}</b></td>
                        <td>
                          <span className="vkpi-campaign-evidence-chip">截图 {evidenceCountForRow(row)}</span>
                          {trackingForRow(row).delivered ? <span className="vkpi-campaign-evidence-chip is-warn">到货</span> : null}
                        </td>
                        <td>
                          <button
                            className="vkpi-campaign-small-button is-primary"
                            type="button"
                            disabled={movingRowId === row.id || !nextProjectStage(row.stage)}
                            onClick={(event) => {
                              event.stopPropagation();
                              void moveRowStage(row);
                            }}
                          >
                            {movingRowId === row.id ? '推进中' : nextProjectStage(row.stage) ? '推进' : '已完成'}
                          </button>
                        </td>
                      </tr>
                      {rowOpen ? (
                        <tr key={`${row.id}-detail`}>
                          <td colSpan={11} className="vkpi-campaign-expand-cell">
                            <div className="vkpi-campaign-expand">
                              <div className="vkpi-campaign-kol-ops">
                                <label>Shopify 归因链接
                                  <input
                                    value={shopifyLinkForRow(row)}
                                    onChange={(event) => setShopifyLinks((current) => ({ ...current, [row.id]: event.target.value }))}
                                    placeholder="https://your-store.myshopify.com/... 或带 ref 的商品链接"
                                  />
                                </label>
                                <button type="button" disabled={savingShopifyRowId === row.id} onClick={() => void saveShopifyLink(row)}>
                                  {savingShopifyRowId === row.id ? '保存中' : '保存链接'}
                                </button>
                                <span>快递单号在「已发货」物流卡片里输入，查到已送达会自动提醒。</span>
                              </div>

                              <div className="vkpi-campaign-data-strip">
                                <div><span>视频时长</span><b>-</b><em>暂无数据</em></div>
                                <div><span>完播率</span><b>-</b><em>暂无数据</em></div>
                                <div><span>点赞</span><b>-</b><em>暂无数据</em></div>
                                <div><span>评论</span><b>-</b><em>暂无数据</em></div>
                                <div><span>分享</span><b>-</b><em>暂无数据</em></div>
                                <div><span>短链点击</span><b>{formatNumber(row.clicks)}</b><em>现有数据</em></div>
                              </div>

                              <div className="vkpi-campaign-timeline">
                                {primaryStageFlow.slice(1).map((stage, index) => {
                                  const toNumber = index + 2;
                                  const done = rowStageNumber >= toNumber;
                                  return (
                                    <div className={`vkpi-campaign-timeline-row ${done ? '' : 'is-todo'}`} key={stage}>
                                      <div><strong>{done ? shortDateTime(row.latestMessageAt) : '-'}</strong><small>停留 {done ? row.stageDurationLabel || '-' : '-'}</small></div>
                                      <div>
                                        <strong>{toNumber - 1} → {toNumber} {stageLabels[stage]}</strong>
                                        <small>{done ? stageDescriptions[stage] : '等待推进'}</small>
                                        {toNumber === 5 ? (
                                          <TrackingWidget
                                            row={row}
                                            tracking={trackingForRow(row)}
                                            saving={savingShipmentRowId === row.id}
                                            onChange={updateTracking}
                                            onSave={saveShipment}
                                          />
                                        ) : null}
                                        {toNumber === 7 ? (
                                          <div className="vkpi-campaign-video">
                                            <div className="vkpi-campaign-thumb">▶</div>
                                            <div>
                                              <b>{row.campaign || '待同步发布内容'}</b>
                                              <span>{row.kolHandle || row.kolName} · {row.platform} · -</span>
                                              <small>内容链接会从项目详情 / 内容数据同步。</small>
                                            </div>
                                            <div><strong>{formatNumber(row.views)}</strong><span>观看</span></div>
                                          </div>
                                        ) : null}
                                      </div>
                                      <button
                                        type="button"
                                        onClick={() => openScreenshotModal({
                                          row,
                                          from: toNumber - 1,
                                          to: toNumber,
                                          stage,
                                        })}
                                      >
                                        + 截图
                                      </button>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
            {!filteredRows.length ? (
              <div className="vkpi-campaign-empty-row">没有匹配的 KOL。调整搜索、阶段或平台筛选后再看。</div>
            ) : null}
          </div>
        </div>
      ) : activeTab === '数据汇总' ? (
        <CampaignAnalyticsTab
          rows={rows}
          stats={stats}
          analytics={analytics}
          health={health}
        />
      ) : activeTab === '物料' ? (
        <CampaignMaterialsTab
          project={project}
          rows={rows}
          stats={stats}
          onCopy={copyMaterialText}
        />
      ) : activeTab === '费用' ? (
        <CampaignFinanceTab
          rows={rows}
          stats={stats}
          expenseLines={expenseLines}
          stageCosts={stageCosts}
        />
      ) : activeTab === '合同归档' ? (
        <CampaignContractsTab
          rows={rows}
          contractLines={contractLines}
        />
      ) : activeTab === '复盘' ? (
        <CampaignRetrospectiveTab
          project={project}
          rows={rows}
          stats={stats}
          analytics={analytics}
          health={health}
          bottleneck={bottleneck}
          onCopy={copyMaterialText}
        />
      ) : (
        <div className="vkpi-campaign-placeholder">
          <h3>{activeTab}</h3>
          <p>建设中：这里会显示 {activeTab} 的明细、筛选和归档结果。</p>
        </div>
      )}

      <div className="vkpi-campaign-task-dock">
        <div className="vkpi-campaign-task-panel">
          <h3>今日该做什么</h3>
          {taskItems.map((item) => (
            <button
              key={`${item.title}-${item.subtitle}`}
              type="button"
              onClick={() => jumpToTask(item.rowId, item.tab)}
            >
              <span className={item.className}>{item.level}</span>
              <b>{item.title}</b>
              <small>{item.subtitle}</small>
            </button>
          ))}
        </div>
      </div>

      {screenshotTarget ? (
        <UploadScreenshotModal
          target={screenshotTarget}
          onClose={() => setScreenshotTarget(null)}
          onSubmit={completeScreenshotUpload}
        />
      ) : null}

      {addKolOpen ? (
        <AddKolModal
          project={project}
          rows={rows}
          kolOptions={kolOptions}
          busy={addingKols}
          onClose={() => setAddKolOpen(false)}
          onSubmit={addSelectedKols}
        />
      ) : null}

      {editOpen ? (
        <EditProjectModal
          project={project}
          busy={editingProject}
          onClose={() => setEditOpen(false)}
          onSubmit={updateProjectProfile}
        />
      ) : null}

      {taskReminderOpen && reminderTasks.length ? (
        <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={dismissTaskReminder}>
          <div className="vkpi-campaign-task-modal" role="dialog" aria-label="今日项目提醒" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <h3>今日项目提醒</h3>
                <p>根据当前项目行检测到需要处理的物流或阶段推进事项。</p>
              </div>
              <button type="button" onClick={dismissTaskReminder}>稍后</button>
            </header>
            <div className="vkpi-campaign-task-modal-list">
              {reminderTasks.map((item) => (
                <button
                  key={`${item.level}-${item.title}-${item.rowId || item.tab || ''}`}
                  type="button"
                  onClick={() => jumpFromTaskReminder(item)}
                >
                  <span className={item.className}>{item.level}</span>
                  <b>{item.title}</b>
                  <small>{item.subtitle}</small>
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {notice ? (
        <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={() => setNotice(null)}>
          <div className={`vkpi-campaign-notice is-${notice.tone}`} role="dialog" aria-label={notice.title} onClick={(event) => event.stopPropagation()}>
            <h3>{notice.title}</h3>
            <p>{notice.body}</p>
            <button type="button" onClick={() => setNotice(null)}>知道了</button>
          </div>
        </div>
      ) : null}

      {confirmAction ? (
        <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={confirmingAction ? undefined : () => setConfirmAction(null)}>
          <div className="vkpi-campaign-notice is-warning" role="dialog" aria-label={confirmAction.title} onClick={(event) => event.stopPropagation()}>
            <h3>{confirmAction.title}</h3>
            <p>{confirmAction.body}</p>
            <div className="vkpi-campaign-confirm-actions">
              <button type="button" onClick={() => setConfirmAction(null)} disabled={confirmingAction}>取消</button>
              <button className={`is-${confirmAction.confirmVariant || 'primary'}`} type="button" onClick={() => void runConfirmAction()} disabled={confirmingAction}>
                {confirmingAction ? '处理中' : confirmAction.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function CampaignContractsTab({
  rows,
  contractLines,
}: {
  rows: VkpiProjectRow[];
  contractLines: ContractLine[];
}) {
  const archiveNeeded = contractLines.filter((line) => line.statusLabel === '需归档').length;
  const termsPending = contractLines.filter((line) => line.statusLabel === '待确认条款').length;
  const notStarted = contractLines.filter((line) => line.statusLabel === '未触发').length;
  const reviewReady = contractLines.filter((line) => line.statusLabel === '待复核').length;
  const evidenceTotal = contractLines.reduce((sum, line) => sum + line.evidenceCount, 0);

  return (
    <div className="vkpi-campaign-contracts" aria-label="项目合同归档">
      <div className="vkpi-campaign-contracts-head">
        <div>
          <span>Contract archive center</span>
          <h3>合同归档</h3>
          <p>当前不新增合同表，先用项目阶段、成本和证据数量推导每个 KOL 的合同归档风险。</p>
        </div>
        <div>
          <strong>{archiveNeeded + reviewReady}</strong>
          <span>需要归档 / 复核</span>
        </div>
      </div>

      <div className="vkpi-campaign-contracts-totals">
        <div><span>全部 KOL</span><strong>{rows.length}</strong><em>当前项目行</em></div>
        <div><span>需归档</span><strong>{archiveNeeded}</strong><em>已合作后应补凭证</em></div>
        <div><span>待确认条款</span><strong>{termsPending}</strong><em>回复到合作前</em></div>
        <div><span>未触发</span><strong>{notStarted}</strong><em>尚未到合同节点</em></div>
        <div><span>证据截图</span><strong>{evidenceTotal}</strong><em>现有阶段证据</em></div>
      </div>

      <div className="vkpi-campaign-contracts-grid">
        <section className="vkpi-campaign-contract-card">
          <header>
            <div>
              <span>合同状态总览</span>
              <h4>按阶段推导，不伪造签约状态</h4>
            </div>
          </header>
          <div className="vkpi-campaign-contract-statuses">
            <div><b>{notStarted}</b><span>未触发</span></div>
            <div><b>{termsPending}</b><span>待确认条款</span></div>
            <div><b>{archiveNeeded}</b><span>需归档</span></div>
            <div><b>{reviewReady}</b><span>待复核</span></div>
          </div>
          <div className="vkpi-campaign-contract-alert">未接入 `campaign_contracts` 之前，这里不会提供假上传、假生成合同或假签署按钮；只显示真实项目行能推导出的待办。</div>
        </section>

        <section className="vkpi-campaign-contract-card">
          <header>
            <div>
              <span>模板库状态</span>
              <h4>下一步接口位置</h4>
            </div>
          </header>
          <div className="vkpi-campaign-contract-template">
            <div><strong>免费寄样 / 佣金模板</strong><span>未接入合同模板表</span></div>
            <div><strong>付费推广模板</strong><span>未接入合同模板表</span></div>
            <div><strong>长期合作模板</strong><span>未接入合同模板表</span></div>
          </div>
          <p>后续接入合同表后，这里再开放模板生成、已签版上传、条款 OCR 和归档导出。</p>
        </section>
      </div>

      <section className="vkpi-campaign-contract-card">
        <header>
          <div>
            <span>合同清单</span>
            <h4>KOL 级归档待办</h4>
          </div>
        </header>
        <div className="vkpi-campaign-contract-table">
          <div className="vkpi-campaign-contract-row is-head">
            <span>KOL</span>
            <span>平台</span>
            <span>阶段</span>
            <span>归档状态</span>
            <span>条款口径</span>
            <span>金额</span>
            <span>证据</span>
            <span>下一步</span>
          </div>
          {contractLines.map((line) => (
            <div className="vkpi-campaign-contract-row" key={line.id}>
              <span><b>{line.kolHandle || line.kolName}</b><small>{line.kolName || '-'}</small></span>
              <span><PlatformPill platform={line.platform} /></span>
              <span>{stageLabels[line.stage]}</span>
              <span className={line.statusClass}>{line.statusLabel}</span>
              <span>{line.contractType}</span>
              <span>{formatMoney(line.amount)}</span>
              <span>{line.evidenceCount}</span>
              <span>{line.nextAction}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function CampaignRetrospectiveTab({
  project,
  rows,
  stats,
  analytics,
  health,
  bottleneck,
  onCopy,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  analytics: ProjectAnalyticsSummary;
  health: ReturnType<typeof healthForRows>;
  bottleneck: ReturnType<typeof bottleneckForRows>;
  onCopy: (text: string, label: string) => Promise<void>;
}) {
  const bestPlatform = analytics.platformRows[0];
  const topKol = analytics.topRows[0];
  const pendingPublish = rows.filter((row) => stageIndex(row.stage) < stageIndex('published')).length;
  const missingCost = rows.filter((row) => !row.cost).length;
  const missingLinks = rows.filter((row) => !row.shopifyLink).length;
  const targetStatus = rows.length ? '目标字段未接入，当前只展示实际值' : '暂无 KOL';
  const highlightItems = [
    topKol ? `${topKol.kolHandle || topKol.kolName} 当前贡献 ${formatNumber(topKol.views)} 曝光，归因销售 ${formatMoney(topKol.gmv)}。` : '暂无 Top KOL，可先追加 KOL 或等待内容同步。',
    bestPlatform ? `${bestPlatform.platform} 是当前最高曝光平台，${bestPlatform.kolCount} 个 KOL 合计 ${formatNumber(bestPlatform.views)} 曝光。` : '暂无平台分布数据。',
    stats.roi != null ? `项目 ROI 为 ${formatRatio(stats.roi)}，成本 ${formatMoney(stats.cost)}，归因销售 ${formatMoney(stats.gmv)}。` : '成本或销售不足，ROI 暂不可判断。',
  ];
  const lessonItems = [
    `当前瓶颈在 ${bottleneck.from}→${bottleneck.to}：${bottleneck.text}`,
    pendingPublish ? `${pendingPublish} 个 KOL 还没到发布节点，收尾前需要逐个确认发布排期。` : '当前 KOL 均已到发布或后续节点。',
    missingLinks ? `${missingLinks} 个 KOL 缺 Shopify / 归因链接，后续销售归因会偏弱。` : '当前 KOL 都有归因链接。',
    missingCost ? `${missingCost} 个 KOL 缺成本记录，费用 tab 的 ROI 仍需复核。` : '当前 KOL 都有成本记录。',
  ].slice(0, 4);
  const retrospectiveText = [
    `${project.campaign || '未命名推广'} · 复盘草稿`,
    `健康度：${health.score} / ${health.label}`,
    `参与 KOL：${rows.length}`,
    `已发布：${stats.published}，发布率 ${stats.publishRate}%`,
    `总曝光：${formatNumber(stats.views)}`,
    `短链点击：${formatNumber(stats.clicks || 0)}`,
    `归因订单：${formatNumber(stats.orders || 0)}`,
    `归因销售：${formatMoney(stats.gmv)}`,
    `成本：${formatMoney(stats.cost)}，ROI：${formatRatio(stats.roi)}`,
    '',
    '亮点：',
    ...highlightItems.map((item, index) => `${index + 1}. ${item}`),
    '',
    '风险 / 教训：',
    ...lessonItems.map((item, index) => `${index + 1}. ${item}`),
  ].join('\n');

  return (
    <div className="vkpi-campaign-retro" aria-label="项目复盘">
      <div className="vkpi-campaign-retro-head">
        <div>
          <span>Campaign retrospective</span>
          <h3>复盘</h3>
          <p>基于当前真实项目数据生成复盘草稿；不冒充 AI 报告，也不写入后端复盘表。</p>
        </div>
        <button type="button" onClick={() => void onCopy(retrospectiveText, '复盘草稿')}>复制复盘草稿</button>
      </div>

      <div className="vkpi-campaign-retro-score">
        <div>
          <span>健康度</span>
          <strong className={health.className}>{health.score}</strong>
          <em>{health.label}</em>
        </div>
        <div>
          <span>当前瓶颈</span>
          <strong>{bottleneck.from}→{bottleneck.to}</strong>
          <em>{bottleneck.text}</em>
        </div>
        <div>
          <span>复盘状态</span>
          <strong>草稿</strong>
          <em>{targetStatus}</em>
        </div>
      </div>

      <div className="vkpi-campaign-retro-grid">
        <section className="vkpi-campaign-retro-card is-wide">
          <header>
            <div>
              <span>KPI vs 当前实际</span>
              <h4>目标字段未接入前只看真实实际值</h4>
            </div>
          </header>
          <div className="vkpi-campaign-retro-kpis">
            <div><span>KOL</span><strong>{rows.length}</strong><em>目标未设置</em></div>
            <div><span>已发布</span><strong>{stats.published}</strong><em>{stats.publishRate}%</em></div>
            <div><span>曝光</span><strong>{formatNumber(stats.views)}</strong><em>自动汇总</em></div>
            <div><span>销售</span><strong>{formatMoney(stats.gmv)}</strong><em>{formatNumber(stats.orders || 0)} 单</em></div>
            <div><span>ROI</span><strong>{formatRatio(stats.roi)}</strong><em>成本 {formatMoney(stats.cost)}</em></div>
          </div>
        </section>

        <section className="vkpi-campaign-retro-card">
          <header>
            <div>
              <span>亮点</span>
              <h4>可以复用的经验</h4>
            </div>
          </header>
          <div className="vkpi-campaign-retro-list">
            {highlightItems.map((item) => <p key={item}>{item}</p>)}
          </div>
        </section>
      </div>

      <div className="vkpi-campaign-retro-grid">
        <section className="vkpi-campaign-retro-card">
          <header>
            <div>
              <span>风险 / 教训</span>
              <h4>下一步需要补齐</h4>
            </div>
          </header>
          <div className="vkpi-campaign-retro-list is-warning">
            {lessonItems.map((item) => <p key={item}>{item}</p>)}
          </div>
        </section>

        <section className="vkpi-campaign-retro-card">
          <header>
            <div>
              <span>平台结论</span>
              <h4>从分布里找下一轮预算方向</h4>
            </div>
          </header>
          <div className="vkpi-campaign-retro-platforms">
            {analytics.platformRows.slice(0, 4).map((item) => (
              <div key={item.platform}>
                <span><PlatformPill platform={item.platform as VkpiProjectRow['platform']} /></span>
                <b>{formatNumber(item.views)}</b>
                <em>{formatMoney(item.gmv)} · ROI {formatRatio(item.roi)}</em>
              </div>
            ))}
            {!analytics.platformRows.length ? <p>暂无平台数据。</p> : null}
          </div>
        </section>
      </div>

      <section className="vkpi-campaign-retro-card">
        <header>
          <div>
            <span>团队备注</span>
            <h4>后端复盘表未接入</h4>
          </div>
        </header>
        <div className="vkpi-campaign-retro-note">
          <p>这里暂时不放假“添加备注”按钮。后续接入 `campaign_retrospectives` 后，再开放团队备注、AI 生成、PDF 导出和管理层分享。</p>
        </div>
      </section>
    </div>
  );
}

function CampaignAnalyticsTab({
  rows,
  stats,
  analytics,
  health,
}: {
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  analytics: ProjectAnalyticsSummary;
  health: ReturnType<typeof healthForRows>;
}) {
  const maxTimelineViews = Math.max(...analytics.timeline.map((item) => item.views), 1);
  const maxTopViews = Math.max(...analytics.topRows.map((row) => row.views || 0), 1);
  const activeRows = rows.filter((row) => !cancelledStages.has(row.stage));
  const pendingRows = rows.filter((row) => stageIndex(row.stage) < stageIndex('published'));

  return (
    <div className="vkpi-campaign-analytics" aria-label="项目数据汇总">
      <div className="vkpi-campaign-analytics-head">
        <div>
          <span>Campaign data cockpit</span>
          <h3>数据汇总</h3>
          <p>基于当前项目详情返回的 KOL 行、短链点击、曝光、成本和归因销售实时聚合。</p>
        </div>
        <div className={`vkpi-campaign-analytics-health ${health.className}`}>
          <span>健康度</span>
          <strong>{health.score}</strong>
          <em>{health.label}</em>
        </div>
      </div>

      <div className="vkpi-campaign-analytics-totals">
        <div>
          <span>总曝光</span>
          <strong>{formatNumber(stats.views)}</strong>
          <em>{rows.length ? `${formatNumber(Math.round(stats.views / rows.length))} / KOL` : '暂无 KOL'}</em>
        </div>
        <div>
          <span>总互动</span>
          <strong>{formatNumber(stats.clicks)}</strong>
          <em>互动率 {formatPercent(analytics.engagement)}</em>
        </div>
        <div>
          <span>归因订单</span>
          <strong>{formatNumber(stats.orders)}</strong>
          <em>已发布 {stats.published} / {rows.length}</em>
        </div>
        <div>
          <span>归因销售</span>
          <strong>{formatMoney(stats.gmv)}</strong>
          <em>ROI {formatRatio(stats.roi)}</em>
        </div>
      </div>

      <div className="vkpi-campaign-analytics-grid">
        <section className="vkpi-campaign-analytics-card is-wide">
          <header>
            <div>
              <span>7 天趋势</span>
              <h4>发布与曝光</h4>
            </div>
            <em>{stats.published} 条已发布内容</em>
          </header>
          <div className="vkpi-campaign-analytics-timeline">
            {analytics.timeline.map((point) => (
              <div key={point.dateKey}>
                <span style={{ height: `${Math.max(8, Math.round((point.views / maxTimelineViews) * 86))}px` }} />
                <strong>{formatNumber(point.views)}</strong>
                <em>{point.posts} 条</em>
                <small>{point.label}</small>
              </div>
            ))}
          </div>
        </section>

        <section className="vkpi-campaign-analytics-card">
          <header>
            <div>
              <span>执行状态</span>
              <h4>当前项目池</h4>
            </div>
          </header>
          <div className="vkpi-campaign-analytics-state">
            <div><b>{rows.length}</b><span>全部 KOL</span></div>
            <div><b>{activeRows.length}</b><span>有效推进</span></div>
            <div><b>{pendingRows.length}</b><span>待发布</span></div>
            <div><b>{stats.publishRate}%</b><span>发布率</span></div>
          </div>
        </section>
      </div>

      <div className="vkpi-campaign-analytics-grid">
        <section className="vkpi-campaign-analytics-card is-wide">
          <header>
            <div>
              <span>平台分布</span>
              <h4>按平台看 ROI 和曝光</h4>
            </div>
          </header>
          <div className="vkpi-campaign-platform-table">
            <div className="vkpi-campaign-platform-row is-head">
              <span>平台</span>
              <span>KOL</span>
              <span>曝光</span>
              <span>互动率</span>
              <span>归因$</span>
              <span>ROI</span>
            </div>
            {analytics.platformRows.map((item) => (
              <div className="vkpi-campaign-platform-row" key={item.platform}>
                <span><PlatformPill platform={item.platform as VkpiProjectRow['platform']} /></span>
                <span>{item.kolCount}</span>
                <span>{formatNumber(item.views)}</span>
                <span>{formatPercent(item.views ? (item.clicks / item.views) * 100 : 0)}</span>
                <span>{formatMoney(item.gmv)}</span>
                <span>{formatRatio(item.roi)}</span>
              </div>
            ))}
            {!analytics.platformRows.length ? <div className="vkpi-campaign-analytics-empty">暂无平台数据。</div> : null}
          </div>
        </section>

        <section className="vkpi-campaign-analytics-card">
          <header>
            <div>
              <span>Top KOL</span>
              <h4>贡献排行</h4>
            </div>
          </header>
          <div className="vkpi-campaign-top-kols">
            {analytics.topRows.map((row) => (
              <div key={row.id}>
                <Avatar name={row.kolName || row.kolHandle} src={row.kolAvatar} size="sm" />
                <div>
                  <strong>{row.kolHandle || row.kolName}</strong>
                  <span>{formatNumber(row.views)} 曝光 · {formatMoney(row.gmv)}</span>
                  <i style={{ width: `${Math.max(6, Math.round(((row.views || 0) / maxTopViews) * 100))}%` }} />
                </div>
              </div>
            ))}
            {!analytics.topRows.length ? <div className="vkpi-campaign-analytics-empty">暂无 KOL 数据。</div> : null}
          </div>
        </section>
      </div>
    </div>
  );
}

function CampaignFinanceTab({
  rows,
  stats,
  expenseLines,
  stageCosts,
}: {
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  expenseLines: ExpenseLine[];
  stageCosts: StageCostSummary[];
}) {
  const recordedLines = expenseLines.filter((line) => line.status === 'recorded');
  const missingLines = expenseLines.filter((line) => line.status === 'missing');
  const grossProfit = stats.gmv * 0.38;
  const netContribution = grossProfit - stats.cost;
  const costCoverage = rows.length ? Math.round((recordedLines.length / rows.length) * 100) : 0;
  const maxStageCost = Math.max(...stageCosts.map((item) => item.amount), 1);

  return (
    <div className="vkpi-campaign-finance" aria-label="项目费用">
      <div className="vkpi-campaign-finance-head">
        <div>
          <span>Campaign finance ledger</span>
          <h3>费用</h3>
          <p>当前先读取项目详情里的真实成本聚合；样品、物流、推广费拆分会在后续接入 cost ledger 明细后展开。</p>
        </div>
        <div>
          <strong>{formatPercent(costCoverage)}</strong>
          <span>成本登记覆盖率</span>
        </div>
      </div>

      <div className="vkpi-campaign-finance-totals">
        <div><span>已记录成本</span><strong>{formatMoney(stats.cost)}</strong><em>{recordedLines.length} 个 KOL 有成本记录</em></div>
        <div><span>归因销售</span><strong>{formatMoney(stats.gmv)}</strong><em>{formatNumber(stats.orders)} 单</em></div>
        <div><span>ROI</span><strong>{formatRatio(stats.roi)}</strong><em>销售 / 成本</em></div>
        <div><span>净贡献估算</span><strong className={netContribution >= 0 ? 'is-green' : 'is-red'}>{formatMoney(netContribution)}</strong><em>按 38% 毛利估算</em></div>
      </div>

      <div className="vkpi-campaign-finance-grid">
        <section className="vkpi-campaign-finance-card">
          <header>
            <div>
              <span>ROI 计算明细</span>
              <h4>用现有真实字段计算</h4>
            </div>
          </header>
          <div className="vkpi-campaign-roi-formula">
            <div><span>归因销售</span><strong>{formatMoney(stats.gmv)}</strong></div>
            <div><span>毛利估算 38%</span><strong>{formatMoney(grossProfit)}</strong></div>
            <div><span>已记录成本</span><strong>{formatMoney(stats.cost)}</strong></div>
            <div><span>净贡献</span><strong className={netContribution >= 0 ? 'is-green' : 'is-red'}>{formatMoney(netContribution)}</strong></div>
          </div>
          <p>公式：净贡献 = 归因销售 × 38% - 已记录成本；ROI = 归因销售 / 已记录成本。</p>
        </section>

        <section className="vkpi-campaign-finance-card">
          <header>
            <div>
              <span>成本完整性</span>
              <h4>缺口提示</h4>
            </div>
          </header>
          <div className="vkpi-campaign-finance-gaps">
            <div><b>{recordedLines.length}</b><span>已登记</span></div>
            <div><b>{missingLines.length}</b><span>未登记</span></div>
            <div><b>{formatMoney(rows.length ? stats.cost / rows.length : 0)}</b><span>均摊成本</span></div>
          </div>
          {missingLines.length ? (
            <div className="vkpi-campaign-finance-alert">{missingLines.length} 个 KOL 还没有成本记录，ROI 会偏高；建议后续从「成本台」或项目明细补登记。</div>
          ) : (
            <div className="vkpi-campaign-finance-ok">当前项目行都已有成本记录，可以继续核对凭证和审批状态。</div>
          )}
        </section>
      </div>

      <div className="vkpi-campaign-finance-grid">
        <section className="vkpi-campaign-finance-card is-wide">
          <header>
            <div>
              <span>阶段成本分布</span>
              <h4>看成本卡在哪个阶段</h4>
            </div>
          </header>
          <div className="vkpi-campaign-stage-costs">
            {stageCosts.map((item) => (
              <div key={item.stage}>
                <div>
                  <strong>{stageLabels[item.stage]}</strong>
                  <span>{item.count} 个 KOL · {formatMoney(item.amount)}</span>
                </div>
                <em><i style={{ width: `${Math.max(5, Math.round((item.amount / maxStageCost) * 100))}%` }} /></em>
              </div>
            ))}
          </div>
        </section>

        <section className="vkpi-campaign-finance-card">
          <header>
            <div>
              <span>费用口径</span>
              <h4>当前版本说明</h4>
            </div>
          </header>
          <div className="vkpi-campaign-finance-notes">
            <p>已接入：项目行 `cost`、`gmv`、`orders`、`roi`。</p>
            <p>未接入：样品成本、物流、现金推广、凭证审批的独立明细。</p>
            <p>后续接入 cost ledger 后，这里会拆成费用分类和凭证表。</p>
          </div>
        </section>
      </div>

      <section className="vkpi-campaign-finance-card">
        <header>
          <div>
            <span>KOL 费用明细</span>
            <h4>按当前项目行展开</h4>
          </div>
        </header>
        <div className="vkpi-campaign-expense-table">
          <div className="vkpi-campaign-expense-row is-head">
            <span>KOL</span>
            <span>平台</span>
            <span>阶段</span>
            <span>成本</span>
            <span>销售</span>
            <span>ROI</span>
            <span>状态</span>
          </div>
          {expenseLines.map((line) => (
            <div className="vkpi-campaign-expense-row" key={line.id}>
              <span><b>{line.kolHandle || line.kolName}</b><small>{line.kolName || '-'}</small></span>
              <span><PlatformPill platform={line.platform} /></span>
              <span>{stageLabels[line.stage]}</span>
              <span>{formatMoney(line.amount)}</span>
              <span>{formatMoney(line.revenue)}</span>
              <span>{formatRatio(line.roi)}</span>
              <span className={line.status === 'recorded' ? 'is-recorded' : 'is-missing'}>{line.status === 'recorded' ? '已登记' : '未登记'}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function CampaignMaterialsTab({
  project,
  rows,
  stats,
  onCopy,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  stats: ProjectStatsSummary;
  onCopy: (text: string, label: string) => Promise<void>;
}) {
  const platforms = Array.from(new Set(rows.map((row) => row.platform).filter(Boolean)));
  const productName = project.productName || project.campaign || '未设置';
  const productSku = project.productSku || '未设置';
  const marketplace = project.marketplace || '未设置';
  const projectLinks = rows
    .map((row) => row.shopifyLink)
    .filter((value): value is string => Boolean(value && value.trim()));
  const primaryLink = project.shopifyLink || projectLinks[0] || '';
  const briefText = [
    `推广：${project.campaign || '未命名推广'}`,
    `产品：${productName}`,
    `SKU：${productSku}`,
    `平台：${platforms.join(' / ') || project.platform || '-'}`,
    `市场 / 店铺：${marketplace}`,
    primaryLink ? `商品 / 归因链接：${primaryLink}` : '商品 / 归因链接：未设置',
    `参与 KOL：${rows.length}`,
    `当前曝光：${formatNumber(stats.views)}`,
    '',
    '发布要求：请按项目沟通内容执行；如有合同或 brief PDF，以归档文件为准。',
  ].join('\n');
  const distributionText = rows.map((row, index) => [
    `${index + 1}. ${row.kolHandle || row.kolName}`,
    row.platform,
    stageLabels[row.stage],
    row.shopifyLink ? `link=${row.shopifyLink}` : 'link=未设置',
  ].join(' · ')).join('\n');
  const readyRows = rows.filter((row) => stageIndex(row.stage) >= stageIndex('agreed'));
  const linkReadyRows = rows.filter((row) => Boolean(row.shopifyLink));
  const pendingLinkRows = rows.filter((row) => !row.shopifyLink);

  return (
    <div className="vkpi-campaign-materials" aria-label="项目物料">
      <div className="vkpi-campaign-materials-head">
        <div>
          <span>Campaign material hub</span>
          <h3>物料</h3>
          <p>先把当前项目已有字段整理成可发给 KOL 的 brief、商品链接和名单清单；文件上传库后续接入 campaign materials。</p>
        </div>
        <div>
          <strong>{linkReadyRows.length}/{rows.length}</strong>
          <span>链接就绪</span>
        </div>
      </div>

      <div className="vkpi-campaign-materials-grid">
        <section className="vkpi-campaign-material-card is-brief">
          <header>
            <div>
              <span>Campaign Brief</span>
              <h4>{project.campaign || '未命名推广'}</h4>
            </div>
            <button type="button" onClick={() => void onCopy(briefText, 'Campaign Brief')}>复制 Brief</button>
          </header>
          <div className="vkpi-campaign-brief-fields">
            <div><span>产品</span><strong>{productName}</strong></div>
            <div><span>SKU</span><strong>{productSku}</strong></div>
            <div><span>市场 / 店铺</span><strong>{marketplace}</strong></div>
            <div><span>平台</span><strong>{platforms.join(' / ') || project.platform || '-'}</strong></div>
          </div>
          <div className="vkpi-campaign-brief-text">
            {briefText.split('\n').map((line, index) => <p key={`${line}-${index}`}>{line || '\u00a0'}</p>)}
          </div>
        </section>

        <section className="vkpi-campaign-material-card">
          <header>
            <div>
              <span>发放状态</span>
              <h4>物料准备度</h4>
            </div>
          </header>
          <div className="vkpi-campaign-material-readiness">
            <div><b>{rows.length}</b><span>参与 KOL</span></div>
            <div><b>{readyRows.length}</b><span>已到合作/发货后</span></div>
            <div><b>{linkReadyRows.length}</b><span>已设置链接</span></div>
            <div><b>{pendingLinkRows.length}</b><span>待补链接</span></div>
          </div>
          {pendingLinkRows.length ? (
            <div className="vkpi-campaign-material-alert">{pendingLinkRows.length} 个 KOL 还没有归因链接。可在「参与 KOL」展开行里保存 Shopify 链接。</div>
          ) : (
            <div className="vkpi-campaign-material-ok">当前所有 KOL 都已有可用链接。</div>
          )}
        </section>
      </div>

      <div className="vkpi-campaign-materials-grid">
        <section className="vkpi-campaign-material-card">
          <header>
            <div>
              <span>共享素材库</span>
              <h4>文件区状态</h4>
            </div>
          </header>
          <div className="vkpi-campaign-material-library">
            <div><strong>Brief PDF</strong><span>未接入上传表</span></div>
            <div><strong>产品图 / 视频</strong><span>未接入上传表</span></div>
            <div><strong>Logo / LUT</strong><span>未接入上传表</span></div>
          </div>
          <p>这里不放假上传按钮。下一步接 `campaign_materials` 后再开放上传、下载和使用记录。</p>
        </section>

        <section className="vkpi-campaign-material-card is-wide">
          <header>
            <div>
              <span>KOL 发放清单</span>
              <h4>按当前项目行生成</h4>
            </div>
            <button type="button" onClick={() => void onCopy(distributionText, 'KOL 发放清单')}>复制清单</button>
          </header>
          <div className="vkpi-campaign-material-table">
            <div className="vkpi-campaign-material-row is-head">
              <span>KOL</span>
              <span>平台</span>
              <span>阶段</span>
              <span>链接</span>
            </div>
            {rows.map((row) => (
              <div className="vkpi-campaign-material-row" key={row.id}>
                <span><b>{row.kolHandle || row.kolName}</b><small>{row.kolName || '-'}</small></span>
                <span><PlatformPill platform={row.platform} /></span>
                <span>{stageLabels[row.stage]}</span>
                <span className={row.shopifyLink ? 'is-ready' : 'is-missing'}>{row.shopifyLink ? '已设置' : '未设置'}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function UploadScreenshotModal({
  target,
  onClose,
  onSubmit,
}: {
  target: ScreenshotTarget;
  onClose: () => void;
  onSubmit: (file: File, note: string) => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const chooseFile = (nextFile?: File | null) => {
    setError('');
    if (!nextFile) {
      setFile(null);
      return;
    }
    const isImage = ['image/png', 'image/jpeg'].includes(nextFile.type);
    if (!isImage) {
      setError('只支持 PNG / JPG 截图。');
      setFile(null);
      return;
    }
    if (nextFile.size > 10 * 1024 * 1024) {
      setError('截图不能超过 10MB。');
      setFile(null);
      return;
    }
    setFile(nextFile);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) {
      setError('请选择截图文件。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await onSubmit(file, note);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : '截图上传失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <form className="vkpi-campaign-upload-modal" onSubmit={submit} role="dialog" aria-label="上传阶段截图" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h3>上传阶段截图</h3>
            <p>{target.row.kolHandle || target.row.kolName} · {target.from}→{target.to} {stageLabels[target.stage]}</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy}>关闭</button>
        </header>
        <label
          className="vkpi-campaign-drop-zone"
          onDragOver={(event) => {
            event.preventDefault();
          }}
          onDrop={(event) => {
            event.preventDefault();
            chooseFile(event.dataTransfer.files?.[0]);
          }}
        >
          <input type="file" accept="image/png,image/jpeg" onChange={(event) => chooseFile(event.target.files?.[0])} />
          <strong>{file ? file.name : '拖拽 PNG/JPG 到这里，或点击选择'}</strong>
          <span>最大 10MB，保存后会进入现有 evidence upload 接口。</span>
        </label>
        <label className="vkpi-campaign-upload-note">备注
          <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="例如：邮件截图 / 发货截图 / 发布截图" />
        </label>
        {error ? <div className="vkpi-campaign-upload-error">{error}</div> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="is-primary" type="submit" disabled={busy || !file}>{busy ? '上传中' : '保存截图'}</button>
        </footer>
      </form>
    </div>
  );
}

function AddKolModal({
  project,
  rows,
  kolOptions,
  busy,
  onClose,
  onSubmit,
}: {
  project: VkpiProjectRow;
  rows: VkpiProjectRow[];
  kolOptions: VkpiKolOption[];
  busy: boolean;
  onClose: () => void;
  onSubmit: (selectedKols: VkpiKolOption[]) => Promise<void>;
}) {
  const [query, setQuery] = useState('');
  const [platform, setPlatform] = useState('全部平台');
  const [claimFilter, setClaimFilter] = useState('全部');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const joinedIds = useMemo(() => new Set(rows.map((row) => row.kolId).filter(Boolean) as string[]), [rows]);
  const joinedHandles = useMemo(() => new Set(rows.map((row) => String(row.kolHandle || '').toLowerCase()).filter(Boolean)), [rows]);
  const platformOptions = useMemo(() => Array.from(new Set(kolOptions.map((kol) => kol.platform))).filter(Boolean), [kolOptions]);
  const visibleKols = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return kolOptions.filter((kol) => {
      const normalizedHandle = String(kol.handle || '').toLowerCase();
      const joined = joinedIds.has(kol.id) || joinedHandles.has(normalizedHandle);
      const matchesQuery = !normalizedQuery || [kol.name, kol.handle, kol.platform, kol.followerLabel].some((value) => String(value || '').toLowerCase().includes(normalizedQuery));
      const matchesPlatform = platform === '全部平台' || kol.platform === platform;
      const matchesClaim = claimFilter === '全部' || (claimFilter === '已关注' ? Boolean(kol.activeClaimId) : !kol.activeClaimId);
      return !joined && matchesQuery && matchesPlatform && matchesClaim;
    }).slice(0, 80);
  }, [claimFilter, joinedHandles, joinedIds, kolOptions, platform, query]);
  const selectedKols = useMemo(() => kolOptions.filter((kol) => selectedIds.has(kol.id)), [kolOptions, selectedIds]);

  const toggleKol = (kolId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(kolId)) next.delete(kolId);
      else next.add(kolId);
      return next;
    });
  };

  const submit = async () => {
    if (!selectedKols.length) return;
    await onSubmit(selectedKols);
  };

  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <div className="vkpi-campaign-add-kol-modal" role="dialog" aria-label="添加 KOL" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h3>添加 KOL</h3>
            <p>追加到「{project.campaign || '当前推广'}」，提交后写入现有项目接口。</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy}>关闭</button>
        </header>
        <div className="vkpi-campaign-add-kol-tools">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 KOL / handle" />
          <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
            <option>全部平台</option>
            {platformOptions.map((item) => <option key={item}>{item}</option>)}
          </select>
          <select value={claimFilter} onChange={(event) => setClaimFilter(event.target.value)}>
            <option>全部</option>
            <option>已关注</option>
            <option>未关注</option>
          </select>
        </div>
        <div className="vkpi-campaign-add-kol-list">
          {visibleKols.map((kol) => {
            const selected = selectedIds.has(kol.id);
            return (
              <button
                key={kol.id}
                type="button"
                className={selected ? 'is-selected' : ''}
                onClick={() => toggleKol(kol.id)}
              >
                <span className="vkpi-campaign-check">{selected ? '✓' : ''}</span>
                <Avatar name={kol.name} src={kol.avatar} size="sm" />
                <span>
                  <b>{kol.name}</b>
                  <small>{kol.handle || '-'} · {kol.platform}</small>
                </span>
                <em>{kol.followerLabel || '-'} 粉丝</em>
                <strong>{kol.activeClaimId ? '已关注' : '未关注'}</strong>
              </button>
            );
          })}
          {!visibleKols.length ? (
            <div className="vkpi-campaign-add-kol-empty">没有可追加的 KOL，可能已经在当前推广里或筛选过窄。</div>
          ) : null}
        </div>
        <footer>
          <span>已选 {selectedKols.length} 个</span>
          <div>
            <button type="button" onClick={onClose} disabled={busy}>取消</button>
            <button className="is-primary" type="button" onClick={() => void submit()} disabled={busy || !selectedKols.length}>
              {busy ? '添加中' : `添加 ${selectedKols.length} 个 KOL`}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

function dateInputValue(value?: string) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw.slice(0, 10);
  return parsed.toISOString().slice(0, 10);
}

function EditProjectModal({
  project,
  busy,
  onClose,
  onSubmit,
}: {
  project: VkpiProjectRow;
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: { projectName?: string; productSku?: string; productName?: string; platform?: string; marketplace?: string; priority?: string; shopifyLink?: string; targetPostDate?: string; dueAt?: string; note?: string }) => Promise<void>;
}) {
  const [projectName, setProjectName] = useState(project.campaign || '');
  const [productSku, setProductSku] = useState(project.productSku || '');
  const [productName, setProductName] = useState(project.productName || '');
  const [platform, setPlatform] = useState<string>(project.platform || 'Other');
  const [marketplace, setMarketplace] = useState(project.marketplace || '');
  const [priority, setPriority] = useState(project.priority || 'normal');
  const [shopifyLink, setShopifyLink] = useState(project.shopifyLink || '');
  const [targetPostDate, setTargetPostDate] = useState('');
  const [dueAt, setDueAt] = useState(dateInputValue(project.closedAt));
  const [error, setError] = useState('');

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const name = projectName.trim();
    if (!name) {
      setError('推广名称不能为空。');
      return;
    }
    if (shopifyLink.trim() && !/^https?:\/\//i.test(shopifyLink.trim())) {
      setError('Shopify 链接需要以 http:// 或 https:// 开头。');
      return;
    }
    setError('');
    await onSubmit({
      projectName: name,
      productSku: productSku.trim(),
      productName: productName.trim(),
      platform,
      marketplace: marketplace.trim(),
      priority,
      shopifyLink: shopifyLink.trim(),
      targetPostDate: targetPostDate || undefined,
      dueAt: dueAt || undefined,
      note: `编辑推广：${name}`,
    });
  };

  return (
    <div className="vkpi-campaign-notice-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <form className="vkpi-campaign-edit-modal" onSubmit={submit} role="dialog" aria-label="编辑项目" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <h3>编辑推广</h3>
            <p>只更新项目基础资料；阶段推进、费用、物流和证据仍在详情里处理。</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy}>关闭</button>
        </header>
        <div className="vkpi-campaign-edit-grid">
          <label className="is-full">推广名称
            <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="AF 35mm F1.2 LAB FE 上市推广" />
          </label>
          <label>产品 SKU
            <input value={productSku} onChange={(event) => setProductSku(event.target.value)} placeholder="VTX-35-LAB-FE" />
          </label>
          <label>产品名称
            <input value={productName} onChange={(event) => setProductName(event.target.value)} placeholder="35mm F1.2 LAB FE" />
          </label>
          <label>平台
            <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
              {editPlatformOptions.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <label>市场 / 店铺
            <input value={marketplace} onChange={(event) => setMarketplace(event.target.value)} placeholder="US / Shopify / Amazon" />
          </label>
          <label>优先级
            <select value={priority} onChange={(event) => setPriority(event.target.value)}>
              <option value="normal">normal</option>
              <option value="high">high</option>
              <option value="urgent">urgent</option>
              <option value="low">low</option>
            </select>
          </label>
          <label>目标发布日期
            <input type="date" value={targetPostDate} onChange={(event) => setTargetPostDate(event.target.value)} />
          </label>
          <label>计划结束
            <input type="date" value={dueAt} onChange={(event) => setDueAt(event.target.value)} />
          </label>
          <label className="is-full">Shopify 归因链接
            <input value={shopifyLink} onChange={(event) => setShopifyLink(event.target.value)} placeholder="https://your-store.myshopify.com/..." />
          </label>
        </div>
        {error ? <div className="vkpi-campaign-upload-error">{error}</div> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="is-primary" type="submit" disabled={busy}>{busy ? '保存中' : '保存推广'}</button>
        </footer>
      </form>
    </div>
  );
}

function TrackingWidget({
  row,
  tracking,
  saving,
  onChange,
  onSave,
}: {
  row: VkpiProjectRow;
  tracking: TrackingState;
  saving: boolean;
  onChange: (row: VkpiProjectRow, key: 'courier' | 'no', value: string) => void;
  onSave: (row: VkpiProjectRow) => void;
}) {
  return (
    <div className={`vkpi-campaign-tracking ${tracking.delivered ? 'is-delivered' : ''}`}>
      <span className={tracking.delivered ? 'is-green' : 'is-red'}>{tracking.delivered ? '已送达' : tracking.courier || '快递'}</span>
      <div>
        <div className="vkpi-campaign-track-form">
          <label>快递商
            <input value={tracking.courier} onChange={(event) => onChange(row, 'courier', event.target.value)} placeholder="SF / DHL / UPS" />
          </label>
          <label>快递单号
            <input value={tracking.no} onChange={(event) => onChange(row, 'no', event.target.value)} placeholder="输入 tracking no." />
          </label>
        </div>
        <strong>{tracking.status}</strong>
        {tracking.delivered ? <em>物流已送达，系统已加入今日提醒：请跟进 KOL 发布排期。</em> : null}
      </div>
      <div className="vkpi-campaign-track-actions">
        <small>每日刷新</small>
        <span>上次：{tracking.last}</span>
        <button type="button" disabled={saving} onClick={() => onSave(row)}>{saving ? '保存中' : '保存物流'}</button>
        <button type="button" disabled title="下次更新接入真实物流追踪">刷新</button>
      </div>
    </div>
  );
}
