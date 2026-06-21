// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { Bell, Check, ChevronRight, X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";
import { NOTIF_ICON_MAP } from "../../data/notifIconMap";

const e = React.createElement;

export function NotificationDetailModal({ notification, onClose, onMarkRead, onNavigate }: any) {
  const { t } = useT();
  const IconComp = (NOTIF_ICON_MAP as any)[notification.iconKey] || Bell;
  const raw = notification.raw || {};
  let metadata = raw.metadata || {};
  if (!metadata || typeof metadata !== "object") metadata = {};
  if (!Object.keys(metadata).length && raw.metadata_json) {
    try {
      metadata = JSON.parse(raw.metadata_json || "{}");
    } catch {
      metadata = {};
    }
  }
  const linked = metadata.feedback_uid
    ? { type: "feedback", name: metadata.feedback_uid }
    : raw.target_type
      ? { type: raw.target_type, name: raw.target_id ? `#${raw.target_id}` : raw.alert_key || notification.id }
      : null;
  const metaRows = [
    metadata.feedback_type && { label: "类型", value: metadata.feedback_type },
    metadata.page_path && { label: "页面", value: metadata.page_path },
    metadata.source && { label: "来源", value: metadata.source },
    metadata.has_screenshot !== undefined && { label: "截图", value: metadata.has_screenshot ? "已附加" : "未附加" },
    raw.rule_key && { label: "规则", value: raw.rule_key },
  ].filter(Boolean);
  return e(CenterModal, { onClose, maxWidth: "md" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("h2", { className: "text-sm font-semibold text-white" }, t("通知详情")),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    e("div", { className: "p-5 space-y-4" },
      // Header with icon
      e("div", { className: "flex items-start gap-3" },
        e("div", {
          className: "shrink-0 w-12 h-12 rounded-md flex items-center justify-center",
          style: { background: notification.iconColor + "22" }
        }, e(IconComp, { size: 20, style: { color: notification.iconColor } })),
        e("div", { className: "flex-1 min-w-0" },
          e("div", { className: "text-[13px] font-semibold text-white" }, notification.title),
          e("div", { className: "text-[10px] text-slate-500 mt-0.5" }, notification.time)
        )
      ),
      // Full content
      e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-3" },
        e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-1.5" }, t("完整内容")),
        e("div", { className: "text-[11px] text-slate-300 leading-relaxed" }, notification.desc)
      ),
      // Linked entity
      metaRows.length > 0 && e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-3" },
        e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, t("来源信息")),
        e("div", { className: "grid grid-cols-2 gap-2" },
          metaRows.map((row) => e("div", { key: row.label, className: "min-w-0 rounded border border-white/[0.05] bg-white/[0.02] px-2 py-1.5" },
            e("div", { className: "text-[9px] text-slate-500" }, row.label),
            e("div", { className: "truncate text-[10px] text-slate-300" }, row.value)
          ))
        )
      ),
      linked && e("div", { className: "rounded-md border border-purple-500/[0.2] bg-purple-500/[0.05] p-3" },
        e("div", { className: "text-[10px] uppercase tracking-wider text-purple-300 mb-1.5" }, t("关联实体")),
        e("div", { className: "flex items-center gap-2" },
          e("span", { className: "text-[8px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-300" }, linked.type),
          e("span", { className: "text-[11px] text-white" }, linked.name)
        )
      ),
      // Actions
      e("div", { className: "flex gap-2 pt-1" },
        notification.unread && e("button", { 
          onClick: () => { onMarkRead && onMarkRead(notification.id); onClose(); },
          className: "flex-1 flex items-center justify-center gap-1.5 rounded-md border border-white/[0.12] px-3 py-2 text-[11px] text-slate-300 hover:bg-white/[0.04]"
        }, e(Check, { size: 11 }), t("标记已读")),
        linked && e("button", {
          onClick: () => { onNavigate && onNavigate(linked); onClose(); },
          className: "flex-1 flex items-center justify-center gap-1.5 rounded-md bg-purple-600 hover:bg-purple-500 px-3 py-2 text-[11px] font-medium text-white"
        , title: "跳到关联板块" }, t("跳转到关联"), e(ChevronRight, { size: 11 }))
      )
    )
  );
}
