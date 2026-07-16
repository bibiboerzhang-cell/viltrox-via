import React from "react";
import { Briefcase, Calendar, Check, Clock, MapPin, Users } from "lucide-react";
import { EVENT_STATUS, EVENT_TYPES } from "../shared/constants";
import { eventTiming, fmtMoneyShort, healthColor, percentOf, sum } from "../shared/helpers";
import { memberFromStaff } from "../shared/lookups";
import type { EventVm, InvitedKolVm, UiStaff } from "../shared/types";

const e = React.createElement;

interface EventCardProps {
  ev: EventVm;
  onOpen: (ev: EventVm) => void;
  staff?: UiStaff[];
}

export default function EventCard({ ev, onOpen, staff = [] }: EventCardProps) {
  const typeCfg = EVENT_TYPES[ev.typeKey] || EVENT_TYPES.other;
  const statusCfg = EVENT_STATUS[ev.status] || EVENT_STATUS.planning;
  const Icon = typeCfg.icon;
  const totalSpent = sum(ev.budgetByCategory, "spent");
  const spentPct = percentOf(totalSpent, ev.budgetTotal);
  const timing = eventTiming(ev.startDate, ev.endDate);
  const isDone = ev.status === "done";
  const isCancelled = ev.status === "cancelled";
  const hasHealthScore = ev.healthScore != null && Number.isFinite(ev.healthScore);
  const hc = healthColor(ev.healthScore);
  const outcomeParts = [
    typeof ev.roi === "number" && Number.isFinite(ev.roi) ? `ROI ${ev.roi}x` : "",
    typeof ev.leads === "number" && Number.isFinite(ev.leads) ? `${ev.leads} lead` : "",
  ].filter(Boolean);

  const confirmedKols = ev.invitedKols.filter((k: InvitedKolVm) => k.status === "confirmed").length;

  return e("div", {
    onClick: () => onOpen(ev),
    className: "rounded-2xl border border-white/[0.06] bg-white/[0.012] hover:bg-white/[0.025] hover:border-white/[0.12] transition-all cursor-pointer p-5 group"
  },
    // 顶部 (icon + title + status)
    e("div", { className: "flex items-start gap-3 mb-4" },
      e("div", {
        className: "w-11 h-11 rounded-xl flex items-center justify-center shrink-0",
        style: { background: `linear-gradient(135deg, ${typeCfg.color}25, ${typeCfg.color}10)`, border: `1px solid ${typeCfg.color}30` }
      }, e(Icon, { size: 19, style: { color: typeCfg.color } })),
      e("div", { className: "flex-1 min-w-0" },
        e("div", { className: "flex items-center gap-2 mb-1 flex-wrap" },
          e("div", { className: "text-[13.5px] font-semibold text-white truncate" }, ev.title),
          e("span", {
            className: "text-[9.5px] px-1.5 py-0.5 rounded font-medium",
            style: { background: statusCfg.color + "20", color: statusCfg.color }
          }, statusCfg.label),
          !isDone && e("span", {
            className: "text-[9.5px] text-slate-400 px-1.5 py-0.5 rounded bg-white/[0.04]"
          }, "健康度 ", e("span", {
            style: { color: hc },
            title: hasHealthScore ? "录入值；当前接口未提供评分来源" : "尚无可核验的健康度评分",
          }, hasHealthScore ? `录入 ${ev.healthScore}` : "待评估"))
        ),
        e("div", { className: "flex items-center gap-2 text-[10.5px] text-slate-400" },
          e(MapPin, { size: 10 }),
          e("span", null, ev.location.city, ", ", ev.location.country),
          e("span", { className: "text-slate-600" }, "·"),
          e(Calendar, { size: 10 }),
          e("span", null, ev.startDate.slice(5).replace("-", "/"),
            ev.startDate !== ev.endDate ? " - " + ev.endDate.slice(5).replace("-","/") : ""
          )
        )
      )
    ),

    // 倒计时 banner (非已完成才显示)
    !isDone && !isCancelled && e("div", {
      className: "mb-3 px-3 py-1.5 rounded-md flex items-center justify-between",
      style: timing.phase === "ended" || timing.phase === "invalid"
        ? { background: "rgba(100,116,139,0.06)", border: "1px solid rgba(100,116,139,0.16)" }
        : timing.phase === "ongoing" || timing.days <= 14
          ? { background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.20)" }
          : { background: "rgba(168,85,247,0.06)", border: "1px solid rgba(168,85,247,0.15)" }
    },
      e("div", { className: "flex items-center gap-1.5" },
        e(Clock, { size: 11, style: { color: timing.phase === "ended" || timing.phase === "invalid" ? "#94a3b8" : timing.phase === "ongoing" || timing.days <= 14 ? "#fbbf24" : "#a855f7" } }),
        e("span", { className: "text-[10.5px] font-medium", style: { color: timing.phase === "ended" || timing.phase === "invalid" ? "#94a3b8" : timing.phase === "ongoing" || timing.days <= 14 ? "#fbbf24" : "#c4b5fd" } },
          timing.label)
      ),
      e("span", { className: "text-[9.5px] text-slate-500 tabular-nums" }, ev.startDate)
    ),

    // 已完成 banner
    isDone && e("div", { className: "mb-3 px-3 py-1.5 rounded-md flex items-center justify-between", style: { background: "rgba(16,185,129,0.06)", border: "1px solid rgba(16,185,129,0.15)" } },
      e("div", { className: "flex items-center gap-1.5" },
        e(Check, { size: 11, className: "text-emerald-400" }),
        e("span", { className: "text-[10.5px] font-medium text-emerald-300" },
          outcomeParts.length ? `已完成 · ${outcomeParts.join(" · ")}` : "已完成 · 结果待录入")
      )
    ),

    // 预算 + 任务进度条
    e("div", { className: "space-y-2 mb-3" },
      e("div", null,
        e("div", { className: "flex items-center justify-between text-[10.5px] mb-1" },
          e("span", { className: "text-slate-400" }, "预算"),
          e("span", { className: "text-white tabular-nums" },
            fmtMoneyShort(totalSpent), e("span", { className: "text-slate-500" }, " / " + fmtMoneyShort(ev.budgetTotal)),
            spentPct == null ? " · 未设预算" : ` (${spentPct}%)`
          )
        ),
        e("div", { className: "h-1.5 rounded-full bg-white/[0.04] overflow-hidden" },
          e("div", { className: "h-full rounded-full transition-all", style: {
            width: Math.min(100, spentPct ?? 0) + "%",
            background: spentPct != null && spentPct > 100 ? "#ef4444" : spentPct != null && spentPct > 80 ? "#fbbf24" : "#a855f7"
          } })
        )
      )
    ),

    // bottom row (KOL + 团队 + Project chip + 更新时间)
    e("div", { className: "flex items-center justify-between pt-2 border-t border-white/[0.04]" },
      e("div", { className: "flex items-center gap-3 text-[10px] text-slate-400" },
        confirmedKols > 0 && e("div", { className: "flex items-center gap-1" },
          e(Users, { size: 10 }), confirmedKols + " KOL 确认"
        ),
        ev.relatedProjectIds.length > 0 && e("div", { className: "flex items-center gap-1" },
          e(Briefcase, { size: 10 }), ev.relatedProjectIds.length + " 项目"
        )
      ),
      // team avatars
      e("div", { className: "flex items-center gap-0.5" },
        ev.teamUserIds.slice(0, 4).map((uid: string, i: number) => {
          const u = memberFromStaff(uid, staff);
          return e("div", {
            key: uid,
            className: "w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold text-white",
            style: { background: u.color, marginLeft: i > 0 ? "-4px" : 0, border: "2px solid #0a0a0d", zIndex: 10 - i }
          }, u.initial);
        })
      )
    )
  );
}
