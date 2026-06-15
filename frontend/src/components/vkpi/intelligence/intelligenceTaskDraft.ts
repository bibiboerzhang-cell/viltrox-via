import type { IntelligenceCardModel } from './IntelligenceCard';
import type { VkpiPageKey } from '../vkpiTypes';

export type IntelligenceTaskWorkstream = 'recommendation_review' | 'market_watch' | 'evidence_review' | 'sync_review' | 'repair_review' | 'brief_followup';

export interface IntelligenceTaskBridgeTarget {
  page: VkpiPageKey;
  label: string;
  intent: string;
  primary?: boolean;
}

export interface IntelligenceTaskFocusPayload {
  page: VkpiPageKey;
  label: string;
  intent: string;
  taskTitle: string;
  objective: string;
  workstream: IntelligenceTaskWorkstream;
  priority: 'high' | 'medium' | 'low';
  sourceLabel: string;
  cardId: string;
  createdAt: string;
}

export interface IntelligenceTaskDraft {
  schemaVersion: 'v2-intelligence-task-draft-v0';
  mode: 'local_only';
  cardId: string;
  cardType: string;
  title: string;
  objective: string;
  workstream: IntelligenceTaskWorkstream;
  priority: 'high' | 'medium' | 'low';
  ownerRole: string;
  entityType: string;
  entityId?: string;
  sourceLabel: string;
  duePolicy: string;
  evidenceRefs: Array<{ label: string; source: string; url?: string }>;
  futureWriteTarget: 'vkpi_operating_task' | 'vkpi_project_task' | 'vkpi_repair_task';
  createdAt: string;
  metadata: Record<string, unknown>;
}

export const INTELLIGENCE_TASK_FOCUS_KEY = 'vkpi:intelligence-task:focus';

function taskWorkstreamForCard(card: IntelligenceCardModel): IntelligenceTaskWorkstream {
  if (card.type === 'recommendation' || card.metadata?.recommendation_id) return 'recommendation_review';
  if (card.type === 'market' || card.type === 'competitor') return 'market_watch';
  if (card.type === 'evidence' || card.type === 'kol') return 'evidence_review';
  if (card.type === 'sync' || card.type === 'system') return 'sync_review';
  if (card.type === 'repair') return 'repair_review';
  return 'brief_followup';
}

function ownerRoleForCard(card: IntelligenceCardModel): string {
  if (card.type === 'recommendation') return 'KOL operator';
  if (card.type === 'market' || card.type === 'competitor') return 'Market lead';
  if (card.type === 'sync' || card.type === 'system' || card.type === 'repair') return 'Ops owner';
  if (card.type === 'evidence' || card.type === 'kol') return 'Evidence reviewer';
  return 'Marketing manager';
}

function futureWriteTargetForCard(card: IntelligenceCardModel): IntelligenceTaskDraft['futureWriteTarget'] {
  if (card.type === 'recommendation' || card.type === 'kol') return 'vkpi_project_task';
  if (card.type === 'repair' || card.type === 'sync' || card.type === 'system') return 'vkpi_repair_task';
  return 'vkpi_operating_task';
}

function duePolicyForCard(card: IntelligenceCardModel): string {
  if (card.priority === 'high') return 'next_business_day';
  if (card.priority === 'medium') return 'within_3_business_days';
  return 'within_7_business_days';
}

function objectiveForCard(card: IntelligenceCardModel): string {
  if (card.type === 'recommendation') return 'Review this recommendation, decide contact/watch/reject, and record outcome.';
  if (card.type === 'market' || card.type === 'competitor') return 'Validate the signal, attach source evidence, and decide whether it becomes a campaign opportunity or risk.';
  if (card.type === 'repair') return 'Review the diagnosis and decide whether to authorize a bounded repair patch.';
  if (card.type === 'sync' || card.type === 'system') return 'Confirm the system state and route any blocker to the responsible operator.';
  if (card.type === 'evidence' || card.type === 'kol') return 'Inspect evidence quality and fill any missing evidence fields before using the card for decisions.';
  return 'Turn the intelligence card into a concrete follow-up action.';
}

export function buildIntelligenceTaskDraft(card: IntelligenceCardModel, options: { pagePath: string } = { pagePath: 'intelligence' }): IntelligenceTaskDraft {
  return {
    schemaVersion: 'v2-intelligence-task-draft-v0',
    mode: 'local_only',
    cardId: card.id,
    cardType: card.type,
    title: `处理：${card.title}`,
    objective: objectiveForCard(card),
    workstream: taskWorkstreamForCard(card),
    priority: card.priority,
    ownerRole: ownerRoleForCard(card),
    entityType: card.entityType,
    entityId: card.entityId,
    sourceLabel: card.sourceLabel,
    duePolicy: duePolicyForCard(card),
    evidenceRefs: card.evidence.map((item) => ({
      label: item.label,
      source: item.source,
      url: item.url,
    })),
    futureWriteTarget: futureWriteTargetForCard(card),
    createdAt: new Date().toISOString(),
    metadata: {
      page_path: options.pagePath,
      confidence: card.confidence,
      freshness_label: card.freshnessLabel,
      card_metadata: card.metadata || {},
    },
  };
}

export function taskDraftEndpointHint(draft: IntelligenceTaskDraft): string {
  if (draft.futureWriteTarget === 'vkpi_project_task') return 'future: POST /api/admin/vkpi/projects/{id}/tasks';
  if (draft.futureWriteTarget === 'vkpi_repair_task') return 'future: POST /api/admin/vkpi/repair/tasks';
  return 'future: POST /api/admin/vkpi/operating-tasks';
}

function isTaskFocusPayload(value: unknown): value is IntelligenceTaskFocusPayload {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

export function buildTaskBridgeFocus(
  draft: IntelligenceTaskDraft,
  target: IntelligenceTaskBridgeTarget,
): IntelligenceTaskFocusPayload {
  return {
    page: target.page,
    label: target.label,
    intent: target.intent,
    taskTitle: draft.title,
    objective: draft.objective,
    workstream: draft.workstream,
    priority: draft.priority,
    sourceLabel: draft.sourceLabel,
    cardId: draft.cardId,
    createdAt: new Date().toISOString(),
  };
}

export function writeTaskBridgeFocus(payload: IntelligenceTaskFocusPayload) {
  if (typeof window === 'undefined') return;
  window.sessionStorage.setItem(INTELLIGENCE_TASK_FOCUS_KEY, JSON.stringify(payload));
}

export function readTaskBridgeFocus(): IntelligenceTaskFocusPayload | null {
  if (typeof window === 'undefined') return null;
  const raw = window.sessionStorage.getItem(INTELLIGENCE_TASK_FOCUS_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return isTaskFocusPayload(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function clearTaskBridgeFocus() {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(INTELLIGENCE_TASK_FOCUS_KEY);
}

function uniqueTargets(targets: IntelligenceTaskBridgeTarget[]): IntelligenceTaskBridgeTarget[] {
  const seen = new Set<VkpiPageKey>();
  return targets.filter((target) => {
    if (seen.has(target.page)) return false;
    seen.add(target.page);
    return true;
  });
}

export function taskDraftBridgeTargets(
  draft: IntelligenceTaskDraft,
  viewMode: 'manager' | 'employee' = 'manager',
): IntelligenceTaskBridgeTarget[] {
  const targets: IntelligenceTaskBridgeTarget[] = [];
  if (viewMode === 'employee') {
    targets.push({
      page: 'command',
      label: '回我的工作台',
      intent: '在我的工作台处理今日任务和项目。',
      primary: true,
    });
  }

  if (draft.workstream === 'recommendation_review') {
    targets.push(
      { page: 'projects', label: '进入项目跟进', intent: '把候选 KOL 放入项目推进或检查现有项目。', primary: viewMode === 'manager' },
      { page: 'discover', label: '打开红人搜索', intent: '补充候选资料、联系方式和最近内容。' },
      { page: 'channels', label: '查看 KOL/账号', intent: '确认账号归属、claim 状态和平台内容。' },
    );
  } else if (draft.workstream === 'market_watch') {
    targets.push(
      { page: 'productBattle', label: '进入产品作战', intent: '把市场/竞品信号转成 SKU 和 campaign 判断。', primary: viewMode === 'manager' },
      { page: 'dataAnalysis', label: '查看数据分析', intent: '下钻平台、内容和自然搜索信号。' },
    );
  } else if (draft.workstream === 'evidence_review') {
    targets.push(
      { page: 'dataQuality', label: '检查数据质量', intent: '确认 evidence refs、媒体状态和数据可信标记。', primary: viewMode === 'manager' },
      { page: 'channels', label: '查看 KOL/账号', intent: '回到账号与内容矩阵补证据。' },
    );
  } else if (draft.workstream === 'sync_review') {
    targets.push(
      { page: 'dataQuality', label: '查看数据质量', intent: '确认同步、媒体、comment、delta 的阻塞状态。', primary: viewMode === 'manager' },
      { page: 'settings', label: '进入系统设置', intent: '只读检查 sync guard、provider 和运行状态。' },
    );
  } else if (draft.workstream === 'repair_review') {
    targets.push(
      { page: 'repairCenter', label: '留在修复中心', intent: '继续按 repair policy 做 dry-run 或人工确认。', primary: viewMode === 'manager' },
      { page: 'audit', label: '查看审计', intent: '确认修复动作需要的审计和权限边界。' },
    );
  } else {
    targets.push(
      { page: viewMode === 'employee' ? 'command' : 'missionControlV2', label: viewMode === 'employee' ? '回我的工作台' : '回管理主控', intent: '回到今日主视图。', primary: true },
      { page: 'reports', label: '查看报表', intent: '把简报判断沉淀到周报或经营报告。' },
    );
  }

  return uniqueTargets(targets);
}
