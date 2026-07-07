// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { m } from "framer-motion";
import { Sparkline } from "./Sparkline";
import { useRowFlash } from "./ui/rowFlash";
import { useT } from "../lib/i18n";

const e = React.createElement;

export function MetricCard({ item, i, scope, onClick }: any) {
  const { t } = useT();
  const { label, sub, icon: Icon, format } = item;
  const scopeData = item.data[scope] || item.data.all;
  const { value, trend, source, sourceLabel, color, spark, waiting, anomaly } = scopeData;
  // 车道B row-flash:数值变化(轮询刷出新值/切 scope)soft pulse 一次;首帧不闪。
  const flashRef = useRowFlash<HTMLDivElement>(value);
  
  const isAccumulating = source === "accumulating";
  const hasNoValue = value === null || value === undefined;
  const isPending = source === "pending" || (hasNoValue && !isAccumulating);
  const isUnavailable = isPending || isAccumulating;
  
  // Format value
  const formatValue = (v: any) => {
    if (v === null || v === undefined) return "--";
    if (format === "compact") {
      if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
      if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
      if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
      return v.toString();
    }
    if (format === "money") {
      if (v >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
      if (v >= 1e3) return "$" + (v / 1e3).toFixed(0) + "k";
      return "$" + v;
    }
    if (format === "percent") return v.toFixed(2) + "%";
    if (format === "multiplier") return v.toFixed(2) + "x";
    if (format === "number") return v.toLocaleString();
    return String(v);
  };
  
  return e(m.div, {
    ref: flashRef,
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    transition: { delay: i * 0.04, duration: 0.5, ease: [0.16, 1, 0.3, 1] },
    whileHover: { y: -2 },
    onClick: onClick,
    className: `group relative cursor-pointer overflow-hidden rounded-xl border ${isUnavailable ? "border-amber-500/15" : "border-white/[0.08]"} bg-white/[0.025] p-4 backdrop-blur-xl transition-colors hover:border-white/[0.18] hover:bg-white/[0.04]`,
  },
    // Header: icon + label + source chip
    e("div", { className: "mb-2 flex items-start justify-between gap-2 text-[12px] text-slate-400" },
      e("span", { className: "flex items-center gap-2 min-w-0" },
        e("span", { className: "shrink-0 rounded-md bg-white/5 p-1.5", style: { color } }, e(Icon, { size: 12 })),
        e("span", { className: "truncate" }, t(label))
      ),
      // Source chip
      isAccumulating
        ? e("span", { className: "shrink-0 rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-medium text-amber-300" }, t(sourceLabel || "累积中"))
        : isPending
          ? e("span", { className: "shrink-0 rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-medium text-amber-300" }, t(sourceLabel || "待接入"))
          : e("span", { className: "shrink-0 rounded-md bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-medium text-emerald-300" }, t(sourceLabel || "真实"))
    ),
    // Body
    e("div", { className: "flex items-end justify-between gap-2" },
      e("div", { className: "min-w-0" },
        e("div", { 
          className: `text-2xl font-light tracking-tight tabular-nums ${isPending || hasNoValue ? "text-slate-500" : "text-white"}` 
        }, formatValue(value)),
        e("div", { 
          className: `mt-1 text-[10px] truncate ${isUnavailable ? "text-amber-400/70" : "text-slate-500"}`
        }, isUnavailable ? t(waiting || trend) : t(sub || trend))
      ),
      !isUnavailable && spark && e(Sparkline, { color, data: spark })
    ),
    // 单源污染防御标注(2026-06-12 波3 R2):单条 evidence 占比 >80% 时提示
    anomaly && e("div", {
      className: "mt-1 text-[9px] text-amber-400 truncate",
      title: "单条 evidence 占总量 80% 以上,数值可能被单源污染(数据清理由主控负责)"
    }, anomaly),
    // Trend footer (only for non-pending)
    !isUnavailable && trend && e("div", {
      className: "mt-2 pt-2 border-t border-white/[0.04] text-[10px] text-emerald-400 truncate"
    }, "↑ " + t(trend))
  );
}
