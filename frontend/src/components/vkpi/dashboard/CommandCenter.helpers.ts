import { normalizeDiscoverPlatform } from '../intelligence/intelligenceDiscoveryFocus';
import type { IntelligenceAction, IntelligenceCardModel } from '../intelligence/IntelligenceCard';
import type { ProjectActionFocusPayload } from '../intelligence/intelligenceProjectActionFocus';
import type { IntelligenceActionFeedbackRecord } from '../intelligence/intelligenceActionFeedback';
import type { VkpiAlertItem, VkpiMetricEvidenceKey, VkpiProjectRow } from '../vkpiTypes';

export type Row = Record<string, unknown>;
export type EmployeeActionBucketKey = 'must' | 'watch' | 'evidence';
export const INTELLIGENCE_FOCUS_KEY = 'vkpi:intelligence-center:focus';

export interface EmployeeActionBucket {
  key: EmployeeActionBucketKey;
  title: string;
  hint: string;
  cards: IntelligenceCardModel[];
}

export function isEvidenceMetric(key: string): key is VkpiMetricEvidenceKey {
  return ['gmv', 'cost', 'roi', 'new_kol', 'published_content', 'valid_clicks', 'net_contribution', 'views', 'active_projects', 'alerts'].includes(key);
}

export function text(value: unknown, fallback = ''): string {
  const next = String(value ?? '').trim();
  return next || fallback;
}

export function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

export function asList(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item): item is Row => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : [];
}

export function numberValue(value: unknown): number {
  const parsed = Number(String(value ?? '').replace(/[$,%\s,]/g, ''));
  return Number.isFinite(parsed) ? parsed : 0;
}

export function compact(value: number): string {
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  if (Math.abs(value) >= 10_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, '')}K`;
  return Math.round(value).toLocaleString('en-US');
}

export function storeIntelligenceFocus(payload: Row) {
  if (typeof window === 'undefined') return;
  window.sessionStorage.setItem(
    INTELLIGENCE_FOCUS_KEY,
    JSON.stringify({ ...payload, createdAt: new Date().toISOString() }),
  );
}

export function employeeProjectActionCount(projects: VkpiProjectRow[]): number {
  return projects.filter((project) => !['closed', 'lost', 'cancelled', 'released'].includes(project.stage)).length;
}

export function employeeActionBucketKey(card: IntelligenceCardModel): EmployeeActionBucketKey {
  if (card.priority === 'high' && card.status !== 'done') return 'must';
  if (typeof card.confidence === 'number' && card.confidence < 0.5) return 'evidence';
  return 'watch';
}

export function buildEmployeeActionBuckets(cards: IntelligenceCardModel[]): EmployeeActionBucket[] {
  const buckets: EmployeeActionBucket[] = [
    { key: 'must', title: '今天必须做', hint: '优先处理，会影响推荐学习、项目推进或风险关闭。', cards: [] },
    { key: 'watch', title: '可观察', hint: '保持关注，有明确入口，但不一定要今天完成。', cards: [] },
    { key: 'evidence', title: '待补证据', hint: '数据不足或样本为空，先补证据再做判断。', cards: [] },
  ];
  const byKey = new Map(buckets.map((bucket) => [bucket.key, bucket]));
  for (const card of cards) {
    byKey.get(employeeActionBucketKey(card))?.cards.push(card);
  }
  return buckets;
}

export function buildProjectActionFocusCard(focus: ProjectActionFocusPayload): IntelligenceCardModel {
  return {
    id: `project-action-focus-${focus.projectId || focus.projectName}-${focus.createdAt}`,
    type: 'kol',
    priority: focus.priority,
    status: 'open',
    title: focus.title,
    summary: focus.summary,
    entityType: '项目动作',
    entityId: focus.projectId,
    confidence: 0.86,
    freshnessLabel: '项目交接',
    sourceLabel: '项目详情 / 今日任务',
    evidence: [
      { label: '项目', source: '项目详情', value: focus.projectName },
      { label: '动作', source: focus.source, value: focus.actionLabel },
      { label: '目标 KOL', source: '项目成员', value: focus.kolHandle || '-' },
      { label: '产品', source: '项目产品', value: focus.productSku || focus.productName || '-' },
    ],
    actions: [
      { label: '查看证据', kind: 'primary' },
      { label: '进入项目跟进', kind: 'secondary' },
    ],
    metadata: {
      focus_type: 'project_action',
      project_id: focus.projectId,
      project_uid: focus.projectUid,
      project_name: focus.projectName,
      row_id: focus.rowId,
      kol_handle: focus.kolHandle,
      tab: focus.tab,
    },
  };
}

export function buildEmployeeIntelligenceCards(input: {
  recommendationBacklog: Row;
  projects: VkpiProjectRow[];
  alerts: VkpiAlertItem[];
}): IntelligenceCardModel[] {
  const recommendationSummary = asRecord(input.recommendationBacklog.summary);
  const recommendationItems = asList(input.recommendationBacklog.items);
  const missingFeedback = numberValue(recommendationSummary.missing_feedback_rows);
  const firstRecommendation = asRecord(recommendationItems[0]);
  const firstKol = asRecord(firstRecommendation.kol);
  const firstLaunch = asRecord(firstRecommendation.launch);
  const firstRecommendationId = text(firstRecommendation.recommendation_id, '');
  const firstRecommendationQuery = [
    text(firstKol.handle || firstKol.display_name, ''),
    text(firstLaunch.product_sku || firstLaunch.product_name || firstLaunch.name, ''),
  ].filter(Boolean).join(' ');
  const actionableProjects = employeeProjectActionCount(input.projects);
  const urgentProject = input.projects.find((project) => ['stalled', 'contacted', 'replied', 'in_discussion', 'shipped', 'received'].includes(project.stage)) || input.projects[0];
  const urgentProjectProduct = urgentProject?.productSku || urgentProject?.productName || urgentProject?.campaign || '';
  const riskyAlerts = input.alerts.filter((alert) => alert.severity === 'danger' || alert.severity === 'warning');
  const topAlert = riskyAlerts[0] || input.alerts[0];

  return [
    {
      id: 'employee-recommendation-feedback',
      type: 'recommendation',
      priority: missingFeedback ? 'high' : 'low',
      status: 'open',
      title: missingFeedback ? `${compact(missingFeedback)} 条推荐需要判断` : '推荐反馈已清',
      summary: missingFeedback ? '先处理接受 / 拒绝 / 延后，系统才会学习你的判断。' : '当前没有待反馈推荐。',
      entityType: '推荐反馈队列',
      confidence: missingFeedback ? 0.82 : 0.45,
      freshnessLabel: '内部待反馈',
      sourceLabel: '推荐学习 / 反馈队列',
      evidence: [
        { label: '待反馈', source: '汇总', value: `${compact(missingFeedback)} 条` },
        { label: '已加载样本', source: '候选列表', value: `${compact(recommendationItems.length)} 条` },
        { label: '候选 KOL', source: text(firstKol.platform, '平台'), value: text(firstKol.handle || firstKol.display_name, '-') },
        { label: '产品', source: 'Launch', value: text(firstLaunch.product_sku || firstLaunch.product_name || firstLaunch.name, '-') },
      ],
      actions: [
        { label: '查看证据', kind: 'primary' },
        { label: '进入智能中心反馈', kind: missingFeedback ? 'secondary' : 'disabled' },
        { label: '打开红人搜索', kind: 'secondary' },
      ],
      metadata: {
        focus_type: 'recommendation_backlog',
        missing_feedback: missingFeedback,
        recommendation_id: firstRecommendationId || undefined,
        discover_query: firstRecommendationQuery || '推荐待反馈 KOL',
        discover_platform: normalizeDiscoverPlatform(firstKol.platform),
      },
    },
    {
      id: 'employee-project-actions',
      type: 'kol',
      priority: actionableProjects ? 'medium' : 'low',
      status: 'open',
      title: actionableProjects ? `${compact(actionableProjects)} 个项目可推进` : '暂无项目动作',
      summary: urgentProject ? `${urgentProject.kolName} · ${urgentProject.stage} · ${urgentProject.campaign}` : '当前没有分配项目。',
      entityType: '项目推进队列',
      entityId: urgentProject?.id,
      confidence: actionableProjects ? 0.76 : 0.25,
      freshnessLabel: '项目工作台',
      sourceLabel: 'V-KPI 项目',
      evidence: [
        { label: '可推进项目', source: '项目列表', value: `${compact(actionableProjects)} 个` },
        { label: '优先项目', source: urgentProject?.platform || '项目', value: urgentProject ? `${urgentProject.kolName} · ${urgentProject.productSku || urgentProject.productName || '-'}` : '-' },
        { label: '阶段', source: '工作流', value: urgentProject?.stage || '-' },
        { label: '负责人', source: '项目负责人', value: urgentProject?.ownerName || '-' },
      ],
      actions: [
        { label: '查看证据', kind: 'primary' },
        { label: '进入项目跟进', kind: 'secondary' },
        { label: '发现相似 KOL', kind: urgentProject ? 'secondary' : 'disabled' },
      ],
      metadata: {
        project_id: urgentProject?.id,
        project_name: urgentProject?.campaign,
        discover_query: urgentProject ? `${urgentProjectProduct} ${urgentProject.platform} review creator`.trim() : undefined,
        discover_platform: urgentProject ? normalizeDiscoverPlatform(urgentProject.platform) : undefined,
      },
    },
    {
      id: 'employee-risk-alerts',
      type: 'sync',
      priority: riskyAlerts.length ? 'high' : 'low',
      status: riskyAlerts.length ? 'open' : 'done',
      title: riskyAlerts.length ? `${compact(riskyAlerts.length)} 条风险提醒` : '风险提醒已清',
      summary: topAlert ? `${topAlert.label} · ${topAlert.description || topAlert.count}` : '当前没有需要处理的风险提醒。',
      entityType: '风险提醒队列',
      entityId: topAlert?.id,
      confidence: topAlert ? 0.72 : 0.3,
      freshnessLabel: '看板提醒',
      sourceLabel: '风险提醒',
      evidence: [
        { label: '风险提醒', source: '提醒池', value: `${compact(riskyAlerts.length)} 条` },
        { label: '最高优先级', source: topAlert?.severity || '提醒', value: topAlert?.label || '-' },
        { label: '描述', source: '提醒详情', value: topAlert?.description || '-' },
      ],
      actions: [
        { label: '查看证据', kind: 'primary' },
        { label: '进入项目跟进', kind: 'secondary' },
      ],
    },
  ];
}

export function actionFeedbackActions(status: IntelligenceCardModel['status']): IntelligenceAction[] {
  return [
    { label: '接受动作', kind: status === 'done' || status === 'accepted' ? 'disabled' : 'secondary' },
    { label: '延后处理', kind: status === 'done' || status === 'snoozed' ? 'disabled' : 'secondary' },
    { label: '标记完成', kind: status === 'done' ? 'disabled' : 'secondary' },
  ];
}

export function applyActionFeedback(card: IntelligenceCardModel, feedback?: IntelligenceActionFeedbackRecord): IntelligenceCardModel {
  const nextStatus = feedback?.status || card.status;
  const baseActions = card.actions.filter((action) => !['接受动作', '延后处理', '标记完成'].includes(action.label));
  return {
    ...card,
    status: nextStatus,
    actions: [...baseActions, ...actionFeedbackActions(nextStatus)],
    metadata: {
      ...card.metadata,
      feedback_status: feedback?.status,
      feedback_updated_at: feedback?.updatedAt,
    },
  };
}

export function priorityLabel(priority: IntelligenceCardModel['priority']): string {
  if (priority === 'high') return '高';
  if (priority === 'low') return '低';
  return '中';
}
