/**
 * Via tab v2 — AI ops
 *
 * Via is the AI governance layer. Shows:
 *   - Proposals (AI-generated action proposals awaiting approval)
 *   - Policies (active policy versions)
 *   - Evaluations (run history)
 *
 * Data: fetchAdminViaSnapshot
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  fetchAdminViaSnapshot,
  runViaEvaluation,
  runViaPolicyAction,
  runViaProposalAction,
} from "../../../services/admin.service";
import type { AuthUser } from "../../../lib/api";
import { Icons } from "../Icons";
import {
  DataTable,
  EmptyCard,
  ErrorCard,
  KPIGrid,
  LoadingCard,
  PageHeader,
  SegButton,
  SectionLabel,
  StatusPill,
  useAdminSnapshot,
  type DataColumn,
} from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

type Section = "proposals" | "policies" | "evaluations";
type ProposalAction = "approve" | "reject" | "apply" | "stage";
type PolicyAction = "promote" | "rollback" | "advance-rollout";

export function ViaTab({ token }: Props) {
  const { t } = useTranslation();
  const tt = (key: string, fallback: string, options: Record<string, unknown> = {}) =>
    String(t(`admin.via.${key}`, { defaultValue: fallback, ...options }));
  const { data, loading, error, refresh } = useAdminSnapshot(token, fetchAdminViaSnapshot);
  const [section, setSection] = useState<Section>("proposals");
  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState<{ tone: "ok" | "err"; msg: string } | null>(null);
  const policies = data?.livePolicies ?? [];
  const evaluations = data?.policyHistory ?? [];

  const policyKeyFor = (r: Record<string, unknown>) =>
    String(r.version_key || r.policy_version_key || r.id || r.policy_key || "");

  const handleEvaluate = async () => {
    setBusy("evaluate");
    try {
      await runViaEvaluation(token);
      setToast({ tone: "ok", msg: tt("messages.evaluated", "Via 评估已触发") });
      refresh();
    } catch (err) {
      setToast({ tone: "err", msg: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy("");
    }
  };

  const handleProposalAction = async (row: Record<string, unknown>, action: ProposalAction) => {
    const proposalKey = String(row.proposal_key || row.id || "");
    if (!proposalKey) return;
    const note = window.prompt(tt("prompts.proposalNote", "提案操作备注"), `admin via ${action}`) || `admin via ${action}`;
    setBusy(`proposal:${proposalKey}:${action}`);
    try {
      await runViaProposalAction(token, proposalKey, action, note);
      setToast({ tone: "ok", msg: tt("messages.proposalUpdated", "提案 {{key}} 已更新", { key: proposalKey }) });
      refresh();
    } catch (err) {
      setToast({ tone: "err", msg: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy("");
    }
  };

  const handlePolicyAction = async (row: Record<string, unknown>, action: PolicyAction) => {
    const versionKey = policyKeyFor(row);
    if (!versionKey) return;
    const note = window.prompt(tt("prompts.policyNote", "策略操作备注"), `admin via ${action}`) || `admin via ${action}`;
    setBusy(`policy:${versionKey}:${action}`);
    try {
      await runViaPolicyAction(token, versionKey, action, note);
      setToast({ tone: "ok", msg: tt("messages.policyUpdated", "策略 {{key}} 已更新", { key: versionKey }) });
      refresh();
    } catch (err) {
      setToast({ tone: "err", msg: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy("");
    }
  };

  const kpis = useMemo(() => {
    const o = (data?.overview || {}) as Record<string, unknown>;
    return [
      { label: tt("kpis.pendingProposals", "待审提案"), value: Number(o.pending_proposals || 0) },
      { label: tt("kpis.activePolicies", "激活策略"), value: Number(o.active_policies || 0) },
      { label: tt("kpis.evaluations7d", "7日评估"), value: Number(o.evaluations_7d || 0) },
      { label: tt("kpis.avgConfidence", "平均置信度"), value: `${Math.round(Number(o.avg_confidence || 0) * 100)}%` },
    ];
  }, [data, t]); // eslint-disable-line react-hooks/exhaustive-deps

  const proposalCols: DataColumn<Record<string, unknown>>[] = [
    {
      key: "title",
      label: tt("columns.proposal", "提案"),
      width: "2.5fr",
      render: (r) => (
        <div>
          <div style={{ color: "var(--ax-text-5)" }}>
            {String(r.title || r.action || "untitled")}
          </div>
          <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            {String(r.reason || r.description || "")}
          </div>
        </div>
      ),
    },
    {
      key: "confidence",
      label: tt("columns.confidence", "置信度"),
      width: "80px",
      accent: true,
      render: (r) => (
        <span className="ax-num" style={{ fontWeight: 600 }}>
          {Math.round(Number(r.confidence || 0) * 100)}%
        </span>
      ),
    },
    {
      key: "status",
      label: "状态",
      width: "100px",
      render: (r) => {
        const s = String(r.status || "pending").toLowerCase();
        const tone =
          s === "approved" ? "pass" : s === "rejected" ? "block" : "review";
        return <StatusPill tone={tone as never}>{String(r.status || "pending")}</StatusPill>;
      },
    },
    {
      key: "actions",
      label: "",
      width: "280px",
      render: (r) => {
        const proposalKey = String(r.proposal_key || r.id || "");
        return (
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }} onClick={(e) => e.stopPropagation()}>
            <button type="button" className="ax-btn ax-btn--sm" disabled={!proposalKey || busy === `proposal:${proposalKey}:approve`} onClick={() => handleProposalAction(r, "approve")}>{tt("actions.approve", "通过")}</button>
            <button type="button" className="ax-btn ax-btn--sm" disabled={!proposalKey || busy === `proposal:${proposalKey}:stage`} onClick={() => handleProposalAction(r, "stage")}>{tt("actions.stage", "暂存")}</button>
            <button type="button" className="ax-btn ax-btn--sm" style={{ color: "var(--ax-status-pass)" }} disabled={!proposalKey || busy === `proposal:${proposalKey}:apply`} onClick={() => handleProposalAction(r, "apply")}>{tt("actions.apply", "应用")}</button>
            <button type="button" className="ax-btn ax-btn--sm" style={{ color: "var(--ax-status-alert)" }} disabled={!proposalKey || busy === `proposal:${proposalKey}:reject`} onClick={() => handleProposalAction(r, "reject")}>{tt("actions.reject", "拒绝")}</button>
          </div>
        );
      },
    },
  ];

  const policyCols: DataColumn<Record<string, unknown>>[] = [
    {
      key: "policy",
      label: tt("columns.policy", "策略"),
      width: "2fr",
      render: (r) => (
        <div>
          <div style={{ color: "var(--ax-text-5)" }}>{String(r.name || r.policy_key || "—")}</div>
          <div className="ax-mono" style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            v{String(r.version || "1")}
          </div>
        </div>
      ),
    },
    {
      key: "scope",
      label: tt("columns.scope", "范围"),
      width: "120px",
      render: (r) => (
        <span style={{ color: "var(--ax-text-3)" }}>{String(r.scope || "global")}</span>
      ),
    },
    {
      key: "status",
      label: "状态",
      width: "100px",
      render: (r) => {
        const active = Boolean(r.active);
        return <StatusPill tone={active ? "active" : "idle"}>{active ? tt("status.active", "在线") : tt("status.inactive", "未启用")}</StatusPill>;
      },
    },
    {
      key: "actions",
      label: "",
      width: "260px",
      render: (r) => {
        const versionKey = policyKeyFor(r);
        return (
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }} onClick={(e) => e.stopPropagation()}>
            <button type="button" className="ax-btn ax-btn--sm" style={{ color: "var(--ax-status-pass)" }} disabled={!versionKey || busy === `policy:${versionKey}:promote`} onClick={() => handlePolicyAction(r, "promote")}>{tt("actions.promote", "提升")}</button>
            <button type="button" className="ax-btn ax-btn--sm" disabled={!versionKey || busy === `policy:${versionKey}:advance-rollout`} onClick={() => handlePolicyAction(r, "advance-rollout")}>{tt("actions.advanceRollout", "扩大灰度")}</button>
            <button type="button" className="ax-btn ax-btn--sm" style={{ color: "var(--ax-status-alert)" }} disabled={!versionKey || busy === `policy:${versionKey}:rollback`} onClick={() => handlePolicyAction(r, "rollback")}>{tt("actions.rollback", "回滚")}</button>
          </div>
        );
      },
    },
  ];

  const evalCols: DataColumn<Record<string, unknown>>[] = [
    {
      key: "target",
      label: tt("columns.target", "目标"),
      width: "1.8fr",
      render: (r) => (
        <span style={{ color: "var(--ax-text-5)" }}>{String(r.target || r.scope || "—")}</span>
      ),
    },
    {
      key: "verdict",
      label: tt("columns.verdict", "结论"),
      width: "100px",
      render: (r) => {
        const v = String(r.verdict || r.outcome || "").toLowerCase();
        const tone =
          v === "pass" || v === "accept"
            ? "pass"
            : v === "fail" || v === "reject"
            ? "block"
            : "review";
        return <StatusPill tone={tone as never}>{String(r.verdict || r.outcome || "—")}</StatusPill>;
      },
    },
    {
      key: "at",
      label: "时间",
      width: "120px",
      render: (r) => (
        <span style={{ color: "var(--ax-text-2)", fontSize: 10 }}>
          {r.created_at ? new Date(String(r.created_at)).toLocaleString() : "—"}
        </span>
      ),
    },
  ];

  const sections: Array<{ key: Section; label: string }> = [
    { key: "proposals", label: `${tt("sections.proposals", "提案")} (${data?.proposals?.length || 0})` },
    { key: "policies", label: `${tt("sections.policies", "策略")} (${policies.length})` },
    { key: "evaluations", label: `${tt("sections.evaluations", "评估")} (${evaluations.length})` },
  ];

  return (
    <div>
      <PageHeader
        title={tt("title", "Via · AI 运营")}
        subtitle={tt("subtitle", "AI 提案 · 策略版本 · 评估记录")}
        actions={
          <>
            <button type="button" className="ax-btn" onClick={handleEvaluate} disabled={busy === "evaluate"}>
              <Icons.via /> {busy === "evaluate" ? tt("actions.evaluating", "评估中…") : tt("actions.evaluate", "立即评估")}
            </button>
            <button type="button" className="ax-btn" onClick={refresh} disabled={loading}>
              <Icons.trending /> {loading ? tt("actions.refreshing", "刷新中…") : tt("actions.refresh", "刷新")}
            </button>
          </>
        }
      />

      {error ? (
        <div style={{ padding: 16 }}>
          <ErrorCard detail={error} onRetry={refresh} />
        </div>
      ) : null}

      {toast ? (
        <div
          style={{
            padding: "8px 16px",
            background:
              toast.tone === "ok"
                ? "rgba(99, 165, 30, 0.08)"
                : "rgba(209, 69, 32, 0.08)",
            color:
              toast.tone === "ok"
                ? "var(--ax-status-pass)"
                : "var(--ax-status-alert)",
            fontSize: 11,
            borderBottom: "0.5px solid var(--ax-border-2)",
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <span>{toast.msg}</span>
          <span style={{ cursor: "pointer", color: "var(--ax-text-1)" }} onClick={() => setToast(null)}>×</span>
        </div>
      ) : null}

      <div style={{ padding: 16 }}>
        {loading && !data ? (
          <LoadingCard label={tt("loading", "加载 Via 数据…")} />
        ) : (
          <>
            <div style={{ marginBottom: 16 }}>
              <KPIGrid items={kpis} columns={4} />
            </div>

            <div style={{ marginBottom: 12 }}>
              <SegButton
                items={sections.map((s) => ({ key: s.key, label: s.label }))}
                active={section}
                onChange={(k) => setSection(k as Section)}
              />
            </div>

            <div
              style={{
                border: "0.5px solid var(--ax-border-2)",
                borderRadius: 6,
                overflow: "hidden",
                background: "var(--ax-bg-1)",
              }}
            >
              {section === "proposals" ? (
                (data?.proposals || []).length === 0 ? (
                  <EmptyCard label={tt("empty.proposals", "无待审提案")} />
                ) : (
                  <DataTable
                    columns={proposalCols}
                    rows={data?.proposals as unknown as Record<string, unknown>[]}
                    rowKey={(r) => String(r.proposal_key || r.id)}
                    showCheckbox={false}
                  />
                )
              ) : section === "policies" ? (
                policies.length === 0 ? (
                  <EmptyCard label={tt("empty.policies", "无激活策略")} />
                ) : (
                  <DataTable
                    columns={policyCols}
                    rows={policies as unknown as Record<string, unknown>[]}
                    rowKey={(r) => String(r.id || r.policy_key)}
                    showCheckbox={false}
                  />
                )
              ) : evaluations.length === 0 ? (
                <EmptyCard label={tt("empty.evaluations", "暂无评估记录")} />
              ) : (
                <DataTable
                  columns={evalCols}
                  rows={evaluations as unknown as Record<string, unknown>[]}
                  rowKey={(r) => String(r.id)}
                  showCheckbox={false}
                />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default ViaTab;
