import { DataStatusBadge } from '../shared/DataStatusBadge';
import { Icon } from '../shared/Icon';
import type { VkpiDataStatus, VkpiRangeKey } from '../vkpiTypes';
import { rangeOptions } from './vkpiLayoutConstants';

interface VkpiTopbarProps {
  query: string;
  range: VkpiRangeKey;
  dataStatus: VkpiDataStatus;
  dataNotice: string;
  viewMode: 'manager' | 'employee';
  lastSyncedAt?: string;
  isRefreshing?: boolean;
  canSwitchView?: boolean;
  onQueryChange: (query: string) => void;
  onRangeChange?: (range: VkpiRangeKey) => void;
  onRefreshData?: () => void;
  onToggleView?: () => void;
  onExportPDF?: () => void;
  onExportCSV?: () => void;
  onGenerateWeeklyReport?: () => void;
}

export function VkpiTopbar({
  query,
  range,
  dataStatus,
  dataNotice,
  viewMode,
  lastSyncedAt,
  isRefreshing,
  canSwitchView,
  onQueryChange,
  onRangeChange,
  onRefreshData,
  onToggleView,
  onExportPDF,
  onExportCSV,
  onGenerateWeeklyReport,
}: VkpiTopbarProps) {
  return (
    <header className="vkpi-topbar">
      <label className="vkpi-search">
        <Icon name="search" />
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="搜索红人 / 项目 / 短链 / 消息"
          aria-label="搜索红人 项目 短链 消息"
        />
        <kbd>⌘ K</kbd>
      </label>

      <label className="vkpi-date-control">
        <span>数据范围</span>
        <select
          value={range}
          onChange={(event) => onRangeChange?.(event.target.value as VkpiRangeKey)}
          aria-label="选择数据范围"
          disabled={!onRangeChange}
        >
          {rangeOptions.map((option) => (
            <option key={option.key} value={option.key}>{option.label}</option>
          ))}
        </select>
        <Icon name="calendar" />
      </label>

      <DataStatusBadge
        status={dataStatus}
        notice={dataNotice}
        lastSyncedAt={lastSyncedAt}
        isRefreshing={isRefreshing}
        onRefresh={onRefreshData}
      />

      {canSwitchView ? (
        <button className="vkpi-button" type="button" onClick={onToggleView}>
          {viewMode === 'manager' ? '切换员工视角' : '返回管理主控'}
        </button>
      ) : null}

      <div className="vkpi-topbar__actions">
        <button className="vkpi-button" type="button" onClick={onExportPDF}>
          <Icon name="file" />
          导出 PDF
        </button>
        <button className="vkpi-button" type="button" onClick={onExportCSV}>
          <Icon name="table" />
          导出 CSV
        </button>
        <button className="vkpi-button vkpi-button--primary" type="button" onClick={onGenerateWeeklyReport}>
          <Icon name="spark" />
          生成周报
        </button>
      </div>
    </header>
  );
}
