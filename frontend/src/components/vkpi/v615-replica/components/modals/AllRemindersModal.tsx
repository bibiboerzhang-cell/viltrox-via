// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { Check, Search, Target, X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";
import { REMINDER_ICON_MAP } from "../../data/reminderIconMap";

const e = React.createElement;

export function AllRemindersModal({ reminders, onClose, viewingAs }) {
  const { t } = useT();
  const [tab, setTab] = useState("todo");
  const [items, setItems] = useState(reminders);
  const [search, setSearch] = useState("");
  
  const filtered = items.filter(r => 
    (tab === "all" || r.status === tab) &&
    (!search || r.title.toLowerCase().includes(search.toLowerCase()) || r.desc.toLowerCase().includes(search.toLowerCase()))
  );
  const todoCount = items.filter(r => r.status === "todo").length;
  const markDone    = (id) => setItems(prev => prev.map(r => r.id === id ? { ...r, status: "done" } : r));
  const markIgnored = (id) => setItems(prev => prev.map(r => r.id === id ? { ...r, status: "ignored" } : r));
  
  const prioColor = { high: "#ef4444", medium: "#f59e0b", low: "#64748b" };
  const sourceLabels = {
    system: { label: t("系统"), color: "#64748b" },
    ai:     { label: t("AI"),   color: "#a855f7" },
    manual: { label: t("手动"), color: "#3b82f6" },
    email:  { label: t("邮件"), color: "#06b6d4" },
    table:  { label: t("表格"), color: "#10b981" },
  };
  
  return e(CenterModal, { onClose, maxWidth: "2xl" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("div", null,
        e("div", { className: "flex items-center gap-2" },
          e("h2", { className: "text-sm font-semibold text-white" }, t("全部工作提醒")),
          todoCount > 0 && e("span", { className: "text-[9px] px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-300 font-medium" }, `${todoCount} ${t("待跟进")}`)
        ),
        viewingAs && e("div", { className: "text-[10px] text-blue-300 mt-0.5" }, `${t("正在以")} ${viewingAs.name} ${t("的身份查看")}`)
      ),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    // Search + tabs
    e("div", { className: "px-5 py-2 border-b border-white/[0.06] space-y-2" },
      e("input", { 
        type: "text", value: search, onChange: (ev) => setSearch(ev.target.value),
        placeholder: t("搜索") + "...",
        className: "w-full rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-[12px] text-white outline-none"
      }),
      e("div", { className: "flex gap-3" },
        [
          { key: "all",     label: t("全部"),   count: items.length },
          { key: "todo",    label: t("待跟进"), count: todoCount },
          { key: "done",    label: t("已完成"), count: items.filter(r => r.status === "done").length },
          { key: "ignored", label: t("已忽略"), count: items.filter(r => r.status === "ignored").length },
        ].map(x => e("button", {
          key: x.key, onClick: () => setTab(x.key),
          className: "text-[11px] py-1.5 px-0.5 border-b-2 flex items-center gap-1",
          style: { borderColor: tab === x.key ? "#a855f7" : "transparent", color: tab === x.key ? "#fff" : "rgba(255,255,255,0.5)" }
        }, x.label, e("span", { className: "text-[9px] text-slate-500" }, x.count)))
      )
    ),
    e("div", { className: "p-3 max-h-[60vh] overflow-y-auto" },
      filtered.length === 0
        ? e("div", { className: "text-center py-8 text-[11px] text-slate-500" }, t("没有内容"))
        : e("div", { className: "space-y-1" },
            filtered.map(r => {
              const IconComp = REMINDER_ICON_MAP[r.iconKey] || Target;
              const sourceCfg = sourceLabels[r.source] || sourceLabels.system;
              return e("div", { 
                key: r.id,
                className: "px-3 py-2 rounded-md hover:bg-white/[0.03] border-l-2",
                style: { borderColor: r.status === "todo" ? prioColor[r.priority] : "transparent", opacity: r.status !== "todo" ? 0.5 : 1 }
              },
                e("div", { className: "flex items-start gap-2.5" },
                  e("div", {
                    className: "shrink-0 w-7 h-7 rounded-md flex items-center justify-center mt-0.5",
                    style: { background: r.iconColor + "20" }
                  }, e(IconComp, { size: 12, style: { color: r.iconColor } })),
                  e("div", { className: "flex-1 min-w-0" },
                    e("div", { className: "flex items-center gap-1.5 mb-0.5" },
                      e("span", { className: "text-[11px] font-medium text-white" }, r.title),
                      e("span", { 
                        className: "text-[8px] uppercase tracking-wider px-1 py-0.5 rounded",
                        style: { background: sourceCfg.color + "20", color: sourceCfg.color }
                      }, sourceCfg.label)
                    ),
                    e("div", { className: "text-[10px] text-slate-400 mb-1" }, r.desc),
                    e("div", { className: "flex items-center gap-2" },
                      e("span", { className: "text-[9px] text-slate-500" }, r.time),
                      r.status === "todo" && e("div", { className: "ml-auto flex items-center gap-1" },
                        e("button", { 
                          onClick: () => markDone(r.id),
                          className: "flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25 text-[9px]"
                        }, e(Check, { size: 9 }), t("完成")),
                        e("button", { 
                          onClick: () => markIgnored(r.id),
                          className: "flex items-center gap-1 px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-400 hover:bg-white/[0.08] text-[9px]"
                        }, e(X, { size: 9 }), t("忽略"))
                      )
                    )
                  )
                )
              );
            })
          )
    )
  );
}
