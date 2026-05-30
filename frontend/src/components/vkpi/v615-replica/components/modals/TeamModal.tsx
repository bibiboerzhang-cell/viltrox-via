// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { Bell, Plus, Target, TrendingUp, Users, X } from "lucide-react";
import { CenterModal } from "./CenterModal";

const e = React.createElement;

export function TeamModal({ user, staff, onClose, onImpersonate, t, onOpenEditGroup, onOpenNewGroup }) {
  const isAdmin = user.role === "admin";
  return e(CenterModal, { onClose, maxWidth: "xl" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("div", null,
        e("h2", { className: "text-sm font-semibold text-white" }, t("团队管理")),
        e("div", { className: "text-[10px] text-slate-500 mt-0.5" }, isAdmin ? `${staff.length} 人 · 你是 Admin` : "我所在的团队")
      ),
      e("div", { className: "flex items-center gap-2" },
        isAdmin && e("button", {
          onClick: () => { onClose(); onOpenNewGroup && onOpenNewGroup(); },
          className: "flex items-center gap-1 rounded-md border border-purple-500/30 bg-purple-500/[0.08] px-2 py-1 text-[10px] text-purple-200 hover:bg-purple-500/[0.15]"
        },
          e(Plus, { size: 10 }), t("新建分组")),
        e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
      )
    ),
    e("div", { className: "p-4 max-h-[60vh] overflow-y-auto" },
      isAdmin
        ? e("div", { className: "space-y-2" },
            // Group: KOL 团队
            e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
              e("div", { className: "flex items-center justify-between mb-2" },
                e("div", { className: "flex items-center gap-2" },
                  e("span", { className: "text-[11px] font-medium text-white" }, "KOL Operations"),
                  e("span", { className: "text-[9px] text-slate-500" }, `${staff.length} ${t("成员")}`)
                ),
                e("button", { 
                  onClick: () => { onClose(); onOpenEditGroup && onOpenEditGroup(); },
                  className: "text-[10px] text-purple-300 hover:text-purple-200" 
                }, t("编辑分组"))
              ),
              // V6.14.4: 小组合作定位 4 行
              e("div", { className: "rounded-md border border-purple-500/[0.15] bg-purple-500/[0.04] p-2 mb-2 space-y-1" },
                [
                  { icon: Target,     label: t("共享 Projects"),   value: "135mm LAB / CineGear / 56mm 复推" },
                  { icon: Users,      label: t("共享 KOL 池"),     value: t("Top performers(78 人)") },
                  { icon: TrendingUp, label: t("共同 KPI 目标"),   value: t("Q2 新增 50 个高活 KOL") },
                  { icon: Bell,       label: t("内部 @ 提醒规则"), value: t("组内变更自动通知") },
                ].map((row, i) => e("div", { key: i, className: "flex items-start gap-2 text-[10px]" },
                  e(row.icon, { size: 10, className: "text-purple-300 shrink-0 mt-0.5" }),
                  e("span", { className: "text-slate-400 shrink-0" }, row.label + ":"),
                  e("span", { className: "text-slate-300 flex-1 min-w-0 truncate" }, row.value)
                ))
              ),
              e("div", { className: "space-y-1.5" },
                staff.map(s => e("div", {
                  key: s.id,
                  className: "flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-white/[0.04]"
                },
                  e("div", {
                    className: "shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-bold text-white",
                    style: { background: s.color }
                  }, s.avatar),
                  e("div", { className: "flex-1 min-w-0" },
                    e("div", { className: "flex items-center gap-1.5" },
                      e("span", { className: "text-[11px] font-medium text-white" }, s.name),
                      s.role === "admin" && e("span", { className: "text-[8px] uppercase tracking-wider px-1 py-0.5 rounded bg-purple-500/15 text-purple-300" }, "Admin")
                    ),
                    e("div", { className: "text-[10px] text-slate-500" }, `${s.title} · ${s.focus}`)
                  ),
                  s.id !== user.id && e("button", {
                    onClick: () => { onImpersonate(s); onClose(); },
                    className: "rounded-md border border-white/[0.08] px-2 py-1 text-[10px] text-slate-300 hover:bg-white/[0.05]"
                  }, t("切换身份(查看为)"))
                ))
              )
            )
          )
        : e("div", { className: "space-y-3" },
            // Staff 视角:看自己的卡片 + 团队联系人
            e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-4 flex items-center gap-3" },
              e("div", {
                className: "shrink-0 w-12 h-12 rounded-full flex items-center justify-center text-[16px] font-bold text-white",
                style: { background: user.avatarGradient }
              }, user.avatar),
              e("div", { className: "flex-1" },
                e("div", { className: "text-[12px] font-medium text-white" }, user.name),
                e("div", { className: "text-[10px] text-slate-500" }, user.email),
                e("div", { className: "text-[10px] text-slate-400 mt-0.5" }, "直属上级:Kevin Chen")
              )
            ),
            e("div", { className: "text-[10px] text-slate-500" }, "我所在的分组:KOL Operations"),
            e("div", { className: "space-y-1" },
              staff.filter(s => s.id !== user.id).map(s => e("div", {
                key: s.id, className: "flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-white/[0.04]"
              },
                e("div", {
                  className: "shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white",
                  style: { background: s.color }
                }, s.avatar),
                e("div", { className: "flex-1 min-w-0" },
                  e("div", { className: "text-[11px] text-white" }, s.name),
                  e("div", { className: "text-[10px] text-slate-500" }, s.title)
                ),
                e("button", { className: "text-[10px] text-purple-300 hover:text-purple-200" }, t("@ 提及"))
              ))
            )
          )
    )
  );
}
