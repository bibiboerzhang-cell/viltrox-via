// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { Bell, Check, Edit3, ImageIcon, X } from "lucide-react";
import { useT } from "../../lib/i18n";
import { PLATFORM_ICONS_MAP } from "../../data/platformIconsMap";

const e = React.createElement;

export function PublishPreviewModal({ item, onClose }) {
  const { t } = useT();
  if (!item) return null;
  const platCfg = PLATFORM_ICONS_MAP[item.platform] || PLATFORM_ICONS_MAP.default;
  
  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "v615-modal fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4 overflow-y-auto",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev) => ev.stopPropagation(),
      className: "relative w-full max-w-lg rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden",
    },
      // Header
      e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center gap-3" },
        e("div", {
          className: "shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-[13px] font-bold text-white",
          style: { background: item.color || "#a855f7" }
        }, item.kolAvatar || (item.label || "").split(" · ").pop().charAt(1) || "K"),
        e("div", { className: "flex-1 min-w-0" },
          e("div", { className: "text-[12px] font-medium text-white truncate" }, item.kolName || item.label),
          e("div", { className: "flex items-center gap-2 text-[9px] text-slate-500 mt-0.5" },
            e("span", { className: "flex items-center gap-1" }, e(platCfg.Icon, { size: 10, style: { color: platCfg.color } }), item.platform),
            e("span", null, "·"),
            e("span", null, item.dateStr || (item.dayStr + " " + item.time)),
            e("span", null, "·"),
            e("span", { style: { color: "#fbbf24" } }, t("待审批"))
          )
        ),
        e("button", { onClick: onClose, className: "shrink-0 rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white hover:bg-white/10" }, e(X, { size: 14 }))
      ),
      // Body
      e("div", { className: "p-5 space-y-3" },
        // 关联 Project chip
        item.projectName && e("div", { className: "flex items-center gap-1.5" },
          e("span", { className: "text-[9px] text-slate-500" }, t("关联项目")),
          e("span", { className: "text-[10px] font-medium text-purple-200 px-2 py-0.5 bg-purple-500/[0.15] border border-purple-500/[0.25] rounded" }, item.projectName)
        ),
        // Content preview
        e("div", { className: "grid grid-cols-2 gap-3" },
          e("div", { className: "rounded-md border border-white/[0.08] bg-gradient-to-br from-purple-900/30 to-slate-900 aspect-video flex items-center justify-center" },
            e(ImageIcon, { size: 32, className: "text-slate-600" })
          ),
          e("div", null,
            e("div", { className: "text-[9px] text-slate-500 mb-1" }, "内容预览"),
            e("div", { className: "text-[11px] font-medium text-white leading-relaxed mb-1" }, item.title || item.label),
            e("div", { className: "text-[10px] text-slate-400 leading-relaxed" }, item.description || "12 分钟评测 · 涵盖画质 / 对焦 / 色彩表现")
          )
        ),
        // Stats prediction
        e("div", { className: "grid grid-cols-3 gap-2" },
          [
            { label: "预期曝光", value: "~12M" },
            { label: "预期 ER",  value: "~6.2%" },
            { label: "短链点击", value: "~340" },
          ].map((s, i) => e("div", {
            key: i, className: "rounded-md border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-center"
          },
            e("div", { className: "text-[9px] text-slate-500 mb-0.5" }, s.label),
            e("div", { className: "text-[14px] font-semibold text-white" }, s.value)
          ))
        ),
        // Actions
        e("div", { className: "flex flex-wrap gap-2 pt-2" },
          e("button", { className: "flex items-center gap-1 rounded-md bg-purple-600 hover:bg-purple-500 px-3 py-1.5 text-[11px] font-medium text-white" },
            e(Check, { size: 11 }), t("审批通过")),
          e("button", { className: "flex items-center gap-1 rounded-md border border-white/[0.12] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.04]" },
            e(Edit3, { size: 11 }), "编辑时间"),
          e("button", { className: "flex items-center gap-1 rounded-md border border-white/[0.12] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.04]" },
            e(Bell, { size: 11 }), "提醒 KOL"),
          e("button", { className: "flex items-center gap-1 ml-auto rounded-md border border-red-500/30 px-3 py-1.5 text-[11px] text-red-300 hover:bg-red-500/[0.06]" },
            e(X, { size: 11 }), "取消")
        )
      )
    )
  );
}
