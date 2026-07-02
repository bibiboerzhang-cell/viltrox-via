// Verbatim from CockpitApp.tsx — conservative leaf extraction (行为不变).
// 纯展示顶栏:吃 props,不持有任何 state/effect。
// 例外:全局搜索框接真(2026-07 P0)——回车把关键词写 localStorage 并派发
// vkpi:open-kol-pool-search 事件(vkpi:open-kol-pool-item 同款管道):
// CockpitApp 监听切到 KOL Pool,KOLPoolPage 挂载/事件时消费并填入本地筛选。

import React from "react";
import { Bell, ChevronDown, FileText, HelpCircle, MessageCircle, Search } from "lucide-react";
import { Avatar } from "./components/Avatar";
import { NAV_ITEMS } from "./data/navItems";

const e = React.createElement;

export function CockpitTopbar({
  activeNav,
  helpBtnRef,
  setShowHelp,
  messagesBtnRef,
  setShowMessages,
  activeReminders,
  setReportOpen,
  notifsBtnRef,
  setShowNotifs,
  runtimeNotifications,
  userMenuBtnRef,
  setShowUserMenu,
  viewingAs,
  currentUser,
  t,
}: any) {
  return e("header", { className: "sticky top-0 z-40 flex h-16 items-center justify-between border-b border-white/[0.06] bg-[#050810]/80 px-4 backdrop-blur-xl md:px-6" },
    e("div", { className: "text-sm font-semibold tracking-wide text-white" }, (NAV_ITEMS.find(n => n.key === activeNav)?.label) || "Dashboard"),
    e("div", { className: "mx-4 hidden max-w-md flex-1 md:block" },
      e("div", { className: "relative" },
        e(Search, { size: 14, className: "absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" }),
        e("input", {
          placeholder: "搜索 KOL Pool(回车跳转)...",
          title: "输入关键词回车 → 跳到 KOL Pool 并按关键词筛选",
          className: "w-full rounded-lg border border-white/[0.08] bg-white/[0.03] py-1.5 pl-9 pr-3 text-xs text-white placeholder-slate-500 focus:border-blue-500/40 focus:outline-none",
          onKeyDown: (ev: any) => {
            if (ev.key !== "Enter") return;
            const q = String(ev.currentTarget.value || "").trim();
            if (!q) return;
            try { window.localStorage.setItem("vkpi:pending-kolpool-search", q); } catch { /* localStorage 不可用忽略 */ }
            window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-search"));
            ev.currentTarget.value = "";
            ev.currentTarget.blur();
          },
        })
      )
    ),
    e("div", { className: "flex items-center gap-2 md:gap-3" },
      // V6.14: Help & Messages 按钮(加 onClick + ref)
      e("button", {
        ref: helpBtnRef,
        onClick: () => setShowHelp(true),
        "aria-label": "Help",
        className: "hidden rounded-lg p-2 text-slate-400 hover:bg-white/[0.04] hover:text-white md:block"
      }, e(HelpCircle, { size: 16 })),
      e("button", {
        ref: messagesBtnRef,
        onClick: () => setShowMessages(true),
        "aria-label": "Work Reminders",
        className: "hidden relative rounded-lg p-2 text-slate-400 hover:bg-white/[0.04] hover:text-white md:block"
      },
        e(MessageCircle, { size: 16 }),
        activeReminders.filter((r: any) => r.status === "todo").length > 0 && e("span", { className: "absolute right-1 top-1 h-2 w-2 rounded-full bg-rose-500" })
      ),
      // V6.10: Generate Report 按钮
      e("button", {
        onClick: () => setReportOpen(true),
        "aria-label": "Generate Report",
        className: "hidden md:flex items-center gap-1.5 rounded-lg border border-purple-500/30 bg-purple-500/[0.08] px-2.5 py-1.5 text-xs text-purple-200 hover:bg-purple-500/[0.15] hover:border-purple-500/50"
      },
        e(FileText, { size: 13 }),
        e("span", null, "Report")
      ),
      // V6.14: Notifications(加 onClick + ref)
      e("button", {
        ref: notifsBtnRef,
        onClick: () => setShowNotifs(true),
        "aria-label": "Notifications",
        className: "relative rounded-lg p-2 text-slate-400 hover:bg-white/[0.04] hover:text-white"
      },
        e(Bell, { size: 16 }),
        runtimeNotifications.filter((n: any) => n.unread).length > 0 && e("span", { className: "absolute right-1 top-1 h-2 w-2 rounded-full bg-rose-500" })
      ),
      // V6.14: User Menu(整个 wrap 加 onClick + ref)
      e("button", {
        ref: userMenuBtnRef,
        onClick: () => setShowUserMenu(true),
        "aria-label": "User Menu",
        className: "flex items-center gap-2 border-l border-white/[0.06] pl-3 rounded-r-lg hover:bg-white/[0.02] py-1 pr-2"
      },
        e(Avatar, {
  src: viewingAs ? null : currentUser.avatarUrl,
  alt: currentUser.name, size: 32,
  fallback: viewingAs ? viewingAs.avatar : currentUser.avatar,
  gradient: viewingAs ? viewingAs.color : currentUser.avatarGradient
        }),
        e("div", { className: "hidden text-xs sm:block text-left" },
          e("div", { className: "text-white flex items-center gap-1" },
            viewingAs ? viewingAs.name : currentUser.name,
            viewingAs && e("span", { className: "text-[8px] px-1 py-0.5 rounded bg-blue-500/20 text-blue-300" }, t("正在以"))
          ),
          e("div", { className: "text-slate-500" },
            viewingAs ? viewingAs.title : (currentUser.role === "admin" ? t("Admin") : t("成员"))
          )
        ),
        e(ChevronDown, { size: 14, className: "hidden text-slate-500 sm:block" })
      )
    )
  );
}
