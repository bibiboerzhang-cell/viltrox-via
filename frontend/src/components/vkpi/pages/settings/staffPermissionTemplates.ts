import type { VkpiPermissionLevel } from "../../../../domains/settings";

export type StaffPermissionMap = Record<string, VkpiPermissionLevel>;
export type StaffPermissionTemplate = {
  key: string;
  label: string;
  detail: string;
  permissions: StaffPermissionMap;
  ownerOnly?: boolean;
};

// C7「两态·默认显示」业务模块(与后端 permissions.py DEFAULT_VISIBLE_KEYS 对齐):
// 这 7 个对非 owner 默认「显示」+ 两态(显示/可使用);其余(系统/用量/运行诊断 + 4 个敏感)
// 是 owner 按需授权,三态(无/显示/可使用)、默认无。改这里务必同步改后端那份。
export const DEFAULT_VISIBLE_MODULE_KEYS = new Set<string>([
  "overview",
  "kol_ops",
  "vkpi",
  "activities",
  "analytics",
  "insights",
  "products",
]);

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

// 2026-07-11 授权页 V1:注册表对齐 navItems.ts 的 17 个主导航项(非 ops / 非 v2),
// 让「板块可见选择器」勾的 chips 与侧栏 canViewBoard 过滤的板块一一对应。
// 旧 15 项里已归 V2 折叠组的 navKey(campaigns/intelligence/attribution/analytics/
// reports/signals/agents/p15)从注册表下线;成员历史存过的 board.<v2Key> 值仍留在
// permissions_json 里(boardLevelFor 默认 read,不影响任何人)。
export const BOARD_PERMISSION_MODULES: BoardPermissionModule[] = [
  { navKey: "dashboard",       label: "Dashboard",        group: "板块 · 总览" },
  { navKey: "my-kol",          label: "MY KOL",           group: "板块 · 达人运营" },
  { navKey: "kol-pool",        label: "KOL Pool",         group: "板块 · 达人运营" },
  { navKey: "kolProfile",      label: "KOL 档案",          group: "板块 · 达人运营" },
  { navKey: "projects",        label: "Projects",         group: "板块 · 增长渠道" },
  { navKey: "events",          label: "Events",           group: "板块 · 增长渠道" },
  { navKey: "shopify",         label: "Shopify",          group: "板块 · 增长渠道" },
  { navKey: "dealers",         label: "Dealers",          group: "板块 · 增长渠道" },
  { navKey: "intelligent",     label: "Intelligent 问答",  group: "板块 · 智能中枢" },
  { navKey: "marketVoice",     label: "市场之声",          group: "板块 · 智能中枢" },
  { navKey: "sku360",          label: "SKU 360°",         group: "板块 · 智能中枢" },
  { navKey: "creativeLibrary", label: "创意资产库",        group: "板块 · 智能中枢" },
  { navKey: "replyQueue",      label: "回复队列",          group: "板块 · 自动化" },
  { navKey: "launchpad",       label: "发射台",            group: "板块 · 自动化" },
  { navKey: "autonomy",        label: "自治驾照",          group: "板块 · 自动化" },
  { navKey: "strategyBoard",   label: "战略台",            group: "板块 · 自动化" },
  { navKey: "gtmCommand",      label: "GTM Command",      group: "板块 · 自动化" },
].map((b) => ({ ...b, key: `${BOARD_PERMISSION_KEY_PREFIX}${b.navKey}` }));

export const BOARD_NAV_KEYS = BOARD_PERMISSION_MODULES.map((m) => m.navKey);

// 读某员工对某板块的级别:未显式设置 → 默认可见(read)。
export function boardLevelFor(permissions: Record<string, unknown> | null | undefined, navKey: string): VkpiPermissionLevel {
  const raw = permissions?.[`${BOARD_PERMISSION_KEY_PREFIX}${navKey}`];
  const next = String(raw || "").toLowerCase();
  if (next === "admin" || next === "write" || next === "read" || next === "none") return next as VkpiPermissionLevel;
  return BOARD_PERMISSION_DEFAULT_LEVEL;
}

// ── 成员状态统一口径(2026-07-11 授权页 V1)──────────────────────────────
// 此前三处各写一份:Drawer.statusLabel(邀请过期)/ StaffTable.statusLabel(已过期,
// 且 pending 优先级不同)/ 卡片区自拼 —— 同一个人在不同视图显示不同状态。
// 现以原 Drawer 口径为唯一真源:停用 > 已验证 > 已激活 > 待激活 > 邀请过期 > 启用,
// 「在线」是独立的 presence 维度(绿点),不混进账号状态。
export type StaffStatusSource = {
  active: boolean;
  verificationStatus?: string;
};

export function staffStatusLabel(member: StaffStatusSource): string {
  if (!member.active) return "停用";
  if (member.verificationStatus === "verified") return "已验证";
  if (member.verificationStatus === "activated") return "已激活";
  if (member.verificationStatus === "pending") return "待激活";
  if (member.verificationStatus === "expired") return "邀请过期";
  return "启用";
}

// 待激活徽:pending/expired 都还没能登录,关系视图卡上黄徽提示。
export function staffPendingActivation(member: StaffStatusSource): boolean {
  return member.active && (member.verificationStatus === "pending" || member.verificationStatus === "expired");
}

export const STAFF_PERMISSION_TEMPLATES: StaffPermissionTemplate[] = [
  {
    key: "employee_workspace",
    label: "成员工作台",
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
