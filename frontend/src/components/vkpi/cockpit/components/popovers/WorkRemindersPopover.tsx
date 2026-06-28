// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useEffect, useState } from "react";
import { Check, List, Plus, Target, X } from "lucide-react";
import { PopoverWrapper } from "./PopoverWrapper";
import { REMINDER_ICON_MAP } from "../../data/reminderIconMap";
import { ActionInboxPanel } from "../ActionInboxPanel";

const e = React.createElement;

export function WorkRemindersPopover({ onClose, reminders, anchorRef, t, viewingAs, onOpenImport, onViewAll, onMarkAllRead, apiToken }: any) {
  const [activeTab, setActiveTab] = useState("todo");
  const [items, setItems] = useState(reminders);

  useEffect(() => { setItems(reminders); }, [reminders]);

  const filtered = items.filter((r: any) => {
    if (activeTab === "all") return true;
    return r.status === activeTab;
  });
  const todoCount = items.filter((r: any) => r.status === "todo").length;
  const doneCount = items.filter((r: any) => r.status === "done").length;

  const markDone    = (id: any) => setItems((prev: any) => prev.map((r: any) => r.id === id ? { ...r, status: "done" } : r));
  const markIgnored = (id: any) => setItems((prev: any) => prev.map((r: any) => r.id === id ? { ...r, status: "ignored" } : r));
  const markAllRead = () => setItems((prev: any) => prev.map((r: any) => r.status === "todo" ? { ...r, status: "done" } : r));

  const prioColor: any = { high: "#ef4444", medium: "#f59e0b", low: "#64748b" };
  const prioLabel: any = { high: t("高"), medium: t("中"), low: t("低") };
  const sourceLabels: any = {
    system: { label: t("系统"), color: "#64748b" },
    ai:     { label: t("AI"),   color: "#a855f7" },
    manual: { label: t("手动"), color: "#3b82f6" },
    email:  { label: t("邮件"), color: "#06b6d4" },
    table:  { label: t("表格"), color: "#10b981" },
  };
  
  return e(PopoverWrapper, { onClose, anchorRef, width: 420 },
    e("div", { className: "w-[400px] max-h-[80vh] flex flex-col" },
      // Header
      e("div", { className: "px-3 py-2 border-b border-white/[0.06] flex items-center justify-between" },
        e("div", { className: "flex items-center gap-2" },
          e(Target, { size: 13, className: "text-purple-300" }),
          e("div", { className: "text-[11px] font-semibold text-white" }, t("工作提醒")),
          todoCount > 0 && e("span", { className: "text-[9px] px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-300 font-medium" }, `${todoCount} ${t("待跟进")}`)
        ),
        onMarkAllRead && e("button", { onClick: () => { markAllRead(); onMarkAllRead(); }, className: "text-[10px] text-slate-400 hover:text-white" }, t("全部已读"))
      ),
      // 视角提示 banner(impersonating)
      viewingAs && e("div", { className: "px-3 py-1.5 bg-blue-500/[0.06] border-b border-blue-500/[0.12] flex items-center gap-2" },
        e("div", {
          className: "shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold text-white",
          style: { background: viewingAs.color }
        }, viewingAs.avatar),
        e("span", { className: "flex-1 text-[10px] text-blue-200" }, `${t("正在以")} ${viewingAs.name} ${t("的身份查看")}`)
      ),
      // Tabs
      e("div", { className: "px-3 pt-2 flex gap-3 border-b border-white/[0.04]" },
        [
          { key: "all",     label: t("全部"),   count: items.length },
          { key: "todo",    label: t("待跟进"), count: todoCount },
          { key: "done",    label: t("已完成"), count: doneCount },
          { key: "ignored", label: t("已忽略"), count: items.filter((r: any) => r.status === "ignored").length },
        ].map((x: any) => e("button", {
          key: x.key,
          onClick: () => setActiveTab(x.key),
          className: "text-[11px] py-1.5 border-b-2 transition-colors flex items-center gap-1",
          style: {
            borderColor: activeTab === x.key ? "#a855f7" : "transparent",
            color: activeTab === x.key ? "#fff" : "rgba(255,255,255,0.5)",
          }
        }, x.label, e("span", { className: "text-[9px] text-slate-500" }, x.count)))
      ),
      // List
      e("div", { className: "flex-1 overflow-y-auto py-1" },
        filtered.length === 0
          ? e("div", { className: "text-center py-8 text-[11px] text-slate-500" }, t("没有内容"))
          : filtered.map((r: any) => {
              const IconComp = (REMINDER_ICON_MAP as any)[r.iconKey] || Target;
              const sourceCfg = sourceLabels[r.source] || sourceLabels.system;
              return e("div", { 
                key: r.id, 
                className: "px-3 py-2 hover:bg-white/[0.03] transition-colors border-l-2",
                style: { 
                  borderColor: r.status === "todo" ? prioColor[r.priority] : "transparent",
                  opacity: r.status !== "todo" ? 0.5 : 1
                }
              },
                e("div", { className: "flex items-start gap-2.5" },
                  e("div", {
                    className: "shrink-0 w-7 h-7 rounded-md flex items-center justify-center mt-0.5",
                    style: { background: r.iconColor + "20" }
                  }, e(IconComp, { size: 12, style: { color: r.iconColor } })),
                  e("div", { className: "flex-1 min-w-0" },
                    e("div", { className: "flex items-center gap-1.5 mb-0.5" },
                      e("span", { className: "text-[11px] font-medium text-white" }, r.title),
                      r.status === "todo" && e("span", {
                        className: "text-[8px] font-bold px-1 py-0.5 rounded uppercase",
                        style: { background: prioColor[r.priority] + "22", color: prioColor[r.priority] }
                      }, prioLabel[r.priority]),
                      e("span", { 
                        className: "text-[8px] uppercase tracking-wider px-1 py-0.5 rounded",
                        style: { background: sourceCfg.color + "20", color: sourceCfg.color }
                      }, sourceCfg.label)
                    ),
                    e("div", { className: "text-[10px] text-slate-400 mb-1.5" }, r.desc),
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
      ),
      // 今日建议(Action Inbox):2026-06-15 从 Dashboard 右栏迁移至「工作提醒」,保留 approve/dismiss 全功能。
      apiToken ? e("div", { className: "border-t border-white/[0.06] px-2 pt-2 pb-1" },
        e("div", { className: "px-1 pb-1.5 text-[10px] font-semibold text-slate-400" }, t("今日建议")),
        e(ActionInboxPanel, { apiToken }),
      ) : null,
      // Bottom - 单按钮 + 导入任务
      onOpenImport && e("div", { className: "border-t border-white/[0.06] p-2" },
        e("button", { 
          onClick: () => { onClose(); onOpenImport && onOpenImport(); },
          className: "w-full flex items-center justify-center gap-2 px-3 py-2 rounded-md border border-purple-500/30 bg-purple-500/[0.06] hover:bg-purple-500/[0.12] text-[11px] text-purple-200"
        },
          e(Plus, { size: 12 }),
          t("+ 导入任务")
        )
      ),
      e("div", { className: "px-3 py-2 border-t border-white/[0.06] text-center" },
        e("button", { 
          onClick: () => { onClose(); onViewAll && onViewAll(); },
          className: "text-[10px] text-purple-300 hover:text-purple-200" 
        }, t("查看所有提醒 →"))
      )
    )
  );
}
