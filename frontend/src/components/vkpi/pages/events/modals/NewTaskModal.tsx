import React, { useState } from "react";
import { X } from "lucide-react";
import { PHASE_LABELS } from "../shared/constants";
import type { ChecklistItem, TaskVm, UiStaff } from "../shared/types";

const e = React.createElement;

interface NewTaskModalProps {
  phaseDefault?: string;
  staff?: UiStaff[];
  currentUserId?: string;
  onClose: () => void;
  onSubmit: (task: TaskVm) => void;
}

export default function NewTaskModal({ phaseDefault, staff = [], currentUserId = "", onClose, onSubmit }: NewTaskModalProps) {
  const [phase, setPhase] = useState(phaseDefault || "2w");
  const [title, setTitle] = useState("");
  // 负责人 = 真实 staff id(mock 四人组退役);默认当前用户,名单未加载则留空(TasksTab 会改派创建人)。
  const [owner, setOwner] = useState(() => {
    if (currentUserId && staff.some(u => String(u.id) === String(currentUserId))) return String(currentUserId);
    return staff[0] ? String(staff[0].id) : "";
  });
  const [dueDate, setDueDate] = useState("");
  const [kind, setKind] = useState("");
  const [notes, setNotes] = useState("");
  const [checklist, setChecklist] = useState<ChecklistItem[]>([]);
  const [newChecklistText, setNewChecklistText] = useState("");

  function addChecklistItem() {
    if (!newChecklistText.trim()) return;
    setChecklist(prev => [...prev, { label: newChecklistText, done: false }]);
    setNewChecklistText("");
  }
  function removeChecklistItem(i: number) {
    setChecklist(prev => prev.filter((_, idx) => idx !== i));
  }

  return e("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4", onClick: onClose },
    e("div", { className: "rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-lg p-5 max-h-[92vh] overflow-y-auto", onClick: (ev: React.MouseEvent) => ev.stopPropagation() },
      e("div", { className: "flex items-center justify-between mb-4" },
        e("div", null,
          e("h3", { className: "text-[14px] font-semibold text-white" }, "新建任务"),
          e("p", { className: "text-[10.5px] text-slate-500 mt-0.5" }, "手动添加 · 也可用 AI 一键生成模板任务清单")
        ),
        e("button", { onClick: onClose, className: "text-slate-500 hover:text-white" }, e(X, { size: 16 }))
      ),
      e("div", { className: "space-y-3" },
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "任务标题"),
          e("input", { type: "text", value: title, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setTitle(ev.target.value),
            placeholder: "例: 确认展位电源 + 网络需求",
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11.5px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" })
        ),
        e("div", { className: "grid grid-cols-2 gap-3" },
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "阶段"),
            e("select", { value: phase, onChange: (ev: React.ChangeEvent<HTMLSelectElement>) => setPhase(ev.target.value),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-purple-500/40" },
              Object.entries(PHASE_LABELS).map(([k, cfg]) => e("option", { key: k, value: k, style: { background: "#0a0a0d" } }, cfg.label, " ", cfg.dateRange))
            )
          ),
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "截止日期"),
            e("input", { type: "date", value: dueDate, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setDueDate(ev.target.value),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-purple-500/40 [color-scheme:dark]" })
          )
        ),
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1.5 block" }, "负责人"),
          staff.length === 0
            ? e("div", { className: "text-[10.5px] text-slate-500 px-2.5 py-1.5 rounded bg-white/[0.02] border border-white/[0.04]" },
                "员工名单未加载 · 任务将分配给创建人")
            : e("div", { className: "flex flex-wrap gap-1.5" },
                staff.map(u => {
                  const active = owner === String(u.id);
                  return e("button", { key: u.id, onClick: () => setOwner(String(u.id)),
                    className: `px-2.5 py-1 rounded text-[10.5px] border flex items-center gap-1.5 ${active ? "border-purple-500/40 bg-purple-500/10 text-white" : "border-white/[0.06] bg-white/[0.02] text-slate-400 hover:bg-white/[0.04]"}`
                  },
                    e("div", { className: "w-4 h-4 rounded-full flex items-center justify-center text-[8.5px] font-bold text-white", style: { background: u.color || "#94a3b8" } }, u.avatar || String(u.name || "?").slice(0, 1)),
                    u.name
                  );
                })
              )
        ),
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1.5 block" }, "任务类型 (可选)"),
          e("div", { className: "flex gap-1.5" },
            ([["", "通用"], ["equipment", "设备类"], ["materials", "物料类"]] as const).map(([k, l]) =>
              e("button", { key: k, onClick: () => setKind(k),
                className: `flex-1 px-2 py-1 rounded text-[10.5px] border ${kind === k ? "border-purple-500/40 bg-purple-500/10 text-white" : "border-white/[0.06] bg-white/[0.02] text-slate-400 hover:bg-white/[0.04]"}`
              }, l)
            )
          )
        ),
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "Checklist (可选 · 后续展开任务里勾)"),
          e("div", { className: "space-y-1.5" },
            checklist.map((c, i) => e("div", { key: i, className: "flex items-center gap-2 px-2.5 py-1 rounded bg-white/[0.02] border border-white/[0.04]" },
              e("div", { className: "w-3 h-3 rounded border border-white/[0.15]" }),
              e("span", { className: "flex-1 text-[10.5px] text-slate-200" }, c.label),
              e("button", { onClick: () => removeChecklistItem(i), className: "text-slate-500 hover:text-red-300" }, e(X, { size: 11 }))
            )),
            e("div", { className: "flex items-center gap-2" },
              e("input", { type: "text", value: newChecklistText, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setNewChecklistText(ev.target.value),
                onKeyDown: (ev: React.KeyboardEvent) => { if (ev.key === "Enter") { ev.preventDefault(); addChecklistItem(); } },
                placeholder: "+ 添加子项 (Enter 确认)",
                className: "flex-1 px-2.5 py-1.5 rounded bg-white/[0.02] border border-white/[0.06] text-[10.5px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" }),
              e("button", { onClick: addChecklistItem, className: "px-2 py-1.5 rounded text-[10.5px] bg-white/[0.04] hover:bg-white/[0.08] text-slate-300" }, "加")
            )
          )
        ),
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "备注"),
          e("textarea", { value: notes, onChange: (ev: React.ChangeEvent<HTMLTextAreaElement>) => setNotes(ev.target.value), rows: 2,
            placeholder: "例: 需要 220V 电源 × 2 + 千兆网线",
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40 resize-none" })
        )
      ),
      e("div", { className: "flex items-center justify-end gap-2 mt-5 pt-4 border-t border-white/[0.05]" },
        e("button", { onClick: onClose, className: "px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" }, "取消"),
        e("button", {
          disabled: !title || !dueDate,
          onClick: () => onSubmit({
            id: "t_" + Date.now(), phase, title, owner, dueDate: dueDate.slice(5).replace("-", "/"),
            kind: kind || undefined, notes: notes || undefined,
            checklist: checklist.length ? checklist : undefined,
            done: false, collaborators: [],
          }),
          className: `px-3.5 py-1.5 rounded-md text-[11px] font-medium ${title && dueDate ? "bg-purple-500 hover:bg-purple-400 text-white" : "bg-white/[0.05] text-slate-600 cursor-not-allowed"}`
        }, "新建任务")
      )
    )
  );
}
