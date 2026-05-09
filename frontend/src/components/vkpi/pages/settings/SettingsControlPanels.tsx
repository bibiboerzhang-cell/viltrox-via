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
  platformBlockedReason,
  onSavePlatformCrawl,
  onTogglePlatformCrawl,
}: {
  platformCrawl: Row[];
  busy: boolean;
  rowEnabled: (row: Row, key?: string) => boolean;
  platformBlockedReason: (row: Row) => string;
  onSavePlatformCrawl: (event: React.FormEvent<HTMLFormElement>, row: Row) => void;
  onTogglePlatformCrawl: (row: Row) => void;
}) {
  return (
    <section className="vkpi-card vkpi-table-card vkpi-action-card--wide vkpi-settings-switch-panel">
      <div className="vkpi-table-card__header"><div><h2>平台抓取开关</h2><span>{platformCrawl.length} 个平台</span></div></div>
      {platformCrawl.length ? (
        <div className="vkpi-settings-card-grid vkpi-settings-card-grid--platforms">
          {platformCrawl.map((row) => {
            const enabled = rowEnabled(row, "crawl_enabled");
            return (
              <article className={`vkpi-settings-toggle-card ${enabled ? "is-on" : "is-off"}`} key={String(row.platform)}>
                <header>
                  <strong>{String(row.platform || "-")}</strong>
                  <span>{enabled ? "抓取开启" : "抓取关闭"}</span>
                </header>
                <div className="vkpi-settings-meta-grid">
                  <small>每日账号<b>{String(row.daily_account_limit ?? 0)}</b></small>
                  <small>每账号内容<b>{String(row.posts_per_account ?? 0)}</b></small>
                  <small>月预算<b>${String(row.monthly_budget_usd ?? 0)}</b></small>
                </div>
                <p className={enabled ? "vkpi-settings-hint" : "vkpi-settings-hint is-muted"}>{platformBlockedReason(row)}</p>
                <form className="vkpi-settings-control-form" onSubmit={(event) => onSavePlatformCrawl(event, row)}>
                  <div className="vkpi-settings-inline-fields">
                    <label>每日账号<input name="daily_account_limit" defaultValue={String(row.daily_account_limit ?? 0)} inputMode="numeric" /></label>
                    <label>每账号内容<input name="posts_per_account" defaultValue={String(row.posts_per_account ?? 0)} inputMode="numeric" /></label>
                    <label>月预算 USD<input name="monthly_budget_usd" defaultValue={String(row.monthly_budget_usd ?? 0)} inputMode="decimal" /></label>
                    <label>失败阈值<input name="failure_threshold" defaultValue={String(row.failure_threshold ?? 5)} inputMode="numeric" /></label>
                  </div>
                  <div className="vkpi-settings-check-row">
                    <label><input type="checkbox" name="crawl_comments" defaultChecked={rowEnabled(row, "crawl_comments")} /> 评论</label>
                    <label><input type="checkbox" name="crawl_followers" defaultChecked={rowEnabled(row, "crawl_followers")} /> 粉丝</label>
                    <label><input type="checkbox" name="crawl_audience_graph" defaultChecked={rowEnabled(row, "crawl_audience_graph")} /> 图谱</label>
                    <label><input type="checkbox" name="only_uncontacted_kols" defaultChecked={rowEnabled(row, "only_uncontacted_kols")} /> 只推未联系</label>
                    <label><input type="checkbox" name="include_company_accounts" defaultChecked={rowEnabled(row, "include_company_accounts")} /> 公司账号</label>
                    <label><input type="checkbox" name="include_competitor_accounts" defaultChecked={rowEnabled(row, "include_competitor_accounts")} /> 竞品账号</label>
                    <label><input type="checkbox" name="include_candidate_kols" defaultChecked={rowEnabled(row, "include_candidate_kols")} /> 候选红人</label>
                  </div>
                  <button className="vkpi-mini-button" type="submit" disabled={busy}>保存限制</button>
                </form>
                <button className="vkpi-mini-button" type="button" disabled={busy} onClick={() => onTogglePlatformCrawl(row)}>{enabled ? "关闭抓取" : "开启抓取"}</button>
              </article>
            );
          })}
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
