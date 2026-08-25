// Share KOL modal — P-GROUP-7「共享 KOL 池」。把某条 My KOL(kol_pool_id)显式共享给成员。
// 镜像 ShareModal.tsx 的写法与样式(React.createElement 风格 + 成员多选 + 已共享列表 + 增/删),
// 但 KOL 共享是「只读授予」一档(无 viewer/editor 角色),故无 role select。
//
// 后端:vkpi_kol_pool_members(迁移 159)。被共享成员的 MY KOL 视图会自动并入这条 KOL(只读)。
//
// props:
//   kolPoolId: string           —— 该 KOL 的 pool id(vkpi_kol_pool.id)
//   kolName:   string           —— 标题展示
//   staff:     UiStaff[]        —— 复用 app 已有的真实成员列表(选人用),不另拉
//   apiToken:  string
//   onClose:   () => void
//
// 诚实状态:loading / empty / error 都显式呈现;无权限(write tab)增删会被后端 403,
// 这里把后端报错原样 surfaced(不假装成功、不本地伪造成员)。

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Share2, Trash2, UserPlus, X } from "lucide-react";
import {
  listKolShareMembers,
  shareKolToStaff,
  unshareKolFromStaff,
  type VkpiKolShareMember,
} from "../../../services/vkpi/kol-api";
import { useModalFocusContract } from "../cockpit/components/modals/modalFocus";

const e = React.createElement;

export function shareKolErrorMessage(err: any): string {
  if (!err) return "操作失败";
  const code = String(err.detail || err.message || "").trim();
  const messages: Record<string, string> = {
    staff_identity_required: "当前账号未绑定有效员工身份，无法共享。",
    my_kol_share_write_forbidden: "无权共享：仅管理层、本人收藏者或当前负责人可操作；他人共享给你的 KOL 不能转分享。",
    share_recipient_self: "不能把 KOL 共享给自己。",
    share_recipient_not_found: "目标成员不存在或已删除。",
    share_recipient_pending: "该成员账号仍在待批准状态，暂不能接收共享。",
    share_recipient_inactive: "该成员已停用或被暂停，暂不能接收共享。",
  };
  if (messages[code]) return messages[code];
  if (err.status === 403) {
    return "当前账号无权共享：需具备 VKPI 写权限，且必须是管理层、本人收藏者或当前负责人。";
  }
  return String(err.message || err);
}

function unwrapMembers(res: unknown): VkpiKolShareMember[] {
  if (Array.isArray(res)) return res as VkpiKolShareMember[];
  if (res && typeof res === "object") {
    const r = res as Record<string, unknown>;
    if (Array.isArray(r.items)) return r.items as VkpiKolShareMember[];
    if (Array.isArray(r.members)) return r.members as VkpiKolShareMember[];
  }
  return [];
}

export function isShareStaffPickable(s: any, memberIds: Set<string>, actorStaffId?: unknown): boolean {
  const id = String(s?.id ?? s?.staff_id ?? "");
  if (!id || memberIds.has(id) || (actorStaffId != null && id === String(actorStaffId))) return false;
  if (s?.active === false || s?.suspendedAt || s?.suspended_at) return false;
  const status = String(s?.verificationStatus ?? s?.verification_status ?? "").trim().toLowerCase();
  return !["pending", "invited", "inactive", "disabled", "suspended"].includes(status);
}

export function ShareKolModal({ kolPoolId, kolName, staff = [], actorStaffId, apiToken, onClose }: any) {
  const titleId = React.useId();
  const dialogRef = useModalFocusContract<HTMLDivElement>({ onClose });
  const [members, setMembers] = useState<VkpiKolShareMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // member-picker 状态
  const [pickStaffId, setPickStaffId] = useState("");
  const [adding, setAdding] = useState(false);
  const [removingId, setRemovingId] = useState("");
  const [actionError, setActionError] = useState("");

  const reload = useCallback(() => {
    if (!apiToken || !kolPoolId) {
      setMembers([]);
      setLoading(false);
      setError(apiToken ? "" : "缺少 API token,无法读取成员。");
      return Promise.resolve();
    }
    setLoading(true);
    setError("");
    return listKolShareMembers(apiToken, String(kolPoolId))
      .then((res) => setMembers(unwrapMembers(res)))
      .catch((err) => setError(shareKolErrorMessage(err)))
      .finally(() => setLoading(false));
  }, [apiToken, kolPoolId]);

  useEffect(() => {
    let alive = true;
    if (!apiToken || !kolPoolId) {
      setMembers([]);
      setLoading(false);
      setError(apiToken ? "" : "缺少 API token,无法读取成员。");
      return;
    }
    setLoading(true);
    setError("");
    listKolShareMembers(apiToken, String(kolPoolId))
      .then((res) => { if (alive) setMembers(unwrapMembers(res)); })
      .catch((err) => { if (alive) setError(shareKolErrorMessage(err)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [apiToken, kolPoolId]);

  // 已共享的 staff_id 集合(从 picker 里隐藏)
  const memberIds = useMemo(
    () => new Set(members.map((m) => String(m.staff_id ?? "")).filter(Boolean)),
    [members],
  );
  const pickable = useMemo(
    () => (Array.isArray(staff) ? staff : []).filter((s: any) => isShareStaffPickable(s, memberIds, actorStaffId)),
    [staff, memberIds, actorStaffId],
  );

  const nameForStaff = useCallback(
    (sid: any) => {
      const s = (Array.isArray(staff) ? staff : []).find((x: any) => String(x.id) === String(sid));
      return s ? s.name : String(sid);
    },
    [staff],
  );

  const handleAdd = useCallback(async () => {
    if (!pickStaffId || adding) return;
    setAdding(true);
    setActionError("");
    try {
      await shareKolToStaff(apiToken, String(kolPoolId), pickStaffId);
      setPickStaffId("");
      await reload();
    } catch (err) {
      setActionError(shareKolErrorMessage(err));
    } finally {
      setAdding(false);
    }
  }, [apiToken, kolPoolId, pickStaffId, adding, reload]);

  const handleRemove = useCallback(async (sid: any) => {
    setRemovingId(String(sid));
    setActionError("");
    try {
      await unshareKolFromStaff(apiToken, String(kolPoolId), sid);
      await reload();
    } catch (err) {
      setActionError(shareKolErrorMessage(err));
    } finally {
      setRemovingId("");
    }
  }, [apiToken, kolPoolId, reload]);

  return e("div", {
    className: "fixed inset-0 z-[9999] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4",
    role: "presentation",
    onClick: onClose,
  },
    e("div", {
      ref: dialogRef,
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": titleId,
      tabIndex: -1,
      className: "rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-lg max-h-[92vh] flex flex-col shadow-2xl",
      onClick: (ev: any) => ev.stopPropagation(),
    },
      // Header
      e("div", { className: "flex items-center justify-between px-5 py-4 border-b border-white/[0.06]" },
        e("div", { className: "flex items-center gap-2.5 min-w-0" },
          e("div", { className: "w-8 h-8 rounded-lg flex items-center justify-center border border-purple-500/30 bg-purple-500/[0.08] shrink-0" },
            e(Share2, { size: 15, className: "text-purple-300" })),
          e("div", { className: "min-w-0" },
            e("h3", { id: titleId, className: "text-[14px] font-semibold text-white truncate" }, "共享 KOL 给成员"),
            e("p", { className: "text-[10.5px] text-slate-500 truncate" }, kolName || String(kolPoolId))
          )
        ),
        e("button", {
          type: "button",
          onClick: onClose,
          "aria-label": "关闭共享 KOL",
          "data-modal-initial-focus": "",
          className: "text-slate-500 hover:text-white shrink-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-purple-300",
        }, e(X, { size: 16 }))
      ),

      // Body
      e("div", { className: "flex-1 overflow-y-auto px-5 py-4 space-y-4" },
        // 已共享成员
        e("div", null,
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "已共享给"),
          loading
            ? e("div", { className: "flex items-center gap-2 py-6 justify-center text-[11px] text-slate-500" },
                e(Loader2, { size: 14, className: "animate-spin" }), "加载成员中…")
            : error
              ? e("div", { className: "rounded-lg border border-red-500/20 bg-red-500/[0.05] px-3 py-3 text-[11px] text-red-300" }, error)
              : members.length === 0
                ? e("div", { className: "text-center py-6 text-[11px] text-slate-500 rounded-lg border border-white/[0.04] bg-white/[0.01]" }, "还没有共享给任何成员")
                : e("div", { className: "space-y-1.5" },
                    members.map((m: any) => {
                      const sid = String(m.staff_id ?? "");
                      const name = m.name || nameForStaff(sid);
                      const busy = removingId === sid;
                      return e("div", {
                        key: sid || String(m.id),
                        className: "flex items-center gap-3 px-3 py-2 rounded-lg border border-white/[0.05] bg-white/[0.02]",
                      },
                        e("div", {
                          className: "w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white shrink-0",
                          style: { background: "linear-gradient(135deg, #a855f7, #06b6d4)" },
                        }, String(name || "?").slice(0, 1).toUpperCase()),
                        e("div", { className: "flex-1 min-w-0" },
                          e("div", { className: "text-[12px] text-white truncate" }, name)
                        ),
                        e("span", {
                          className: "text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0",
                          style: { background: "rgba(168,85,247,0.15)", color: "#c4b5fd" },
                        }, "只读"),
                        e("button", {
                          onClick: () => handleRemove(sid),
                          disabled: busy,
                          title: "撤销共享",
                          className: "shrink-0 p-1 rounded text-slate-500 hover:text-red-300 hover:bg-red-500/10 disabled:opacity-40",
                        }, busy ? e(Loader2, { size: 13, className: "animate-spin" }) : e(Trash2, { size: 13 }))
                      );
                    })
                  )
        ),

        // 添加成员
        !loading && !error && e("div", null,
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "共享给成员"),
          e("div", { className: "flex items-center gap-2" },
            e("select", {
              value: pickStaffId,
              onChange: (ev: any) => setPickStaffId(ev.target.value),
              className: "flex-1 min-w-0 rounded-md border border-white/[0.08] bg-white/[0.03] px-2.5 py-1.5 text-[11px] text-slate-200 outline-none focus:border-purple-500/40",
            },
              e("option", { value: "", style: { background: "#0b1220" } }, pickable.length ? "选择成员…" : "无可共享成员"),
              ...pickable.map((s: any) => e("option", { key: s.id, value: String(s.id), style: { background: "#0b1220" } }, s.name))
            ),
            e("button", {
              onClick: handleAdd,
              disabled: !pickStaffId || adding,
              className: `shrink-0 px-3 py-1.5 rounded-md text-[11px] font-medium flex items-center gap-1.5 ${pickStaffId && !adding ? "bg-purple-500 hover:bg-purple-400 text-white" : "bg-white/[0.05] text-slate-600 cursor-not-allowed"}`,
            },
              adding ? e(Loader2, { size: 12, className: "animate-spin" }) : e(UserPlus, { size: 12 }),
              "共享")
          ),
          actionError && e("div", { className: "mt-2 rounded-lg border border-red-500/20 bg-red-500/[0.05] px-3 py-2 text-[11px] text-red-300" }, actionError),
          e("div", { className: "mt-2 text-[10px] text-slate-600" },
            "共享 = 让该成员在自己的 MY KOL 里只读看见这条 KOL,不改归属/收藏/认领。")
        )
      ),

      // Footer
      e("div", { className: "flex items-center justify-end px-5 py-3 border-t border-white/[0.06]" },
        e("button", {
          onClick: onClose,
          className: "px-3 py-1.5 rounded-md border border-white/[0.08] text-[11px] text-slate-300 hover:bg-white/[0.04]",
        }, "完成")
      )
    )
  );
}

export default ShareKolModal;
