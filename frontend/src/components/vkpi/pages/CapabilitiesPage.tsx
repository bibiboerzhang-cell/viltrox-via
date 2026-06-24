import React from "react";
import { getCapabilities } from "../../../services/vkpi/agentOps-api";

// 平台能力总览:Agent-OS 能干什么(接 /agents/capabilities)。按组展示 + 就绪度。
// 红线:只读自描述清单。

const STATUS_CLS: Record<string, string> = {
  ready: "text-emerald-300 border-emerald-500/25 bg-emerald-500/[0.08]",
  awaiting_shopify_token: "text-amber-300 border-amber-500/25 bg-amber-500/[0.08]",
};

export function CapabilitiesPage({ apiToken = "" }: { apiToken?: string }) {
  const [caps, setCaps] = React.useState<any>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    if (!apiToken) { setLoading(false); return; }
    getCapabilities(apiToken).then((r: any) => setCaps(r)).catch(() => {}).finally(() => setLoading(false));
  }, [apiToken]);

  if (loading) return <div className="p-6 text-sm text-slate-400">能力总览加载中…</div>;
  if (!apiToken) return <div className="p-6 text-sm text-red-300/80">未登录 / 无 token</div>;

  const groups = caps?.groups || {};
  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-white">平台能力总览</h2>
        <span className="text-[12px] text-slate-400">{caps?.ready ?? 0} / {caps?.total ?? 0} 就绪</span>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {Object.entries(groups).map(([group, items]: [string, any]) => (
          <div key={group} className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
            <div className="mb-2 text-[12px] font-semibold text-white">{group}</div>
            <div className="space-y-1.5">
              {(Array.isArray(items) ? items : []).map((c: any, i: number) => (
                <div key={i} className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-[11px] text-slate-200">{c.name}</div>
                    <div className="truncate text-[9px] text-slate-500">{c.endpoint}</div>
                  </div>
                  <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[8px] ${STATUS_CLS[c.status] || "text-slate-400 border-white/10"}`}>
                    {c.status === "ready" ? "就绪" : c.status === "awaiting_shopify_token" ? "待 token" : c.status}
                    {c.live_rows != null ? ` · ${c.live_rows}` : ""}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="text-[10px] text-slate-500">{caps?.note}</div>
    </div>
  );
}
