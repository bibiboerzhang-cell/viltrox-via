// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { Check, ThumbsUp } from "lucide-react";
import { useT } from "../../lib/i18n";

const e = React.createElement;

export function AIDecisionConfirmModal({ insight, onClose, onConfirm }) {
  const { t } = useT();
  if (!insight) return null;
  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "v615-modal fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev) => ev.stopPropagation(),
      className: "relative w-full max-w-md rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden",
    },
      e("div", { className: "px-5 py-4 border-b border-white/[0.06]" },
        e("div", { className: "flex items-center gap-2 mb-2" },
          e(ThumbsUp, { size: 14, className: "text-emerald-400" }),
          e("h2", { className: "text-sm font-semibold text-white" }, t("确认执行决策"))
        ),
        e("p", { className: "text-[11px] text-slate-300 leading-relaxed" }, insight.todayDecision.text)
      ),
      e("div", { className: "p-5 space-y-3" },
        e("div", { className: "text-[10px] text-slate-500" }, t("执行后会:")),
        e("ul", { className: "text-[11px] text-slate-300 space-y-1" },
          e("li", { className: "flex items-start gap-2" }, e(Check, { size: 11, className: "text-emerald-400 mt-0.5 shrink-0" }), t("创建 KOL 合作任务并加入对应 Project")),
          e("li", { className: "flex items-start gap-2" }, e(Check, { size: 11, className: "text-emerald-400 mt-0.5 shrink-0" }), t("通知负责人(Maya / Tom)")),
          e("li", { className: "flex items-start gap-2" }, e(Check, { size: 11, className: "text-emerald-400 mt-0.5 shrink-0" }), t("在 Active Campaigns 中记录决策来源"))
        ),
        e("div", { className: "pt-2 flex justify-end gap-2" },
          e("button", { onClick: onClose, className: "rounded-md border border-white/10 px-4 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.04]" }, "取消"),
          e("button", { onClick: onConfirm, className: "rounded-md bg-purple-600 hover:bg-purple-500 px-4 py-1.5 text-[11px] font-medium text-white" }, t("确认执行"))
        )
      )
    )
  );
}
