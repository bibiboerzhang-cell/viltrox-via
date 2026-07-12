// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { m } from "framer-motion";
import { Calendar, Minimize, Plus, X } from "lucide-react";

const e = React.createElement;

export function UpcomingEventsCard({ events, dragConstraintsRef, onEventClick, onViewAll }: any) {
  const [collapsed, setCollapsed] = useState(false);
  const nearestEvent = events[0];

  // 折叠态:只显示一个 chip(像 iOS app icon)
  if (collapsed) {
    return e(m.div, {
      drag: true,
      dragConstraints: dragConstraintsRef,
      dragMomentum: false,
      dragElastic: 0.05,
      initial: { opacity: 0, scale: 0.9 },
      animate: { opacity: 1, scale: 1 },
      whileDrag: { scale: 1.05, cursor: "grabbing" },
      onClick: () => setCollapsed(false),
      className: "cursor-pointer rounded-xl border border-line bg-panel px-3 py-2 backdrop-blur-xl shadow-ds-sm hover:border-line-strong active:cursor-grabbing",
      style: { cursor: "grab" }
    },
      e("div", { className: "flex items-center gap-2" },
        e("span", { className: "rounded-md bg-warn-soft p-1.5 text-warn" },
          e(Calendar, { size: 12 })
        ),
        e("div", { className: "min-w-0" },
          e("div", { className: "text-[10px] uppercase tracking-wider text-muted" }, "Next event"),
          e("div", { className: "text-xs font-medium text-ink truncate max-w-[140px]" }, nearestEvent?.title || "No events"),
          e("div", { className: "text-[10px] text-muted" }, nearestEvent?.date)
        )
      )
    );
  }

  return e(m.div, {
    drag: true,
    dragConstraints: dragConstraintsRef,
    dragMomentum: false,
    dragElastic: 0.05,
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    transition: { delay: 0.4 },
    whileDrag: { scale: 1.02, cursor: "grabbing", zIndex: 50 },
    className: "rounded-xl border border-line bg-panel p-3 backdrop-blur-xl shadow-ds"
  },
    // Drag handle bar
    e("div", { className: "mb-2 flex items-center justify-between gap-2 cursor-grab active:cursor-grabbing select-none" },
      e("div", { className: "flex items-center gap-1.5 flex-1 min-w-0" },
        e("div", { className: "flex gap-0.5" },
          e("span", { className: "h-1 w-1 rounded-full bg-line-strong" }),
          e("span", { className: "h-1 w-1 rounded-full bg-line-strong" }),
          e("span", { className: "h-1 w-1 rounded-full bg-line-strong" })
        ),
        e(Calendar, { size: 12, className: "text-warn ml-1" }),
        e("h3", { className: "text-xs font-semibold text-ink truncate" }, "Upcoming Events"),
        e("span", { className: "text-[9px] text-muted tabular-nums" }, `(${events.length})`),
        // 数据来源标注:真实 events API(诚实化 2026-06-14)
        e("span", { className: "shrink-0 rounded border border-good-soft bg-good-soft px-1 py-px text-[8px] text-good" }, "实时")
      ),
      e("div", { className: "flex items-center gap-1 shrink-0" },
        // 加号接真:跳 Events 页(在那里新建活动);无 onViewAll 的老调用方直接隐藏,不留死按钮。
        onViewAll && e("button", {
          onClick: (ev: any) => { ev.stopPropagation(); onViewAll(); },
          title: "去 Events 页新建活动",
          className: "rounded-md p-1 text-muted hover:bg-accent-soft hover:text-ink"
        }, e(Plus, { size: 10 })),
        e("button", {
          onClick: (ev: any) => { ev.stopPropagation(); setCollapsed(true); },
          title: "Minimize",
          className: "rounded-md p-1 text-muted hover:bg-accent-soft hover:text-ink"
        }, e(X, { size: 10 }))
      )
    ),

    e("div", { className: "space-y-2 max-h-[200px] overflow-y-auto pr-1" },
      // 诚实空态:无真实未来活动时显示「暂无活动」,不编造占位卡。
      events.length === 0 && e("div", { className: "rounded-lg border border-dashed border-line bg-panel px-3 py-4 text-center" },
        e("div", { className: "text-[11px] text-muted" }, "暂无活动"),
        e("div", { className: "mt-0.5 text-[9px] text-muted" }, "近期无即将开始的真实活动")
      ),
      events.map((evt: any) => {
        const Icon = evt.icon;
        return e("div", {
          key: evt.id,
          onClick: (ev: any) => { ev.stopPropagation(); if (onEventClick) onEventClick(evt); },
          className: "group cursor-pointer rounded-lg border border-line bg-panel p-2 transition-colors hover:border-line-strong hover:bg-accent-soft"
        },
          e("div", { className: "mb-1.5 flex items-start gap-2" },
            e("span", { className: "shrink-0 rounded-md p-1", style: { background: `color-mix(in srgb, ${evt.color} 13%, transparent)`, color: evt.color } },
              e(Icon, { size: 10 })
            ),
            e("div", { className: "min-w-0 flex-1" },
              e("div", { className: "flex items-center justify-between gap-2" },
                e("div", { className: "min-w-0 text-[11px] font-medium text-ink truncate" }, evt.title),
                e("div", { className: "shrink-0 text-[9px] tabular-nums text-muted" }, evt.date)
              ),
              e("div", { className: "text-[9px] text-muted truncate" }, evt.location)
            )
          ),
          // Material progress(精简)
          e("div", { className: "flex items-center gap-2" },
            e("div", { className: "flex-1 h-0.5 overflow-hidden rounded-full bg-card" },
              e(m.div, {
                initial: { width: 0 },
                animate: { width: `${evt.materialProgress}%` },
                transition: { delay: 0.5, duration: 0.8, ease: [0.16, 1, 0.3, 1] },
                className: "h-full rounded-full",
                style: { background: evt.color }
              })
            ),
            e("span", { className: "shrink-0 text-[9px] tabular-nums", style: { color: evt.color } }, `${evt.materialProgress}%`)
          )
        );
      })
    ),

    events.length > 0 && e("button", {
      // 跳到完整 Events 页;无 onViewAll(老调用方)时回退到打开第一个活动。
      onClick: () => { if (onViewAll) onViewAll(); else if (onEventClick && events[0]) onEventClick(events[0]); },
      className: "mt-2 w-full rounded-md border border-line bg-panel py-1 text-[10px] text-muted hover:text-ink hover:border-line-strong"
    }, "View all events →")
  );
}
