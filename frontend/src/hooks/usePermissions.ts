import { useAuth } from './useAuth';
import { EMPLOYEE_ALLOWED_PAGES } from '../components/vkpi/layout/vkpiDashboardRouting';

/**
 * Frontend RBAC hook — mirrors backend permissions.py logic.
 *
 * Provides fine-grained permission checking without backend API calls.
 * All logic is local to the authenticated user object (from /api/auth/me).
 *
 * Permission hierarchy (matching backend):
 *   none < read < write < admin
 *
 * Usage:
 *   const perms = usePermissions();
 *   if (perms.hasPermission('vkpi', 'write')) { ... }
 *   if (perms.isManager()) { ... }
 *   if (perms.canAccessPage('projects')) { ... }
 */

type TabPermissionKey =
  | 'overview'
  | 'operations'
  | 'creators'
  | 'products'
  | 'analytics'
  | 'student'
  | 'via'
  | 'command'
  | 'runtime'
  | 'intelligence'
  | 'deepsight'
  | 'system'
  | 'kol_ops'
  | 'vkpi'
  | 'activities'
  | 'insights';

type SystemPermissionKey =
  | 'system.api_keys'
  | 'system.usage'
  | 'system.models'
  | 'system.restart'
  | 'system.members';

type PermissionKey = TabPermissionKey | SystemPermissionKey;
type PermissionLevel = 'none' | 'read' | 'write' | 'admin';
type VkpiPageKey =
  | 'command'
  | 'v615Replica'
  | 'missionControlV2'
  | 'dashboardPremium'
  | 'agents'
  | 'intelligenceCenter'
  | 'kolPoolV2'
  | 'discover'
  | 'projects'
  | 'links'
  | 'attribution'
  | 'costs'
  | 'productBattle'
  | 'industryData'
  | 'dataAnalysis'
  | 'analytics'
  | 'channels'
  | 'campaigns'
  | 'dataQuality'
  | 'reports'
  | 'audit'
  | 'settings';

interface UsePermissionsResult {
  /** Check if user has a given permission level on a tab */
  hasPermission(tab: TabPermissionKey, level?: PermissionLevel): boolean;
  /** Check if user has a given permission level on a system key */
  hasSystemPermission(key: SystemPermissionKey, level?: PermissionLevel): boolean;
  /** Check raw permission level (core validator) */
  hasPermissionLevel(value: string, level: PermissionLevel): boolean;
  /** Check if user can access a page (against EMPLOYEE_ALLOWED_PAGES) */
  canAccessPage(pageKey: VkpiPageKey): boolean;
  /** Get raw permission string for a key ('none'|'read'|'write'|'admin') */
  getPermissionLevel(key: PermissionKey): PermissionLevel;
  /** Check if user is a manager (can use manager view) */
  isManager(): boolean;
  /** Check if user is owner */
  isOwner(): boolean;
}

/**
 * Permission hierarchy validator (mirrors backend _level_allows).
 *
 * Rules:
 *   level=read:  value∈{read,write,admin} → pass
 *   level=write: value∈{write,admin}     → pass
 *   level=admin: value={admin}           → pass
 *   otherwise:                            → fail
 */
function levelAllows(value: string, level: PermissionLevel): boolean {
  const normalized = String(value || 'none').toLowerCase().trim();
  if (level === 'read') {
    return ['read', 'write', 'admin'].includes(normalized);
  }
  if (level === 'write') {
    return ['write', 'admin'].includes(normalized);
  }
  if (level === 'admin') {
    return normalized === 'admin';
  }
  return false;
}

/**
 * Employee-default-accessible pages.
 *
 * Single source of truth: imported from vkpiDashboardRouting.ts so this hook and the
 * routing gate (canAccessPage / enforcePageAccess) can never drift apart. Includes the
 * employee landing page (v615Replica = company official-account aggregate, a public asset)
 * plus the curated EMPLOYEE_NAV_ITEMS keys. Data-level scoping (scope=self) hides other
 * people's funnels / projects / activities; this set only gates manager-only pages.
 */

/**
 * OWNER_ONLY_SYSTEM_KEYS (mirrors backend permissions.py).
 * Non-owners cannot have write/admin on these.
 */
const OWNER_ONLY_SYSTEM_KEYS = new Set<SystemPermissionKey>([
  'system.api_keys',
  'system.models',
  'system.restart',
  'system.members',
]);

/**
 * Manager-eligible roles (matches canUseManagerView logic).
 */
const MANAGER_ROLES = new Set([
  'manager',
  'lead',
  'marketing_lead',
  'marketing_manager',
]);

export function usePermissions(): UsePermissionsResult {
  const { user } = useAuth();

  const isOwner = (): boolean => {
    return Boolean(user?.is_owner);
  };

  const getPermissionLevel = (key: PermissionKey): PermissionLevel => {
    if (!user?.permissions) return 'none';
    const value = String((user.permissions as Record<string, unknown>)[key] || 'none').toLowerCase().trim();
    if (['none', 'read', 'write', 'admin'].includes(value)) {
      return value as PermissionLevel;
    }
    return 'none';
  };

  const hasPermissionLevel = (value: string, level: PermissionLevel): boolean => {
    return levelAllows(value, level);
  };

  const hasPermission = (tab: TabPermissionKey, level: PermissionLevel = 'read'): boolean => {
    if (isOwner()) return true;
    return hasPermissionLevel(getPermissionLevel(tab), level);
  };

  const hasSystemPermission = (key: SystemPermissionKey, level: PermissionLevel = 'read'): boolean => {
    if (isOwner()) return true;
    if (OWNER_ONLY_SYSTEM_KEYS.has(key) && level !== 'read') {
      return false;
    }
    return hasPermissionLevel(getPermissionLevel(key), level);
  };

  const isManager = (): boolean => {
    if (isOwner()) return true;
    const staffRole = String(user?.staff_role || '').toLowerCase().trim();
    const role = String(user?.role || '').toLowerCase().trim();
    if (MANAGER_ROLES.has(staffRole) || MANAGER_ROLES.has(role)) {
      return true;
    }
    if (getPermissionLevel('vkpi') === 'admin') return true;
    if (getPermissionLevel('system.members') === 'admin') return true;
    return false;
  };

  const canAccessPage = (pageKey: VkpiPageKey): boolean => {
    if (isManager()) return true;
    return EMPLOYEE_ALLOWED_PAGES.has(pageKey);
  };

  return {
    hasPermission,
    hasSystemPermission,
    hasPermissionLevel,
    canAccessPage,
    getPermissionLevel,
    isManager,
    isOwner,
  };
}
