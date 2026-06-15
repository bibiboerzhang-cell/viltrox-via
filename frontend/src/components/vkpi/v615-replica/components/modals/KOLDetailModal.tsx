// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { ImageIcon, X } from "lucide-react";
import { useT } from "../../lib/i18n";

const e = React.createElement;

export function KOLDetailModal({ mover, onClose, onOpenKolPool }: { mover?: any; onClose?: () => void; onOpenKolPool?: (mover: any) => void }) {
  const { t } = useT();
  if (!mover) return null;
  // 2026-06-12 死按钮诚实化:无写接口的 CTA 一律 disabled+待接入,不再渲染假 hover
  const pendingBtn = "rounded-md border border-white/[0.12] px-3 py-1.5 text-[11px] text-slate-500 opacity-60 cursor-not-allowed";
  const openProfile = onOpenKolPool ? () => { onClose && onClose(); onOpenKolPool(mover); } : undefined;
  // 假数据(实际从 mover.handle 查 KOL pool 数据)
  const trendData = {
    follower: [0,2,3,5,8,11,12.4].map(v => ({ value: v })),
    reach:    [0,5,12,18,24,32,38.4].map(v => ({ value: v })),
    er:       [5.2,5.4,5.6,5.8,6.0,6.2,6.24].map(v => ({ value: v })),
  };
  
  const recentPosts = [
    { title: "135mm LAB Review", reach: "28.1M", er: "7.1%" },
    { title: "Cine vs AF",       reach: "7.2M",  er: "5.4%" },
    { title: "BTS Field Shoot",  reach: "3.1M",  er: "4.8%" },
  ];
  
  const activeProjects = mover.type === "kol" ? ["135mm LAB", "85mm vs GM"] : ["所有 LAB 系列产品"];
  
  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "v615-modal fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4 overflow-y-auto",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev: any) => ev.stopPropagation(),
      className: "relative w-full max-w-2xl rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden",
    },
      // Header
      e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center gap-3" },
        e("div", {
          className: "shrink-0 w-11 h-11 rounded-full flex items-center justify-center text-[16px] font-bold text-white",
          style: { background: `linear-gradient(135deg, ${mover.badgeColor}cc, ${mover.badgeColor}88)` }
        }, (mover.handle || "K").replace("@", "").charAt(0).toUpperCase()),
        e("div", { className: "flex-1 min-w-0" },
          e("div", { className: "flex items-center gap-2 mb-0.5" },
            e("h2", { className: "text-sm font-semibold text-white" }, mover.handle),
            mover.type === "matrix" && e("span", { className: "text-[9px] uppercase tracking-wider bg-purple-500/[0.15] text-purple-300 px-1.5 py-0.5 rounded" }, "矩阵"),
            e("span", {
              className: "text-[9px] font-semibold px-2 py-0.5 rounded ml-1",
              style: { background: `${mover.badgeColor}22`, color: mover.badgeColor }
            }, mover.badge + " " + (mover.badge === "★" ? "Top performer" : mover.badge === "↑" ? "Rising" : mover.badge === "✓" ? "Active" : "At risk"))
          ),
          e("div", { className: "text-[10px] text-slate-500" },
            mover.type === "kol" ? "YouTube · 1.42M followers · 长期合作 · 自 2024/03" : "公司矩阵 · 主账号 · IG · 自 2022/06"
          )
        ),
        e("button", { onClick: onClose, className: "shrink-0 rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white hover:bg-white/10" }, e(X, { size: 14 }))
      ),
      // Body
      e("div", { className: "p-5 space-y-3" },
        // 3 trend sparklines
        e("div", { className: "grid grid-cols-3 gap-2" },
          [
            { label: "7 天粉丝", value: mover.deltaFollower, color: "#10b981", data: trendData.follower },
            { label: "7 天曝光", value: mover.deltaReach,    color: "#06b6d4", data: trendData.reach },
            { label: "7 天 ER",  value: "6.24%",              color: "#fbbf24", data: trendData.er },
          ].map((m, i) => e("div", { key: i, className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-2.5" },
            e("div", { className: "text-[9px] text-slate-500" }, m.label),
            e("div", { className: "text-[14px] font-semibold mb-1", style: { color: m.value.startsWith("-") ? "#ef4444" : m.color } }, m.value),
            // mini sparkline
            e("svg", { viewBox: "0 0 80 24", style: { width: "100%", height: "20px" } },
              (() => {
                const vals = m.data.map(d => (typeof d === "object" ? d.value : d));
                const max = Math.max(...vals, 1);
                const min = Math.min(...vals);
                const range = max - min || 1;
                const pts = vals.map((v, idx) => {
                  const x = (idx / (vals.length - 1)) * 78 + 1;
                  const y = 22 - ((v - min) / range) * 20;
                  return x.toFixed(1) + "," + y.toFixed(1);
                }).join(" ");
                return e("polyline", { points: pts, fill: "none", stroke: m.color, strokeWidth: 1.5, strokeLinecap: "round" });
              })()
            )
          ))
        ),
        // Recent posts
        e("div", null,
          e("div", { className: "text-[10px] text-slate-500 mb-2" }, t("最近 3 条发布")),
          e("div", { className: "grid grid-cols-3 gap-2" },
            recentPosts.map((p, i) => e("div", { key: i, className: "rounded-md border border-white/[0.06] bg-white/[0.02] overflow-hidden cursor-pointer hover:bg-white/[0.03]" },
              e("div", { className: "aspect-video bg-gradient-to-br from-purple-900/30 to-slate-900 flex items-center justify-center" },
                e(ImageIcon, { size: 20, className: "text-slate-600" })
              ),
              e("div", { className: "p-2" },
                e("div", { className: "text-[10px] font-medium text-white truncate" }, p.title),
                e("div", { className: "text-[9px] text-slate-500 mt-0.5" }, p.reach + " · ER " + p.er)
              )
            ))
          )
        ),
        // Active projects
        e("div", { className: "flex items-center gap-2 flex-wrap" },
          e("span", { className: "text-[10px] text-slate-500" }, "参与中:"),
          activeProjects.map((p, i) => e("span", {
            key: i,
            className: "text-[10px] font-medium text-purple-200 px-2 py-0.5 bg-purple-500/[0.12] border border-purple-500/[0.25] rounded"
          }, p))
        ),
        // Actions(2026-06-12 死按钮清查:加大投入/续约/评估/退出合作 无后端写接口 → disabled;
        // 查看完整档案 → 接真跳 KOL Pool)
        e("div", { className: "pt-2 flex flex-wrap gap-2 border-t border-white/[0.06]" },
          mover.badge !== "⚠️"
            ? [
                e("button", { key: 0, disabled: true, title: "待接入", className: pendingBtn }, t("加大投入")),
                e("button", { key: 1, disabled: true, title: "待接入", className: pendingBtn }, "续约"),
                e("button", {
                  key: 2,
                  onClick: openProfile,
                  disabled: !openProfile,
                  title: openProfile ? "在 KOL Pool 查看完整档案" : "待接入",
                  className: openProfile ? "rounded-md border border-white/[0.12] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.04]" : pendingBtn
                }, "查看完整档案 →"),
              ]
            : [
                e("button", { key: 0, disabled: true, title: "待接入", className: pendingBtn }, t("评估")),
                e("button", { key: 1, disabled: true, title: "待接入", className: "rounded-md border border-red-500/20 px-3 py-1.5 text-[11px] text-red-300/50 opacity-60 cursor-not-allowed" }, "退出合作"),
                e("button", {
                  key: 2,
                  onClick: openProfile,
                  disabled: !openProfile,
                  title: openProfile ? "在 KOL Pool 查看完整档案" : "待接入",
                  className: openProfile ? "rounded-md border border-white/[0.12] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.04]" : pendingBtn
                }, "查看完整档案 →"),
              ]
        )
      )
    )
  );
}
