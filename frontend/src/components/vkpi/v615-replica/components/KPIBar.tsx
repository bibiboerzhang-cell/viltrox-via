// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { AlertCircle, Filter, Flame, Search, Sparkles, Target, TrendingUp, Users } from "lucide-react";
import { candidateKindGroup } from "../lib/candidateKind";
import { formatNumber } from "../lib/format";

const e = React.createElement;

export function KPIBar({ items, onCardClick, activeKindFilter, onTotalClick }) {
  const total = items.length;
  const fits = items.filter(i => i.v6_fit != null).map(i => i.v6_fit);
  const avgFit = fits.length ? Math.round(fits.reduce((a, b) => a + b, 0) / fits.length) : "待评估";
  
  const trendItems = items.filter(i => i.trend_resonance != null);
  const highTrend = trendItems.length ? items.filter(i => (i.trend_resonance || 0) >= 0.5).length : "待评估";
  
  // Search v2 breakdown
  const existingCount = items.filter(i => candidateKindGroup(i.candidate_kind) === "existing").length;
  const newCount      = items.filter(i => candidateKindGroup(i.candidate_kind) === "new").length;
  const validatingCount = items.filter(i => i.candidate_kind === "new_discovered").length;
  const lowConfCount  = items.filter(i => i.candidate_kind === "existing_low_confidence").length;
  
  const totalReach = items.reduce((sum, k) => {
    if (!k.estimated_country_reach) return sum;
    return sum + Object.values(k.estimated_country_reach).reduce((a, b) => a + b, 0);
  }, 0);
  
  const cards = [
    { icon: Users,       label: "Pool 总数",       value: total,                                    sub: existingCount + " 已有 · " + newCount + " 新发现",    color: "#a855f7", filterKey: "" },
    { icon: Sparkles,    label: "新发现 KOL",      value: newCount,                                  sub: validatingCount + " 校验中 · " + (newCount - validatingCount) + " 待画像", color: "#c4b5fd", filterKey: "new" },
    { icon: AlertCircle, label: "待补全",          value: lowConfCount,                              sub: "已有库 · 数据不全",                                  color: "#fdba74", filterKey: "existing_low_confidence" },
    { icon: Target,      label: "平均 V6 Fit",     value: avgFit,                                    sub: fits.length ? fits.length + " 个有效" : "评分待生成", color: "#10b981", filterKey: null },
    { icon: Flame,       label: "本周高 Trend",    value: highTrend,                                 sub: trendItems.length ? "真实 trend 字段" : "Trend 数据待接入", color: "#ef4444", filterKey: null },
    { icon: TrendingUp,  label: "月度估算 Reach",  value: totalReach ? formatNumber(totalReach) : "待评估", sub: totalReach ? "avg_views 汇总" : "reach 字段待接入", color: "#ec4899", filterKey: null },
  ];
  
  return e("div", { className: "grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-4" },
    cards.map((c, i) => {
      const isTotalCard = c.label === "Pool 总数";
      const clickable = c.filterKey !== null || (isTotalCard && onTotalClick);
      const isActive  = clickable && activeKindFilter === c.filterKey && c.filterKey !== "";
      const Card = clickable ? motion.button : motion.div;
      const cardProps = {
        key: c.label,
        initial: { opacity: 0, y: 8 },
        animate: { opacity: 1, y: 0 },
        transition: { delay: i * 0.04, duration: 0.4 },
        onClick: clickable ? () => {
          if (isTotalCard && onTotalClick) {
            onTotalClick();
            return;
          }
          onCardClick(c.filterKey);
        } : undefined,
        className: "rounded-xl border p-3 text-left backdrop-blur-xl transition-colors " +
          (clickable ? "cursor-pointer hover:border-white/[0.18] hover:bg-white/[0.04] " : "") +
          (isActive ? "border-white/[0.25]" : "border-white/[0.08]"),
        style: { background: isActive ? c.color + "12" : "rgba(255,255,255,0.025)" }
      };
      if (clickable) cardProps.type = "button";
      return e(Card, cardProps,
        e("div", { className: "flex items-center gap-1.5 mb-1" },
          e("span", { className: "rounded-md p-1", style: { background: c.color + "1a", color: c.color } }, e(c.icon, { size: 11 })),
          e("span", { className: "text-[10px] text-slate-400 flex-1" }, c.label),
          clickable && e(Filter, { size: 9, className: "text-slate-600", title: isTotalCard ? "查看全部 KOL" : "点击筛选" })
        ),
        e("div", { className: "text-xl font-light text-white tabular-nums" }, c.value),
        e("div", { className: "text-[9px] text-slate-500 truncate" }, c.sub)
      );
    })
  );
}
