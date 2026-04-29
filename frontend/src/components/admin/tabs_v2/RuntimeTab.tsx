/**
 * Runtime tab v2 — infra & trust
 *
 * Shows:
 *   - Integrations (11 integrations, health status)
 *   - Trust (users, events, rules)
 *   - Staff (members, roles)
 *   - System (runtime snapshot: scheduler, workers, DB actor)
 *
 * Data: fetchAdminSystemSnapshot + fetchAdminRuntimeSnapshot (both)
 */
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  clearAdminSystemCache,
  clearRuntimeCacheTier,
  createAdminApiToken,
  fetchAdminRuntimeSnapshot,
  fetchAdminSystemSnapshot,
  inviteAdminStaff,
  reactivateAdminStaff,
  revokeAdminApiToken,
  runAdminIntegrationAction,
  runAdminIntegrationHealth,
  runAdminIntegrationHealthAll,
  suspendAdminStaff,
  updateAdminStaff,
  updateAdminTrustRule,
  type AdminRuntimeSnapshot,
  type AdminSystemSnapshot,
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
  SectionLabel,
  SegButton,
  StatusPill,
  type DataColumn,
} from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

type Section = "integrations" | "trust" | "staff" | "tokens" | "system";
type Row = Record<string, unknown>;
type RuleDraft = { id: number; event_kind: string; delta: string; description: string; enabled: boolean };

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asNumber(value: unknown, fallback = 0): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function formatDurationSeconds(value: unknown): string {
  const seconds = asNumber(value, -1);
  if (seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function text(...values: unknown[]): string {
  for (const value of values) {
    const next = String(value ?? "").trim();
    if (next) return next;
  }
  return "";
}

function boolish(value: unknown): boolean {
  return value === true || value === 1 || value === "1" || String(value).toLowerCase() === "true";
}

function rowId(value: Row): number {
  const id = Number(value.id);
  return Number.isFinite(id) ? id : 0;
}

const inputStyle = {
  background: "var(--ax-bg-0)",
  border: "0.5px solid var(--ax-border-3)",
  borderRadius: 6,
  color: "var(--ax-text-5)",
  fontFamily: "inherit",
  fontSize: 11,
  minWidth: 0,
  outline: "none",
  padding: "8px 10px",
};

export function RuntimeTab({ token }: Props) {
  const { t } = useTranslation();
  const tt = (key: string, fallback: string, options: Record<string, unknown> = {}) =>
    String(t(`admin.runtime.v2.${key}`, { defaultValue: fallback, ...options }));
  const [runtime, setRuntime] = useState<AdminRuntimeSnapshot | null>(null);
  const [system, setSystem] = useState<AdminSystemSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [section, setSection] = useState<Section>("integrations");
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState<{ tone: "pass" | "block"; body: string } | null>(null);
  const [ruleDraft, setRuleDraft] = useState<RuleDraft | null>(null);
  const [staffFormOpen, setStaffFormOpen] = useState(false);
  const [staffForm, setStaffForm] = useState({ email: "", name: "", role: "readonly" });
  const [tokenFormOpen, setTokenFormOpen] = useState(false);
  const [tokenForm, setTokenForm] = useState({ name: "", scope: "readonly", expires_days: "90" });
  const [createdToken, setCreatedToken] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    Promise.allSettled([
      fetchAdminRuntimeSnapshot(token),
      fetchAdminSystemSnapshot(token),
    ]).then((results) => {
      if (!alive) return;
      if (results[0].status === "fulfilled") setRuntime(results[0].value);
      if (results[1].status === "fulfilled") setSystem(results[1].value);
      if (results.every((r) => r.status === "rejected")) {
        setError("Runtime + System 快照都拉取失败");
      }
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [token, tick]);

  const refresh = () => setTick((n) => n + 1);

  const handleClearSystemCache = async (prefix: string) => {
    const label = prefix || tt("cache.allCache", "全部缓存");
    if (!window.confirm(tt("cache.confirmClear", "确认清理 {{label}}？这会删除本地缓存并让后续请求重新构建。", { label }))) return;
    setBusy(`system-cache:${prefix || "all"}`);
    setMessage(null);
    try {
      const result = await clearAdminSystemCache(token, prefix);
      setMessage({ tone: "pass", body: tt("cache.cleared", "缓存已清理：{{count}}", { count: String(result.cleared ?? result.keys_deleted ?? 0) }) });
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("cache.clearFailed", "清缓存失败") });
    } finally {
      setBusy("");
    }
  };

  const handleClearRuntimeTier = async (tier: string) => {
    if (!window.confirm(tt("cache.confirmClearTier", "确认清理 runtime cache tier {{tier}}？", { tier }))) return;
    setBusy(`runtime-cache:${tier}`);
    setMessage(null);
    try {
      const result = await clearRuntimeCacheTier(token, tier);
      if (result.error) throw new Error(String(result.error));
      setMessage({ tone: "pass", body: tt("cache.tierCleared", "{{tier}} 已清理：{{count}}", { tier, count: String(result.keys_deleted ?? 0) }) });
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("cache.clearFailed", "清缓存失败") });
    } finally {
      setBusy("");
    }
  };

  const handleIntegrationAction = async (row: Row, action: "enable" | "disable" | "test" | "health") => {
    const id = rowId(row);
    const name = text(row.service_name, row.name, row.id);
    if (!id) return;
    if (action === "disable" && !window.confirm(tt("integrations.confirmDisable", "确认停用 {{name}}？", { name }))) return;
    setBusy(`integration:${id}:${action}`);
    setMessage(null);
    try {
      const result = action === "health"
        ? await runAdminIntegrationHealth(token, id)
        : await runAdminIntegrationAction(token, id, action);
      const status = text(result.status, result.message, result.ok ? "ok" : "");
      setMessage({ tone: "pass", body: tt("integrations.updated", "{{name}} 操作完成：{{status}}", { name, status: status || action }) });
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("integrations.failed", "集成操作失败") });
    } finally {
      setBusy("");
    }
  };

  const handleIntegrationHealthAll = async () => {
    setBusy("integrations:health-all");
    setMessage(null);
    try {
      const result = await runAdminIntegrationHealthAll(token);
      setMessage({ tone: "pass", body: tt("integrations.healthAllDone", "全部集成检测完成：{{count}} 项", { count: String((result.results as unknown[])?.length ?? 0) }) });
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("integrations.failed", "集成操作失败") });
    } finally {
      setBusy("");
    }
  };

  const openRuleEditor = (row: Row) => {
    const id = rowId(row);
    if (!id) return;
    setRuleDraft({
      id,
      event_kind: text(row.event_kind, row.kind, `rule_${id}`),
      delta: String(row.delta ?? 0),
      description: text(row.description),
      enabled: !("enabled" in row) || boolish(row.enabled),
    });
  };

  const submitRule = async () => {
    if (!ruleDraft) return;
    const delta = Math.round(Number(ruleDraft.delta));
    if (!Number.isFinite(delta)) {
      setMessage({ tone: "block", body: tt("trust.deltaInvalid", "Trust delta 必须是数字") });
      return;
    }
    setBusy(`trust-rule:${ruleDraft.id}`);
    setMessage(null);
    try {
      await updateAdminTrustRule(token, ruleDraft.id, {
        delta,
        description: ruleDraft.description,
        enabled: ruleDraft.enabled,
      });
      setMessage({ tone: "pass", body: tt("trust.ruleSaved", "Trust 规则已保存：{{kind}}", { kind: ruleDraft.event_kind }) });
      setRuleDraft(null);
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("trust.ruleSaveFailed", "Trust 规则保存失败") });
    } finally {
      setBusy("");
    }
  };

  const submitStaffInvite = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy("staff:invite");
    setMessage(null);
    try {
      await inviteAdminStaff(token, {
        email: staffForm.email.trim(),
        name: staffForm.name.trim(),
        role: staffForm.role,
      });
      setMessage({ tone: "pass", body: tt("staff.invited", "Staff 邀请已创建：{{email}}", { email: staffForm.email.trim() }) });
      setStaffForm({ email: "", name: "", role: "readonly" });
      setStaffFormOpen(false);
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("staff.inviteFailed", "邀请 staff 失败") });
    } finally {
      setBusy("");
    }
  };

  const handleStaffRole = async (row: Row) => {
    const id = rowId(row);
    if (!id) return;
    const role = window.prompt(tt("staff.rolePrompt", "新的 staff 角色"), text(row.role, "readonly"));
    if (!role) return;
    setBusy(`staff:${id}:role`);
    setMessage(null);
    try {
      await updateAdminStaff(token, id, { role });
      setMessage({ tone: "pass", body: tt("staff.updated", "Staff 已更新") });
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("staff.updateFailed", "Staff 更新失败") });
    } finally {
      setBusy("");
    }
  };

  const handleStaffActive = async (row: Row, action: "suspend" | "reactivate") => {
    const id = rowId(row);
    if (!id) return;
    const label = text(row.user_email, row.email, row.user_name, row.name, `#${id}`);
    const reason = action === "suspend"
      ? window.prompt(tt("staff.suspendReason", "停用原因"), "admin suspended from runtime tab")
      : "";
    if (action === "suspend" && !reason) return;
    setBusy(`staff:${id}:${action}`);
    setMessage(null);
    try {
      if (action === "suspend") {
        await suspendAdminStaff(token, id, reason || "");
      } else {
        await reactivateAdminStaff(token, id);
      }
      setMessage({ tone: "pass", body: tt("staff.activeUpdated", "{{label}} 状态已更新", { label }) });
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("staff.updateFailed", "Staff 更新失败") });
    } finally {
      setBusy("");
    }
  };

  const submitToken = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy("token:create");
    setMessage(null);
    setCreatedToken(null);
    try {
      const result = await createAdminApiToken(token, {
        name: tokenForm.name.trim(),
        scope: tokenForm.scope,
        expires_days: Math.max(1, Math.round(Number(tokenForm.expires_days || 90))),
      });
      setCreatedToken(text(result.token));
      setMessage({ tone: "pass", body: tt("tokens.created", "API token 已创建；完整 token 只显示一次。") });
      setTokenForm({ name: "", scope: "readonly", expires_days: "90" });
      setTokenFormOpen(false);
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("tokens.createFailed", "创建 API token 失败") });
    } finally {
      setBusy("");
    }
  };

  const handleRevokeToken = async (row: Row) => {
    const id = rowId(row);
    const label = text(row.name, row.token_prefix, `#${id}`);
    if (!id || !window.confirm(tt("tokens.confirmRevoke", "确认吊销 API token {{label}}？", { label }))) return;
    setBusy(`token:${id}:revoke`);
    setMessage(null);
    try {
      await revokeAdminApiToken(token, id);
      setMessage({ tone: "pass", body: tt("tokens.revoked", "API token 已吊销：{{label}}", { label }) });
      refresh();
    } catch (err) {
      setMessage({ tone: "block", body: err instanceof Error ? err.message : tt("tokens.revokeFailed", "吊销 API token 失败") });
    } finally {
      setBusy("");
    }
  };

  const kpis = useMemo(() => {
    const is = (system?.integrationsSummary || {}) as Record<string, unknown>;
    return [
      { label: tt("kpis.integrations", "集成"), value: Number(is.total || 0) },
      { label: tt("kpis.healthy", "健康"), value: Number(is.healthy || 0) },
      { label: tt("kpis.degraded", "降级"), value: Number(is.degraded || 0) },
      { label: tt("kpis.failing", "失败"), value: Number(is.failing || 0) },
    ];
  }, [system, t]); // eslint-disable-line react-hooks/exhaustive-deps

  const queueStats = useMemo(() => asRecord(asRecord(runtime?.runtime).queue), [runtime]);
  const queueSummary = useMemo(() => asRecord(queueStats.summary), [queueStats]);
  const queueKpis = useMemo(() => {
    return [
      {
        label: tt("queue.waiting", "等待"),
        value: asNumber(queueSummary.waiting),
        hint: tt("queue.waitingHint", "待处理"),
      },
      {
        label: tt("queue.processing", "处理中"),
        value: asNumber(queueSummary.processing),
        hint: tt("queue.processingHint", "处理中"),
      },
      {
        label: tt("queue.failed", "失败"),
        value: asNumber(queueSummary.failed),
        hint: tt("queue.failedHint", "失败"),
        delta: asNumber(queueSummary.failed) > 0 ? { text: tt("queue.needsReview", "需要查看"), tone: "down" as const } : undefined,
      },
      {
        label: tt("queue.avgTime", "平均耗时"),
        value: formatDurationSeconds(queueSummary.avg_duration_seconds),
        hint: tt("queue.avgTimeHint", "平均耗时"),
      },
      {
        label: tt("queue.eta", "预计等待"),
        value: formatDurationSeconds(queueSummary.eta_wait_seconds),
        hint: tt("queue.etaHint", "预计等待"),
      },
      {
        label: tt("queue.workers", "Workers"),
        value: asNumber(queueSummary.configured_concurrency),
        hint: `${asNumber(queueSummary.worker_processes)} × ${asNumber(queueSummary.worker_async_consumers)}`,
      },
    ];
  }, [queueSummary, t]); // eslint-disable-line react-hooks/exhaustive-deps

  const queueRows = useMemo(() => {
    const byType = asRecord(queueSummary.by_job_type);
    return Object.entries(byType).map(([jobType, counts]) => ({
      job_type: jobType,
      ...asRecord(counts),
    }));
  }, [queueSummary]);

  const integrationList = useMemo(() => {
    const byCat = system?.integrationsByCategory || {};
    const flat: Array<Record<string, unknown> & { _cat: string }> = [];
    Object.entries(byCat).forEach(([cat, items]) => {
      (items as Record<string, unknown>[]).forEach((it) => flat.push({ ...it, _cat: cat }));
    });
    return flat;
  }, [system]);

  const trustRuleRows = useMemo<Row[]>(() => {
    const rules = asRecord(system?.trustRules);
    return [
      ...((Array.isArray(rules.positive) ? rules.positive : []) as Row[]),
      ...((Array.isArray(rules.negative) ? rules.negative : []) as Row[]),
    ];
  }, [system]);

  const apiTokenRows = useMemo<Row[]>(() => system?.apiTokens || [], [system]);

  const intCols: DataColumn<Record<string, unknown>>[] = [
    {
      key: "name",
      label: tt("columns.integration", "集成"),
      width: "1.8fr",
      render: (r) => (
        <div>
          <div style={{ color: "var(--ax-text-5)" }}>
            {String(r.name || r.service_name || r.key || r.id || "—")}
          </div>
          <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            {String(r._cat || r.category || "")}
          </div>
        </div>
      ),
    },
    {
      key: "status",
      label: tt("columns.status", "状态"),
      width: "100px",
      render: (r) => {
        const s = String(r.status || r.health || "unknown").toLowerCase();
        const tone =
          s === "healthy" || s === "ok"
            ? "pass"
            : s === "degraded" || s === "stale"
            ? "queue"
            : s === "failing" || s === "error"
            ? "block"
            : "idle";
        return <StatusPill tone={tone as never}>{s.toUpperCase()}</StatusPill>;
      },
    },
    {
      key: "lastChecked",
      label: tt("columns.lastChecked", "最近检查"),
      width: "140px",
      render: (r) => (
        <span style={{ color: "var(--ax-text-2)", fontSize: 10 }}>
          {r.last_health_check
            ? new Date(String(r.last_health_check)).toLocaleString()
            : r.last_checked_at
            ? new Date(String(r.last_checked_at)).toLocaleString()
            : r.updated_at
            ? new Date(String(r.updated_at)).toLocaleString()
            : "—"}
        </span>
      ),
    },
    {
      key: "detail",
      label: tt("columns.detail", "详情"),
      width: "1fr",
      render: (r) => (
        <span style={{ color: "var(--ax-text-3)", fontSize: 10 }}>
          {String(r.detail || r.message || r.purpose || r.last_error || "—")}
        </span>
      ),
    },
    {
      key: "actions",
      label: tt("columns.actions", "操作"),
      width: "270px",
      render: (r) => {
        const id = rowId(r);
        const enabled = !("enabled" in r) || boolish(r.enabled);
        return (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }} onClick={(event) => event.stopPropagation()}>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              disabled={!id || busy === `integration:${id}:health`}
              onClick={() => handleIntegrationAction(r, "health")}
            >
              {tt("actions.health", "检测")}
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              disabled={!id || busy === `integration:${id}:test`}
              onClick={() => handleIntegrationAction(r, "test")}
            >
              {tt("actions.test", "测试")}
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: enabled ? "var(--ax-status-alert)" : "var(--ax-status-pass)" }}
              disabled={!id || busy === `integration:${id}:${enabled ? "disable" : "enable"}`}
              onClick={() => handleIntegrationAction(r, enabled ? "disable" : "enable")}
            >
              {enabled ? tt("actions.disable", "停用") : tt("actions.enable", "启用")}
            </button>
          </div>
        );
      },
    },
  ];

  const trustCols: DataColumn<Record<string, unknown>>[] = [
    {
      key: "user",
      label: tt("columns.user", "用户"),
      width: "1.6fr",
      render: (r) => (
        <div>
          <div className="ax-mono" style={{ fontSize: 10 }}>
            {String(r.creator_code || `V_${String(r.user_id || r.id).padStart(6, "0")}`)}
          </div>
          <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            {String(r.handle || r.name || r.email || "")}
          </div>
        </div>
      ),
    },
    {
      key: "trust",
      label: tt("columns.trust", "信任"),
      width: "80px",
      accent: true,
      render: (r) => {
        const t = Number(r.trust_score || 0);
        return (
          <span
            className="ax-num"
            style={{
              color:
                t >= 80
                  ? "var(--ax-status-pass)"
                  : t < 40
                  ? "var(--ax-status-alert)"
                  : "var(--ax-text-5)",
              fontWeight: 600,
            }}
          >
            {Math.round(t)}
          </span>
        );
      },
    },
    {
      key: "violations",
      label: tt("columns.violations", "违规"),
      width: "90px",
      render: (r) => (
        <span
          className="ax-num"
          style={{
            color: Number(r.violations || r.violation_count || 0) > 0 ? "var(--ax-status-alert)" : "var(--ax-text-4)",
          }}
        >
          {Number(r.violations || r.violation_count || 0)}
        </span>
      ),
    },
    {
      key: "lastFlag",
      label: tt("columns.lastFlag", "最近标记"),
      width: "140px",
      render: (r) => (
        <span style={{ color: "var(--ax-text-2)", fontSize: 10 }}>
          {r.last_flagged_at || r.flagged_at ? new Date(String(r.last_flagged_at || r.flagged_at)).toLocaleString() : "—"}
        </span>
      ),
    },
  ];

  const trustRuleCols: DataColumn<Row>[] = [
    {
      key: "event",
      label: tt("columns.rule", "规则"),
      width: "1.5fr",
      render: (r) => (
        <div>
          <div className="ax-mono" style={{ color: "var(--ax-text-5)", fontSize: 10 }}>
            {text(r.event_kind, r.kind, r.id) || "—"}
          </div>
          <div style={{ color: "var(--ax-text-1)", fontSize: 9 }}>{text(r.description) || "—"}</div>
        </div>
      ),
    },
    {
      key: "delta",
      label: "Delta",
      width: "80px",
      accent: true,
      render: (r) => {
        const delta = Number(r.delta || 0);
        return <span className="ax-num" style={{ color: delta < 0 ? "var(--ax-status-alert)" : "var(--ax-status-pass)" }}>{delta}</span>;
      },
    },
    {
      key: "enabled",
      label: tt("columns.enabled", "启用"),
      width: "90px",
      render: (r) => {
        const enabled = !("enabled" in r) || boolish(r.enabled);
        return <StatusPill tone={enabled ? "pass" : "idle"}>{enabled ? tt("status.enabled", "启用") : tt("status.disabled", "停用")}</StatusPill>;
      },
    },
    {
      key: "actions",
      label: tt("columns.actions", "操作"),
      width: "120px",
      render: (r) => (
        <button type="button" className="ax-btn ax-btn--sm" onClick={() => openRuleEditor(r)}>
          {tt("actions.edit", "编辑")}
        </button>
      ),
    },
  ];

  const staffCols: DataColumn<Record<string, unknown>>[] = [
    {
      key: "member",
      label: tt("columns.member", "成员"),
      width: "1.8fr",
      render: (r) => (
        <div>
          <div style={{ color: "var(--ax-text-5)" }}>
            {String(r.user_name || r.name || r.user_email || r.email || "—")}
          </div>
          <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>{String(r.user_email || r.email || "")}</div>
        </div>
      ),
    },
    {
      key: "role",
      label: tt("columns.role", "角色"),
      width: "120px",
      render: (r) => (
        <span style={{ color: "var(--ax-text-3)" }}>{String(r.role || "—")}</span>
      ),
    },
    {
      key: "status",
      label: tt("columns.status", "状态"),
      width: "100px",
      render: (r) => {
        const active = !("active" in r) || boolish(r.active);
        const s = active ? "active" : "suspended";
        const tone = s === "active" ? "pass" : s === "suspended" ? "block" : "idle";
        return <StatusPill tone={tone as never}>{s.toUpperCase()}</StatusPill>;
      },
    },
    {
      key: "lastLogin",
      label: tt("columns.lastLogin", "最近登录"),
      width: "140px",
      render: (r) => (
        <span style={{ color: "var(--ax-text-2)", fontSize: 10 }}>
          {r.last_active_at || r.last_login_at || r.last_login ? new Date(String(r.last_active_at || r.last_login_at || r.last_login)).toLocaleString() : "—"}
        </span>
      ),
    },
    {
      key: "actions",
      label: tt("columns.actions", "操作"),
      width: "240px",
      render: (r) => {
        const id = rowId(r);
        const active = !("active" in r) || boolish(r.active);
        return (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }} onClick={(event) => event.stopPropagation()}>
            <button type="button" className="ax-btn ax-btn--sm" disabled={!id || busy === `staff:${id}:role`} onClick={() => handleStaffRole(r)}>
              {tt("actions.role", "角色")}
            </button>
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: active ? "var(--ax-status-alert)" : "var(--ax-status-pass)" }}
              disabled={!id || busy === `staff:${id}:${active ? "suspend" : "reactivate"}`}
              onClick={() => handleStaffActive(r, active ? "suspend" : "reactivate")}
            >
              {active ? tt("actions.suspend", "停用") : tt("actions.reactivate", "恢复")}
            </button>
          </div>
        );
      },
    },
  ];

  const tokenCols: DataColumn<Row>[] = [
    {
      key: "token",
      label: tt("columns.token", "Token"),
      width: "1.6fr",
      render: (r) => (
        <div>
          <div style={{ color: "var(--ax-text-5)" }}>{text(r.name, "unnamed")}</div>
          <div className="ax-mono" style={{ color: "var(--ax-text-1)", fontSize: 9 }}>
            {text(r.token_prefix, r.prefix, "—")}
          </div>
        </div>
      ),
    },
    {
      key: "scope",
      label: tt("columns.scope", "权限"),
      width: "100px",
      render: (r) => <span style={{ color: "var(--ax-text-3)" }}>{text(r.scope, "readonly")}</span>,
    },
    {
      key: "active",
      label: tt("columns.status", "状态"),
      width: "90px",
      render: (r) => {
        const active = !r.revoked_at && (!("active" in r) || boolish(r.active));
        return <StatusPill tone={active ? "pass" : "block"}>{active ? tt("status.active", "在线") : tt("status.revoked", "已吊销")}</StatusPill>;
      },
    },
    {
      key: "expires",
      label: tt("columns.expires", "过期"),
      width: "140px",
      render: (r) => <span style={{ color: "var(--ax-text-2)", fontSize: 10 }}>{text(r.expires_at) || "—"}</span>,
    },
    {
      key: "actions",
      label: tt("columns.actions", "操作"),
      width: "120px",
      render: (r) => (
        <button
          type="button"
          className="ax-btn ax-btn--sm"
          style={{ color: "var(--ax-status-alert)" }}
          disabled={!rowId(r) || Boolean(r.revoked_at) || busy === `token:${rowId(r)}:revoke`}
          onClick={() => handleRevokeToken(r)}
        >
          {tt("actions.revoke", "吊销")}
        </button>
      ),
    },
  ];

  const queueCols: DataColumn<Record<string, unknown>>[] = [
    {
      key: "job_type",
      label: tt("columns.jobType", "任务类型"),
      width: "1.7fr",
      render: (r) => (
        <span className="ax-mono" style={{ color: "var(--ax-text-5)", fontSize: 10 }}>
          {String(r.job_type || "unknown")}
        </span>
      ),
    },
    {
      key: "waiting",
      label: tt("queue.waiting", "等待"),
      width: "90px",
      accent: true,
      render: (r) => <span className="ax-num">{asNumber(r.waiting)}</span>,
    },
    {
      key: "processing",
      label: tt("queue.processing", "处理中"),
      width: "110px",
      render: (r) => <span className="ax-num">{asNumber(r.processing)}</span>,
    },
    {
      key: "failed",
      label: tt("queue.failed", "失败"),
      width: "90px",
      render: (r) => (
        <span className="ax-num" style={{ color: asNumber(r.failed) > 0 ? "var(--ax-status-alert)" : "var(--ax-text-4)" }}>
          {asNumber(r.failed)}
        </span>
      ),
    },
    {
      key: "completed",
      label: tt("columns.recentDone", "近期完成"),
      width: "110px",
      render: (r) => <span className="ax-num">{asNumber(r.completed)}</span>,
    },
  ];

  const sections: Array<{ key: Section; label: string }> = [
    { key: "integrations", label: `${tt("sections.integrations", "集成")} (${integrationList.length})` },
    { key: "trust", label: `${tt("sections.trust", "Trust")} (${(system?.trustUsers || []).length})` },
    { key: "staff", label: `${tt("sections.staff", "Staff")} (${(system?.staffMembers || []).length})` },
    { key: "tokens", label: `${tt("sections.tokens", "API Tokens")} (${apiTokenRows.length})` },
    { key: "system", label: tt("sections.system", "系统") },
  ];

  return (
    <div>
      <PageHeader
        title={tt("title", "运行态")}
        subtitle={tt("subtitle", "Integrations 健康 · Trust 系统 · Staff · 系统指标")}
        actions={
          <>
            <button type="button" className="ax-btn" onClick={handleIntegrationHealthAll} disabled={Boolean(busy)}>
              <Icons.trending /> {busy === "integrations:health-all" ? tt("actions.testing", "检测中…") : tt("actions.healthAll", "全量检测")}
            </button>
            <button type="button" className="ax-btn" onClick={() => setStaffFormOpen((value) => !value)} disabled={Boolean(busy)}>
              <Icons.plus /> {tt("staff.invite", "邀请 Staff")}
            </button>
            <button type="button" className="ax-btn" onClick={() => setTokenFormOpen((value) => !value)} disabled={Boolean(busy)}>
              <Icons.plus /> {tt("tokens.create", "创建 Token")}
            </button>
            <button type="button" className="ax-btn" onClick={() => handleClearSystemCache("admin_")} disabled={Boolean(busy)}>
              <Icons.close /> {busy === "system-cache:admin_" ? tt("cache.clearing", "清理中…") : tt("cache.clearAdmin", "清 admin cache")}
            </button>
            <button type="button" className="ax-btn" onClick={() => handleClearRuntimeTier("memory")} disabled={Boolean(busy)}>
              <Icons.close /> {busy === "runtime-cache:memory" ? tt("cache.clearing", "清理中…") : tt("cache.clearMemory", "清 memory cache")}
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

      {message ? (
        <div
          style={{
            padding: "8px 16px",
            background:
              message.tone === "pass"
                ? "rgba(99, 165, 30, 0.08)"
                : "rgba(209, 69, 32, 0.08)",
            color:
              message.tone === "pass"
                ? "var(--ax-status-pass)"
                : "var(--ax-status-alert)",
            fontSize: 11,
            borderBottom: "0.5px solid var(--ax-border-2)",
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <span>{message.body}</span>
          <span style={{ cursor: "pointer", color: "var(--ax-text-1)" }} onClick={() => setMessage(null)}>×</span>
        </div>
      ) : null}

      <div style={{ padding: 16 }}>
        {loading && !runtime && !system ? (
          <LoadingCard label={tt("loading", "加载 Runtime + System 数据…")} />
        ) : (
          <>
            {staffFormOpen ? (
              <form
                className="ax-card"
                onSubmit={submitStaffInvite}
                style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 150px auto", gap: 8, alignItems: "end", marginBottom: 16 }}
              >
                <label style={{ display: "grid", gap: 5 }}>
                  <span className="ax-label">{tt("staff.email", "邮箱")}</span>
                  <input
                    required
                    type="email"
                    style={inputStyle}
                    value={staffForm.email}
                    onChange={(event) => setStaffForm((current) => ({ ...current, email: event.target.value }))}
                    placeholder="ops@viltrox.com"
                  />
                </label>
                <label style={{ display: "grid", gap: 5 }}>
                  <span className="ax-label">{tt("staff.name", "姓名")}</span>
                  <input
                    style={inputStyle}
                    value={staffForm.name}
                    onChange={(event) => setStaffForm((current) => ({ ...current, name: event.target.value }))}
                    placeholder="Operations"
                  />
                </label>
                <label style={{ display: "grid", gap: 5 }}>
                  <span className="ax-label">{tt("staff.role", "角色")}</span>
                  <select
                    style={inputStyle}
                    value={staffForm.role}
                    onChange={(event) => setStaffForm((current) => ({ ...current, role: event.target.value }))}
                  >
                    {["readonly", "analyst", "operations", "admin"].map((role) => (
                      <option key={role} value={role}>{role}</option>
                    ))}
                  </select>
                </label>
                <button type="submit" className="ax-btn ax-btn--primary" disabled={busy === "staff:invite"}>
                  <Icons.plus /> {busy === "staff:invite" ? tt("staff.inviting", "邀请中…") : tt("staff.invite", "邀请 Staff")}
                </button>
              </form>
            ) : null}

            {tokenFormOpen ? (
              <form
                className="ax-card"
                onSubmit={submitToken}
                style={{ display: "grid", gridTemplateColumns: "1.5fr 160px 120px auto", gap: 8, alignItems: "end", marginBottom: 16 }}
              >
                <label style={{ display: "grid", gap: 5 }}>
                  <span className="ax-label">{tt("tokens.name", "Token 名称")}</span>
                  <input
                    required
                    style={inputStyle}
                    value={tokenForm.name}
                    onChange={(event) => setTokenForm((current) => ({ ...current, name: event.target.value }))}
                    placeholder="ci-readonly"
                  />
                </label>
                <label style={{ display: "grid", gap: 5 }}>
                  <span className="ax-label">{tt("tokens.scope", "权限")}</span>
                  <select
                    style={inputStyle}
                    value={tokenForm.scope}
                    onChange={(event) => setTokenForm((current) => ({ ...current, scope: event.target.value }))}
                  >
                    {["readonly", "ci", "admin"].map((scope) => (
                      <option key={scope} value={scope}>{scope}</option>
                    ))}
                  </select>
                </label>
                <label style={{ display: "grid", gap: 5 }}>
                  <span className="ax-label">{tt("tokens.expiresDays", "有效天数")}</span>
                  <input
                    min={1}
                    type="number"
                    style={inputStyle}
                    value={tokenForm.expires_days}
                    onChange={(event) => setTokenForm((current) => ({ ...current, expires_days: event.target.value }))}
                  />
                </label>
                <button type="submit" className="ax-btn ax-btn--primary" disabled={busy === "token:create"}>
                  <Icons.plus /> {busy === "token:create" ? tt("tokens.creating", "创建中…") : tt("tokens.create", "创建 Token")}
                </button>
              </form>
            ) : null}

            {createdToken ? (
              <div className="ax-card" style={{ marginBottom: 16, borderColor: "var(--ax-status-review)" }}>
                <SectionLabel>{tt("tokens.createdTitle", "完整 API Token，仅显示一次")}</SectionLabel>
                <code style={{ color: "var(--ax-text-5)", fontSize: 11, wordBreak: "break-all" }}>{createdToken}</code>
              </div>
            ) : null}

            <div style={{ marginBottom: 16 }}>
              <KPIGrid items={kpis} columns={4} />
            </div>

            <div className="ax-card" style={{ marginBottom: 16 }}>
              <SectionLabel>{tt("queue.title", "队列面板")}</SectionLabel>
              <div style={{ marginBottom: 12 }}>
                <KPIGrid items={queueKpis} columns={6} />
              </div>
              {queueRows.length > 0 ? (
                <DataTable
                  columns={queueCols}
                  rows={queueRows}
                  rowKey={(r, i) => String(r.job_type || i)}
                  showCheckbox={false}
                />
              ) : (
                <EmptyCard label={tt("empty.queue", "暂无队列任务")} hint={tt("empty.queueHint", "有 link / upload 进入处理后这里会显示等待与处理状态")} />
              )}
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
              {section === "integrations" ? (
                integrationList.length === 0 ? (
                  <EmptyCard label={tt("empty.integrations", "暂无集成配置")} />
                ) : (
                  <DataTable
                    columns={intCols}
                    rows={integrationList}
                    rowKey={(r, i) => String(r.id || r.key || i)}
                    showCheckbox={false}
                  />
                )
              ) : section === "trust" ? (
                <div>
                  {trustRuleRows.length > 0 ? (
                    <div style={{ borderBottom: "0.5px solid var(--ax-border-2)" }}>
                      <div style={{ padding: "12px 12px 0" }}>
                        <SectionLabel>{tt("trust.rules", "Trust 规则")}</SectionLabel>
                      </div>
                      <DataTable
                        columns={trustRuleCols}
                        rows={trustRuleRows}
                        rowKey={(r, i) => String(r.id || r.event_kind || i)}
                        showCheckbox={false}
                      />
                    </div>
                  ) : null}
                  {ruleDraft ? (
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1.2fr 110px 2fr 120px auto",
                        gap: 8,
                        alignItems: "end",
                        borderBottom: "0.5px solid var(--ax-border-2)",
                        padding: 12,
                      }}
                    >
                      <label style={{ display: "grid", gap: 5 }}>
                        <span className="ax-label">{tt("trust.rule", "规则")}</span>
                        <input style={inputStyle} value={ruleDraft.event_kind} disabled />
                      </label>
                      <label style={{ display: "grid", gap: 5 }}>
                        <span className="ax-label">Delta</span>
                        <input
                          style={inputStyle}
                          value={ruleDraft.delta}
                          onChange={(event) => setRuleDraft((current) => current ? { ...current, delta: event.target.value } : current)}
                        />
                      </label>
                      <label style={{ display: "grid", gap: 5 }}>
                        <span className="ax-label">{tt("columns.detail", "详情")}</span>
                        <input
                          style={inputStyle}
                          value={ruleDraft.description}
                          onChange={(event) => setRuleDraft((current) => current ? { ...current, description: event.target.value } : current)}
                        />
                      </label>
                      <label style={{ display: "flex", gap: 6, alignItems: "center", color: "var(--ax-text-3)", fontSize: 11, paddingBottom: 8 }}>
                        <input
                          type="checkbox"
                          checked={ruleDraft.enabled}
                          onChange={(event) => setRuleDraft((current) => current ? { ...current, enabled: event.target.checked } : current)}
                        />
                        {tt("columns.enabled", "启用")}
                      </label>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button type="button" className="ax-btn ax-btn--primary" disabled={busy === `trust-rule:${ruleDraft.id}`} onClick={submitRule}>
                          {tt("actions.save", "保存")}
                        </button>
                        <button type="button" className="ax-btn" onClick={() => setRuleDraft(null)}>
                          {tt("actions.cancel", "取消")}
                        </button>
                      </div>
                    </div>
                  ) : null}
                  <div style={{ padding: "12px 12px 0" }}>
                    <SectionLabel>{tt("trust.users", "Trust 用户")}</SectionLabel>
                  </div>
                  {(system?.trustUsers || []).length === 0 ? (
                    <EmptyCard label={tt("empty.trust", "暂无 Trust 数据")} hint={tt("empty.trustHint", "系统仍在累积信任分数")} />
                  ) : (
                    <DataTable
                      columns={trustCols}
                      rows={system?.trustUsers as Record<string, unknown>[]}
                      rowKey={(r, i) => String(r.user_id || r.id || i)}
                      showCheckbox={false}
                    />
                  )}
                </div>
              ) : section === "staff" ? (
                (system?.staffMembers || []).length === 0 ? (
                  <EmptyCard label={tt("empty.staff", "暂无 staff 成员")} />
                ) : (
                  <DataTable
                    columns={staffCols}
                    rows={system?.staffMembers as Record<string, unknown>[]}
                    rowKey={(r, i) => String(r.id || r.email || i)}
                    showCheckbox={false}
                  />
                )
              ) : section === "tokens" ? (
                apiTokenRows.length === 0 ? (
                  <EmptyCard label={tt("empty.tokens", "暂无 API tokens")} hint={tt("empty.tokensHint", "创建 token 后会显示 prefix、scope、过期时间和吊销状态。")} />
                ) : (
                  <DataTable
                    columns={tokenCols}
                    rows={apiTokenRows}
                    rowKey={(r, i) => String(r.id || r.token_prefix || i)}
                    showCheckbox={false}
                  />
                )
              ) : (
                <div className="ax-card" style={{ borderRadius: 0, border: 0 }}>
                  <SectionLabel>{tt("systemRuntime", "系统运行时")}</SectionLabel>
                  <pre
                    style={{
                      margin: 0,
                      fontFamily: "var(--ax-font-mono)",
                      fontSize: 10,
                      color: "var(--ax-text-4)",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-all",
                      maxHeight: 400,
                      overflow: "auto",
                    }}
                  >
                    {JSON.stringify(runtime || {}, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default RuntimeTab;
