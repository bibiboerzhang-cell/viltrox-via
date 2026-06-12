// @ts-nocheck
// 2026-06-12 C10(波3 R1):KOL 漏斗卡 — 消费后端 GET /api/admin/vkpi/dashboard → summary.funnel
// shape = { favorites_total, claimed_total, in_project_total, published_total, by_staff[] }
// 后端块未上线前显示空态「漏斗数据待后端」,绝不硬编码假数。

import React from "react";
import { motion } from "framer-motion";
import { Filter } from "lucide-react";

const e = React.createElement;

const STAGE_COLORS = ["#a855f7", "#3b82f6", "#f59e0b", "#10b981"];

function staffLabel(row) {
  return String(row.staff_name || row.name || (row.staff_id != null ? `Staff ${row.staff_id}` : "未知成员"));
}

function staffCount(row) {
  const value = Number(
    row.favorites_total ?? row.favorites ?? row.count ?? row.total ?? NaN,
  );
  return Number.isFinite(value) ? value : null;
}

export function KolFunnelCard({ funnel, onOpenMyKol }) {
  const stages = Array.isArray(funnel?.stages) ? funnel.stages : [];
  const isReal = Boolean(funnel?.isReal);
  const byStaff = Array.isArray(funnel?.byStaff) ? funnel.byStaff : [];
  const maxCount = Math.max(1, ...stages.map((stage) => Number(stage.count) || 0));

  return e(motion.div, {
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    transition: { delay: 0.1 },
    className: "rounded-xl border border-white/[0.08] bg-white/[0.025] p-4 backdrop-blur-xl",
  },
    // Header
    e("div", { className: "mb-3 flex items-center justify-between" },
      e("div", { className: "flex items-center gap-2" },
        e(Filter, { size: 14, className: "text-purple-300" }),
        e("h3", { className: "text-sm font-semibold text-white" }, "KOL 漏斗")
      ),
      isReal
        ? e("span", { className: "rounded-md bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-medium text-emerald-300" }, "真实")
        : e("span", { className: "rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-medium text-amber-300" }, "待后端")
    ),
    // Body
    !isReal
      ? e("div", { className: "rounded-md border border-dashed border-white/[0.08] px-3 py-8 text-center text-[11px] text-slate-500" },
          "漏斗数据待后端",
          e("div", { className: "mt-1 text-[9px] text-slate-600" }, "等待 dashboard summary.funnel 聚合块上线")
        )
      : e("div", { className: "space-y-2" },
          stages.map((stage, index) => {
            const count = Number(stage.count) || 0;
            const width = Math.max(4, Math.min(100, (count / maxCount) * 100));
            const color = STAGE_COLORS[index % STAGE_COLORS.length];
            return e("div", { key: stage.key },
              e("div", { className: "mb-0.5 flex items-center justify-between text-[10px]" },
                e("span", { className: "text-slate-300" }, `${index + 1}.${stage.label}`),
                e("span", { className: "tabular-nums text-slate-400" },
                  stage.count == null ? "--" : count.toLocaleString()
                )
              ),
              e("div", { className: "h-1.5 overflow-hidden rounded-full bg-white/[0.06]" },
                e(motion.div, {
                  initial: { width: 0 },
                  animate: { width: `${width}%` },
                  transition: { delay: 0.2 + index * 0.06, duration: 0.6, ease: [0.16, 1, 0.3, 1] },
                  className: "h-full rounded-full",
                  style: { background: `linear-gradient(90deg, ${color}, ${color}80)` }
                })
              )
            );
          }),
          // by_staff 摘要(前 3 名)
          byStaff.length > 0 && e("div", { className: "mt-2 border-t border-white/[0.06] pt-2 space-y-1" },
            e("div", { className: "text-[9px] uppercase tracking-wider text-slate-500" }, "按成员"),
            byStaff.slice(0, 3).map((row, index) => e("div", {
              key: row.staff_id ?? index,
              className: "flex items-center justify-between text-[10px]"
            },
              e("span", { className: "min-w-0 truncate text-slate-300" }, staffLabel(row)),
              e("span", { className: "shrink-0 tabular-nums text-slate-400" },
                staffCount(row) == null ? "--" : staffCount(row).toLocaleString()
              )
            )),
            byStaff.length > 3 && e("div", { className: "text-[9px] text-slate-500" }, `+${byStaff.length - 3} 人`)
          )
        ),
    // Footer
    e("div", { className: "mt-3 flex items-center justify-between border-t border-white/[0.06] pt-2" },
      e("div", { className: "text-[9px] text-slate-500" }, "Sources: dashboard summary.funnel"),
      onOpenMyKol && e("button", {
        onClick: onOpenMyKol,
        className: "text-[10px] text-slate-300 hover:text-white"
      }, "去 MY KOL →")
    )
  );
}
