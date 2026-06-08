// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useMemo, useState } from "react";
import { AlertTriangle, ChevronRight, Globe2 } from "lucide-react";
import { GeoTierChip } from "./GeoTierChip";
import { formatNumber } from "../lib/format";
import { COUNTRY_INFO, getCountryInfo, normalizeCountryCode } from "../data/countryInfo";

const e = React.createElement;

export function MarketCoverageCard({ items }) {
  const [showAll, setShowAll] = useState(false);
  const [showGaps, setShowGaps] = useState(false);
  
  // 聚合所有 KOL 的 country reach
  const countryStats = useMemo(() => {
    const stats = {};
    items.forEach(kol => {
      if (!kol.estimated_country_reach) return;
      Object.entries(kol.estimated_country_reach).forEach(([rawCountry, reach]) => {
        const country = normalizeCountryCode(rawCountry);
        if (!country) return;
        if (!stats[country]) stats[country] = { country, kol_count: 0, reach: 0 };
        stats[country].kol_count += 1;
        stats[country].reach += reach;
      });
    });
    return Object.values(stats).sort((a, b) => b.reach - a.reach);
  }, [items]);
  
  const displayStats = showAll ? countryStats : countryStats.slice(0, 5);
  const maxReach = Math.max(...countryStats.map(s => s.reach), 1);
  
  // 缺口检测:Tier A/B 国家但 KOL < 3
  const gaps = [];
  ["FR", "KR", "ES", "IT"].forEach(c => {
    const stat = countryStats.find(s => s.country === c);
    if (!stat || stat.kol_count < 3) {
      const cInfo = COUNTRY_INFO[c];
      gaps.push({ country: c, current: stat?.kol_count || 0, suggest: 3 - (stat?.kol_count || 0), info: cInfo });
    }
  });
  
  return e("div", { className: "rounded-xl border border-white/[0.07] bg-white/[0.018] backdrop-blur-xl overflow-hidden" },
    // Header — 紧凑
    e("div", { className: "px-4 py-2.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("div", { className: "flex items-center gap-2" },
        e("div", { className: "rounded-md p-1", style: { background: "rgba(6,182,212,0.15)" } }, e(Globe2, { size: 11, className: "text-cyan-400" })),
        e("h3", { className: "text-[11px] font-semibold text-white" }, "海外市场覆盖"),
        e("span", { className: "text-[10px] text-slate-500" }, "· " + countryStats.length + " 国 · " + gaps.length + " 缺口"),
      ),
      e("button", { 
        onClick: () => setShowAll(!showAll),
        className: "text-[10px] text-slate-400 hover:text-white flex items-center gap-1" 
      }, showAll ? "折叠" : "查看全部", e(ChevronRight, { size: 10, style: { transform: showAll ? "rotate(90deg)" : "none" } }))
    ),
    
    // Country rows — 紧凑(行高减小,字号小)
    e("div", { className: "px-4 py-2 space-y-1" },
      displayStats.map((s, i) => {
        const cInfo = getCountryInfo(s.country) || { code: s.country, flag: "·", name: s.country, tier: "?" };
        const widthPct = (s.reach / maxReach) * 100;
        return e("div", { key: s.country, className: "flex items-center gap-3 text-[10px] py-0.5" },
          e("div", { className: "flex items-center gap-1.5 w-[100px] shrink-0" },
            e("span", { style: { fontSize: 13 } }, cInfo.flag),
            e("span", { className: "text-white font-medium" }, cInfo.code),
            e(GeoTierChip, { tier: cInfo.tier })
          ),
          e("div", { className: "w-[50px] shrink-0 text-slate-300 tabular-nums" },
            e("span", { className: "text-white font-medium" }, s.kol_count),
            e("span", { className: "text-slate-500 ml-1" }, "KOL")
          ),
          e("div", { className: "flex-1 min-w-0" },
            e("div", { className: "geo-bar-bg" },
              e("div", { className: "geo-bar-fill", style: { 
                width: widthPct + "%",
                background: cInfo.tier === "A" ? "#10b981" : cInfo.tier === "B" ? "#fbbf24" : "#64748b"
              }})
            )
          ),
          e("div", { className: "w-[60px] text-right shrink-0 text-slate-300 tabular-nums" }, formatNumber(s.reach)),
          e("div", { className: "w-[74px] text-right shrink-0 tabular-nums text-[9px]",
            style: { color: cInfo.tier === "A" ? "#86efac" : cInfo.tier === "B" ? "#fde68a" : "#cbd5e1" }
          }, "GMV 待接入")
        );
      })
    ),
    
    // Gap alerts — 折叠
    gaps.length > 0 && e("div", { className: "border-t border-white/[0.06]" },
      e("button", {
        onClick: () => setShowGaps(!showGaps),
        className: "w-full px-4 py-2 flex items-center gap-2 hover:bg-white/[0.02]"
      },
        e(AlertTriangle, { size: 10, className: "text-amber-400" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-amber-300 font-medium" }, "算法建议 · " + gaps.length + " 市场配置缺口"),
        e(ChevronRight, { size: 10, className: "text-slate-500 ml-auto", style: { transform: showGaps ? "rotate(90deg)" : "none", transition: "transform 0.2s" } })
      ),
      showGaps && e("div", { className: "px-4 pb-2 space-y-1 bg-amber-500/[0.03]" },
        gaps.map(g => e("div", { key: g.country, className: "flex items-center gap-2 text-[10px] py-1" },
          e("span", { style: { fontSize: 12 } }, g.info.flag),
          e("span", { className: "text-white font-medium w-[24px]" }, g.country),
          e("span", { className: "text-slate-400 flex-1" }, g.info.name, " · " + g.current + " KOL · 建议补 " + g.suggest + " 个"),
          e("button", { className: "px-2 py-0.5 rounded text-[10px] border border-amber-500/30 bg-amber-500/[0.08] text-amber-300 hover:bg-amber-500/[0.15]" },
            "去发现"
          )
        ))
      )
    )
  );
}
