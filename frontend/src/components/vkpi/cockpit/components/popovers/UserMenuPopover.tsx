// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { ChevronRight, Languages, LogOut, Menu, Settings, ShieldCheck, UserCircle, Users } from "lucide-react";
import { PopoverWrapper } from "./PopoverWrapper";

const e = React.createElement;

export function UserMenuPopover({ onClose, theme, onToggleTheme, anchorRef, t, user, staff, lang, onToggleLang, viewingAs, onResetView, onImpersonate, onOpenProfile, onOpenTeam, onOpenSettings, onOpenMembersAuth, isOwner, onLogout }: any) {
  // 2026-07-03:role 可能是裸值(admin/owner)也可能是历史显示标签(管理层),都认。
  const roleRaw = String(user.role || "");
  const isAdmin = ["admin", "owner"].includes(roleRaw.toLowerCase()) || roleRaw === "管理层";
  const staffList = Array.isArray(staff) ? staff : [];
  const openMenuItem = (handler: any) => {
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
      // Menu items - 3 main + 授权页 V1:「成员与授权」敏感管理入口收进头像菜单,仅 Owner
      // 渲染(判据 usePermissions().isOwner(),由 CockpitApp 透传;后端 require_tab(system.members) 硬闸)。
      e("div", { className: "py-1" },
        [
          { icon: UserCircle, label: t("个人资料"), right: null,     onClick: onOpenProfile },
          { icon: Users,       label: t("团队管理"), right: isAdmin ? "4 人" : "我的团队", onClick: onOpenTeam },
          { icon: Settings,    label: t("系统设置"), right: null,     onClick: onOpenSettings },
          ...(isOwner && onOpenMembersAuth ? [
            { icon: ShieldCheck, label: t("成员与授权"), right: null, ownerBadge: true, onClick: onOpenMembersAuth },
          ] : []),
        ].map((m: any, i: any) => e("button", {
          key: i,
          onClick: () => openMenuItem(m.onClick),
          className: "mx-2 flex w-[calc(100%-1rem)] items-center gap-3 rounded-lg bg-white/[0.018] px-3 py-2 text-left text-slate-200 transition-colors hover:bg-white/[0.055]"
        },
          e(m.icon, { size: 13, className: "text-slate-400 shrink-0" }),
          e("span", { className: "flex-1 text-[12px] text-white" }, m.label),
          m.ownerBadge && e("span", { className: "text-[8px] font-bold tracking-widest px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300" }, "OWNER"),
          m.right && e("span", { className: "text-[10px] text-slate-500" }, m.right),
          e(ChevronRight, { size: 11, className: "text-slate-600" })
        ))
      ),
      // Admin only: impersonate(快捷入口,完整列表在团队管理)
      isAdmin && !viewingAs && onImpersonate && e("div", { className: "py-1 border-t border-white/[0.06]" },
        e("div", { className: "px-3 py-1 text-[9px] uppercase tracking-wider text-slate-500" }, t("切换身份(查看为)")),
        staffList.filter((s: any) => String(s.id) !== String(user.id) && !s.isAdmin).slice(0, 3).map((s: any) => e("button", {
          key: s.id,
          onClick: () => { onImpersonate(s); onClose(); },
          className: "mx-2 flex w-[calc(100%-1rem)] items-center gap-2.5 rounded-lg bg-white/[0.018] px-3 py-1.5 text-left transition-colors hover:bg-white/[0.055]"
        },
          e("div", { className: "relative shrink-0" },
            e("div", {
              className: "w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-bold text-white",
              style: { background: s.color }
            }, s.avatar),
            (s.online || s.user_online) ? e("span", {
              className: "absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full bg-emerald-400",
              style: { boxShadow: "0 0 0 1.5px #0a1020" },
              title: "在线"
            }) : null
          ),
          e("span", { className: "flex-1 text-[11px] text-slate-300" }, s.name),
          (s.online || s.user_online) ? e("span", { className: "text-[8.5px] text-emerald-400" }, "在线") : e("span", { className: "text-[9px] text-slate-500" }, s.title)
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
