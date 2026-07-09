// Verbatim from vkpi_v6.15.7_integrated.html
// 波2门面:KPI 卡按 gtm_viz 执行标准重建 —— eyebrow 标签 + 状态点 + 大号数字 + 全宽 sparkline
// + 玻璃卡 + 错峰入场;去掉图标块/状态chip/冗长副文案(废话)。数据/交互零变。

import React from "react";
import { Sparkline } from "./Sparkline";
import { useRowFlash } from "./ui/rowFlash";
import { useT } from "../lib/i18n";

const e = React.createElement;

export function MetricCard({ item, i, scope, onClick }: any) {
  const { t } = useT();
  const { label, format } = item;
  const scopeData = item.data[scope] || item.data.all;
  const { value, source, sourceLabel, color, spark, waiting, anomaly } = scopeData;
  // 车道B row-flash:数值变化 soft pulse 一次;首帧不闪。
  const flashRef = useRowFlash<HTMLDivElement>(value);

  const isAccumulating = source === "accumulating";
  const hasNoValue = value === null || value === undefined;
  const isPending = source === "pending" || (hasNoValue && !isAccumulating);
  const isUnavailable = isPending || isAccumulating;

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

  return e("div", {
    ref: flashRef,
    onClick,
    className: "ds-kpi ds-kpi-rise cursor-pointer",
    style: { animationDelay: `${(i || 0) * 0.05}s` },
    title: t(sourceLabel || (isUnavailable ? "待接入" : "真实")),
  },
    // eyebrow:状态点 + 指标名
    e("div", { className: "ds-kpi__k" },
      e("span", { className: `ds-kpi__dot ${isUnavailable ? "ds-kpi__dot--pend" : "ds-kpi__dot--good"}` }),
      e("span", { className: "truncate" }, t(label))
    ),
    // 大号数字
    e("div", { className: `ds-kpi__val ${isUnavailable ? "ds-kpi__val--pend" : ""}` }, formatValue(value)),
    // 全宽 sparkline
    !isUnavailable && spark && e(Sparkline, { color, data: spark, width: 240, height: 30, fluid: true }),
    // 待接入/累积中:一行极简说明(替代原冗长副文案)
    isUnavailable && e("div", { className: "mt-2 text-[10px] text-warn truncate" }, t(waiting || sourceLabel || "待接入")),
    // 单源污染标注
    anomaly && e("div", {
      className: "mt-1 text-[9px] text-warn truncate",
      title: "单条 evidence 占总量 80% 以上,数值可能被单源污染(数据清理由主控负责)"
    }, anomaly)
  );
}
