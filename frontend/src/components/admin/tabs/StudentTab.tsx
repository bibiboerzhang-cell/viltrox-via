import type { Dispatch, FormEvent, SetStateAction } from "react";
import { useTranslation } from "react-i18next";

import type { AdminStudentSnapshot, StudentRosterRecord } from "../../../services/admin.service";
import { EmptyState, MetricStrip, Panel, StatusPill } from "../../ui";
import { compactNumber, DataTable, formatDate, percentLabel, TablePager, titleCase, toneForStatus } from "../shared";

interface SchoolForm {
  school_id: string;
  school_code: string;
  school_name: string;
  region: string;
  country: string;
  partnership_status: string;
  primary_color: string;
  accent_color: string;
}

interface BatchForm {
  school_id: string;
  batch_name: string;
  count: string;
  roster_csv: string;
}

interface StudentTabProps {
  student: AdminStudentSnapshot | null;
  schoolForm: SchoolForm;
  setSchoolForm: Dispatch<SetStateAction<SchoolForm>>;
  batchForm: BatchForm;
  setBatchForm: Dispatch<SetStateAction<BatchForm>>;
  submitSchool: (event: FormEvent) => Promise<void>;
  submitBatch: (event: FormEvent) => Promise<void>;
  busy: string;
  studentSearch: string;
  setStudentSearch: Dispatch<SetStateAction<string>>;
  studentPage: number;
  studentTotalPages: number;
  studentRowsFiltered: StudentRosterRecord[];
  studentRowsPaged: StudentRosterRecord[];
  setStudentPage: Dispatch<SetStateAction<number>>;
  selectedStudentRow: StudentRosterRecord | null;
  setSelectedStudentId: Dispatch<SetStateAction<string>>;
  selectedStudentOps: Array<Record<string, unknown>>;
}

export function StudentTab({
  student,
  schoolForm,
  setSchoolForm,
  batchForm,
  setBatchForm,
  submitSchool,
  submitBatch,
  busy,
  studentSearch,
  setStudentSearch,
  studentPage,
  studentTotalPages,
  studentRowsFiltered,
  studentRowsPaged,
  setStudentPage,
  selectedStudentRow,
  setSelectedStudentId,
  selectedStudentOps,
}: StudentTabProps) {
  const { t } = useTranslation();
  const schoolFieldLabels: Array<{ key: keyof SchoolForm; label: string }> = [
    { key: "school_id", label: t("admin.student.schoolForm.fields.schoolId") },
    { key: "school_code", label: t("admin.student.schoolForm.fields.schoolCode") },
    { key: "school_name", label: t("admin.student.schoolForm.fields.schoolName") },
    { key: "region", label: t("admin.student.schoolForm.fields.region") },
    { key: "country", label: t("admin.student.schoolForm.fields.country") },
    { key: "partnership_status", label: t("admin.student.schoolForm.fields.partnership") },
    { key: "primary_color", label: t("admin.student.schoolForm.fields.primaryColor") },
    { key: "accent_color", label: t("admin.student.schoolForm.fields.accentColor") },
  ];

  return (
    <div className="admin-workspace-grid">
      <Panel title={t("admin.student.kpi.title")} kicker={t("admin.student.kpi.kicker")}>
        <MetricStrip
          columns={4}
          items={[
            { label: t("admin.student.kpi.metrics.schools.label"), value: compactNumber(student?.schools.length || 0), note: t("admin.student.kpi.metrics.schools.note") },
            { label: t("admin.student.kpi.metrics.students.label"), value: compactNumber(student?.overview?.students.length || 0), note: t("admin.student.kpi.metrics.students.note") },
            { label: t("admin.student.kpi.metrics.batches.label"), value: compactNumber(student?.overview?.batch_progress.length || 0), note: t("admin.student.kpi.metrics.batches.note") },
            { label: t("admin.student.kpi.metrics.anomalies.label"), value: compactNumber(student?.overview?.recent_anomalies.length || 0), note: t("admin.student.kpi.metrics.anomalies.note") },
          ]}
        />
      </Panel>

        <Panel title={t("admin.student.schoolForm.title")} kicker={t("admin.student.schoolForm.kicker")}>
          <form className="admin-form-grid" onSubmit={submitSchool}>
          {schoolFieldLabels.map(({ key, label }) => (
            <label key={key} className="auth-field">
              <span>{label}</span>
              <input
                value={schoolForm[key]}
                onChange={(event) => setSchoolForm((current) => ({ ...current, [key]: event.target.value }))}
                required={key === "school_id" || key === "school_code" || key === "school_name"}
              />
            </label>
          ))}
          <div className="auth-actions">
            <button className="primary-button" type="submit" disabled={busy === "student:school"}>
              {busy === "student:school" ? t("admin.student.schoolForm.saving") : t("admin.student.schoolForm.save")}
            </button>
          </div>
        </form>
      </Panel>

      <Panel title={t("admin.student.batchForm.title")} kicker={t("admin.student.batchForm.kicker")}>
        <form className="admin-form-grid" onSubmit={submitBatch}>
          <label className="auth-field">
            <span>{t("admin.student.batchForm.school")}</span>
            <select value={batchForm.school_id} onChange={(event) => setBatchForm((current) => ({ ...current, school_id: event.target.value }))}>
              <option value="">{t("admin.student.batchForm.selectSchool")}</option>
              {(student?.schools || []).map((school) => (
                <option key={school.school_id} value={school.school_id}>
                  {school.school_code || school.school_id} · {school.school_name || school.school_id}
                </option>
              ))}
            </select>
          </label>
          <label className="auth-field">
            <span>{t("admin.student.batchForm.batchName")}</span>
            <input value={batchForm.batch_name} onChange={(event) => setBatchForm((current) => ({ ...current, batch_name: event.target.value }))} required />
          </label>
          <label className="auth-field">
            <span>{t("admin.student.batchForm.count")}</span>
            <input type="number" min={1} value={batchForm.count} onChange={(event) => setBatchForm((current) => ({ ...current, count: event.target.value }))} />
          </label>
          <label className="auth-field admin-form-grid__full">
            <span>{t("admin.student.batchForm.rosterCsv")}</span>
            <textarea
              rows={6}
              value={batchForm.roster_csv}
              onChange={(event) => setBatchForm((current) => ({ ...current, roster_csv: event.target.value }))}
              placeholder={t("admin.student.batchForm.rosterPlaceholder")}
            />
          </label>
          <div className="auth-actions">
            <button className="primary-button" type="submit" disabled={busy === "student:batch"}>
              {busy === "student:batch" ? t("admin.student.batchForm.generating") : t("admin.student.batchForm.generate")}
            </button>
          </div>
        </form>
      </Panel>

      <Panel title={t("admin.student.funnel.title")} kicker={t("admin.student.funnel.kicker")}>
        <div className="admin-card-list">
          {(student?.schools || []).slice(0, 8).map((school) => (
            <article key={school.school_id} className="admin-mini-card">
              <strong>{school.school_name || school.school_id}</strong>
              <p>
                  {school.region || school.country || t("admin.student.funnel.regionPending")} · {school.partnership_status || t("admin.shared.pilot")}
                </p>
              <span>
                {compactNumber(school.activated_count || 0)} {t("admin.student.batchProgress.columns.activated").toLowerCase()} / {compactNumber(school.issued_count || 0)} {t("admin.student.batchProgress.columns.issued").toLowerCase()} · {percentLabel(school.activation_rate || 0)}
              </span>
            </article>
          ))}
        </div>
      </Panel>

      <Panel title={t("admin.student.batchProgress.title")} kicker={t("admin.student.batchProgress.kicker")}>
        <div className="admin-two-column">
          <DataTable
            columns={[t("admin.student.batchProgress.columns.batch"), t("admin.student.batchProgress.columns.issued"), t("admin.student.batchProgress.columns.activated"), t("admin.student.batchProgress.columns.rate")]}
            rows={(student?.overview?.batch_progress || []).slice(0, 8).map((item) => [
              `${item.school_id || t("admin.student.batchProgress.schoolFallback")} · ${item.batch_name || t("admin.student.batchProgress.batchFallback")}`,
              compactNumber(item.issued || 0),
              compactNumber(item.activated || 0),
              percentLabel(item.activation_rate || 0),
            ])}
            empty={t("admin.student.batchProgress.empty")}
          />
          <div className="admin-list-stack">
            {(student?.overview?.recent_events || []).slice(0, 6).map((item, index) => (
              <article key={index} className="admin-list-item">
                <strong>{titleCase(String(item.event_type || item.audit_type || t("admin.shared.event")))}</strong>
                  <p>{String(item.school_id || item.location || item.reason || t("admin.student.batchProgress.studentEvent"))}</p>
                </article>
              ))}
            </div>
          </div>
      </Panel>

      <Panel title={t("admin.student.roster.title")} kicker={t("admin.student.roster.kicker")}>
        <div className="admin-note-form">
          <label className="auth-field admin-form-grid__full">
            <span>{t("admin.student.roster.searchLabel")}</span>
            <input value={studentSearch} onChange={(event) => setStudentSearch(event.target.value)} placeholder={t("admin.student.roster.searchPlaceholder")} />
          </label>
        </div>
        <TablePager page={studentPage} totalPages={studentTotalPages} totalItems={studentRowsFiltered.length} label={t("admin.student.roster.pagerLabel")} onChange={setStudentPage} />
        <DataTable
          columns={[t("admin.student.roster.columns.student"), t("admin.student.roster.columns.school"), t("admin.student.roster.columns.studentId"), t("admin.student.roster.columns.creatorId"), t("admin.student.roster.columns.status"), t("admin.student.roster.columns.action")]}
          rows={studentRowsPaged.map((item) => [
            <div key={`student-${item.user_id}`}>
              <div className="table-primary">{String(item.name || item.email || t("admin.student.roster.studentFallback", { id: item.user_id }))}</div>
              <small>{String(item.email || t("admin.student.roster.noEmail"))}</small>
            </div>,
            String(item.school_name || item.school_id || t("admin.shared.missing")),
            String(item.student_id_code || t("admin.shared.missing")),
            String(item.creator_code || t("admin.shared.missing")),
            <StatusPill key={`student-status-${item.user_id}`} label={String(item.status || t("admin.shared.pending"))} tone={toneForStatus(String(item.status || ""))} />,
            <button className="outline-btn" type="button" onClick={() => setSelectedStudentId(String(item.user_id))}>
              {t("admin.operations.actions.inspect")}
            </button>,
          ])}
          empty={t("admin.student.roster.empty")}
        />
      </Panel>

      <Panel title={t("admin.student.detail.title")} kicker={t("admin.student.detail.kicker")}>
        {selectedStudentRow ? (
          <div className="admin-two-column">
            <div className="admin-list-stack">
              <article className="admin-list-item">
                <strong>{String(selectedStudentRow.name || selectedStudentRow.email || t("admin.student.roster.studentFallback", { id: selectedStudentRow.user_id }))}</strong>
                <p>
                  {String(selectedStudentRow.school_name || selectedStudentRow.school_id || t("admin.student.detail.schoolPending"))} · {String(selectedStudentRow.student_id_code || t("admin.student.detail.noStudentId"))}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.student.detail.creatorLane")}</strong>
                <p>{String(selectedStudentRow.creator_code || t("admin.student.detail.noCreatorCode"))} · {String(selectedStudentRow.status || t("admin.shared.pending"))}</p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.student.detail.schoolFunnel")}</strong>
                <p>
                  {(student?.schools || [])
                    .filter((item) => String(item.school_id || "") === String(selectedStudentRow.school_id || ""))
                    .slice(0, 1)
                    .map((item) => t("admin.student.detail.funnelLine", { activated: compactNumber(item.activated_count || 0), issued: compactNumber(item.issued_count || 0), rate: percentLabel(item.activation_rate || 0) }))
                    .join("") || t("admin.student.detail.funnelPending")}
                </p>
              </article>
            </div>
            <div className="admin-list-stack">
              {selectedStudentOps.length ? (
                selectedStudentOps.map((item, index) => (
                  <article key={`student-op-${index}`} className="admin-list-item">
                    <strong>{titleCase(String(item.event_type || item.audit_type || t("admin.shared.event")))}</strong>
                    <p>{String(item.reason || item.location || item.school_id || t("admin.student.detail.opsEvent"))}</p>
                  </article>
                ))
              ) : (
                <article className="admin-list-item">
                  <strong>{t("admin.student.detail.noStudentOps")}</strong>
                  <p>{t("admin.student.detail.noStudentOpsBody")}</p>
                </article>
              )}
              {(student?.overview?.recent_anomalies || []).some((item) => `${item.school_id || ""} ${item.creator_code || ""} ${item.student_id_code || ""}`.includes(String(selectedStudentRow.school_id || ""))) ? (
                <article className="admin-list-item">
                  <strong>{t("admin.student.detail.recentAnomalyWatch")}</strong>
                  <p>
                    {(student?.overview?.recent_anomalies || [])
                      .filter((item) => `${item.school_id || ""} ${item.creator_code || ""} ${item.student_id_code || ""}`.includes(String(selectedStudentRow.school_id || "")))
                      .slice(0, 2)
                      .map((item) => String(item.reason || item.message || item.audit_type || t("admin.student.detail.anomaly")))
                      .join(" · ")}
                  </p>
                </article>
              ) : null}
            </div>
          </div>
        ) : (
          <EmptyState title={t("admin.student.detail.emptyTitle")} body={t("admin.student.detail.emptyBody")} />
        )}
      </Panel>
    </div>
  );
}
