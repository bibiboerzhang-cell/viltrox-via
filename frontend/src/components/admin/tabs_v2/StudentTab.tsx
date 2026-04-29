/**
 * Student tab v2
 *
 * Keeps the refined black admin UI, but wires it to the live student QR/pass
 * backend instead of the old placeholder-shaped fields.
 */
import { type CSSProperties, type FormEvent, type ReactNode, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  createStudentBatch,
  createStudentSchool,
  fetchAdminStudentSnapshot,
  fetchStudentBatchDetail,
  reissueStudentCard,
  revokeStudentCard,
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

type Section = "schools" | "batches" | "roster";
type BusyKey = string | null;
type Row = Record<string, unknown>;

const inputStyle = {
  width: "100%",
  boxSizing: "border-box",
  background: "var(--ax-bg-0)",
  border: "0.5px solid var(--ax-border-3)",
  borderRadius: "var(--ax-r-md)",
  color: "var(--ax-text-5)",
  fontFamily: "inherit",
  fontSize: 11,
  outline: "none",
  padding: "8px 10px",
} satisfies CSSProperties;

const textareaStyle = {
  ...inputStyle,
  minHeight: 76,
  resize: "vertical",
} satisfies CSSProperties;

const formGridStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
  gap: 10,
} satisfies CSSProperties;

function asRecord(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Row) : {};
}

function num(...values: unknown[]): number {
  for (const value of values) {
    if (value === null || value === undefined || value === "") continue;
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function text(...values: unknown[]): string {
  for (const value of values) {
    const next = String(value ?? "").trim();
    if (next) return next;
  }
  return "";
}

function percentFromRate(rate: number): string {
  return `${Math.round(Math.max(0, Math.min(1, rate)) * 100)}%`;
}

function localClaimHref(value: unknown): string {
  const raw = text(value);
  if (!raw || typeof window === "undefined") return raw;
  try {
    const parsed = new URL(raw);
    const localHost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
    return localHost ? `${parsed.pathname}${parsed.search}${parsed.hash}` : raw;
  } catch {
    return raw;
  }
}

function normalizeRate(raw: unknown, issued: number, activated: number): number {
  const direct = num(raw);
  if (direct > 1) return direct / 100;
  if (direct > 0) return direct;
  return issued > 0 ? activated / issued : 0;
}

function schoolIssued(row: Row): number {
  const stats = asRecord(row.stats);
  return num(row.issued_count, row.issued, row.qr_count, stats.issued);
}

function schoolActivated(row: Row): number {
  const stats = asRecord(row.stats);
  return num(row.activated_count, row.activated, row.bound_count, stats.bound);
}

function batchIssued(row: Row): number {
  return num(row.issued_count, row.issued, row.qr_count, row.count);
}

function batchActivated(row: Row): number {
  return num(row.activated_count, row.activated, row.bound_count, row.bound);
}

function batchPending(row: Row): number {
  const issued = batchIssued(row);
  const activated = batchActivated(row);
  return num(row.pending_count, row.pending, Math.max(0, issued - activated - num(row.revoked_count, row.revoked)));
}

function fieldLabel(label: string, children: ReactNode) {
  return (
    <label style={{ display: "grid", gap: 5 }}>
      <span className="ax-label" style={{ margin: 0 }}>
        {label}
      </span>
      {children}
    </label>
  );
}

export function StudentTab({ token }: Props) {
  const { t } = useTranslation();
  const tt = (key: string, fallback: string, options: Record<string, unknown> = {}) =>
    String(t(`admin.student.v2.${key}`, { defaultValue: fallback, ...options }));
  const { data, loading, error, refresh } = useAdminSnapshot(token, fetchAdminStudentSnapshot);
  const [section, setSection] = useState<Section>("schools");
  const [busy, setBusy] = useState<BusyKey>(null);
  const [message, setMessage] = useState<{ tone: "pass" | "review" | "block"; body: string } | null>(null);
  const [lastBatch, setLastBatch] = useState<Row | null>(null);
  const [lastSavedSchool, setLastSavedSchool] = useState<Row | null>(null);
  const [cardRows, setCardRows] = useState<Row[]>([]);
  const [cardContext, setCardContext] = useState("");
  const [schoolForm, setSchoolForm] = useState({
    school_id: "",
    school_code: "",
    school_name: "",
    region: "",
    country: "US",
    partnership_status: "pilot",
    primary_color: "#111111",
    accent_color: "#ff7a1a",
  });
  const [batchForm, setBatchForm] = useState({
    school_id: "",
    batch_name: "",
    count: "24",
    roster_csv: "",
  });

  const schools = useMemo<Row[]>(
    () => {
      const remote = ((data?.schools?.length ? data.schools : data?.overview?.schools) ?? []) as unknown as Row[];
      if (!lastSavedSchool) return remote;
      const savedId = text(lastSavedSchool.school_id);
      if (!savedId || remote.some((school) => text(school.school_id) === savedId)) return remote;
      return [...remote, lastSavedSchool];
    },
    [data, lastSavedSchool],
  );
  const batches = useMemo<Row[]>(
    () => ((data?.overview?.batch_progress ?? data?.funnels?.batch_progress) ?? []) as unknown as Row[],
    [data],
  );
  const roster = useMemo<Row[]>(
    () => (data?.overview?.students ?? []) as unknown as Row[],
    [data],
  );
  const recentEvents = useMemo<Row[]>(
    () => ((data?.overview?.recent_events ?? data?.funnels?.recent_events) ?? []) as unknown as Row[],
    [data],
  );

  const totals = useMemo(() => {
    const issued = schools.reduce((sum, item) => sum + schoolIssued(item), 0);
    const activated = schools.reduce((sum, item) => sum + schoolActivated(item), 0);
    const fallbackIssued = issued || batches.reduce((sum, item) => sum + batchIssued(item), 0);
    const fallbackActivated = activated || batches.reduce((sum, item) => sum + batchActivated(item), 0);
    return {
      issued: fallbackIssued,
      activated: fallbackActivated,
      activationRate: normalizeRate(0, fallbackIssued, fallbackActivated),
    };
  }, [batches, schools]);

  const kpis = useMemo(
    () => [
      { label: tt("kpis.schools", "学校"), value: schools.length },
      { label: tt("kpis.qrIssued", "已发 QR"), value: totals.issued },
      { label: tt("kpis.activated", "已激活"), value: totals.activated },
      { label: tt("kpis.activation", "激活率"), value: totals.issued > 0 ? percentFromRate(totals.activationRate) : "—" },
    ],
    [schools.length, t, totals], // eslint-disable-line react-hooks/exhaustive-deps
  );

  async function submitSchool(event: FormEvent) {
    event.preventDefault();
    setBusy("school");
    setMessage(null);
    try {
      const result = await createStudentSchool(token, schoolForm);
      setLastSavedSchool(result as Row);
      setMessage({ tone: "pass", body: `学校 ${schoolForm.school_name || schoolForm.school_id} 已保存。` });
      setBatchForm((current) => ({ ...current, school_id: schoolForm.school_id || current.school_id }));
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : "保存学校失败" });
    } finally {
      setBusy(null);
    }
  }

  async function submitBatch(event: FormEvent) {
    event.preventDefault();
    setBusy("batch");
    setMessage(null);
    try {
      const result = await createStudentBatch(token, {
        school_id: batchForm.school_id,
        batch_name: batchForm.batch_name,
        count: Math.max(1, Number(batchForm.count || 1)),
        roster_csv: batchForm.roster_csv,
      });
      setLastBatch(result as Row);
      setCardRows((Array.isArray((result as Row).items) ? (result as Row).items : []) as Row[]);
      setCardContext(`${batchForm.school_id} · ${text((result as Row).batch_name, batchForm.batch_name)}`);
      setMessage({ tone: "pass", body: `QR 批次 ${text((result as Row).batch_name, batchForm.batch_name)} 已生成。` });
      setBatchForm((current) => ({ ...current, batch_name: "", roster_csv: "" }));
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : "生成 QR 批次失败" });
    } finally {
      setBusy(null);
    }
  }

  async function loadBatchCards(row: Row) {
    const schoolId = text(row.school_id);
    const batchName = text(row.batch_name, row.batch_id, row.batch_code);
    if (!schoolId || !batchName) return;
    setBusy(`detail:${schoolId}:${batchName}`);
    setMessage(null);
    try {
      const detail = await fetchStudentBatchDetail(token, schoolId, batchName);
      const items = (Array.isArray(detail.items) ? detail.items : []) as Row[];
      setCardRows(items);
      setCardContext(`${text(row.school_name, schoolId)} · ${batchName}`);
      setMessage({ tone: "pass", body: tt("messages.batchLoaded", "已加载 {{count}} 张 QR 卡。", { count: items.length }) });
      setSection("batches");
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("messages.batchLoadFailed", "加载 QR 明细失败") });
    } finally {
      setBusy(null);
    }
  }

  async function handleReissueCard(card: Row) {
    const qrId = text(card.qr_id);
    if (!qrId) return;
    setBusy(`reissue:${qrId}`);
    setMessage(null);
    try {
      const updated = await reissueStudentCard(token, qrId);
      setCardRows((current) => current.map((row) => (text(row.qr_id) === qrId ? { ...row, ...updated } : row)));
      setMessage({ tone: "pass", body: tt("messages.cardReissued", "QR {{qr}} 已重新签发。", { qr: qrId }) });
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("messages.cardReissueFailed", "重新签发失败") });
    } finally {
      setBusy(null);
    }
  }

  async function handleRevokeCard(card: Row) {
    const qrId = text(card.qr_id);
    if (!qrId) return;
    const reason = window.prompt(tt("prompts.revokeReason", "撤销原因"), "admin revoked test or invalid QR");
    if (!reason) return;
    setBusy(`revoke:${qrId}`);
    setMessage(null);
    try {
      const updated = await revokeStudentCard(token, qrId, reason);
      setCardRows((current) => current.map((row) => (text(row.qr_id) === qrId ? { ...row, ...updated } : row)));
      setMessage({ tone: "pass", body: tt("messages.cardRevoked", "QR {{qr}} 已撤销。", { qr: qrId }) });
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("messages.cardRevokeFailed", "撤销失败") });
    } finally {
      setBusy(null);
    }
  }

  const schoolCols: DataColumn<Row>[] = [
    {
      key: "school",
      label: tt("columns.school", "学校"),
      width: "1.8fr",
      render: (r) => (
        <div>
          <div style={{ color: "var(--ax-text-5)", fontWeight: 600 }}>
            {text(r.school_name, r.school_code, r.school_id) || "—"}
          </div>
          <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            {text(r.region, r.country) || tt("empty.noRegion", "地区待定")} · {text(r.school_code, r.school_id)}
          </div>
        </div>
      ),
    },
    {
      key: "status",
      label: tt("columns.status", "状态"),
      width: "100px",
      render: (r) => {
        const s = text(r.partnership_status, "pending").toLowerCase();
        const tone = s === "active" ? "pass" : s === "suspended" ? "block" : "review";
        return <StatusPill tone={tone as never}>{text(r.partnership_status, "pending")}</StatusPill>;
      },
    },
    {
      key: "issued",
      label: tt("columns.issued", "已发放"),
      width: "80px",
      render: (r) => (
        <span className="ax-num" style={{ fontWeight: 600 }}>
          {schoolIssued(r)}
        </span>
      ),
    },
    {
      key: "activated",
      label: tt("columns.activated", "已激活"),
      width: "90px",
      render: (r) => (
        <span className="ax-num" style={{ color: "var(--ax-status-pass)", fontWeight: 600 }}>
          {schoolActivated(r)}
        </span>
      ),
    },
    {
      key: "rate",
      label: tt("columns.rate", "比率"),
      width: "80px",
      accent: true,
      render: (r) => {
        const issued = schoolIssued(r);
        const activated = schoolActivated(r);
        return (
          <span className="ax-num" style={{ fontWeight: 600 }}>
            {issued > 0 ? percentFromRate(normalizeRate(r.activation_rate, issued, activated)) : "—"}
          </span>
        );
      },
    },
  ];

  const batchCols: DataColumn<Row>[] = [
    {
      key: "batch",
      label: tt("columns.batch", "批次"),
      width: "1.6fr",
      render: (r) => (
        <div>
          <div className="ax-mono" style={{ color: "var(--ax-text-5)", fontSize: 10 }}>
            {text(r.batch_name, r.batch_id, r.batch_code) || "—"}
          </div>
          <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            {text(r.school_name, r.school_id)}
          </div>
        </div>
      ),
    },
    {
      key: "issued",
      label: tt("columns.issued", "已发放"),
      width: "80px",
      render: (r) => <span className="ax-num">{batchIssued(r)}</span>,
    },
    {
      key: "activated",
      label: tt("columns.activated", "已激活"),
      width: "90px",
      render: (r) => (
        <span className="ax-num" style={{ color: "var(--ax-status-pass)", fontWeight: 600 }}>
          {batchActivated(r)}
        </span>
      ),
    },
    {
      key: "pending",
      label: tt("columns.pending", "待领取"),
      width: "80px",
      render: (r) => <span className="ax-num">{batchPending(r)}</span>,
    },
    {
      key: "rate",
      label: tt("columns.rate", "比率"),
      width: "80px",
      accent: true,
      render: (r) => {
        const issued = batchIssued(r);
        const activated = batchActivated(r);
        return <span className="ax-num">{issued > 0 ? percentFromRate(normalizeRate(r.activation_rate, issued, activated)) : "—"}</span>;
      },
    },
    {
      key: "actions",
      label: "",
      width: "110px",
      render: (r) => {
        const schoolId = text(r.school_id);
        const batchName = text(r.batch_name, r.batch_id, r.batch_code);
        return (
          <button
            type="button"
            className="ax-btn ax-btn--sm"
            disabled={!schoolId || !batchName || busy === `detail:${schoolId}:${batchName}`}
            onClick={() => loadBatchCards(r)}
          >
            <Icons.eye /> {tt("actions.detail", "明细")}
          </button>
        );
      },
    },
  ];

  const rosterCols: DataColumn<Row>[] = [
    {
      key: "student",
      label: tt("columns.student", "学生"),
      width: "1.6fr",
      render: (r) => (
        <div>
          <div style={{ color: "var(--ax-text-5)", fontWeight: 600 }}>
            {text(r.name, r.email, r.student_id_code) || "—"}
          </div>
          <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            {text(r.email, `user:${r.user_id ?? ""}`)}
          </div>
        </div>
      ),
    },
    {
      key: "school",
      label: tt("columns.school", "学校"),
      width: "1.2fr",
      render: (r) => <span style={{ color: "var(--ax-text-3)" }}>{text(r.school_name, r.school_id) || "—"}</span>,
    },
    {
      key: "student_id",
      label: tt("columns.studentId", "学号"),
      width: "120px",
      render: (r) => <span className="ax-mono">{text(r.student_id_code, r.user_code, r.student_id) || "—"}</span>,
    },
    {
      key: "creator",
      label: tt("columns.creator", "创作者"),
      width: "95px",
      render: (r) => <span className="ax-mono">{text(r.creator_code) || "—"}</span>,
    },
    {
      key: "status",
      label: tt("columns.status", "状态"),
      width: "95px",
      render: (r) => {
        const s = text(r.status, "pending").toLowerCase();
        const tone = s === "active" ? "pass" : s === "expired" || s === "revoked" ? "block" : "review";
        return <StatusPill tone={tone as never}>{text(r.status, "pending")}</StatusPill>;
      },
    },
  ];

  const eventCols: DataColumn<Row>[] = [
    {
      key: "type",
      label: tt("columns.trackEvent", "Track 事件"),
      width: "1.1fr",
      render: (r) => <span style={{ color: "var(--ax-text-5)", fontWeight: 600 }}>{text(r.event_type, r.audit_type, "event")}</span>,
    },
    {
      key: "school",
      label: tt("columns.school", "学校"),
      width: "1.1fr",
      render: (r) => <span>{text(r.school_name, r.school_id) || "—"}</span>,
    },
    {
      key: "target",
      label: tt("columns.qrUser", "QR / 用户"),
      width: "1.2fr",
      render: (r) => <span className="ax-mono">{text(r.qr_id, r.creator_code, r.user_id) || "—"}</span>,
    },
    {
      key: "at",
      label: tt("columns.time", "时间"),
      width: "150px",
      render: (r) => <span style={{ color: "var(--ax-text-2)" }}>{text(r.created_at, r.issued_at) || "—"}</span>,
    },
  ];

  const cardCols: DataColumn<Row>[] = [
    {
      key: "card",
      label: tt("columns.qrCard", "QR 卡片"),
      width: "1.7fr",
      render: (r) => {
        const metadata = asRecord(r.metadata);
        return (
          <div>
            <div className="ax-mono" style={{ color: "var(--ax-text-5)", fontSize: 10 }}>
              {text(metadata.public_claim_id, r.display_serial, r.qr_id) || "—"}
            </div>
            <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
              {text(r.qr_id)} · {text(r.roster_mode, "anonymous")}
            </div>
          </div>
        );
      },
    },
    {
      key: "status",
      label: tt("columns.status", "状态"),
      width: "100px",
      render: (r) => {
        const status = text(r.status, "issued").toLowerCase();
        const tone = status === "bound" ? "pass" : status === "revoked" || status === "expired" ? "block" : "review";
        return <StatusPill tone={tone as never}>{tt(`status.${status}`, status)}</StatusPill>;
      },
    },
    {
      key: "bound",
      label: tt("columns.boundUser", "绑定用户"),
      width: "95px",
      render: (r) => <span className="ax-mono">{num(r.bound_user_id) ? `#${num(r.bound_user_id)}` : "—"}</span>,
    },
    {
      key: "links",
      label: tt("columns.links", "链接"),
      width: "190px",
      render: (r) => (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {text(r.claim_url) ? (
            <a className="ax-btn ax-btn--sm" href={localClaimHref(r.claim_url)} target="_blank" rel="noreferrer">
              <Icons.externalLink /> {tt("actions.claim", "领取")}
            </a>
          ) : null}
          {text(r.qr_code_url) ? (
            <a className="ax-btn ax-btn--sm" href={text(r.qr_code_url)} target="_blank" rel="noreferrer">
              QR
            </a>
          ) : null}
        </div>
      ),
    },
    {
      key: "actions",
      label: "",
      width: "190px",
      render: (r) => {
        const qrId = text(r.qr_id);
        const bound = text(r.status).toLowerCase() === "bound" || num(r.bound_user_id) > 0;
        return (
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              disabled={!qrId || bound || busy === `reissue:${qrId}`}
              onClick={() => handleReissueCard(r)}
            >
              {tt("actions.reissue", "重签发")}
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-alert)" }}
              disabled={!qrId || bound || text(r.status).toLowerCase() === "revoked" || busy === `revoke:${qrId}`}
              onClick={() => handleRevokeCard(r)}
            >
              {tt("actions.revoke", "撤销")}
            </button>
          </div>
        );
      },
    },
  ];

  const sections: Array<{ key: Section; label: string }> = [
    { key: "schools", label: `${tt("sections.schools", "学校")} (${schools.length})` },
    { key: "batches", label: `${tt("sections.batches", "QR 批次")} (${batches.length})` },
    { key: "roster", label: `${tt("sections.roster", "名册")} (${roster.length})` },
  ];

  const lastBatchItems = (Array.isArray(lastBatch?.items) ? lastBatch?.items : []) as Row[];
  const lastQr = lastBatchItems[0] ?? null;

  return (
    <div>
      <PageHeader
        title={tt("title", "学生 QR / Track")}
        subtitle={tt("subtitle", "学校配置、二维码批次、学生绑定、动态通行证 check-in")}
        actions={
          <button type="button" className="ax-btn" onClick={refresh} disabled={loading}>
            <Icons.trending /> {loading ? tt("actions.refreshing", "刷新中…") : tt("actions.refresh", "刷新")}
          </button>
        }
      />

      {error ? (
        <div style={{ padding: 16 }}>
          <ErrorCard detail={error} onRetry={refresh} />
        </div>
      ) : null}

      <div style={{ padding: 16 }}>
        {loading && !data ? (
          <LoadingCard label={tt("loading", "加载学生数据…")} />
        ) : (
          <>
            <div style={{ marginBottom: 16 }}>
              <KPIGrid items={kpis} columns={4} />
            </div>

            {message ? (
              <div
                style={{
                  border: `0.5px solid var(--ax-status-${message.tone})`,
                  borderRadius: 6,
                  color: `var(--ax-status-${message.tone})`,
                  fontSize: 11,
                  marginBottom: 14,
                  padding: "9px 10px",
                }}
              >
                {message.body}
              </div>
            ) : null}

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
                gap: 12,
                marginBottom: 16,
              }}
            >
              <div className="ax-card">
                <SectionLabel>{tt("schoolForm.title", "学校设置")}</SectionLabel>
                <form onSubmit={submitSchool} style={formGridStyle}>
                  {fieldLabel(
                    tt("schoolForm.schoolId", "学校 ID"),
                    <input
                      required
                      style={inputStyle}
                      value={schoolForm.school_id}
                      onChange={(event) => setSchoolForm((current) => ({ ...current, school_id: event.target.value }))}
                      placeholder="AFI_001"
                    />,
                  )}
                  {fieldLabel(
                    tt("schoolForm.code", "代码"),
                    <input
                      required
                      style={inputStyle}
                      value={schoolForm.school_code}
                      onChange={(event) => setSchoolForm((current) => ({ ...current, school_code: event.target.value }))}
                      placeholder="AFI"
                    />,
                  )}
                  <div style={{ gridColumn: "1 / -1" }}>
                    {fieldLabel(
                      tt("schoolForm.schoolName", "学校名称"),
                      <input
                        required
                        style={inputStyle}
                        value={schoolForm.school_name}
                        onChange={(event) => setSchoolForm((current) => ({ ...current, school_name: event.target.value }))}
                        placeholder="American Film Institute"
                      />,
                    )}
                  </div>
                  {fieldLabel(
                    tt("schoolForm.region", "地区"),
                    <input
                      style={inputStyle}
                      value={schoolForm.region}
                      onChange={(event) => setSchoolForm((current) => ({ ...current, region: event.target.value }))}
                      placeholder="Los Angeles"
                    />,
                  )}
                  {fieldLabel(
                    tt("schoolForm.country", "国家"),
                    <input
                      style={inputStyle}
                      value={schoolForm.country}
                      onChange={(event) => setSchoolForm((current) => ({ ...current, country: event.target.value }))}
                    />,
                  )}
                  <div style={{ gridColumn: "1 / -1", display: "flex", justifyContent: "flex-end" }}>
                    <button type="submit" className="ax-btn ax-btn--primary" disabled={busy === "school"}>
                      <Icons.plus /> {busy === "school" ? tt("actions.saving", "保存中…") : tt("actions.saveSchool", "保存学校")}
                    </button>
                  </div>
                </form>
              </div>

              <div className="ax-card">
                <SectionLabel>{tt("batchForm.title", "签发 QR 批次")}</SectionLabel>
                <form onSubmit={submitBatch} style={formGridStyle}>
                  {fieldLabel(
                    tt("batchForm.school", "学校"),
                    <select
                      required
                      style={inputStyle}
                      value={batchForm.school_id}
                      onChange={(event) => setBatchForm((current) => ({ ...current, school_id: event.target.value }))}
                    >
                      <option value="">{tt("batchForm.selectSchool", "选择学校")}</option>
                      {schools.map((school) => (
                        <option key={text(school.school_id)} value={text(school.school_id)}>
                          {text(school.school_code, school.school_id)} · {text(school.school_name, school.school_id)}
                        </option>
                      ))}
                    </select>,
                  )}
                  {fieldLabel(
                    tt("batchForm.count", "数量"),
                    <input
                      min={1}
                      type="number"
                      style={inputStyle}
                      value={batchForm.count}
                      onChange={(event) => setBatchForm((current) => ({ ...current, count: event.target.value }))}
                    />,
                  )}
                  <div style={{ gridColumn: "1 / -1" }}>
                    {fieldLabel(
                      tt("batchForm.batchName", "批次名称"),
                      <input
                        required
                        style={inputStyle}
                        value={batchForm.batch_name}
                        onChange={(event) => setBatchForm((current) => ({ ...current, batch_name: event.target.value }))}
                        placeholder="spring-campus-2026"
                      />,
                    )}
                  </div>
                  <div style={{ gridColumn: "1 / -1" }}>
                    {fieldLabel(
                      tt("batchForm.rosterCsv", "名册 CSV（可选）"),
                      <textarea
                        style={textareaStyle}
                        value={batchForm.roster_csv}
                        onChange={(event) => setBatchForm((current) => ({ ...current, roster_csv: event.target.value }))}
                        placeholder={tt("batchForm.rosterPlaceholder", "name,email,major,year")}
                      />,
                    )}
                  </div>
                  <div style={{ gridColumn: "1 / -1", display: "flex", justifyContent: "space-between", gap: 10 }}>
                    <div style={{ color: "var(--ax-text-1)", fontSize: 10 }}>
                      {tt("batchForm.note", "生成静态 claim QR，并通过 `/api/student/claim` 与 `/api/student/signup` 绑定。")}
                    </div>
                    <button type="submit" className="ax-btn ax-btn--primary" disabled={busy === "batch"}>
                      <Icons.plus /> {busy === "batch" ? tt("actions.generating", "生成中…") : tt("actions.generateQr", "生成 QR")}
                    </button>
                  </div>
                </form>
                {lastBatch ? (
                  <div style={{ borderTop: "0.5px solid var(--ax-border-2)", marginTop: 12, paddingTop: 10 }}>
                    <div style={{ color: "var(--ax-text-5)", fontSize: 11, fontWeight: 600 }}>
                      {tt("latestBatch", "最新批次")}: {text(lastBatch.batch_name)}
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
                      {text(lastBatch.manifest_url) ? (
                        <a className="ax-btn ax-btn--sm" href={text(lastBatch.manifest_url)} target="_blank" rel="noreferrer">
                          <Icons.download /> {tt("actions.manifest", "Manifest")}
                        </a>
                      ) : null}
                      {text(lastBatch.printable_url) ? (
                        <a className="ax-btn ax-btn--sm" href={text(lastBatch.printable_url)} target="_blank" rel="noreferrer">
                          <Icons.externalLink /> {tt("actions.printable", "打印页")}
                        </a>
                      ) : null}
                      {lastQr ? (
                        <a className="ax-btn ax-btn--sm" href={localClaimHref(lastQr.claim_url)} target="_blank" rel="noreferrer">
                          <Icons.externalLink /> {tt("actions.firstClaim", "首张 QR 领取")}
                        </a>
                      ) : null}
                    </div>
                  </div>
                ) : null}
              </div>
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
              {section === "schools" ? (
                schools.length === 0 ? (
                  <EmptyCard label={tt("empty.schools", "暂无学校")} hint={tt("empty.schoolsHint", "添加合作学校开启学生创作者计划")} />
                ) : (
                  <DataTable
                    columns={schoolCols}
                    rows={schools}
                    rowKey={(r) => text(r.school_id, r.school_code)}
                    showCheckbox={false}
                  />
                )
              ) : section === "batches" ? (
                batches.length === 0 ? (
                  <EmptyCard label={tt("empty.batches", "暂无 QR 批次")} />
                ) : (
                  <DataTable
                    columns={batchCols}
                    rows={batches}
                    rowKey={(r, i) => `${text(r.school_id)}:${text(r.batch_name, r.batch_id, r.batch_code)}:${i}`}
                    showCheckbox={false}
                  />
                )
              ) : roster.length === 0 ? (
                <EmptyCard label={tt("empty.roster", "暂无学生名册")} />
              ) : (
                <DataTable
                  columns={rosterCols}
                  rows={roster}
                  rowKey={(r) => text(r.user_id, r.student_id_code, r.creator_code)}
                  showCheckbox={false}
                />
              )}
            </div>

            {cardRows.length > 0 ? (
              <div className="ax-card" style={{ marginTop: 16 }}>
                <SectionLabel>{tt("cards.title", "QR 卡片明细")}</SectionLabel>
                <div style={{ color: "var(--ax-text-2)", fontSize: 10, marginBottom: 10 }}>
                  {cardContext || tt("cards.contextFallback", "最近加载批次")}
                </div>
                <DataTable
                  columns={cardCols}
                  rows={cardRows}
                  rowKey={(r) => text(r.qr_id, r.display_serial)}
                  showCheckbox={false}
                />
              </div>
            ) : null}

            <div className="ax-card" style={{ marginTop: 16 }}>
              <SectionLabel>{tt("events.title", "最近 QR / Track 事件")}</SectionLabel>
              {recentEvents.length === 0 ? (
                <EmptyCard label={tt("empty.events", "暂无 track 事件")} hint={tt("empty.eventsHint", "扫码 claim、注册绑定、动态 pass 生成和 check-in 会显示在这里。")} />
              ) : (
                <DataTable
                  columns={eventCols}
                  rows={recentEvents.slice(0, 12)}
                  rowKey={(r, i) => `${text(r.event_key, r.event_type, r.qr_id)}:${i}`}
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

export default StudentTab;
