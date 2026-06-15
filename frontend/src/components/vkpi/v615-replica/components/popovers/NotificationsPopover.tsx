// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useEffect, useState } from "react";
import { Bell } from "lucide-react";
import { PopoverWrapper } from "./PopoverWrapper";
import { NOTIF_ICON_MAP } from "../../data/notifIconMap";

const e = React.createElement;

export function NotificationsPopover({ onClose, notifications, anchorRef, t, onViewAll, onItemClick, onMarkAllRead }: any) {
  const [items, setItems] = useState(notifications);
  useEffect(() => { setItems(notifications); }, [notifications]);
  const unreadCount = items.filter((n: any) => n.unread).length;
  const markAllRead = () => {
    setItems((prev: any) => prev.map((n: any) => ({ ...n, unread: false })));
    onMarkAllRead && onMarkAllRead();
  };
  return e(PopoverWrapper, { onClose, anchorRef, width: 380 },
    e("div", { className: "w-[360px] max-h-[70vh] flex flex-col" },
      e("div", { className: "px-3 py-2 border-b border-white/[0.06] flex items-center justify-between" },
        e("div", { className: "flex items-center gap-2" },
          e(Bell, { size: 13, className: "text-slate-400" }),
          e("div", { className: "text-[11px] font-semibold text-white" }, t("通知")),
          unreadCount > 0 && e("span", { className: "text-[9px] px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-300 font-medium" }, `${unreadCount} ${t("未读")}`)
        ),
        onMarkAllRead && e("button", { onClick: markAllRead, className: "text-[10px] text-slate-400 hover:text-white" }, t("全部已读"))
      ),
      e("div", { className: "flex-1 overflow-y-auto py-1" },
        items.map((n: any) => {
          const IconComp = (NOTIF_ICON_MAP as any)[n.iconKey] || Bell;
          return e("div", {
            key: n.id,
            onClick: () => { onClose(); onItemClick && onItemClick(n); },
            className: "flex items-start gap-3 px-3 py-2 hover:bg-white/[0.04] cursor-pointer transition-colors relative"
          },
            n.unread && e("span", { className: "absolute left-1 top-3.5 h-1.5 w-1.5 rounded-full bg-rose-500" }),
            e("div", {
              className: "shrink-0 w-8 h-8 rounded-md flex items-center justify-center",
              style: { background: n.iconColor + "22" }
            }, e(IconComp, { size: 13, style: { color: n.iconColor } })),
            e("div", { className: "flex-1 min-w-0 pl-1" },
              e("div", { className: "text-[11px] font-medium text-white leading-snug" }, n.title),
              e("div", { className: "text-[10px] text-slate-400 mt-0.5" }, n.desc),
              e("div", { className: "text-[9px] text-slate-500 mt-1" }, n.time)
            )
          );
        })
      ),
      e("div", { className: "px-3 py-2 border-t border-white/[0.06] text-center" },
        e("button", { 
          onClick: () => { onClose(); onViewAll && onViewAll(); },
          className: "text-[10px] text-purple-300 hover:text-purple-200" 
        }, t("查看所有通知 →"))
      )
    )
  );
}
