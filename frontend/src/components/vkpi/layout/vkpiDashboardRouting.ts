import type { VkpiPageKey } from '../vkpiTypes';

const VKPI_PAGE_KEYS = new Set<VkpiPageKey>([
  'command',
  'cockpit',
  'missionControlV2',
  'dashboardPremium',
  'agents',
  'intelligenceCenter',
  'kolPoolV2',
  'discover',
  'projects',
  'links',
  'attribution',
  'costs',
  'productBattle',
  'industryData',
  'industryBoard',
  'dataExport',
  'dataQuery',
  'marketTrends',
  'learning',
  'capabilities',
  'skillStudio',
  'discovery2',
  'dataAnalysis',
  'analytics',
  'channels',
  'campaigns',
  'dataQuality',
  'reports',
  'audit',
  'settings',
]);

const VKPI_PAGE_ALIASES: Record<string, VkpiPageKey> = {
  // Repair Center was frozen and removed. Keep old task/hash targets useful by
  // sending them to the live operations and data-quality surface.
  repairCenter: 'dataQuality',
  ops: 'dataQuality',
};

// 成员默认可达页面(单一事实来源,2026-06-14 RBAC UX 决策):
// - cockpit:成员默认落地页 = 公司官方账号聚合看板 + Events / MY KOL / 项目宿主(公共资产 + 本人数据)。
// - 其余键与 EMPLOYEE_NAV_ITEMS(vkpiLayoutConstants.ts)一一对应:我的工作台 / MY KOL / 搜索红人 /
//   我的项目 / 我的短链 / 我的归因 / 我的周报 / 个人设置。
// 数据可见范围由 scope=self 在数据层收窄(别人的漏斗/项目/活动不可见);页面门禁只挡管理层专属页
// (costs / audit / dataQuality / productBattle / dataAnalysis / agents / kolPoolV2 / 智能中心等)。
// usePermissions.ts 直接 import 本常量,保证两套权限层不再各自维护一份而漂移。
export const EMPLOYEE_ALLOWED_PAGES = new Set<VkpiPageKey>([
  'cockpit',
  'command',
  'channels',
  'discover',
  'projects',
  'links',
  'attribution',
  'reports',
  'settings',
]);

const DEFAULT_MANAGER_PAGE: VkpiPageKey = 'cockpit';
const DEFAULT_EMPLOYEE_PAGE: VkpiPageKey = 'cockpit';
const importMetaEnv = (import.meta as { env?: { DEV?: boolean } }).env;

if (importMetaEnv?.DEV) {
  VKPI_PAGE_KEYS.add('glass-demo');
}

export function isVkpiPageKey(value: string): value is VkpiPageKey {
  return VKPI_PAGE_KEYS.has(value as VkpiPageKey);
}

export function cleanVkpiPageCandidate(value: string): string {
  return value.trim().replace(/^#\/?/, '').split(/[?#]/)[0];
}

export function normalizeVkpiPage(value: string, viewMode: 'manager' | 'employee'): VkpiPageKey {
  const candidate = cleanVkpiPageCandidate(value);
  const page: string = VKPI_PAGE_ALIASES[candidate] || candidate;
  if (viewMode === 'manager' && (page === 'command' || page === 'dashboard')) return DEFAULT_MANAGER_PAGE;
  if (viewMode === 'employee' && (page === 'dashboard' || page === DEFAULT_MANAGER_PAGE)) return DEFAULT_EMPLOYEE_PAGE;
  if (viewMode === 'employee' && isVkpiPageKey(page) && !EMPLOYEE_ALLOWED_PAGES.has(page)) return DEFAULT_EMPLOYEE_PAGE;
  return isVkpiPageKey(page) ? page : viewMode === 'employee' ? DEFAULT_EMPLOYEE_PAGE : DEFAULT_MANAGER_PAGE;
}

// 成员版页面门禁:管理层可访问全部页;成员仅 EMPLOYEE_ALLOWED_PAGES。
export function canAccessPage(page: VkpiPageKey, viewMode: 'manager' | 'employee'): boolean {
  if (viewMode === 'manager') {
    return VKPI_PAGE_KEYS.has(page);
  }
  return EMPLOYEE_ALLOWED_PAGES.has(page);
}

// 越权访问优雅拦截:不可访问 → 回退到该视图默认页。
export function enforcePageAccess(page: VkpiPageKey, viewMode: 'manager' | 'employee'): VkpiPageKey {
  if (canAccessPage(page, viewMode)) {
    return page;
  }
  return viewMode === 'employee' ? DEFAULT_EMPLOYEE_PAGE : DEFAULT_MANAGER_PAGE;
}

export function getInitialVkpiPage(viewMode: 'manager' | 'employee'): VkpiPageKey {
  if (typeof window === 'undefined') return viewMode === 'employee' ? DEFAULT_EMPLOYEE_PAGE : DEFAULT_MANAGER_PAGE;
  const hashPage = window.location.hash.replace(/^#\/?/, '');
  const queryPage = new URLSearchParams(window.location.search).get('page') || '';
  return normalizeVkpiPage(hashPage || queryPage, viewMode);
}

export function writeVkpiHash(page: VkpiPageKey) {
  if (typeof window === 'undefined') return false;
  const nextHash = `#${page}`;
  // replaceState 不会原生触发 hashchange，所以只在真实变化后补发一次。
  // 相同 hash 若继续派发，会让监听器 write -> dispatch -> write 无限递归。
  if (window.location.hash === nextHash) return false;
  // 只替换 hash，保留 Cockpit 深链中的 pathname 和 query（例如
  // /?cockpit=dealers#cockpit）。若只写 nextHash，replaceState 会把 query 一并丢掉，
  // 导致内层 CockpitApp 读不到目标页而回落 Dashboard。
  const nextUrl = `${window.location.pathname}${window.location.search}${nextHash}`;
  window.history.replaceState(null, '', nextUrl);
  const event = typeof HashChangeEvent === 'function' ? new HashChangeEvent('hashchange') : new Event('hashchange');
  window.dispatchEvent(event);
  return true;
}
