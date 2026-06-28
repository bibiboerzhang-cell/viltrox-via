// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { Bell, ChevronRight, X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";
import { NOTIF_ICON_MAP } from "../../data/notifIconMap";

const e = React.createElement;

export function AllNotificationsModal({ notifications, onClose, onNotifClick }: any) {
  const { t } = useT();
  const [tab, setTab] = useState("all");
  const [items, setItems] = useState(notifications);

  // Categorize by iconKey
  const isSystem = (n: any) => ["filetext", "zap", "trending", "package", "target"].includes(n.iconKey);
  const isAI     = (n: any) => n.iconKey === "sparkles";
  const isAlert  = (n: any) => n.iconKey === "warning";
  const isFeedback = (n: any) => n.category === "feedback" || n.source === "vkpi_feedback";

  const filtered = items.filter((n: any) => {
    if (tab === "all")    return true;
    if (tab === "system") return isSystem(n);
    if (tab === "ai")     return isAI(n);
    if (tab === "alert")  return isAlert(n);
    if (tab === "feedback") return isFeedback(n);
    return true;
  });
  
  return e(CenterModal, { onClose, maxWidth: "2xl" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("div", null,
        e("h2", { className: "text-sm font-semibold text-white" }, t("全部通知")),
        e("div", { className: "text-[10px] text-slate-500 mt-0.5" }, `${items.length} ${t("项")} · ${t("按类型筛选")}`)
      ),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    e("div", { className: "px-5 pt-2 flex gap-3 border-b border-white/[0.04]" },
      [
        { key: "all",    label: t("全部"),   count: items.length },
        { key: "system", label: t("系统"),   count: items.filter(isSystem).length },
        { key: "ai",     label: t("AI"),     count: items.filter(isAI).length },
        { key: "alert",  label: "⚠ Alert",  count: items.filter(isAlert).length },
        { key: "feedback", label: t("反馈"), count: items.filter(isFeedback).length },
      ].map((x: any) => e("button", {
        key: x.key, onClick: () => setTab(x.key),
        className: "text-[11px] py-1.5 px-0.5 border-b-2 flex items-center gap-1",
        style: { borderColor: tab === x.key ? "#a855f7" : "transparent", color: tab === x.key ? "#fff" : "rgba(255,255,255,0.5)" }
      }, x.label, e("span", { className: "text-[9px] text-slate-500" }, x.count)))
    ),
    e("div", { className: "p-3 max-h-[60vh] overflow-y-auto space-y-1" },
      filtered.map((n: any) => {
        const IconComp = (NOTIF_ICON_MAP as any)[n.iconKey] || Bell;
        return e("button", {
          key: n.id,
          onClick: () => { onClose(); onNotifClick && onNotifClick(n); },
          className: "w-full text-left flex items-start gap-3 px-3 py-2 rounded-md hover:bg-white/[0.04] relative"
        },
          n.unread && e("span", { className: "absolute left-1 top-3.5 h-1.5 w-1.5 rounded-full bg-rose-500" }),
          e("div", {
            className: "shrink-0 w-8 h-8 rounded-md flex items-center justify-center",
            style: { background: n.iconColor + "22" }
          }, e(IconComp, { size: 13, style: { color: n.iconColor } })),
          e("div", { className: "flex-1 min-w-0 pl-1" },
            e("div", { className: "text-[11px] font-medium text-white" }, n.title),
            e("div", { className: "text-[10px] text-slate-400 mt-0.5" }, n.desc),
            e("div", { className: "text-[9px] text-slate-500 mt-1" }, n.time)
          ),
          e(ChevronRight, { size: 11, className: "text-slate-600 mt-2.5" })
        );
      })
    )
  );
}
