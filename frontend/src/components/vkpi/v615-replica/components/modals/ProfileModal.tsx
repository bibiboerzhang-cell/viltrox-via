// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { ShieldCheck, X } from "lucide-react";
import { Avatar } from "../Avatar";
import { CenterModal } from "./CenterModal";

const e = React.createElement;

export function ProfileModal({ user, onClose, t }) {
  const [tab, setTab] = useState("basic");
  return e(CenterModal, { onClose, maxWidth: "md" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("h2", { className: "text-sm font-semibold text-white" }, t("个人资料")),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    e("div", { className: "p-5 space-y-4" },
      // Avatar
      e("div", { className: "flex items-center gap-4" },
        e("div", {
          className: "shrink-0 w-16 h-16 rounded-full flex items-center justify-center text-[20px] font-bold text-white",
          style: { background: user.avatarGradient }
        }, user.avatar),
        e("div", { className: "flex-1" },
          // 2026-06-12 死按钮诚实化:更换头像 / 保存 无写接口 → disabled+待接入
          e("button", { disabled: true, title: "待接入", className: "rounded-md border border-white/[0.12] px-3 py-1.5 text-[11px] text-slate-500 opacity-60 cursor-not-allowed" }, t("更换头像")),
          e("div", { className: "text-[10px] text-slate-500 mt-1.5" }, "JPG / PNG · 最大 2MB")
        )
      ),
      // Tabs
      e("div", { className: "border-b border-white/[0.06] flex gap-4" },
        [
          { key: "basic",    label: "基本信息" },
          { key: "password", label: "修改密码" },
        ].map(x => e("button", {
          key: x.key, onClick: () => setTab(x.key),
          className: "text-[11px] py-2 px-0.5 border-b-2",
          style: { borderColor: tab === x.key ? "#a855f7" : "transparent", color: tab === x.key ? "#fff" : "rgba(255,255,255,0.5)" }
        }, x.label))
      ),
      // Tab content
      tab === "basic" && e("div", { className: "space-y-3" },
        [
          { label: t("姓名"),  value: user.name, editable: true,  hint: null },
          { label: t("邮箱"),  value: user.email, editable: false, hint: t("如需修改请联系 Admin") },
          { label: t("角色"),  value: user.role === "admin" ? t("Admin") : t("员工"), editable: false, hint: null },
          { label: t("部门"),  value: "Marketing", editable: false, hint: null },
        ].map((f, i) => e("div", { key: i },
          e("label", { className: "text-[10px] text-slate-500 mb-1 flex items-center justify-between" }, 
            e("span", null, f.label),
            !f.editable && e("span", { className: "flex items-center gap-1 text-[9px] text-slate-600" },
              e(ShieldCheck, { size: 9 }), t("锁定")
            )
          ),
          e("input", { 
            type: "text", defaultValue: f.value,
            disabled: !f.editable,
            className: "w-full rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-[12px] text-white outline-none disabled:opacity-60 disabled:cursor-not-allowed"
          }),
          f.hint && e("div", { className: "text-[9px] text-slate-500 mt-1" }, f.hint)
        ))
      ),
      tab === "password" && e("div", { className: "space-y-3" },
        ["当前密码", "新密码", "确认新密码"].map((label, i) => e("div", { key: i },
          e("label", { className: "text-[10px] text-slate-500 mb-1 block" }, label),
          e("input", { 
            type: "password", placeholder: "••••••••",
            className: "w-full rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-[12px] text-white outline-none"
          })
        )),
        e("div", { className: "text-[9px] text-slate-500" }, t("密码至少 8 位 · 含大小写字母和数字"))
      )
    ),
    e("div", { className: "px-5 py-2.5 border-t border-white/[0.06] flex justify-end gap-2" },
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 px-3 py-1 text-[11px] text-slate-300 hover:bg-white/[0.04]" }, t("取消")),
      e("button", { disabled: true, title: "待接入", className: "rounded-md bg-purple-600/40 px-3 py-1 text-[11px] font-medium text-white/50 opacity-60 cursor-not-allowed" }, t("保存"))
    )
  );
}
