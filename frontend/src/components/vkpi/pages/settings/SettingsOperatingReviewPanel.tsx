import React from "react";
import { getOperatingReviewStatus } from "../../../../services/vkpi.ui-api";

type Row = Record<string, unknown>;

function listValue(payload: Row, key: string): Row[] {
  const value = payload[key];
  return Array.isArray(value) ? value.filter((item): item is Row => item && typeof item === "object") : [];
}

function recordValue(payload: Row, key: string): Row {
  const value = payload[key];
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function text(row: Row, key: string, fallback = "-"): string {
  const value = row[key];
  return value == null || value === "" ? fallback : String(value);
}

function countText(row: Row, key: string): string {
  const value = Number(row[key] ?? 0);
  return Number.isFinite(value) ? value.toLocaleString("en-US") : "0";
}

function boolText(value: unknown): string {
  return String(Boolean(value));
}

function priorityClass(priority: string): string {
  return ["critical", "danger", "high", "warning"].includes(priority.toLowerCase()) ? "vkpi-chip--warn" : "";
}

export function SettingsOperatingReviewPanel({ apiToken }: { apiToken?: string }) {
  const [payload, setPayload] = React.useState<Row>({});
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  const loadReview = React.useCallback(async () => {
    if (!apiToken) return;
    setLoading(true);
    setError("");
    try {
      setPayload(await getOperatingReviewStatus(apiToken, 25));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Operating review 读取失败");
    } finally {
      setLoading(false);
    }
  }, [apiToken]);

  React.useEffect(() => {
    void loadReview();
  }, [loadReview]);

  const counts = recordValue(payload, "counts");
  const workItems = listValue(payload, "top_work_items").slice(0, 12);
  const gaps = Array.isArray(payload.gaps) ? payload.gaps.map(String) : [];
  const alertRules = recordValue(payload, "alert_rules_open");
  const competitorBrands = recordValue(payload, "competitor_brands_pending");
  const hasPayload = Object.keys(payload).length > 0;

  return (
    <section className="vkpi-card vkpi-table-card vkpi-action-card--wide" data-testid="vkpi-operating-review-panel">
      <div className="vkpi-table-card__header">
        <div>
          <h2>Operating Review</h2>
          <span>v5.3.1 后只读 backlog 快照 · 不写库 · 不调模型</span>
        </div>
        <button className="vkpi-button" type="button" disabled={loading || !apiToken} onClick={() => void loadReview()}>
          {loading ? "刷新中" : "刷新"}
        </button>
      </div>

      <div className="vkpi-platform-crawl-kpis">
        <div><span>Open alerts</span><strong>{countText(counts, "open_alerts")}</strong></div>
        <div><span>竞品待审</span><strong>{countText(counts, "pending_competitor_signals")}</strong></div>
        <div><span>推荐反馈</span><strong>{countText(counts, "recommendation_feedback")}</strong></div>
        <div><span>Memory 反馈</span><strong>{countText(counts, "memory_feedback")}</strong></div>
      </div>

      <div className="vkpi-chip-list">
        <span className="vkpi-chip">provider_calls={String(Boolean(payload.provider_calls))}</span>
        <span className="vkpi-chip">write_db={String(Boolean(payload.write_db))}</span>
        <span className="vkpi-chip">outcomes={countText(counts, "recommendation_outcomes")}</span>
        <span className="vkpi-chip">scenario={text(payload, "scenario", "vkpi_operating_review")}</span>
      </div>

      {error ? <div className="vkpi-inline-message is-error">{error}</div> : null}
      {!error && !hasPayload && !loading ? <div className="vkpi-empty-panel">暂无 Operating Review 数据。</div> : null}

      <div className="vkpi-settings-card-grid">
        <article className="vkpi-settings-toggle-card is-on">
          <header><strong>Open alert rules</strong><span>{Object.keys(alertRules).length}</span></header>
          <p>{Object.entries(alertRules).map(([key, value]) => `${key}: ${value}`).join(" / ") || "none"}</p>
        </article>
        <article className="vkpi-settings-toggle-card is-on">
          <header><strong>Pending competitor brands</strong><span>{Object.keys(competitorBrands).length}</span></header>
          <p>{Object.entries(competitorBrands).map(([key, value]) => `${key}: ${value}`).join(" / ") || "none"}</p>
        </article>
        <article className={`vkpi-settings-toggle-card ${gaps.length ? "is-off" : "is-on"}`}>
          <header><strong>Feedback gaps</strong><span>{gaps.length}</span></header>
          <p>{gaps.join(" / ") || "none"}</p>
        </article>
        <article className="vkpi-settings-toggle-card is-on">
          <header><strong>Safety</strong><span>read-only</span></header>
          <p>provider_calls={boolText(payload.provider_calls)} / write_db={boolText(payload.write_db)}</p>
        </article>
      </div>

      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead>
            <tr>
              <th>事项</th>
              <th>优先级</th>
              <th>来源</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            {workItems.length ? workItems.map((item, index) => {
              const priority = text(item, "priority", "warning");
              return (
                <tr key={`${text(item, "source_table")}-${text(item, "source_id")}-${index}`}>
                  <td><strong>{text(item, "title")}</strong><br /><small>{text(item, "kind")}</small></td>
                  <td><span className={`vkpi-chip ${priorityClass(priority)}`}>{priority}</span></td>
                  <td>{text(item, "source_table")}:{text(item, "source_id")}</td>
                  <td>{text(item, "reason")}</td>
                </tr>
              );
            }) : (
              <tr><td className="vkpi-table-empty" colSpan={4}>{loading ? "正在读取 Operating Review" : "当前没有待处理事项。"}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
