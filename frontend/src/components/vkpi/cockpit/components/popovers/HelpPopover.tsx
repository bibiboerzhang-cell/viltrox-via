// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { BookOpen, Bug, ChevronRight, Keyboard, MessageCircle } from "lucide-react";
import { PopoverWrapper } from "./PopoverWrapper";

const e = React.createElement;

export function HelpPopover({ onClose, anchorRef, t, onOpenDocs, onOpenShortcuts, onOpenFeedback }: any) {
  const items = [
    { icon: BookOpen,    title: t("文档 & 指南"),       desc: t("KOL 找人到项目操作说明"), badge: "PDF", disabled: !onOpenDocs, onClick: onOpenDocs },
    { icon: Keyboard,    title: t("键盘快捷键"),        desc: "⌘ K / ⌘ ? / ⌘ /",       badge: null,    onClick: onOpenShortcuts },
    { icon: Bug,         title: t("提交反馈 / 报 bug"), desc: t("发送到管理通知列表"), badge: t("已接入"), onClick: onOpenFeedback },
  ];
  return e(PopoverWrapper, { onClose, anchorRef, width: 320 },
    e("div", { className: "w-[300px]" },
      e("div", { className: "px-3 py-2 border-b border-white/[0.06]" },
        e("div", { className: "text-[11px] font-semibold text-white" }, t("帮助 & 反馈"))
      ),
      e("div", { className: "py-1" },
        items.map((item: any, i: any) => e("button", {
          key: i,
          disabled: item.disabled,
          onClick: () => {
            if (item.disabled) return;
            onClose();
            item.onClick && item.onClick();
          },
          className: `w-full flex items-center gap-3 px-3 py-2 text-left transition-colors ${item.disabled ? "cursor-not-allowed opacity-55" : "hover:bg-white/[0.04]"}`
        },
          e("div", { className: "shrink-0 w-7 h-7 rounded-md flex items-center justify-center bg-white/[0.05]" },
            e(item.icon, { size: 13, className: "text-slate-400" })
          ),
          e("div", { className: "flex-1 min-w-0" },
            e("div", { className: "flex items-center gap-1.5" },
              e("span", { className: "text-[12px] text-white" }, item.title),
              item.badge && e("span", { className: "text-[8px] uppercase tracking-wider px-1 py-0.5 rounded bg-amber-500/15 text-amber-300" }, item.badge)
            ),
            e("div", { className: "text-[10px] text-slate-500" }, item.desc)
          ),
          !item.disabled && e(ChevronRight, { size: 12, className: "text-slate-600" })
        ))
      ),
      // 飞书找波
      e("div", { className: "border-t border-white/[0.06] p-3 bg-gradient-to-br from-blue-500/[0.05] to-purple-500/[0.05]" },
        e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, t("技术支持")),
        e("div", { className: "flex items-start gap-2.5" },
          e("div", { 
            className: "shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-[12px] font-bold text-white",
            style: { background: "linear-gradient(135deg, #f59e0b, #ec4899)" }
          }, "波"),
          e("div", { className: "flex-1 min-w-0" },
            e("div", { className: "flex items-center gap-1.5 mb-0.5" },
              e("span", { className: "text-[11px] font-medium text-white" }, "张建波 (BOBOBOBO)"),
              e("span", { className: "text-[9px] px-1 py-0.5 rounded bg-emerald-500/15 text-emerald-300" }, t("已认证"))
            ),
            e("div", { className: "text-[10px] text-slate-400 mb-1" }, t("Viltrox 唯卓仕 · 北美组")),
            e("div", { className: "flex items-center gap-2 text-[10px]" },
              e("a", { 
                className: "flex items-center gap-1 text-slate-400"
              }, e(MessageCircle, { size: 10 }), t("飞书")),
              e("span", { className: "text-slate-600" }, "·"),
              e("span", { className: "text-slate-400" }, "+1-8582269427")
            )
          )
        )
      ),
      e("div", { className: "px-3 py-2 border-t border-white/[0.06] flex items-center justify-between" },
        e("span", { className: "text-[9px] text-slate-500" }, "V-KPI v6.14.2"),
        e("span", { className: "text-[9px] text-slate-500" }, `${t("更新")} 2026/05/25`)
      )
    )
  );
}
