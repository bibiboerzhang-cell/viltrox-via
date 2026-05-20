import type { VkpiPageKey, VkpiRangeKey } from '../vkpiTypes';

export interface VkpiNavItem {
  key: VkpiPageKey;
  label: string;
  icon: string;
}

export const rangeOptions: Array<{ key: VkpiRangeKey; label: string }> = [
  { key: 'today', label: '今天' },
  { key: '7d', label: '近 7 天' },
  { key: '30d', label: '近 30 天' },
  { key: 'mtd', label: '本月至今' },
  { key: 'qtd', label: '本季度至今' },
];

export const MANAGER_NAV_ITEMS: VkpiNavItem[] = [
  { key: 'dashboardPremium', label: '决策驾驶舱', icon: 'spark' },
  { key: 'command', label: '管理主控', icon: 'grid' },
  { key: 'channels', label: 'KOL/账号管理', icon: 'grid' },
  { key: 'discover', label: '红人搜索', icon: 'discover' },
  { key: 'projects', label: '项目跟进', icon: 'folder' },
  { key: 'links', label: '短链中心', icon: 'link' },
  { key: 'attribution', label: '销售归因', icon: 'nodes' },
  { key: 'costs', label: '成本台', icon: 'table' },
  { key: 'productBattle', label: '产品作战', icon: 'spark' },
  { key: 'dataAnalysis', label: '数据分析', icon: 'analytics' },
  { key: 'campaigns', label: '活动预算', icon: 'table' },
  { key: 'dataQuality', label: '数据质量', icon: 'report' },
  { key: 'audit', label: '审计', icon: 'table' },
  { key: 'reports', label: '报表导出', icon: 'report' },
  { key: 'settings', label: '系统设置', icon: 'settings' },
];

export const EMPLOYEE_NAV_ITEMS: VkpiNavItem[] = [
  { key: 'dashboardPremium', label: '我的驾驶舱', icon: 'spark' },
  { key: 'command', label: '我的工作台', icon: 'grid' },
  { key: 'channels', label: '我的平台', icon: 'grid' },
  { key: 'discover', label: '搜索红人', icon: 'discover' },
  { key: 'projects', label: '我的项目', icon: 'folder' },
  { key: 'links', label: '我的短链', icon: 'link' },
  { key: 'attribution', label: '我的归因', icon: 'nodes' },
  { key: 'productBattle', label: '产品作战', icon: 'spark' },
  { key: 'dataAnalysis', label: '数据分析', icon: 'analytics' },
  { key: 'reports', label: '我的周报', icon: 'report' },
  { key: 'settings', label: '个人设置', icon: 'settings' },
];
