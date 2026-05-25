type Row = Record<string, unknown>;

export interface ProjectActionFocusPayload {
  schemaVersion: 'v2-project-action-focus-v0';
  source: 'project_detail' | 'project_focus' | 'manual';
  projectId?: string;
  projectUid?: string;
  projectName: string;
  rowId?: string;
  kolId?: string;
  kolHandle?: string;
  productSku?: string;
  productName?: string;
  title: string;
  summary: string;
  priority: 'high' | 'medium' | 'low';
  actionLabel: string;
  tab?: string;
  createdAt: string;
}

const PROJECT_ACTION_FOCUS_KEY = 'vkpi:projects:action-focus';

function text(value: unknown, fallback = ''): string {
  const next = String(value ?? '').trim();
  return next || fallback;
}

function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

function priority(value: unknown): ProjectActionFocusPayload['priority'] {
  const key = text(value).toLowerCase();
  if (key === 'high' || key === '高') return 'high';
  if (key === 'low' || key === '低') return 'low';
  return 'medium';
}

export function writeProjectActionFocus(payload: Omit<ProjectActionFocusPayload, 'schemaVersion' | 'createdAt'> & { createdAt?: string }) {
  if (typeof window === 'undefined') return;
  window.sessionStorage.setItem(PROJECT_ACTION_FOCUS_KEY, JSON.stringify({
    schemaVersion: 'v2-project-action-focus-v0',
    ...payload,
    createdAt: payload.createdAt || new Date().toISOString(),
  }));
}

export function readProjectActionFocus(): ProjectActionFocusPayload | null {
  if (typeof window === 'undefined') return null;
  const raw = window.sessionStorage.getItem(PROJECT_ACTION_FOCUS_KEY);
  if (!raw) return null;
  try {
    const parsed = asRecord(JSON.parse(raw));
    const projectName = text(parsed.projectName);
    const title = text(parsed.title);
    if (!projectName || !title) return null;
    return {
      schemaVersion: 'v2-project-action-focus-v0',
      source: ['project_detail', 'project_focus', 'manual'].includes(text(parsed.source))
        ? parsed.source as ProjectActionFocusPayload['source']
        : 'manual',
      projectId: text(parsed.projectId || parsed.id, '') || undefined,
      projectUid: text(parsed.projectUid || parsed.project_uid, '') || undefined,
      projectName,
      rowId: text(parsed.rowId, '') || undefined,
      kolId: text(parsed.kolId, '') || undefined,
      kolHandle: text(parsed.kolHandle, '') || undefined,
      productSku: text(parsed.productSku, '') || undefined,
      productName: text(parsed.productName, '') || undefined,
      title,
      summary: text(parsed.summary, '项目有一条待执行动作。'),
      priority: priority(parsed.priority),
      actionLabel: text(parsed.actionLabel, '进入项目跟进'),
      tab: text(parsed.tab, '') || undefined,
      createdAt: text(parsed.createdAt, new Date().toISOString()),
    };
  } catch {
    return null;
  }
}

export function clearProjectActionFocus() {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(PROJECT_ACTION_FOCUS_KEY);
}
