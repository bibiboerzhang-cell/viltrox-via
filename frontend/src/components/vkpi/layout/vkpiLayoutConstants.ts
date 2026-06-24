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
  { key: 'v615Replica', label: '管理主控', icon: 'grid' },
  { key: 'intelligenceCenter', label: '智能中心', icon: 'spark' },
  { key: 'agents', label: 'Agents 战情室', icon: 'nodes' },
  { key: 'channels', label: 'MY KOL', icon: 'heart' },
  { key: 'kolPoolV2', label: 'KOL Pool', icon: 'grid' },
  { key: 'projects', label: '项目跟进', icon: 'folder' },
  { key: 'links', label: '短链与归因', icon: 'link' },
  { key: 'productBattle', label: '产品分析', icon: 'spark' },
  { key: 'industryBoard', label: '行业大盘', icon: 'analytics' },
  { key: 'dataAnalysis', label: '数据分析', icon: 'analytics' },
  { key: 'learning', label: '学习闭环', icon: 'nodes' },
  { key: 'dataExport', label: '数据导出', icon: 'report' },
  { key: 'dataQuality', label: '数据质量', icon: 'report' },
  { key: 'audit', label: '审计', icon: 'table' },
  { key: 'reports', label: '报表导出', icon: 'report' },
  { key: 'settings', label: '系统设置', icon: 'settings' },
];

export const EMPLOYEE_NAV_ITEMS: VkpiNavItem[] = [
  { key: 'command', label: '我的工作台', icon: 'grid' },
  { key: 'channels', label: 'MY KOL', icon: 'heart' },
  { key: 'discover', label: '搜索红人', icon: 'discover' },
  { key: 'projects', label: '我的项目', icon: 'folder' },
  { key: 'links', label: '我的短链', icon: 'link' },
  { key: 'attribution', label: '我的归因', icon: 'nodes' },
  { key: 'reports', label: '我的周报', icon: 'report' },
  { key: 'settings', label: '个人设置', icon: 'settings' },
];
