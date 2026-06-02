import React, { useState } from "react";
import {
  Activity, ArrowLeft, BarChart3, BookOpen, Calendar, CircleDot, DollarSign,
  Edit3, MapPin, MoreHorizontal, Package, ShieldCheck, Users, X
} from "lucide-react";
import PlaceholderTab from "../components/PlaceholderTab.js";
import { EVENT_STATUS, EVENT_TYPES } from "../shared/constants.js";
import { daysUntil } from "../shared/helpers.js";
import { ownerById } from "../shared/lookups.js";
import BudgetExpensesTab from "../tabs/BudgetExpensesTab.js";
import KolsTab from "../tabs/KolsTab.js";
import MaterialsTab from "../tabs/MaterialsTab.js";
import OverviewTab from "../tabs/OverviewTab.js";
import TasksTab from "../tabs/TasksTab.js";

const e = React.createElement;
export default function EventDetailView({ ev, onBack, currentUser, onEdit, onDelete, onUpdateTeam, stock, setStock }) {
  const [tab, setTab] = useState("overview");
  const [menuOpen, setMenuOpen] = useState(false);
  const typeCfg = EVENT_TYPES[ev.typeKey];
  const statusCfg = EVENT_STATUS[ev.status];
  const Icon = typeCfg.icon;
  const days = daysUntil(ev.startDate);
  const isDone = ev.status === "done";
  
  // 当前用户是否参与该 event
  const isParticipant = ev.teamUserIds.includes(currentUser.id);
  
  const TABS = [
    { k: "overview",  l: "概览",      i: BarChart3 },
    { k: "budget",    l: "预算+费用", i: DollarSign },
    { k: "tasks",     l: "任务",      i: CircleDot },
    { k: "kols",      l: "参与 KOL",  i: Users },
    { k: "materials", l: "物料",      i: Package },
    { k: "onsite",    l: "现场",      i: Activity },
    { k: "retro",     l: "复盘",      i: BookOpen },
  ];
  
  if (!isParticipant) {
    return e("div", { className: "max-w-7xl mx-auto p-5" },
      e("button", { onClick: onBack, className: "text-[11px] text-slate-400 hover:text-white flex items-center gap-1 mb-3" },
        e(ArrowLeft, { size: 11 }), "返回 Events"
      ),
      e("div", { className: "rounded-xl border border-amber-500/20 bg-amber-500/[0.04] p-8 text-center" },
        e(ShieldCheck, { size: 32, className: "text-amber-400 mx-auto mb-3" }),
        e("div", { className: "text-[13px] text-amber-200 font-medium mb-1" }, currentUser.name, " 不在 ", ev.title, " 的团队成员中"),
        e("div", { className: "text-[10.5px] text-slate-400 mb-3" }, "你只能看到自己参与的 Event 详情 (权限隔离)"),
        e("div", { className: "text-[10px] text-slate-500" },
          "团队: ", ev.teamUserIds.map(u => ownerById(u).name).join(", ")
        )
      )
    );
  }
  
  return e("div", { className: "max-w-7xl mx-auto" },
    // 顶部 banner
    e("div", { className: "border-b border-white/[0.06] px-5 py-4" },
      e("button", { onClick: onBack, className: "text-[11px] text-slate-400 hover:text-white flex items-center gap-1 mb-3" },
        e(ArrowLeft, { size: 11 }), "返回 Events"
      ),
      e("div", { className: "flex items-start gap-4" },
        e("div", {
          className: "w-12 h-12 rounded-xl flex items-center justify-center shrink-0",
          style: { background: `linear-gradient(135deg, ${typeCfg.color}30, ${typeCfg.color}10)`, border: `1px solid ${typeCfg.color}40` }
        }, e(Icon, { size: 22, style: { color: typeCfg.color } })),
        e("div", { className: "flex-1" },
          e("div", { className: "flex items-center gap-2 mb-1 flex-wrap" },
            e("h1", { className: "text-[18px] font-bold text-white" }, ev.title),
            e("span", { className: "text-[10px] px-1.5 py-0.5 rounded font-medium", style: { background: statusCfg.color + "20", color: statusCfg.color } }, statusCfg.label),
            !isDone && e("span", {
              className: "text-[10px] px-1.5 py-0.5 rounded font-medium",
              style: { background: days <= 14 ? "rgba(251,191,36,0.20)" : "rgba(168,85,247,0.18)", color: days <= 14 ? "#fbbf24" : "#c4b5fd" }
            }, days > 0 ? `倒计时 ${days} 天` : `进行中 ${-days} 天`)
          ),
          e("div", { className: "flex items-center gap-3 text-[11px] text-slate-400" },
            e("div", { className: "flex items-center gap-1" }, e(MapPin, { size: 11 }), ev.location.city, ", ", ev.location.country),
            e("span", { className: "text-slate-600" }, "·"),
            e("div", { className: "flex items-center gap-1" }, e(Calendar, { size: 11 }), ev.startDate, ev.startDate !== ev.endDate ? " → " + ev.endDate : ""),
            e("span", { className: "text-slate-600" }, "·"),
            e("span", null, "更新于 ", ev.updatedAt)
          )
        ),
        // 团队 avatars + "..." 菜单
        e("div", { className: "flex items-center gap-2" },
          e("div", { className: "flex items-center gap-0.5" },
            ev.teamUserIds.slice(0, 5).map((uid, i) => {
              const u = ownerById(uid);
              return e("div", { key: uid,
                className: "w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white",
                style: { background: u.color, marginLeft: i > 0 ? "-6px" : 0, border: "2px solid #0a0a0d", zIndex: 10 - i }
              }, u.initial);
            })
          ),
          e("div", { className: "relative" },
            e("button", { onClick: () => setMenuOpen(!menuOpen),
              className: "w-8 h-8 rounded-md hover:bg-white/[0.05] border border-white/[0.06] flex items-center justify-center text-slate-400 hover:text-white"
            }, e(MoreHorizontal, { size: 14 })),
            menuOpen && e("div", { className: "absolute right-0 top-full mt-1.5 w-44 rounded-lg border border-white/[0.08] bg-[#0b1220] shadow-2xl z-30 overflow-hidden" },
              e("button", { onClick: () => { setMenuOpen(false); onEdit && onEdit(); },
                className: "w-full px-3 py-2 text-left text-[11px] text-slate-200 hover:bg-white/[0.04] flex items-center gap-2"
              }, e(Edit3, { size: 11, className: "text-purple-300" }), "编辑 Event (预算/日期...)"),
              e("button", { onClick: () => { setMenuOpen(false); onDelete && onDelete(); },
                className: "w-full px-3 py-2 text-left text-[11px] text-red-300 hover:bg-red-500/10 flex items-center gap-2 border-t border-white/[0.04]"
              }, e(X, { size: 11 }), "删除 Event")
            )
          )
        )
      )
    ),
    
    // tabs
    e("div", { className: "border-b border-white/[0.06] px-5 flex items-center gap-1 overflow-x-auto" },
      TABS.map(t => {
        const IT = t.i;
        const active = tab === t.k;
        return e("button", {
          key: t.k,
          onClick: () => setTab(t.k),
          className: `px-3 py-2.5 text-[11.5px] font-medium border-b-2 flex items-center gap-1.5 transition-all whitespace-nowrap ${active ? "text-purple-300 border-purple-500" : "text-slate-400 border-transparent hover:text-white"}`
        }, e(IT, { size: 11 }), t.l);
      })
    ),
    
    // content
    tab === "overview"  && e(OverviewTab, { ev, onUpdateTeam }),
    tab === "budget"    && e(BudgetExpensesTab, { ev, currentUser }),
    tab === "tasks"     && e(TasksTab, { ev, currentUser }),
    tab === "kols"      && e(KolsTab, { ev }),
    tab === "materials" && e(MaterialsTab, { ev, stock }),
    tab === "onsite"    && e(PlaceholderTab, { icon: Activity, title: "现场数据 (Event 进行中才激活)", message: "到场人数 / Lead 收集 / 现场销售 / 媒体到访 / KOL 内容产出 · 真接入时实时同步现场签到 iPad" }),
    tab === "retro"     && e(PlaceholderTab, { icon: BookOpen, title: "复盘 (Event 结束后生成)", message: "投入 vs 产出 · ROI 计算 · 高光 + 痛点 · AI 起草复盘文档" })
  );
}
