import React from "react";
import { getIndustryBoard, getMovers } from "../../../services/vkpi/agentOps-api";

// 行业大盘页:商业盘 + KOL盘 + 市场盘 + 市场预估(接 GET /metrics/industry-board)。
// 红线:只读展示;市场预估在真订单数据接入前诚实标 awaiting_data。

function fmtCents(v: unknown): string {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n) || n === 0) return "$0";
  return "$" + (n / 100).toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function fmtNum(v: unknown): string {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n.toLocaleString() : "—";
}

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-4">
      <div className="text-[11px] text-slate-400">{label}</div>
      <div className="mt-1 text-lg font-semibold text-white">{value}</div>
      {hint ? <div className="mt-0.5 text-[10px] text-slate-500">{hint}</div> : null}
    </div>
  );
}

function MoversPanel({ apiToken }: { apiToken: string }) {
  const [data, setData] = React.useState<any>(null);
  const [metric, setMetric] = React.useState("followers");
  React.useEffect(() => {
    if (!apiToken) return;
    getMovers(apiToken, 30, metric).then((r: any) => setData(r)).catch(() => setData(null));
  }, [apiToken, metric]);
  const risers: any[] = data?.risers || [];
  const fallers: any[] = data?.fallers || [];
  const Row = ({ x, up }: { x: any; up: boolean }) => (
    <div className="flex items-center justify-between text-[11px]">
      <span className="truncate text-slate-200">{x.name}</span>
      <span className={up ? "text-emerald-300" : "text-rose-300"}>
        {up ? "▲" : "▼"} {x.pct_change != null ? `${(x.pct_change * 100).toFixed(1)}%` : `Δ${fmtNum(x.delta)}`}
      </span>
    </div>
  );
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-[11px] font-medium text-cyan-300/80">近 30 天 movers · 谁在变好</div>
        <select value={metric} onChange={(e) => setMetric(e.target.value)} className="rounded border border-white/10 bg-white/[0.03] px-1.5 py-0.5 text-[10px] text-slate-300">
          <option value="followers">粉丝</option>
          <option value="total_views">播放</option>
          <option value="engagement_rate">互动率</option>
        </select>
      </div>
      {data?.status === "ok" ? (
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">{risers.slice(0, 5).map((x) => <Row key={`u${x.channel_id}`} x={x} up />)}{!risers.length && <div className="text-[10px] text-slate-500">—</div>}</div>
          <div className="space-y-1">{fallers.slice(0, 5).map((x) => <Row key={`d${x.channel_id}`} x={x} up={false} />)}{!fallers.length && <div className="text-[10px] text-slate-500">—</div>}</div>
        </div>
      ) : (
        <div className="text-[10px] text-slate-500">{data ? "暂无时序数据" : "加载中…"}</div>
      )}
    </div>
  );
}

export function IndustryBoardPage({ apiToken = "" }: { apiToken?: string }) {
  const [board, setBoard] = React.useState<any>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  const load = React.useCallback(() => {
    if (!apiToken) { setLoading(false); setError("未登录 / 无 token"); return; }
    setLoading(true); setError("");
    getIndustryBoard(apiToken)
      .then((r: any) => setBoard(r))
      .catch((e: any) => setError(e?.message || "加载失败"))
      .finally(() => setLoading(false));
  }, [apiToken]);
  React.useEffect(() => { load(); }, [load]);

  if (loading) return <div className="p-6 text-sm text-slate-400">行业大盘加载中…</div>;
  if (error) return <div className="p-6 text-sm text-red-300/80">行业大盘加载失败 · {error}</div>;

  const commerce = board?.commerce || {};
  const portfolio = commerce.portfolio || {};
  const gmv = commerce.gmv || {};
  const kol = board?.kol || {};
  const market = board?.market || {};
  const estimate = board?.market_estimate || {};
  const highValue: any[] = Array.isArray(kol.high_value) ? kol.high_value : [];

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-white">行业大盘 · 市场预估</h2>
        <button onClick={load} className="rounded-md border border-white/10 px-2 py-1 text-[11px] text-slate-300 hover:bg-white/[0.06]">刷新</button>
      </div>

      <div className="text-[11px] font-medium text-emerald-300/80">商业盘</div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="组合 ROI" value={portfolio.roi == null ? (portfolio.status === "awaiting_m5" ? "待数据" : "—") : portfolio.roi} />
        <Stat label="组合营收" value={fmtCents(portfolio.revenue_cents)} hint={portfolio.status === "awaiting_m5" ? "待真订单" : ""} />
        <Stat label="短链 GMV" value={fmtCents(gmv.revenue_cents)} hint={`${fmtNum(gmv.orders)} 单 / ${fmtNum(gmv.clicks)} 点击`} />
        <Stat label="短链数" value={fmtNum(gmv.links)} />
      </div>

      <div className="text-[11px] font-medium text-sky-300/80">KOL 盘</div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="KOL 池规模" value={fmtNum(kol.pool_size)} />
        <Stat label="高价值红人" value={fmtNum(highValue.length)} hint="按合作项目数" />
      </div>
      {highValue.length ? (
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
          <div className="mb-2 text-[11px] text-slate-400">高价值红人榜</div>
          <div className="space-y-1">
            {highValue.slice(0, 8).map((k: any) => (
              <div key={k.kol_pool_id} className="flex items-center justify-between text-[11px]">
                <span className="truncate text-slate-200">{k.name || `KOL #${k.kol_pool_id}`}</span>
                <span className="shrink-0 text-slate-400">{k.projects} 项目 · 权重 {k.recommendation_weight ?? "—"}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <MoversPanel apiToken={apiToken} />

      <div className="text-[11px] font-medium text-amber-300/80">市场盘</div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="竞品信号" value={fmtNum(market.competitor_signals)} />
        <Stat label="产品盘" value={fmtNum(market.products)} />
        <Stat label="销售归因" value={fmtNum(market.sales_attributions)} />
        <Stat label="Shopify 订单" value={fmtNum(market.shopify_orders)} hint={market.shopify_orders ? "" : "待 token"} />
      </div>

      <div className="rounded-xl border border-violet-300/15 bg-violet-500/[0.06] p-4">
        <div className="text-[11px] font-medium text-violet-200">市场预估</div>
        <div className="mt-1 text-sm text-white">
          {estimate.status === "ready" ? "数据就绪,可做提前盘判断" : "待真实订单数据(Shopify token)接入"}
        </div>
        <div className="mt-0.5 text-[10px] text-slate-500">{estimate.note}</div>
      </div>
    </div>
  );
}
