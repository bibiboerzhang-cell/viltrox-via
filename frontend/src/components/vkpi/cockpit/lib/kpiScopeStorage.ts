// 【D4】KPI scope 记忆(per-staff):Dashboard KPI 卡/弹窗的 scope(all/kol/company)按 staff id
// 独立存 localStorage,防同一浏览器多账号登录互相串号。
// 旧共享键(vkpi-dashboard-state-v1.kpiScope,见 lib/storage.ts)保留为身份未知时的兜底初值;
// 身份就绪(staff id > 0)后以本模块的 per-staff 键为准(CockpitApp 挂了读回 effect)。

const VALID_KPI_SCOPES = new Set(["all", "kol", "company"]);

function kpiScopeStorageKey(staffId: unknown): string {
  return `vkpi:kpi-scope:${Number(staffId) || 0}`;
}

/** 读回该员工上次选择的 KPI scope;无记录/值非法/无身份(id<=0)返回 null。 */
export function loadKpiScopeForStaff(staffId: unknown): string | null {
  const id = Number(staffId) || 0;
  if (id <= 0 || typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(kpiScopeStorageKey(id));
    return raw && VALID_KPI_SCOPES.has(raw) ? raw : null;
  } catch {
    return null;
  }
}

/** 记住该员工的 KPI scope;无身份(id<=0)或值非法时静默跳过(不写共享键,防串号)。 */
export function saveKpiScopeForStaff(staffId: unknown, scope: string): void {
  const id = Number(staffId) || 0;
  if (id <= 0 || !VALID_KPI_SCOPES.has(scope) || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(kpiScopeStorageKey(id), scope);
  } catch {
    // localStorage 不可用(隐私模式等)→ 静默降级为不记忆,不阻断 UI。
  }
}
