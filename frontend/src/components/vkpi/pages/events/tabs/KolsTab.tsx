import React, { useEffect, useState } from "react";
import { Link2, Plus, Users, X } from "lucide-react";
import {
  inviteKolToEvent, removeKolFromEvent, fromUiInviteCreate,
} from "../../../../../services/vkpi/events-api";
import { listMarketingKols } from "../../../../../services/vkpi/kol-api";
import DeleteConfirmModal from "../modals/DeleteConfirmModal";
import InviteKolModal, { type InviteKolSubmitItem } from "../modals/InviteKolModal";
import { GoaffproLinkSection } from "../../../shared/GoaffproLinkSection";
import type { EventVm, InviteVm, KolPoolEntry } from "../shared/types";

const e = React.createElement;

function normKol(k: Record<string, any>): KolPoolEntry {
  const id = String(k.id || k.kol_pool_id || k.kolPoolId || "");
  return {
    id,
    name: String(k.display_name || k.name || k.handle || id || "KOL"),
    handle: String(k.handle || ""),
    platform: String(k.platform || ""),
    followers: Number(k.follower_count || k.followers || k.fans || 0),
  };
}

interface KolsTabProps {
  ev: EventVm;
  token: string;
  invites?: InviteVm[];
  loading?: boolean;
  error?: string;
  reload?: () => void;
}

export default function KolsTab({ ev, token, invites = [], loading, error, reload }: KolsTabProps) {
  const [kols, setKols] = useState<InviteVm[]>(invites);
  const [showInvite, setShowInvite] = useState(false);
  const [deleting, setDeleting] = useState<{ id: string; name: string } | null>(null);
  const [opError, setOpError] = useState("");
  const [kolMap, setKolMap] = useState<Record<string, KolPoolEntry>>({});       // 真 KOL 池 id -> {name,handle,...}
  const [linkOpen, setLinkOpen] = useState<string | null>(null);  // 展开追踪链的 invite id

  useEffect(() => { setKols(invites); }, [invites]);

  // 接真 KOL 池:拉一次建 id->KOL 映射,解析邀约的 kol_id 显示名/平台(取代 mock lookups)。
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listMarketingKols(token, { limit: 300 })
      .then((res: any) => {
        if (cancelled) return;
        const map: Record<string, KolPoolEntry> = {};
        (Array.isArray(res && res.kols) ? res.kols : []).forEach((r: Record<string, any>) => {
          const k = normKol(r);
          if (k.id) map[k.id] = k;
        });
        setKolMap(map);
      })
      .catch(() => { /* 解析失败时回退 minimal 显示 */ });
    return () => { cancelled = true; };
  }, [token]);

  const onErr = (label: string) => (err: any) => {
    setOpError(label + ":" + String(err && err.message ? err.message : err));
    reload && reload();
  };

  function removeKol(inviteId: string) {
    setDeleting(null);
    setKols((prev) => prev.filter((k) => k.id !== inviteId));
    removeKolFromEvent(token, ev.id, inviteId)
      .then(() => reload && reload())
      .catch(onErr("移除邀请失败"));
  }

  function inviteKols(newKols: InviteKolSubmitItem[]) {
    setShowInvite(false);
    Promise.all((newKols || []).map((k) =>
      inviteKolToEvent(token, ev.id, fromUiInviteCreate({ kolId: k.id, status: k.status }))
    ))
      .then(() => reload && reload())
      .catch(onErr("邀请 KOL 失败"));
  }

  return e("div", { className: "p-5" },
    (error || opError) && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-rose-500/30 bg-rose-500/10 text-[11px] text-rose-200" }, "⚠ ", opError || error),
    loading && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-white/[0.06] bg-white/[0.02] text-[11px] text-slate-400" }, "加载 KOL 邀请中…"),
    e("div", { className: "flex items-center justify-between mb-3" },
      e("div", { className: "text-[11px] text-slate-500" }, "已邀请 ", kols.length, " 位 · 确认 ", kols.filter((k) => k.status === "confirmed").length),
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
          e("div", { className: "text-[10px] text-slate-500 mt-1" }, "点右上 「邀请 KOL」 从真实 KOL Pool 选择")
        )
      : e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] overflow-hidden" },
          kols.map((ik) => {
            const k = kolMap[String(ik.kolId)] || { name: String(ik.kolId || "KOL"), handle: "", platform: "", followers: 0 };
            const cfg = ik.status === "confirmed" ? { c: "#10b981", label: "已确认" }
                      : ik.status === "pending"   ? { c: "#fbbf24", label: "待回复" }
                      : { c: "#ef4444", label: "拒绝" };
            const isLinkOpen = linkOpen === ik.id;
            return e("div", { key: ik.id, className: "border-b border-white/[0.04] last:border-0" },
              e("div", { className: "px-4 py-2.5 flex items-center gap-3 hover:bg-white/[0.02] group" },
                e("div", { className: "w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-bold text-white shrink-0", style: { background: "linear-gradient(135deg, #a855f7, #06b6d4)" } }, (k.name || "K").charAt(0)),
                e("div", { className: "flex-1 min-w-0" },
                  e("div", { className: "flex items-center gap-2" },
                    e("div", { className: "text-[12px] text-white font-medium truncate" }, k.name),
                    k.handle && e("span", { className: "text-[10px] text-slate-500" }, k.handle),
                    e("span", { className: "text-[9.5px] px-1.5 py-0.5 rounded font-medium", style: { background: cfg.c + "20", color: cfg.c } }, cfg.label)
                  ),
                  e("div", { className: "text-[10px] text-slate-400 mt-0.5" },
                    k.platform || "—", k.followers ? " · " + (k.followers / 1000).toFixed(0) + "K followers" : "",
                    ik.days && ik.days !== "—" && e("span", null, " · 到场 ", ik.days),
                    ik.travel && e("span", null, " · ", ik.travel)
                  )
                ),
                e("button", {
                  onClick: () => setLinkOpen(isLinkOpen ? null : ik.id),
                  className: `text-[10px] px-2 py-1 rounded-md border flex items-center gap-1 ${isLinkOpen ? "border-teal-400/40 bg-teal-400/10 text-teal-200" : "border-white/[0.08] text-slate-400 hover:text-teal-200 hover:border-teal-400/30"}`,
                  title: "为该 KOL 生成活动追踪链"
                }, e(Link2, { size: 11 }), "追踪链"),
                e("button", {
                  onClick: () => setDeleting({ id: ik.id, name: k.name }),
                  className: "opacity-0 group-hover:opacity-100 text-slate-500 hover:text-red-300 p-1",
                  title: "移除邀请"
                }, e(X, { size: 12 }))
              ),
              isLinkOpen && ik.kolId
                ? e("div", { className: "px-2 pb-2" },
                    e(GoaffproLinkSection, { apiToken: token, kolPoolId: ik.kolId, product: (ev && (ev.productName || ev.productSku)) || undefined })
                  )
                : null
            );
          })
        ),
    showInvite && e(InviteKolModal, {
      token,
      ev,
      existingKolIds: new Set(kols.map((k) => String(k.kolId))),
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
