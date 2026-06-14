import React, { useEffect, useState } from "react";
import { Plus, Users, X } from "lucide-react";
import {
  inviteKolToEvent, removeKolFromEvent, fromUiInviteCreate,
} from "../../../../../services/vkpi/events-api";
import DeleteConfirmModal from "../modals/DeleteConfirmModal.js";
import InviteKolModal from "../modals/InviteKolModal.js";
import { kolById } from "../shared/lookups.js";

const e = React.createElement;
export default function KolsTab({ ev, token, invites = [], loading, error, reload }) {
  const [kols, setKols] = useState(invites);
  const [showInvite, setShowInvite] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [opError, setOpError] = useState("");

  // 父级 reload 后 props.invites 变化 → 同步本地乐观态
  useEffect(() => { setKols(invites); }, [invites]);

  const onErr = (label) => (err) => {
    setOpError(label + ":" + String(err && err.message ? err.message : err));
    reload && reload();
  };

  function removeKol(inviteId) {
    setDeleting(null);
    setKols(prev => prev.filter(k => k.id !== inviteId));  // 乐观
    removeKolFromEvent(token, ev.id, inviteId)
      .then(() => reload && reload())
      .catch(onErr("移除邀请失败"));
  }

  function inviteKols(newKols) {
    setShowInvite(false);
    // InviteKolModal 给的是 [{ id(=kol id), status }];映射成 kolId 再创建
    Promise.all((newKols || []).map(k =>
      inviteKolToEvent(token, ev.id, fromUiInviteCreate({ kolId: k.id, status: k.status }))
    ))
      .then(() => reload && reload())
      .catch(onErr("邀请 KOL 失败"));
  }

  return e("div", { className: "p-5" },
    (error || opError) && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-rose-500/30 bg-rose-500/10 text-[11px] text-rose-200" }, "⚠ ", opError || error),
    loading && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-white/[0.06] bg-white/[0.02] text-[11px] text-slate-400" }, "加载 KOL 邀请中…"),
    e("div", { className: "flex items-center justify-between mb-3" },
      e("div", { className: "text-[11px] text-slate-500" }, "已邀请 ", kols.length, " 位 · 确认 ", kols.filter(k => k.status === "confirmed").length),
      e("button", {
        onClick: () => setShowInvite(true),
        className: "px-2.5 py-1 rounded-md bg-purple-500/90 hover:bg-purple-500 text-white text-[10.5px] font-medium flex items-center gap-1"
      },
        e(Plus, { size: 11 }), "邀请 KOL"
      )
    ),
    kols.length === 0
      ? e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-8 text-center" },
          e(Users, { size: 24, className: "text-slate-600 mx-auto mb-2" }),
          e("div", { className: "text-[11px] text-slate-400" }, "还没邀请 KOL"),
          e("div", { className: "text-[10px] text-slate-500 mt-1" }, "点右上 「邀请 KOL」 从 KOL Pool 选择")
        )
      : e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] overflow-hidden" },
          kols.map((ik, i) => {
            const k = kolById(ik.kolId);
            if (!k) return null;
            const cfg = ik.status === "confirmed" ? { c: "#10b981", label: "已确认" }
                      : ik.status === "pending"   ? { c: "#fbbf24", label: "待回复" }
                      : { c: "#ef4444", label: "拒绝" };
            return e("div", { key: ik.id, className: "px-4 py-2.5 flex items-center gap-3 border-b border-white/[0.04] last:border-0 hover:bg-white/[0.02] group" },
              e("div", { className: "w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-bold text-white shrink-0", style: { background: "linear-gradient(135deg, #a855f7, #06b6d4)" } }, k.name.charAt(0)),
              e("div", { className: "flex-1" },
                e("div", { className: "flex items-center gap-2" },
                  e("div", { className: "text-[12px] text-white font-medium" }, k.name),
                  e("span", { className: "text-[10px] text-slate-500" }, k.handle),
                  e("span", { className: "text-[9.5px] px-1.5 py-0.5 rounded font-medium", style: { background: cfg.c + "20", color: cfg.c } }, cfg.label)
                ),
                e("div", { className: "text-[10px] text-slate-400 mt-0.5" },
                  k.platform, " · ", (k.followers/1000).toFixed(0), "K followers",
                  ik.days && ik.days !== "—" && e("span", null, " · 到场 ", ik.days),
                  ik.travel && e("span", null, " · ", ik.travel)
                )
              ),
              e("button", {
                onClick: () => setDeleting({ id: ik.id, name: k.name }),
                className: "opacity-0 group-hover:opacity-100 text-slate-500 hover:text-red-300 p-1",
                title: "移除邀请"
              }, e(X, { size: 12 }))
            );
          })
        ),
    showInvite && e(InviteKolModal, {
      ev,
      existingKolIds: new Set(kols.map(k => k.kolId)),
      onClose: () => setShowInvite(false),
      onSubmit: inviteKols,
    }),
    deleting && e(DeleteConfirmModal, {
      title: `移除 "${deleting.name}" 的邀请?`,
      subtitle: "如果对方已确认到场,你需要单独通知",
      onClose: () => setDeleting(null),
      onConfirm: () => removeKol(deleting.id),
    })
  );
}
