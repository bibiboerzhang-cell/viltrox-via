// 纯重构:从 KOLDetailDrawerSections.tsx 抽出的内聚展示子区块(只吃 props,零 state/effect/handler)。
// idiom 保真:沿用 e()=React.createElement 写法。所有渲染逻辑逐字搬运,行为零变。
// 红线:本文件渲染 V6 Fit Breakdown 仅做只读展示(scoreText/fixedOrDash),绝不读写评分逻辑。

import React from "react";
import { Camera, Check, Flame, Globe2, Layers } from "lucide-react";
import { GeoTierChip } from "./GeoTierChip";
import { formatNumber } from "../lib/format";
import { BRAND_TIER } from "../data/brandTier";
import { getCountryInfo } from "../data/countryInfo";
import { fixedOrDash, pctOrZero, scoreText } from "./KOLDetailDrawer.helpers";

const e = React.createElement;

// ── 当前设备 & 升级机会 ──
export function KOLDrawerDevices({ item, devices }: any) {
  if (!devices.camera_body) return null;
  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center gap-1.5 mb-2" },
      e(Camera, { size: 11, className: "text-slate-400" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "当前设备 & 升级机会")
    ),
    // Camera body
    e("div", { className: "flex items-center gap-2 mb-2" },
      e("span", { className: "text-[10px] text-slate-500" }, "机身"),
      e("span", { className: "text-[12px] text-white font-medium" }, devices.camera_body)
    ),
    // Lenses
    devices.lenses.length > 0 && e("div", { className: "space-y-1 mb-2" },
      e("div", { className: "text-[10px] text-slate-500 mb-1" }, "在用镜头"),
      devices.lenses.map((l: any, i: number) => {
        const bi = (BRAND_TIER as any)[l.brand] || { color: "#94a3b8", label: l.brand };
        return e("div", { key: i, className: "flex items-center gap-2 text-[11px]" },
          e("span", {
            className: "px-1.5 py-0.5 rounded text-[9px] font-medium uppercase tracking-wider",
            style: {
              background: bi.tier === "viltrox" ? "rgba(168,85,247,0.18)"
                       : bi.tier === "competitor" ? "rgba(248,113,113,0.15)"
                       : "rgba(148,163,184,0.12)",
              color: bi.color
            }
          }, bi.label),
          e("span", { className: "text-white" }, l.model),
          l.type === "viltrox" && e("span", { className: "ml-auto text-[9px] text-emerald-400 inline-flex items-center gap-0.5" }, e(Check, { size: 9 }), "已用")
        );
      })
    ),
    // Upgrade window
    e("div", { className: "flex items-center gap-2 mt-2 pt-2 border-t border-white/[0.04]" },
      e("span", { className: "text-[10px] text-slate-500" }, "Upgrade 窗口"),
      e("span", {
        className: "px-2 py-0.5 rounded text-[10px] font-medium",
        style: {
          background: devices.upgrade_window === "very high" ? "rgba(16,185,129,0.2)"
                   : devices.upgrade_window === "high" ? "rgba(16,185,129,0.15)"
                   : devices.upgrade_window === "medium" ? "rgba(251,191,36,0.15)"
                   : "rgba(100,116,139,0.15)",
          color: devices.upgrade_window === "very high" ? "#34d399"
               : devices.upgrade_window === "high" ? "#86efac"
               : devices.upgrade_window === "medium" ? "#fde68a"
               : "#94a3b8"
        }
      }, devices.upgrade_window),
      e("span", { className: "text-[10px] text-slate-500" }, "× 系数 " + fixedOrDash(item.upgrade_factor || 1, 2))
    )
  );
}

// ── Geo distribution ──
export function KOLDrawerGeoDistribution({ item, geoDistribution }: any) {
  if (!(geoDistribution.length > 0)) return null;
  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center gap-1.5 mb-2" },
      e(Globe2, { size: 11, className: "text-cyan-400" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "粉丝地理分布 · 估算 Reach")
    ),
    e("div", { className: "space-y-1.5" },
      geoDistribution.map((g: any, i: number) => {
        const cInfo = getCountryInfo(g.country) || { code: g.country, flag: "·", name: g.country, tier: "?" };
        const reach = item.estimated_country_reach?.[cInfo.code] || item.estimated_country_reach?.[g.country];
        const sharePct = pctOrZero(g.share);
        return e("div", { key: i, className: "flex items-center gap-2 text-[11px]" },
          e("span", { style: { fontSize: 12 } }, cInfo.flag),
          e("span", { className: "text-white font-medium w-[28px]" }, cInfo.code),
          e(GeoTierChip, { tier: cInfo.tier }),
          e("div", { className: "flex-1 geo-bar-bg max-w-[120px]" },
            e("div", { className: "geo-bar-fill", style: {
              width: sharePct + "%",
              background: cInfo.tier === "A" ? "#10b981" : cInfo.tier === "B" ? "#fbbf24" : "#64748b"
            }})
          ),
          e("span", { className: "text-slate-300 tabular-nums w-[40px] text-right" }, sharePct.toFixed(0) + "%"),
          reach && e("span", { className: "text-slate-500 tabular-nums text-[10px] ml-auto" }, "~" + formatNumber(reach) + " reach")
        );
      })
    )
  );
}

// ── V6 Fit Breakdown ──
export function KOLDrawerV6Breakdown({ item, v6Breakdown }: any) {
  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center gap-1.5 mb-2" },
      e(Layers, { size: 11, className: "text-purple-400" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "V6 Fit 公式 Breakdown")
    ),
    e("div", { className: "space-y-1.5" },
      [
        { label: "Base Match (内容)",        value: v6Breakdown?.base ?? item.v6_fit,         suffix: "" },
        { label: "× Industry Tier",          value: v6Breakdown?.industry,     suffix: "" },
        { label: "× Upgrade Factor",         value: v6Breakdown?.upgrade ?? item.upgrade_factor,      suffix: "" },
        { label: "× Geo Match (海外加权)",    value: v6Breakdown?.geo_match ?? item.geo_match,    suffix: "" },
        { label: "× Real ER",                value: v6Breakdown?.real_er,      suffix: "" },
        { label: "× Loyalty Depth",          value: v6Breakdown?.loyalty,      suffix: "" },
        { label: "× Trend Resonance",        value: v6Breakdown?.trend ?? item.trend_resonance,        suffix: "" },
        { label: "× Platform Native",        value: v6Breakdown?.platform_native, suffix: "" },
        { label: "× Price Match",            value: v6Breakdown?.price_match,  suffix: "" },
        { label: "× Network Boost",          value: v6Breakdown?.network,      suffix: "" },
      ].map((m, i) => {
        const val = m.value;
        const isBase = i === 0;
        const isPositive = !isBase && val >= 1.0;
        const isNegative = !isBase && val < 1.0;
        return e("div", { key: i, className: "flex items-center justify-between text-[11px]" },
          e("span", { className: "text-slate-400" }, m.label),
          e("span", {
            className: "tabular-nums font-medium",
            style: { color: isBase ? "#fff" : isPositive ? "#86efac" : isNegative ? "#fca5a5" : "#fff" }
          }, isBase ? (val ?? "—") : fixedOrDash(val))
        );
      }),
      v6Breakdown?.competitor_decay < 0 && e("div", { className: "flex items-center justify-between text-[11px]" },
        e("span", { className: "text-slate-400" }, "− Competitor Decay"),
        e("span", { className: "tabular-nums font-medium text-rose-400" }, v6Breakdown.competitor_decay)
      ),
      e("div", { className: "border-t border-white/[0.06] pt-1.5 mt-1 flex items-center justify-between" },
        e("span", { className: "text-[11px] text-slate-300 font-medium" }, "= Final V6 Fit"),
        e("span", { className: "text-[14px] font-semibold tabular-nums",
          style: { color: item.v6_fit >= 85 ? "#10b981" : item.v6_fit >= 70 ? "#fbbf24" : "#fb923c" }
        }, scoreText(item.v6_fit))
      )
    )
  );
}

// ── Trend hits ──
export function KOLDrawerTrendHits({ trendHits }: any) {
  if (!(trendHits.length > 0)) return null;
  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center gap-1.5 mb-2" },
      e(Flame, { size: 11, className: "text-rose-400" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "本周 Trend 命中")
    ),
    e("div", { className: "flex flex-wrap gap-1" },
      trendHits.map((t: any, i: number) => e("span", {
        key: i,
        className: "px-2 py-0.5 rounded text-[10px] border",
        style: { background: "rgba(239,68,68,0.08)", borderColor: "rgba(239,68,68,0.2)", color: "#fda4af" }
      }, "#" + t))
    )
  );
}
