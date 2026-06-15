// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { AlertTriangle, Image, X } from "lucide-react";
import { useT } from "../../lib/i18n";

const e = React.createElement;

export function SignalDetailModal({ alert, onClose }: any) {
  const { t } = useT();
  if (!alert) return null;
  // 2026-06-15 UI 清理:去模型名 + 空/raw 区块不渲染(信号详情只显有内容的部分,看起来完整专业)。
  const srcsAll = Array.isArray(alert.sources) ? alert.sources : [];
  const srcsWithMentions = srcsAll.filter((s: any) => Number(s.mentions) > 0);
  const realSources = srcsAll.filter((s: any) => (s.url && String(s.url).startsWith("http")) || Number(s.mentions) > 0);
  const impactRows = (Array.isArray(alert.impact) && alert.impact.length)
    ? alert.impact
    : (alert.raw && alert.raw.impact ? [{ level: "▸", text: String(alert.raw.impact) }] : []);
  const actionRows = Array.isArray(alert.actions) ? alert.actions : [];
  const summaryText = alert.summary || alert.desc || "";
  const sevColor: any = {
    high:   "#ef4444",
    medium: "#f59e0b",
    low:    "#10b981",
    info:   "#06b6d4",
  };
  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "v615-modal fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4 overflow-y-auto",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev: any) => ev.stopPropagation(),
      className: "relative w-full max-w-2xl max-h-[90vh] flex flex-col rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden",
    },
      // Header
      e("div", { className: "relative border-b border-white/[0.06] shrink-0 px-5 py-3.5" },
        e("div", {
          className: "absolute inset-0 opacity-25",
          style: { background: `radial-gradient(ellipse at top right, ${sevColor[alert.severity]}66, transparent 70%)` }
        }),
        e("div", { className: "relative flex items-start gap-3" },
          e("div", { 
            className: "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
            style: { background: `${sevColor[alert.severity]}22`, color: sevColor[alert.severity] }
          }, e(AlertTriangle, { size: 18 })),
          e("div", { className: "flex-1 min-w-0" },
            e("h2", { className: "text-base font-semibold text-white mb-1" }, alert.title),
            e("div", { className: "flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-slate-400" },
              e("span", null, `检测时间:${alert.time}`),
              e("span", null, "· "),
              e("span", null, `总提及:${alert.totalMentions}`),
              e("span", null, "· "),
              e("span", { className: "text-emerald-400 font-semibold" }, `Trend ${alert.trendPct}`)
            )
          ),
          e("button", {
            onClick: onClose,
            className: "shrink-0 rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white hover:bg-white/10"
          }, e(X, { size: 14 }))
        )
      ),
      // Body
      e("div", { className: "flex-1 overflow-y-auto p-5 space-y-4" },
        // 1. 缩略图
        alert.thumbnails && alert.thumbnails.length > 0 && e("div", null,
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "相关图片"),
          e("div", { className: "flex gap-2 overflow-x-auto" },
            alert.thumbnails.map((src: any, i: any) => e("div", {
              key: i,
              className: "shrink-0 h-24 w-32 rounded-md bg-white/[0.04] border border-white/[0.06] overflow-hidden flex items-center justify-center",
              style: { backgroundImage: `url(${src})`, backgroundSize: "cover", backgroundPosition: "center" }
            }, !src && e("span", { className: "text-slate-600 text-[10px]" }, "[Image]")))
          )
        ),
        // 2. 主要内容(摘要)
        summaryText && e("div", null,
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "主要内容"),
          e("p", { className: "text-[12px] leading-relaxed text-slate-300" }, summaryText)
        ),
        // 3. 热度分布(仅当有真实 mentions 才显,避免 0/raw 字段名露出)
        srcsWithMentions.length > 0 && e("div", null,
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "热度分布(各源提及)"),
          e("div", { className: "space-y-1.5" },
            srcsWithMentions.map((s: any, i: any) => {
              const maxMentions = Math.max(1, ...srcsWithMentions.map((x: any) => Number(x.mentions) || 0));
              const pct = ((Number(s.mentions) || 0) / maxMentions) * 100;
              return e("div", { key: i, className: "flex items-center gap-2 text-[11px]" },
                e("span", { className: "w-32 text-slate-400 truncate" }, s.name),
                e("div", { className: "flex-1 h-2 rounded-full bg-white/[0.04] overflow-hidden" },
                  e("div", { className: "h-full rounded-full", style: { width: pct + "%", background: sevColor[alert.severity] } })
                ),
                e("span", { className: "w-12 text-right text-slate-300 font-semibold" }, Number(s.mentions) || 0)
              );
            })
          )
        ),
        // 4. 对 Viltrox 的影响(仅当有内容)
        impactRows.length > 0 && e("div", null,
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "对 Viltrox 的影响"),
          e("div", { className: "space-y-1" },
            impactRows.map((imp: any, i: any) => e("div", { key: i, className: "flex items-start gap-2 text-[11px] text-slate-300" },
              e("span", { className: "shrink-0" }, imp.level),
              e("span", null, imp.text)
            ))
          )
        ),
        // 5. 建议行动(仅当有)
        actionRows.length > 0 && e("div", null,
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "建议行动"),
          e("div", { className: "space-y-1.5" },
            actionRows.map((a: any, i: any) => e("div", {
              key: i,
              className: "flex items-start gap-2 rounded-md border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-[11px] text-slate-300"
            },
              e("span", { className: "shrink-0 flex items-center justify-center h-4 w-4 rounded-full bg-purple-500/20 text-[9px] font-bold text-purple-300" }, i + 1),
              e("span", { className: "flex-1" }, a)
            ))
          )
        ),
        // 6. 原始来源(仅当有真实来源链接,raw 字段名不再露)
        realSources.length > 0 && e("div", null,
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "原始来源"),
          e("div", { className: "space-y-1" },
            realSources.map((s: any, i: any) => e("div", { key: i, className: "flex items-center gap-2 text-[11px] text-slate-400" },
              e("span", { className: "text-slate-500" }, "→"),
              e("span", { className: "flex-1" }, s.name),
              e("button", { className: "rounded border border-white/[0.06] px-2 py-0.5 text-[9px] text-white/25", disabled: true, title: "来源链接待接入" }, "查看")
            ))
          )
        )
      ),
      // Footer
      e("div", { className: "shrink-0 border-t border-white/[0.06] px-5 py-2.5 flex items-center justify-between" },
        e("div", { className: "text-[10px] text-slate-500" }, "多源聚合 · 实时更新"),
        e("div", { className: "flex gap-1.5" },
          e("button", {
            onClick: onClose,
            className: "rounded-md border border-white/10 bg-white/[0.03] px-3 py-1 text-[11px] text-slate-300 hover:bg-white/[0.06]"
          }, t("关闭")),
          e("button", { className: "rounded-md px-3 py-1 text-[11px] font-medium text-white/40 bg-white/[0.06]", disabled: true, title: "待接入" }, "执行建议 →")
        )
      )
    )
  );
}
