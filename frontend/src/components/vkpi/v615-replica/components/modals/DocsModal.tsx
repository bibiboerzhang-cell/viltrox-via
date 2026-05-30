// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { ChevronRight, X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";

const e = React.createElement;

export function DocsModal({ onClose }) {
  const { t } = useT();
  const sections = [
    { title: "V-KPI 整体概览",      desc: "what / who / where" },
    { title: "Dashboard 模块",      desc: "6 KPI 卡 / Active Campaigns / 7 天日历 / Top Movers" },
    { title: "Discover 模块",       desc: "KOL 发现 / Recommendation" },
    { title: "KOL Pool 模块",       desc: "1023 KOL 库 · 标签 · 评分" },
    { title: "Projects 模块",       desc: "KOL 推广项目 · 9 步漏斗" },
    { title: "Campaigns 模块",      desc: "广告系列 · 预算 · ROI" },
    { title: "Intelligence 模块",   desc: "AI 信号 · 竞品监控 · BH" },
    { title: "Reports 模块",        desc: "周报 · 月报 · KOL 报告" },
  ];
  return e(CenterModal, { onClose, maxWidth: "lg" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("h2", { className: "text-sm font-semibold text-white" }, "文档 & 指南"),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    e("div", { className: "p-5 max-h-[60vh] overflow-y-auto" },
      e("div", { className: "rounded-lg border border-amber-500/[0.2] bg-amber-500/[0.05] p-3 mb-4 text-[10px] text-amber-200" }, t("占位框架 · 内容后续补")),
      e("div", { className: "space-y-1.5" },
        sections.map((s, i) => e("button", {
          key: i,
          className: "w-full flex items-center gap-3 px-3 py-2 rounded-md hover:bg-white/[0.04] text-left"
        },
          e(BookOpen, { size: 12, className: "text-slate-400 shrink-0" }),
          e("div", { className: "flex-1 min-w-0" },
            e("div", { className: "text-[11px] text-white" }, s.title),
            e("div", { className: "text-[10px] text-slate-500" }, s.desc)
          ),
          e(ChevronRight, { size: 11, className: "text-slate-600" })
        ))
      )
    )
  );
}
