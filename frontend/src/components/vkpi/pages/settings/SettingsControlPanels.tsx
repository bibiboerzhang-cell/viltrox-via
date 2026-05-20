import React from "react";

type Row = Record<string, unknown>;

export function FeatureFlagsPanel({
  featureFlags,
  busy,
  apiToken,
  rowEnabled,
  onRunMorningSync,
  onToggleFeatureFlag,
}: {
  featureFlags: Row[];
  busy: boolean;
  apiToken?: string;
  rowEnabled: (row: Row, key?: string) => boolean;
  onRunMorningSync: () => void;
  onToggleFeatureFlag: (row: Row) => void;
}) {
  return (
    <section className="vkpi-card vkpi-table-card vkpi-action-card--wide vkpi-settings-switch-panel">
      <div className="vkpi-table-card__header">
        <div><h2>系统功能开关</h2><span>{featureFlags.length} 个开关</span></div>
        <button className="vkpi-button" type="button" disabled={busy || !apiToken} onClick={onRunMorningSync}>手动执行 08:00 同步</button>
      </div>
      {featureFlags.length ? (
        <div className="vkpi-settings-card-grid">
          {featureFlags.map((row) => {
            const enabled = rowEnabled(row);
            return (
              <article className={`vkpi-settings-toggle-card ${enabled ? "is-on" : "is-off"}`} key={String(row.flag_key)}>
                <header>
                  <strong>{String(row.flag_key || "-")}</strong>
                  <span>{enabled ? "开启" : "关闭"}</span>
                </header>
                <p>{String(row.description || "无说明")}</p>
                <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => onToggleFeatureFlag(row)}>{enabled ? "关闭" : "开启"}</button>
              </article>
            );
          })}
        </div>
      ) : <div className="vkpi-empty-panel">暂无功能开关记录。后端未初始化时不会默认开启高成本能力。</div>}
    </section>
  );
}

export function PlatformCrawlPanel({
  platformCrawl,
  busy,
  rowEnabled,
  onTogglePlatformCrawl,
}: {
  platformCrawl: Row[];
  busy: boolean;
  rowEnabled: (row: Row, key?: string) => boolean;
  onTogglePlatformCrawl: (row: Row) => void;
}) {
  const sortedPlatforms = React.useMemo(
    () => [...platformCrawl].sort((a, b) => String(a.platform || "").localeCompare(String(b.platform || ""))),
    [platformCrawl],
  );
  const enabledCount = platformCrawl.filter((row) => rowEnabled(row, "crawl_enabled")).length;
  const readyCount = enabledCount;
  const blockedCount = Math.max(platformCrawl.length - enabledCount, 0);

  return (
    <section className="vkpi-card vkpi-table-card vkpi-action-card--wide vkpi-settings-switch-panel">
      <div className="vkpi-table-card__header vkpi-platform-crawl-header">
        <div>
          <h2>平台抓取</h2>
          <span>{platformCrawl.length} 个平台 · {enabledCount} 个已开启</span>
        </div>
      </div>
      {platformCrawl.length ? (
        <div className="vkpi-platform-switches">
          <div className="vkpi-platform-crawl-kpis">
            <div><span>已开启</span><strong>{enabledCount}</strong></div>
            <div><span>可抓取</span><strong>{readyCount}</strong></div>
            <div><span>关闭</span><strong>{blockedCount}</strong></div>
          </div>
          <div className="vkpi-platform-switch-grid">
            {sortedPlatforms.map((row) => {
              const platform = String(row.platform || "-");
              const enabled = rowEnabled(row, "crawl_enabled");
              return (
                <article className={`vkpi-platform-switch-card ${enabled ? "is-on" : "is-off"}`} key={platform}>
                  <strong>{platform}</strong>
                  <button
                    className={`vkpi-crawl-primary-toggle ${enabled ? "is-on" : "is-off"}`}
                    type="button"
                    disabled={busy}
                    onClick={() => onTogglePlatformCrawl(row)}
                  >
                    {enabled ? "开启" : "关闭"}
                  </button>
                </article>
              );
            })}
          </div>
        </div>
      ) : <div className="vkpi-empty-panel">暂无平台抓取设置。默认不抓取、不烧钱。</div>}
    </section>
  );
}

export function BudgetSettingsTable({
  budgetSettings,
  busy,
  rowEnabled,
  onSaveBudgetSetting,
}: {
  budgetSettings: Row[];
  busy: boolean;
  rowEnabled: (row: Row, key?: string) => boolean;
  onSaveBudgetSetting: (event: React.FormEvent<HTMLFormElement>, row: Row) => void;
}) {
  return (
    <section className="vkpi-card vkpi-table-card">
      <div className="vkpi-table-card__header">
        <div><h2>预算控制</h2><span>{budgetSettings.length} 项</span></div>
        <span className="vkpi-settings-hint">抓取要真正运行，相关预算必须启用且月预算大于本月已用。</span>
      </div>
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead><tr><th>预算项</th><th>启用</th><th>月预算 USD</th><th>本月已用</th><th>告警阈值</th><th>操作</th></tr></thead>
          <tbody>
            {budgetSettings.length ? budgetSettings.map((row) => (
              <tr key={String(row.budget_key)}>
                <td>{String(row.budget_key || "-")}</td>
                <td>
                  <form id={`vkpi-budget-${String(row.budget_key)}`} className="vkpi-table-control-form" onSubmit={(event) => onSaveBudgetSetting(event, row)}>
                    <label className="vkpi-checkbox"><input type="checkbox" name="enabled" defaultChecked={rowEnabled(row)} /> 启用</label>
                  </form>
                </td>
                <td><input className="vkpi-table-input" form={`vkpi-budget-${String(row.budget_key)}`} name="monthly_limit_usd" defaultValue={String(row.monthly_limit_usd ?? 0)} inputMode="decimal" /></td>
                <td>${String(row.current_month_spent ?? 0)}</td>
                <td><input className="vkpi-table-input" form={`vkpi-budget-${String(row.budget_key)}`} name="alert_threshold_pct" defaultValue={String(row.alert_threshold_pct ?? 80)} inputMode="numeric" /></td>
                <td><button className="vkpi-mini-button" form={`vkpi-budget-${String(row.budget_key)}`} type="submit" disabled={busy}>保存</button></td>
              </tr>
            )) : <tr><td className="vkpi-table-empty" colSpan={6}>暂无预算设置。建议先保留默认关闭，按平台逐步开启。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function CommentAlertThresholdCard({
  settings,
  busy,
  onSave,
}: {
  settings: Row;
  busy: boolean;
  onSave: (event: React.FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <section className="vkpi-card vkpi-action-card">
      <div className="vkpi-table-card__header">
        <div>
          <h2>评论风险告警</h2>
          <span>Sentiment → Alert 阈值</span>
        </div>
      </div>
      <form className="vkpi-form-stack" onSubmit={onSave}>
        <label className="vkpi-checkbox">
          <input type="checkbox" name="enabled" defaultChecked={settings.enabled !== false} /> 开启评论风险告警
        </label>
        <input name="window_days" defaultValue={String(settings.window_days ?? 7)} placeholder="观察窗口天数" inputMode="numeric" />
        <input name="min_negative" defaultValue={String(settings.min_negative ?? 3)} placeholder="负面评论阈值" inputMode="numeric" />
        <input name="min_critical" defaultValue={String(settings.min_critical ?? 2)} placeholder="Critical 阈值" inputMode="numeric" />
        <input name="min_hostile" defaultValue={String(settings.min_hostile ?? 1)} placeholder="Hostile 阈值" inputMode="numeric" />
        <p className="vkpi-settings-hint">达到任一阈值就生成提醒；Hostile 命中会标记为高风险。</p>
        <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy}>保存评论告警阈值</button>
      </form>
    </section>
  );
}
