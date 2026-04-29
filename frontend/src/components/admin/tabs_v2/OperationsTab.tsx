/**
 * Operations tab v2
 *
 * Sub-sections (tabs within the page):
 *   - Review queue (submissions pending)
 *   - Verification queue (handle verifications)
 *   - Users (all users with actions)
 *   - Social accounts (linked-platform moderation)
 *   - Redemptions (rewards redemption list)
 *
 * Each section uses DataTable + SegButton for sub-nav.
 */
import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import {
  adjustAdminPoints,
  approveAdminSubmission,
  correctAdminSubmissionProduct,
  createManualAdminSubmission,
  deleteAdminSubmission,
  fetchAdminOperationsSnapshot,
  grantAdminPoints,
  reanalyzeAdminSubmission,
  rejectAdminSubmission,
  runAdminSocialAction,
  runAdminUserAction,
  runAdminVerificationAction,
  updateAdminRedemption,
  type AdminOperationsSnapshot,
  type AdminSocialAccountRecord,
  type AdminUserRecord,
  type AdminVerificationRecord,
  type AdminRedemptionRecord,
} from "../../../services/admin.service";
import type { AdminSubmission, AuthUser } from "../../../lib/api";
import { Icons } from "../Icons";
import {
  BulkBar,
  DataTable,
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

type SubSection = "review" | "verify" | "users" | "social" | "redemptions";
type AnalysisRecord = Record<string, unknown>;
type PointMode = "grant" | "adjust" | "deduct";

function parseAnalysis(value: unknown): AnalysisRecord {
  if (!value) return {};
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value) as unknown;
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? parsed as AnalysisRecord
        : {};
    } catch {
      return {};
    }
  }
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as AnalysisRecord
    : {};
}

function pickString(source: AnalysisRecord, keys: string[], fallback = "—"): string {
  for (const key of keys) {
    const value = source[key];
    if (value !== undefined && value !== null && String(value).trim()) {
      return String(value);
    }
  }
  return fallback;
}

function pickNumber(source: AnalysisRecord, keys: string[], fallback = 0): number {
  for (const key of keys) {
    const value = Number(source[key]);
    if (Number.isFinite(value)) {
      return value;
    }
  }
  return fallback;
}

function pickList(source: AnalysisRecord, keys: string[]): unknown[] {
  for (const key of keys) {
    const value = source[key];
    if (Array.isArray(value)) {
      return value;
    }
  }
  return [];
}

function formatItem(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") {
    const item = value as Record<string, unknown>;
    return String(
      item.label
      || item.product
      || item.viltrox_lens
      || item.lens
      || item.area
      || item.suggestion
      || JSON.stringify(item),
    );
  }
  return String(value);
}

export function OperationsTab({ token }: Props) {
  const { t } = useTranslation();
  const tt = (key: string, fallback: string, options: Record<string, unknown> = {}) =>
    String(t(`admin.operations.v2.${key}`, { defaultValue: fallback, ...options }));
  const statusLabel = (value: unknown) => {
    const raw = String(value || "pending");
    const key = raw.toLowerCase().replace(/[^a-z0-9]+/g, "_");
    return tt(`status.${key}`, raw);
  };
  const { data, loading, error, refresh } = useAdminSnapshot(
    token,
    fetchAdminOperationsSnapshot,
  );
  const [section, setSection] = useState<SubSection>("review");
  const [busy, setBusy] = useState<string>("");
  const [toast, setToast] = useState<{ tone: "ok" | "err"; msg: string } | null>(null);
  const [selectedSubmissionId, setSelectedSubmissionId] = useState<string | null>(null);
  const [manualOpen, setManualOpen] = useState(false);

  const handleApprove = async (s: AdminSubmission) => {
    setBusy(`approve:${s.id}`);
    try {
      await approveAdminSubmission(token, s.id, { memo_append: "admin approved" });
      setToast({ tone: "ok", msg: `已批准 #${s.id}` });
      refresh();
    } catch (e) {
      setToast({ tone: "err", msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  };

  const handleReject = async (s: AdminSubmission) => {
    const reason = window.prompt("拒绝理由?", "质量不达标");
    if (!reason) return;
    setBusy(`reject:${s.id}`);
    try {
      await rejectAdminSubmission(token, s.id, reason);
      setToast({ tone: "ok", msg: `已拒绝 #${s.id}` });
      refresh();
    } catch (e) {
      setToast({ tone: "err", msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  };

  const handleReanalyze = async (s: AdminSubmission) => {
    setBusy(`reanalyze:${s.id}`);
    try {
      await reanalyzeAdminSubmission(token, s.id);
      setToast({ tone: "ok", msg: `已重新分析 #${s.id}` });
      refresh();
    } catch (e) {
      setToast({ tone: "err", msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  };

  const handleDeleteSubmission = async (s: AdminSubmission) => {
    if (!window.confirm(`删除投稿 #${s.id}? 这个操作会移除该条后台记录。`)) return;
    setBusy(`delete:${s.id}`);
    try {
      await deleteAdminSubmission(token, s.id);
      setToast({ tone: "ok", msg: `已删除 #${s.id}` });
      if (selectedSubmissionId === String(s.id)) setSelectedSubmissionId(null);
      refresh();
    } catch (e) {
      setToast({ tone: "err", msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  };

  const handleManualSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const finalScore = Number(form.get("final_score") || 0);
    const creatorScore = Number(form.get("creator_score") || 0);
    setBusy("manual:add");
    try {
      await createManualAdminSubmission(token, {
        platform: String(form.get("platform") || "Uploaded Video"),
        extracted_handle: String(form.get("extracted_handle") || "").trim(),
        url: String(form.get("url") || "").trim(),
        title: String(form.get("title") || "").trim(),
        detection_status: String(form.get("detection_status") || "confirmed"),
        product_series: String(form.get("product_series") || "").trim(),
        product_label: String(form.get("product_label") || form.get("product_series") || "").trim(),
        final_score: finalScore,
        creator_score: creatorScore,
        overall_score: Math.round((finalScore + creatorScore) / 2),
        views: Number(form.get("views") || 0),
        likes: Number(form.get("likes") || 0),
        comments: Number(form.get("comments") || 0),
        shares: Number(form.get("shares") || 0),
        recommendation: String(form.get("recommendation") || "Manually added by admin"),
        memo: String(form.get("memo") || ""),
      });
      e.currentTarget.reset();
      setManualOpen(false);
      setToast({ tone: "ok", msg: "已手动添加投稿" });
      refresh();
    } catch (err) {
      setToast({ tone: "err", msg: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy("");
    }
  };

  const handleUserAction = async (u: AdminUserRecord, action: "approve" | "reject" | "block" | "unblock") => {
    if (!u.id) return;
    setBusy(`user:${u.id}:${action}`);
    try {
      await runAdminUserAction(
        token,
        u.id,
        action,
        action === "block" ? "admin blocked from operations queue" : "admin unblocked from operations queue",
      );
      setToast({ tone: "ok", msg: `${action} 成功 user#${u.id}` });
      refresh();
    } catch (e) {
      setToast({ tone: "err", msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  };

  const handlePointAction = async (u: AdminUserRecord, mode: PointMode) => {
    if (!u.id) return;
    const rawAmount = window.prompt(
      mode === "deduct" ? "扣除积分数量" : mode === "grant" ? "发放积分数量" : "调整积分数量",
      mode === "deduct" ? "10" : "25",
    );
    if (!rawAmount) return;
    const amount = Math.round(Math.abs(Number(rawAmount)));
    if (!Number.isFinite(amount) || amount <= 0) {
      setToast({ tone: "err", msg: "积分数量必须大于 0" });
      return;
    }
    const reason = window.prompt("积分调整原因", "admin operations adjustment") || "admin operations adjustment";
    setBusy(`points:${u.id}:${mode}`);
    try {
      if (mode === "grant") {
        await grantAdminPoints(token, u.id, { points: amount, reason });
      } else {
        await adjustAdminPoints(token, u.id, {
          delta: mode === "deduct" ? -amount : amount,
          reason,
        });
      }
      setToast({ tone: "ok", msg: `积分已更新 user#${u.id}` });
      refresh();
    } catch (e) {
      setToast({ tone: "err", msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  };

  const handleSocialAction = async (account: AdminSocialAccountRecord, action: "verify" | "reject") => {
    if (!account.id) return;
    if (action === "reject" && !window.confirm(`移除社交账号 ${account.platform || ""} ${account.handle || ""}?`)) {
      return;
    }
    setBusy(`social:${account.id}:${action}`);
    try {
      await runAdminSocialAction(token, account.id, action);
      setToast({ tone: "ok", msg: `${action} 成功 social#${account.id}` });
      refresh();
    } catch (e) {
      setToast({ tone: "err", msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  };

  const handleRedemptionAction = async (r: AdminRedemptionRecord, nextStatus: "fulfilled" | "shipped" | "delivered" | "cancelled") => {
    if (!r.id) return;
    const tracking = window.prompt("物流单号 / tracking number", r.tracking_number || "");
    if (tracking === null) return;
    const note = window.prompt("后台备注", r.admin_note || `admin marked ${nextStatus}`) || `admin marked ${nextStatus}`;
    setBusy(`redemption:${r.id}:${nextStatus}`);
    try {
      await updateAdminRedemption(token, r.id, {
        status: nextStatus,
        tracking_number: tracking,
        admin_note: note,
      });
      setToast({ tone: "ok", msg: `兑换 #${r.id} 已更新为 ${nextStatus}` });
      refresh();
    } catch (e) {
      setToast({ tone: "err", msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  };

  const handleCorrection = async (
    row: AdminSubmission,
    payload: { correct_series: string; correct_label: string; note: string },
  ) => {
    const correctSeries = payload.correct_series.trim();
    const correctLabel = payload.correct_label.trim();
    if (!correctSeries || !correctLabel) {
      setToast({ tone: "err", msg: "产品纠错需要填写正确系列和正确标签" });
      return;
    }
    setBusy(`correct:${row.id}`);
    try {
      await correctAdminSubmissionProduct(token, row.id, {
        correct_series: correctSeries,
        correct_label: correctLabel,
        note: payload.note.trim(),
      });
      setToast({ tone: "ok", msg: `产品纠错已保存 #${row.id}` });
      refresh();
    } catch (e) {
      setToast({ tone: "err", msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  };

  const handleVerify = async (v: AdminVerificationRecord, action: "approve" | "reject") => {
    setBusy(`verify:${v.id}:${action}`);
    try {
      await runAdminVerificationAction(token, v.id, action);
      setToast({ tone: "ok", msg: `${action} 成功 verification#${v.id}` });
      refresh();
    } catch (e) {
      setToast({ tone: "err", msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy("");
    }
  };

  // ── KPI ──
  const kpis = useMemo(() => {
    if (!data) return [];
    return [
      { label: tt("kpis.reviewQueue", "审核队列"), value: data.reviewQueue.length },
      { label: tt("kpis.verifyQueue", "验证队列"), value: data.verifyQueue.length },
      { label: tt("kpis.users", "用户"), value: data.users.length },
      { label: tt("kpis.socialClaims", "社媒绑定"), value: data.socials.length },
      { label: tt("kpis.pendingRedemptions", "待处理兑换"), value: data.redemptions.filter((r) => (r.status || "").toLowerCase() === "pending").length },
    ];
  }, [data, t]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Review columns ──
  const reviewColumns: DataColumn<AdminSubmission>[] = useMemo(
    () => [
      {
        key: "title",
        label: "标题",
        width: "2fr",
        render: (r) => (
          <div style={{ color: "var(--ax-text-5)" }}>
            {r.title || <span style={{ color: "var(--ax-text-1)" }}>无标题</span>}
          </div>
        ),
      },
      {
        key: "creator",
        label: "创作者",
        width: "1fr",
        render: (r) => (
          <div>
            <div className="ax-mono" style={{ fontSize: 10 }}>{r.creator_code || "—"}</div>
            {r.extracted_handle ? (
              <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
                @{r.extracted_handle.replace(/^@/, "")}
              </div>
            ) : null}
          </div>
        ),
      },
      {
        key: "platform",
        label: "平台",
        width: "70px",
        render: (r) => <span style={{ color: "var(--ax-text-3)" }}>{r.platform || "—"}</span>,
      },
      {
        key: "score",
        label: "分数",
        width: "60px",
        accent: true,
        render: (r) => (
          <span className="ax-num" style={{ color: "var(--ax-text-5)", fontWeight: 600 }}>
            {r.overall_score != null ? Math.round(r.overall_score) : "—"}
          </span>
        ),
      },
      {
        key: "status",
        label: "状态",
        width: "80px",
        render: (r) => {
          const s = (r.detection_status || "").toLowerCase();
          const tone =
            s === "confirmed" || s === "approved"
              ? "pass"
              : s === "rejected"
              ? "block"
              : s === "pending"
              ? "review"
              : "queue";
          return <StatusPill tone={tone as never}>{statusLabel(r.detection_status)}</StatusPill>;
        },
      },
      {
        key: "actions",
        label: "",
        width: "210px",
        render: (r) => (
          <div style={{ display: "flex", gap: 4 }} onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              onClick={() => setSelectedSubmissionId(String(r.id))}
            >
              <Icons.eye /> 详情
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-pass)" }}
              disabled={busy === `approve:${r.id}`}
              onClick={() => handleApprove(r)}
            >
              批准
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-alert)" }}
              disabled={busy === `reject:${r.id}`}
              onClick={() => handleReject(r)}
            >
              拒绝
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              disabled={busy === `reanalyze:${r.id}`}
              onClick={() => handleReanalyze(r)}
            >
              重分析
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-alert)" }}
              disabled={busy === `delete:${r.id}`}
              onClick={() => handleDeleteSubmission(r)}
            >
              删除
            </button>
          </div>
        ),
      },
    ],
    [busy, token], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // ── Verify columns ──
  const verifyColumns: DataColumn<AdminVerificationRecord>[] = useMemo(
    () => [
      {
        key: "handle",
        label: tt("columns.handle", "Handle"),
        width: "1.5fr",
        render: (r) => (
          <div>
            <div style={{ color: "var(--ax-text-5)" }}>
              @{(r.handle || "").replace(/^@/, "")}
            </div>
            {r.user_name ? (
              <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>{r.user_name}</div>
            ) : null}
          </div>
        ),
      },
      {
        key: "platform",
        label: "平台",
        width: "80px",
        render: (r) => <span style={{ color: "var(--ax-text-3)" }}>{r.platform || "—"}</span>,
      },
      {
        key: "status",
        label: "状态",
        width: "80px",
        render: (r) => {
          const s = (r.status || "").toLowerCase();
          const tone = s === "verified" ? "pass" : s === "rejected" ? "block" : "review";
          return <StatusPill tone={tone as never}>{statusLabel(r.status)}</StatusPill>;
        },
      },
      {
        key: "comment",
        label: tt("columns.comment", "验证内容"),
        width: "2fr",
        render: (r) => (
          <code style={{ fontSize: 10, color: "var(--ax-text-3)" }}>
            {r.generated_comment || "—"}
          </code>
        ),
      },
      {
        key: "actions",
        label: "",
        width: "140px",
        render: (r) => (
          <div style={{ display: "flex", gap: 4 }} onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-pass)" }}
              disabled={busy === `verify:${r.id}:approve`}
              onClick={() => handleVerify(r, "approve")}
            >
              通过
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-alert)" }}
              disabled={busy === `verify:${r.id}:reject`}
              onClick={() => handleVerify(r, "reject")}
            >
              拒绝
            </button>
          </div>
        ),
      },
    ],
    [busy, token], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // ── User columns ──
  const userColumns: DataColumn<AdminUserRecord>[] = useMemo(
    () => [
      {
        key: "user",
        label: "用户",
        width: "1.5fr",
        render: (r) => (
          <div>
            <div className="ax-mono" style={{ fontSize: 11, color: "var(--ax-text-5)" }}>
              {r.creator_code || `V_${String(r.id).padStart(6, "0")}`}
            </div>
            <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
              {r.name || r.email || "—"}
            </div>
          </div>
        ),
      },
      {
        key: "email",
        label: "邮箱",
        width: "1.5fr",
        render: (r) => <span style={{ color: "var(--ax-text-3)" }}>{r.email || "—"}</span>,
      },
      {
        key: "role",
        label: "角色",
        width: "80px",
        render: (r) => <span style={{ color: "var(--ax-text-4)" }}>{r.role || "creator"}</span>,
      },
      {
        key: "points",
        label: "积分",
        width: "80px",
        render: (r) => (
          <span className="ax-num" style={{ color: "var(--ax-text-5)" }}>
            {r.points_balance ?? 0}
          </span>
        ),
      },
      {
        key: "status",
        label: "状态",
        width: "80px",
        render: (r) => {
          const s = (r.status || "active").toLowerCase();
          const tone = s === "active" ? "active" : s === "blocked" ? "block" : "idle";
          return <StatusPill tone={tone as never}>{statusLabel(r.status || "active")}</StatusPill>;
        },
      },
      {
        key: "actions",
        label: "",
        width: "330px",
        render: (r) => (
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }} onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-pass)" }}
              disabled={busy === `user:${r.id}:approve`}
              onClick={() => handleUserAction(r, "approve")}
            >
              通过
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              disabled={busy === `user:${r.id}:unblock`}
              onClick={() => handleUserAction(r, "unblock")}
            >
              启用
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-alert)" }}
              disabled={busy === `user:${r.id}:block`}
              onClick={() => handleUserAction(r, "block")}
            >
              封禁
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-review)" }}
              disabled={busy === `points:${r.id}:grant`}
              onClick={() => handlePointAction(r, "grant")}
            >
              +积分
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-alert)" }}
              disabled={busy === `points:${r.id}:deduct`}
              onClick={() => handlePointAction(r, "deduct")}
            >
              -积分
            </button>
          </div>
        ),
      },
    ],
    [busy, token], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // ── Social account columns ──
  const socialColumns: DataColumn<AdminSocialAccountRecord>[] = useMemo(
    () => [
      {
        key: "account",
        label: "账号",
        width: "1.7fr",
        render: (r) => (
          <div>
            <div style={{ color: "var(--ax-text-5)" }}>
              {String(r.platform || "platform")} · {String(r.handle || "—")}
            </div>
            <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
              {r.verify_code ? tt("social.verifyCode", "验证码 {{code}}", { code: r.verify_code }) : tt("social.awaiting", "等待验证")}
            </div>
          </div>
        ),
      },
      {
        key: "creator",
        label: "创作者",
        width: "1.4fr",
        render: (r) => (
          <span style={{ color: "var(--ax-text-3)" }}>
            {r.user_name || r.email || `user#${r.user_id || "—"}`}
          </span>
        ),
      },
      {
        key: "status",
        label: "状态",
        width: "90px",
        render: (r) => (
          <StatusPill tone={r.verified ? "pass" : "review"}>
            {r.verified ? tt("status.verified", "已验证") : tt("status.pending", "待验证")}
          </StatusPill>
        ),
      },
      {
        key: "actions",
        label: "",
        width: "190px",
        render: (r) => (
          <div style={{ display: "flex", gap: 4 }} onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              disabled={Boolean(r.verified) || busy === `social:${r.id}:verify`}
              onClick={() => handleSocialAction(r, "verify")}
              title={tt("social.strictTitle", "严格验证模式下，后端可能拒绝人工通过")}
            >
              验证
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-alert)" }}
              disabled={busy === `social:${r.id}:reject`}
              onClick={() => handleSocialAction(r, "reject")}
            >
              移除
            </button>
          </div>
        ),
      },
    ],
    [busy, token], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // ── Redemption columns ──
  const redeemColumns: DataColumn<AdminRedemptionRecord>[] = useMemo(
    () => [
      {
        key: "item",
        label: "奖品",
        width: "2fr",
        render: (r) => (
          <div>
            <div style={{ color: "var(--ax-text-5)" }}>{r.item_name || "—"}</div>
            <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
              {r.tracking_number ? tt("redemptions.tracking", "物流 {{tracking}}", { tracking: r.tracking_number }) : r.admin_note || tt("status.pending", "待处理")}
            </div>
          </div>
        ),
      },
      {
        key: "creator",
        label: "创作者",
        width: "1fr",
        render: (r) => (
          <span className="ax-mono" style={{ fontSize: 10 }}>{r.creator_code || `V_${String(r.user_id).padStart(6, "0")}`}</span>
        ),
      },
      {
        key: "points",
        label: "积分",
        width: "80px",
        render: (r) => (
          <span className="ax-num" style={{ color: "var(--ax-text-5)" }}>
            {r.points_cost ?? 0}
          </span>
        ),
      },
      {
        key: "status",
        label: "状态",
        width: "100px",
        render: (r) => {
          const s = (r.status || "pending").toLowerCase();
          const tone = s === "fulfilled" ? "pass" : s === "cancelled" ? "block" : "review";
          return <StatusPill tone={tone as never}>{statusLabel(r.status || "pending")}</StatusPill>;
        },
      },
      {
        key: "at",
        label: "提交于",
        width: "120px",
        render: (r) => (
          <span style={{ color: "var(--ax-text-2)", fontSize: 10 }}>
            {r.created_at ? new Date(r.created_at).toLocaleDateString() : "—"}
          </span>
        ),
      },
      {
        key: "actions",
        label: "",
        width: "260px",
        render: (r) => (
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }} onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              disabled={busy === `redemption:${r.id}:fulfilled`}
              onClick={() => handleRedemptionAction(r, "fulfilled")}
            >
              履约
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              disabled={busy === `redemption:${r.id}:shipped`}
              onClick={() => handleRedemptionAction(r, "shipped")}
            >
              发货
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-pass)" }}
              disabled={busy === `redemption:${r.id}:delivered`}
              onClick={() => handleRedemptionAction(r, "delivered")}
            >
              送达
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-alert)" }}
              disabled={busy === `redemption:${r.id}:cancelled`}
              onClick={() => handleRedemptionAction(r, "cancelled")}
            >
              取消
            </button>
          </div>
        ),
      },
    ],
    [busy, token], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const sections: Array<{ key: SubSection; label: string; count: number }> = [
    { key: "review", label: tt("sections.review", "审核队列"), count: data?.reviewQueue.length || 0 },
    { key: "verify", label: tt("sections.verify", "验证队列"), count: data?.verifyQueue.length || 0 },
    { key: "users", label: tt("sections.users", "用户"), count: data?.users.length || 0 },
    { key: "social", label: tt("sections.social", "社媒账号"), count: data?.socials.length || 0 },
    { key: "redemptions", label: tt("sections.redemptions", "兑换记录"), count: data?.redemptions.length || 0 },
  ];
  const selectedSubmission = useMemo(() => {
    const rows = data?.reviewQueue ?? [];
    if (!rows.length || !selectedSubmissionId) return null;
    return rows.find((item) => String(item.id) === selectedSubmissionId) || null;
  }, [data?.reviewQueue, selectedSubmissionId]);

  return (
    <div>
      <PageHeader
        title={tt("title", "运营工作台")}
        subtitle={tt("subtitle", "审核队列 · 验证队列 · 用户管理 · 社媒账号 · 兑换记录")}
        actions={
          <>
            <button type="button" className="ax-btn" onClick={() => setManualOpen((v) => !v)}>
              <Icons.plus /> {tt("actions.manualAdd", "手动添加")}
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
          <LoadingCard label={tt("loading", "加载运营数据…")} />
        ) : (
          <>
            {manualOpen ? (
              <ManualSubmissionForm
                busy={busy === "manual:add"}
                onSubmit={handleManualSubmit}
                onCancel={() => setManualOpen(false)}
              />
            ) : null}

            <div style={{ marginBottom: 16 }}>
              <KPIGrid items={kpis} columns={5} />
            </div>

            <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
              <SegButton
                items={sections.map((s) => ({
                  key: s.key,
                  label: `${s.label} ${s.count > 0 ? `(${s.count})` : ""}`,
                }))}
                active={section}
                onChange={(k) => setSection(k as SubSection)}
              />
            </div>

            {section === "review" ? (
              <div className="ax-ops-review-layout">
                <div
                  style={{
                    border: "0.5px solid var(--ax-border-2)",
                    borderRadius: 6,
                    overflow: "hidden",
                    background: "var(--ax-bg-1)",
                  }}
                >
                  <DataTable
                    columns={reviewColumns}
                    rows={data?.reviewQueue ?? []}
                    rowKey={(r) => String(r.id)}
                    showCheckbox={false}
                    onRowClick={(row) => setSelectedSubmissionId(String(row.id))}
                    selectedId={selectedSubmission ? String(selectedSubmission.id) : null}
                    emptyLabel={tt("empty.review", "审核队列为空 ✓")}
                  />
                </div>
                <SubmissionDetailPanel
                  row={selectedSubmission}
                  busy={busy}
                  onClose={() => setSelectedSubmissionId(null)}
                  onApprove={handleApprove}
                  onReject={handleReject}
                  onReanalyze={handleReanalyze}
                  onDelete={handleDeleteSubmission}
                  onCorrect={handleCorrection}
                />
              </div>
            ) : (
              <div
                style={{
                  border: "0.5px solid var(--ax-border-2)",
                  borderRadius: 6,
                  overflow: "hidden",
                  background: "var(--ax-bg-1)",
                }}
              >
                {section === "verify" ? (
                  <DataTable
                    columns={verifyColumns}
                    rows={data?.verifyQueue ?? []}
                    rowKey={(r) => String(r.id)}
                    showCheckbox={false}
                    emptyLabel={tt("empty.verify", "验证队列为空 ✓")}
                  />
                ) : section === "users" ? (
                  <DataTable
                    columns={userColumns}
                    rows={data?.users ?? []}
                    rowKey={(r) => String(r.id)}
                    showCheckbox={false}
                    emptyLabel={tt("empty.users", "暂无用户")}
                  />
                ) : section === "social" ? (
                  <DataTable
                    columns={socialColumns}
                    rows={data?.socials ?? []}
                    rowKey={(r) => String(r.id)}
                    showCheckbox={false}
                    emptyLabel={tt("empty.social", "暂无社交账号")}
                  />
                ) : (
                  <DataTable
                    columns={redeemColumns}
                    rows={data?.redemptions ?? []}
                    rowKey={(r) => String(r.id)}
                    showCheckbox={false}
                    emptyLabel={tt("empty.redemptions", "暂无兑换记录")}
                  />
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function SubmissionDetailPanel({
  row,
  busy,
  onClose,
  onApprove,
  onReject,
  onReanalyze,
  onDelete,
  onCorrect,
}: {
  row: AdminSubmission | null;
  busy: string;
  onClose: () => void;
  onApprove: (row: AdminSubmission) => void;
  onReject: (row: AdminSubmission) => void;
  onReanalyze: (row: AdminSubmission) => void;
  onDelete: (row: AdminSubmission) => void;
  onCorrect: (row: AdminSubmission, payload: { correct_series: string; correct_label: string; note: string }) => void;
}) {
  const { t } = useTranslation();
  const tt = (key: string, fallback: string, options: Record<string, unknown> = {}) =>
    String(t(`admin.operations.v2.detail.${key}`, { defaultValue: fallback, ...options }));
  const [correctionDraft, setCorrectionDraft] = useState({
    submissionId: "",
    correctSeries: "",
    correctLabel: "",
    note: "",
  });

  if (!row) {
    return (
      <div className="ax-card" style={{ padding: 18, color: "var(--ax-text-2)", fontSize: 11 }}>
        {tt("selectPrompt", "选择左侧任一投稿查看详情")}
      </div>
    );
  }

  const analysis = parseAnalysis(row.video_analysis);
  const prefilter = parseAnalysis(analysis.prefilter);
  const camera = pickString(prefilter, ["camera_body", "camera", "camera_brand"], pickString(analysis, ["camera_body", "camera", "camera_brand"]));
  const lens = pickString(prefilter, ["viltrox_lens", "lens"], pickString(analysis, ["viltrox_lens", "lens", "gear_combo"]));
  const summary = pickString(analysis, ["content_summary", "quality_summary", "summary", "notes"], row.memo || row.recommendation || "—");
  const timestamps = pickList(analysis, ["timestamps", "timeline", "moments"]).slice(0, 12);
  const improvements = pickList(analysis, ["improvements", "improvement_suggestions", "suggestions"]).slice(0, 5);
  const products = pickList(analysis, ["products_detected", "detected_products", "brand_elements"]).slice(0, 6);
  const brandScore = pickNumber(analysis, ["brand_exposure", "brand_score", "brand_visibility"], Number(row.marketing_score || 0));
  const storyScore = pickNumber(analysis, ["storytelling", "story_score", "conversion_potential"], Number(row.creator_score || 0));
  const techScore = Number(row.tech_score || pickNumber(analysis, ["tech_score", "technical_score"], 0));
  const marketingScore = Number(row.marketing_score || pickNumber(analysis, ["marketing_score", "mkt_score"], 0));
  const status = String(row.detection_status || "pending");
  const statusTone = status.toLowerCase().includes("confirm") ? "pass" : status.toLowerCase().includes("reject") ? "block" : "review";
  const correction =
    correctionDraft.submissionId === String(row.id)
      ? correctionDraft
      : {
          submissionId: String(row.id),
          correctSeries: String(row.product_series || pickString(analysis, ["series", "product_series"])),
          correctLabel: String(row.product_label || pickString(analysis, ["product_label", "viltrox_lens"])),
          note: "",
        };

  return (
    <aside
      className="ax-card"
      style={{
        padding: 0,
        overflow: "hidden",
        position: "sticky",
        top: 76,
        maxHeight: "calc(100vh - 96px)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div style={{ padding: 14, borderBottom: "0.5px solid var(--ax-border-2)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "start" }}>
          <div style={{ minWidth: 0 }}>
            <SectionLabel>{tt("sectionLabel", "投稿详情")}</SectionLabel>
            <h3 style={{ margin: "6px 0", fontSize: 15, lineHeight: 1.25, color: "var(--ax-text-5)" }}>
              {row.title || tt("fallbackTitle", "投稿 #{{id}}", { id: row.id })}
            </h3>
            <div style={{ fontSize: 10, color: "var(--ax-text-2)" }}>
              #{row.id} · {row.platform || "—"} · {row.extracted_handle ? `@${String(row.extracted_handle).replace(/^@/, "")}` : row.creator_code || "—"}
            </div>
          </div>
          <button type="button" className="ax-btn ax-btn--sm" onClick={onClose} aria-label="Close detail">
            <Icons.close />
          </button>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
          <StatusPill tone={statusTone as never}>{status}</StatusPill>
          <StatusPill tone="queue" >{tt("totalScore", "总分 {{score}}/400", { score: Math.round(Number(row.final_score || row.overall_score || 0)) })}</StatusPill>
          <StatusPill tone="active">{tt("creatorScore", "创作者 {{score}}", { score: Math.round(Number(row.creator_score || 0)) })}</StatusPill>
          <StatusPill tone="review">{tt("points", "积分 {{points}}", { points: Number(row.points_awarded || 0) })}</StatusPill>
        </div>

        <div style={{ display: "flex", gap: 6, marginTop: 12, flexWrap: "wrap" }}>
          {row.url ? (
            <a className="ax-btn ax-btn--sm" href={String(row.url)} target="_blank" rel="noreferrer">
              {tt("openLink", "打开链接")} <Icons.externalLink />
            </a>
          ) : null}
          <button type="button" className="ax-btn ax-btn--sm" disabled={busy === `approve:${row.id}`} onClick={() => onApprove(row)}>
            <Icons.check /> 批准
          </button>
          <button type="button" className="ax-btn ax-btn--sm" disabled={busy === `reanalyze:${row.id}`} onClick={() => onReanalyze(row)}>
            重分析
          </button>
          <button type="button" className="ax-btn ax-btn--sm" style={{ color: "var(--ax-status-alert)" }} disabled={busy === `reject:${row.id}`} onClick={() => onReject(row)}>
            拒绝
          </button>
          <button type="button" className="ax-btn ax-btn--sm" style={{ color: "var(--ax-status-alert)" }} disabled={busy === `delete:${row.id}`} onClick={() => onDelete(row)}>
            删除
          </button>
        </div>
      </div>

      <div style={{ overflow: "auto", padding: 14 }}>
        <DetailBlock title={tt("gearTitle", "器材信息")}>
          <FactGrid
            items={[
              [tt("camera", "相机"), camera],
              [tt("lens", "镜头"), lens],
              [tt("series", "系列"), row.product_series || pickString(analysis, ["series", "product_series"])],
              [tt("product", "产品"), row.product_label || pickString(analysis, ["product_label", "viltrox_lens"])],
            ]}
          />
          {products.length ? <ChipLine items={products} tone="warm" /> : null}
        </DetailBlock>

        <DetailBlock title={tt("correctionTitle", "产品纠错")}>
          <form
            style={{ display: "grid", gap: 8 }}
            onSubmit={(event) => {
              event.preventDefault();
              onCorrect(row, {
                correct_series: correction.correctSeries,
                correct_label: correction.correctLabel,
                note: correction.note,
              });
            }}
          >
            <label style={{ display: "grid", gap: 4, color: "var(--ax-text-2)", fontSize: 10 }}>
              {tt("correctSeries", "正确系列")}
              <input
                className="ax-input"
                value={correction.correctSeries}
                onChange={(event) =>
                  setCorrectionDraft((current) => ({
                    ...current,
                    submissionId: String(row.id),
                    correctSeries: event.target.value,
                    correctLabel: current.submissionId === String(row.id) ? current.correctLabel : correction.correctLabel,
                    note: current.submissionId === String(row.id) ? current.note : "",
                  }))
                }
                placeholder="LAB / PRO / AIR"
              />
            </label>
            <label style={{ display: "grid", gap: 4, color: "var(--ax-text-2)", fontSize: 10 }}>
              {tt("correctLabel", "正确标签")}
              <input
                className="ax-input"
                value={correction.correctLabel}
                onChange={(event) =>
                  setCorrectionDraft((current) => ({
                    ...current,
                    submissionId: String(row.id),
                    correctSeries: current.submissionId === String(row.id) ? current.correctSeries : correction.correctSeries,
                    correctLabel: event.target.value,
                    note: current.submissionId === String(row.id) ? current.note : "",
                  }))
                }
                placeholder="AF 90mm F3.5 DL"
              />
            </label>
            <label style={{ display: "grid", gap: 4, color: "var(--ax-text-2)", fontSize: 10 }}>
              {tt("note", "备注")}
              <textarea
                className="ax-input"
                rows={3}
                value={correction.note}
                onChange={(event) =>
                  setCorrectionDraft((current) => ({
                    ...current,
                    submissionId: String(row.id),
                    correctSeries: current.submissionId === String(row.id) ? current.correctSeries : correction.correctSeries,
                    correctLabel: current.submissionId === String(row.id) ? current.correctLabel : correction.correctLabel,
                    note: event.target.value,
                  }))
                }
                placeholder={tt("notePlaceholder", "解释这次纠错，方便后续学习。")}
              />
            </label>
            <button type="submit" className="ax-btn ax-btn--primary" disabled={busy === `correct:${row.id}`}>
              {busy === `correct:${row.id}` ? tt("saving", "保存中...") : tt("saveCorrection", "保存产品纠错")}
            </button>
          </form>
        </DetailBlock>

        <DetailBlock title={tt("viaAnalysis", "Via 内容分析")}>
          <p style={{ margin: 0, color: "var(--ax-text-3)", fontSize: 11, lineHeight: 1.7 }}>{summary}</p>
          <ChipLine
            items={[
              row.content_genre || analysis.content_genre,
              row.vertical_category || analysis.vertical_category,
              analysis.confidence ? `confidence ${String(analysis.confidence)}` : "",
            ].filter(Boolean)}
          />
        </DetailBlock>

        <DetailBlock title={tt("scoreTitle", "内容评分")}>
          <ScorePair
            left={{ label: "品牌曝光", value: brandScore || Number(row.final_score || 0) / 40 }}
            right={{ label: "故事说服力", value: storyScore || Number(row.creator_score || 0) / 10 }}
          />
          <MetricBar label="技术完成度" value={techScore} />
          <MetricBar label="营销潜力" value={marketingScore} />
        </DetailBlock>

        {timestamps.length ? (
          <DetailBlock title="时间线">
            <div style={{ display: "grid", gap: 6 }}>
              {timestamps.map((item, index) => {
                const rec = parseAnalysis(item);
                const time = pickString(rec, ["time", "timestamp", "at"], `#${index + 1}`);
                const label = pickString(rec, ["label", "tag", "scene"], "");
                const text = pickString(rec, ["note", "description", "event", "summary"], formatItem(item));
                return (
                  <div key={`${time}-${index}`} style={{ display: "grid", gridTemplateColumns: "48px 1fr", gap: 8, fontSize: 10, color: "var(--ax-text-3)" }}>
                    <span className="ax-mono" style={{ color: "var(--ax-status-review)" }}>{time}</span>
                    <span>{label ? `${label} · ` : ""}{text}</span>
                  </div>
                );
              })}
            </div>
          </DetailBlock>
        ) : null}

        {improvements.length ? (
          <DetailBlock title="改善建议">
            <div style={{ display: "grid", gap: 8 }}>
              {improvements.map((item, index) => {
                const rec = parseAnalysis(item);
                const priority = pickString(rec, ["priority"], "medium");
                const area = pickString(rec, ["area", "title"], `建议 ${index + 1}`);
                const suggestion = pickString(rec, ["suggestion", "problem", "expected_improvement"], formatItem(item));
                return (
                  <div key={`${area}-${index}`} style={{ borderTop: index ? "0.5px solid var(--ax-border-1)" : "none", paddingTop: index ? 8 : 0 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: priority === "high" ? "var(--ax-status-alert)" : "var(--ax-status-review)" }}>
                      {priority.toUpperCase()} · {area}
                    </div>
                    <div style={{ marginTop: 3, fontSize: 11, color: "var(--ax-text-3)", lineHeight: 1.6 }}>{suggestion}</div>
                  </div>
                );
              })}
            </div>
          </DetailBlock>
        ) : null}
      </div>
    </aside>
  );
}

function DetailBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ borderBottom: "0.5px solid var(--ax-border-1)", paddingBottom: 12, marginBottom: 12 }}>
      <SectionLabel>{title}</SectionLabel>
      <div style={{ marginTop: 8 }}>{children}</div>
    </section>
  );
}

function FactGrid({ items }: { items: Array<[string, unknown]> }) {
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {items.map(([label, value]) => (
        <div key={label} style={{ display: "grid", gridTemplateColumns: "70px 1fr", gap: 8, fontSize: 11 }}>
          <span style={{ color: "var(--ax-text-1)" }}>{label}</span>
          <strong style={{ color: "var(--ax-text-5)", fontWeight: 600 }}>{formatItem(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function ChipLine({ items, tone = "default" }: { items: unknown[]; tone?: "default" | "warm" }) {
  const clean = items.map(formatItem).filter((item) => item && item !== "—").slice(0, 8);
  if (!clean.length) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 8 }}>
      {clean.map((item) => (
        <span
          key={item}
          style={{
            border: "0.5px solid var(--ax-border-2)",
            borderRadius: 999,
            padding: "3px 7px",
            fontSize: 10,
            color: tone === "warm" ? "var(--ax-status-review)" : "var(--ax-text-3)",
            background: tone === "warm" ? "rgba(255, 122, 24, 0.08)" : "var(--ax-bg-2)",
          }}
        >
          {item}
        </span>
      ))}
    </div>
  );
}

function ScorePair({ left, right }: { left: { label: string; value: number }; right: { label: string; value: number } }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 10 }}>
      {[left, right].map((item) => (
        <div key={item.label} style={{ border: "0.5px solid rgba(255, 122, 24, 0.35)", borderRadius: 6, padding: 10, textAlign: "center", background: "rgba(255, 122, 24, 0.06)" }}>
          <div className="ax-num" style={{ fontSize: 24, fontWeight: 700, color: "var(--ax-status-review)" }}>
            {Number(item.value || 0).toFixed(1)}
          </div>
          <div style={{ fontSize: 10, color: "var(--ax-text-2)" }}>{item.label}</div>
        </div>
      ))}
    </div>
  );
}

function MetricBar({ label, value }: { label: string; value: number }) {
  const normalized = Math.max(0, Math.min(10, Number(value || 0)));
  return (
    <div style={{ display: "grid", gridTemplateColumns: "84px 1fr 24px", gap: 8, alignItems: "center", fontSize: 10, color: "var(--ax-text-2)", marginTop: 6 }}>
      <span>{label}</span>
      <span style={{ height: 5, borderRadius: 999, background: "var(--ax-bg-3)", overflow: "hidden" }}>
        <i style={{ display: "block", height: "100%", width: `${normalized * 10}%`, background: normalized >= 7 ? "var(--ax-status-pass)" : "var(--ax-status-review)" }} />
      </span>
      <strong className="ax-num" style={{ color: "var(--ax-text-5)" }}>{normalized.toFixed(0)}</strong>
    </div>
  );
}

function ManualSubmissionForm({
  busy,
  onSubmit,
  onCancel,
}: {
  busy: boolean;
  onSubmit: (e: FormEvent<HTMLFormElement>) => void;
  onCancel: () => void;
}) {
  return (
    <form
      onSubmit={onSubmit}
      className="ax-card"
      style={{ marginBottom: 16, display: "grid", gap: 12 }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div>
          <SectionLabel>Manual submission</SectionLabel>
          <div style={{ marginTop: 4, color: "var(--ax-text-4)", fontSize: 13 }}>
            直接写入 `/api/admin/submissions/manual`，用于补录 v1 里已有的手动投稿能力。
          </div>
        </div>
        <button type="button" className="ax-btn ax-btn--sm" onClick={onCancel}>
          关闭
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10 }}>
        <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
          平台
          <select name="platform" className="ax-input" defaultValue="Uploaded Video">
            <option>Uploaded Video</option>
            <option>TikTok</option>
            <option>Instagram</option>
            <option>YouTube</option>
            <option>Facebook</option>
            <option>Reddit</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
          Handle
          <input name="extracted_handle" className="ax-input" placeholder="@creator" />
        </label>
        <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
          状态
          <select name="detection_status" className="ax-input" defaultValue="confirmed">
            <option value="confirmed">confirmed</option>
            <option value="suspected">suspected</option>
            <option value="not_detected">not_detected</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
          产品系列
          <input name="product_series" className="ax-input" placeholder="AIR / LAB / PRO" />
        </label>
        <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10, gridColumn: "span 2" }}>
          URL
          <input name="url" className="ax-input" placeholder="https://..." />
        </label>
        <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10, gridColumn: "span 2" }}>
          标题
          <input name="title" className="ax-input" placeholder="视频标题" />
        </label>
        <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
          Campaign
          <input name="final_score" className="ax-input" type="number" min="0" defaultValue="0" />
        </label>
        <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
          Creator
          <input name="creator_score" className="ax-input" type="number" min="0" defaultValue="0" />
        </label>
        <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
          Views
          <input name="views" className="ax-input" type="number" min="0" defaultValue="0" />
        </label>
        <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
          Likes
          <input name="likes" className="ax-input" type="number" min="0" defaultValue="0" />
        </label>
        <input name="product_label" type="hidden" />
        <input name="comments" type="hidden" defaultValue="0" />
        <input name="shares" type="hidden" defaultValue="0" />
        <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10, gridColumn: "span 4" }}>
          Recommendation / memo
          <input name="recommendation" className="ax-input" placeholder="Manually added by admin" />
        </label>
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button type="button" className="ax-btn" onClick={onCancel}>取消</button>
        <button type="submit" className="ax-btn ax-btn--primary" disabled={busy}>
          {busy ? "写入中…" : "+ 添加投稿"}
        </button>
      </div>
    </form>
  );
}

export default OperationsTab;
