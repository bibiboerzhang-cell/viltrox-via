// Share modal — 把 Project / Event 的成员分享接到真后端(member CRUD 已就绪)。
// 同时被 v615 (.tsx, React.createElement 风格) 和 events (.js) 两套世界复用,
//
// props:
//   kind: 'project' | 'event'   —— 决定走哪套 API
//   targetId: string            —— project_id / event_id
//   targetName: string          —— 标题展示
//   staff: UiStaff[]            —— 复用 app 已有的真实成员列表(toUiStaffList 产物),不另拉
//   apiToken: string
//   onClose: () => void
//
// 诚实状态:loading / empty / error 都显式呈现;非 owner/admin 增删会被后端 403,
// 这里把后端报错原样surfaced(不假装成功、不本地伪造成员)。

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Share2, Trash2, UserPlus, X } from "lucide-react";
import {
  addProjectMember,
  listProjectMembers,
  removeProjectMember,
  unwrapMembers,
} from "../../../services/vkpi/projectMembers-api";
import {
  addEventMember,
  listEventMembers,
  removeEventMember,
} from "../../../services/vkpi/eventMembers-api";

const e = React.createElement;

const ROLE_LABEL = { viewer: "查看", editor: "编辑" };

function errMessage(err: any) {
  if (!err) return "操作失败";
  if (err.status === 403) return "无权限:只有项目/活动的 owner 或 admin 可以管理成员。";
  return String(err.message || err);
}

export function ShareModal({ kind, targetId, targetName, staff = [], apiToken, onClose }: any) {
  const api = useMemo(() => {
    return kind === "event"
      ? { list: listEventMembers, add: addEventMember, remove: removeEventMember }
      : { list: listProjectMembers, add: addProjectMember, remove: removeProjectMember };
  }, [kind]);

  const [members, setMembers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // member-picker 状态
  const [pickStaffId, setPickStaffId] = useState("");
  const [pickRole, setPickRole] = useState("viewer");
  const [adding, setAdding] = useState(false);
  const [removingId, setRemovingId] = useState("");
  const [actionError, setActionError] = useState("");

  const reload = useCallback(() => {
    if (!apiToken || !targetId) {
      setMembers([]);
      setLoading(false);
      setError(apiToken ? "" : "缺少 API token,无法读取成员。");
      return Promise.resolve();
    }
    setLoading(true);
    setError("");
    return api
      .list(apiToken, String(targetId))
      .then((res) => setMembers(unwrapMembers(res)))
      .catch((err) => setError(errMessage(err)))
      .finally(() => setLoading(false));
  }, [api, apiToken, targetId]);

  useEffect(() => {
    let alive = true;
    if (!apiToken || !targetId) {
      setMembers([]);
      setLoading(false);
      setError(apiToken ? "" : "缺少 API token,无法读取成员。");
      return;
    }
    setLoading(true);
    setError("");
    api
      .list(apiToken, String(targetId))
      .then((res) => { if (alive) setMembers(unwrapMembers(res)); })
      .catch((err) => { if (alive) setError(errMessage(err)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [api, apiToken, targetId]);

  // 已是成员的 staff_id 集合(从 picker 里隐藏)
  const memberIds = useMemo(
    () => new Set(members.map((m) => String(m.staff_id ?? m.id ?? ""))),
    [members],
  );
  const pickable = useMemo(
    () => (Array.isArray(staff) ? staff : []).filter((s: any) => !memberIds.has(String(s.id))),
    [staff, memberIds],
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
      await api.add(apiToken, String(targetId), pickStaffId, pickRole as any);
      setPickStaffId("");
      setPickRole("viewer");
      await reload();
    } catch (err) {
      setActionError(errMessage(err));
    } finally {
      setAdding(false);
    }
  }, [api, apiToken, targetId, pickStaffId, pickRole, adding, reload]);

  const handleRemove = useCallback(async (sid: any) => {
    setRemovingId(String(sid));
    setActionError("");
    try {
      await api.remove(apiToken, String(targetId), sid);
      await reload();
    } catch (err) {
      setActionError(errMessage(err));
    } finally {
      setRemovingId("");
    }
  }, [api, apiToken, targetId, reload]);

  return e("div", {
    className: "fixed inset-0 z-[9999] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4",
    onClick: onClose,
  },
    e("div", {
      className: "rounded-2xl border border-white/[0.08] bg-[#0b1220] w-full max-w-lg max-h-[92vh] flex flex-col shadow-2xl",
      onClick: (ev: any) => ev.stopPropagation(),
    },
      // Header
      e("div", { className: "flex items-center justify-between px-5 py-4 border-b border-white/[0.06]" },
        e("div", { className: "flex items-center gap-2.5 min-w-0" },
          e("div", { className: "w-8 h-8 rounded-lg flex items-center justify-center border border-purple-500/30 bg-purple-500/[0.08] shrink-0" },
            e(Share2, { size: 15, className: "text-purple-300" })),
          e("div", { className: "min-w-0" },
            e("h3", { className: "text-[14px] font-semibold text-white truncate" },
              "分享" + (kind === "event" ? "活动" : "项目")),
            e("p", { className: "text-[10.5px] text-slate-500 truncate" }, targetName || String(targetId))
          )
        ),
        e("button", { onClick: onClose, className: "text-slate-500 hover:text-white shrink-0" }, e(X, { size: 16 }))
      ),

      // Body
      e("div", { className: "flex-1 overflow-y-auto px-5 py-4 space-y-4" },
        // 当前成员
        e("div", null,
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "当前成员"),
          loading
            ? e("div", { className: "flex items-center gap-2 py-6 justify-center text-[11px] text-slate-500" },
                e(Loader2, { size: 14, className: "animate-spin" }), "加载成员中…")
            : error
              ? e("div", { className: "rounded-lg border border-red-500/20 bg-red-500/[0.05] px-3 py-3 text-[11px] text-red-300" }, error)
              : members.length === 0
                ? e("div", { className: "text-center py-6 text-[11px] text-slate-500 rounded-lg border border-white/[0.04] bg-white/[0.01]" }, "还没有共享成员")
                : e("div", { className: "space-y-1.5" },
                    members.map((m: any) => {
                      const sid = String(m.staff_id ?? m.id ?? "");
                      const name = m.name || nameForStaff(sid);
                      const roleKey = String(m.role || "viewer");
                      const busy = removingId === sid;
                      return e("div", {
                        key: sid,
                        className: "flex items-center gap-3 px-3 py-2 rounded-lg border border-white/[0.05] bg-white/[0.02]",
                      },
                        e("div", {
                          className: "w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white shrink-0",
                          style: { background: "linear-gradient(135deg, #a855f7, #06b6d4)" },
                        }, String(name || "?").slice(0, 1).toUpperCase()),
                        e("div", { className: "flex-1 min-w-0" },
                          e("div", { className: "text-[12px] text-white truncate" }, name),
                          m.email && e("div", { className: "text-[10px] text-slate-500 truncate" }, m.email)
                        ),
                        e("span", {
                          className: "text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0",
                          style: { background: "rgba(168,85,247,0.15)", color: "#c4b5fd" },
                        }, (ROLE_LABEL as any)[roleKey] || roleKey),
                        e("button", {
                          onClick: () => handleRemove(sid),
                          disabled: busy,
                          title: "移除成员",
                          className: "shrink-0 p-1 rounded text-slate-500 hover:text-red-300 hover:bg-red-500/10 disabled:opacity-40",
                        }, busy ? e(Loader2, { size: 13, className: "animate-spin" }) : e(Trash2, { size: 13 }))
                      );
                    })
                  )
        ),

        // 添加成员
        !loading && !error && e("div", null,
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "添加成员"),
          e("div", { className: "flex items-center gap-2" },
            e("select", {
              value: pickStaffId,
              onChange: (ev: any) => setPickStaffId(ev.target.value),
              className: "flex-1 min-w-0 rounded-md border border-white/[0.08] bg-white/[0.03] px-2.5 py-1.5 text-[11px] text-slate-200 outline-none focus:border-purple-500/40",
            },
              e("option", { value: "", style: { background: "#0b1220" } }, pickable.length ? "选择成员…" : "无可添加成员"),
              ...pickable.map((s: any) => e("option", { key: s.id, value: String(s.id), style: { background: "#0b1220" } }, s.name))
            ),
            e("select", {
              value: pickRole,
              onChange: (ev: any) => setPickRole(ev.target.value),
              className: "shrink-0 rounded-md border border-white/[0.08] bg-white/[0.03] px-2.5 py-1.5 text-[11px] text-slate-200 outline-none focus:border-purple-500/40",
            },
              e("option", { value: "viewer", style: { background: "#0b1220" } }, "查看"),
              e("option", { value: "editor", style: { background: "#0b1220" } }, "编辑")
            ),
            e("button", {
              onClick: handleAdd,
              disabled: !pickStaffId || adding,
              className: `shrink-0 px-3 py-1.5 rounded-md text-[11px] font-medium flex items-center gap-1.5 ${pickStaffId && !adding ? "bg-purple-500 hover:bg-purple-400 text-white" : "bg-white/[0.05] text-slate-600 cursor-not-allowed"}`,
            },
              adding ? e(Loader2, { size: 12, className: "animate-spin" }) : e(UserPlus, { size: 12 }),
              "添加")
          ),
          actionError && e("div", { className: "mt-2 rounded-lg border border-red-500/20 bg-red-500/[0.05] px-3 py-2 text-[11px] text-red-300" }, actionError),
          e("div", { className: "mt-2 text-[10px] text-slate-600" },
            "查看 = 只读;编辑 = 可改。仅 owner / admin 可增删成员。")
        ),

        // 共同目标 / 提醒规则 —— 后端尚无对应列,显式标注「后续接入」,不接假后端。
        e("div", { className: "rounded-lg border border-white/[0.04] bg-white/[0.01] px-3 py-3 space-y-2.5 opacity-70" },
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "协作设置 (可选,后续接入)"),
          e("div", null,
            e("label", { className: "block text-[10px] text-slate-500 mb-1" }, "共同目标"),
            e("input", {
              type: "text", disabled: true, placeholder: "例:本月新增 5 条合作内容",
              className: "w-full rounded-md border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5 text-[11px] text-slate-400 placeholder-slate-600 cursor-not-allowed",
            })
          ),
          e("div", null,
            e("label", { className: "block text-[10px] text-slate-500 mb-1" }, "提醒规则"),
            e("input", {
              type: "text", disabled: true, placeholder: "例:逾期未推进 3 天提醒成员",
              className: "w-full rounded-md border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5 text-[11px] text-slate-400 placeholder-slate-600 cursor-not-allowed",
            })
          )
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

export default ShareModal;
