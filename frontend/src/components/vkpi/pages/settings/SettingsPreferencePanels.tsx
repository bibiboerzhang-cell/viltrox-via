import React from "react";
import { CardHeader } from "../../shared/CardHeader";

type Row = Record<string, unknown>;

interface PreferenceSettingsCardProps {
  busy: boolean;
  apiToken?: string;
  landingPage: string;
  dateRangeDefault: string;
  tableDensity: string;
  rowsPerPage: string;
  compactMode: boolean;
  rightPanelOpen: boolean;
  onLandingPageChange: (value: string) => void;
  onDateRangeDefaultChange: (value: string) => void;
  onTableDensityChange: (value: string) => void;
  onRowsPerPageChange: (value: string) => void;
  onCompactModeChange: (value: boolean) => void;
  onRightPanelOpenChange: (value: boolean) => void;
  onSubmit: React.FormEventHandler;
}

export function PreferenceSettingsCard({
  busy,
  apiToken,
  landingPage,
  dateRangeDefault,
  tableDensity,
  rowsPerPage,
  compactMode,
  rightPanelOpen,
  onLandingPageChange,
  onDateRangeDefaultChange,
  onTableDensityChange,
  onRowsPerPageChange,
  onCompactModeChange,
  onRightPanelOpenChange,
  onSubmit,
}: PreferenceSettingsCardProps) {
  return (
    <section className="vkpi-card vkpi-action-card">
      <CardHeader title="个人偏好" />
      <form className="vkpi-form-stack" onSubmit={onSubmit}>
        <label>默认入口
          <select value={landingPage} onChange={(event) => onLandingPageChange(event.target.value)}>
            <option value="dashboard">管理主控 / 工作台</option>
            <option value="discover">红人搜索</option>
            <option value="projects">项目跟进</option>
            <option value="links">短链中心</option>
            <option value="attribution">销售归因</option>
            <option value="reports">报表导出</option>
            <option value="settings">系统设置</option>
          </select>
        </label>
        <label>默认数据范围
          <select value={dateRangeDefault} onChange={(event) => onDateRangeDefaultChange(event.target.value)}>
            <option value="today">今天</option>
            <option value="7d">近 7 天</option>
            <option value="14d">近 14 天</option>
            <option value="30d">近 30 天</option>
            <option value="mtd">本月</option>
            <option value="qtd">本季度</option>
          </select>
        </label>
        <label>表格密度
          <select value={tableDensity} onChange={(event) => onTableDensityChange(event.target.value)}>
            <option value="comfortable">标准</option>
            <option value="compact">紧凑</option>
            <option value="spacious">宽松</option>
          </select>
        </label>
        <input value={rowsPerPage} onChange={(event) => onRowsPerPageChange(event.target.value)} placeholder="每页条数" inputMode="numeric" />
        <label className="vkpi-checkbox"><input type="checkbox" checked={compactMode} onChange={(event) => onCompactModeChange(event.target.checked)} /> 启用紧凑模式</label>
        <label className="vkpi-checkbox"><input type="checkbox" checked={rightPanelOpen} onChange={(event) => onRightPanelOpenChange(event.target.checked)} /> 默认打开右侧详情</label>
        <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !apiToken}>保存个人偏好</button>
      </form>
    </section>
  );
}

interface NotificationSettingsCardProps {
  busy: boolean;
  apiToken?: string;
  emailEnabled: boolean;
  inAppEnabled: boolean;
  dailyDigestEnabled: boolean;
  weeklySummaryEnabled: boolean;
  stalledProjectEnabled: boolean;
  claimActivityEnabled: boolean;
  attributionAlertEnabled: boolean;
  costAlertEnabled: boolean;
  systemAlertEnabled: boolean;
  quietHoursStart: string;
  quietHoursEnd: string;
  onEmailEnabledChange: (value: boolean) => void;
  onInAppEnabledChange: (value: boolean) => void;
  onDailyDigestEnabledChange: (value: boolean) => void;
  onWeeklySummaryEnabledChange: (value: boolean) => void;
  onStalledProjectEnabledChange: (value: boolean) => void;
  onClaimActivityEnabledChange: (value: boolean) => void;
  onAttributionAlertEnabledChange: (value: boolean) => void;
  onCostAlertEnabledChange: (value: boolean) => void;
  onSystemAlertEnabledChange: (value: boolean) => void;
  onQuietHoursStartChange: (value: string) => void;
  onQuietHoursEndChange: (value: string) => void;
  onSubmit: React.FormEventHandler;
}

export function NotificationSettingsCard({
  busy,
  apiToken,
  emailEnabled,
  inAppEnabled,
  dailyDigestEnabled,
  weeklySummaryEnabled,
  stalledProjectEnabled,
  claimActivityEnabled,
  attributionAlertEnabled,
  costAlertEnabled,
  systemAlertEnabled,
  quietHoursStart,
  quietHoursEnd,
  onEmailEnabledChange,
  onInAppEnabledChange,
  onDailyDigestEnabledChange,
  onWeeklySummaryEnabledChange,
  onStalledProjectEnabledChange,
  onClaimActivityEnabledChange,
  onAttributionAlertEnabledChange,
  onCostAlertEnabledChange,
  onSystemAlertEnabledChange,
  onQuietHoursStartChange,
  onQuietHoursEndChange,
  onSubmit,
}: NotificationSettingsCardProps) {
  return (
    <section className="vkpi-card vkpi-action-card">
      <CardHeader title="通知配置" />
      <form className="vkpi-form-stack" onSubmit={onSubmit}>
        <div className="vkpi-inline-message">当前为 v3 存储模式：只保存通知偏好，不发送邮件、Slack、微信或站内推送。</div>
        <label className="vkpi-checkbox"><input type="checkbox" checked={inAppEnabled} onChange={(event) => onInAppEnabledChange(event.target.checked)} /> 站内通知开关</label>
        <label className="vkpi-checkbox"><input type="checkbox" checked={emailEnabled} onChange={(event) => onEmailEnabledChange(event.target.checked)} /> 邮件通知开关</label>
        <label className="vkpi-checkbox"><input type="checkbox" checked={dailyDigestEnabled} onChange={(event) => onDailyDigestEnabledChange(event.target.checked)} /> 每日 08:00 摘要</label>
        <label className="vkpi-checkbox"><input type="checkbox" checked={weeklySummaryEnabled} onChange={(event) => onWeeklySummaryEnabledChange(event.target.checked)} /> 每周总结提醒</label>
        <label className="vkpi-checkbox"><input type="checkbox" checked={stalledProjectEnabled} onChange={(event) => onStalledProjectEnabledChange(event.target.checked)} /> 项目停滞提醒</label>
        <label className="vkpi-checkbox"><input type="checkbox" checked={claimActivityEnabled} onChange={(event) => onClaimActivityEnabledChange(event.target.checked)} /> 红人认领动态</label>
        <label className="vkpi-checkbox"><input type="checkbox" checked={attributionAlertEnabled} onChange={(event) => onAttributionAlertEnabledChange(event.target.checked)} /> 归因异常提醒</label>
        <label className="vkpi-checkbox"><input type="checkbox" checked={costAlertEnabled} onChange={(event) => onCostAlertEnabledChange(event.target.checked)} /> 成本异常提醒</label>
        <label className="vkpi-checkbox"><input type="checkbox" checked={systemAlertEnabled} onChange={(event) => onSystemAlertEnabledChange(event.target.checked)} /> 系统状态提醒</label>
        <div className="vkpi-form-grid">
          <input value={quietHoursStart} onChange={(event) => onQuietHoursStartChange(event.target.value)} placeholder="免打扰开始 22:00" />
          <input value={quietHoursEnd} onChange={(event) => onQuietHoursEndChange(event.target.value)} placeholder="免打扰结束 08:00" />
        </div>
        <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !apiToken}>保存通知配置</button>
      </form>
    </section>
  );
}

export function TeamPreferenceTable({ preferenceList }: { preferenceList: Row[] }) {
  return (
    <section className="vkpi-card vkpi-table-card">
      <div className="vkpi-table-card__header"><div><h2>个人偏好列表</h2><span>{preferenceList.length} 人</span></div></div>
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead><tr><th>成员 ID</th><th>默认入口</th><th>数据范围</th><th>表格密度</th><th>每页</th><th>更新时间</th></tr></thead>
          <tbody>
            {preferenceList.length ? preferenceList.map((row) => {
              const prefs = (row.preferences || {}) as Row;
              return (
                <tr key={String(row.staff_id || row.id)}>
                  <td>{String(row.staff_id || "-")}</td>
                  <td>{String(prefs.landing_page || "-")}</td>
                  <td>{String(prefs.date_range_default || "-")}</td>
                  <td>{String(prefs.table_density || "-")}</td>
                  <td>{String(prefs.rows_per_page || "-")}</td>
                  <td>{String(row.updated_at || "-")}</td>
                </tr>
              );
            }) : <tr><td className="vkpi-table-empty" colSpan={6}>暂无成员偏好记录。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function TeamNotificationTable({
  notificationList,
  boolValue,
}: {
  notificationList: Row[];
  boolValue: (value: unknown, fallback?: boolean) => boolean;
}) {
  const enabledText = (value: unknown) => (boolValue(value, false) ? "开" : "关");
  return (
    <section className="vkpi-card vkpi-table-card">
      <div className="vkpi-table-card__header"><div><h2>通知配置列表</h2><span>{notificationList.length} 人</span></div></div>
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead><tr><th>成员 ID</th><th>站内</th><th>邮件</th><th>日报</th><th>周报</th><th>异常</th><th>免打扰</th><th>状态</th></tr></thead>
          <tbody>
            {notificationList.length ? notificationList.map((row) => {
              const settings = (row.settings || {}) as Row;
              return (
                <tr key={String(row.staff_id || row.id)}>
                  <td>{String(row.staff_id || "-")}</td>
                  <td>{enabledText(settings.in_app_enabled)}</td>
                  <td>{enabledText(settings.email_enabled)}</td>
                  <td>{enabledText(settings.daily_digest_enabled)}</td>
                  <td>{enabledText(settings.weekly_summary_enabled)}</td>
                  <td>{enabledText(settings.attribution_alert_enabled)}</td>
                  <td>{String(settings.quiet_hours_start || "-")}-{String(settings.quiet_hours_end || "-")}</td>
                  <td>只存不发</td>
                </tr>
              );
            }) : <tr><td className="vkpi-table-empty" colSpan={8}>暂无通知配置记录。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
