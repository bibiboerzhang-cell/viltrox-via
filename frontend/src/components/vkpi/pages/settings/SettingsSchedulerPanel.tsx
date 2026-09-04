import React from "react";

type Row = Record<string, unknown>;

const RISK_LABEL: Record<string, string> = { low: "低", medium: "中", high: "高" };

function boolVal(value: unknown): boolean {
  return value === true || value === "true" || value === 1 || value === "1";
}

function timeLabel(value: unknown): string {
  const raw = String(value ?? "").trim();
  if (!raw) return "—";
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return raw;
  return dt.toLocaleString("zh-CN", { hour12: false });
}

export function SettingsSchedulerPanel({
  tasks,
  status,
  busy,
  onToggleTask,
}: {
  tasks: Row[];
  status: Row;
  busy: boolean;
  onToggleTask: (taskKey: string, enabled: boolean) => void;
}) {
  const byRisk = (status.by_risk as Row | undefined) || {};
  const total = Number(status.total ?? tasks.length) || 0;
  const enabledCount = Number(status.enabled ?? 0) || 0;

  return (
    <section className="vkpi-card vkpi-table-card vkpi-action-card--wide">
      <div className="vkpi-table-card__header">
        <div>
          <h2>定时任务 / Scheduler</h2>
          <span>
            {total} 个任务 · {enabledCount} 已开启 · 低 {Number(byRisk.low ?? 0)} / 中 {Number(byRisk.medium ?? 0)} / 高 {Number(byRisk.high ?? 0)}
          </span>
        </div>
      </div>
      <p className="vkpi-settings-hint">
        切换本身不会立即运行任务；已经接线的任务会在后续调度窗口执行。请先核对风险、预算和付费提示，再开启外部抓取、LLM 或批量任务。
      </p>
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead>
            <tr>
              <th>任务</th>
              <th>风险</th>
              <th>启用</th>
              <th>最近运行</th>
              <th>最近错误</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {tasks.length ? (
              tasks.map((row) => {
                const taskKey = String(row.task_key || "");
                const enabled = boolVal(row.enabled);
                const risk = String(row.risk_level || "low");
                const lastError = String(row.last_error || "").trim();
                const paidExecution = row.paid_execution === true;
                const enableWarning = String(row.enable_warning || "").trim();
                return (
                  <tr key={taskKey}>
                    <td>
                      <strong>{taskKey || "-"}</strong>
                      {row.label ? <div className="vkpi-settings-hint">{String(row.label)}</div> : null}
                      {paidExecution ? <div className="vkpi-table-warn">付费执行 · 开启前需确认</div> : null}
                      {enableWarning ? <div className="vkpi-settings-hint">{enableWarning}</div> : null}
                    </td>
                    <td>
                      <span className={`vkpi-pill vkpi-pill--risk-${risk}`}>{RISK_LABEL[risk] || risk}</span>
                    </td>
                    <td>{enabled ? "开启" : "关闭"}</td>
                    <td>{timeLabel(row.last_run_at)}</td>
                    <td>{lastError ? <span className="vkpi-table-warn">{lastError}</span> : "—"}</td>
                    <td>
                      <button
                        className={`vkpi-mini-button ${enabled ? "is-on" : ""}`}
                        type="button"
                        disabled={busy || !taskKey}
                        onClick={() => onToggleTask(taskKey, !enabled)}
                      >
                        {enabled ? "关闭" : "开启"}
                      </button>
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td className="vkpi-table-empty" colSpan={6}>
                  暂无调度任务注册记录。迁移应用后任务默认关闭；人工启用已接线任务后，它会在后续调度窗口执行。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
