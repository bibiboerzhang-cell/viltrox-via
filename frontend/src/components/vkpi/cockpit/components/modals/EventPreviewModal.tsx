// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { ChevronRight, X } from "lucide-react";
import { Breadcrumb } from "../Breadcrumb";

const e = React.createElement;

export function EventPreviewModal({ event, allEvents, onClose, onViewDetails }: any) {
  if (!event) return null;
  const Icon = event.icon;

  // 找附近 events(同 country 或同 city 的其他)
  const nearby = allEvents.filter((e: any) =>
    e.id !== event.id && (e.country === event.country || e.city === event.city)
  );
  
  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "cockpit-modal fixed inset-0 flex items-center justify-center bg-black/60 backdrop-blur-md p-4",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0 }, animate: { scale: 1, opacity: 1 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev: any) => ev.stopPropagation(),
      className: "relative w-full max-w-sm rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden",
    },
      // Header
      e("div", { className: "relative px-5 py-4 border-b border-white/[0.06]" },
        e("div", { 
          className: "absolute inset-0 opacity-30", 
          style: { background: `radial-gradient(ellipse at top right, ${event.color}66, transparent 70%)` } 
        }),
        e("div", { className: "relative flex items-start gap-3" },
          e("div", { 
            className: "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
            style: { background: `${event.color}22`, color: event.color }
          }, e(Icon, { size: 18 })),
          e("div", { className: "flex-1 min-w-0" },
            // Breadcrumb: World > Country > City
            e("div", { className: "flex items-center gap-1 text-[10px] text-slate-500 mb-1" },
              e("span", null, "World"),
              e(ChevronRight, { size: 10 }),
              e("span", null, event.country),
              e(ChevronRight, { size: 10 }),
              e("span", { className: "text-slate-300" }, event.city)
            ),
            e("h3", { className: "text-base font-semibold text-white truncate" }, event.title),
            e("p", { className: "text-xs text-slate-400" }, event.date)
          ),
          e("button", {
            onClick: onClose,
            className: "shrink-0 rounded-md p-1 text-slate-500 hover:text-white hover:bg-white/5"
          }, e(X, { size: 14 }))
        )
      ),
      
      // Body
      e("div", { className: "px-5 py-4" },
        // Progress
        e("div", { className: "mb-4" },
          e("div", { className: "mb-1 flex items-center justify-between" },
            e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "Progress"),
            e("span", { className: "text-sm font-medium tabular-nums", style: { color: event.color } }, 
              `${event.materialProgress}% · ${event.materialStatus}`
            )
          ),
          e("div", { className: "h-1 overflow-hidden rounded-full bg-white/[0.06]" },
            e(motion.div, {
              initial: { width: 0 },
              animate: { width: `${event.materialProgress}%` },
              transition: { duration: 0.8, ease: [0.16, 1, 0.3, 1] },
              className: "h-full rounded-full",
              style: { background: event.color }
            })
          )
        ),
        // Venue
        e("div", { className: "mb-4 text-xs" },
          e("div", { className: "text-slate-500 mb-1" }, "Venue"),
          e("div", { className: "text-slate-300" }, event.venue || event.location)
        ),
        // Nearby events
        nearby.length > 0 && e("div", null,
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, 
            `Other events in ${event.country}`
          ),
          e("div", { className: "space-y-1.5" },
            nearby.slice(0, 3).map((evt: any) => {
              const NIcon = evt.icon;
              return e("button", {
                key: evt.id,
                onClick: () => onViewDetails(evt),
                className: "w-full flex items-center gap-2 rounded-lg border border-white/[0.04] bg-white/[0.02] px-2.5 py-1.5 text-left hover:border-white/[0.12] hover:bg-white/[0.04]"
              },
                e("span", { 
                  className: "shrink-0 rounded p-1", 
                  style: { background: `${evt.color}22`, color: evt.color } 
                }, e(NIcon, { size: 10 })),
                e("div", { className: "min-w-0 flex-1" },
                  e("div", { className: "text-[11px] font-medium text-white truncate" }, evt.title),
                  e("div", { className: "text-[9px] text-slate-500" }, evt.date)
                ),
                e("span", { className: "shrink-0 text-[10px] tabular-nums", style: { color: evt.color } }, 
                  `${evt.materialProgress}%`
                )
              );
            })
          )
        ),
        nearby.length === 0 && e("div", { className: "text-[11px] text-slate-500 italic" }, 
          `No other events in ${event.country} yet.`
        )
      ),
      
      // Footer
      e("div", { className: "border-t border-white/[0.06] px-5 py-3 flex items-center justify-between" },
        e("button", {
          onClick: onClose,
          className: "text-xs text-slate-400 hover:text-white"
        }, "Cancel"),
        e("button", { 
          onClick: () => onViewDetails(event),
          className: "rounded-lg px-3 py-1.5 text-xs font-medium text-white",
          style: { background: event.color }
        }, "View Full Timeline →")
      )
    )
  );
}
