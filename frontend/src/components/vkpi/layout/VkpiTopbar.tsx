import { DataStatusBadge } from '../shared/DataStatusBadge';
import { Icon } from '../shared/Icon';
import type { VkpiDataStatus, VkpiRangeKey } from '../vkpiTypes';
import { useT } from '../cockpit/lib/i18n';
import { rangeOptions } from './vkpiLayoutConstants';
import { ThemeSwitch } from '../../../shared/ThemeSwitch';

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
  weeklyReportStatus?: {
    state: 'idle' | 'loading' | 'success' | 'error';
    message: string;
    href?: string;
  } | null;
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
  weeklyReportStatus,
}: VkpiTopbarProps) {
  const { t } = useT();
  return (
    <header className="vkpi-topbar">
      <label className="vkpi-search">
        <Icon name="search" />
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder={t('搜索红人 / 项目 / 短链 / 消息')}
          aria-label={t('搜索红人 项目 短链 消息')}
        />
        <kbd>⌘ K</kbd>
      </label>

      <label className="vkpi-date-control">
        <span>{t('数据范围')}</span>
        <select
          value={range}
          onChange={(event) => onRangeChange?.(event.target.value as VkpiRangeKey)}
          aria-label={t('选择数据范围')}
          disabled={!onRangeChange}
        >
          {rangeOptions.map((option) => (
            <option key={option.key} value={option.key}>{t(option.label)}</option>
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
          {t(viewMode === 'manager' ? '切换我的视角' : '返回管理主控')}
        </button>
      ) : null}

      <div className="vkpi-topbar__actions">
        <ThemeSwitch />
        <button
          className="vkpi-button"
          type="button"
          onClick={onExportPDF}
          disabled={!onExportPDF}
          title={t(onExportPDF ? '导出当前报表 PDF' : '当前页面未接入 PDF 导出')}
        >
          <Icon name="file" />
          {t('导出 PDF')}
        </button>
        <button
          className="vkpi-button"
          type="button"
          onClick={onExportCSV}
          disabled={!onExportCSV}
          title={t(onExportCSV ? '导出当前数据 CSV' : '当前页面未接入 CSV 导出')}
        >
          <Icon name="table" />
          {t('导出 CSV')}
        </button>
        <button
          className="vkpi-button vkpi-button--primary"
          type="button"
          onClick={onGenerateWeeklyReport}
          disabled={!onGenerateWeeklyReport}
          title={t(onGenerateWeeklyReport ? '生成真实周报' : '当前页面未接入周报生成')}
        >
          <Icon name="spark" />
          {t('生成周报')}
        </button>
        {weeklyReportStatus?.message ? (
          <span
            className={`vkpi-topbar__action-status is-${weeklyReportStatus.state}`}
            style={{
              alignItems: 'center',
              border: weeklyReportStatus.state === 'error' ? '1px solid #fecaca' : '1px solid #bbf7d0',
              borderRadius: 999,
              color: weeklyReportStatus.state === 'error' ? '#b91c1c' : '#047857',
              display: 'inline-flex',
              fontSize: 12,
              fontWeight: 800,
              gap: 8,
              maxWidth: 280,
              minHeight: 36,
              padding: '0 12px',
              whiteSpace: 'nowrap',
            }}
          >
            {t(weeklyReportStatus.message)}
            {weeklyReportStatus.href ? (
              <a
                href={weeklyReportStatus.href}
                target="_blank"
                rel="noreferrer"
                style={{ color: 'inherit', textDecoration: 'underline' }}
              >
                {t('打开')}
              </a>
            ) : null}
          </span>
        ) : null}
      </div>
    </header>
  );
}
