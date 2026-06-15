// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { ChevronRight, Languages, LogOut, Menu, Settings, UserCircle, Users } from "lucide-react";
import { PopoverWrapper } from "./PopoverWrapper";

const e = React.createElement;

export function UserMenuPopover({ onClose, theme, onToggleTheme, anchorRef, t, user, staff, lang, onToggleLang, viewingAs, onResetView, onImpersonate, onOpenProfile, onOpenTeam, onOpenSettings, onLogout }) {
  const isAdmin = user.role === "admin";
  const staffList = Array.isArray(staff) ? staff : [];
  const openMenuItem = (handler) => {
    onClose();
    if (handler) handler();
  };
  return e(PopoverWrapper, { onClose, anchorRef, width: 280 },
    e("div", { className: "w-[280px]" },
      // Profile header
      e("div", { className: "px-3 py-3 border-b border-white/[0.06] flex items-center gap-3" },
        e("div", {
          className: "shrink-0 w-10 h-10 rounded-full flex items-center justify-center text-[14px] font-bold text-white",
          style: { background: user.avatarGradient }
        }, user.avatar),
        e("div", { className: "min-w-0 flex-1" },
          e("div", { className: "text-[12px] font-medium text-white" }, user.name),
          e("div", { className: "text-[10px] text-slate-500 flex items-center gap-1 flex-wrap" },
            e("span", { className: "text-[9px] px-1 py-0.5 rounded bg-purple-500/15 text-purple-300" }, isAdmin ? t("Admin") : t("成员")),
            e("span", null, "· " + user.email)
          )
        )
      ),
      // viewing as banner
      viewingAs && e("div", { className: "px-3 py-2 border-b border-white/[0.06] bg-blue-500/[0.06] flex items-center gap-2" },
        e("div", {
          className: "shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white",
          style: { background: viewingAs.color }
        }, viewingAs.avatar),
        e("div", { className: "flex-1 text-[10px] text-blue-200" }, `${t("正在以")} ${viewingAs.name} ${t("的身份查看")}`),
        e("button", {
          onClick: () => { onResetView(); onClose(); },
          className: "text-[9px] text-blue-300 hover:text-white px-1.5 py-0.5 rounded border border-blue-500/30 hover:bg-blue-500/15"
        }, t("返回 Admin 视角"))
      ),
      // Menu items - 3 main
      e("div", { className: "py-1" },
        [
          { icon: UserCircle, label: t("个人资料"), right: null,     onClick: onOpenProfile },
          { icon: Users,       label: t("团队管理"), right: isAdmin ? "4 人" : "我的团队", onClick: onOpenTeam },
          { icon: Settings,    label: t("系统设置"), right: null,     onClick: onOpenSettings },
        ].map((m, i) => e("button", {
          key: i,
          onClick: () => openMenuItem(m.onClick),
          className: "mx-2 flex w-[calc(100%-1rem)] items-center gap-3 rounded-lg bg-white/[0.018] px-3 py-2 text-left text-slate-200 transition-colors hover:bg-white/[0.055]"
        },
          e(m.icon, { size: 13, className: "text-slate-400 shrink-0" }),
          e("span", { className: "flex-1 text-[12px] text-white" }, m.label),
          m.right && e("span", { className: "text-[10px] text-slate-500" }, m.right),
          e(ChevronRight, { size: 11, className: "text-slate-600" })
        ))
      ),
      // Admin only: impersonate(快捷入口,完整列表在团队管理)
      isAdmin && !viewingAs && onImpersonate && e("div", { className: "py-1 border-t border-white/[0.06]" },
        e("div", { className: "px-3 py-1 text-[9px] uppercase tracking-wider text-slate-500" }, t("切换身份(查看为)")),
        staffList.filter(s => String(s.id) !== String(user.id) && !s.isAdmin).slice(0, 3).map(s => e("button", {
          key: s.id,
          onClick: () => { onImpersonate(s); onClose(); },
          className: "mx-2 flex w-[calc(100%-1rem)] items-center gap-2.5 rounded-lg bg-white/[0.018] px-3 py-1.5 text-left transition-colors hover:bg-white/[0.055]"
        },
          e("div", {
            className: "shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-bold text-white",
            style: { background: s.color }
          }, s.avatar),
          e("span", { className: "flex-1 text-[11px] text-slate-300" }, s.name),
          e("span", { className: "text-[9px] text-slate-500" }, s.title)
        ))
      ),
      // Language only (V6.14.4: 切换主题删除 - 无 light theme)
      e("div", { className: "py-1 border-t border-white/[0.06]" },
        e("button", { 
          onClick: onToggleLang,
          className: "mx-2 flex w-[calc(100%-1rem)] items-center gap-3 rounded-lg bg-white/[0.018] px-3 py-2 text-left transition-colors hover:bg-white/[0.055]" 
        },
          e(Languages, { size: 13, className: "text-slate-400 shrink-0" }),
          e("span", { className: "flex-1 text-[12px] text-white" }, t("语言")),
          e("span", { className: "text-[10px] text-slate-300 font-medium" }, lang === "en" ? "English" : "中文")
        )
      ),
      // Logout
      e("div", { className: "py-1 border-t border-white/[0.06]" },
        e("button", { onClick: () => { onClose(); onLogout && onLogout(); }, className: "mx-2 flex w-[calc(100%-1rem)] items-center gap-3 rounded-lg bg-white/[0.018] px-3 py-2 text-left transition-colors hover:bg-rose-500/[0.08]" },
          e(LogOut, { size: 13, className: "text-rose-400 shrink-0" }),
          e("span", { className: "flex-1 text-[12px] text-rose-300" }, t("退出登录"))
        )
      )
    )
  );
}
