// #21 trend-pulse:近期行业帖 hashtag 实时聚合(自取数;数据稀疏如实显示,不编造)。
import React, { useEffect, useState } from "react";
import { Flame } from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

export function TrendPulseBar({ apiToken = "" }: { apiToken?: string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    apiFetch<any>("/api/admin/vkpi/industry-data/hashtag-trends/v0?limit=12&days=14", {}, apiToken)
      .then((d) => { if (alive) setData(d); })
      .catch(() => { /* 静默:无趋势显空 */ })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [apiToken]);

  const trends: any[] = data && Array.isArray(data.trends) ? data.trends : [];
  const total = (data && data.summary && data.summary.total_hashtags) || trends.length;
  return e("div", { className: "rounded-xl border border-line bg-panel p-3 flex items-center gap-3 backdrop-blur-xl" },
    e("div", { className: "flex items-center gap-2 shrink-0" },
      e("div", { className: "rounded-md p-1.5", style: { background: "rgba(239,68,68,0.18)" } }, e(Flame, { size: 12, className: "text-rose-400" })),
      e("div", null,
        e("div", { className: "text-[11px] font-semibold text-ink" }, "近期市场热词"),
        e("div", { className: "text-[9px] text-muted" }, loading ? "聚合中…" : trends.length ? `${total} 个 · 近 14 天行业帖` : "暂无行业帖热词")
      )
    ),
    e("div", { className: "flex-1 flex flex-wrap gap-1.5" },
      trends.length
        ? trends.slice(0, 12).map((t: any, i: number) => e("span", {
            key: i,
            className: "px-2 py-0.5 rounded-md text-[10px] font-medium border",
            style: { background: "rgba(239,68,68,0.06)", borderColor: "rgba(239,68,68,0.2)", color: "#fda4af" },
            title: `${t.post_count || 0} 帖${t.platform ? " · " + t.platform : ""}`,
          }, `#${t.hashtag}${t.post_count ? ` ·${t.post_count}` : ""}`))
        : e("span", { className: "text-[10px] text-muted" }, loading ? "…" : "等更多行业帖入库后自动出热词")
    )
  );
}
