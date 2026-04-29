import { useMemo, useState } from "react";

import {
  fetchAdminAnalyticsSnapshot,
  generateBrandInsights,
  generateMarketGaps,
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
  StatusPill,
  useAdminSnapshot,
  type DataColumn,
} from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

type Row = Record<string, unknown>;
type View = "overview" | "pipeline" | "creators" | "cohorts";

function rows(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item): item is Row => Boolean(item && typeof item === "object")) : [];
}

function num(value: unknown, digits = 0) {
  const parsed = Number(value || 0);
  if (!Number.isFinite(parsed)) return digits ? "0.0" : "0";
  return parsed.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function moneyCents(value: unknown) {
  return `$${num(Number(value || 0) / 100, 2)}`;
}

function trendTotal(series: Row[]) {
  return series.reduce((sum, item) => sum + Number(item.value || 0), 0);
}

function trendLast(series: Row[]) {
  return Number(series[series.length - 1]?.value || 0);
}

export function AnalyticsTab({ token }: Props) {
  const { data, loading, error, refresh } = useAdminSnapshot(token, fetchAdminAnalyticsSnapshot);
  const [view, setView] = useState<View>("overview");
  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState("");

  const trends = data?.trends || {};
  const trendRows = {
    submissions: rows(trends.submissions),
    gmv: rows(trends.gmv),
    score: rows(trends.score),
    active_creators: rows(trends.active_creators),
  };

  const kpis = useMemo(() => [
    { label: "30d submissions", value: num(trendTotal(trendRows.submissions)) },
    { label: "30d GMV", value: moneyCents(trendTotal(trendRows.gmv)) },
    { label: "Latest score", value: num(trendLast(trendRows.score), 1) },
    { label: "Active creators", value: num(trendLast(trendRows.active_creators)) },
  ], [trendRows.active_creators, trendRows.gmv, trendRows.score, trendRows.submissions]);

  const generate = async (kind: "market" | "brand") => {
    setBusy(kind);
    setToast("");
    try {
      if (kind === "market") {
        await generateMarketGaps(token);
        setToast("Market gaps 已重新生成");
      } else {
        await generateBrandInsights(token);
        setToast("Brand insights 已重新生成");
      }
      await refresh();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const correlationColumns: DataColumn<Row>[] = [
    { key: "metric_a", label: "Metric A", width: "1fr", render: (r) => String(r.metric_a || "—") },
    { key: "metric_b", label: "Metric B", width: "1fr", render: (r) => String(r.metric_b || "—") },
    { key: "r", label: "Pearson r", width: "100px", render: (r) => <StatusPill tone={Math.abs(Number(r.r || 0)) >= 0.5 ? "active" : "review"}>{num(r.r, 3)}</StatusPill> },
    { key: "sample", label: "Sample", width: "80px", render: (r) => num(r.sample_size) },
  ];

  const creatorColumns: DataColumn<Row>[] = [
    { key: "handle", label: "Creator", width: "1.2fr", render: (r) => <strong>{String(r.handle || "—")}</strong> },
    { key: "submissions", label: "Submissions", width: "100px", render: (r) => num(r.submissions) },
    { key: "views", label: "Views", width: "120px", render: (r) => num(r.views) },
    { key: "score", label: "Avg score", width: "100px", render: (r) => num(r.avg_score, 1) },
    { key: "gmv", label: "GMV", width: "110px", render: (r) => moneyCents(r.gmv_cents) },
  ];

  const seriesColumns: DataColumn<Row>[] = [
    { key: "series", label: "Series", width: "1fr", render: (r) => <strong>{String(r.series || "—")}</strong> },
    { key: "submissions", label: "Submissions", width: "100px", render: (r) => num(r.submissions) },
    { key: "score", label: "Avg score", width: "100px", render: (r) => num(r.avg_score, 1) },
    { key: "gmv", label: "GMV", width: "110px", render: (r) => moneyCents(r.gmv_cents) },
  ];

  const cohortColumns: DataColumn<Row>[] = [
    { key: "cohort", label: "Cohort", width: "1fr", render: (r) => String(r.cohort || "—") },
    { key: "size", label: "Size", width: "90px", render: (r) => num(r.size) },
    { key: "active", label: "30d active", width: "110px", render: (r) => `${num(r.active_30d_pct, 1)}%` },
  ];

  return (
    <div>
      <PageHeader
        title="Analytics"
        subtitle="Trends · correlations · funnel · cohorts · rankings"
        actions={
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" className="ax-btn" onClick={() => generate("market")} disabled={busy === "market"}>
              <Icons.trending /> {busy === "market" ? "Generating…" : "Generate gaps"}
            </button>
            <button type="button" className="ax-btn" onClick={() => generate("brand")} disabled={busy === "brand"}>
              <Icons.via /> {busy === "brand" ? "Generating…" : "Generate brand"}
            </button>
            <button type="button" className="ax-btn" onClick={refresh} disabled={loading}>
              <Icons.trending /> {loading ? "刷新中…" : "刷新"}
            </button>
          </div>
        }
      />

      {error ? (
        <div style={{ padding: 16 }}>
          <ErrorCard detail={error} onRetry={refresh} />
        </div>
      ) : null}

      <div style={{ padding: 16, display: "grid", gap: 12 }}>
        {toast ? <div className="ax-card" style={{ color: "var(--ax-text-5)" }}>{toast}</div> : null}
        {loading && !data ? <LoadingCard /> : null}
        {data ? (
          <>
            <KPIGrid items={kpis} />
            <div className="ax-card" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {(["overview", "pipeline", "creators", "cohorts"] as View[]).map((item) => (
                <button key={item} type="button" className={`ax-btn ax-btn--sm${view === item ? " is-active" : ""}`} onClick={() => setView(item)}>
                  {item}
                </button>
              ))}
            </div>

            {view === "overview" ? (
              <div style={{ display: "grid", gap: 12 }}>
                <div className="ax-card" style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
                  <TrendPanel title="Submissions" rows={trendRows.submissions} />
                  <TrendPanel title="GMV" rows={trendRows.gmv} valueFormat={moneyCents} />
                  <TrendPanel title="Score" rows={trendRows.score} digits={1} />
                  <TrendPanel title="Active creators" rows={trendRows.active_creators} />
                </div>
                <div className="ax-card">
                  <SectionLabel>Correlations</SectionLabel>
                  {data.correlations.length ? <DataTable columns={correlationColumns} rows={data.correlations} rowKey={(r, i) => `${r.metric_a}-${r.metric_b}-${i}`} showCheckbox={false} /> : <EmptyCard label="暂无相关性数据" />}
                </div>
              </div>
            ) : null}

            {view === "pipeline" ? (
              <div style={{ display: "grid", gap: 12 }}>
                <div className="ax-card">
                  <SectionLabel>Submission → Sales Funnel</SectionLabel>
                  <Funnel stages={data.pipeline} />
                  <div style={{ marginTop: 10, fontSize: 12, color: "var(--ax-text-2)" }}>Window: {String(data.pipelineSummary?.window || "30d")} · GMV {moneyCents(data.pipelineSummary?.gmv_cents)}</div>
                </div>
                <div className="ax-card">
                  <SectionLabel>Rejection Reasons</SectionLabel>
                  <ReasonList rows={data.rejectionReasons} />
                </div>
              </div>
            ) : null}

            {view === "creators" ? (
              <div style={{ display: "grid", gap: 12 }}>
                <div className="ax-card">
                  <SectionLabel>Creator Rankings</SectionLabel>
                  {data.creatorRankings.length ? <DataTable columns={creatorColumns} rows={data.creatorRankings} rowKey={(r, i) => `${r.handle}-${i}`} showCheckbox={false} /> : <EmptyCard label="暂无创作者排行数据" />}
                </div>
                <div className="ax-card">
                  <SectionLabel>Series Performance</SectionLabel>
                  {data.seriesPerformance.length ? <DataTable columns={seriesColumns} rows={data.seriesPerformance} rowKey={(r, i) => `${r.series}-${i}`} showCheckbox={false} /> : <EmptyCard label="暂无产品系列数据" />}
                </div>
              </div>
            ) : null}

            {view === "cohorts" ? (
              <div className="ax-card">
                <SectionLabel>Cohorts</SectionLabel>
                {data.cohorts.length ? <DataTable columns={cohortColumns} rows={data.cohorts} rowKey={(r, i) => `${r.cohort}-${i}`} showCheckbox={false} /> : <EmptyCard label="暂无 cohort 数据" />}
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}

function TrendPanel({
  title,
  rows,
  digits = 0,
  valueFormat,
}: {
  title: string;
  rows: Row[];
  digits?: number;
  valueFormat?: (value: unknown) => string;
}) {
  const maxValue = Math.max(1, ...rows.map((row) => Number(row.value || 0)));
  const last = rows[rows.length - 1]?.value || 0;
  return (
    <div>
      <SectionLabel>{title}</SectionLabel>
      <div className="ax-kpi" style={{ marginBottom: 8 }}>
        <div className="ax-kpi__value">{valueFormat ? valueFormat(last) : num(last, digits)}</div>
        <div className="ax-kpi__label">latest day</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${Math.max(rows.length, 1)}, 1fr)`, gap: 2, minHeight: 72, alignItems: "end" }}>
        {rows.length ? rows.map((row, index) => {
          const value = Number(row.value || 0);
          return <div key={`${row.date}-${index}`} title={`${row.date}: ${num(value, digits)}`} style={{ height: Math.max(3, Math.round((value / maxValue) * 66)), background: "var(--ax-text-5)", opacity: 0.72, borderRadius: 2 }} />;
        }) : <div style={{ color: "var(--ax-text-2)", fontSize: 12 }}>暂无数据</div>}
      </div>
    </div>
  );
}

function Funnel({ stages }: { stages: Row[] }) {
  const max = Math.max(1, ...stages.map((stage) => Number(stage.count || 0)));
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {stages.length ? stages.map((stage) => {
        const count = Number(stage.count || 0);
        return (
          <div key={String(stage.name)} style={{ display: "grid", gridTemplateColumns: "130px 1fr 80px", gap: 8, alignItems: "center", fontSize: 12 }}>
            <strong>{String(stage.name || "—")}</strong>
            <div style={{ height: 10, background: "var(--ax-bg-2)", borderRadius: 3, overflow: "hidden" }}>
              <div style={{ width: `${Math.max(3, (count / max) * 100)}%`, height: "100%", background: "var(--ax-text-5)" }} />
            </div>
            <span style={{ textAlign: "right" }}>{num(count)}</span>
          </div>
        );
      }) : <EmptyCard label="暂无漏斗数据" />}
    </div>
  );
}

function ReasonList({ rows }: { rows: Row[] }) {
  if (!rows.length) return <EmptyCard label="暂无拒绝原因数据" />;
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {rows.slice(0, 12).map((row, index) => (
        <div key={`${row.reason}-${index}`} style={{ display: "grid", gridTemplateColumns: "1fr 90px", gap: 8, fontSize: 12 }}>
          <span>{String(row.reason || "—")}</span>
          <strong style={{ textAlign: "right" }}>{num(row.count)}</strong>
        </div>
      ))}
    </div>
  );
}

export default AnalyticsTab;
