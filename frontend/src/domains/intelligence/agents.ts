import {
  getDashboardAgentsInbox,
  type VkpiAgentInboxItem,
  type VkpiAgentsInboxResponse,
} from '../../services/vkpi/dashboard-api';

export type IntelligenceAgentInboxItem = VkpiAgentInboxItem;
export type IntelligenceAgentsInboxResponse = VkpiAgentsInboxResponse;

export const intelligenceAgentFilters = [
  { id: '', label: '全部' },
  { id: 'brief', label: '简报' },
  { id: 'recommendation', label: '推荐' },
  { id: 'evidence', label: '证据' },
  { id: 'sync_sentinel', label: '同步' },
  { id: 'brain', label: '脑层' },
  { id: 'market_intel', label: '市场' },
] as const;

export function fetchIntelligenceAgentsInbox(
  token: string,
  params: { limit?: number; agentId?: string } = {},
) {
  return getDashboardAgentsInbox(token, params);
}

export function formatAgentTime(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function agentValueLabel(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') return value.toLocaleString('en-US');
  if (Array.isArray(value)) return `${value.length} 项`;
  if (typeof value === 'object') return JSON.stringify(value).slice(0, 80);
  return String(value);
}

export function agentStatusLabel(status: string): string {
  if (status === 'active') return '在线';
  if (status === 'warning') return '警告';
  return '空闲';
}

export function selectFirstAvailableAgent(
  items: IntelligenceAgentInboxItem[],
  selectedId: string,
): string {
  return selectedId && items.some((item) => item.id === selectedId)
    ? selectedId
    : items[0]?.id || '';
}
