import type { VkpiPermissionLevel } from "../../../../domains/settings";

export type StaffPermissionMap = Record<string, VkpiPermissionLevel>;
export type StaffPermissionTemplate = {
  key: string;
  label: string;
  detail: string;
  permissions: StaffPermissionMap;
  ownerOnly?: boolean;
};

export const STAFF_PERMISSION_MODULES: Array<{ key: string; label: string; group: string; ownerOnly?: boolean }> = [
  { key: "overview", label: "管理主控", group: "常用" },
  { key: "kol_ops", label: "KOL/账号管理", group: "常用" },
  { key: "vkpi", label: "V-KPI 工作台", group: "常用" },
  { key: "activities", label: "项目/活动", group: "常用" },
  { key: "analytics", label: "数据分析", group: "数据" },
  { key: "insights", label: "洞察报表", group: "数据" },
  { key: "products", label: "产品作战", group: "数据" },
  { key: "runtime", label: "运行诊断", group: "系统" },
  { key: "system", label: "系统设置", group: "系统" },
  { key: "system.usage", label: "用量/预算", group: "系统" },
  { key: "system.api_keys", label: "API Key", group: "敏感", ownerOnly: true },
  { key: "system.models", label: "模型配置", group: "敏感", ownerOnly: true },
  { key: "system.members", label: "成员管理", group: "敏感", ownerOnly: true },
  { key: "system.restart", label: "服务重启", group: "敏感", ownerOnly: true },
];

// ── 导航板块授权(2026-06-15)─────────────────────────────────────────────
// 把「侧栏 15 个导航板块」做成独立权限单元,key = `board.<navKey>`(前缀避开
// 和 14 个 tab 撞名,如 analytics)。存进同一张 permissions_json(通用 map,
// normalize_permissions 的 merge 会保留,无需迁移)。
// 默认 'read'(可见):现有员工不会因为新增此层而突然看不到任何板块;owner 显式
// 设 '无' 才隐藏某板块。可见性在前端强制(侧栏过滤 + 页面守卫);数据写入仍由既有
// 14-tab 守卫保护,本层不改后端 require_tab。
export const BOARD_PERMISSION_KEY_PREFIX = "board.";
export const BOARD_PERMISSION_DEFAULT_LEVEL: VkpiPermissionLevel = "read";

export type BoardPermissionModule = { key: string; navKey: string; label: string; group: string };

export const BOARD_PERMISSION_MODULES: BoardPermissionModule[] = [
  { navKey: "dashboard",    label: "Dashboard",     group: "板块 · 核心" },
  { navKey: "my-kol",       label: "MY KOL",        group: "板块 · 核心" },
  { navKey: "kol-pool",     label: "KOL Pool",      group: "板块 · 核心" },
  { navKey: "projects",     label: "Projects",      group: "板块 · 项目" },
  { navKey: "campaigns",    label: "Campaigns",     group: "板块 · 项目" },
  { navKey: "events",       label: "Events",        group: "板块 · 项目" },
  { navKey: "intelligence", label: "Intelligence",  group: "板块 · 数据" },
  { navKey: "attribution",  label: "Attribution",   group: "板块 · 数据" },
  { navKey: "analytics",    label: "Analytics",     group: "板块 · 数据" },
  { navKey: "reports",      label: "Reports",       group: "板块 · 数据" },
  { navKey: "signals",      label: "Signals",       group: "板块 · 数据" },
  { navKey: "agents",       label: "Agents",        group: "板块 · 运营" },
  { navKey: "p15",          label: "P15 Warehouse", group: "板块 · 运营" },
  { navKey: "shopify",      label: "Shopify",       group: "板块 · 运营" },
  { navKey: "dealers",      label: "Dealers",       group: "板块 · 运营" },
].map((b) => ({ ...b, key: `${BOARD_PERMISSION_KEY_PREFIX}${b.navKey}` }));

export const BOARD_NAV_KEYS = BOARD_PERMISSION_MODULES.map((m) => m.navKey);

// 读某员工对某板块的级别:未显式设置 → 默认可见(read)。
export function boardLevelFor(permissions: Record<string, unknown> | null | undefined, navKey: string): VkpiPermissionLevel {
  const raw = permissions?.[`${BOARD_PERMISSION_KEY_PREFIX}${navKey}`];
  const next = String(raw || "").toLowerCase();
  if (next === "admin" || next === "write" || next === "read" || next === "none") return next as VkpiPermissionLevel;
  return BOARD_PERMISSION_DEFAULT_LEVEL;
}

export const STAFF_PERMISSION_TEMPLATES: StaffPermissionTemplate[] = [
  {
    key: "employee_workspace",
    label: "成成员作台",
    detail: "官方账号矩阵和个人工作台，不开放管理后台",
    permissions: {
      overview: "none", kol_ops: "read", vkpi: "write", activities: "none",
      analytics: "none", insights: "none", products: "none", runtime: "none", system: "none",
      "system.usage": "none", "system.api_keys": "none", "system.models": "none", "system.members": "none", "system.restart": "none",
    },
  },
  {
    key: "kol_outreach",
    label: "KOL 外联",
    detail: "搜索、证据、项目和外联操作",
    permissions: {
      overview: "read", kol_ops: "write", vkpi: "write", activities: "write",
      analytics: "read", insights: "read", products: "read", runtime: "none", system: "none",
      "system.usage": "none", "system.api_keys": "none", "system.models": "none", "system.members": "none", "system.restart": "none",
    },
  },
  {
    key: "content_ops",
    label: "内容运营",
    detail: "内容分析、项目执行和只读产品证据",
    permissions: {
      overview: "read", kol_ops: "read", vkpi: "write", activities: "write",
      analytics: "write", insights: "read", products: "read", runtime: "none", system: "none",
      "system.usage": "none", "system.api_keys": "none", "system.models": "none", "system.members": "none", "system.restart": "none",
    },
  },
  {
    key: "finance",
    label: "财务",
    detail: "成本、归因、预算和报表复核",
    permissions: {
      overview: "read", kol_ops: "read", vkpi: "read", activities: "read",
      analytics: "read", insights: "write", products: "read", runtime: "none", system: "none",
      "system.usage": "read", "system.api_keys": "none", "system.models": "none", "system.members": "none", "system.restart": "none",
    },
  },
  {
    key: "viewer",
    label: "只读观察",
    detail: "非敏感模块只读",
    permissions: Object.fromEntries(STAFF_PERMISSION_MODULES.map((module) => [module.key, module.ownerOnly ? "none" : "read"])) as StaffPermissionMap,
  },
  {
    key: "admin",
    label: "管理员",
    detail: "Owner 专用，普通成员授权入口不显示",
    ownerOnly: true,
    permissions: Object.fromEntries(STAFF_PERMISSION_MODULES.map((module) => [module.key, module.ownerOnly ? "read" : "admin"])) as StaffPermissionMap,
  },
];

export const STAFF_ASSIGNABLE_PERMISSION_TEMPLATES = STAFF_PERMISSION_TEMPLATES.filter((template) => !template.ownerOnly);

export function permissionsForTemplate(templateKey: string): StaffPermissionMap {
  const template = STAFF_PERMISSION_TEMPLATES.find((item) => item.key === templateKey) || STAFF_PERMISSION_TEMPLATES[0];
  return { ...template.permissions };
}

export function vkpiPermissionFromTemplate(templateKey: string): "none" | "read" | "write" {
  const level = permissionsForTemplate(templateKey).vkpi;
  if (level === "admin" || level === "write") return "write";
  if (level === "read") return "read";
  return "none";
}
