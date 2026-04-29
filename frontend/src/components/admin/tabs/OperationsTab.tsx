import type { Dispatch, FormEvent, SetStateAction } from "react";
import { useTranslation } from "react-i18next";

import type { AdminSubmission } from "../../../lib/api";
import type {
  AdminOperationsSnapshot,
  AdminProductsSnapshot,
  AdminRedemptionRecord,
  AdminSocialAccountRecord,
  AdminUserRecord,
  AdminVerificationRecord,
} from "../../../services/admin.service";
import { EmptyState, MetricStrip, Panel, StatusPill } from "../../ui";
import { compactNumber, DataTable, formatDate, formatDateTime, JsonInfoList, TablePager, titleCase, toneForStatus } from "../shared";

interface ManualSubmissionForm {
  platform: string;
  extracted_handle: string;
  url: string;
  title: string;
  detection_status: string;
  product_series: string;
  product_label: string;
  final_score: string;
  creator_score: string;
  overall_score: string;
  views: string;
  likes: string;
  comments: string;
  shares: string;
  recommendation: string;
  memo: string;
}

interface CorrectionForm {
  submission_id: string;
  correct_series: string;
  correct_label: string;
  note: string;
}

interface PointsForm {
  user_id: string;
  mode: string;
  amount: string;
  reason: string;
}

interface OperationsTabProps {
  command: { submissions: AdminSubmission[] } | null;
  operations: AdminOperationsSnapshot | null;
  products: AdminProductsSnapshot | null;
  operationsSearch: string;
  setOperationsSearch: Dispatch<SetStateAction<string>>;
  submissionStatusFilter: string;
  setSubmissionStatusFilter: Dispatch<SetStateAction<string>>;
  verificationStatusFilter: string;
  setVerificationStatusFilter: Dispatch<SetStateAction<string>>;
  filteredSubmissionRows: AdminSubmission[];
  reviewRowsPaged: AdminSubmission[];
  reviewPage: number;
  reviewTotalPages: number;
  setReviewPage: Dispatch<SetStateAction<number>>;
  manualSubmissionOpen: boolean;
  setManualSubmissionOpen: Dispatch<SetStateAction<boolean>>;
  manualSubmissionForm: ManualSubmissionForm;
  setManualSubmissionForm: Dispatch<SetStateAction<ManualSubmissionForm>>;
  submitManualSubmission: (event: FormEvent) => Promise<void>;
  selectedSubmissionRow: AdminSubmission | null;
  selectedSubmissionAnalysis: Record<string, unknown>;
  correctionForm: CorrectionForm;
  setCorrectionForm: Dispatch<SetStateAction<CorrectionForm>>;
  loadProducts: () => Promise<void>;
  busy: string;
  approveSubmission: (submissionId: number) => Promise<void>;
  rejectSubmission: (submissionId: number) => Promise<void>;
  reanalyzeSubmission: (submissionId: number) => Promise<void>;
  removeSubmission: (submissionId: number) => Promise<void>;
  setSelectedSubmissionId: Dispatch<SetStateAction<string>>;
  submitCorrection: (event: FormEvent, submissionId?: number) => Promise<void>;
  pendingUsers: AdminUserRecord[];
  pendingUsersPaged: AdminUserRecord[];
  pendingUserPage: number;
  pendingUserTotalPages: number;
  setPendingUserPage: Dispatch<SetStateAction<number>>;
  selectedUserRow: AdminUserRecord | null;
  selectedUserSocialRows: AdminSocialAccountRecord[];
  selectedUserRedemptions: AdminRedemptionRecord[];
  selectedUserPointRows: Array<Record<string, unknown>>;
  setSelectedUserId: Dispatch<SetStateAction<string>>;
  userAction: (userId: number, action: "approve" | "reject") => Promise<void>;
  verificationRows: AdminVerificationRecord[];
  verificationRowsPaged: AdminVerificationRecord[];
  verificationPage: number;
  verificationTotalPages: number;
  setVerificationPage: Dispatch<SetStateAction<number>>;
  verificationAction: (verificationId: number, action: "approve" | "reject") => Promise<void>;
  socialAction: (accountId: number, action: "verify" | "reject") => Promise<void>;
  redemptionAction: (redemptionId: number, status: "fulfilled") => Promise<void>;
  pointsForm: PointsForm;
  setPointsForm: Dispatch<SetStateAction<PointsForm>>;
  submitPoints: (event: FormEvent) => Promise<void>;
}

export function OperationsTab(props: OperationsTabProps) {
  const { t } = useTranslation();
  const {
    operations,
    products,
    operationsSearch,
    setOperationsSearch,
    submissionStatusFilter,
    setSubmissionStatusFilter,
    verificationStatusFilter,
    setVerificationStatusFilter,
    filteredSubmissionRows,
    reviewRowsPaged,
    reviewPage,
    reviewTotalPages,
    setReviewPage,
    manualSubmissionOpen,
    setManualSubmissionOpen,
    manualSubmissionForm,
    setManualSubmissionForm,
    submitManualSubmission,
    selectedSubmissionRow,
    selectedSubmissionAnalysis,
    correctionForm,
    setCorrectionForm,
    loadProducts,
    busy,
    approveSubmission,
    rejectSubmission,
    reanalyzeSubmission,
    removeSubmission,
    setSelectedSubmissionId,
    submitCorrection,
    pendingUsers,
    pendingUsersPaged,
    pendingUserPage,
    pendingUserTotalPages,
    setPendingUserPage,
    selectedUserRow,
    selectedUserSocialRows,
    selectedUserRedemptions,
    selectedUserPointRows,
    setSelectedUserId,
    userAction,
    verificationRows,
    verificationRowsPaged,
    verificationPage,
    verificationTotalPages,
    setVerificationPage,
    verificationAction,
    socialAction,
    redemptionAction,
    pointsForm,
    setPointsForm,
    submitPoints,
  } = props;
  const verifiedVerificationCount =
    Number(operations?.verifyStats?.verified || 0) + Number(operations?.verifyStats?.approved_override || 0);
  const manualSubmissionFields: Array<keyof ManualSubmissionForm> = [
    "final_score",
    "creator_score",
    "overall_score",
    "views",
    "likes",
    "comments",
    "shares",
  ];
  const manualSubmissionFieldLabels: Partial<Record<keyof ManualSubmissionForm, string>> = {
    final_score: t("admin.operations.submissionCommand.fields.finalScore"),
    creator_score: t("admin.operations.submissionCommand.fields.creatorScore"),
    overall_score: t("admin.operations.submissionCommand.fields.overallScore"),
    views: t("admin.operations.submissionCommand.fields.views"),
    likes: t("admin.operations.submissionCommand.fields.likes"),
    comments: t("admin.operations.submissionCommand.fields.comments"),
    shares: t("admin.operations.submissionCommand.fields.shares"),
  };

  return (
    <div className="admin-workspace-grid">
      <Panel title={t("admin.operations.filters.title")} kicker={t("admin.operations.filters.kicker")}>
        <div className="admin-filter-grid">
          <label className="auth-field">
            <span>{t("admin.operations.filters.searchLabel")}</span>
            <input value={operationsSearch} onChange={(event) => setOperationsSearch(event.target.value)} placeholder={t("admin.operations.filters.searchPlaceholder")} />
          </label>
          <label className="auth-field">
            <span>{t("admin.operations.filters.submissionStatus")}</span>
            <select value={submissionStatusFilter} onChange={(event) => setSubmissionStatusFilter(event.target.value)}>
              <option value="all">{t("admin.operations.filters.all")}</option>
              <option value="pending">{t("admin.shared.pending")}</option>
              <option value="confirmed">{t("admin.shared.confirmed")}</option>
              <option value="approved">{t("admin.shared.approved")}</option>
              <option value="rejected">{t("admin.shared.rejected")}</option>
              <option value="suspected">{t("admin.shared.suspected")}</option>
            </select>
          </label>
          <label className="auth-field">
            <span>{t("admin.operations.filters.verificationStatus")}</span>
            <select value={verificationStatusFilter} onChange={(event) => setVerificationStatusFilter(event.target.value)}>
              <option value="all">{t("admin.operations.filters.all")}</option>
              <option value="pending">{t("admin.shared.pending")}</option>
              <option value="needs_review">{t("admin.operations.filters.needsReview")}</option>
              <option value="verified">{t("admin.shared.verified")}</option>
              <option value="failed">{t("admin.operations.filters.failed")}</option>
              <option value="rejected">{t("admin.shared.rejected")}</option>
            </select>
          </label>
        </div>
        <div className="admin-inline-actions">
          <span className="admin-chip">{t("admin.operations.filters.reviewQueue", { count: filteredSubmissionRows.length })}</span>
          <span className="admin-chip">{t("admin.operations.filters.users", { count: pendingUsers.length })}</span>
          <span className="admin-chip">{t("admin.operations.filters.verifications", { count: verificationRows.length })}</span>
        </div>
      </Panel>

      <Panel title={t("admin.operations.submissionCommand.title")} kicker={t("admin.operations.submissionCommand.kicker")}>
        <div className="admin-inline-actions admin-inline-actions--stretch">
          <button className="primary-button" type="button" onClick={() => setManualSubmissionOpen((current) => !current)}>
            {manualSubmissionOpen ? t("admin.operations.submissionCommand.closeManual") : t("admin.operations.submissionCommand.manualAdd")}
          </button>
          <button
            className="ghost-button"
            type="button"
            onClick={() =>
              selectedSubmissionRow &&
              setCorrectionForm((current) => ({
                ...current,
                submission_id: String(selectedSubmissionRow.id),
                correct_series: String(selectedSubmissionRow.product_series || current.correct_series || ""),
                correct_label: String(selectedSubmissionRow.product_label || current.correct_label || ""),
              }))
            }
          >
            {t("admin.operations.submissionCommand.prefillCorrection")}
          </button>
          <button className="ghost-button" type="button" onClick={() => void loadProducts()}>
            {t("admin.operations.submissionCommand.refreshCatalog")}
          </button>
        </div>
        {manualSubmissionOpen ? (
          <form className="admin-form-grid admin-form-grid--dense" onSubmit={submitManualSubmission}>
            <label className="auth-field">
              <span>{t("admin.operations.submissionCommand.fields.platform")}</span>
              <select value={manualSubmissionForm.platform} onChange={(event) => setManualSubmissionForm((current) => ({ ...current, platform: event.target.value }))}>
                <option value="Instagram">Instagram</option>
                <option value="TikTok">TikTok</option>
                <option value="YouTube">YouTube</option>
                <option value="Facebook">Facebook</option>
                <option value="Reddit">Reddit</option>
                <option value="Uploaded Video">Uploaded Video</option>
              </select>
            </label>
            <label className="auth-field">
              <span>{t("admin.operations.submissionCommand.fields.creatorHandle")}</span>
                <input value={manualSubmissionForm.extracted_handle} onChange={(event) => setManualSubmissionForm((current) => ({ ...current, extracted_handle: event.target.value }))} placeholder={t("admin.operations.submissionCommand.creatorPlaceholder")} />
            </label>
            <label className="auth-field">
              <span>{t("admin.operations.submissionCommand.fields.status")}</span>
              <select value={manualSubmissionForm.detection_status} onChange={(event) => setManualSubmissionForm((current) => ({ ...current, detection_status: event.target.value }))}>
                <option value="confirmed">{t("admin.shared.confirmed")}</option>
                <option value="suspected">{t("admin.shared.suspected")}</option>
                <option value="not_detected">{t("admin.operations.submissionCommand.fields.notDetected")}</option>
              </select>
            </label>
            <label className="auth-field admin-form-grid__full">
              <span>{t("admin.operations.submissionCommand.fields.urlSource")}</span>
                <input value={manualSubmissionForm.url} onChange={(event) => setManualSubmissionForm((current) => ({ ...current, url: event.target.value }))} placeholder={t("admin.operations.submissionCommand.urlPlaceholder")} />
            </label>
            <label className="auth-field admin-form-grid__full">
              <span>{t("admin.operations.submissionCommand.fields.title")}</span>
                <input value={manualSubmissionForm.title} onChange={(event) => setManualSubmissionForm((current) => ({ ...current, title: event.target.value }))} placeholder={t("admin.operations.submissionCommand.titlePlaceholder")} />
            </label>
            <label className="auth-field">
              <span>{t("admin.operations.submissionCommand.fields.series")}</span>
                <input value={manualSubmissionForm.product_series} onChange={(event) => setManualSubmissionForm((current) => ({ ...current, product_series: event.target.value }))} placeholder={t("admin.operations.submissionCommand.seriesPlaceholder")} />
            </label>
            <label className="auth-field">
              <span>{t("admin.operations.submissionCommand.fields.label")}</span>
                <input value={manualSubmissionForm.product_label} onChange={(event) => setManualSubmissionForm((current) => ({ ...current, product_label: event.target.value }))} placeholder={t("admin.operations.submissionCommand.labelPlaceholder")} />
            </label>
            <label className="auth-field">
              <span>{t("admin.operations.submissionCommand.fields.recommendation")}</span>
              <input value={manualSubmissionForm.recommendation} onChange={(event) => setManualSubmissionForm((current) => ({ ...current, recommendation: event.target.value }))} />
            </label>
            {manualSubmissionFields.map((field) => (
              <label key={field} className="auth-field">
                <span>{manualSubmissionFieldLabels[field] || field.replace(/_/g, " ")}</span>
                <input type="number" min={0} value={manualSubmissionForm[field]} onChange={(event) => setManualSubmissionForm((current) => ({ ...current, [field]: event.target.value }))} />
              </label>
            ))}
            <label className="auth-field admin-form-grid__full">
              <span>{t("admin.operations.submissionCommand.fields.adminMemo")}</span>
              <textarea rows={3} value={manualSubmissionForm.memo} onChange={(event) => setManualSubmissionForm((current) => ({ ...current, memo: event.target.value }))} />
            </label>
            <div className="auth-actions">
              <button className="primary-button" type="submit" disabled={busy === "operations:manual-submission"}>
                {busy === "operations:manual-submission" ? t("admin.operations.submissionCommand.creating") : t("admin.operations.submissionCommand.create")}
              </button>
            </div>
          </form>
        ) : (
          <p className="section-note">{t("admin.operations.submissionCommand.note")}</p>
        )}
      </Panel>

      <Panel title={t("admin.operations.reviewQueue.title")} kicker={t("admin.operations.reviewQueue.kicker")}>
        <TablePager page={reviewPage} totalPages={reviewTotalPages} totalItems={filteredSubmissionRows.length} label={t("admin.operations.reviewQueue.label")} onChange={setReviewPage} />
        <DataTable
          columns={[
            t("admin.operations.reviewQueue.columns.submission"),
            t("admin.operations.reviewQueue.columns.status"),
            t("admin.operations.reviewQueue.columns.score"),
            t("admin.operations.reviewQueue.columns.creator"),
            t("admin.operations.reviewQueue.columns.date"),
            t("admin.operations.reviewQueue.columns.actions"),
          ]}
          rows={reviewRowsPaged.map((item) => [
            <div key={`${item.id}-submission`}>
              <div className="table-primary">{item.title || t("admin.operations.reviewQueue.fallbackTitle", { id: item.id })}</div>
              <small>
                  {item.platform || t("admin.operations.reviewQueue.unknownPlatform")} · {item.product_label || item.product_series || t("admin.operations.reviewQueue.noProduct")}
              </small>
            </div>,
            <StatusPill key={`${item.id}-submission-status`} label={item.detection_status || t("admin.shared.pending")} tone={toneForStatus(item.detection_status || "")} />,
            compactNumber(item.overall_score || item.final_score || 0),
            item.creator_code || item.display_name || item.extracted_handle || t("admin.operations.reviewQueue.unassigned"),
            formatDate(item.created_at || ""),
            <div key={`${item.id}-submission-actions`} className="table-actions">
              <button
                className="outline-btn"
                type="button"
                onClick={() => {
                  setSelectedSubmissionId(String(item.id));
                  setCorrectionForm((current) => ({
                    ...current,
                    submission_id: String(item.id),
                    correct_series: String(item.product_series || current.correct_series || ""),
                    correct_label: String(item.product_label || current.correct_label || ""),
                  }));
                }}
              >
                {t("admin.operations.actions.inspect")}
              </button>
              <button className="outline-btn" type="button" disabled={busy === `operations:submission:${item.id}:approve`} onClick={() => void approveSubmission(item.id)}>
                {t("admin.operations.actions.approve")}
              </button>
              <button className="outline-btn" type="button" disabled={busy === `operations:submission:${item.id}:reject`} onClick={() => void rejectSubmission(item.id)}>
                {t("admin.operations.actions.reject")}
              </button>
              <button className="outline-btn" type="button" disabled={busy === `operations:submission:${item.id}:reanalyze`} onClick={() => void reanalyzeSubmission(item.id)}>
                {t("admin.operations.actions.reanalyze")}
              </button>
              <button className="outline-btn outline-btn--danger" type="button" disabled={busy === `operations:submission:${item.id}:delete`} onClick={() => void removeSubmission(item.id)}>
                {t("admin.operations.actions.delete")}
              </button>
            </div>,
          ])}
          empty={t("admin.operations.reviewQueue.empty")}
        />
      </Panel>

      <Panel title={t("admin.operations.submissionDetail.title")} kicker={t("admin.operations.submissionDetail.kicker")}>
        {selectedSubmissionRow ? (
          <div className="admin-two-column">
            <div className="admin-list-stack">
              <article className="admin-list-item">
                <strong>{String(selectedSubmissionRow.title || t("admin.operations.reviewQueue.fallbackTitle", { id: selectedSubmissionRow.id }))}</strong>
                <p>
                  #{selectedSubmissionRow.id} · {String(selectedSubmissionRow.platform || t("admin.shared.platform"))} ·{" "}
                  {String(selectedSubmissionRow.creator_code || selectedSubmissionRow.display_name || selectedSubmissionRow.extracted_handle || t("admin.shared.creator"))}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.operations.submissionDetail.statusScores")}</strong>
                <p>
                  {t("admin.operations.submissionDetail.statusScoreLine", {
                    status: String(selectedSubmissionRow.detection_status || t("admin.shared.pending")),
                    campaign: compactNumber(selectedSubmissionRow.final_score || 0),
                    creator: compactNumber(selectedSubmissionRow.creator_score || 0),
                    overall: compactNumber(selectedSubmissionRow.overall_score || 0),
                  })}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.operations.submissionDetail.trafficPayout")}</strong>
                <p>
                  {t("admin.operations.submissionDetail.trafficLine", {
                    views: compactNumber(selectedSubmissionRow.views || 0),
                    likes: compactNumber(selectedSubmissionRow.likes || 0),
                    comments: compactNumber(selectedSubmissionRow.comments || 0),
                    shares: compactNumber(selectedSubmissionRow.shares || 0),
                  })}
                </p>
                <p>
                  {t("admin.operations.submissionDetail.pointsLine", {
                    points: compactNumber(selectedSubmissionRow.points_awarded || 0),
                    status: String(selectedSubmissionRow.points_status || t("admin.shared.pending")),
                  })}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.operations.submissionDetail.productMemo")}</strong>
                <p>
                  {String(selectedSubmissionRow.product_series || "—")} · {String(selectedSubmissionRow.product_label || "—")}
                </p>
                <p>{String(selectedSubmissionRow.memo || selectedSubmissionRow.recommendation || t("admin.operations.submissionDetail.noMemo"))}</p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.operations.submissionDetail.aiSnapshot")}</strong>
                <p>
                  {t("admin.operations.submissionDetail.aiGenreLine", {
                    genre: String(selectedSubmissionRow.content_genre || selectedSubmissionAnalysis.content_genre || "—"),
                    vertical: String(selectedSubmissionRow.vertical_category || selectedSubmissionAnalysis.vertical_category || "—"),
                  })}
                </p>
                <p>
                  {t("admin.operations.submissionDetail.aiScoreLine", {
                    tech: compactNumber(selectedSubmissionRow.tech_score || selectedSubmissionAnalysis.tech_score || 0),
                    marketing: compactNumber(selectedSubmissionRow.marketing_score || selectedSubmissionAnalysis.marketing_score || 0),
                    confidence: String(selectedSubmissionAnalysis.confidence || "—"),
                  })}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.operations.submissionDetail.evidenceLayers")}</strong>
                <p>
                  {((selectedSubmissionAnalysis.layers_used as Array<unknown> | undefined) || [])
                    .slice(0, 4)
                    .map((item) => String(item))
                    .join(" · ") || t("admin.operations.submissionDetail.noLayerHistory")}
                </p>
                <p>{String(selectedSubmissionAnalysis.notes || selectedSubmissionAnalysis.error || t("admin.operations.submissionDetail.noVisionNotes"))}</p>
              </article>
              <div className="table-actions">
                <button className="outline-btn" type="button" disabled={busy === `operations:submission:${selectedSubmissionRow.id}:approve`} onClick={() => void approveSubmission(selectedSubmissionRow.id)}>
                  {t("admin.operations.actions.approveAward")}
                </button>
                <button className="outline-btn" type="button" disabled={busy === `operations:submission:${selectedSubmissionRow.id}:reject`} onClick={() => void rejectSubmission(selectedSubmissionRow.id)}>
                  {t("admin.operations.actions.reject")}
                </button>
                <button className="outline-btn" type="button" disabled={busy === `operations:submission:${selectedSubmissionRow.id}:reanalyze`} onClick={() => void reanalyzeSubmission(selectedSubmissionRow.id)}>
                  {t("admin.operations.actions.reanalyze")}
                </button>
                <button className="outline-btn outline-btn--danger" type="button" disabled={busy === `operations:submission:${selectedSubmissionRow.id}:delete`} onClick={() => void removeSubmission(selectedSubmissionRow.id)}>
                  {t("admin.operations.actions.delete")}
                </button>
                {selectedSubmissionRow.url ? (
                  <a className="outline-btn" href={String(selectedSubmissionRow.url)} target="_blank" rel="noreferrer">
                    {t("admin.operations.actions.openSource")}
                  </a>
                ) : null}
              </div>
            </div>
            <div className="admin-list-stack">
              <article className="admin-list-item">
                <strong>{t("admin.operations.submissionDetail.payloadDetail")}</strong>
                <p>
                  {t("admin.operations.submissionDetail.payloadLine", {
                    camera: String((selectedSubmissionAnalysis.prefilter as Record<string, unknown> | undefined)?.camera_body || selectedSubmissionAnalysis.camera_body || "—"),
                    lens: String((selectedSubmissionAnalysis.prefilter as Record<string, unknown> | undefined)?.viltrox_lens || selectedSubmissionAnalysis.viltrox_lens || "—"),
                  })}
                </p>
                <p>
                  Products{" "}
                  {((selectedSubmissionAnalysis.products_detected as Array<unknown> | undefined) || [])
                    .slice(0, 4)
                    .map((item) => String(item))
                    .join(" · ") || t("admin.operations.submissionDetail.noStructuredProductHits")}
                </p>
              </article>
              <form className="admin-form-grid" onSubmit={(event) => void submitCorrection(event, selectedSubmissionRow.id)}>
                <label className="auth-field">
                  <span>{t("admin.operations.submissionCommand.fields.submission")}</span>
                  <input value={`#${selectedSubmissionRow.id}`} disabled />
                </label>
                <label className="auth-field">
                  <span>{t("admin.products.correction.correctSeries")}</span>
                  <input
                    value={correctionForm.submission_id === String(selectedSubmissionRow.id) ? correctionForm.correct_series : String(selectedSubmissionRow.product_series || "")}
                    onChange={(event) =>
                      setCorrectionForm((current) => ({
                        ...current,
                        submission_id: String(selectedSubmissionRow.id),
                        correct_series: event.target.value,
                      }))
                    }
                  />
                </label>
                <label className="auth-field">
                  <span>{t("admin.products.correction.correctLabel")}</span>
                  <input
                    value={correctionForm.submission_id === String(selectedSubmissionRow.id) ? correctionForm.correct_label : String(selectedSubmissionRow.product_label || "")}
                    onChange={(event) =>
                      setCorrectionForm((current) => ({
                        ...current,
                        submission_id: String(selectedSubmissionRow.id),
                        correct_label: event.target.value,
                      }))
                    }
                  />
                </label>
                <label className="auth-field admin-form-grid__full">
                  <span>{t("admin.operations.submissionDetail.catalogQuickPick")}</span>
                  <select
                    value=""
                    onChange={(event) => {
                      const value = event.target.value;
                      if (!value) return;
                      const [series, label] = value.split("||");
                      setCorrectionForm((current) => ({
                        ...current,
                        submission_id: String(selectedSubmissionRow.id),
                        correct_series: series || "",
                        correct_label: label || "",
                      }));
                      event.currentTarget.value = "";
                    }}
                  >
                    <option value="">{t("admin.operations.submissionDetail.pickFromCatalog")}</option>
                    {(products?.catalog || []).slice(0, 200).map((item, index) => (
                      <option key={`submission-catalog-${index}`} value={`${String(item.series || "")}||${String(item.label || "")}`}>
                        {String(item.series || "—")} / {String(item.label || "—")}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="auth-field admin-form-grid__full">
                  <span>{t("admin.operations.submissionDetail.note")}</span>
                  <textarea
                    rows={4}
                    value={correctionForm.submission_id === String(selectedSubmissionRow.id) ? correctionForm.note : ""}
                    onChange={(event) =>
                      setCorrectionForm((current) => ({
                        ...current,
                        submission_id: String(selectedSubmissionRow.id),
                        note: event.target.value,
                      }))
                    }
                    placeholder={t("admin.operations.submissionDetail.notePlaceholder")}
                  />
                </label>
                <div className="auth-actions">
                  <button className="primary-button" type="submit" disabled={busy === "products:correction"}>
                    {busy === "products:correction" ? t("admin.operations.submissionDetail.saving") : t("admin.operations.submissionDetail.saveCorrection")}
                  </button>
                </div>
              </form>
            </div>
          </div>
        ) : (
          <EmptyState title={t("admin.operations.submissionDetail.emptyTitle")} body={t("admin.operations.submissionDetail.emptyBody")} />
        )}
      </Panel>

      <Panel title={t("admin.operations.pendingUsers.title")} kicker={t("admin.operations.pendingUsers.kicker")}>
        <TablePager page={pendingUserPage} totalPages={pendingUserTotalPages} totalItems={pendingUsers.length} label={t("admin.operations.pendingUsers.label")} onChange={setPendingUserPage} />
        <DataTable
          columns={[
            t("admin.operations.pendingUsers.columns.creator"),
            t("admin.operations.pendingUsers.columns.status"),
            t("admin.operations.pendingUsers.columns.balance"),
            t("admin.operations.pendingUsers.columns.actions"),
          ]}
          rows={pendingUsersPaged.map((userRow) => [
            <div key={`${userRow.id}-creator`}>
              <div className="table-primary">{String(userRow.name || userRow.email || `User #${userRow.id}`)}</div>
              <small>{String(userRow.creator_code || t("admin.operations.pendingUsers.noCreatorCode"))}</small>
            </div>,
            <StatusPill key={`${userRow.id}-status`} label={String(userRow.status || t("admin.shared.pending"))} tone={toneForStatus(String(userRow.status || ""))} />,
            compactNumber(userRow.points_balance || 0),
            <div key={`${userRow.id}-actions`} className="table-actions">
              <button className="outline-btn" type="button" onClick={() => setSelectedUserId(String(userRow.id))}>
                {t("admin.operations.actions.inspect")}
              </button>
              <button className="outline-btn" type="button" disabled={busy === `operations:user:${userRow.id}:approve`} onClick={() => void userAction(Number(userRow.id), "approve")}>
                {t("admin.operations.actions.approve")}
              </button>
              <button className="outline-btn outline-btn--danger" type="button" disabled={busy === `operations:user:${userRow.id}:reject`} onClick={() => void userAction(Number(userRow.id), "reject")}>
                {t("admin.operations.actions.reject")}
              </button>
            </div>,
          ])}
          empty={t("admin.operations.pendingUsers.empty")}
        />
      </Panel>

      <Panel title={t("admin.operations.userDetail.title")} kicker={t("admin.operations.userDetail.kicker")}>
        {selectedUserRow ? (
          <div className="admin-two-column">
            <div className="admin-list-stack">
              <article className="admin-list-item">
                <strong>{String(selectedUserRow.name || selectedUserRow.email || `User #${selectedUserRow.id}`)}</strong>
                <p>
                  #{String(selectedUserRow.id)} · {String(selectedUserRow.email || t("admin.operations.userDetail.noEmail"))} · {String(selectedUserRow.creator_code || t("admin.operations.userDetail.noCreatorCode"))}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.operations.userDetail.statusBalances")}</strong>
                <p>
                  {t("admin.operations.userDetail.statusBalanceLine", {
                    status: String(selectedUserRow.status || t("admin.shared.pending")),
                    role: String(selectedUserRow.role || t("admin.shared.creator")),
                    balance: compactNumber(selectedUserRow.points_balance || 0),
                    total: compactNumber(selectedUserRow.points_total || 0),
                  })}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.operations.userDetail.activity")}</strong>
                <p>
                  {t("admin.operations.userDetail.activityLine", {
                    created: formatDateTime(selectedUserRow.created_at || ""),
                    lastLogin: formatDateTime(selectedUserRow.last_login || ""),
                  })}
                </p>
                <p>{String(selectedUserRow.note || t("admin.operations.userDetail.noAdminNote"))}</p>
              </article>
              <div className="table-actions">
                <button className="outline-btn" type="button" disabled={busy === `operations:user:${selectedUserRow.id}:approve`} onClick={() => void userAction(Number(selectedUserRow.id), "approve")}>
                  {t("admin.operations.actions.approve")}
                </button>
                <button className="outline-btn outline-btn--danger" type="button" disabled={busy === `operations:user:${selectedUserRow.id}:reject`} onClick={() => void userAction(Number(selectedUserRow.id), "reject")}>
                  {t("admin.operations.actions.reject")}
                </button>
              </div>
            </div>
            <div className="admin-list-stack">
              <article className="admin-list-item">
                <strong>{t("admin.operations.userDetail.linkedPlatforms")}</strong>
                <p>
                  {selectedUserSocialRows.length
                    ? selectedUserSocialRows.slice(0, 4).map((item) => `${String(item.platform || t("admin.shared.platform"))}:${String(item.handle || t("admin.shared.handle"))}`).join(" · ")
                    : t("admin.operations.userDetail.noLinkedPlatforms")}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.operations.userDetail.recentRedemptions")}</strong>
                <p>
                  {selectedUserRedemptions.length
                    ? selectedUserRedemptions.slice(0, 3).map((item) => `${String(item.item_name || t("admin.shared.reward"))} (${String(item.status || t("admin.shared.pending"))})`).join(" · ")
                    : t("admin.operations.userDetail.noRedemptions")}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.operations.userDetail.recentPointsOps")}</strong>
                <p>
                  {selectedUserPointRows.length
                    ? selectedUserPointRows.slice(0, 3).map((item) => `${compactNumber(item.delta || 0)}:${String(item.reason || t("admin.shared.points"))}`).join(" · ")
                    : t("admin.operations.userDetail.noPointOps")}
                </p>
              </article>
            </div>
          </div>
        ) : (
          <EmptyState title={t("admin.operations.userDetail.emptyTitle")} body={t("admin.operations.userDetail.emptyBody")} />
        )}
      </Panel>

      <Panel title={t("admin.operations.verificationQueue.title")} kicker={t("admin.operations.verificationQueue.kicker")}>
        <MetricStrip
          columns={4}
          items={[
            { label: t("admin.operations.verificationQueue.metrics.pending.label"), value: compactNumber(operations?.verifyStats?.pending || 0), note: t("admin.operations.verificationQueue.metrics.pending.note") },
            { label: t("admin.operations.verificationQueue.metrics.needsReview.label"), value: compactNumber(operations?.verifyStats?.needs_review || 0), note: t("admin.operations.verificationQueue.metrics.needsReview.note") },
            { label: t("admin.operations.verificationQueue.metrics.verified.label"), value: compactNumber(verifiedVerificationCount), note: t("admin.operations.verificationQueue.metrics.verified.note") },
            { label: t("admin.operations.verificationQueue.metrics.failed.label"), value: compactNumber(operations?.verifyStats?.failed || 0), note: t("admin.operations.verificationQueue.metrics.failed.note") },
          ]}
        />
        <TablePager page={verificationPage} totalPages={verificationTotalPages} totalItems={verificationRows.length} label={t("admin.operations.verificationQueue.label")} onChange={setVerificationPage} />
        <DataTable
          columns={[
            t("admin.operations.verificationQueue.columns.platform"),
            t("admin.operations.verificationQueue.columns.handle"),
            t("admin.operations.verificationQueue.columns.status"),
            t("admin.operations.verificationQueue.columns.actions"),
          ]}
          rows={verificationRowsPaged.map((item) => [
            <div key={`${item.id}-platform`}>
              <div className="table-primary">{titleCase(String(item.platform || t("admin.shared.platform")))}</div>
              <small>{String(item.generated_comment || t("admin.operations.verificationQueue.generated"))}</small>
            </div>,
            String(item.handle || "—"),
            <StatusPill key={`${item.id}-status`} label={String(item.status || t("admin.shared.pending"))} tone={toneForStatus(String(item.status || ""))} />,
            <div key={`${item.id}-verify-actions`} className="table-actions">
              <button className="outline-btn" type="button" disabled={busy === `operations:verification:${item.id}:approve`} onClick={() => void verificationAction(Number(item.id), "approve")}>
                {t("admin.operations.actions.approve")}
              </button>
              <button className="outline-btn outline-btn--danger" type="button" disabled={busy === `operations:verification:${item.id}:reject`} onClick={() => void verificationAction(Number(item.id), "reject")}>
                {t("admin.operations.actions.reject")}
              </button>
            </div>,
          ])}
          empty={t("admin.operations.verificationQueue.empty")}
        />
      </Panel>

      <Panel title={t("admin.operations.linkedPlatforms.title")} kicker={t("admin.operations.linkedPlatforms.kicker")}>
        <DataTable
          columns={[
            t("admin.operations.linkedPlatforms.columns.platform"),
            t("admin.operations.linkedPlatforms.columns.creator"),
            t("admin.operations.linkedPlatforms.columns.status"),
            t("admin.operations.linkedPlatforms.columns.action"),
          ]}
          rows={(operations?.socials || []).slice(0, 8).map((item) => [
            `${titleCase(String(item.platform || t("admin.shared.platform")))} · ${String(item.handle || t("admin.operations.linkedPlatforms.unknownHandle"))}`,
            String(item.user_name || item.email || t("admin.operations.linkedPlatforms.creatorProfilePending")),
            <StatusPill key={`${item.id}-social-status`} label={item.verified ? t("admin.shared.verified") : t("admin.shared.pending")} tone={item.verified ? "success" : "warning"} />,
            <div key={`${item.id}-social-action`} className="table-actions">
              <button className="outline-btn outline-btn--danger" type="button" disabled={busy === `operations:social:${item.id}:reject`} onClick={() => void socialAction(Number(item.id), "reject")}>
                {t("admin.operations.actions.remove")}
              </button>
            </div>,
          ])}
          empty={t("admin.operations.linkedPlatforms.empty")}
        />
      </Panel>

      <Panel title={t("admin.operations.redemptionsAffiliate.title")} kicker={t("admin.operations.redemptionsAffiliate.kicker")}>
        <div className="admin-two-column">
          <div>
            <strong className="section-mini-head">{t("admin.operations.redemptionsAffiliate.recentTitle")}</strong>
            <DataTable
              columns={[
                t("admin.operations.redemptionsAffiliate.columns.item"),
                t("admin.operations.redemptionsAffiliate.columns.status"),
                t("admin.operations.redemptionsAffiliate.columns.points"),
                t("admin.operations.redemptionsAffiliate.columns.action"),
              ]}
              rows={(operations?.redemptions || []).slice(0, 6).map((item) => [
                String(item.item_name || `${t("admin.operations.redemptionsAffiliate.redemptionFallback")} #${item.id}`),
                <StatusPill key={`${item.id}-status`} label={String(item.status || t("admin.shared.queued"))} tone={toneForStatus(String(item.status || ""))} />,
                compactNumber(item.points_cost || 0),
                <div key={`${item.id}-redeem-action`} className="table-actions">
                  <button className="outline-btn" type="button" disabled={busy === `operations:redemption:${item.id}:fulfilled`} onClick={() => void redemptionAction(Number(item.id), "fulfilled")}>
                    {t("admin.operations.actions.fulfill")}
                  </button>
                </div>,
              ])}
              empty={t("admin.operations.redemptionsAffiliate.empty")}
            />
          </div>
          <div>
            <strong className="section-mini-head">{t("admin.operations.redemptionsAffiliate.affiliateTitle")}</strong>
            <JsonInfoList payload={operations?.affiliate || null} />
          </div>
        </div>
      </Panel>

      <Panel title={t("admin.operations.pointsOps.title")} kicker={t("admin.operations.pointsOps.kicker")}>
        <div className="admin-two-column">
          <form className="admin-form-grid" onSubmit={submitPoints}>
            <label className="auth-field">
              <span>{t("admin.operations.pointsOps.fields.user")}</span>
              <select value={pointsForm.user_id} onChange={(event) => setPointsForm((current) => ({ ...current, user_id: event.target.value }))}>
                <option value="">{t("admin.operations.pointsOps.selectUser")}</option>
                {(operations?.users || []).slice(0, 60).map((item) => (
                  <option key={String(item.id)} value={String(item.id)}>
                    {String(item.creator_code || item.email || `User #${item.id}`)}
                  </option>
                ))}
              </select>
            </label>
            <label className="auth-field">
              <span>{t("admin.operations.pointsOps.fields.mode")}</span>
              <select value={pointsForm.mode} onChange={(event) => setPointsForm((current) => ({ ...current, mode: event.target.value }))}>
                <option value="grant">{t("admin.operations.pointsOps.modes.grant")}</option>
                <option value="adjust">{t("admin.operations.pointsOps.modes.adjust")}</option>
                <option value="deduct">{t("admin.operations.pointsOps.modes.deduct")}</option>
              </select>
            </label>
            <label className="auth-field">
              <span>{t("admin.operations.pointsOps.fields.amount")}</span>
              <input type="number" min={1} value={pointsForm.amount} onChange={(event) => setPointsForm((current) => ({ ...current, amount: event.target.value }))} />
            </label>
            <label className="auth-field admin-form-grid__full">
              <span>{t("admin.operations.pointsOps.fields.reason")}</span>
              <input value={pointsForm.reason} onChange={(event) => setPointsForm((current) => ({ ...current, reason: event.target.value }))} />
            </label>
            <div className="auth-actions">
              <button className="primary-button" type="submit" disabled={busy === "operations:points"}>
                {busy === "operations:points" ? t("admin.operations.pointsOps.updating") : t("admin.operations.pointsOps.run")}
              </button>
            </div>
          </form>
          <DataTable
            columns={[
              t("admin.operations.pointsOps.columns.when"),
              t("admin.operations.pointsOps.columns.user"),
              t("admin.operations.pointsOps.columns.delta"),
              t("admin.operations.pointsOps.columns.reason"),
            ]}
            rows={(operations?.pointsLog || []).slice(0, 10).map((item, index) => [
              formatDate(item.created_at || ""),
              String(item.email || item.user_id || `row-${index}`),
              compactNumber(item.delta || 0),
              String(item.reason || t("admin.operations.pointsOps.pointsChange")),
            ])}
            empty={t("admin.operations.pointsOps.empty")}
          />
        </div>
      </Panel>
    </div>
  );
}
