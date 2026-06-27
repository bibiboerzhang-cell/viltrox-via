import React from "react";
import { getDiscoveryProviders, federatedSearch, discoveryEnroll } from "../../../services/vkpi/agentOps-api";

// 联邦发现页:跨源搜达人(自有 + Apify + 商业占位)→ 一键落 Pool(去重)。
// 红线:落新档数据薄诚实;进 MY KOL 仍手动勾选。

export function DiscoveryPage({ apiToken = "" }: { apiToken?: string }) {
  const [providers, setProviders] = React.useState<any[]>([]);
  const [q, setQ] = React.useState("");
  const [res, setRes] = React.useState<any>(null);
  const [enrollMsg, setEnrollMsg] = React.useState("");
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    if (!apiToken) return;
    getDiscoveryProviders(apiToken).then((r: any) => setProviders(r?.providers || [])).catch(() => {});
  }, [apiToken]);

  const run = React.useCallback(() => {
    if (!apiToken || !q.trim()) return;
    setLoading(true); setEnrollMsg(""); setRes(null);
    federatedSearch(apiToken, q.trim()).then((r: any) => setRes(r)).catch((e: any) => setEnrollMsg(e?.message || "搜索失败")).finally(() => setLoading(false));
  }, [apiToken, q]);

  const enroll = React.useCallback(() => {
    if (!apiToken || !q.trim()) return;
    setEnrollMsg("落库中…");
    discoveryEnroll(apiToken, q.trim())
      .then((r: any) => setEnrollMsg(`已落 Pool:新增 ${r.enrolled ?? 0} · 去重跳过 ${r.skipped ?? 0}(进 MY KOL 仍需手动勾选)`))
      .catch((e: any) => setEnrollMsg(e?.message || "落库失败"));
  }, [apiToken, q]);

  const sources = res?.sources || {};
  const results: any[] = res?.results || [];

  return (
    <div className="space-y-4 p-4">
      <h2 className="text-base font-semibold text-white">联邦发现 · 补人</h2>

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="产品/需求/关键词,如 sony 闪光灯 摄影"
          className="min-w-[260px] flex-1 rounded-md border border-white/10 bg-white/[0.03] px-3 py-1.5 text-[12px] text-slate-200"
        />
        <button onClick={run} disabled={loading || !apiToken} className="rounded-md border border-sky-300/30 bg-sky-500/[0.15] px-3 py-1.5 text-[12px] text-sky-100 hover:bg-sky-500/[0.25] disabled:opacity-50">{loading ? "搜索中…" : "联邦搜索"}</button>
        {results.length ? <button onClick={enroll} className="rounded-md border border-emerald-300/30 bg-emerald-500/[0.15] px-3 py-1.5 text-[12px] text-emerald-100 hover:bg-emerald-500/[0.25]">一键落 Pool</button> : null}
      </div>

      <div className="flex flex-wrap gap-1.5 text-[10px]">
        {providers.map((p: any) => (
          <span key={p.name} className={`rounded border px-1.5 py-0.5 ${p.enabled && p.adapter_ready ? "border-emerald-500/30 text-emerald-300" : "border-white/10 text-slate-500"}`}>
            {p.name}{p.enabled ? "" : "·停"}{!p.adapter_ready && p.name !== "internal_pool" ? "·待key" : ""}
          </span>
        ))}
      </div>

      {enrollMsg ? <div className="text-[12px] text-emerald-300/80">{enrollMsg}</div> : null}

      {res ? (
        <div className="space-y-2">
          <div className="text-[11px] text-slate-400">
            源状态:{Object.entries(sources).map(([k, v]: [string, any]) => `${k}=${v.status}(${v.count})`).join(" · ")}
          </div>
          {results.length ? (
            <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] divide-y divide-white/[0.04]">
              {results.map((x: any, i: number) => (
                <div key={i} className="flex items-center justify-between px-3 py-2 text-[11px]">
                  <span className="truncate text-slate-200">{x.name || x.handle || `#${x.kol_pool_id}`}</span>
                  <span className="shrink-0 text-slate-500">{x.platform || "—"} · {x.source}{x.in_pool ? " · 已在池" : ""}</span>
                </div>
              ))}
            </div>
          ) : <div className="text-[12px] text-slate-500">无结果(商业源未配置时只有自有源;配 VKPI_APIFY_SEARCH_ACTORS 开 Apify 搜索)</div>}
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-8 text-center text-[12px] text-slate-500">输入关键词 → 联邦搜索 → 一键落 Pool</div>
      )}
    </div>
  );
}
