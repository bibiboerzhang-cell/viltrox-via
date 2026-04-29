import { useEffect, useMemo, useState } from "react";

import type { AuthUser } from "../../../lib/api";
import {
  fetchInsightsChannels,
  fetchInsightsCohorts,
  fetchInsightsCommerce,
  fetchInsightsContent,
  fetchInsightsHealth,
  fetchInsightsOverview,
  fetchInsightsStaffKpi,
  fetchInsightsUsers,
} from "../../../services/admin.service";
import { DataTable, ErrorCard, LoadingCard, PageHeader, SectionLabel, type DataColumn } from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

type Panel = "overview" | "users" | "content" | "commerce" | "channels" | "cohorts" | "health" | "staff_kpi";
type Row = Record<string, unknown>;

const PANELS: Array<{ key: Panel; label: string }> = [
  { key: "overview", label: "总览" },
  { key: "users", label: "用户" },
  { key: "content", label: "内容" },
  { key: "commerce", label: "商业" },
  { key: "channels", label: "渠道" },
  { key: "cohorts", label: "同期群" },
  { key: "health", label: "健康度" },
  { key: "staff_kpi", label: "员工 KPI" },
];

function text(value: unknown, fallback = "—") {
  const next = String(value ?? "").trim();
  return next || fallback;
}

function number(value: unknown, digits = 0) {
  const parsed = Number(value || 0);
  if (!Number.isFinite(parsed)) return digits ? "0.00" : "0";
  return parsed.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function usd(value: unknown) {
  return `$${number(value, 2)}`;
}

function rows(value: unknown): Row[] {
  return Array.isArray(value) ? (value as Row[]) : [];
}

export function InsightsTab({ token }: Props) {
  const [panel, setPanel] = useState<Panel>("overview");
  const [windowKey, setWindowKey] = useState("30d");
  const [data, setData] = useState<Row | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const fetchers: Record<Panel, () => Promise<Row>> = {
        overview: () => fetchInsightsOverview(token, windowKey),
        users: () => fetchInsightsUsers(token, windowKey),
        content: () => fetchInsightsContent(token, windowKey),
        commerce: () => fetchInsightsCommerce(token, windowKey),
        channels: () => fetchInsightsChannels(token, windowKey),
        cohorts: () => fetchInsightsCohorts(token, 12),
        health: () => fetchInsightsHealth(token),
        staff_kpi: () => fetchInsightsStaffKpi(token, windowKey),
      };
      setData(await fetchers[panel]());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panel, windowKey, token]);

  return (
    <div>
      <PageHeader
        title="Insights"
        subtitle="用户 · 内容 · 商业 · 渠道 · 同期群 · 健康度 · 员工 KPI"
        actions={
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {["7d", "30d", "90d", "365d", "all"].map((item) => (
              <button
                key={item}
                type="button"
                className={`ax-btn ax-btn--sm${windowKey === item ? " is-active" : ""}`}
                onClick={() => setWindowKey(item)}
              >
                {item === "all" ? "全部" : item}
              </button>
            ))}
          </div>
        }
      />
      <div style={{ padding: 16, display: "grid", gap: 12 }}>
        <div className="ax-card" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {PANELS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`ax-btn ax-btn--sm${panel === item.key ? " is-active" : ""}`}
              onClick={() => setPanel(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        {loading ? <LoadingCard label="Loading Insights…" /> : null}
        {error ? <ErrorCard label="Insights 加载失败" detail={error} onRetry={load} /> : null}
        {!loading && !error && data ? <PanelContent panel={panel} data={data} /> : null}
      </div>
    </div>
  );
}

function PanelContent({ panel, data }: { panel: Panel; data: Row }) {
  if (panel === "overview") return <OverviewPanel data={data} />;
  if (panel === "users") return <UsersPanel data={data} />;
  if (panel === "content") return <ContentPanel data={data} />;
  if (panel === "commerce") return <CommercePanel data={data} />;
  if (panel === "channels") return <ChannelsPanel data={data} />;
  if (panel === "cohorts") return <CohortsPanel data={data} />;
  if (panel === "staff_kpi") return <StaffKpiPanel data={data} />;
  return <HealthPanel data={data} />;
}

function OverviewPanel({ data }: { data: Row }) {
  const kpi = (data.kpi || {}) as Row;
  const funnel = (data.funnel || {}) as Row;
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <KpiBand
        items={[
          ["累计用户", number(kpi.total_users), `窗口 +${number(kpi.users_window)}`],
          ["累计投稿", number(kpi.total_submissions), `窗口 +${number(kpi.submissions_window)}`],
          ["归因 GMV", usd(kpi.total_gmv_usd), `窗口 ${usd(kpi.gmv_window_usd)}`],
          ["单用户 GMV", usd(kpi.avg_gmv_per_user_usd), "LTV 估算"],
        ]}
      />
      <div className="ax-card">
        <SectionLabel>转化漏斗</SectionLabel>
        <Funnel
          items={[
            ["注册", Number(funnel.stage_register || 0)],
            ["投稿", Number(funnel.stage_submit || 0)],
            ["通过", Number(funnel.stage_approve || 0)],
            ["购买", Number(funnel.stage_buy || 0)],
          ]}
        />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        <MiniSeries title="用户增长" rows={rows(data.daily_users)} valueKey="n" />
        <MiniSeries title="投稿趋势" rows={rows(data.daily_submissions)} valueKey="n" />
        <MiniSeries title="GMV 趋势" rows={rows(data.daily_gmv)} valueKey="n" cents />
      </div>
    </div>
  );
}

function UsersPanel({ data }: { data: Row }) {
  const activity = (data.activity || {}) as Row;
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <KpiBand
        items={[
          ["DAU", number(activity.dau), "今日活跃"],
          ["WAU", number(activity.wau), "7 日活跃"],
          ["MAU", number(activity.mau), "30 日活跃"],
          ["流失率", `${number(data.churn_rate, 1)}%`, "30 日未活跃"],
        ]}
      />
      <SimpleTable title="来源分布" rows={rows(data.source_distribution)} columns={[
        { key: "source", label: "来源", render: (r) => text(r.source) },
        { key: "n", label: "用户", render: (r) => number(r.n) },
      ]} />
    </div>
  );
}

function ContentPanel({ data }: { data: Row }) {
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <SimpleTable title="评分分布" rows={rows(data.score_distribution)} columns={[
        { key: "bucket", label: "分数段", render: (r) => text(r.bucket) },
        { key: "n", label: "内容数", render: (r) => number(r.n) },
      ]} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <SimpleTable title="拒绝/异常原因" rows={rows(data.rejection_reasons)} columns={[
          { key: "reason", label: "原因", render: (r) => text(r.reason) },
          { key: "n", label: "次数", render: (r) => number(r.n) },
        ]} />
        <SimpleTable title="热门产品" rows={rows(data.top_products)} columns={[
          { key: "sku", label: "产品", render: (r) => text(r.sku) },
          { key: "n", label: "投稿", render: (r) => number(r.n) },
        ]} />
      </div>
    </div>
  );
}

function CommercePanel({ data }: { data: Row }) {
  const funnel = (data.funnel || {}) as Row;
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <KpiBand
        items={[
          ["GMV", usd(data.gmv_usd), `${number(data.order_count)} orders`],
          ["AOV", usd(data.aov_usd), "平均订单"],
          ["佣金", usd(data.commission_usd), "已估算"],
          ["整体转化", `${number(funnel.rate_overall, 1)}%`, "注册到购买"],
        ]}
      />
      <SimpleTable title="Top 创作者贡献" rows={rows(data.top_creators)} columns={[
        { key: "creator_code", label: "VID", render: (r) => <span className="ax-mono">{text(r.creator_code)}</span> },
        { key: "email", label: "邮箱", render: (r) => text(r.email) },
        { key: "order_count", label: "订单", render: (r) => number(r.order_count) },
        { key: "gmv_cents", label: "GMV", render: (r) => usd(Number(r.gmv_cents || 0) / 100) },
      ]} />
    </div>
  );
}

function ChannelsPanel({ data }: { data: Row }) {
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <SimpleTable title="UTM 来源" rows={rows(data.utm_sources)} columns={[
        { key: "source", label: "来源", render: (r) => text(r.source) },
        { key: "order_count", label: "订单", render: (r) => number(r.order_count) },
        { key: "gmv_cents", label: "GMV", render: (r) => usd(Number(r.gmv_cents || 0) / 100) },
      ]} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <SimpleTable title="活动归因 Top 10" rows={rows(data.activity_attribution)} columns={[
          { key: "title", label: "活动", render: (r) => text(r.title || r.activity_id) },
          { key: "user_count", label: "用户", render: (r) => number(r.user_count) },
          { key: "gmv_cents", label: "GMV", render: (r) => usd(Number(r.gmv_cents || 0) / 100) },
        ]} />
        <SimpleTable title="KOL 归因 Top 10" rows={rows(data.kol_attribution)} columns={[
          { key: "channel_name", label: "红人", render: (r) => text(r.channel_name) },
          { key: "platform", label: "平台", render: (r) => text(r.platform) },
          { key: "gmv_cents", label: "GMV", render: (r) => usd(Number(r.gmv_cents || 0) / 100) },
        ]} />
      </div>
    </div>
  );
}

function CohortsPanel({ data }: { data: Row }) {
  return <SimpleTable title="注册 cohort" rows={rows(data.cohorts)} columns={[
    { key: "d", label: "日期", render: (r) => text(r.d) },
    { key: "users", label: "用户", render: (r) => number(r.users) },
  ]} />;
}

function HealthPanel({ data }: { data: Row }) {
  const growth = (data.growth || {}) as Row;
  const ops = (data.ops || {}) as Row;
  return <KpiBand items={[
    ["7 日新增", number(growth.weekly_new_users), `${number(growth.weekly_growth_pct, 1)}%`],
    ["AI 错误率", `${number(ops.ai_error_rate_7d, 2)}%`, "7 日"],
  ]} />;
}

function StaffKpiPanel({ data }: { data: Row }) {
  return <SimpleTable title="员工 KPI" rows={rows(data.staff_kpi)} columns={[
    { key: "staff_name", label: "员工", render: (r) => text(r.staff_name) },
    { key: "action_count", label: "操作数", render: (r) => number(r.action_count) },
    { key: "redemption_actions", label: "兑换动作", render: (r) => number(r.redemption_actions) },
    { key: "kol_campaign_count", label: "KOL 活动", render: (r) => number(r.kol_campaign_count) },
    { key: "kol_revenue_usd", label: "KOL 收入", render: (r) => usd(r.kol_revenue_usd) },
    { key: "kol_roi", label: "ROI", render: (r) => `${number(Number(r.kol_roi || 0) * 100, 1)}%` },
  ]} />;
}

function KpiBand({ items }: { items: Array<[string, string, string]> }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${Math.min(items.length, 4)}, minmax(0, 1fr))`, gap: 12 }}>
      {items.map(([label, value, hint]) => (
        <div key={label} className="ax-kpi">
          <div className="ax-kpi__label">{label}</div>
          <div className="ax-kpi__value">{value}</div>
          <div style={{ color: "var(--ax-text-2)", fontSize: 11 }}>{hint}</div>
        </div>
      ))}
    </div>
  );
}

function SimpleTable({ title, rows: tableRows, columns }: { title: string; rows: Row[]; columns: DataColumn<Row>[] }) {
  const safeRows = useMemo(() => tableRows.slice(0, 50), [tableRows]);
  return (
    <div className="ax-card" style={{ overflowX: "auto" }}>
      <SectionLabel>{title}</SectionLabel>
      <DataTable columns={columns} rows={safeRows} rowKey={(row, index) => String(row.id || row.key || index)} showCheckbox={false} emptyLabel="暂无数据" />
    </div>
  );
}

function MiniSeries({ title, rows: seriesRows, valueKey, cents = false }: { title: string; rows: Row[]; valueKey: string; cents?: boolean }) {
  const last = seriesRows.slice(-14);
  const max = Math.max(...last.map((r) => Number(r[valueKey] || 0)), 1);
  return (
    <div className="ax-card">
      <SectionLabel>{title}</SectionLabel>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 120 }}>
        {last.length ? last.map((row, index) => {
          const value = Number(row[valueKey] || 0);
          return (
            <div
              key={`${title}:${index}`}
              title={`${text(row.d)} · ${cents ? usd(value / 100) : number(value)}`}
              style={{
                flex: 1,
                height: `${Math.max(3, (value / max) * 100)}%`,
                background: "var(--ax-accent)",
                borderRadius: 3,
                opacity: 0.72,
              }}
            />
          );
        }) : <div style={{ color: "var(--ax-text-2)", fontSize: 12 }}>暂无趋势数据</div>}
      </div>
    </div>
  );
}

function Funnel({ items }: { items: Array<[string, number]> }) {
  const max = Math.max(items[0]?.[1] || 0, 1);
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {items.map(([label, value]) => (
        <div key={label} style={{ display: "grid", gridTemplateColumns: "90px 1fr 80px", gap: 10, alignItems: "center", fontSize: 12 }}>
          <strong>{label}</strong>
          <div style={{ height: 24, background: "rgba(255,255,255,0.06)", borderRadius: 4, overflow: "hidden" }}>
            <div style={{ width: `${(value / max) * 100}%`, height: "100%", background: "var(--ax-accent)" }} />
          </div>
          <span className="ax-mono" style={{ textAlign: "right" }}>{number(value)}</span>
        </div>
      ))}
    </div>
  );
}

export default InsightsTab;
