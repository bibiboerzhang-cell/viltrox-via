// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { Bell, ChevronDown, MessageCircle, RefreshCw } from "lucide-react";
import { KPAvatar } from "./components/KPAvatar";

const e = React.createElement;

export function TopBar() {
  const [showSyncDetail, setShowSyncDetail] = useState(false);
  return e("header", { className: "h-14 border-b border-white/[0.06] bg-[#0a0e1a]/70 backdrop-blur-xl flex items-center px-5 gap-4 relative" },
    e("div", { className: "flex-1 flex items-center gap-2" },
      e("h1", { className: "text-[14px] font-medium text-white" }, "KOL Pool"),
      e("span", { className: "text-[10px] text-slate-500" }, "/ V6 算法:Real ER + 海外 Geo + Loyalty + Trend + 设备分析")
    ),
    // 数据更新时间戳
    e("div", { className: "relative" },
      e("button", {
        onClick: () => setShowSyncDetail(!showSyncDetail),
        className: "flex items-center gap-1.5 rounded-md border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5 text-[10px] text-slate-300 hover:bg-white/[0.04] hover:border-white/[0.1] transition-colors"
      },
        e("span", { className: "h-1.5 w-1.5 rounded-full bg-emerald-400", style: { boxShadow: "0 0 6px rgba(52,211,153,0.6)" } }),
        e("span", { className: "text-slate-400" }, "数据更新"),
        e("span", { className: "text-white tabular-nums" }, "2 分钟前"),
        e(ChevronDown, { size: 9, className: "text-slate-500" })
      ),
      showSyncDetail && e("div", {
        className: "absolute top-full right-0 mt-1.5 w-[260px] rounded-lg border border-white/[0.08] bg-[#0a1020] shadow-2xl z-50 py-2"
      },
        e("div", { className: "px-3 py-1.5 text-[9px] uppercase tracking-wider text-slate-500 border-b border-white/[0.04]" }, "各数据源同步状态"),
        [
          { src: "YouTube 抓取",     time: "2 分钟前",  status: "ok" },
          { src: "Instagram 抓取",   time: "12 分钟前", status: "ok" },
          { src: "TikTok 抓取",      time: "28 分钟前", status: "ok" },
          { src: "Real ER 校准",     time: "1 小时前",  status: "warn" },
          { src: "V6 Fit 重算",      time: "2 小时前",  status: "warn" },
          { src: "Geo / 地理推断",   time: "今早",      status: "warn" },
        ].map((s, i) => e("div", { key: i, className: "px-3 py-1.5 flex items-center gap-2 text-[11px]" },
          e("span", { className: "h-1 w-1 rounded-full", style: { background: s.status === "ok" ? "#34d399" : "#fbbf24" } }),
          e("span", { className: "text-slate-300 flex-1" }, s.src),
          e("span", { className: "text-slate-500 tabular-nums text-[10px]" }, s.time),
        )),
        e("div", { className: "px-3 pt-2 mt-1 border-t border-white/[0.04]" },
          e("button", { className: "w-full text-left text-[10px] text-purple-300 hover:text-purple-200 flex items-center gap-1" },
            e(RefreshCw, { size: 9 }), "立即重新同步全部"
          )
        )
      )
    ),
    e("button", { className: "rounded-md p-2 text-slate-400 hover:bg-white/[0.04] hover:text-white" }, e(MessageCircle, { size: 14 })),
    e("button", { className: "relative rounded-md p-2 text-slate-400 hover:bg-white/[0.04] hover:text-white" },
      e(Bell, { size: 14 }),
      e("span", { className: "absolute top-1 right-1 h-1.5 w-1.5 rounded-full bg-rose-500" })
    ),
    e("div", { className: "flex items-center gap-2 border-l border-white/[0.06] pl-3" },
      e(KPAvatar, { name: "Kevin", color: "linear-gradient(135deg, #f59e0b, #ec4899)", size: 28 }),
      e("div", { className: "text-[10px]" },
        e("div", { className: "text-white" }, "Kevin Chen"),
        e("div", { className: "text-slate-500" }, "Admin")
      ),
      e(ChevronDown, { size: 12, className: "text-slate-500" })
    )
  );
}
