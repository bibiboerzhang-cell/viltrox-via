// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { Activity, Bell, BookOpen, Compass, Database, LayoutDashboard, Sparkles, Star, Target, TrendingUp, Users, Zap } from "lucide-react";

const e = React.createElement;

export function Sidebar({ myListCount = 0 }) {
  const items = [
    { icon: LayoutDashboard, label: "Dashboard" },
    { icon: Sparkles,         label: "Intelligence" },
    { icon: Compass,          label: "Discover", badge: 12 },
    { icon: Users,            label: "KOL Pool",  active: true, badge: 1023 },
    { icon: Star,             label: "我的列表",  sub: true, badge: myListCount > 0 ? myListCount : null, badgeColor: "amber" },
    { icon: Database,         label: "Projects" },
    { icon: Target,           label: "Campaigns" },
    { icon: Activity,         label: "Events", badge: "New" },
    { icon: TrendingUp,       label: "Attribution" },
    { icon: BookOpen,         label: "Reports" },
    { icon: Bell,             label: "Signals", badge: 49, badgeColor: "amber-alert" },
    { icon: Zap,              label: "Agents",  badge: 7 },
  ];
  return e("aside", { className: "w-[180px] shrink-0 border-r border-white/[0.06] bg-[#0a0e1a] flex flex-col" },
    e("div", { className: "px-4 py-4 border-b border-white/[0.06]" },
      e("div", { className: "flex items-center gap-2" },
        e("div", { className: "w-7 h-7 rounded-md bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center text-white font-bold text-xs" }, "V"),
        e("span", { className: "text-[13px] font-semibold text-white tracking-wide" }, "VILTROX")
      )
    ),
    e("nav", { className: "flex-1 py-2 overflow-y-auto" },
      items.map((item, i) => e("button", {
        key: i,
        className: "w-full flex items-center justify-between gap-2 text-left transition-colors " + 
          (item.sub ? "pl-7 pr-3 py-1.5 " : "px-3 py-2 ") +
          (item.active ? "bg-purple-500/[0.12] border-r-2 border-purple-400" : "hover:bg-white/[0.03]")
      },
        e("div", { className: "flex items-center gap-2.5 min-w-0" },
          e(item.icon, { 
            size: item.sub ? 11 : 13, 
            className: item.active ? "text-purple-300" : item.sub ? "text-amber-400/80" : "text-slate-400" 
          }),
          e("span", { 
            className: (item.sub ? "text-[11px] " : "text-[12px] ") + (item.active ? "text-white font-medium" : "text-slate-300") 
          }, item.label)
        ),
        item.badge && e("span", {
          className: "text-[9px] px-1.5 py-0.5 rounded font-medium tabular-nums",
          style: item.badgeColor === "amber"
            ? { background: "rgba(251,191,36,0.15)", color: "#fde68a" }
            : item.badgeColor === "amber-alert"
              ? { background: "rgba(251,146,60,0.18)", color: "#fdba74", border: "1px solid rgba(251,146,60,0.3)" }
              : typeof item.badge === "string" 
                ? { background: "rgba(16,185,129,0.15)", color: "#34d399" }
                : { background: "rgba(255,255,255,0.06)", color: "rgba(148,163,184,0.8)" }
        }, item.badge)
      ))
    )
  );
}
