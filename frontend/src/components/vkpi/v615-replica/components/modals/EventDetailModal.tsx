// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Edit, Filter, X } from "lucide-react";
import { loadStoredState, saveStoredState } from "../../lib/storage";
import { TIMELINE_CATEGORIES } from "../../data/timelineCategories";
import { TIMELINE_STATUS } from "../../data/timelineStatus";

const e = React.createElement;

export function EventDetailModal({ event, onClose, onOpenFullEvent }) {
  // V6.10: filter 持久化
  const stored = loadStoredState();
  const [filter, setFilter] = useState(stored.eventFilter || "all");
  
  useEffect(() => {
    saveStoredState({ eventFilter: filter });
  }, [filter]);
  
  if (!event) return null;
  
  const timeline = event.timeline || [];
  const filtered = filter === "all" ? timeline : timeline.filter(t => t.category === filter);
  
  // 计算各 category 进度
  const stats = {};
  Object.keys(TIMELINE_CATEGORIES).forEach(cat => {
    const items = timeline.filter(t => t.category === cat);
    const done = items.filter(t => t.status === "done").length;
    stats[cat] = { done, total: items.length, pct: items.length ? Math.round((done / items.length) * 100) : 0 };
  });
  
  const overallDone = timeline.filter(t => t.status === "done").length;
  const overallPct = timeline.length ? Math.round((overallDone / timeline.length) * 100) : 0;
  
  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "v615-modal fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4 overflow-y-auto",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev) => ev.stopPropagation(),
      className: "relative w-full max-w-3xl my-8 rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden",
    },
      // ─── Header ───
      e("div", { className: "relative border-b border-white/[0.06]" },
        e("div", { 
          className: "absolute inset-0 opacity-30", 
          style: { background: `radial-gradient(ellipse at top right, ${event.color}66, transparent 70%)` } 
        }),
        e("div", { className: "relative px-6 py-5 flex items-start gap-4" },
          e("div", { 
            className: "flex h-12 w-12 shrink-0 items-center justify-center rounded-xl",
            style: { background: `${event.color}22`, color: event.color }
          }, e(event.icon, { size: 24 })),
          e("div", { className: "flex-1 min-w-0" },
            e("div", { className: "flex items-baseline gap-3 mb-1" },
              e("h2", { className: "text-2xl font-semibold text-white" }, event.title),
              e("span", { className: "text-sm text-slate-400" }, event.date)
            ),
            e("p", { className: "text-sm text-slate-400" }, event.desc),
            e("div", { className: "mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-400" },
              e("div", null,
                e("span", { className: "text-slate-500" }, "Venue: "),
                e("span", { className: "text-slate-300" }, event.venue || event.location)
              ),
              event.budget && e("div", null,
                e("span", { className: "text-slate-500" }, "Budget: "),
                e("span", { className: "text-slate-300" }, event.budget)
              ),
              event.team && e("div", null,
                e("span", { className: "text-slate-500" }, "Team: "),
                e("span", { className: "text-slate-300" }, event.team.join(" · "))
              ),
            )
          ),
          e("button", {
            onClick: onClose,
            className: "shrink-0 rounded-lg border border-white/10 bg-white/5 p-2 text-slate-400 hover:text-white hover:bg-white/10"
          }, e(X, { size: 16 }))
        )
      ),
      
      // ─── Stats Bar (5 类进度条) ───
      e("div", { className: "border-b border-white/[0.06] px-6 py-4" },
        // Overall
        e("div", { className: "mb-3" },
          e("div", { className: "mb-1 flex items-center justify-between" },
            e("span", { className: "text-xs uppercase tracking-wider text-slate-500" }, "Overall Progress"),
            e("span", { className: "text-sm font-medium tabular-nums", style: { color: event.color } }, `${overallPct}% · ${overallDone}/${timeline.length}`)
          ),
          e("div", { className: "h-1.5 overflow-hidden rounded-full bg-white/[0.06]" },
            e(motion.div, {
              initial: { width: 0 },
              animate: { width: `${overallPct}%` },
              transition: { duration: 0.8, ease: [0.16, 1, 0.3, 1] },
              className: "h-full rounded-full",
              style: { background: `linear-gradient(90deg, ${event.color}, ${event.color}aa)` }
            })
          )
        ),
        // Category breakdown
        e("div", { className: "grid grid-cols-5 gap-3" },
          Object.entries(TIMELINE_CATEGORIES).map(([key, cat]) => {
            const s = stats[key];
            const active = filter === key;
            return e("button", {
              key, 
              onClick: () => setFilter(active ? "all" : key),
              className: `text-left rounded-lg border p-2 transition-all ${active ? "border-white/30 bg-white/[0.06]" : "border-white/[0.06] bg-white/[0.02] hover:border-white/15"}`
            },
              e("div", { className: "flex items-center gap-1.5 mb-1.5" },
                e(cat.icon, { size: 11, style: { color: cat.color } }),
                e("span", { className: "text-[10px] uppercase tracking-wider", style: { color: cat.color } }, cat.label)
              ),
              e("div", { className: "mb-1 flex items-baseline gap-1" },
                e("span", { className: "text-base font-medium text-white tabular-nums" }, s.pct + "%"),
                e("span", { className: "text-[10px] text-slate-500 tabular-nums" }, `${s.done}/${s.total}`)
              ),
              e("div", { className: "h-0.5 overflow-hidden rounded-full bg-white/[0.06]" },
                e("div", {
                  className: "h-full rounded-full transition-all",
                  style: { width: `${s.pct}%`, background: cat.color }
                })
              )
            );
          })
        )
      ),
      
      // ─── Filter / Total ───
      e("div", { className: "flex items-center justify-between px-6 py-3 border-b border-white/[0.04]" },
        e("h3", { className: "text-sm font-semibold text-white" }, 
          filter === "all" ? "Full Timeline" : `${TIMELINE_CATEGORIES[filter].label} Timeline`
        ),
        e("div", { className: "flex items-center gap-2 text-xs text-slate-400" },
          e("span", null, `${filtered.length} items`),
          filter !== "all" && e("button", {
            onClick: () => setFilter("all"),
            className: "text-slate-300 hover:text-white underline-offset-2 hover:underline"
          }, "Show all")
        )
      ),
      
      // ─── Timeline ───
      e("div", { className: "px-6 py-4 max-h-[50vh] overflow-y-auto" },
        e("div", { className: "relative" },
          // 中线
          e("div", { className: "absolute left-[78px] top-0 bottom-0 w-px bg-white/[0.06]" }),
          
          filtered.map((item, i) => {
            const cat = TIMELINE_CATEGORIES[item.category];
            const st = TIMELINE_STATUS[item.status];
            return e(motion.div, {
              key: item.id,
              initial: { opacity: 0, x: -8 },
              animate: { opacity: 1, x: 0 },
              transition: { delay: i * 0.03 },
              className: "relative flex items-start gap-4 pb-4"
            },
              // 日期(左)
              e("div", { className: "w-[70px] shrink-0 pt-0.5" },
                e("div", { className: "text-xs font-medium text-white tabular-nums" }, item.date)
              ),
              // 状态点(中线上)
              e("div", { className: "relative shrink-0 z-10 -ml-[2px]" },
                e("div", { 
                  className: "h-3 w-3 rounded-full border-2",
                  style: { 
                    background: item.status === "done" ? st.color : "#0a1020",
                    borderColor: st.color,
                    boxShadow: item.status === "in-progress" ? `0 0 12px ${st.color}` : "none",
                  }
                })
              ),
              // 内容(右)
              e("div", { className: "flex-1 min-w-0 -mt-0.5" },
                e("div", { className: "flex items-center gap-2 mb-1 flex-wrap" },
                  e("span", { 
                    className: "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px] uppercase tracking-wider font-medium",
                    style: { color: cat.color, background: `${cat.color}1a` }
                  },
                    e(cat.icon, { size: 9 }),
                    cat.label
                  ),
                  e("span", { 
                    className: "rounded px-1.5 py-0.5 text-[9px] uppercase tracking-wider",
                    style: { color: st.color, background: `${st.color}1a` }
                  }, st.label)
                ),
                e("div", { 
                  className: "text-sm font-medium",
                  style: { 
                    color: item.status === "done" ? "rgba(255,255,255,0.55)" : "white",
                    textDecoration: item.status === "done" ? "line-through" : "none",
                  }
                }, item.title),
                e("div", { className: "mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-500" },
                  e("span", null, "👤 ", item.owner),
                  item.note && e("span", { className: "text-slate-400 italic truncate max-w-md" }, "· ", item.note)
                )
              )
            );
          })
        )
      ),
      
      // ─── Footer Actions ───
      e("div", { className: "border-t border-white/[0.06] px-6 py-3 flex items-center justify-between" },
        e("div", { className: "text-xs text-slate-500" },
          "Template-driven · " + Object.keys(TIMELINE_CATEGORIES).length + " phases · auto-generated from event playbook"
        ),
        e("div", { className: "flex gap-2" },
          // 跳真实 Events 页并打开该活动详情(编辑 + 完整 tab 都在那里)。
          e("button", {
            onClick: () => { if (onOpenFullEvent) onOpenFullEvent(event); else onClose && onClose(); },
            className: "rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-slate-300 hover:bg-white/[0.06]",
            title: onOpenFullEvent ? "在 Events 页编辑该活动" : "待接入"
          }, "Edit Plan"),
          e("button", {
            onClick: () => { if (onOpenFullEvent) onOpenFullEvent(event); else onClose && onClose(); },
            className: "rounded-lg px-3 py-1.5 text-xs text-white",
            style: { background: event.color },
            title: onOpenFullEvent ? "查看完整活动详情" : "待接入"
          }, "View Full Report →")
        )
      )
    )
  );
}
