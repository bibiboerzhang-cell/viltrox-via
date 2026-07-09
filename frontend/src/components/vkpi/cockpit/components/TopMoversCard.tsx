// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { m } from "framer-motion";
import { TrendingUp } from "lucide-react";
import { useT } from "../lib/i18n";

const e = React.createElement;

export function TopMoversCard({ movers, onMoverClick, onViewAll }: any) {
  const { t } = useT();
  return e(m.div, {
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    transition: { delay: 0.24 },
    className: "h-full rounded-xl border border-line bg-panel p-4 backdrop-blur-xl flex flex-col",
  },
    e("div", { className: "mb-3 flex items-center justify-between" },
      e("div", { className: "flex items-center gap-2" },
        e(TrendingUp, { size: 14, className: "text-good" }),
        e("h3", { className: "text-sm font-semibold text-ink" }, "V6 Fit Top")
      ),
      e("span", { className: "text-[10px] text-muted" }, "真实 KOL Pool")
    ),
    e("div", { className: "flex-1 space-y-2" },
      movers.length === 0
        // D7 空态诚实化(2026-07-02):后端 /dashboard/fit-movers 不足两天会回落「当前 Fit 最高」
        // (top_fit,仍是真数据),所以走到空卡 = fit 每日快照还一份都没有、Pool 里也没有已评分行。
        ? e("div", { className: "rounded-md border border-dashed border-line px-3 py-8 text-center text-[11px] text-muted" },
            "fit 快照累积中(每日快照;movers 需≥2天)",
            e("div", { className: "mt-1 text-[9px] text-muted" }, "暂无已评分 KOL 可显示")
          )
        : movers.map((m: any, i: any) => e("div", {
        key: m.handle,
        onClick: () => onMoverClick && onMoverClick(m),
        className: "flex items-center gap-2.5 rounded-md py-1.5 px-2 hover:bg-accent-soft cursor-pointer transition-colors"
      },
        // Badge icon
        e("div", { 
          className: "shrink-0 h-6 w-6 rounded-full flex items-center justify-center text-[11px] font-bold",
          style: { background: `${m.badgeColor}22`, color: m.badgeColor }
        }, m.badge),
        // Name + note
        e("div", { className: "min-w-0 flex-1" },
          e("div", { className: "flex items-center gap-1.5" },
            e("span", { className: "text-[12px] font-medium text-ink truncate" }, m.handle),
            m.type === "matrix" && e("span", { className: "shrink-0 rounded px-1 py-0.5 text-[8px] uppercase tracking-wider bg-accent-soft text-accent" }, "矩阵")
          ),
          e("div", { className: "text-[9px] text-muted truncate" }, m.note)
        ),
        // Delta
        e("div", { className: "shrink-0 text-right" },
          e("div", { 
            className: "text-[11px] font-semibold tabular-nums",
            style: { color: m.deltaFollower.startsWith("-") ? "#ef4444" : "#10b981" }
          }, m.deltaFollower),
          e("div", { className: "text-[9px] text-muted tabular-nums" }, m.deltaReach)
        )
      ))
    ),
    e("div", { className: "mt-3 border-t border-line pt-2 flex items-center justify-between" },
      e("div", { className: "text-[9px] text-muted" }, "按 Fit 分排序 · 真实 KOL Pool(fit 变动时显 ±Δ)"),
      e("button", { onClick: onViewAll, className: "text-[10px] text-ink-2 hover:text-ink" }, "View All →")
    )
  );
}
