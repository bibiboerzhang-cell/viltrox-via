import React, { useState } from "react";
import { X } from "lucide-react";
import { PROJECTS } from "../data/projects.js";
import { EVENT_TYPES } from "../shared/constants.js";

const e = React.createElement;
export default function NewEventModal({ initialData, onClose, onSubmit, teamOptions = [], currentUserId }) {
  // 真员工(UiStaff[])喂团队成员选择器,替换 mock TEAM。
  const team = Array.isArray(teamOptions) ? teamOptions : [];
  const isEdit = !!initialData;
  const [title, setTitle] = useState(initialData?.title || "");
  const [typeKey, setTypeKey] = useState(initialData?.typeKey || "tradeshow");
  const [startDate, setStartDate] = useState(initialData?.startDate || "");
  const [endDate, setEndDate] = useState(initialData?.endDate || "");
  const [city, setCity] = useState(initialData?.location?.city || "");
  const [country, setCountry] = useState(initialData?.location?.country || "");
  const [locName, setLocName] = useState(initialData?.location?.name || "");
  const [budget, setBudget] = useState(initialData?.budgetTotal || 20000);
  const [teamIds, setTeamIds] = useState(initialData?.teamUserIds || (currentUserId ? [String(currentUserId)] : (team[0] ? [String(team[0].id)] : [])));
  const [projectIds, setProjectIds] = useState(initialData?.relatedProjectIds || []);
  const [note, setNote] = useState(initialData?.note || "");
  const [autoCategories, setAutoCategories] = useState(!isEdit);
  
  const toggleTeam = (id) => setTeamIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  const toggleProject = (id) => setProjectIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  
  return e("div", {
    className: "fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4",
    onClick: onClose
  },
    e("div", { className: "rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-2xl max-h-[92vh] overflow-y-auto p-5", onClick: ev => ev.stopPropagation() },
      e("div", { className: "flex items-center justify-between mb-5" },
        e("div", null,
          e("h3", { className: "text-[14px] font-semibold text-white" }, isEdit ? "编辑 Event" : "新建 Event"),
          e("p", { className: "text-[10.5px] text-slate-500 mt-0.5" }, isEdit ? "修改 event 基础信息 / 预算 / 团队" : "创建后可在「任务」tab 自动生成模板任务清单")
        ),
        e("button", { onClick: onClose, className: "text-slate-500 hover:text-white" }, e(X, { size: 16 }))
      ),
      
      e("div", { className: "space-y-3" },
        // 类型选择 (icon button group)
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1.5 block" }, "类型"),
          e("div", { className: "grid grid-cols-5 gap-1.5" },
            Object.entries(EVENT_TYPES).map(([k, cfg]) => {
              const I = cfg.icon;
              const active = typeKey === k;
              return e("button", {
                key: k, onClick: () => setTypeKey(k),
                className: `py-2 px-1 rounded-lg border flex flex-col items-center gap-1 transition-all ${active ? "bg-white/[0.05]" : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]"}`,
                style: active ? { borderColor: cfg.color + "60", background: cfg.color + "15" } : {}
              },
                e(I, { size: 14, style: { color: active ? cfg.color : "#94a3b8" } }),
                e("span", { className: "text-[9.5px]", style: { color: active ? cfg.color : "#94a3b8" } }, cfg.label)
              );
            })
          )
        ),
        
        // 名称
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "名称"),
          e("input", {
            type: "text", value: title, onChange: ev => setTitle(ev.target.value),
            placeholder: "例: CineGear LA 2026",
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11.5px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40"
          })
        ),
        
        // 时间 (起止)
        e("div", { className: "grid grid-cols-2 gap-3" },
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "开始日期"),
            e("input", { type: "date", value: startDate, onChange: ev => setStartDate(ev.target.value),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-purple-500/40 [color-scheme:dark]" })
          ),
          e("div", null,
            e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "结束日期"),
            e("input", { type: "date", value: endDate, onChange: ev => setEndDate(ev.target.value),
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white focus:outline-none focus:border-purple-500/40 [color-scheme:dark]" })
          )
        ),
        
        // 地点
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "地点"),
          e("div", { className: "space-y-2" },
            e("input", { type: "text", value: locName, onChange: ev => setLocName(ev.target.value),
              placeholder: "场地/会议中心名称 (例: LA Convention Center)",
              className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" }),
            e("div", { className: "grid grid-cols-2 gap-2" },
              e("input", { type: "text", value: city, onChange: ev => setCity(ev.target.value), placeholder: "城市",
                className: "px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" }),
              e("input", { type: "text", value: country, onChange: ev => setCountry(ev.target.value), placeholder: "国家代码 (US / DE / JP)",
                className: "px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40" })
            )
          )
        ),
        
        // 预算
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "初始预算 (USD)"),
          e("div", { className: "flex items-center gap-2" },
            e("div", { className: "relative flex-1" },
              e("span", { className: "absolute left-3 top-1/2 -translate-y-1/2 text-[11px] text-slate-500" }, "$"),
              e("input", { type: "number", value: budget, onChange: ev => setBudget(parseInt(ev.target.value)||0),
                className: "w-full pl-6 pr-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11.5px] text-white tabular-nums focus:outline-none focus:border-purple-500/40" })
            ),
            e("label", { className: "flex items-center gap-1.5 text-[10.5px] text-slate-300 cursor-pointer" },
              e("input", { type: "checkbox", checked: autoCategories, onChange: ev => setAutoCategories(ev.target.checked), className: "accent-purple-500" }),
              "按「", (EVENT_TYPES[typeKey] || EVENT_TYPES.other).label, "」模板自动分类"
            )
          )
        ),
        
        // 团队
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1.5 block" }, "团队成员 (可多选,第一个 = 负责人)"),
          e("div", { className: "flex flex-wrap gap-1.5" },
            team.map(u => {
              const active = teamIds.includes(u.id);
              return e("button", {
                key: u.id, onClick: () => toggleTeam(u.id),
                className: `px-2.5 py-1 rounded text-[10.5px] border flex items-center gap-1.5 transition-all ${active ? "border-purple-500/40 bg-purple-500/10 text-white" : "border-white/[0.06] bg-white/[0.02] text-slate-400 hover:bg-white/[0.04]"}`
              },
                e("div", { className: "w-4 h-4 rounded-full flex items-center justify-center text-[8.5px] font-bold text-white", style: { background: u.color } }, u.avatar),
                u.name
              );
            })
          )
        ),
        
        // Project 关联
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1.5 block" }, "关联 Project (可选, 多选)"),
          e("div", { className: "flex flex-wrap gap-1.5" },
            PROJECTS.map(p => {
              const active = projectIds.includes(p.id);
              return e("button", {
                key: p.id, onClick: () => toggleProject(p.id),
                className: `px-2 py-1 rounded text-[10px] border ${active ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-200" : "border-white/[0.06] bg-white/[0.02] text-slate-400 hover:bg-white/[0.04]"}`
              }, p.title);
            })
          )
        ),
        
        // 备注
        e("div", null,
          e("label", { className: "text-[10.5px] text-slate-400 mb-1 block" }, "备注 (可选)"),
          e("textarea", { value: note, onChange: ev => setNote(ev.target.value),
            placeholder: "例: 重点展示 135mm LAB + 27mm T2.0",
            rows: 2,
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40 resize-none" })
        )
      ),
      
      e("div", { className: "flex items-center justify-end gap-2 mt-5 pt-4 border-t border-white/[0.05]" },
        e("button", { onClick: onClose, className: "px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]" }, "取消"),
        e("button", {
          disabled: !title || !startDate,
          onClick: () => onSubmit({ title, typeKey, startDate, endDate, locName, city, country, budget, teamIds, ownerId: teamIds[0] || (currentUserId ? String(currentUserId) : (team[0]?.id || "")), projectIds, note }),
          className: `px-3.5 py-1.5 rounded-md text-[11px] font-medium ${title && startDate ? "bg-purple-500 hover:bg-purple-400 text-white" : "bg-white/[0.05] text-slate-600 cursor-not-allowed"}`
        }, isEdit ? "保存修改" : "创建 Event")
      )
    )
  );
}
