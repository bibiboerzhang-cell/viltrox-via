import type { Dispatch, SetStateAction } from "react";
import { useTranslation } from "react-i18next";

import type { AdminViaSnapshot, ViaPolicyVersionRecord, ViaProposalRecord } from "../../../services/admin.service";
import { EmptyState, MetricStrip, Panel, StatusPill } from "../../ui";
import { compactNumber, DataTable, formatDate, percentLabel, TablePager, toNumber, toneForStatus } from "../shared";

interface ViaTabProps {
  via: AdminViaSnapshot | null;
  rolloutNote: string;
  setRolloutNote: Dispatch<SetStateAction<string>>;
  busy: string;
  evaluateViaNow: () => Promise<void>;
  viaSearch: string;
  setViaSearch: Dispatch<SetStateAction<string>>;
  viaPage: number;
  viaTotalPages: number;
  viaProposalRows: ViaProposalRecord[];
  viaRowsPaged: ViaProposalRecord[];
  setViaPage: Dispatch<SetStateAction<number>>;
  proposalAction: (proposalKey: string, action: "approve" | "stage" | "apply" | "reject") => Promise<void>;
  selectedPolicyKey: string;
  setSelectedPolicyKey: Dispatch<SetStateAction<string>>;
  selectedViaLivePolicy: ViaPolicyVersionRecord | null;
  selectedViaHistoryRows: Array<Record<string, unknown>>;
  selectedViaAlerts: Array<Record<string, unknown>>;
  selectedViaMemory: Array<Record<string, unknown>>;
  policyAction: (versionKey: string, action: "promote" | "advance-rollout" | "rollback") => Promise<void>;
}

export function ViaTab({
  via,
  rolloutNote,
  setRolloutNote,
  busy,
  evaluateViaNow,
  viaSearch,
  setViaSearch,
  viaPage,
  viaTotalPages,
  viaProposalRows,
  viaRowsPaged,
  setViaPage,
  proposalAction,
  selectedPolicyKey,
  setSelectedPolicyKey,
  selectedViaLivePolicy,
  selectedViaHistoryRows,
  selectedViaAlerts,
  selectedViaMemory,
  policyAction,
}: ViaTabProps) {
  const { t } = useTranslation();
  const proposalActionLabels: Record<"approve" | "stage" | "apply" | "reject", string> = {
    approve: t("admin.via.proposals.actions.approve"),
    stage: t("admin.via.proposals.actions.stage"),
    apply: t("admin.via.proposals.actions.apply"),
    reject: t("admin.via.proposals.actions.reject"),
  };
  return (
    <div className="admin-workspace-grid">
      <Panel title={t("admin.via.overview.title")} kicker={t("admin.via.overview.kicker")}>
        <MetricStrip
          columns={4}
          items={[
            { label: t("admin.via.overview.metrics.decisions.label"), value: compactNumber(via?.overview?.metrics?.decision_count || 0), note: t("admin.via.overview.metrics.decisions.note") },
            { label: t("admin.via.overview.metrics.acceptedRate.label"), value: percentLabel(via?.overview?.metrics?.accepted_rate || 0), note: t("admin.via.overview.metrics.acceptedRate.note") },
            { label: t("admin.via.overview.metrics.avgReward.label"), value: String(toNumber(via?.overview?.metrics?.avg_reward || 0).toFixed(3)), note: t("admin.via.overview.metrics.avgReward.note") },
            { label: t("admin.via.overview.metrics.rewardTraces.label"), value: compactNumber(via?.overview?.metrics?.reward_trace_count || 0), note: t("admin.via.overview.metrics.rewardTraces.note") },
          ]}
        />
        <div className="admin-note-form">
          <label className="auth-field admin-form-grid__full">
            <span>{t("admin.via.overview.rolloutNote")}</span>
            <textarea rows={3} value={rolloutNote} onChange={(event) => setRolloutNote(event.target.value)} placeholder={t("admin.via.overview.rolloutPlaceholder")} />
          </label>
          <div className="auth-actions">
            <button className="primary-button" type="button" disabled={busy === "via:evaluate"} onClick={() => void evaluateViaNow()}>
              {busy === "via:evaluate" ? t("admin.via.overview.evaluating") : t("admin.via.overview.evaluateAction")}
            </button>
          </div>
        </div>
      </Panel>

      <Panel title={t("admin.via.proposals.title")} kicker={t("admin.via.proposals.kicker")}>
        <div className="admin-note-form">
          <label className="auth-field admin-form-grid__full">
            <span>{t("admin.via.proposals.searchLabel")}</span>
            <input value={viaSearch} onChange={(event) => setViaSearch(event.target.value)} placeholder={t("admin.via.proposals.searchPlaceholder")} />
          </label>
        </div>
        <TablePager page={viaPage} totalPages={viaTotalPages} totalItems={viaProposalRows.length} label={t("admin.via.proposals.pagerLabel")} onChange={setViaPage} />
        <DataTable
          columns={[t("admin.via.proposals.columns.policy"), t("admin.via.proposals.columns.status"), t("admin.via.proposals.columns.actions")]}
          rows={viaRowsPaged.map((proposal) => [
            <div key={proposal.proposal_key}>
              <div className="table-primary">{proposal.policy_key || proposal.proposal_key}</div>
              <small>{proposal.target || proposal.audit_actor || t("admin.via.proposals.proposalFallback")}</small>
            </div>,
            <StatusPill key={`${proposal.proposal_key}-status`} label={proposal.status || t("admin.shared.pending")} tone={toneForStatus(proposal.status || "")} />,
            <div key={`${proposal.proposal_key}-actions`} className="admin-inline-actions">
              <button className="outline-btn" type="button" onClick={() => setSelectedPolicyKey(String(proposal.policy_key || proposal.proposal_key))}>
                {t("admin.via.proposals.focus")}
              </button>
              {(["approve", "stage", "apply", "reject"] as const).map((action) => (
                <button
                  key={action}
                  className="outline-btn"
                  type="button"
                  disabled={busy === `via:proposal:${proposal.proposal_key}:${action}`}
                  onClick={() => void proposalAction(proposal.proposal_key, action)}
                >
                  {proposalActionLabels[action]}
                </button>
              ))}
            </div>,
          ])}
          empty={t("admin.via.proposals.empty")}
        />
      </Panel>

      <Panel title={t("admin.via.detail.title")} kicker={t("admin.via.detail.kicker")}>
        {selectedViaLivePolicy ? (
          <div className="admin-two-column">
            <div className="admin-list-stack">
              <article className="admin-list-item">
                <strong>{String(selectedViaLivePolicy.policy_key || selectedViaLivePolicy.version_key || t("admin.via.detail.policyFallback"))}</strong>
                <p>
                  {String(selectedViaLivePolicy.version_label || selectedViaLivePolicy.version_key || t("admin.via.detail.versionFallback"))} · {String(selectedViaLivePolicy.status || t("admin.shared.live"))}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.via.detail.configSnapshot")}</strong>
                <p>
                  {t("admin.via.detail.configLine", {
                    rollout: Math.round(toNumber((selectedViaLivePolicy.config || {}).rollout_percentage || 0) * 100),
                    mode: String((selectedViaLivePolicy.config || {}).mode || (selectedViaLivePolicy.config || {}).target || t("admin.shared.default")),
                  })}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.via.detail.recentHistory")}</strong>
                <p>{selectedViaHistoryRows.slice(0, 3).map((item) => String(item.status || item.source || item.version_label || t("admin.shared.event"))).join(" · ") || t("admin.via.detail.noPolicyHistory")}</p>
              </article>
            </div>
            <div className="admin-list-stack">
              <article className="admin-list-item">
                <strong>{t("admin.via.detail.alertStream")}</strong>
                <p>{selectedViaAlerts.length ? selectedViaAlerts.map((item) => String(item.reason || item.message || item.alert_type || t("admin.shared.alert"))).join(" · ") : t("admin.via.detail.noAlerts")}</p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.via.detail.memoryRetention")}</strong>
                <p>
                  {selectedViaMemory.length
                    ? selectedViaMemory.map((item) => `${String(item.bucket_key || item.policy_key || t("admin.shared.memory"))} ${compactNumber(item.live_decision_count || item.count || 0)}`).join(" · ")
                    : t("admin.via.detail.noMemoryRetention")}
                </p>
              </article>
            </div>
          </div>
        ) : (
          <EmptyState title={t("admin.via.detail.emptyTitle")} body={t("admin.via.detail.emptyBody")} />
        )}
      </Panel>

      <Panel title={t("admin.via.livePolicies.title")} kicker={t("admin.via.livePolicies.kicker")}>
        <div className="admin-card-list">
          {(via?.livePolicies || []).map((policy) => {
            const liveHealth = (via?.liveRolloutHealth || []).find((item) => String(item.version_key || "") === policy.version_key);
            return (
              <article key={policy.version_key} className="admin-mini-card">
                <strong>{policy.policy_key || policy.version_key}</strong>
                <p>{policy.version_label || policy.version_key}</p>
                <StatusPill label={String(liveHealth?.status || policy.status || t("admin.shared.live"))} tone={toneForStatus(String(liveHealth?.status || policy.status || ""))} />
                <span>{t("admin.via.livePolicies.rollout", { value: Math.round(toNumber((liveHealth?.current_rollout_percentage || (policy.config || {}).rollout_percentage || 0)) * 100) })}</span>
                <div className="admin-inline-actions">
                  <button className="outline-btn" type="button" onClick={() => void policyAction(policy.version_key, "promote")}>
                    {t("admin.via.livePolicies.promote")}
                  </button>
                  <button className="outline-btn" type="button" onClick={() => void policyAction(policy.version_key, "advance-rollout")}>
                    {t("admin.via.livePolicies.advance")}
                  </button>
                  <button className="outline-btn" type="button" onClick={() => void policyAction(policy.version_key, "rollback")}>
                    {t("admin.via.livePolicies.rollback")}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      </Panel>

      <Panel title={t("admin.via.signals.title")} kicker={t("admin.via.signals.kicker")}>
        <div className="admin-three-column">
          <div>
            <strong className="section-mini-head">{t("admin.via.signals.shadowReadiness")}</strong>
            <div className="admin-list-stack">
              {(via?.shadowReadiness || []).slice(0, 5).map((item, index) => (
                <article key={index} className="admin-list-item">
                  <strong>{String(item.policy_key || item.version_label || t("admin.via.detail.policyFallback"))}</strong>
                  <p>
                    {String(item.status || t("admin.shared.hold"))} · {Math.round(toNumber(item.recommended_rollout_percentage || 0) * 100)}%
                  </p>
                </article>
              ))}
            </div>
          </div>
          <div>
            <strong className="section-mini-head">{t("admin.via.signals.rolloutAlerts")}</strong>
            <div className="admin-list-stack">
              {(via?.rolloutAlerts || []).slice(0, 5).map((item, index) => (
                <article key={index} className="admin-list-item">
                  <strong>{String(item.policy_key || item.alert_type || t("admin.shared.alert"))}</strong>
                  <p>{String(item.status || t("admin.shared.open"))} · {String(item.reason || item.message || t("admin.via.signals.watchSignal"))}</p>
                </article>
              ))}
            </div>
          </div>
          <div>
            <strong className="section-mini-head">{t("admin.via.signals.retrievalLearner")}</strong>
            <div className="admin-list-stack">
              {(via?.retrievalEvidence || []).slice(0, 3).map((item, index) => (
                <article key={`retrieval-${index}`} className="admin-list-item">
                  <strong>{String(item.policy_key || item.retrieval_mode || t("admin.via.signals.retrieval"))}</strong>
                  <p>
                    {compactNumber(item.vector_hit_count || 0)} {t("admin.via.signals.vector")} · {compactNumber(item.bundle_hit_count || 0)} {t("admin.via.signals.bundle")} · {compactNumber(item.seed_hit_count || 0)} {t("admin.via.signals.seed")}
                  </p>
                </article>
              ))}
              {(via?.routingLearner || []).slice(0, 3).map((item, index) => (
                <article key={`routing-${index}`} className="admin-list-item">
                  <strong>{String(item.bucket_key || item.provider || t("admin.via.signals.provider"))}</strong>
                  <p>
                    {t("admin.via.signals.routingLine", {
                      accepted: percentLabel(item.accepted_rate || 0),
                      reward: toNumber(item.avg_reward || 0).toFixed(3),
                    })}
                  </p>
                </article>
              ))}
            </div>
          </div>
        </div>
      </Panel>

      <Panel title={t("admin.via.history.title")} kicker={t("admin.via.history.kicker")}>
        <DataTable
          columns={[t("admin.via.history.columns.policy"), t("admin.via.history.columns.version"), t("admin.via.history.columns.source"), t("admin.via.history.columns.status"), t("admin.via.history.columns.timeNote")]}
          rows={(via?.policyHistory || []).slice(0, 16).map((item, index) => [
            <div key={`policy-history-${index}`}>
              <div className="table-primary">{String(item.policy_key || item.version_key || t("admin.via.detail.policyFallback"))}</div>
              <small>{String(item.version_label || item.version_key || t("admin.via.detail.versionFallback"))}</small>
            </div>,
            String(item.version_key || item.version_label || t("admin.shared.missing")),
            String(item.source || item.audit_actor || item.target || t("admin.via.history.system")),
            <StatusPill key={`policy-history-status-${index}`} label={String(item.status || t("admin.shared.draft"))} tone={toneForStatus(String(item.status || ""))} />,
            `${formatDate(item.created_at || item.updated_at || "")} · ${String(item.note || item.rollout_note || t("admin.via.history.noNote"))}`,
          ])}
          empty={t("admin.via.history.empty")}
        />
      </Panel>
    </div>
  );
}
