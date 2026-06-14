// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { Bell, Target, TrendingUp, Users, X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";

const e = React.createElement;

export function EditGroupModal({ groupName = "KOL Operations", mode = "edit", staff, initialMembers, initialDesc, permissions, onClose, onSave }) {
  const { t } = useT();
  const [name, setName] = useState(groupName);
  const [desc, setDesc] = useState(mode === "new" ? "" : (initialDesc || ""));
  const [members, setMembers] = useState(mode === "new" ? [] : (Array.isArray(initialMembers) ? initialMembers : []));
  const toggleMember = (id) => setMembers(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  const save = () => {
    onSave && onSave({ mode, name, desc, members });
    onClose();
  };
  return e(CenterModal, { onClose, maxWidth: "lg" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("h2", { className: "text-sm font-semibold text-white" }, mode === "new" ? t("新建分组") : t("编辑分组")),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    e("div", { className: "p-5 space-y-4 max-h-[65vh] overflow-y-auto" },
      // Name + description
      e("div", null,
        e("label", { className: "text-[10px] text-slate-500 mb-1 block" }, t("组名")),
        e("input", { type: "text", value: name, onChange: (ev) => setName(ev.target.value),
          className: "w-full rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-[12px] text-white outline-none" })
      ),
      e("div", null,
        e("label", { className: "text-[10px] text-slate-500 mb-1 block" }, t("组描述")),
        e("textarea", { value: desc, onChange: (ev) => setDesc(ev.target.value), rows: 2,
          className: "w-full rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-[11px] text-white outline-none resize-none" })
      ),
      // Members
      e("div", null,
        e("div", { className: "flex items-center justify-between mb-2" },
          e("label", { className: "text-[10px] text-slate-500" }, t("成员列表")),
          e("button", { className: "text-[10px] text-purple-300 hover:text-purple-200", onClick: () => document.getElementById("edit-group-member-list")?.scrollIntoView({ behavior: "smooth" }) }, "+ " + t("添加成员"))
        ),
        e("div", { id: "edit-group-member-list", className: "space-y-1" },
          staff.map(s => {
            const isMember = members.includes(s.id);
            return e("label", {
              key: s.id,
              className: "flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-white/[0.03] cursor-pointer"
            },
              e("input", { type: "checkbox", checked: isMember, onChange: () => toggleMember(s.id),
                className: "shrink-0 accent-purple-500" }),
              e("div", { 
                className: "shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white",
                style: { background: s.color }
              }, s.avatar),
              e("div", { className: "flex-1 min-w-0" },
                e("div", { className: "text-[11px] text-white" }, s.name),
                e("div", { className: "text-[10px] text-slate-500" }, s.title)
              ),
              (s.isAdmin || s.role === "admin" || s.role === "owner") && e("span", { className: "text-[8px] uppercase tracking-wider px-1 py-0.5 rounded bg-purple-500/15 text-purple-300" }, "Admin")
            );
          })
        )
      ),
      // Sharing & collaboration scope
      e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-3 space-y-2" },
        e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, t("组级权限")),
        [
          { icon: Target,   label: t("共享 Projects"),     value: (permissions && (permissions.shared_projects || []).join(" / ")) || "135mm LAB / CineGear / 56mm 复推" },
          { icon: Users,    label: t("共享 KOL 池"),       value: (permissions && permissions.shared_kol_pool) || t("Top performers(78 人)") },
          { icon: TrendingUp, label: t("共同 KPI 目标"),   value: (permissions && permissions.kpi_goal) || t("Q2 新增 50 个高活 KOL") },
          { icon: Bell,     label: t("内部 @ 提醒规则"),   value: (permissions && permissions.reminder_rule) || t("组内变更自动通知") },
        ].map((row, i) => e("div", { key: i, className: "flex items-start gap-2.5" },
          e(row.icon, { size: 11, className: "text-slate-400 shrink-0 mt-0.5" }),
          e("div", { className: "flex-1 min-w-0" },
            e("div", { className: "text-[11px] text-white" }, row.label),
            e("div", { className: "text-[10px] text-slate-400" }, row.value)
          ),
          e("button", { className: "text-white/25", disabled: true, title: "权限编辑待接入" }, t("编辑"))
        ))
      )
    ),
    e("div", { className: "px-5 py-2.5 border-t border-white/[0.06] flex justify-end gap-2" },
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 px-3 py-1 text-[11px] text-slate-300 hover:bg-white/[0.04]" }, t("取消")),
      e("button", { onClick: save, className: "rounded-md bg-purple-600 hover:bg-purple-500 px-3 py-1 text-[11px] font-medium text-white" }, t("保存分组"))
    )
  );
}
