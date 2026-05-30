export const PROJECT_STAGE_FLOW = [
  { key: 'discovery', label: '发现' },
  { key: 'contacted', label: '已联系' },
  { key: 'replied', label: '已回复' },
  { key: 'agreed', label: '已合作' },
  { key: 'shipped', label: '已发货' },
  { key: 'received', label: '已到货' },
  { key: 'published', label: '已发布' },
  { key: 'measured', label: '自动化' },
  { key: 'closed', label: '已关闭' },
] as const;

export const PROJECT_STAGE_COLOR: Record<string, string> = {
  discovery: '#94a3b8',
  contacted: '#06b6d4',
  replied: '#06b6d4',
  agreed: '#a855f7',
  shipped: '#a855f7',
  received: '#a855f7',
  published: '#10b981',
  measured: '#10b981',
  closed: '#64748b',
};

export const PROJECT_STATUS_COLOR: Record<string, string> = {
  规划中: '#06b6d4',
  进行中: '#a855f7',
  收尾中: '#10b981',
  已结束: '#64748b',
  已取消: '#94a3b8',
};

export function healthColor(score: number) {
  if (score >= 85) return '#10b981';
  if (score >= 70) return '#fbbf24';
  return '#ef4444';
}

export function healthBg(score: number) {
  if (score >= 85) return 'rgba(16,185,129,0.12)';
  if (score >= 70) return 'rgba(251,191,36,0.12)';
  return 'rgba(239,68,68,0.12)';
}

export function statusBg(status: string) {
  return `${PROJECT_STATUS_COLOR[status] || '#64748b'}1f`;
}

export function formatLargeNum(value: number | null | undefined) {
  if (!value && value !== 0) return '—';
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}K`;
  return String(value);
}

export function formatMoneyShort(value: number | null | undefined) {
  if (!value && value !== 0) return '—';
  if (value === 0) return '$0';
  if (value >= 1000000) return `$${(value / 1000000).toFixed(2)}M`;
  if (value >= 1000) return `$${(value / 1000).toFixed(1)}K`;
  return `$${value}`;
}

export function ownerColor(initial: string) {
  if (initial === 'J') return '#a855f7';
  if (initial === 'M') return '#ec4899';
  if (initial === 'T') return '#f59e0b';
  return '#06b6d4';
}
