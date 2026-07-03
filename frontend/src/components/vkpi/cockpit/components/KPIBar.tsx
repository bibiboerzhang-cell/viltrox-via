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
  // 【K6 待补全卡诚实降级 2026-07-02】自查结论:vkpi_kol_pool 无 candidate_kind 列(grep 迁移 + 本地库
  // information_schema 均无),前端只能按 linked_main_kol_id 推导,而全池仅 1 行有链接 → 「待补全」
  // (existing_low_confidence)计数失真无意义。故本卡降级为灰态「数据不足(分类待后端)」:不可点、
  // 排末位、字号缩小,不再占一等位;等后端真出 candidate_kind 列再接真计数(lowConfCount 保留在此)。
  void lowConfCount; // 保留推导逻辑不删,接真时直接换回 value
  const cards = [
    { icon: Users,       label: "Pool 总数",       value: total,                                    sub: existingCount + " 已链主表 · " + newCount + " 未链(≈新)",    color: "#a855f7", filterKey: "" },
    { icon: Sparkles,    label: "新发现 KOL",      value: newCount,                                  sub: "按未链主表推导 · candidate_kind 待后端", color: "#c4b5fd", filterKey: "new" },
    { icon: Target,      label: "平均 V6 Fit",     value: avgFit,                                    sub: fits.length ? fits.length + " 个有效 · 旧V6分(未含RealER)" : "评分待生成", color: "#10b981", filterKey: null },
    { icon: TrendingUp,  label: "播放量汇总",      value: totalReach ? formatNumber(totalReach) : "待评估", sub: totalReach ? "Σ avg_views · 非去重触达" : "reach 字段待接入", color: "#ec4899", filterKey: null },
    { icon: AlertCircle, label: "待补全",          value: "数据不足",                                sub: "candidate_kind 列缺失 · 分类待后端",                                color: "#64748b", filterKey: null, degraded: true },
  ];

  return e("div", { className: "grid grid-cols-2 gap-1.5 md:grid-cols-3 xl:grid-cols-5" },
    cards.map((c: any, i: any) => {
      const isTotalCard = c.label === "Pool 总数";
      const clickable = !c.degraded && (c.filterKey !== null || (isTotalCard && onTotalClick));
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
          (c.degraded ? "opacity-55 " : "") +
          (isActive ? "border-white/[0.20]" : "border-white/[0.055]"),
        style: { background: isActive ? c.color + "0F" : "rgba(255,255,255,0.014)" },
        title: c.degraded ? "candidate_kind 列后端尚未落库,「待补全」暂无可信计数;接真后恢复" : undefined
      };
      if (clickable) cardProps.type = "button";
      return e(Card as any, cardProps,
        e("div", { className: "flex items-center gap-1.5" },
          e("span", { className: "rounded p-0.5", style: { background: c.color + "16", color: c.color } }, e(c.icon, { size: 9 })),
          e("span", { className: "min-w-0 flex-1 truncate text-[9px] text-slate-500" }, c.label),
          clickable && e(Filter, { size: 8, className: "text-slate-700", title: isTotalCard ? "查看全部 KOL" : "点击筛选" } as any)
        ),
        // 【K6】灰态卡字号缩小(15px→11px),灰色而非白色,视觉上退出一等位
        e("div", { className: "mt-1 font-medium leading-none tabular-nums " + (c.degraded ? "text-[11px] text-slate-500" : "text-[15px] text-white") }, c.value),
        e("div", { className: "mt-0.5 truncate text-[8px] text-slate-600" }, c.sub)
      );
    })
  );
}
