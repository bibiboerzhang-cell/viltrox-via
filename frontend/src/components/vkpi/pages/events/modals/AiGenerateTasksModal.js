import React, { useState } from "react";
import { ListChecks, X } from "lucide-react";
import { AI_TASK_TEMPLATES } from "../data/ai-templates.js";
import { EVENT_TYPES, PHASE_LABELS, TASK_KINDS } from "../shared/constants.js";

const e = React.createElement;
export default function AiGenerateTasksModal({ ev, existingTitles, onClose, onSubmit }) {
  const template = AI_TASK_TEMPLATES[ev.typeKey] || AI_TASK_TEMPLATES.tradeshow;
  // #23 去假 LLM:这是静态任务模板,不调用任何模型 → 即时呈现、默认勾选 suggested,不再假装"生成中"。
  const [loading] = useState(false);
  const [selected, setSelected] = useState(() => {
    const initial = new Set();
    template.forEach((t, i) => {
      if (t.suggested && !existingTitles.has(t.title)) initial.add(i);
    });
    return initial;
  });
  
  function toggle(i) {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }
  
  function selectAll() {
    setSelected(new Set(template.map((_, i) => i).filter(i => !existingTitles.has(template[i].title))));
  }
  function selectNone() { setSelected(new Set()); }
  
  function submit() {
    const tasks = Array.from(selected).map(i => {
      const t = template[i];
      return {
        id: "t_ai_" + Date.now() + "_" + i,
        phase: t.phase, title: t.title, kind: t.kind || undefined,
        owner: "J", collaborators: [], dueDate: "TBD", done: false,
        notes: "模板生成 · 调整 ddl + 负责人",
      };
    });
    onSubmit(tasks);
  }
  
  return e("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4", onClick: onClose },
    e("div", { className: "rounded-2xl border border-purple-500/30 bg-[#0b1220] w-full max-w-2xl p-5 max-h-[92vh] flex flex-col", onClick: ev2 => ev2.stopPropagation() },
      e("div", { className: "flex items-center justify-between mb-4" },
        e("div", { className: "flex items-center gap-2.5" },
          e("div", { className: "w-9 h-9 rounded-lg flex items-center justify-center", style: { background: "linear-gradient(135deg, #a855f7, #06b6d4)" } },
            e(ListChecks,{ size: 16, className: "text-white" })
          ),
          e("div", null,
            e("h3", { className: "text-[14px] font-semibold text-white" }, "任务模板"),
            e("p", { className: "text-[10.5px] text-slate-500 mt-0.5" },
              "基于 ", (EVENT_TYPES[ev.typeKey] || EVENT_TYPES.other).label, " · ", ev.title, " · 时间节奏自动推荐"
            )
          )
        ),
        e("button", { onClick: onClose, className: "text-slate-500 hover:text-white" }, e(X, { size: 16 }))
      ),
      
      loading
        ? e("div", { className: "py-12 flex flex-col items-center gap-3" },
            e(ListChecks,{ size: 24, className: "text-purple-300 animate-pulse" }),
            e("div", { className: "text-[11.5px] text-purple-200" }, "正在生成模板任务..."),
            e("div", { className: "text-[10px] text-slate-500" }, "结合 event 类型 + 倒计时 + 历史模板")
          )
        : e(React.Fragment, null,
            e("div", { className: "flex items-center justify-between mb-3" },
              e("div", { className: "text-[10.5px] text-slate-400" }, 
                "已选 ", e("span", { className: "text-purple-300 font-medium" }, selected.size), " / ", template.length, " 项"
              ),
              e("div", { className: "flex items-center gap-2" },
                e("button", { onClick: selectAll, className: "text-[10px] text-purple-300 hover:text-purple-200" }, "全选"),
                e("span", { className: "text-slate-600" }, "·"),
                e("button", { onClick: selectNone, className: "text-[10px] text-slate-400 hover:text-white" }, "全不选")
              )
            ),
            e("div", { className: "flex-1 overflow-y-auto space-y-1.5 -mx-1 px-1" },
              Object.keys(PHASE_LABELS).map(phaseKey => {
                const phaseTasks = template.map((t, i) => ({ ...t, _idx: i })).filter(t => t.phase === phaseKey);
                if (!phaseTasks.length) return null;
                return e("div", { key: phaseKey },
                  e("div", { className: "text-[9.5px] text-slate-500 uppercase tracking-wider px-2 py-1 sticky top-0 bg-[#0b1220] z-10" },
                    PHASE_LABELS[phaseKey].label, " ", e("span", { className: "text-slate-600" }, PHASE_LABELS[phaseKey].dateRange)
                  ),
                  phaseTasks.map(t => {
                    const isSel = selected.has(t._idx);
                    const existed = existingTitles.has(t.title);
                    return e("label", { key: t._idx, className: `flex items-center gap-2.5 px-2.5 py-1.5 rounded cursor-pointer ${existed ? "opacity-40" : isSel ? "bg-purple-500/10 border border-purple-500/30" : "border border-white/[0.04] hover:bg-white/[0.02]"}` },
                      e("input", { type: "checkbox", checked: isSel, disabled: existed, onChange: () => toggle(t._idx), className: "accent-purple-500" }),
                      e("span", { className: "flex-1 text-[11px] text-white" }, t.title),
                      existed && e("span", { className: "text-[9px] text-slate-500" }, "已存在"),
                      t.kind && (() => {
                        const kc = TASK_KINDS[t.kind];
                        const KI = kc.icon;
                        return e("span", { className: "text-[9px] px-1 py-0.5 rounded flex items-center gap-0.5", style: { background: kc.color + "20", color: kc.color } },
                          e(KI, { size: 8 }), kc.label
                        );
                      })(),
                      t.suggested && !existed && e("span", { className: "text-[9px] text-purple-300 px-1 py-0.5 rounded bg-purple-500/15" }, "推荐")
                    );
                  })
                );
              })
            ),
            e("div", { className: "flex items-center justify-between gap-2 mt-4 pt-3 border-t border-white/[0.05]" },
              e("div", { className: "text-[9.5px] text-slate-500" },
                e(ListChecks,{ size: 9, className: "inline mr-1 text-purple-300" }),
                "导入后 ddl 设为 TBD · 你可以单独调整"
              ),
              e("div", { className: "flex items-center gap-2" },
                e("button", { onClick: onClose, className: "px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" }, "取消"),
                e("button", {
                  disabled: selected.size === 0,
                  onClick: submit,
                  className: `px-3.5 py-1.5 rounded-md text-[11px] font-medium ${selected.size > 0 ? "bg-purple-500 hover:bg-purple-400 text-white" : "bg-white/[0.05] text-slate-600 cursor-not-allowed"}`
                }, "导入 ", selected.size, " 个任务")
              )
            )
          )
    )
  );
}
