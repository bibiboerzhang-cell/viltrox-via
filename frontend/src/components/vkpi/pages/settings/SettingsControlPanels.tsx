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
  const [selectedPlatform, setSelectedPlatform] = React.useState<string>("");
  const [advancedOpen, setAdvancedOpen] = React.useState(false);
  const sortedPlatforms = React.useMemo(
    () => [...platformCrawl].sort((a, b) => String(a.platform || "").localeCompare(String(b.platform || ""))),
    [platformCrawl],
  );
  const selectedRow = React.useMemo(() => {
    if (!sortedPlatforms.length) return undefined;
    return sortedPlatforms.find((row) => String(row.platform || "") === selectedPlatform)
      || sortedPlatforms.find((row) => String(row.platform || "").toLowerCase() === "instagram")
      || sortedPlatforms[0];
  }, [selectedPlatform, sortedPlatforms]);
  React.useEffect(() => {
    if (!selectedRow) return;
    const key = String(selectedRow.platform || "");
    if (key && key !== selectedPlatform) setSelectedPlatform(key);
  }, [selectedPlatform, selectedRow]);
  const enabledCount = platformCrawl.filter((row) => rowEnabled(row, "crawl_enabled")).length;
  const readyCount = enabledCount;
  const blockedCount = Math.max(platformCrawl.length - enabledCount, 0);

  const numberText = (value: unknown) => String(value ?? 0);
  const selectedEnabled = selectedRow ? rowEnabled(selectedRow, "crawl_enabled") : false;
  const selectedReason = selectedRow ? platformBlockedReason(selectedRow) : "";
  const selectedReady = selectedEnabled;

  return (
    <section className="vkpi-card vkpi-table-card vkpi-action-card--wide vkpi-settings-switch-panel">
      <div className="vkpi-table-card__header vkpi-platform-crawl-header">
        <div>
          <h2>平台抓取</h2>
          <span>{platformCrawl.length} 个平台 · {enabledCount} 个已开启 · API 默认配置</span>
        </div>
        {selectedRow ? (
          <button
            className={`vkpi-crawl-primary-toggle ${selectedEnabled ? "is-on" : "is-off"}`}
            type="button"
            disabled={busy}
            onClick={() => onTogglePlatformCrawl(selectedRow)}
          >
            {selectedEnabled ? "开启" : "关闭"}
          </button>
        ) : null}
      </div>
      {platformCrawl.length ? (
        <div className="vkpi-platform-crawl-console">
          <aside className="vkpi-platform-crawl-list" aria-label="平台抓取列表">
            <div className="vkpi-platform-crawl-kpis">
              <div><span>已开启</span><strong>{enabledCount}</strong></div>
              <div><span>可抓取</span><strong>{readyCount}</strong></div>
              <div><span>阻塞</span><strong>{blockedCount}</strong></div>
            </div>
            <div className="vkpi-platform-crawl-list__rows">
              {sortedPlatforms.map((row) => {
                const platform = String(row.platform || "-");
                const enabled = rowEnabled(row, "crawl_enabled");
                const reason = platformBlockedReason(row);
                const ready = enabled;
                const selected = String(selectedRow?.platform || "") === platform;
                return (
                  <button
                    className={`vkpi-platform-crawl-row ${selected ? "is-selected" : ""} ${enabled ? "is-on" : "is-off"}`}
                    key={platform}
                    type="button"
                    onClick={() => setSelectedPlatform(platform)}
                  >
                    <span className="vkpi-platform-crawl-row__name">{platform}</span>
                    <span className={`vkpi-platform-crawl-row__status ${ready ? "is-ok" : "is-blocked"}`}>{enabled ? "开启" : "关闭"}</span>
                    <small>API 已配置 · {numberText(row.daily_account_limit)} 账号 / {numberText(row.posts_per_account)} 内容 / ${numberText(row.monthly_budget_usd)}</small>
                  </button>
                );
              })}
            </div>
          </aside>
          {selectedRow ? (
            <article className="vkpi-platform-crawl-detail" key={String(selectedRow.platform)}>
              <header className="vkpi-platform-crawl-detail__header">
                <div>
                  <span>当前平台</span>
                  <h3>{String(selectedRow.platform || "-")}</h3>
                </div>
                <div className="vkpi-platform-crawl-detail__actions">
                  <span className={`vkpi-platform-crawl-ready ${selectedReady ? "is-ok" : "is-blocked"}`}>
                    {selectedEnabled ? "已开启" : "已关闭"}
                  </span>
                  <button
                    className={`vkpi-crawl-primary-toggle ${selectedEnabled ? "is-on" : "is-off"}`}
                    type="button"
                    disabled={busy}
                    onClick={() => onTogglePlatformCrawl(selectedRow)}
                  >
                    {selectedEnabled ? "开启" : "关闭"}
                  </button>
                </div>
              </header>
              <p className={`vkpi-platform-crawl-reason ${selectedReady ? "is-ok" : "is-blocked"}`}>{selectedReason}</p>
              <form className="vkpi-settings-control-form vkpi-platform-crawl-form" onSubmit={(event) => onSavePlatformCrawl(event, selectedRow)}>
                <div className="vkpi-settings-inline-fields vkpi-settings-inline-fields--primary">
                  <label>每日账号<input name="daily_account_limit" defaultValue={String(selectedRow.daily_account_limit ?? 0)} inputMode="numeric" /></label>
                  <label>每账号内容<input name="posts_per_account" defaultValue={String(selectedRow.posts_per_account ?? 0)} inputMode="numeric" /></label>
                  <label>月预算 USD<input name="monthly_budget_usd" defaultValue={String(selectedRow.monthly_budget_usd ?? 0)} inputMode="decimal" /></label>
                  <label>失败阈值<input name="failure_threshold" defaultValue={String(selectedRow.failure_threshold ?? 5)} inputMode="numeric" /></label>
                </div>
                <button
                  className="vkpi-platform-advanced-toggle"
                  type="button"
                  onClick={() => setAdvancedOpen((value) => !value)}
                >
                  {advancedOpen ? "收起高级范围" : "展开高级范围"}
                </button>
                {advancedOpen ? (
                  <div className="vkpi-settings-check-row vkpi-settings-check-row--panel">
                    <label><input type="checkbox" name="crawl_comments" defaultChecked={rowEnabled(selectedRow, "crawl_comments")} /> 评论</label>
                    <label><input type="checkbox" name="crawl_followers" defaultChecked={rowEnabled(selectedRow, "crawl_followers")} /> 粉丝</label>
                    <label><input type="checkbox" name="crawl_audience_graph" defaultChecked={rowEnabled(selectedRow, "crawl_audience_graph")} /> 图谱</label>
                    <label><input type="checkbox" name="only_uncontacted_kols" defaultChecked={rowEnabled(selectedRow, "only_uncontacted_kols")} /> 只推未联系</label>
                    <label><input type="checkbox" name="include_company_accounts" defaultChecked={rowEnabled(selectedRow, "include_company_accounts")} /> 公司账号</label>
                    <label><input type="checkbox" name="include_competitor_accounts" defaultChecked={rowEnabled(selectedRow, "include_competitor_accounts")} /> 竞品账号</label>
                    <label><input type="checkbox" name="include_candidate_kols" defaultChecked={rowEnabled(selectedRow, "include_candidate_kols")} /> 候选红人</label>
                  </div>
                ) : null}
                <div className="vkpi-platform-crawl-detail__footer">
                  <span>日常只需要点开启/关闭；需要调整预算和抓取数量时再保存限制。</span>
                  <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy}>保存当前平台限制</button>
                </div>
              </form>
            </article>
          ) : null}
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
