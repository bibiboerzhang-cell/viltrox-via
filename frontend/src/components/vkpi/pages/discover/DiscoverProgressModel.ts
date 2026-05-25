export type SearchStepStatus = 'pending' | 'active' | 'done' | 'error';

export interface SearchProgressStep {
  key: string;
  label: string;
  detail: string;
  status: SearchStepStatus;
}

export interface SearchProgressState {
  visible: boolean;
  title: string;
  percent: number;
  steps: SearchProgressStep[];
}

const searchStepDefinitions = [
  { key: 'candidate', label: '推荐方向', detail: '先确认搜索意图' },
  { key: 'source', label: '数据源', detail: '平台搜索 / 已有档案' },
  { key: 'profile', label: '账号资料', detail: '头像、粉丝、链接' },
  { key: 'posts', label: '最近内容', detail: '样本内容或 posts' },
  { key: 'decision', label: '可分析', detail: '建档 / 抓取 / 产品适配' },
];

export const idleSearchProgress: SearchProgressState = {
  visible: false,
  title: '',
  percent: 0,
  steps: searchStepDefinitions.map((step) => ({ ...step, status: 'pending' })),
};

export function searchProgressState(
  title: string,
  percent: number,
  activeKey: string,
  doneKeys: string[] = [],
  errorKey = '',
): SearchProgressState {
  const done = new Set(doneKeys);
  return {
    visible: true,
    title,
    percent: Math.max(0, Math.min(100, Math.round(percent))),
    steps: searchStepDefinitions.map((step) => ({
      ...step,
      status: errorKey === step.key ? 'error' : done.has(step.key) ? 'done' : activeKey === step.key ? 'active' : 'pending',
    })),
  };
}
