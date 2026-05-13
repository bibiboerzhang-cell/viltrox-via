import React from "react";
import { listTeamFeedback, updateTeamFeedbackStatus } from "../../../../services/vkpi.ui-api";

type Row = Record<string, unknown>;

const STATUS_OPTIONS = [
  { value: "", label: "全部" },
  { value: "open", label: "未处理" },
  { value: "triaged", label: "已分拣" },
  { value: "in_progress", label: "处理中" },
  { value: "resolved", label: "已解决" },
  { value: "closed", label: "已关闭" },
];

const STATUS_ACTIONS = [
  { value: "triaged", label: "标记分拣" },
  { value: "in_progress", label: "开始处理" },
  { value: "resolved", label: "标记解决" },
  { value: "closed", label: "关闭" },
];

const severityLabel: Record<string, string> = {
  critical: "紧急",
  high: "高",
  medium: "中",
  low: "低",
};

function text(row: Row, key: string, fallback = "-"): string {
  const value = row[key];
  return value == null || value === "" ? fallback : String(value);
}

function statusLabel(status: string): string {
  return STATUS_OPTIONS.find((item) => item.value === status)?.label || status || "-";
}

function toneForSeverity(severity: string): string {
  if (severity === "critical" || severity === "high") return "vkpi-chip--warn";
  return "";
}

export function SettingsFeedbackPanel({ apiToken }: { apiToken?: string }) {
  const [statusFilter, setStatusFilter] = React.useState("open");
  const [rows, setRows] = React.useState<Row[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [busyUid, setBusyUid] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [error, setError] = React.useState("");

  const loadFeedback = React.useCallback(async () => {
    if (!apiToken) return;
    setLoading(true);
    setError("");
    try {
      const result = await listTeamFeedback(apiToken, statusFilter, 100);
      const nextRows = result.feedback || [];
      setRows(nextRows);
      setMessage(`已加载 ${result.count ?? nextRows.length} 条反馈`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "反馈列表加载失败");
    } finally {
      setLoading(false);
    }
  }, [apiToken, statusFilter]);

  React.useEffect(() => {
    void loadFeedback();
  }, [loadFeedback]);

  const updateStatus = async (uid: string, status: string) => {
    if (!apiToken || !uid || !status) return;
    setBusyUid(uid);
    setError("");
    try {
      await updateTeamFeedbackStatus(apiToken, uid, status);
      setMessage(`反馈 ${uid} 已更新为 ${statusLabel(status)}`);
      await loadFeedback();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "反馈状态更新失败");
    } finally {
      setBusyUid("");
    }
  };

  const openCount = rows.filter((row) => text(row, "status") === "open").length;
  const urgentCount = rows.filter((row) => ["critical", "high"].includes(text(row, "severity", ""))).length;
  const buttonIssueCount = rows.filter((row) => text(row, "feedback_type", "") === "button_issue").length;

  return (
    <section className="vkpi-card vkpi-table-card vkpi-action-card--wide" data-testid="vkpi-feedback-admin-panel">
      <div className="vkpi-table-card__header">
        <div>
          <h2>内测反馈管理</h2>
          <span>真实团队反馈 · 状态闭环 · 不再散落在聊天里</span>
        </div>
        <button className="vkpi-button" type="button" disabled={loading || !apiToken} onClick={() => void loadFeedback()}>
          {loading ? "刷新中" : "刷新"}
        </button>
      </div>

      <div className="vkpi-platform-crawl-kpis">
        <div><span>当前列表</span><strong>{rows.length}</strong></div>
        <div><span>未处理</span><strong>{openCount}</strong></div>
        <div><span>高优先级</span><strong>{urgentCount}</strong></div>
        <div><span>按钮问题</span><strong>{buttonIssueCount}</strong></div>
      </div>

      <div className="vkpi-chip-list" aria-label="反馈状态筛选">
        {STATUS_OPTIONS.map((item) => (
          <button
            key={item.value || "all"}
            className={`vkpi-chip ${statusFilter === item.value ? "is-selected" : ""}`}
            type="button"
            onClick={() => setStatusFilter(item.value)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      {error ? <div className="vkpi-inline-message is-error">{error}</div> : null}

      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead>
            <tr>
              <th>问题</th>
              <th>页面</th>
              <th>类型</th>
              <th>优先级</th>
              <th>状态</th>
              <th>提交时间</th>
              <th>处理</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? rows.map((row) => {
              const uid = text(row, "uid", "");
              const severity = text(row, "severity", "medium");
              const status = text(row, "status", "open");
              return (
                <tr key={uid || `${text(row, "title")}-${text(row, "created_at")}`}>
                  <td>
                    <strong>{text(row, "title")}</strong>
                    <br />
                    <small>{text(row, "detail", "无详细说明")}</small>
                  </td>
                  <td>{text(row, "page_path")}</td>
                  <td>{text(row, "feedback_type")}</td>
                  <td><span className={`vkpi-chip ${toneForSeverity(severity)}`}>{severityLabel[severity] || severity}</span></td>
                  <td>{statusLabel(status)}</td>
                  <td>{text(row, "created_at")}</td>
                  <td>
                    <div className="vkpi-chip-list">
                      {STATUS_ACTIONS.filter((item) => item.value !== status).map((item) => (
                        <button
                          key={item.value}
                          className="vkpi-mini-button"
                          type="button"
                          disabled={busyUid === uid}
                          onClick={() => void updateStatus(uid, item.value)}
                        >
                          {busyUid === uid ? "更新中" : item.label}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
              );
            }) : (
              <tr>
                <td className="vkpi-table-empty" colSpan={7}>当前筛选下暂无反馈。内测期间这里应该每天检查。</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
