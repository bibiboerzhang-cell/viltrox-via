import type { VkpiPageKey } from '../vkpiTypes';

const VKPI_PAGE_KEYS = new Set<VkpiPageKey>([
  'command',
  'v615Replica',
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
  'dataAnalysis',
  'analytics',
  'channels',
  'campaigns',
  'dataQuality',
  'reports',
  'audit',
  'settings',
]);

// 员工 UI = 管理层同款全量 UI(2026-06-14 用户决策「一模一样」):员工与管理层共享同一页面集,
// 差异只在数据层(后端按 RBAC 作用域过滤:项目 own-only、成本指标隐藏、设置员工视图等)。
// 故 EMPLOYEE_ALLOWED_PAGES 直接别名到全量页面集。
const EMPLOYEE_ALLOWED_PAGES = VKPI_PAGE_KEYS;

const DEFAULT_MANAGER_PAGE: VkpiPageKey = 'v615Replica';
const DEFAULT_EMPLOYEE_PAGE: VkpiPageKey = 'v615Replica';
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
  const page = cleanVkpiPageCandidate(value);
  if (viewMode === 'manager' && (page === 'command' || page === 'dashboard')) return DEFAULT_MANAGER_PAGE;
  if (viewMode === 'employee' && (page === 'dashboard' || page === DEFAULT_MANAGER_PAGE)) return DEFAULT_EMPLOYEE_PAGE;
  if (viewMode === 'employee' && isVkpiPageKey(page) && !EMPLOYEE_ALLOWED_PAGES.has(page)) return DEFAULT_EMPLOYEE_PAGE;
  return isVkpiPageKey(page) ? page : viewMode === 'employee' ? DEFAULT_EMPLOYEE_PAGE : DEFAULT_MANAGER_PAGE;
}

// 员工版页面门禁:管理层可访问全部页;员工仅 EMPLOYEE_ALLOWED_PAGES。
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
  if (typeof window === 'undefined') return;
  const nextHash = `#${page}`;
  if (window.location.hash !== nextHash) {
    window.history.replaceState(null, '', nextHash);
  }
  const event = typeof HashChangeEvent === 'function' ? new HashChangeEvent('hashchange') : new Event('hashchange');
  window.dispatchEvent(event);
}
