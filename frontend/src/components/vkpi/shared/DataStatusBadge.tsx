import type { VkpiDashboardData } from '../vkpiTypes';

function formatSyncTime(value?: string): string {
  if (!value) return '点击刷新';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '点击刷新';
  return parsed.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

export function DataStatusBadge({
  status,
  notice,
  lastSyncedAt,
  isRefreshing,
  onRefresh,
}: {
  status: VkpiDashboardData['dataStatus'];
  notice: string;
  lastSyncedAt?: string;
  isRefreshing?: boolean;
  onRefresh?: () => void;
}) {
  const label = isRefreshing ? '刷新中' : status === 'live' ? '已同步' : status === 'partial' ? '部分同步' : '暂无数据';
  return (
    <button
      className={`vkpi-data-status is-${status} ${isRefreshing ? 'is-refreshing' : ''}`}
      type="button"
      title={notice}
      onClick={onRefresh}
      disabled={!onRefresh || isRefreshing}
    >
      <i className="vkpi-data-status__dot" />
      <span className="vkpi-data-status__text">{label}</span>
      <small>{formatSyncTime(lastSyncedAt)}</small>
    </button>
  );
}
