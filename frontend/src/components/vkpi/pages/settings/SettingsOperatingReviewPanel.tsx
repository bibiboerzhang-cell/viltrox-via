import React from "react";
import {
  createMemoryFeedback,
  getMemoryFeedbackBacklog,
  getOperatingReviewStatus,
  getRecommendationFeedbackBacklog,
} from "../../../../services/vkpi/intelligence-api";
import { productRecommendationAction } from "../../../../services/vkpi/product-api";

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
  const [recommendationBacklog, setRecommendationBacklog] = React.useState<Row>({});
  const [memoryBacklog, setMemoryBacklog] = React.useState<Row>({});
  const [loading, setLoading] = React.useState(false);
  const [actionBusyId, setActionBusyId] = React.useState("");
  const [error, setError] = React.useState("");

  const loadReview = React.useCallback(async () => {
    if (!apiToken) return;
    setLoading(true);
    setError("");
    try {
      const [review, recommendations, memory] = await Promise.all([
        getOperatingReviewStatus(apiToken, 25),
        getRecommendationFeedbackBacklog(apiToken, 12),
        getMemoryFeedbackBacklog(apiToken, 12, "kol"),
      ]);
      setPayload(review);
      setRecommendationBacklog(recommendations);
      setMemoryBacklog(memory);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Operating review 读取失败");
    } finally {
      setLoading(false);
    }
  }, [apiToken]);

  React.useEffect(() => {
    void loadReview();
  }, [loadReview]);

  const handleRecommendationAction = React.useCallback(async (recommendationId: string, action: "shortlist" | "reject" | "feedback") => {
    if (!apiToken || !recommendationId) return;
    setActionBusyId(`${recommendationId}:${action}`);
    setError("");
    try {
      await productRecommendationAction(
        apiToken,
        recommendationId,
        action,
        action === "reject"
          ? { reason: "Operating Review 手动拒绝", source: "operating_review_backlog" }
          : action === "feedback"
            ? { note: "Operating Review 标记需复核", source: "operating_review_backlog" }
            : { note: "Operating Review 手动入选", source: "operating_review_backlog" },
      );
      await loadReview();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "推荐反馈写入失败");
    } finally {
      setActionBusyId("");
    }
  }, [apiToken, loadReview]);

  const handleMemoryFeedback = React.useCallback(async (entityUid: string, suggestion: Row) => {
    if (!apiToken || !entityUid) return;
    setActionBusyId(`memory:${entityUid}`);
    setError("");
    try {
      await createMemoryFeedback(apiToken, {
        entity_uid: entityUid,
        feedback_type: text(suggestion, "suggested_feedback_type", "entity_review"),
        note: "Operating Review 标记需核查",
        source: "operating_review_memory_backlog",
        suggested_action: text(suggestion, "suggested_action", ""),
        reasons: Array.isArray(suggestion.reasons) ? suggestion.reasons : [],
      });
      await loadReview();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Memory 反馈写入失败");
    } finally {
      setActionBusyId("");
    }
  }, [apiToken, loadReview]);

  const counts = recordValue(payload, "counts");
  const workItems = listValue(payload, "top_work_items").slice(0, 12);
  const gaps = Array.isArray(payload.gaps) ? payload.gaps.map(String) : [];
  const alertRules = recordValue(payload, "alert_rules_open");
  const competitorBrands = recordValue(payload, "competitor_brands_pending");
  const recommendationSummary = recordValue(recommendationBacklog, "summary");
  const memorySummary = recordValue(memoryBacklog, "summary");
  const recommendationItems = listValue(recommendationBacklog, "items").slice(0, 8);
  const memoryItems = listValue(memoryBacklog, "items").slice(0, 8);
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
        <span className="vkpi-chip">推荐待反馈={countText(recommendationSummary, "missing_feedback_rows")}</span>
        <span className="vkpi-chip">Memory 待核查={countText(memorySummary, "backlog_candidates")}</span>
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

      <div className="vkpi-settings-card-grid">
        <article className="vkpi-settings-toggle-card is-off">
          <header><strong>推荐反馈 backlog</strong><span>{countText(recommendationSummary, "missing_feedback_rows")}</span></header>
          <p>returned={recommendationItems.length.toLocaleString("en-US")} / with_feedback={countText(recommendationSummary, "with_feedback_rows")}</p>
        </article>
        <article className="vkpi-settings-toggle-card is-off">
          <header><strong>Memory 核查 backlog</strong><span>{countText(memorySummary, "backlog_candidates")}</span></header>
          <p>high_priority={countText(recordValue(memoryBacklog, "severity_counts"), "high")} / feedback_rows={countText(memorySummary, "memory_feedback_rows")}</p>
        </article>
      </div>

      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead>
            <tr>
              <th>推荐</th>
              <th>Run</th>
              <th>建议</th>
              <th>原因</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {recommendationItems.length ? recommendationItems.map((item) => {
              const kol = recordValue(item, "kol");
              const suggestion = recordValue(item, "suggestion");
              const reasons = Array.isArray(suggestion.reasons) ? suggestion.reasons.map(String).join(", ") : "";
              const recommendationId = text(item, "recommendation_id", "");
              return (
                <tr key={`rec-feedback-${recommendationId}`}>
                  <td><strong>{text(kol, "platform")}:{text(kol, "handle")}</strong><br /><small>rank={text(item, "rank")} score={text(item, "score")}</small></td>
                  <td>{text(item, "run_uid")}</td>
                  <td><span className="vkpi-chip vkpi-chip--warn">{text(suggestion, "suggested_action")}</span></td>
                  <td>{reasons || "-"}</td>
                  <td>
                    <div className="vkpi-row-actions">
                      <button
                        className="vkpi-mini-button"
                        type="button"
                        disabled={!apiToken || Boolean(actionBusyId)}
                        onClick={() => void handleRecommendationAction(recommendationId, "shortlist")}
                      >
                        入选
                      </button>
                      <button
                        className="vkpi-mini-button"
                        type="button"
                        disabled={!apiToken || Boolean(actionBusyId)}
                        onClick={() => void handleRecommendationAction(recommendationId, "feedback")}
                      >
                        需复核
                      </button>
                      <button
                        className="vkpi-mini-button"
                        type="button"
                        disabled={!apiToken || Boolean(actionBusyId)}
                        onClick={() => void handleRecommendationAction(recommendationId, "reject")}
                      >
                        拒绝
                      </button>
                    </div>
                  </td>
                </tr>
              );
            }) : (
              <tr><td className="vkpi-table-empty" colSpan={5}>{loading ? "正在读取推荐反馈 backlog" : "没有推荐反馈 backlog。"}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead>
            <tr>
              <th>Memory 实体</th>
              <th>信号</th>
              <th>建议</th>
              <th>优先级</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {memoryItems.length ? memoryItems.map((item) => {
              const signals = recordValue(item, "signals");
              const suggestion = recordValue(item, "suggestion");
              const entityUid = text(item, "entity_uid", "");
              return (
                <tr key={`memory-feedback-${entityUid}`}>
                  <td><strong>{text(item, "display_name", text(item, "identity_key"))}</strong><br /><small>{entityUid}</small></td>
                  <td>sync={text(signals, "sync_status")} / weak={text(signals, "weak_label")} / risk={Array.isArray(signals.risk_flags) ? signals.risk_flags.length : 0}</td>
                  <td><span className={`vkpi-chip ${priorityClass(text(suggestion, "severity"))}`}>{text(suggestion, "suggested_action")}</span></td>
                  <td>{text(suggestion, "severity")} · {text(suggestion, "priority_score")}</td>
                  <td>
                    <button
                      className="vkpi-mini-button"
                      type="button"
                      disabled={!apiToken || Boolean(actionBusyId)}
                      onClick={() => void handleMemoryFeedback(entityUid, suggestion)}
                    >
                      记录核查
                    </button>
                  </td>
                </tr>
              );
            }) : (
              <tr><td className="vkpi-table-empty" colSpan={5}>{loading ? "正在读取 Memory backlog" : "没有 Memory backlog。"}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
