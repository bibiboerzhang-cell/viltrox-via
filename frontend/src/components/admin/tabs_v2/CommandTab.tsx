/**
 * Command tab v2 — command center
 *
 * Aggregates commerce + brand + market signals into one operational dashboard.
 * Uses multiple snapshots in parallel.
 *
 * Sections:
 *   - Commerce (orders, webhook events)
 *   - Brand (matrix scanning 18+ Viltrox accounts)
 *   - Market (heatmap across categories)
 */
import { useEffect, useMemo, useState } from "react";

import {
  fetchAdminBrandSnapshot,
  fetchAdminCommerceSnapshot,
  fetchAdminMarketSnapshot,
  attributeAdminOrder,
  fetchAdminOrderDetail,
  fetchAdminSystemSnapshot,
  fetchTrustDistribution,
  fetchTrustUserDetail,
  flagAdminOrder,
  resolvePayoutDispute,
  runPayoutCycleAction,
  runPayoutAction,
  runTrustUserAction,
  type AdminBrandSnapshot,
  type AdminCommerceSnapshot,
  type AdminMarketSnapshot,
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
  SegButton,
  SectionLabel,
  StatusPill,
  type DataColumn,
} from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

type Section = "commerce" | "brand" | "market" | "trust";

function num(value: unknown, digits = 0) {
  const parsed = Number(value || 0);
  if (!Number.isFinite(parsed)) return digits ? "0.00" : "0";
  return parsed.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function CommandTab({ token }: Props) {
  const [commerce, setCommerce] = useState<AdminCommerceSnapshot | null>(null);
  const [brand, setBrand] = useState<AdminBrandSnapshot | null>(null);
  const [market, setMarket] = useState<AdminMarketSnapshot | null>(null);
  const [system, setSystem] = useState<AdminSystemSnapshot | null>(null);
  const [trustDistribution, setTrustDistribution] = useState<Record<string, unknown> | null>(null);
  const [trustDetail, setTrustDetail] = useState<Record<string, unknown> | null>(null);
  const [commerceDetail, setCommerceDetail] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [section, setSection] = useState<Section>("commerce");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    Promise.allSettled([
      fetchAdminCommerceSnapshot(token),
      fetchAdminBrandSnapshot(token),
      fetchAdminMarketSnapshot(token),
      fetchAdminSystemSnapshot(token),
      fetchTrustDistribution(token),
    ]).then((results) => {
      if (!alive) return;
      if (results[0].status === "fulfilled") setCommerce(results[0].value);
      if (results[1].status === "fulfilled") setBrand(results[1].value);
      if (results[2].status === "fulfilled") setMarket(results[2].value);
      if (results[3].status === "fulfilled") setSystem(results[3].value);
      if (results[4].status === "fulfilled") setTrustDistribution(results[4].value);
      const allFailed = results.every((r) => r.status === "rejected");
      if (allFailed) {
        setError("所有 Command 快照拉取失败");
      }
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [token, tick]);

  const refresh = () => setTick((n) => n + 1);

  const kpis = useMemo(() => {
    const orders = commerce?.ordersSummary as Record<string, unknown> | undefined;
    const attribution = commerce?.attributionOverview as Record<string, unknown> | undefined;
    return [
      { label: "Orders 30d", value: Number(orders?.total || 0) },
      { label: "Revenue 30d", value: `$${Number(orders?.revenue || 0).toLocaleString()}` },
      { label: "Brand accounts", value: (brand?.matrix || []).length },
      { label: "Trust watch", value: (system?.trustUsers || []).length },
    ];
  }, [commerce, brand, system]);

  const orderCols: DataColumn<Record<string, unknown>>[] = [
    {
      key: "order",
      label: "Order",
      width: "1.5fr",
      render: (r) => (
        <div>
          <div className="ax-mono" style={{ fontSize: 10 }}>
            #{String(r.order_number || r.id || "—")}
          </div>
          <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            {String(r.customer_email || "—")}
          </div>
        </div>
      ),
    },
    {
      key: "amount",
      label: "Amount",
      width: "100px",
      accent: true,
      render: (r) => (
        <span className="ax-num" style={{ fontWeight: 600 }}>
          ${Number(r.total || r.amount || 0).toLocaleString()}
        </span>
      ),
    },
    {
      key: "creator",
      label: "Creator",
      width: "100px",
      render: (r) => (
        <span className="ax-mono" style={{ fontSize: 10 }}>
          {String(r.creator_code || "—")}
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
          s === "paid" || s === "fulfilled" ? "pass" : s === "cancelled" ? "block" : "review";
        return <StatusPill tone={tone as never}>{String(r.status || "pending")}</StatusPill>;
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
    {
      key: "actions",
      label: "Actions",
      width: "260px",
      render: (r) => {
        const orderId = Number(r.id || r.order_id || 0);
        return (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <button type="button" className="ax-btn ax-btn--sm" onClick={() => showOrderDetail(orderId)} disabled={!orderId}>Detail</button>
            <button type="button" className="ax-btn ax-btn--sm" onClick={() => attributeOrder(orderId)} disabled={!orderId}>Attribute</button>
            <button type="button" className="ax-btn ax-btn--sm" onClick={() => flagOrder(orderId)} disabled={!orderId}>Flag</button>
          </div>
        );
      },
    },
  ];

  const showOrderDetail = async (orderId: number) => {
    if (!orderId) return;
    setError(null);
    try {
      setCommerceDetail(await fetchAdminOrderDetail(token, orderId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const attributeOrder = async (orderId: number) => {
    if (!orderId) return;
    const creatorHandle = window.prompt("绑定 creator handle / VID", "");
    if (!creatorHandle) return;
    const reason = window.prompt("归因原因", "manual admin attribution") || "manual admin attribution";
    setError(null);
    try {
      await attributeAdminOrder(token, orderId, { creator_handle: creatorHandle, reason });
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const flagOrder = async (orderId: number) => {
    if (!orderId) return;
    const reason = window.prompt("异常类型: fraud / bot / duplicate", "fraud");
    if (!["fraud", "bot", "duplicate"].includes(String(reason))) return;
    setError(null);
    try {
      await flagAdminOrder(token, orderId, reason as "fraud" | "bot" | "duplicate");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const payoutCycleAction = async (cycleId: unknown, action: "approve-all" | "process") => {
    const id = String(cycleId || "");
    if (!id) return;
    if (!window.confirm(`${action} payout cycle ${id}?`)) return;
    setError(null);
    try {
      await runPayoutCycleAction(token, id, action);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const payoutAction = async (payoutId: unknown, action: "approve" | "hold" | "release" | "adjust") => {
    const id = Number(payoutId || 0);
    if (!id) return;
    const payload: Record<string, unknown> = {};
    if (action === "hold") {
      const reason = window.prompt("Hold reason", "");
      if (!reason) return;
      payload.reason = reason;
    }
    if (action === "adjust") {
      const amount = Number(window.prompt("New amount USD", "0") || 0);
      const reason = window.prompt("Adjustment reason", "");
      if (!reason || !Number.isFinite(amount)) return;
      payload.new_amount_cents = Math.round(amount * 100);
      payload.reason = reason;
    }
    setError(null);
    try {
      await runPayoutAction(token, id, action, payload);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const resolveDispute = async (disputeId: unknown) => {
    const id = Number(disputeId || 0);
    if (!id) return;
    const resolution = window.prompt("Resolution: uphold / overturn", "uphold");
    if (!["uphold", "overturn"].includes(String(resolution))) return;
    const note = window.prompt("Resolution note", "") || "";
    setError(null);
    try {
      await resolvePayoutDispute(token, id, { resolution: resolution as "uphold" | "overturn", note });
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const brandCols: DataColumn<Record<string, unknown>>[] = [
    {
      key: "account",
      label: "Account",
      width: "1.5fr",
      render: (r) => (
        <div>
          <div style={{ color: "var(--ax-text-5)" }}>
            @{String(r.handle || r.account || "").replace(/^@/, "")}
          </div>
          <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            {String(r.region || r.market || "—")}
          </div>
        </div>
      ),
    },
    {
      key: "platform",
      label: "Platform",
      width: "90px",
      render: (r) => (
        <span style={{ color: "var(--ax-text-3)" }}>{String(r.platform || "—")}</span>
      ),
    },
    {
      key: "followers",
      label: "Followers",
      width: "100px",
      render: (r) => (
        <span className="ax-num" style={{ fontWeight: 600 }}>
          {Number(r.followers || 0).toLocaleString()}
        </span>
      ),
    },
    {
      key: "posts30d",
      label: "Posts 30d",
      width: "90px",
      render: (r) => <span className="ax-num">{Number(r.posts_30d || 0)}</span>,
    },
    {
      key: "health",
      label: "Health",
      width: "80px",
      render: (r) => {
        const h = String(r.health || r.status || "ok").toLowerCase();
        const tone = h === "ok" || h === "healthy" ? "pass" : h === "stale" ? "idle" : "block";
        return <StatusPill tone={tone as never}>{h.toUpperCase()}</StatusPill>;
      },
    },
  ];

  const marketCols: DataColumn<Record<string, unknown>>[] = [
    {
      key: "signal",
      label: "Signal",
      width: "2.5fr",
      render: (r) => (
        <div>
          <div style={{ color: "var(--ax-text-5)" }}>
            {String(r.title || r.label || "—")}
          </div>
          <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            {String(r.source || r.category || "")}
          </div>
        </div>
      ),
    },
    {
      key: "severity",
      label: "Severity",
      width: "100px",
      render: (r) => {
        const s = String(r.severity || r.tone || "info").toLowerCase();
        const tone = s === "critical" || s === "high" ? "block" : s === "warning" ? "queue" : "review";
        return <StatusPill tone={tone as never}>{s.toUpperCase()}</StatusPill>;
      },
    },
    {
      key: "at",
      label: "时间",
      width: "120px",
      render: (r) => (
        <span style={{ color: "var(--ax-text-2)", fontSize: 10 }}>
          {r.created_at ? new Date(String(r.created_at)).toLocaleDateString() : "—"}
        </span>
      ),
    },
  ];

  const trustCols: DataColumn<Record<string, unknown>>[] = [
    {
      key: "user",
      label: "User",
      width: "1.3fr",
      render: (r) => (
        <div>
          <strong>{String(r.email || r.handle || r.username || `#${r.user_id || r.id || "—"}`)}</strong>
          <div className="ax-mono" style={{ fontSize: 10, color: "var(--ax-text-1)" }}>user {String(r.user_id || r.id || "—")}</div>
        </div>
      ),
    },
    { key: "score", label: "Score", width: "80px", render: (r) => <span className="ax-num">{Number(r.trust_score ?? r.score ?? 0)}</span> },
    { key: "violations", label: "Signals", width: "80px", render: (r) => <span className="ax-num">{Number(r.violations ?? r.event_count ?? 0)}</span> },
    {
      key: "status",
      label: "Status",
      width: "110px",
      render: (r) => {
        const s = String(r.status || r.trust_status || "watching").toLowerCase();
        const tone = s === "blocked" ? "block" : s === "flagged" ? "flag" : s === "trusted" ? "pass" : "review";
        return <StatusPill tone={tone as never}>{s}</StatusPill>;
      },
    },
    {
      key: "actions",
      label: "Actions",
      width: "250px",
      render: (r) => {
        const userId = Number(r.user_id || r.id || 0);
        return (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <button type="button" className="ax-btn ax-btn--sm" onClick={() => showTrustDetail(userId)} disabled={!userId}>Detail</button>
            <button type="button" className="ax-btn ax-btn--sm" onClick={() => moderateTrustUser(userId, "flag")} disabled={!userId}>Flag</button>
            <button type="button" className="ax-btn ax-btn--sm" onClick={() => moderateTrustUser(userId, "block")} disabled={!userId}>Block</button>
            <button type="button" className="ax-btn ax-btn--sm" onClick={() => moderateTrustUser(userId, "clear-flag")} disabled={!userId}>Clear</button>
          </div>
        );
      },
    },
  ];

  const showTrustDetail = async (userId: number) => {
    if (!userId) return;
    setError(null);
    try {
      setTrustDetail(await fetchTrustUserDetail(token, userId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const moderateTrustUser = async (userId: number, action: "block" | "flag" | "clear-flag") => {
    if (!userId) return;
    const reason = action === "clear-flag" ? "" : window.prompt("Trust 操作原因", "admin trust review");
    if (action !== "clear-flag" && !reason) return;
    setError(null);
    try {
      await runTrustUserAction(token, userId, action, reason ? { reason } : {});
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const sections: Array<{ key: Section; label: string }> = [
    { key: "commerce", label: `Commerce (${(commerce?.orders || []).length})` },
    { key: "brand", label: `Brand Matrix (${(brand?.matrix || []).length})` },
    { key: "market", label: `Market (${(market?.observations || []).length})` },
    { key: "trust", label: `Trust (${(system?.trustUsers || []).length})` },
  ];

  return (
    <div>
      <PageHeader
        title="Command Center"
        subtitle="Commerce · Brand matrix · Market signals — Viltrox 全景"
        actions={
          <button type="button" className="ax-btn" onClick={refresh} disabled={loading}>
            <Icons.trending /> {loading ? "刷新中…" : "刷新"}
          </button>
        }
      />

      {error ? (
        <div style={{ padding: 16 }}>
          <ErrorCard detail={error} onRetry={refresh} />
        </div>
      ) : null}

      <div style={{ padding: 16 }}>
        {loading && !commerce && !brand && !market && !system ? (
          <LoadingCard label="并行加载 Commerce + Brand + Market + Trust…" />
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
              {section === "commerce" ? (
                (commerce?.orders || []).length === 0 ? (
                  <EmptyCard label="暂无订单" hint="等待 Shopify webhook 注入订单数据" />
                ) : (
                  <>
                    <DataTable
                      columns={orderCols}
                      rows={commerce?.orders as Record<string, unknown>[]}
                      rowKey={(r) => String(r.id || r.order_number)}
                      showCheckbox={false}
                    />
                    {commerceDetail ? (
                      <div className="ax-card" style={{ margin: 12, background: "rgba(255,255,255,0.03)" }}>
                        <SectionLabel>Order Detail</SectionLabel>
                        <pre style={{ whiteSpace: "pre-wrap", fontSize: 11, maxHeight: 280, overflow: "auto" }}>{JSON.stringify(commerceDetail, null, 2)}</pre>
                      </div>
                    ) : null}
                    <CommerceActionPanels
                      cycles={commerce?.payoutCycles || []}
                      payouts={Array.isArray(commerce?.payoutCurrentCycle?.payouts) ? commerce?.payoutCurrentCycle?.payouts as Record<string, unknown>[] : []}
                      disputes={commerce?.payoutDisputes || []}
                      onCycleAction={payoutCycleAction}
                      onPayoutAction={payoutAction}
                      onResolveDispute={resolveDispute}
                    />
                  </>
                )
              ) : section === "brand" ? (
                (brand?.matrix || []).length === 0 ? (
                  <EmptyCard label="Brand matrix 为空" hint="DeepSight 尚未扫描" />
                ) : (
                  <DataTable
                    columns={brandCols}
                    rows={brand?.matrix as Record<string, unknown>[]}
                    rowKey={(r, i) => `${r.platform}:${r.handle}:${i}`}
                    showCheckbox={false}
                  />
                )
              ) : section === "market" ? ((market?.observations || []).length === 0 ? (
                <EmptyCard label="暂无市场信号" />
              ) : (
                <DataTable
                  columns={marketCols}
                  rows={market?.observations as Record<string, unknown>[]}
                  rowKey={(r, i) => String(r.id || i)}
                  showCheckbox={false}
                />
              )) : (system?.trustUsers || []).length === 0 ? (
                <EmptyCard label="暂无 Trust 用户数据" hint={`Distribution: ${JSON.stringify(trustDistribution || {})}`} />
              ) : (
                <>
                  <DataTable
                    columns={trustCols}
                    rows={system?.trustUsers as Record<string, unknown>[]}
                    rowKey={(r, i) => String(r.user_id || r.id || i)}
                    showCheckbox={false}
                  />
                  {trustDetail ? (
                    <div className="ax-card" style={{ margin: 12, background: "rgba(255,255,255,0.03)" }}>
                      <SectionLabel>Trust Detail</SectionLabel>
                      <pre style={{ whiteSpace: "pre-wrap", fontSize: 11, maxHeight: 320, overflow: "auto" }}>{JSON.stringify(trustDetail, null, 2)}</pre>
                    </div>
                  ) : null}
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function CommerceActionPanels({
  cycles,
  payouts,
  disputes,
  onCycleAction,
  onPayoutAction,
  onResolveDispute,
}: {
  cycles: Record<string, unknown>[];
  payouts: Record<string, unknown>[];
  disputes: Record<string, unknown>[];
  onCycleAction: (cycleId: unknown, action: "approve-all" | "process") => void;
  onPayoutAction: (payoutId: unknown, action: "approve" | "hold" | "release" | "adjust") => void;
  onResolveDispute: (disputeId: unknown) => void;
}) {
  return (
    <div style={{ margin: 12, display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
      <div className="ax-card" style={{ background: "rgba(255,255,255,0.03)" }}>
        <SectionLabel>Payout Cycles</SectionLabel>
        {cycles.length ? cycles.slice(0, 5).map((cycle) => (
          <div key={String(cycle.id || cycle.cycle_id)} style={{ display: "grid", gap: 6, padding: "8px 0", borderBottom: "1px solid var(--ax-border-1)" }}>
            <strong>{String(cycle.id || cycle.cycle_id || "—")}</strong>
            <span style={{ fontSize: 12, color: "var(--ax-text-2)" }}>{String(cycle.status || "pending")} · {String(cycle.payout_count || cycle.count || 0)} payouts</span>
            <div style={{ display: "flex", gap: 6 }}>
              <button type="button" className="ax-btn ax-btn--sm" onClick={() => onCycleAction(cycle.id || cycle.cycle_id, "approve-all")}>Approve all</button>
              <button type="button" className="ax-btn ax-btn--sm" onClick={() => onCycleAction(cycle.id || cycle.cycle_id, "process")}>Process</button>
            </div>
          </div>
        )) : <div style={{ color: "var(--ax-text-2)", fontSize: 12 }}>No cycles</div>}
      </div>
      <div className="ax-card" style={{ background: "rgba(255,255,255,0.03)" }}>
        <SectionLabel>Payout State</SectionLabel>
        {payouts.length ? payouts.slice(0, 5).map((payout) => (
          <div key={String(payout.id)} style={{ display: "grid", gap: 6, padding: "8px 0", borderBottom: "1px solid var(--ax-border-1)" }}>
            <strong>#{String(payout.id)} · ${num(Number(payout.amount_cents || payout.amount || 0) / 100, 2)}</strong>
            <span style={{ fontSize: 12, color: "var(--ax-text-2)" }}>{String(payout.status || "pending")}</span>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button type="button" className="ax-btn ax-btn--sm" onClick={() => onPayoutAction(payout.id, "approve")}>Approve</button>
              <button type="button" className="ax-btn ax-btn--sm" onClick={() => onPayoutAction(payout.id, "hold")}>Hold</button>
              <button type="button" className="ax-btn ax-btn--sm" onClick={() => onPayoutAction(payout.id, "release")}>Release</button>
              <button type="button" className="ax-btn ax-btn--sm" onClick={() => onPayoutAction(payout.id, "adjust")}>Adjust</button>
            </div>
          </div>
        )) : <div style={{ color: "var(--ax-text-2)", fontSize: 12 }}>Open a payout cycle to see payouts</div>}
      </div>
      <div className="ax-card" style={{ background: "rgba(255,255,255,0.03)" }}>
        <SectionLabel>Disputes</SectionLabel>
        {disputes.length ? disputes.slice(0, 5).map((dispute) => (
          <div key={String(dispute.id)} style={{ display: "grid", gap: 6, padding: "8px 0", borderBottom: "1px solid var(--ax-border-1)" }}>
            <strong>#{String(dispute.id)} · {String(dispute.status || "open")}</strong>
            <span style={{ fontSize: 12, color: "var(--ax-text-2)" }}>{String(dispute.reason || dispute.note || "—")}</span>
            <button type="button" className="ax-btn ax-btn--sm" onClick={() => onResolveDispute(dispute.id)}>Resolve</button>
          </div>
        )) : <div style={{ color: "var(--ax-text-2)", fontSize: 12 }}>No open disputes</div>}
      </div>
    </div>
  );
}

export default CommandTab;
