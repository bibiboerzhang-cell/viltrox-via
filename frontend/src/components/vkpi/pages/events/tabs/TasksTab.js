import React, { useEffect, useState } from "react";
import { AlertCircle, Check, ChevronRight, ListChecks, Plus, ShieldCheck, X } from "lucide-react";
import {
  addEventTask, updateEventTask, deleteEventTask, fromUiTaskCreate,
} from "../../../../../services/vkpi/events-api";
import { TEAM } from "../data/team.js";
import AiGenerateTasksModal from "../modals/AiGenerateTasksModal.js";
import DeleteConfirmModal from "../modals/DeleteConfirmModal.js";
import NewTaskModal from "../modals/NewTaskModal.js";
import { EQUIP_SOURCE, ITEM_STATUS, MAT_SOURCE, PHASE_LABELS, TASK_KINDS } from "../shared/constants.js";
import { ownerByInitial } from "../shared/lookups.js";

const e = React.createElement;
export default function TasksTab({ ev, currentUser, token, tasks = [], loading, error, reload }) {
  const phases = ["4w", "2w", "1w", "live", "after", "prep"];
  const [tasksState, setTasksState] = useState(tasks);
  const [expandedId, setExpandedId] = useState(null);
  const [addCollabFor, setAddCollabFor] = useState(null);
  const [showNew, setShowNew] = useState(false);
  const [showAi, setShowAi] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [opError, setOpError] = useState("");

  // 父级 reload 后 props.tasks 变化 → 同步本地乐观态
  useEffect(() => { setTasksState(tasks); }, [tasks]);

  const onErr = (label) => (err) => {
    setOpError(label + ":" + String(err && err.message ? err.message : err));
    reload && reload();  // 失败回滚到服务器真值
  };

  function deleteTask(id) {
    setDeleting(null);
    setTasksState(prev => prev.filter(t => t.id !== id));  // 乐观
    deleteEventTask(token, ev.id, id)
      .then(() => reload && reload())
      .catch(onErr("删除任务失败"));
  }

  // 任务板:能进这个 Event 就看完整任务板 → 渲染全部任务;owner 仅作每条「归属」展示,不再用来隐藏。
  // (旧 own-only 过滤把 owner='J' 占位等"非本人"任务全藏了 → 20 项只显示 2 项、新建任务看不到 的根因)
  const visible = tasksState;

  function toggleTask(id) {
    const cur = tasksState.find(t => t.id === id);
    if (!cur) return;
    const newDone = !cur.done;
    setTasksState(prev => prev.map(t => {
      if (t.id !== id) return t;
      return {
        ...t, done: newDone,
        doneAt: newDone ? "今天" : undefined,
        doneBy: newDone ? currentUser.initial : undefined,  // ★ 记录实际点的人
      };
    }));
    updateEventTask(token, ev.id, id, { done: newDone, done_by: newDone ? currentUser.initial : "" })
      .then(() => reload && reload())
      .catch(onErr("更新任务失败"));
  }

  function toggleChecklistItem(taskId, itemIdx) {
    const cur = tasksState.find(t => t.id === taskId);
    if (!cur) return;
    const newChecklist = (cur.checklist || []).map((c, i) =>
      i === itemIdx ? { ...c, done: !c.done } : c
    );
    setTasksState(prev => prev.map(t => t.id === taskId ? { ...t, checklist: newChecklist } : t));
    updateEventTask(token, ev.id, taskId, { checklist: newChecklist })
      .then(() => reload && reload())
      .catch(onErr("更新 checklist 失败"));
  }

  function addCollaborator(taskId, userInitial) {
    setAddCollabFor(null);
    const cur = tasksState.find(t => t.id === taskId);
    if (!cur) return;
    const existing = cur.collaborators || [];
    if (existing.includes(userInitial)) return;
    const next = [...existing, userInitial];
    setTasksState(prev => prev.map(t => t.id === taskId ? { ...t, collaborators: next } : t));
    updateEventTask(token, ev.id, taskId, { collaborators: next })
      .then(() => reload && reload())
      .catch(onErr("添加协作者失败"));
  }

  function createTask(t) {
    setShowNew(false);
    // NewTaskModal owner 默认写死 "J"(占位,无对应真人)→ 归属错人。占位/空 → 改派当前用户。
    const owned = { ...t, owner: (t.owner && t.owner !== "J") ? t.owner : ((currentUser && currentUser.initial) || "All") };
    addEventTask(token, ev.id, fromUiTaskCreate(owned))
      .then(() => reload && reload())
      .catch(onErr("新建任务失败"));
  }

  function createTasksBulk(list) {
    setShowAi(false);
    // 模板任务默认 owner 写死占位 "J" → 任务列表按 owner 过滤,当前用户看不到(就是「没有显示」)。
    // 改派给当前用户,导入者立刻看得到、可再分派。
    const mine = (list || []).map(t => ({ ...t, owner: (currentUser && currentUser.initial) || t.owner }));
    Promise.all(mine.map(t => addEventTask(token, ev.id, fromUiTaskCreate(t))))
      .then(() => reload && reload())
      .catch(onErr("生成任务失败"));
  }
  
  const doneCount = visible.filter(t => t.done).length;

  return e("div", { className: "p-5" },
    // 真后端状态横幅
    (error || opError) && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-rose-500/30 bg-rose-500/10 text-[11px] text-rose-200" }, "⚠ ", opError || error),
    loading && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-white/[0.06] bg-white/[0.02] text-[11px] text-slate-400" }, "加载任务中…"),
    // 头部进度
    e("div", { className: "mb-4 rounded-xl border border-purple-500/20 bg-purple-500/[0.04] p-3 flex items-center justify-between gap-3" },
      e("div", { className: "flex-1" },
        e("div", { className: "text-[11px] text-purple-200 font-semibold mb-0.5 flex items-center gap-1.5" },
          "任务进度 ",
          e("span", { className: "text-[9.5px] px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-200" },
            "视角: ", currentUser.name
          )
        ),
        e("div", { className: "text-[10px] text-slate-400" }, doneCount, " / ", visible.length, " 完成 · 全部 ", tasksState.length, " 项")
      ),
      // 按钮组
      e("div", { className: "flex items-center gap-2" },
        e("button", {
          onClick: () => setShowAi(true),
          className: "px-2.5 py-1.5 rounded-md text-[10.5px] font-medium flex items-center gap-1.5 border border-purple-500/40 hover:border-purple-500/60 text-purple-200 hover:bg-purple-500/10",
          style: { background: "linear-gradient(135deg, rgba(168,85,247,0.10), rgba(6,182,212,0.06))" }
        }, e(ListChecks, { size: 11 }), "任务模板"),
        e("button", {
          onClick: () => setShowNew(true),
          className: "px-2.5 py-1.5 rounded-md bg-purple-500/90 hover:bg-purple-500 text-white text-[10.5px] font-medium flex items-center gap-1"
        }, e(Plus, { size: 11 }), "新建任务"),
        e("div", { className: "text-right ml-2 hidden sm:block" },
          e("div", { className: "text-[20px] font-bold text-white tabular-nums leading-none" }, visible.length ? Math.round(doneCount / visible.length * 100) : 0, "%"),
          e("div", { className: "w-24 h-1 rounded-full bg-white/[0.04] overflow-hidden mt-1.5" },
            e("div", { className: "h-full rounded-full transition-all", style: {
              width: (visible.length ? doneCount / visible.length * 100 : 0) + "%",
              background: "linear-gradient(90deg, #a855f7, #06b6d4)"
            } })
          )
        )
      )
    ),
    
    visible.length === 0 && e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-10 text-center" },
      e(ShieldCheck, { size: 28, className: "text-slate-600 mx-auto mb-2" }),
      e("div", { className: "text-[11.5px] text-slate-400" }, "这个 Event 还没有任务"),
      e("div", { className: "text-[10px] text-slate-500 mt-1" }, "点「任务模板」一键导入,或「新建任务」手动添加")
    ),
    
    // 分阶段任务
    visible.length > 0 && e("div", { className: "space-y-3" },
      phases.map(phase => {
        const phaseTasks = visible.filter(t => t.phase === phase);
        if (phaseTasks.length === 0) return null;
        const phaseDone = phaseTasks.filter(t => t.done).length;
        const cfg = PHASE_LABELS[phase];
        const allDone = phaseDone === phaseTasks.length;
        const inProgress = phaseDone > 0 && phaseDone < phaseTasks.length;
        
        return e("div", { key: phase, className: "rounded-lg border border-white/[0.06] bg-white/[0.012] overflow-hidden" },
          // phase header
          e("div", {
            className: "px-3 py-2 flex items-center justify-between",
            style: { background: allDone ? "rgba(16,185,129,0.04)" : inProgress ? "rgba(168,85,247,0.04)" : "rgba(255,255,255,0.02)" }
          },
            e("div", { className: "flex items-center gap-2" },
              e("div", {
                className: "w-1.5 h-1.5 rounded-full",
                style: { background: allDone ? "#10b981" : inProgress ? "#a855f7" : "#475569" }
              }),
              e("span", { className: "text-[12px] font-semibold text-white" }, cfg.label),
              e("span", { className: "text-[10px] text-slate-500" }, cfg.dateRange)
            ),
            e("div", { className: "text-[10.5px] tabular-nums" },
              e("span", { className: allDone ? "text-emerald-300" : inProgress ? "text-purple-200" : "text-slate-500" },
                phaseDone, " / ", phaseTasks.length
              )
            )
          ),
          // tasks
          e("div", { className: "divide-y divide-white/[0.03]" },
            phaseTasks.map(t => {
              const owner = t.owner === "All" ? null : ownerByInitial(t.owner);
              const doneBy = t.doneBy ? ownerByInitial(t.doneBy) : null;
              const collabs = (t.collaborators || []).map(c => ownerByInitial(c));
              const hasDetail = t.checklist || t.details || t.notes;
              const expanded = expandedId === t.id;
              
              return e("div", { key: t.id, className: "hover:bg-white/[0.012] group" },
                // 主行
                e("div", { className: "px-3 py-2 flex items-center gap-2.5" },
                  // checkbox
                  e("button", {
                    onClick: () => toggleTask(t.id),
                    className: `w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-all ${t.done ? "bg-emerald-500 border-emerald-500" : t.alert === "warn" ? "border-amber-400/60 hover:bg-amber-500/10" : "border-white/[0.15] hover:bg-white/[0.05]"}`
                  }, t.done && e(Check, { size: 10, className: "text-white" })),
                  
                  // title + alert + kind chip
                  e("div", {
                    className: "flex-1 min-w-0 cursor-pointer",
                    onClick: () => hasDetail ? setExpandedId(expanded ? null : t.id) : null
                  },
                    e("div", { className: "flex items-center gap-2 flex-wrap" },
                      e("span", { className: `text-[11.5px] ${t.done ? "text-slate-500 line-through" : "text-white"}` }, t.title),
                      t.kind && (() => {
                        const kc = TASK_KINDS[t.kind];
                        if (!kc) return null;  // 未知 kind(如通用 "task")无配置 → 不渲染标,避免 undefined.icon 整页崩
                        const KI = kc.icon;
                        return e("span", { className: "text-[9px] px-1 py-0.5 rounded font-medium flex items-center gap-0.5", style: { background: kc.color + "20", color: kc.color } },
                          e(KI, { size: 8 }), kc.label
                        );
                      })(),
                      t.alert === "warn" && !t.done && e("span", { className: "text-[9px] text-amber-300 px-1 py-0.5 rounded bg-amber-500/15 flex items-center gap-0.5" },
                        e(AlertCircle, { size: 9 }), "近 ddl"
                      ),
                      hasDetail && e(ChevronRight, { size: 10, className: `text-slate-500 transition-transform ${expanded ? "rotate-90" : ""}` })
                    )
                  ),
                  
                  // collaborators avatars
                  collabs.length > 0 && e("div", { className: "flex items-center gap-0.5" },
                    collabs.map((u, i) => e("div", {
                      key: u.initial,
                      className: "w-4 h-4 rounded-full flex items-center justify-center text-[8px] font-bold text-white",
                      style: { background: u.color, opacity: 0.7, marginLeft: i > 0 ? "-3px" : 0 },
                      title: "协作 " + u.name
                    }, u.initial))
                  ),
                  
                  // owner avatar (assigned)
                  owner ? e("div", {
                    className: "w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold text-white shrink-0",
                    style: { background: owner.color, opacity: t.done ? 0.4 : 1 },
                    title: "分配 " + owner.name
                  }, owner.initial)
                  : e("div", { className: "text-[9px] text-slate-500 shrink-0 px-1.5 py-0.5 rounded bg-white/[0.04]" }, "All"),
                  
                  // doneBy badge (实际点的人,如果跟 owner 不一致)
                  t.done && doneBy && doneBy.initial !== t.owner && e("div", { className: "flex items-center gap-0.5 text-[9px] text-slate-500" },
                    e("span", null, "→"),
                    e("div", {
                      className: "w-4 h-4 rounded-full flex items-center justify-center text-[8px] font-bold text-white",
                      style: { background: doneBy.color },
                      title: "实际完成 " + doneBy.name
                    }, doneBy.initial)
                  ),
                  
                  // ddl
                  e("div", { className: `text-[10px] tabular-nums shrink-0 ${t.done ? "text-slate-600" : t.alert === "warn" ? "text-amber-300" : "text-slate-400"}` },
                    t.done ? "✓ " + (t.doneAt || "完成") : t.dueDate
                  ),
                  // delete 按钮 (hover 出现)
                  e("button", {
                    onClick: (ev2) => { ev2.stopPropagation(); setDeleting({ type: "task", id: t.id, title: t.title }); },
                    className: "opacity-0 group-hover:opacity-100 text-slate-500 hover:text-red-300 p-0.5 shrink-0",
                    title: "删除任务"
                  }, e(X, { size: 11 }))
                ),
                
                // 展开区
                expanded && hasDetail && e("div", { className: "px-3 pb-3 pl-10 space-y-2" },
                  // notes
                  t.notes && e("div", { className: "text-[10.5px] text-slate-300 px-2.5 py-1.5 rounded bg-white/[0.02] border border-white/[0.04]" },
                    e("span", { className: "text-slate-500 mr-1.5" }, "备注:"), t.notes
                  ),
                  
                  // checklist
                  t.checklist && e("div", { className: "rounded border border-white/[0.04] bg-white/[0.012] divide-y divide-white/[0.03]" },
                    e("div", { className: "px-2.5 py-1.5 text-[9.5px] text-slate-500 font-medium uppercase tracking-wider" }, "Checklist"),
                    t.checklist.map((c, i) => e("div", { key: i, className: "px-2.5 py-1.5 flex items-center gap-2" },
                      e("button", {
                        onClick: () => toggleChecklistItem(t.id, i),
                        className: `w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0 transition-all ${c.done ? "bg-emerald-500/80 border-emerald-500" : "border-white/[0.15]"}`
                      }, c.done && e(Check, { size: 8, className: "text-white" })),
                      e("span", { className: `text-[10.5px] flex-1 ${c.done ? "text-slate-500 line-through" : "text-slate-200"}` }, c.label),
                      c.value && e("span", { className: "text-[10px] text-cyan-300 font-mono" }, c.value)
                    ))
                  ),
                  
                  // equipment details
                  t.kind === "equipment" && t.details?.items && e("div", { className: "rounded border border-white/[0.04] bg-white/[0.012] overflow-hidden" },
                    e("div", { className: "px-2.5 py-1.5 text-[9.5px] text-slate-500 font-medium uppercase tracking-wider bg-slate-500/5" }, "设备清单"),
                    e("div", { className: "divide-y divide-white/[0.03]" },
                      t.details.items.map((item, i) => {
                        const src = EQUIP_SOURCE[item.source] || { label: item.source, color: "#94a3b8" };
                        const st = ITEM_STATUS[item.status] || { label: item.status, color: "#94a3b8" };
                        return e("div", { key: i, className: "px-2.5 py-2 flex items-center gap-2 text-[10.5px]" },
                          e("div", { className: "flex-1 min-w-0" },
                            e("div", { className: "text-white font-medium truncate" }, item.name),
                            item.note && e("div", { className: "text-[9.5px] text-slate-500 mt-0.5" }, item.note)
                          ),
                          e("span", { className: "text-[9.5px] tabular-nums text-slate-400" }, "× ", item.qty),
                          e("span", { className: "text-[9px] px-1.5 py-0.5 rounded font-medium shrink-0", style: { background: src.color + "20", color: src.color } }, src.label),
                          e("span", { className: "text-[9px] px-1.5 py-0.5 rounded font-medium shrink-0", style: { background: st.color + "20", color: st.color } }, st.label)
                        );
                      })
                    )
                  ),
                  
                  // materials details
                  t.kind === "materials" && t.details?.items && e("div", { className: "rounded border border-white/[0.04] bg-white/[0.012] overflow-hidden" },
                    e("div", { className: "px-2.5 py-1.5 text-[9.5px] text-slate-500 font-medium uppercase tracking-wider bg-slate-500/5" }, "物料清单"),
                    e("div", { className: "divide-y divide-white/[0.03]" },
                      t.details.items.map((item, i) => {
                        const src = MAT_SOURCE[item.source] || { label: item.source, color: "#94a3b8" };
                        const st = ITEM_STATUS[item.status] || { label: item.status, color: "#94a3b8" };
                        return e("div", { key: i, className: "px-2.5 py-2 flex items-center gap-2 text-[10.5px]" },
                          e("div", { className: "flex-1 min-w-0" },
                            e("div", { className: "text-white font-medium truncate" }, item.name),
                            item.note && e("div", { className: "text-[9.5px] text-slate-500 mt-0.5" }, item.note)
                          ),
                          e("span", { className: "text-[9.5px] tabular-nums text-slate-400" }, "× ", item.qty),
                          e("span", { className: "text-[9px] px-1.5 py-0.5 rounded font-medium shrink-0", style: { background: src.color + "20", color: src.color } }, src.label),
                          e("span", { className: "text-[9px] px-1.5 py-0.5 rounded font-medium shrink-0", style: { background: st.color + "20", color: st.color } }, st.label)
                        );
                      })
                    )
                  ),
                  
                  // 协作者管理 + add 按钮
                  e("div", { className: "flex items-center gap-2 px-2.5" },
                    e("span", { className: "text-[9.5px] text-slate-500" }, "协作者:"),
                    collabs.length === 0 && e("span", { className: "text-[10px] text-slate-500" }, "无"),
                    collabs.map(u => e("div", { key: u.initial, className: "px-1.5 py-0.5 rounded text-[10px] flex items-center gap-1", style: { background: u.color + "20", color: u.color } },
                      e("div", { className: "w-3 h-3 rounded-full flex items-center justify-center text-[7.5px] font-bold text-white", style: { background: u.color } }, u.initial),
                      u.name
                    )),
                    addCollabFor === t.id
                      ? e("div", { className: "flex items-center gap-1" },
                          TEAM.filter(u => u.initial !== t.owner && !(t.collaborators || []).includes(u.initial)).map(u =>
                            e("button", {
                              key: u.id,
                              onClick: () => addCollaborator(t.id, u.initial),
                              className: "w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold text-white hover:scale-110 transition-transform",
                              style: { background: u.color },
                              title: "+ " + u.name
                            }, u.initial)
                          ),
                          e("button", { onClick: () => setAddCollabFor(null), className: "text-[10px] text-slate-500 hover:text-white" }, "取消")
                        )
                      : e("button", {
                          onClick: () => setAddCollabFor(t.id),
                          className: "text-[10px] text-purple-300 hover:text-purple-200 px-1.5 py-0.5 rounded hover:bg-purple-500/10 flex items-center gap-0.5"
                        }, e(Plus, { size: 9 }), "加人"
                      )
                  )
                )
              );
            })
          )
        );
      })
    ),
    
    // Modals
    showNew && e(NewTaskModal, {
      onClose: () => setShowNew(false),
      onSubmit: createTask,
    }),
    showAi && e(AiGenerateTasksModal, {
      ev,
      existingTitles: new Set(tasksState.map(t => t.title)),
      onClose: () => setShowAi(false),
      onSubmit: createTasksBulk,
    }),
    deleting && e(DeleteConfirmModal, {
      title: `删除任务 "${deleting.title}"?`,
      subtitle: "任务的所有备注 / checklist / 设备清单都会一起删除",
      onClose: () => setDeleting(null),
      onConfirm: () => deleteTask(deleting.id),
    })
  );
}
