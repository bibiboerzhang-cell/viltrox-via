// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { X } from "lucide-react";

const e = React.createElement;

export function PinDetailModal({ pin, mode, onClose }: any) {
  if (!pin) return null;
  // 2026-06-12 死按钮诚实化:无后端/路由的 CTA 一律 disabled+待接入;Open in maps 接真 Google Maps 查询
  const pendingBtnFull = "w-full rounded-lg border border-white/[0.1] bg-white/[0.03] py-2 text-sm text-slate-500 opacity-60 cursor-not-allowed";
  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "cockpit-modal fixed inset-0 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.9, opacity: 0 }, animate: { scale: 1, opacity: 1 }, exit: { scale: 0.9, opacity: 0 },
      onClick: (ev) => ev.stopPropagation(),
      className: "relative w-full max-w-md rounded-2xl border border-white/10 bg-[#0b1220] p-6 shadow-2xl",
    },
      e("button", {
        onClick: onClose,
        className: "absolute right-4 top-4 rounded-lg border border-white/10 bg-white/5 p-2 text-slate-400 hover:text-white",
      }, e(X, { size: 16 })),
      
      e("div", { className: "mb-4 flex items-center gap-3" },
        e("span", { className: "rounded-lg p-2", style: { background: `${mode.color}22`, color: mode.color } },
          e(mode.icon, { size: 20 })
        ),
        e("div", { className: "min-w-0 flex-1" },
          e("h3", { className: "text-xl font-semibold text-white truncate" }, pin.handle || pin.name || pin.label),
          pin.niche && e("p", { className: "text-xs text-slate-400" }, pin.niche),
          pin.type && !pin.parentItem && e("p", { className: "text-xs text-slate-400" }, pin.type),
          // Venue 上下文(显示 parent)
          pin.parentItem && e("p", { className: "text-xs text-slate-400" }, `Visited by ${pin.parentItem}`)
        )
      ),
      
      // KOL 个人详情
      pin.handle && [
        e("div", { key: "kk1", className: "mb-4 grid grid-cols-2 gap-3" },
          e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "Engagement"),
            e("div", { className: "mt-1 text-xl font-light text-white tabular-nums" }, pin.engagement || "—")
          ),
          e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "Followers"),
            e("div", { className: "mt-1 text-xl font-light text-white tabular-nums" }, pin.followers || "—")
          )
        ),
        // 如果该 KOL 有 venues,展示
        pin.venues && pin.venues.length > 0 && e("div", { key: "kk-venues", className: "mb-4" },
          e("div", { className: "mb-2 text-[10px] uppercase tracking-wider text-slate-500" }, "Frequent locations"),
          pin.venues.map((v: any) => e("div", {
            key: v.name,
            className: "mb-2 flex items-center justify-between rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5"
          },
            e("div", { className: "min-w-0" },
              e("div", { className: "text-xs text-white truncate" }, v.name),
              e("div", { className: "text-[10px] text-slate-500 truncate" }, v.note || v.type)
            ),
            e("span", { className: "shrink-0 rounded px-2 py-0.5 text-[10px]", style: { background: `${mode.color}22`, color: mode.color } }, v.type)
          ))
        ),
        e("button", { key: "kk2", disabled: true, title: "待接入",
          className: pendingBtnFull
        }, "View full profile →")
      ],

      // 国家 / 城市聚合详情
      !pin.handle && Number.isFinite(Number(pin.count)) && [
        e("div", { key: "cc1", className: "mb-4 grid grid-cols-2 gap-3" },
          e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "KOL Count"),
            e("div", { className: "mt-1 text-xl font-light text-white tabular-nums" }, Number(pin.count).toLocaleString())
          ),
          e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "Level"),
            e("div", { className: "mt-1 text-sm text-white" }, pin.country ? "City cluster" : "Country")
          )
        ),
        e("div", { key: "cc2", className: "rounded-lg border border-violet-500/20 bg-violet-500/[0.06] p-3 text-xs text-slate-300" },
          pin.country ? "点击城市后显示该城市簇内 Top KOL 分布。" : "点击国家后显示城市簇拆分。"
        )
      ],

      // Venue 详情(点击具体 venue pin 时)
      pin.parentItem && !pin.handle && [
        e("div", { key: "vv1", className: "mb-4 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "Note"),
          e("div", { className: "mt-1 text-sm text-white" }, pin.note || "—")
        ),
        e("button", { key: "vv2",
          onClick: () => window.open(
            "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(pin.name || pin.label || pin.parentItem || ""),
            "_blank", "noopener,noreferrer"
          ),
          title: "在 Google Maps 中打开",
          className: "w-full rounded-lg border border-white/[0.1] bg-white/[0.04] py-2 text-sm text-slate-300 hover:bg-white/[0.08]"
        }, "Open in maps →")
      ],
      
      // Store 详情
      pin.revenue && pin.type && [
        e("div", { key: "ss1", className: "mb-4 grid grid-cols-2 gap-3" },
          e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "30d Revenue"),
            e("div", { className: "mt-1 text-xl font-light text-emerald-400 tabular-nums" }, pin.revenue)
          ),
          e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "Type"),
            e("div", { className: "mt-1 text-sm text-white" }, pin.type)
          )
        ),
        // DL1(2026-07-02):删掉「View dealer details 待接入」死按钮,改为直接展示 pin 自带字段
        // (名称/地址/城市/州省/国家,有什么显什么;字段来自 /dealers/locations pin 或层级 raw)。
        (() => {
          const raw = (pin.raw && typeof pin.raw === "object") ? pin.raw : {};
          const fields = [
            ["名称", pin.name || pin.label || raw.name],
            ["地址", pin.address || raw.address],
            ["城市", pin.city || raw.city],
            ["州/省", pin.state || raw.state],
            ["国家", pin.country || raw.country],
          ].filter(([, v]) => v);
          return fields.length > 0 && e("div", { key: "ss2", className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 space-y-1.5" },
            e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "Dealer info"),
            fields.map(([label, value]: any) => e("div", { key: label, className: "flex items-start justify-between gap-3 text-xs" },
              e("span", { className: "shrink-0 text-slate-500" }, label),
              e("span", { className: "min-w-0 text-right text-white break-words" }, String(value))
            ))
          );
        })()
      ]
    )
  );
}
