// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { Check, Clock, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { useT } from "../lib/i18n";

const e = React.createElement;

export function AIIntelligenceCard({ insight, onApprove, onLater, onRegenerate, regenerating }: any) {
  const { t } = useT();
  // 2026-06-12 波3 R4:confidence 此前为前端硬编码 72,现仅在后端真实提供置信度时显示。
  const confidence = Number(insight.confidence);
  const confLabel = Number.isFinite(confidence) && confidence > 0 ? `${confidence}% conf · ` : "";
  return e(motion.div, {
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    transition: { delay: 0.18 },
    className: "h-full relative overflow-hidden rounded-xl border border-violet-500/30 bg-gradient-to-br from-violet-500/[0.12] via-indigo-500/[0.06] to-cyan-500/[0.08] p-4 backdrop-blur-xl flex flex-col",
    style: { boxShadow: "0 8px 40px rgba(139,92,246,.18)" },
  },
    e("div", {
      className: "pointer-events-none absolute -right-12 -top-12 h-40 w-40 rounded-full opacity-30 blur-3xl",
      style: { background: "radial-gradient(circle, #8b5cf6, transparent 70%)" }
    }),
    // Header
    e("div", { className: "relative mb-3 flex items-center justify-between" },
      e("div", { className: "flex items-center gap-2" },
        e(Sparkles, { size: 14, className: "text-violet-300" }),
        e("h3", { className: "text-sm font-semibold text-white" }, "AI Today")
      ),
      e("span", { className: "rounded-md bg-violet-500/20 px-2 py-0.5 text-[9px] font-medium text-violet-200" }, `${confLabel}${insight.updatedLabel}`)
    ),
    
    // ─ 今日重点决策 ─
    e("div", { className: "relative mb-3" },
      e("div", { className: "mb-1.5 text-[9px] uppercase tracking-wider text-violet-300" }, "📌 今日重点决策"),
      e("p", { className: "text-[12px] leading-relaxed text-slate-200" }, 
        insight.todayDecision.text.split(insight.todayDecision.amount).reduce((acc: any, part: any, i: any, arr: any) => {
          if (i === arr.length - 1) return [...acc, part];
          return [...acc, part, e("span", { key: i, className: "font-semibold text-amber-300" }, insight.todayDecision.amount)];
        }, [])
      ),
      e("div", { className: "mt-1 text-[10px] text-slate-400" }, t("理由:") + insight.todayDecision.reason),
      e("div", { className: "mt-2 flex gap-1.5" },
        e("button", { 
          onClick: onApprove,
          className: "rounded-md bg-violet-500/30 px-2.5 py-1 text-[10px] font-medium text-white hover:bg-violet-500/40 flex items-center gap-1"
        }, e(Check, { size: 10 }), insight.todayDecision.primaryAction),
        e("button", {
          onClick: onLater || undefined,
          disabled: !onLater,
          title: onLater ? undefined : "待接入真实提醒写入接口",
          className: `rounded-md border border-white/[0.12] px-2.5 py-1 text-[10px] text-slate-300 flex items-center gap-1 ${onLater ? "hover:bg-white/[0.04]" : "opacity-50 cursor-not-allowed"}`
        }, e(Clock, { size: 10 }), insight.todayDecision.secondaryAction)
      )
    ),
    
    // ─ 加强 ─
    e("div", { className: "relative mb-2.5 border-t border-white/[0.08] pt-2.5" },
      e("div", { className: "mb-1.5 flex items-center justify-between" },
        e("div", { className: "text-[9px] uppercase tracking-wider text-emerald-300" }, insight.strengthenLabel || "📈 加强"),
        e("span", { className: "text-[9px] text-slate-500" }, `${insight.strengthen.length} ${t("项")}`)
      ),
      ...insight.strengthen.map((s: any, i: any) => e("div", { key: i, className: "mb-1 flex items-start gap-1.5 text-[11px]" },
        e("span", { className: "text-emerald-400 mt-0.5" }, "·"),
        e("div", { className: "flex-1" },
          e("span", { className: "text-slate-200" }, s.text),
          e("span", { className: "ml-1 text-[10px] text-slate-500" }, s.detail)
        )
      ))
    ),
    
    // ─ 减弱 ─
    e("div", { className: "relative mb-2.5" },
      e("div", { className: "mb-1.5 flex items-center justify-between" },
        e("div", { className: "text-[9px] uppercase tracking-wider text-amber-300" }, "📉 减弱"),
        e("span", { className: "text-[9px] text-slate-500" }, `${insight.weaken.length} 项`)
      ),
      ...insight.weaken.map((s: any, i: any) => e("div", { key: i, className: "mb-1 flex items-start gap-1.5 text-[11px]" },
        e("span", { className: "text-amber-400 mt-0.5" }, "·"),
        e("div", { className: "flex-1" },
          e("span", { className: "text-slate-200" }, s.text),
          e("span", { className: "ml-1 text-[10px] text-slate-500" }, s.detail)
        )
      ))
    ),
    
    // ─ 今天适合发 ─
    e("div", { className: "relative mb-2" },
      e("div", { className: "mb-1.5 text-[9px] uppercase tracking-wider text-cyan-300" }, insight.todayContentLabel || "📅 今天适合发"),
      ...insight.todayContent.map((c: any, i: any) => e("div", { key: i, className: "mb-1 flex items-start gap-1.5 text-[11px]" },
        e("span", { className: "text-cyan-400 mt-0.5" }, "·"),
        e("span", { className: "flex-1 text-slate-200" }, c)
      ))
    ),
    
    // Footer
    e("div", { className: "relative mt-3 flex items-center justify-between border-t border-violet-500/20 pt-2.5" },
      e("span", { className: "text-[9px] text-violet-300/70" }, insight.poweredBy),
      onRegenerate && e("button", { 
        onClick: onRegenerate,
        disabled: regenerating,
        className: "flex items-center gap-1 text-[10px] text-violet-200 hover:text-white disabled:opacity-50"
      }, 
        regenerating ? e(Loader2, { size: 10, className: "animate-spin" }) : e(RefreshCw, { size: 10 }),
        regenerating ? "Regenerating..." : "Regenerate"
      )
    )
  );
}
