// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { AlertCircle, Filter, Search, Sparkles, Target, TrendingUp, Users } from "lucide-react";
import { candidateKindGroup } from "../lib/candidateKind";
import { formatNumber } from "../lib/format";

const e = React.createElement;

export function KPIBar({ items, onCardClick, activeKindFilter, onTotalClick }: any) {
  const total = items.length;
  const fits = items.filter((i: any) => i.v6_fit != null).map((i: any) => i.v6_fit);
  const avgFit = fits.length ? Math.round(fits.reduce((a: any, b: any) => a + b, 0) / fits.length) : "待评估";

  // Search v2 breakdown
  const existingCount = items.filter((i: any) => candidateKindGroup(i.candidate_kind) === "existing").length;
  const newCount      = items.filter((i: any) => candidateKindGroup(i.candidate_kind) === "new").length;
  const validatingCount = items.filter((i: any) => i.candidate_kind === "new_discovered").length;
  const lowConfCount  = items.filter((i: any) => i.candidate_kind === "existing_low_confidence").length;

  const totalReach = items.reduce((sum: any, k: any) => {
    if (!k.estimated_country_reach) return sum;
    return sum + Object.values(k.estimated_country_reach).reduce((a: any, b: any) => a + b, 0);
  }, 0);
  
  // 诊断 P1-8/9/10 + P2-5 状态卡诚实化:candidate_kind 后端未供(前端按 linked_main_kol_id 推导,
  // 全池仅 1 行有链接→新/已有拆分失真);月度Reach 实为 avg_views 静态总和非去重触达;
  // V6Fit 是旧静态分未含 RealER 影子。下方卡名/sub 如实标注。
  // 【B6 Trend 诚实摘除 2026-07】原「本周高 Trend」卡(Flame 图标)已移除:后端不存在 trend_score
  // 列,trend_resonance 恒 null → 卡值恒「待评估」纯占位。数据接入后恢复:在下方数组补回
  // { icon: Flame, label: "本周高 Trend", value: items.filter(i => (i.trend_resonance||0) >= 0.5).length,
  //   color: "#ef4444", filterKey: null },并把外层 xl:grid-cols-5 改回 xl:grid-cols-6。
  const cards = [
    { icon: Users,       label: "Pool 总数",       value: total,                                    sub: existingCount + " 已链主表 · " + newCount + " 未链(≈新)",    color: "#a855f7", filterKey: "" },
    { icon: Sparkles,    label: "新发现 KOL",      value: newCount,                                  sub: "按未链主表推导 · candidate_kind 待后端", color: "#c4b5fd", filterKey: "new" },
    { icon: AlertCircle, label: "待补全",          value: lowConfCount,                              sub: "已链主表 · 数据不全(分类待后端)",                                  color: "#fdba74", filterKey: "existing_low_confidence" },
    { icon: Target,      label: "平均 V6 Fit",     value: avgFit,                                    sub: fits.length ? fits.length + " 个有效 · 旧V6分(未含RealER)" : "评分待生成", color: "#10b981", filterKey: null },
    { icon: TrendingUp,  label: "播放量汇总",      value: totalReach ? formatNumber(totalReach) : "待评估", sub: totalReach ? "Σ avg_views · 非去重触达" : "reach 字段待接入", color: "#ec4899", filterKey: null },
  ];

  return e("div", { className: "grid grid-cols-2 gap-1.5 md:grid-cols-3 xl:grid-cols-5" },
    cards.map((c: any, i: any) => {
      const isTotalCard = c.label === "Pool 总数";
      const clickable = c.filterKey !== null || (isTotalCard && onTotalClick);
      const isActive  = clickable && activeKindFilter === c.filterKey && c.filterKey !== "";
      const Card = clickable ? motion.button : motion.div;
      const cardProps: any = {
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
        className: "rounded-md border px-2.5 py-1.5 text-left transition-colors " +
          (clickable ? "cursor-pointer hover:border-white/[0.14] hover:bg-white/[0.03] " : "") +
          (isActive ? "border-white/[0.20]" : "border-white/[0.055]"),
        style: { background: isActive ? c.color + "0F" : "rgba(255,255,255,0.014)" }
      };
      if (clickable) cardProps.type = "button";
      return e(Card as any, cardProps,
        e("div", { className: "flex items-center gap-1.5" },
          e("span", { className: "rounded p-0.5", style: { background: c.color + "16", color: c.color } }, e(c.icon, { size: 9 })),
          e("span", { className: "min-w-0 flex-1 truncate text-[9px] text-slate-500" }, c.label),
          clickable && e(Filter, { size: 8, className: "text-slate-700", title: isTotalCard ? "查看全部 KOL" : "点击筛选" } as any)
        ),
        e("div", { className: "mt-1 text-[15px] font-medium leading-none text-white tabular-nums" }, c.value),
        e("div", { className: "mt-0.5 truncate text-[8px] text-slate-600" }, c.sub)
      );
    })
  );
}
