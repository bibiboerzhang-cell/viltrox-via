import React from "react";
import {
  getMarketTrends,
  type MarketObservation,
  type MarketObservationConfidence,
  type MarketObservationKind,
  type MarketTrendsResponse,
} from "../../../services/vkpi/marketTrends-api";

// N7 市场趋势 / 观察页:列出 market_observation 合成的市场观察条目。
// 每条显示 来源 / 证据 / 置信度 / 时间。纯只读,无真数据诚实显示空态。

const KIND_FILTERS: Array<{ key: "" | MarketObservationKind; label: string }> = [
  { key: "", label: "全部" },
  { key: "热点", label: "热点" },
  { key: "竞品", label: "竞品" },
  { key: "机会", label: "机会" },
  { key: "风险", label: "风险" },
];

const KIND_STYLE: Record<MarketObservationKind, string> = {
  热点: "border-amber-300/30 bg-amber-500/[0.12] text-amber-100",
  竞品: "border-rose-300/30 bg-rose-500/[0.12] text-rose-100",
  机会: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100",
  风险: "border-orange-300/30 bg-orange-500/[0.12] text-orange-100",
};

const CONF_STYLE: Record<MarketObservationConfidence, string> = {
  high: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100",
  med: "border-sky-300/30 bg-sky-500/[0.12] text-sky-100",
  low: "border-slate-300/20 bg-white/[0.04] text-slate-300",
};

const CONF_LABEL: Record<MarketObservationConfidence, string> = {
  high: "高置信",
  med: "中置信",
  low: "低置信",
};

function formatTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // 按观看者浏览器时区显示(项目约定:存 UTC,展示走本地时区)
  return d.toLocaleString();
}

function evidenceSummary(refs: MarketObservation["evidence_refs"]): string {
  if (!Array.isArray(refs) || refs.length === 0) return "无";
  return refs
    .map((r) => {
      if (!r || typeof r !== "object") return "";
      const type = typeof r.type === "string" ? r.type : "evidence";
      const extra = Object.keys(r).filter((k) => k !== "type" && k !== "raw");
      return extra.length ? `${type}(${extra.join(",")})` : type;
    })
    .filter(Boolean)
    .join(" · ");
}

function ObservationCard({ obs, generatedAt }: { obs: MarketObservation; generatedAt: string }) {
  const [open, setOpen] = React.useState(false);
  const kindClass = KIND_STYLE[obs.kind] ?? "border-white/10 bg-white/[0.04] text-slate-200";
  const confClass = CONF_STYLE[obs.confidence] ?? CONF_STYLE.med;
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-md border px-2 py-0.5 text-[10px] font-medium ${kindClass}`}>{obs.kind}</span>
        <span className={`rounded-md border px-2 py-0.5 text-[10px] font-medium ${confClass}`}>
          {CONF_LABEL[obs.confidence] ?? obs.confidence}
        </span>
        <span className="text-[13px] font-medium text-white">{obs.topic}</span>
      </div>

      <div className="mt-2 grid grid-cols-1 gap-1 text-[11px] text-slate-300 sm:grid-cols-3">
        <div>
          <span className="text-slate-500">来源</span> <span className="text-slate-200">{obs.source || "—"}</span>
        </div>
        <div>
          <span className="text-slate-500">证据</span>{" "}
          <span className="text-slate-200">{evidenceSummary(obs.evidence_refs)}</span>
        </div>
        <div>
          <span className="text-slate-500">时间</span>{" "}
          <span className="text-slate-200">{formatTime(generatedAt)}</span>
        </div>
      </div>

      {obs.suggested_action ? (
        <div className="mt-2 text-[11px] text-sky-200/90">建议:{obs.suggested_action}</div>
      ) : null}

      {Array.isArray(obs.evidence_refs) && obs.evidence_refs.length ? (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="text-[10px] text-slate-400 hover:text-slate-200"
          >
            {open ? "收起证据明细" : `证据明细 (${obs.evidence_refs.length})`}
          </button>
          {open ? (
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-white/[0.06] bg-black/20 p-2 text-[10px] leading-relaxed text-slate-300/90">
              {JSON.stringify(obs.evidence_refs, null, 2)}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function MarketTrendsPage({ apiToken = "" }: { apiToken?: string }) {
  const [data, setData] = React.useState<MarketTrendsResponse | null>(null);
  const [filter, setFilter] = React.useState<"" | MarketObservationKind>("");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  const load = React.useCallback(
    (kind: "" | MarketObservationKind) => {
      if (!apiToken) {
        setError("未登录 / 无 token");
        return;
      }
      setLoading(true);
      setError("");
      getMarketTrends(apiToken, kind || undefined)
        .then((r) => setData(r))
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "加载失败"))
        .finally(() => setLoading(false));
    },
    [apiToken],
  );

  React.useEffect(() => {
    load(filter);
  }, [load, filter]);

  const observations = data?.observations ?? [];
  const generatedAt = data?.generated_at ?? "";
  const byKind = data?.by_kind ?? {};
  const sources = data?.sources_used ?? [];

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-white">市场趋势 / 观察</h2>
        <button
          type="button"
          onClick={() => load(filter)}
          disabled={loading || !apiToken}
          className="rounded-md border border-white/10 px-3 py-1.5 text-[12px] text-slate-200 hover:bg-white/[0.06] disabled:opacity-50"
        >
          {loading ? "刷新中…" : "刷新"}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {KIND_FILTERS.map((f) => {
          const active = filter === f.key;
          const count = f.key ? byKind[f.key] : undefined;
          return (
            <button
              key={f.key || "all"}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`rounded-md border px-2.5 py-1 text-[11px] ${
                active
                  ? "border-sky-300/40 bg-sky-500/[0.18] text-sky-100"
                  : "border-white/10 bg-white/[0.02] text-slate-300 hover:bg-white/[0.06]"
              }`}
            >
              {f.label}
              {typeof count === "number" ? <span className="ml-1 text-slate-400">{count}</span> : null}
            </button>
          );
        })}
      </div>

      {error ? <div className="text-[12px] text-red-300/80">{error}</div> : null}

      {data ? (
        <div className="text-[11px] text-slate-400">
          共 {observations.length} 条观察
          {sources.length ? <> · 来源 {sources.join(", ")}</> : null}
          {generatedAt ? <> · 生成于 {formatTime(generatedAt)}</> : null}
        </div>
      ) : null}

      {!loading && data && observations.length === 0 ? (
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-6 text-center text-[12px] text-slate-400">
          暂无市场观察(无真数据时诚实留空)。
        </div>
      ) : null}

      <div className="space-y-2">
        {observations.map((obs, i) => (
          <ObservationCard key={`${obs.kind}-${obs.topic}-${i}`} obs={obs} generatedAt={generatedAt} />
        ))}
      </div>
    </div>
  );
}

export default MarketTrendsPage;
