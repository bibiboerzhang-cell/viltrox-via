type Row = Record<string, unknown>;

export interface RepairRoadmapFocusPayload {
  schemaVersion: 'v2-repair-roadmap-focus-v0';
  source: 'dashboard_intelligence_roadmap' | 'manual';
  key: string;
  title: string;
  statusLabel: string;
  statusTone: 'ready' | 'partial' | 'planned';
  readiness: string;
  budgetPolicy: string;
  dataNeeded: string;
  output: string;
  gate: string;
  actionLabel: string;
  actionPage: string;
  createdAt: string;
}

const REPAIR_ROADMAP_FOCUS_KEY = 'vkpi:repair-center:roadmap-focus';

function text(value: unknown, fallback = ''): string {
  const next = String(value ?? '').trim();
  return next || fallback;
}

function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

function tone(value: unknown): RepairRoadmapFocusPayload['statusTone'] {
  const key = text(value);
  if (key === 'ready' || key === 'partial' || key === 'planned') return key;
  return 'planned';
}

export function writeRepairRoadmapFocus(payload: Omit<RepairRoadmapFocusPayload, 'schemaVersion' | 'createdAt'> & { createdAt?: string }) {
  if (typeof window === 'undefined') return;
  window.sessionStorage.setItem(REPAIR_ROADMAP_FOCUS_KEY, JSON.stringify({
    schemaVersion: 'v2-repair-roadmap-focus-v0',
    ...payload,
    createdAt: payload.createdAt || new Date().toISOString(),
  }));
}

export function readRepairRoadmapFocus(): RepairRoadmapFocusPayload | null {
  if (typeof window === 'undefined') return null;
  const raw = window.sessionStorage.getItem(REPAIR_ROADMAP_FOCUS_KEY);
  if (!raw) return null;
  try {
    const parsed = asRecord(JSON.parse(raw));
    const key = text(parsed.key);
    const title = text(parsed.title);
    if (!key || !title) return null;
    return {
      schemaVersion: 'v2-repair-roadmap-focus-v0',
      source: text(parsed.source) === 'dashboard_intelligence_roadmap' ? 'dashboard_intelligence_roadmap' : 'manual',
      key,
      title,
      statusLabel: text(parsed.statusLabel, '待接入'),
      statusTone: tone(parsed.statusTone),
      readiness: text(parsed.readiness, '0%'),
      budgetPolicy: text(parsed.budgetPolicy, '需要预算策略。'),
      dataNeeded: text(parsed.dataNeeded, '需要数据依赖。'),
      output: text(parsed.output, '需要定义产出。'),
      gate: text(parsed.gate, '需要准入门槛。'),
      actionLabel: text(parsed.actionLabel, '查看相关页面'),
      actionPage: text(parsed.actionPage, 'dashboardPremium'),
      createdAt: text(parsed.createdAt, new Date().toISOString()),
    };
  } catch {
    return null;
  }
}

export function clearRepairRoadmapFocus() {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(REPAIR_ROADMAP_FOCUS_KEY);
}
