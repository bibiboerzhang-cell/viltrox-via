// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { ShieldCheck, X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { apiFetch } from "../../../../../services/http";

const e = React.createElement;

export function ProfileModal({ user, onClose, t, apiToken }: any) {
  const [tab, setTab] = useState("basic");
  // 修改密码:接真后端 /api/auth/change-password(验当前密码→设新密码)。
  const [curPw, setCurPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [saving, setSaving] = useState(false);
  const [pwMsg, setPwMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const changePassword = async () => {
    if (saving) return;
    setPwMsg(null);
    if (!curPw || !newPw) { setPwMsg({ ok: false, text: "请填写当前密码和新密码" }); return; }
    if (newPw.length < 8) { setPwMsg({ ok: false, text: "新密码至少 8 位" }); return; }
    if (newPw !== confirmPw) { setPwMsg({ ok: false, text: "两次新密码不一致" }); return; }
    setSaving(true);
    try {
      const res: any = await apiFetch(
        "/api/auth/change-password",
        { method: "POST", body: JSON.stringify({ current_password: curPw, new_password: newPw }) },
        apiToken,
      );
      if (res && res.status === "success") {
        setPwMsg({ ok: true, text: "密码已更新" });
        setCurPw(""); setNewPw(""); setConfirmPw("");
        setTimeout(() => { onClose && onClose(); }, 900);
      } else {
        setPwMsg({ ok: false, text: (res && res.message) || "修改失败" });
      }
    } catch (err: any) {
      setPwMsg({ ok: false, text: String(err && err.message ? err.message : err) });
    } finally { setSaving(false); }
  };
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
          { label: t("角色"),  value: user.role === "admin" ? t("Admin") : t("成员"), editable: false, hint: null },
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
        [
          { label: "当前密码", value: curPw, set: setCurPw },
          { label: "新密码", value: newPw, set: setNewPw },
          { label: "确认新密码", value: confirmPw, set: setConfirmPw },
        ].map((f, i) => e("div", { key: i },
          e("label", { className: "text-[10px] text-slate-500 mb-1 block" }, f.label),
          e("input", {
            type: "password", placeholder: "••••••••", value: f.value,
            onChange: (ev: any) => f.set(ev.target.value),
            className: "w-full rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-[12px] text-white outline-none"
          })
        )),
        e("div", { className: "text-[9px] text-slate-500" }, t("密码至少 8 位 · 含大小写字母和数字")),
        pwMsg && e("div", { className: `text-[10px] ${pwMsg.ok ? "text-emerald-300" : "text-rose-300"}` }, pwMsg.text)
      )
    ),
    e("div", { className: "px-5 py-2.5 border-t border-white/[0.06] flex justify-end gap-2" },
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 px-3 py-1 text-[11px] text-slate-300 hover:bg-white/[0.04]" }, t("取消")),
      tab === "password"
        ? e("button", {
            onClick: changePassword,
            disabled: saving || !curPw || !newPw || !confirmPw,
            title: "修改密码",
            className: `rounded-md px-3 py-1 text-[11px] font-medium ${saving || !curPw || !newPw || !confirmPw ? "bg-purple-600/40 text-white/50 opacity-60 cursor-not-allowed" : "bg-purple-600 text-white hover:bg-purple-500"}`
          }, saving ? "保存中…" : t("保存"))
        : e("button", { disabled: true, title: "待接入(基本信息改名暂未开)", className: "rounded-md bg-purple-600/40 px-3 py-1 text-[11px] font-medium text-white/50 opacity-60 cursor-not-allowed" }, t("保存"))
    )
  );
}
