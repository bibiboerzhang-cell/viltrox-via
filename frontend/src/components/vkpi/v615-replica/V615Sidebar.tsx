// @ts-nocheck
// Verbatim from V615ReplicaApp.tsx — conservative leaf extraction (行为不变).
// 纯展示侧边栏:吃 props,不持有任何 state/effect。

import React from "react";
import { Moon, PanelLeftClose, PanelLeftOpen, Sun } from "lucide-react";
import { TaskProgressBoard } from "./components/TaskProgressBoard";
import { NAV_ITEMS } from "./data/navItems";

const e = React.createElement;

export function V615Sidebar({
  collapsed,
  setCollapsed,
  activeNav,
  setActiveNav,
  theme,
  setTheme,
  versionBadge,
  apiToken,
}: any) {
  return e("aside", {
    className: `${collapsed ? "w-[64px]" : "w-[260px]"} sticky top-0 hidden h-screen shrink-0 flex-col justify-between border-r border-white/[0.06] bg-[#050810]/85 backdrop-blur-xl transition-all duration-300 md:flex`
  },
    e("div", null,
      e("div", { className: `flex h-16 items-center ${collapsed ? "justify-center" : "px-5"}` },
        e("div", { className: "text-2xl font-black tracking-[.18em] text-white" }, collapsed ? "V" : "VILTROX")
      ),
      e("nav", { className: `space-y-1 ${collapsed ? "px-2" : "px-3"}` },
        NAV_ITEMS.map(({ icon: Icon, label, badge, key }) => {
          const active = activeNav === key;
          return e("button", {
            key, onClick: () => setActiveNav(key), title: collapsed ? label : undefined,
            className: `group flex w-full items-center ${collapsed ? "justify-center px-2" : "px-3 gap-3"} rounded-lg py-2 text-sm transition ${
              active ? "bg-blue-600/20 text-white" : "text-slate-400 hover:bg-white/[0.04] hover:text-white"
            }`,
            style: active ? { boxShadow: "inset 2px 0 0 #60a5fa" } : {},
          },
            e(Icon, { size: 16, className: active ? "text-blue-300" : "text-slate-500" }),
            !collapsed && e("span", { className: "flex-1 text-left" }, label),
            !collapsed && badge && e("span", {
              className: `rounded px-1.5 py-0.5 text-[10px] ${
                badge === "New" ? "bg-emerald-500/30 text-emerald-200" :
                "bg-slate-600/40 text-slate-300"
              }`
            }, badge)
          );
        })
      )
    ),
    e("div", { className: `flex flex-col gap-2 ${collapsed ? "px-2" : "px-3"} pb-4` },
      !collapsed && e(TaskProgressBoard, { apiToken }),
      e("button", {
        onClick: () => setCollapsed(!collapsed),
        className: `flex items-center ${collapsed ? "justify-center" : "gap-3 px-3"} rounded-lg py-2 text-sm text-slate-400 hover:bg-white/[0.04] hover:text-white`,
      },
        collapsed ? e(PanelLeftOpen, { size: 16 }) : e(PanelLeftClose, { size: 16 }),
        !collapsed && e("span", null, "Collapse")
      ),
      e("button", {
        onClick: () => setTheme(theme === "dark" ? "light" : "dark"),
        className: `flex items-center ${collapsed ? "justify-center" : "gap-3 px-3"} rounded-lg py-2 text-sm text-slate-400 hover:bg-white/[0.04] hover:text-white`,
      },
        theme === "dark" ? e(Moon, { size: 16 }) : e(Sun, { size: 16 }),
        !collapsed && e("span", null, theme === "dark" ? "Dark" : "Light")
      ),
      // 版本徽标:仅展开态显示一行极淡文字,折叠态隐藏以免挤压窄轨
      !collapsed && versionBadge && versionBadge.shortSha && e("div", {
        className: "px-3 pt-1 text-[10px] leading-tight text-slate-600 select-none",
        title: versionBadge.hasClient
          ? (versionBadge.inSync ? "前后端构建一致" : "前端与后端构建不一致")
          : "前端构建标记缺失",
      },
        `${versionBadge.shortSha} · ${versionBadge.inSync ? "✓同步" : "⚠不一致"}`
      )
    )
  );
}
