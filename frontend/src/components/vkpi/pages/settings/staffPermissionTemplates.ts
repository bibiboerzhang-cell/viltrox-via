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
