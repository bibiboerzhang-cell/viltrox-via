import React, { useState } from "react";
import {
  AlertCircle, Briefcase, CircleDot, DollarSign, ExternalLink, MapPin, Plus,
  ShieldCheck, Users, X
} from "lucide-react";
import { daysUntil, fmtMoneyShort, healthColor, sum } from "../shared/helpers";
import { memberFromStaff, projectById } from "../shared/lookups";
import type { EventVm, InvitedKolVm, TaskVm, UiStaff } from "../shared/types";

const e = React.createElement;

interface OverviewTabProps {
  ev: EventVm;
  staff?: UiStaff[];
  tasks?: TaskVm[];
  onUpdateTeam: (teamIds: string[]) => void;
}

// tasks 由 EventDetailView 从真后端拉取后作 prop 传入(不在此独立再拉一次),
// 概览的「任务进度 / 近期 ddl」全部基于真数据;无任务时安全降级为空。
export default function OverviewTab({ ev, staff = [], tasks = [], onUpdateTeam }: OverviewTabProps) {
  const [addingTeam, setAddingTeam] = useState(false);
  // 加成员候选 = 真实员工名单里还不在团队里的人(mock TEAM 退役,mock id 不再落库)。
  const teamCandidates = (Array.isArray(staff) ? staff : []).filter(u => !ev.teamUserIds.includes(String(u.id)));
  const totalSpent = sum(ev.budgetByCategory, "spent");
  const days = daysUntil(ev.startDate);
  const isDone = ev.status === "done";
  const confirmedKols = ev.invitedKols.filter((k: InvitedKolVm) => k.status === "confirmed").length;
  const doneTasks = tasks.filter(t => t.done).length;

  return e("div", { className: "p-5 space-y-4" },
    // 倒计时大数字
    !isDone && e("div", { className: "rounded-xl p-5 border", style: { background: days <= 14 ? "linear-gradient(135deg, rgba(251,191,36,0.10), rgba(251,191,36,0.02))" : "linear-gradient(135deg, rgba(168,85,247,0.10), rgba(168,85,247,0.02))", borderColor: days <= 14 ? "rgba(251,191,36,0.20)" : "rgba(168,85,247,0.18)" } },
      e("div", { className: "flex items-center justify-between" },
        e("div", null,
          e("div", { className: "text-[10.5px] text-slate-400 mb-1" }, days <= 14 ? "紧急 — 距开幕" : "倒计时"),
          e("div", { className: "flex items-baseline gap-2" },
            e("div", { className: "text-[36px] font-bold tabular-nums", style: { color: days <= 14 ? "#fbbf24" : "#a855f7" } },
              days > 0 ? days : Math.abs(days)),
            e("div", { className: "text-[14px] text-slate-300" }, days >= 0 ? "天" : "天 (进行中)")
          ),
          e("div", { className: "text-[10.5px] text-slate-500 mt-1" },
            ev.startDate, " · ", ev.location.city
          )
        ),
        e("div", { className: "text-right text-[10.5px] text-slate-400" },
          e("div", { className: "mb-1" }, "近期 ddl"),
          tasks.filter(t => !t.done && t.alert === "warn").slice(0, 2).map(t =>
            e("div", { key: t.id, className: "text-amber-300 flex items-center gap-1.5" },
              e(AlertCircle, { size: 11 }), t.title, e("span", { className: "text-amber-500" }, t.dueDate)
            )
          )
        )
      )
    ),

    // 4 KPI cards
    e("div", { className: "grid grid-cols-4 gap-3" },
      e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3" },
        e("div", { className: "flex items-center gap-1.5 text-[10px] text-slate-400 mb-2" }, e(DollarSign, { size: 10 }), "预算使用"),
        e("div", { className: "text-[20px] font-bold text-white tabular-nums" }, fmtMoneyShort(totalSpent)),
        e("div", { className: "text-[10px] text-slate-500" }, "/ " + fmtMoneyShort(ev.budgetTotal), " · ", Math.round(totalSpent/ev.budgetTotal*100), "%")
      ),
      e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3" },
        e("div", { className: "flex items-center gap-1.5 text-[10px] text-slate-400 mb-2" }, e(CircleDot, { size: 10 }), "任务进度"),
        e("div", { className: "text-[20px] font-bold text-white tabular-nums" }, doneTasks),
        e("div", { className: "text-[10px] text-slate-500" }, "/ " + tasks.length + " · ", tasks.length ? Math.round(doneTasks/tasks.length*100) : 0, "%")
      ),
      e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3" },
        e("div", { className: "flex items-center gap-1.5 text-[10px] text-slate-400 mb-2" }, e(Users, { size: 10 }), "已确认 KOL"),
        e("div", { className: "text-[20px] font-bold text-white tabular-nums" }, confirmedKols),
        e("div", { className: "text-[10px] text-slate-500" }, "/ " + ev.invitedKols.length + " 邀请")
      ),
      e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3" },
        e("div", { className: "flex items-center gap-1.5 text-[10px] text-slate-400 mb-2" }, e(ShieldCheck, { size: 10 }), "健康度"),
        e("div", { className: "text-[20px] font-bold tabular-nums", style: { color: healthColor(ev.healthScore) } }, ev.healthScore),
        e("div", { className: "text-[10px] text-slate-500" }, ev.healthScore >= 85 ? "进度良好" : ev.healthScore >= 70 ? "需关注" : "落后")
      )
    ),

    // 地图占位 + 关联信息
    e("div", { className: "grid grid-cols-2 gap-3" },
      // 地图占位
      e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3" },
        e("div", { className: "flex items-center justify-between mb-2" },
          e("div", { className: "flex items-center gap-1.5 text-[11px] text-slate-300 font-medium" },
            e(MapPin, { size: 11, className: "text-cyan-400" }), "现场地址"
          ),
          e("button", {
            onClick: () => {
              const q = ev.location.lat
                ? `${ev.location.lat},${ev.location.lng}`
                : encodeURIComponent(`${ev.location.name} ${ev.location.city}`);
              window.open(`https://www.google.com/maps/search/?api=1&query=${q}`, "_blank");
            },
            className: "text-[9.5px] text-cyan-300 hover:text-cyan-200 flex items-center gap-0.5"
          },
            e(ExternalLink, { size: 9 }), "在 Google Maps 打开"
          )
        ),
        e("div", { className: "h-40 rounded bg-[#0a0a0d] border border-white/[0.04] flex items-center justify-center relative overflow-hidden" },
          // 简单模拟地图网格背景
          e("div", { className: "absolute inset-0 opacity-20", style: {
            backgroundImage: "linear-gradient(0deg, rgba(6,182,212,0.15) 1px, transparent 1px), linear-gradient(90deg, rgba(6,182,212,0.15) 1px, transparent 1px)",
            backgroundSize: "20px 20px"
          } }),
          e("div", { className: "text-center z-10" },
            e(MapPin, { size: 22, className: "text-cyan-400 mx-auto mb-1" }),
            e("div", { className: "text-[11.5px] font-medium text-white" }, ev.location.name),
            e("div", { className: "text-[10px] text-slate-400" }, ev.location.city, ", ", ev.location.country),
            ev.location.lat != null && ev.location.lng != null && e("div", { className: "text-[9.5px] text-slate-500 mt-1 font-mono tabular-nums" },
              ev.location.lat.toFixed(4), ", ", ev.location.lng.toFixed(4)
            )
          )
        )
      ),
      // 关联 Project + 团队
      e("div", { className: "space-y-3" },
        // 团队
        e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3" },
          e("div", { className: "flex items-center justify-between mb-2" },
            e("div", { className: "flex items-center gap-1.5 text-[11px] text-slate-300 font-medium" },
              e(Users, { size: 11, className: "text-purple-400" }), "团队 (", ev.teamUserIds.length, " 人)"
            ),
            // + 加成员
            e("div", { className: "relative" },
              e("button", {
                onClick: () => setAddingTeam(!addingTeam),
                className: "text-[10px] text-purple-300 hover:text-purple-200 px-1.5 py-0.5 rounded hover:bg-purple-500/10 flex items-center gap-0.5"
              }, e(Plus, { size: 9 }), addingTeam ? "取消" : "加成员"),
              addingTeam && e("div", { className: "absolute right-0 top-full mt-1.5 w-48 rounded-lg border border-white/[0.08] bg-[#0b1220] shadow-2xl z-20 p-1.5 max-h-60 overflow-y-auto" },
                e("div", { className: "text-[9.5px] text-slate-500 px-2 py-1 uppercase tracking-wider" }, "添加成员"),
                staff.length === 0
                  ? e("div", { className: "text-[10.5px] text-slate-400 px-2 py-2" }, "员工名单未加载,暂不能加人")
                  : teamCandidates.length === 0
                  ? e("div", { className: "text-[10.5px] text-slate-400 px-2 py-2" }, "所有人都已加入")
                  : teamCandidates.map(u =>
                      e("button", {
                        key: u.id,
                        onClick: () => { onUpdateTeam([...ev.teamUserIds, String(u.id)]); setAddingTeam(false); },
                        className: "w-full px-2 py-1.5 rounded text-left hover:bg-white/[0.05] flex items-center gap-2"
                      },
                        e("div", { className: "w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold text-white", style: { background: u.color || "#94a3b8" } }, u.avatar || String(u.name || "?").slice(0, 1)),
                        e("span", { className: "text-[10.5px] text-slate-200" }, u.name)
                      )
                    )
              )
            )
          ),
          e("div", { className: "space-y-1.5" },
            ev.teamUserIds.map(uid => {
              const u = memberFromStaff(uid, staff); // 真人优先,兼容老 mock 字母 id
              const isOwner = uid === ev.ownerId;
              return e("div", { key: uid, className: "flex items-center gap-2 text-[10.5px] group" },
                e("div", { className: "w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold text-white", style: { background: u.color } }, u.initial),
                e("span", { className: "text-slate-300 flex-1" }, u.name),
                isOwner && e("span", { className: "text-[9px] text-purple-300 px-1.5 py-0.5 rounded bg-purple-500/15" }, "负责人"),
                !isOwner && onUpdateTeam && e("button", {
                  onClick: () => onUpdateTeam(ev.teamUserIds.filter(x => x !== uid)),
                  className: "opacity-0 group-hover:opacity-100 text-slate-500 hover:text-red-300 p-0.5",
                  title: "移除"
                }, e(X, { size: 11 }))
              );
            })
          )
        ),
        // Project chips
        ev.relatedProjectIds.length > 0 && e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3" },
          e("div", { className: "flex items-center gap-1.5 text-[11px] text-slate-300 font-medium mb-2" },
            e(Briefcase, { size: 11, className: "text-cyan-400" }), "关联 Project"
          ),
          e("div", { className: "flex flex-wrap gap-1.5" },
            ev.relatedProjectIds.map(pid => {
              const p = projectById(pid);
              return e("div", { key: pid, className: "px-2 py-1 rounded text-[10px] bg-cyan-500/10 text-cyan-200 border border-cyan-500/20" }, p?.title || pid);
            })
          )
        )
      )
    ),

    // 备注
    ev.note && e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3" },
      e("div", { className: "text-[10px] text-slate-400 mb-1" }, "备注"),
      e("div", { className: "text-[11.5px] text-slate-200" }, ev.note)
    )
  );
}
