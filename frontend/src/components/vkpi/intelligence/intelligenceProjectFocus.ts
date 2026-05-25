type Row = Record<string, unknown>;

export interface IntelligenceProjectFocusPayload {
  schemaVersion: 'v2-project-focus-v0';
  source: 'discover_candidate_decision' | 'intelligence_task' | 'manual';
  projectId?: string;
  projectUid?: string;
  projectName: string;
  kolId?: string;
  kolHandle?: string;
  productSku?: string;
  productName?: string;
  summary: string;
  createdAt: string;
}

const PROJECT_FOCUS_KEY = 'vkpi:projects:focus';

function text(value: unknown, fallback = ''): string {
  const next = String(value ?? '').trim();
  return next || fallback;
}

function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

export function writeProjectFocus(payload: Omit<IntelligenceProjectFocusPayload, 'schemaVersion' | 'createdAt'> & { createdAt?: string }) {
  if (typeof window === 'undefined') return;
  window.sessionStorage.setItem(PROJECT_FOCUS_KEY, JSON.stringify({
    schemaVersion: 'v2-project-focus-v0',
    ...payload,
    createdAt: payload.createdAt || new Date().toISOString(),
  }));
}

export function readProjectFocus(): IntelligenceProjectFocusPayload | null {
  if (typeof window === 'undefined') return null;
  const raw = window.sessionStorage.getItem(PROJECT_FOCUS_KEY);
  if (!raw) return null;
  try {
    const parsed = asRecord(JSON.parse(raw));
    const projectName = text(parsed.projectName);
    if (!projectName) return null;
    return {
      schemaVersion: 'v2-project-focus-v0',
      source: ['discover_candidate_decision', 'intelligence_task', 'manual'].includes(text(parsed.source))
        ? parsed.source as IntelligenceProjectFocusPayload['source']
        : 'manual',
      projectId: text(parsed.projectId || parsed.id, '') || undefined,
      projectUid: text(parsed.projectUid || parsed.project_uid, '') || undefined,
      projectName,
      kolId: text(parsed.kolId, '') || undefined,
      kolHandle: text(parsed.kolHandle, '') || undefined,
      productSku: text(parsed.productSku, '') || undefined,
      productName: text(parsed.productName, '') || undefined,
      summary: text(parsed.summary, '项目已创建，继续推进阶段、物流、费用和证据。'),
      createdAt: text(parsed.createdAt, new Date().toISOString()),
    };
  } catch {
    return null;
  }
}

export function clearProjectFocus() {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(PROJECT_FOCUS_KEY);
}
