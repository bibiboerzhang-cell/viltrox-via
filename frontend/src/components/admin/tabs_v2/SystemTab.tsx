import { useEffect, useMemo, useState, type FormEvent } from "react";

import type { AuthUser } from "../../../lib/api";
import {
  fetchAdminSystemSnapshot,
  fetchSystemModels,
  fetchSystemProviders,
  fetchSystemUsage,
  inviteStaffMember,
  probeSystemProvider,
  requestSystemModelSwitch,
  restartSystemRoles,
  rotateSystemProviderKey,
  updateStaffPermissions,
  type AdminSystemSnapshot,
  type SystemModelsSnapshot,
  type SystemProvidersSnapshot,
  type SystemUsageSnapshot,
} from "../../../services/admin.service";
import { Icons } from "../Icons";
import {
  DataTable,
  ErrorCard,
  KPIGrid,
  LoadingCard,
  PageHeader,
  SectionLabel,
  StatusPill,
  type DataColumn,
} from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

type Row = Record<string, unknown>;
type Section = "keys" | "usage" | "models" | "restart" | "members";

const TAB_KEYS = [
  "overview",
  "operations",
  "creators",
  "products",
  "analytics",
  "student",
  "via",
  "command",
  "runtime",
  "intelligence",
  "deepsight",
  "system",
  "kol_ops",
];

const SYSTEM_KEYS = ["system.api_keys", "system.usage", "system.models", "system.restart", "system.members"];

function str(value: unknown, fallback = "—") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function maskKey(value: unknown) {
  const text = str(value, "");
  if (!text) return "not configured";
  return `${text.slice(0, 15)}...`;
}

function statusTone(value: unknown): "pass" | "review" | "queue" | "new" | "active" | "idle" | "churn" | "block" | "flag" {
  const raw = String(value || "").toLowerCase();
  if (["healthy", "ok", "active"].includes(raw)) return "active";
  if (["down", "failed", "suspended"].includes(raw)) return "block";
  if (["unknown", "pending"].includes(raw)) return "review";
  return "idle";
}

function num(value: unknown, digits = 0) {
  const parsed = Number(value || 0);
  if (!Number.isFinite(parsed)) return digits ? "0.00" : "0";
  return parsed.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function usd(value: unknown) {
  return `$${num(value, 4)}`;
}

function defaultPerms(role: string) {
  const mode = role === "admin" ? "write" : "read";
  const base: Record<string, string> = {};
  TAB_KEYS.forEach((key) => { base[key] = mode; });
  base["system.api_keys"] = "read";
  base["system.usage"] = "read";
  base["system.models"] = "read";
  base["system.restart"] = "none";
  base["system.members"] = "none";
  return base;
}

export function SystemTab({ token, user }: Props) {
  const [section, setSection] = useState<Section>("keys");
  const [system, setSystem] = useState<AdminSystemSnapshot | null>(null);
  const [providers, setProviders] = useState<SystemProvidersSnapshot | null>(null);
  const [models, setModels] = useState<SystemModelsSnapshot | null>(null);
  const [usage, setUsage] = useState<SystemUsageSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [role, setRole] = useState("readonly");
  const [permissions, setPermissions] = useState<Record<string, string>>(defaultPerms("readonly"));
  const [busy, setBusy] = useState("");
  const [modelSelections, setModelSelections] = useState<Record<string, string>>({});
  const [modelConfirmPassword, setModelConfirmPassword] = useState("");
  const [restartRoles, setRestartRoles] = useState<Record<string, boolean>>({
    public: true,
    admin: true,
    worker: true,
    scheduler: true,
  });

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [systemSnapshot, providerSnapshot, modelSnapshot, usageSnapshot] = await Promise.all([
        fetchAdminSystemSnapshot(token),
        fetchSystemProviders(token).catch(() => ({ providers: [] })),
        fetchSystemModels(token).catch(() => ({
          available_models: {},
          task_model_binding: {},
          pricing_usd_per_1m_tokens: {},
        })),
        fetchSystemUsage(token).catch(() => ({
          window_days: 7,
          today: {},
          by_provider: [],
          by_task: [],
          daily: [],
          cost_basis: "unavailable",
        })),
      ]);
      setSystem(systemSnapshot);
      setProviders(providerSnapshot);
      setModels(modelSnapshot);
      setUsage(usageSnapshot);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    setPermissions(defaultPerms(role));
  }, [role]);

  const providerRows = providers?.providers || [];
  const modelRows = useMemo(() => Object.entries(models?.task_model_binding || {}).map(([task, model]) => ({ task, model })), [models]);

  useEffect(() => {
    if (!modelRows.length) return;
    setModelSelections((prev) => {
      const next = { ...prev };
      modelRows.forEach((row) => {
        if (!next[row.task]) next[row.task] = row.model;
      });
      return next;
    });
  }, [modelRows]);

  const kpis = [
    { label: "Providers", value: providerRows.length },
    { label: "Model tasks", value: modelRows.length },
    { label: "Staff", value: system?.staffMembers?.length || 0 },
    { label: "Audit rows", value: system?.auditLog?.length || 0 },
  ];

  const staffColumns: DataColumn<Row>[] = [
    { key: "name", label: "成员", width: "1.2fr", render: (r) => <strong>{str(r.name || r.email)}</strong> },
    { key: "role", label: "角色", width: "0.7fr", render: (r) => str(r.role) },
    { key: "status", label: "状态", width: "0.8fr", render: (r) => <StatusPill tone={statusTone(r.status)}>{str(r.status, "active")}</StatusPill> },
    { key: "owner", label: "Owner", width: "0.6fr", render: (r) => Number(r.is_owner || 0) === 1 ? "yes" : "—" },
    { key: "login", label: "最近登录", width: "1fr", render: (r) => str(r.last_login_at) },
  ];

  const submitInvite = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") || "").trim();
    if (!email.endsWith("@viltrox.com")) {
      setToast("只能邀请 @viltrox.com 邮箱");
      return;
    }
    setBusy("invite");
    try {
      await inviteStaffMember(token, { email, role, permissions });
      setToast("邀请已发送");
      event.currentTarget.reset();
      await load();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const saveFirstStaffPermissions = async () => {
    const first = system?.staffMembers?.[0];
    if (!first?.id) return;
    setBusy("permissions");
    try {
      await updateStaffPermissions(token, Number(first.id), permissions);
      setToast("权限矩阵已保存到第一位成员");
      await load();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const submitKeyRotate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy("rotate-key");
    try {
      const result = await rotateSystemProviderKey(token, {
        provider: String(form.get("provider") || ""),
        new_key: String(form.get("new_key") || ""),
        confirm_password: String(form.get("confirm_password") || ""),
        move_current_to_previous: true,
      });
      setToast(`Key rotated for ${result.provider}; restart required`);
      event.currentTarget.reset();
      await load();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const submitRestart = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const roles = Object.entries(restartRoles).filter(([, enabled]) => enabled).map(([key]) => key);
    setBusy("restart");
    try {
      const result = await restartSystemRoles(token, {
        roles,
        confirm_password: String(form.get("confirm_password") || ""),
      });
      setToast(result.enabled ? "Restart requested" : "Dry-run complete; set SYSTEM_RESTART_ENABLED=1 on server to enable");
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  if (loading) return <LoadingCard label="Loading System…" />;
  if (error) return <ErrorCard label="System 加载失败" detail={error} onRetry={load} />;

  return (
    <div>
      <PageHeader
        title="System"
        subtitle="API keys · usage · models · restart · members"
        actions={<button type="button" className="ax-btn" onClick={load}><Icons.command /> Refresh</button>}
      />

      <div style={{ padding: 16, display: "grid", gap: 12 }}>
        {toast ? <div className="ax-card" style={{ color: "var(--ax-text-5)" }}>{toast}</div> : null}
        <KPIGrid items={kpis} />

        <div className="ax-card" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {(["keys", "usage", "models", "restart", "members"] as Section[]).map((item) => (
            <button
              key={item}
              type="button"
              className={`ax-btn ax-btn--sm${section === item ? " is-active" : ""}`}
              onClick={() => setSection(item)}
            >
              {item}
            </button>
          ))}
        </div>

        {section === "keys" ? (
          <div style={{ display: "grid", gap: 12 }}>
            <div className="ax-card" style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10 }}>
              {["anthropic", "openai", "google", "apify", "resend"].map((name) => {
                const row = providerRows.find((item) => String(item.provider) === name) || {};
                return (
                  <div key={name} className="ax-card" style={{ background: "rgba(255,255,255,0.03)" }}>
                    <SectionLabel>{name}</SectionLabel>
                    <StatusPill tone={statusTone(row.latest_status)}>{str(row.latest_status, "unknown")}</StatusPill>
                    <div style={{ marginTop: 8, fontSize: 11, color: "var(--ax-text-2)" }}>key: {maskKey(row.key_prefix)}</div>
                    <div style={{ fontSize: 11, color: "var(--ax-text-2)" }}>last ok: {str(row.last_ok_at)}</div>
                    <button
                      type="button"
                      className="ax-btn ax-btn--sm"
                      style={{ marginTop: 8 }}
                      onClick={async () => {
                        setBusy(`probe:${name}`);
                        try {
                          await probeSystemProvider(token, name);
                          setToast(`${name} probe done`);
                          await load();
                        } catch (err) {
                          setToast(err instanceof Error ? err.message : String(err));
                        } finally {
                          setBusy("");
                        }
                      }}
                    >
                      {busy === `probe:${name}` ? "Probing…" : "Probe"}
                    </button>
                  </div>
                );
              })}
            </div>
            <form className="ax-card" onSubmit={submitKeyRotate} style={{ display: "grid", gridTemplateColumns: "0.8fr 1.8fr 1fr auto", gap: 8 }}>
              <select className="input" name="provider" defaultValue="anthropic">
                {["anthropic", "openai", "google", "apify", "resend"].map((provider) => <option key={provider} value={provider}>{provider}</option>)}
              </select>
              <input className="input" name="new_key" type="password" placeholder="new provider key" required />
              <input className="input" name="confirm_password" type="password" placeholder="admin password" required />
              <button className="ax-btn" type="submit" disabled={busy === "rotate-key"}>{busy === "rotate-key" ? "Rotating…" : "Rotate key"}</button>
            </form>
          </div>
        ) : null}

        {section === "usage" ? (
          <div style={{ display: "grid", gap: 12 }}>
            <div className="ax-card">
              <SectionLabel>用量看板</SectionLabel>
              <p style={{ color: "var(--ax-text-2)", fontSize: 12 }}>
                成本口径使用本地 ai_usage_log × model_pricing.py 估算，不依赖原厂账单。月度对账时再刷新真实账单写差异校准记录。
              </p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
                <Metric label="今日调用" value={num(usage?.today?.calls)} />
                <Metric label="今日成本" value={usd(usage?.today?.cost_usd)} />
                <Metric label={`${usage?.window_days || 7} 日记录`} value={num(usage?.daily?.reduce((sum, row) => sum + Number(row.calls || 0), 0))} />
                <Metric label="平均延迟" value={`${num(usage?.today?.avg_latency_ms)}ms`} />
              </div>
            </div>
            <div className="ax-card" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <UsageList title="Provider cost" rows={usage?.by_provider || []} primaryKey="provider" />
              <UsageList title="Task cost" rows={usage?.by_task || []} primaryKey="triggered_by" />
            </div>
          </div>
        ) : null}

        {section === "models" ? (
          <div className="ax-card" style={{ display: "grid", gap: 10 }}>
            <SectionLabel>模型配置</SectionLabel>
            {modelRows.map((row) => (
              <div key={row.task} style={{ display: "grid", gridTemplateColumns: "1.3fr 1.4fr 1fr auto", gap: 8, alignItems: "center", fontSize: 12 }}>
                <strong>{row.task}</strong>
                <select
                  className="input"
                  value={modelSelections[row.task] || row.model}
                  onChange={(event) => setModelSelections((prev) => ({ ...prev, [row.task]: event.target.value }))}
                >
                  {Array.from(new Set([
                    row.model,
                    ...Object.entries(models?.available_models || {}).flatMap(([provider, names]) =>
                      names.map((name) => `${provider}/${name}`),
                    ),
                  ])).map((binding) => (
                    <option key={`${row.task}:${binding}`} value={binding}>{binding}</option>
                  ))}
                </select>
                <input
                  className="input"
                  type="password"
                  placeholder="admin password"
                  value={modelConfirmPassword}
                  onChange={(event) => setModelConfirmPassword(event.target.value)}
                />
                <button
                  type="button"
                  className="ax-btn ax-btn--sm"
                  onClick={async () => {
                    const selected = modelSelections[row.task] || row.model;
                    setBusy(`model:${row.task}`);
                    try {
                      await requestSystemModelSwitch(token, {
                        task: row.task,
                        model: selected,
                        confirm_password: modelConfirmPassword,
                      });
                      setToast("模型已写入 .env；重启对应服务后生效");
                      setModelConfirmPassword("");
                      await load();
                    } catch (err) {
                      setToast(err instanceof Error ? err.message : String(err));
                    } finally {
                      setBusy("");
                    }
                  }}
                >
                  {busy === `model:${row.task}` ? "Testing…" : "Test"}
                </button>
              </div>
            ))}
          </div>
        ) : null}

        {section === "restart" ? (
          <form className="ax-card" onSubmit={submitRestart}>
            <SectionLabel>服务重启</SectionLabel>
            <p style={{ color: "var(--ax-text-2)", fontSize: 12 }}>需要二次密码。服务器未设置 SYSTEM_RESTART_ENABLED=1 时只做 dry-run，不会执行 systemctl。</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 10 }}>
              {["public", "admin", "worker", "scheduler"].map((roleName) => (
                <label key={roleName} className="ax-btn" style={{ justifyContent: "flex-start" }}>
                  <input
                    type="checkbox"
                    checked={Boolean(restartRoles[roleName])}
                    onChange={(event) => setRestartRoles((prev) => ({ ...prev, [roleName]: event.target.checked }))}
                  />
                  {roleName}
                </label>
              ))}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8 }}>
              <input className="input" name="confirm_password" type="password" placeholder="admin password" required />
              <button type="submit" className="ax-btn" disabled={busy === "restart"}>{busy === "restart" ? "Restarting…" : "Restart selected"}</button>
            </div>
          </form>
        ) : null}

        {section === "members" ? (
          <div style={{ display: "grid", gap: 12 }}>
            <div className="ax-card">
              <SectionLabel>邀请成员</SectionLabel>
              <form onSubmit={submitInvite} style={{ display: "grid", gridTemplateColumns: "1.3fr 0.8fr auto", gap: 8 }}>
                <input className="input" name="email" type="email" placeholder="name@viltrox.com" required />
                <select className="input" value={role} onChange={(event) => setRole(event.target.value)}>
                  <option value="readonly">readonly</option>
                  <option value="admin">admin</option>
                </select>
                <button type="submit" className="ax-btn" disabled={busy === "invite"}><Icons.mail /> Invite</button>
              </form>
            </div>

            <div className="ax-card">
              <SectionLabel>权限矩阵</SectionLabel>
              <PermissionMatrix permissions={permissions} onChange={setPermissions} />
              <button type="button" className="ax-btn ax-btn--sm" style={{ marginTop: 10 }} onClick={saveFirstStaffPermissions}>
                保存到第一位成员
              </button>
            </div>

            <div className="ax-card" style={{ overflowX: "auto" }}>
              <DataTable
                columns={staffColumns}
                rows={system?.staffMembers || []}
                rowKey={(row, index) => String(row.id || index)}
                showCheckbox={false}
                emptyLabel="暂无 staff 成员"
              />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="ax-kpi">
      <div className="ax-kpi__label">{label}</div>
      <div className="ax-kpi__value">{value}</div>
    </div>
  );
}

function UsageList({
  title,
  rows,
  primaryKey,
}: {
  title: string;
  rows: Array<Record<string, unknown>>;
  primaryKey: string;
}) {
  return (
    <div>
      <SectionLabel>{title}</SectionLabel>
      <div style={{ display: "grid", gap: 6 }}>
        {rows.length ? rows.slice(0, 8).map((row, index) => (
          <div key={`${title}:${index}`} style={{ display: "grid", gridTemplateColumns: "1fr 90px 90px", gap: 8, fontSize: 12 }}>
            <span>{str(row[primaryKey], "unknown")}</span>
            <span>{num(row.calls)}</span>
            <span>{usd(row.cost_usd)}</span>
          </div>
        )) : (
          <div style={{ color: "var(--ax-text-2)", fontSize: 12 }}>暂无用量记录</div>
        )}
      </div>
    </div>
  );
}

function PermissionMatrix({
  permissions,
  onChange,
}: {
  permissions: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const keys = [...TAB_KEYS, ...SYSTEM_KEYS];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
      {keys.map((key) => (
        <label key={key} style={{ display: "grid", gridTemplateColumns: "1fr 110px", gap: 8, alignItems: "center", fontSize: 11 }}>
          <span>{key}</span>
          <select
            className="input"
            value={permissions[key] || "none"}
            onChange={(event) => onChange({ ...permissions, [key]: event.target.value })}
          >
            <option value="none">none</option>
            <option value="read">read</option>
            <option value="write">write</option>
          </select>
        </label>
      ))}
    </div>
  );
}

export default SystemTab;
