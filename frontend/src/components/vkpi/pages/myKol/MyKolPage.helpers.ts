import type { OfficialChannelAccount, StaffManagedSummary } from '../channels/channelTypes';
import type { VkpiDashboardData, VkpiProjectRow } from '../../vkpiTypes';
import { platformDisplay, safeNumber } from '../../shared/vkpiDataUtils';
import { projectDate } from '../channels/myKolMatrixData';
import type { FunnelStageKey, PlatformFilter } from '../channels/myKolMatrixTypes';

export type StaffCard = {
  id: string;
  name: string;
  role: string;
  avatar?: string;
  focus?: string;
  accent?: string;
  accounts: OfficialChannelAccount[];
  projects: VkpiProjectRow[];
  // A1/A2:后端 staff_managed 接真——分管 KOL 数/粉丝合计/视频数 + 精简名单(cap 20)
  managed?: StaffManagedSummary;
};

// 2026-06-16:仅保留能匹配真实 staff 的展示元数据。删掉 Kevin Chen / Maya Liu / Tom Chen
// 这三个无真实 staff 匹配的 mock —— 它们此前被渲染成空幻影负责人卡(无效账号)。
export const knownStaffDisplay = [
  { id: 'display-jianbo', name: 'Jianbo Z', role: 'Founder', focus: '焦点: 全局 + 战略', accent: '#10b981' },
];

export const platformAccent: Record<string, string> = {
  facebook: '#2f80ed',
  instagram: '#ec4899',
  reddit: '#f97316',
  tiktok: '#06d6d6',
  x: '#94a3b8',
  twitter: '#94a3b8',
  youtube: '#ff1744',
};

export const employeePlatformOrder: Exclude<PlatformFilter, 'all'>[] = ['YouTube', 'Instagram', 'TikTok', 'Facebook', 'Reddit', 'X'];

export const employeeFunnelStages: Array<{ key: FunnelStageKey; label: string }> = [
  { key: 'claimed', label: '已认领' },
  { key: 'contacted', label: '已联系' },
  { key: 'replied', label: '已回复' },
  { key: 'agreed', label: '已合作' },
  { key: 'shipped', label: '已发货' },
  { key: 'received', label: '已到货' },
  { key: 'published', label: '已发布' },
];

export function employeeFunnelStage(projects: VkpiProjectRow[]): FunnelStageKey {
  if (!projects.length) return 'claimed';
  const latest = [...projects].sort((left, right) => projectDate(right) - projectDate(left))[0];
  const stage = latest?.stage || '';
  if (stage === 'contacted') return 'contacted';
  if (stage === 'replied') return 'replied';
  if (stage === 'agreed') return 'agreed';
  if (stage === 'shipped') return 'shipped';
  if (stage === 'received') return 'received';
  if (['content_published', 'published', 'released', 'measured', 'closed'].includes(stage)) return 'published';
  return 'claimed';
}

export function compactNumber(value: number | null | undefined) {
  const next = safeNumber(value);
  if (!next) return '—';
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: next >= 1000 ? 1 : 0 }).format(next);
}

export function signedNumber(value: number | null | undefined, unit = '') {
  const next = safeNumber(value);
  if (!next) return '';
  const sign = next > 0 ? '+' : '';
  return `${sign}${compactNumber(next)}${unit}`;
}

export function initials(name: string) {
  return name
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase() || 'K';
}

export function staffIdentityKey(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '');
}

export function staffFirstToken(value: string) {
  return staffIdentityKey(value.split(/\s+/)[0] || value);
}

export function matchesKnownStaff(card: StaffCard, known: typeof knownStaffDisplay[number]) {
  const cardName = staffIdentityKey(card.name);
  const knownName = staffIdentityKey(known.name);
  const knownFirst = staffFirstToken(known.name);
  return Boolean(knownFirst && (cardName === knownName || cardName === knownFirst || knownName.includes(cardName) || cardName.includes(knownFirst)));
}

export function isGenericStaffShell(card: StaffCard) {
  const name = staffIdentityKey(card.name);
  // A1:分管 KOL 也算真实数据——有分管的 admin/staff 壳不再被当幻影滤掉
  return (name === 'admin' || name === 'staff' || name === 'staffuser')
    && !card.accounts.length && !card.projects.length && !(card.managed?.managedKolCount);
}

export function staffDisplayRole(actual: string, fallback: string) {
  const normalized = actual.trim().toLowerCase();
  return normalized && !['admin', 'readonly', 'staff'].includes(normalized) ? actual : fallback;
}

export function isAdminLike(card: Pick<StaffCard, 'name' | 'role'>) {
  const value = `${card.name} ${card.role}`.toLowerCase();
  return value.includes('admin') || value.includes('director') || value.includes('founder') || value.includes('kevin') || value.includes('jianbo');
}

export function staffFocusLine(card: StaffCard) {
  if (card.focus) return card.focus;
  const value = `${card.name} ${card.role}`.toLowerCase();
  if (value.includes('maya')) return '焦点: 135mm LAB · CineGear';
  if (value.includes('tom')) return '焦点: 56mm 复推';
  if (value.includes('founder') || value.includes('director') || value.includes('admin')) return '焦点: 全局';
  const platformNames = Array.from(new Set(card.accounts.map((account) => platformDisplay(account.platform)))).slice(0, 2);
  if (platformNames.length) return `焦点: ${platformNames.join(' · ')}`;
  return '焦点: 暂无';
}

export function statusLabel(value: string) {
  const labels: Record<string, string> = {
    configured_pending_provider: '待同步',
    no_results: '无结果',
    not_configured: '待配置',
    not_supported: '未接入',
    official_readonly: '只读',
    synced: '已同步',
  };
  return labels[value] || value || '暂无';
}

export function readCollapse(storageKey: string, fallback: boolean) {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(storageKey);
    return raw === null ? fallback : raw === '1';
  } catch {
    return fallback;
  }
}

export function writeCollapse(storageKey: string, value: boolean) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(storageKey, value ? '1' : '0');
  } catch {
    // localStorage 不可用忽略,折叠状态仅本会话生效
  }
}

export const COLLAPSE_KEY_TEAM = 'vkpi:mykol-collapse:team';
export const COLLAPSE_KEY_OFFICIAL = 'vkpi:mykol-collapse:official';

// G-ui:公司账号分组——后端 account.group 三态,UI 归到 3 个有序段。
export const ACCOUNT_GROUPS: Array<{ key: string; label: string }> = [
  { key: 'main_brand', label: '主品牌' },
  { key: 'product_line', label: '产品线' },
  { key: 'regional', label: '区域' },
];

export function accountGroupKey(account: OfficialChannelAccount) {
  const raw = String(account.group || '').toLowerCase();
  if (raw === 'main_brand' || raw === 'product_line' || raw === 'regional') return raw;
  return 'main_brand';
}

export function normalizeHandle(value: string | undefined) {
  return String(value || '').trim().toLowerCase().replace(/^@/, '').replace(/[\s_-]+/g, '.');
}

export function isOwnedMatrixLike(kol: VkpiDashboardData['kolOptions'][number]) {
  const handle = normalizeHandle(kol.handle || kol.name);
  return [
    'viltrox',
    'viltrox.official',
    'viltroxofficial',
    'viltrox.global',
    'viltrox.cine',
    'viltrox.flash',
    'viltrox.us',
    'viltrox.usa',
    'viltrox.community',
    'viltrox.thailand',
  ].includes(handle);
}

// Temporary until kolOptions carries content counts from the posts endpoint.
export const contentReadyDefaultKolIds = new Set(['110', '2741', '2742', '3015', '3603']);

export function preferredEmployeeKolItem<T extends { kol: { id: string } }>(items: T[]) {
  return items.find((item) => contentReadyDefaultKolIds.has(String(item.kol.id))) || items[0];
}
