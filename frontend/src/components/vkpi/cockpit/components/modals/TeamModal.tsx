// Verbatim from vkpi_v6.15.7_integrated.html
// P-GROUP 调整批(2026-07-01):
//   1) 组列表化 —— 原先只显示 groupList[0],新建的组看不见/没法再编辑;改为 map 全部组。
//   2) 组卡信息读真值 —— 原 4 行是硬编码假文案(135mm LAB / Top performers 等);改读
//      group.permissions(shared_projects/shared_kol_pool_ids/kpi_goal/reminder_rule),没填诚实不显示。
//   3) 删除分组 —— 接现成 DELETE /staff-groups/{id}(window.confirm 确认 + onRefreshGroups 刷新)。
//   4) 员工视角去硬编码 —— 「直属上级:Kevin Chen」删;「我所在的分组」按 member_ids 算真值。

import React from "react";
import { Bell, Plus, Share2, Target, Trash2, TrendingUp, Users, X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { apiFetch } from "../../../../../services/http";
import { deleteStaffGroup } from "../../../../../services/vkpi/groups-api";
import {
  listTeamKolShares,
  revokeTeamKolShare,
  type VkpiTeamKolShare,
} from "../../../../../services/vkpi/team-share-api";
import { formatLocal } from "../../../lib/timeLocal";

const e = React.createElement;

// C2 共享管理:KOL 共享关系集中视图(谁 → 谁 → KOL → 时间)+ 撤销。
// 行来源 GET /my-kol/shares:管理层看全部,普通成员只看自己发出+收到(后端按 scope 过滤,
// 前端不再二次筛)。撤销走 DELETE /my-kol/shares/{id},乐观移除 + 失败回滚。
function ShareAdminSection({ apiToken, t }: any) {
  const [shares, setShares] = React.useState<VkpiTeamKolShare[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [revokingId, setRevokingId] = React.useState<any>(null);

  const load = React.useCallback(async () => {
    if (!apiToken) {
      setShares([]);
      setLoading(false);
      setError("缺少 API token,无法读取共享关系。");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res: any = await listTeamKolShares(apiToken);
      setShares(Array.isArray(res && res.items) ? res.items : []);
    } catch (err: any) {
      setError(String(err && err.message ? err.message : err));
    } finally {
      setLoading(false);
    }
  }, [apiToken]);

  React.useEffect(() => { load(); }, [load]);

  // 撤销:先乐观移除本行,失败回滚原列表(诚实 surface 后端报错,不假装成功)。
  const revoke = async (row: any) => {
    if (!apiToken || revokingId) return;
    const viaGroup = row.shared_via_group_id != null;
    const msg = `确认撤销「${row.from_name || t("未知")} → ${row.to_name}」对 KOL「${row.kol_name || row.handle || row.kol_pool_id}」的共享?`
      + (viaGroup ? "\n注意:该行由分组共享产生,分组重算后可能恢复;永久移除请编辑分组共享配置。" : "");
    if (!window.confirm(msg)) return;
    setRevokingId(row.id);
    const prev = shares;
    setShares(prev.filter((x: any) => x.id !== row.id)); // 乐观更新
    try {
      await revokeTeamKolShare(apiToken, row.id);
    } catch (err: any) {
      setShares(prev); // 失败回滚
      window.alert("撤销失败:" + String(err && err.message ? err.message : err));
    } finally {
      setRevokingId(null);
    }
  };

  return e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 mt-3" },
    e("div", { className: "flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-500 mb-2" },
      e(Share2, { size: 10, className: "text-purple-300" }),
      e("span", null, t("共享管理") + " · KOL" + (!loading && !error ? ` · ${shares.length}` : ""))
    ),
    loading
      ? e("div", { className: "py-3 text-center text-[10px] text-slate-500" }, "加载共享关系…")
      : error
        ? e("div", { className: "rounded-md border border-red-500/20 bg-red-500/[0.05] px-2 py-2 text-[10px] text-red-300" }, error)
        : shares.length === 0
          ? e("div", { className: "py-3 text-center text-[10px] text-slate-500" }, t("暂无 KOL 共享记录"))
          : e("div", { className: "space-y-0.5" },
              shares.map((row: any) => e("div", {
                key: row.id,
                className: "flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-white/[0.04]"
              },
                e("div", { className: "flex-1 min-w-0" },
                  e("div", { className: "flex items-center gap-1.5 text-[11px] min-w-0" },
                    e("span", { className: "text-white truncate" }, row.from_name || t("未知")),
                    e("span", { className: "text-slate-600 shrink-0" }, "→"),
                    e("span", { className: "text-white truncate" }, row.to_name || String(row.to_staff_id ?? "")),
                    row.shared_via_group_id != null && e("span", {
                      className: "text-[8px] px-1 py-0.5 rounded bg-purple-500/15 text-purple-300 shrink-0",
                      title: "由分组共享展开产生,分组重算后可能恢复"
                    }, t("分组共享"))
                  ),
                  e("div", { className: "text-[10px] text-slate-500 truncate" },
                    `${row.kol_name || row.handle || `#${row.kol_pool_id}`}`
                    + (row.platform ? ` · ${row.platform}` : "")
                    + (row.handle ? ` @${row.handle}` : "")
                    + ` · ${formatLocal(row.created_at)}`
                  ),
                  // 协作设置(kind='kol' 的 collab-settings):没设过诚实不显示。
                  (row.shared_goal || row.reminder_rule) && e("div", { className: "text-[10px] text-slate-500 truncate" },
                    [
                      row.shared_goal && `${t("共同目标")}:${row.shared_goal}`,
                      row.reminder_rule && `${t("提醒规则")}:${row.reminder_rule}`,
                    ].filter(Boolean).join(" · ")
                  )
                ),
                row.can_revoke && e("button", {
                  onClick: () => revoke(row),
                  disabled: !apiToken || revokingId === row.id,
                  title: t("撤销共享"),
                  className: "shrink-0 flex items-center gap-0.5 text-[10px] text-rose-300/80 hover:text-rose-300 disabled:text-white/25"
                }, e(Trash2, { size: 10 }), revokingId === row.id ? "…" : t("撤销"))
              ))
            )
  );
}

export function TeamModal({ user, staff, groups, onClose, onImpersonate, t, onOpenEditGroup, onOpenNewGroup, onRefreshGroups, apiToken = "" }: any) {
  const isAdmin = user.role === "admin";
  // #19 @提及:输入一条消息 → POST /team/mention → 目标成员通知流出现一条(后端复用 vkpi_alerts)。
  const [mentioning, setMentioning] = React.useState<any>(null);
  const [deleting, setDeleting] = React.useState<any>(null);
  const mentionMember = async (s: any) => {
    if (!apiToken || mentioning) return;
    const msg = (window.prompt(`给 ${s.name} 发一条提及通知:`) || "").trim();
    if (!msg) return;
    setMentioning(s.id);
    try {
      const res: any = await apiFetch("/api/admin/vkpi/team/mention", { method: "POST", body: JSON.stringify({ target_staff_id: Number(s.id), message: msg }) }, apiToken);
      window.alert(res && res.status === "success" ? `已提及 ${s.name}` : ((res && res.detail) || "提及失败"));
    } catch (err: any) { window.alert("提及失败:" + String(err && err.message ? err.message : err)); }
    finally { setMentioning(null); }
  };
  const groupList = Array.isArray(groups) ? groups : [];
  // 删除分组:接现成后端 DELETE;确认后删 + 刷新(不影响员工账号本身)。
  const removeGroup = async (g: any) => {
    if (!apiToken || deleting) return;
    if (!window.confirm(`确认删除分组「${g.name}」?成员关系与共享配置将一并移除(不影响员工账号)。`)) return;
    setDeleting(g.id);
    try {
      await deleteStaffGroup(apiToken, g.id);
      if (onRefreshGroups) await onRefreshGroups();
    } catch (err: any) {
      window.alert("删除失败:" + String(err && err.message ? err.message : err));
    } finally { setDeleting(null); }
  };
  // 组卡信息行:读 group.permissions 真值,没填的行不显示(诚实空)。
  const permRows = (g: any) => {
    const p = (g && g.permissions) || {};
    const rows: any[] = [];
    const sp = Array.isArray(p.shared_projects) ? p.shared_projects.length : 0;
    const sk = Array.isArray(p.shared_kol_pool_ids) ? p.shared_kol_pool_ids.length : 0;
    if (sp > 0) rows.push({ icon: Target, label: t("共享 Projects"), value: `${sp} ${t("个项目")}` });
    if (sk > 0) rows.push({ icon: Users, label: t("共享 KOL 池"), value: `${sk} ${t("个 KOL")}` });
    if (p.kpi_goal) rows.push({ icon: TrendingUp, label: t("共同 KPI 目标"), value: p.kpi_goal });
    if (p.reminder_rule) rows.push({ icon: Bell, label: t("内部 @ 提醒规则"), value: p.reminder_rule });
    return rows;
  };
  const memberIdsOf = (g: any) => ((g && g.member_ids) || []).map((x: any) => String(x));
  const myGroupNames = groupList.filter((g: any) => memberIdsOf(g).includes(String(user.id))).map((g: any) => g.name);
  return e(CenterModal, { onClose, maxWidth: "xl" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("div", null,
        e("h2", { className: "text-sm font-semibold text-white" }, t("团队管理")),
        e("div", { className: "text-[10px] text-slate-500 mt-0.5" }, isAdmin ? `${staff.length} 人 · ${groupList.length} 个分组 · 你是 Admin` : "我所在的团队")
      ),
      e("div", { className: "flex items-center gap-2" },
        isAdmin && e("button", {
          onClick: () => { onClose(); onOpenNewGroup && onOpenNewGroup(); },
          className: "flex items-center gap-1 rounded-md border border-purple-500/30 bg-purple-500/[0.08] px-2 py-1 text-[10px] text-purple-200 hover:bg-purple-500/[0.15]"
        },
          e(Plus, { size: 10 }), t("新建分组")),
        e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
      )
    ),
    e("div", { className: "p-4 max-h-[60vh] overflow-y-auto" },
      isAdmin
        ? e("div", { className: "space-y-2" },
            // 分组卡列表:全部组都显示,可编辑/删除(此前只显示第 1 个组,新建的组看不见)。
            groupList.length === 0 && e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-4 text-center text-[11px] text-slate-500" },
              t("尚无分组 · 点右上「新建分组」创建")
            ),
            groupList.map((g: any) => {
              const gMemberIds = memberIdsOf(g);
              const gMembers = staff.filter((s: any) => gMemberIds.includes(String(s.id)));
              const rows = permRows(g);
              return e("div", { key: g.id || g.name, className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
                e("div", { className: "flex items-center justify-between mb-2" },
                  e("div", { className: "flex items-center gap-2 min-w-0" },
                    e("span", { className: "text-[11px] font-medium text-white truncate" }, g.name),
                    e("span", { className: "text-[9px] text-slate-500 shrink-0" }, `${gMembers.length} ${t("成员")}`)
                  ),
                  e("div", { className: "flex items-center gap-2 shrink-0" },
                    e("button", {
                      onClick: () => { onClose(); onOpenEditGroup && onOpenEditGroup(g); },
                      className: "text-[10px] text-purple-300 hover:text-purple-200"
                    }, t("编辑分组")),
                    e("button", {
                      onClick: () => removeGroup(g),
                      disabled: !apiToken || deleting === g.id,
                      title: t("删除分组"),
                      className: "flex items-center gap-0.5 text-[10px] text-rose-300/80 hover:text-rose-300 disabled:text-white/25"
                    }, e(Trash2, { size: 10 }), deleting === g.id ? "…" : t("删除"))
                  )
                ),
                g.description && e("div", { className: "text-[10px] text-slate-500 mb-2" }, g.description),
                // 共享/权限信息:读真值,没配置的组不显示此块。
                rows.length > 0 && e("div", { className: "rounded-md border border-purple-500/[0.15] bg-purple-500/[0.04] p-2 mb-2 space-y-1" },
                  rows.map((row: any, i: any) => e("div", { key: i, className: "flex items-start gap-2 text-[10px]" },
                    e(row.icon, { size: 10, className: "text-purple-300 shrink-0 mt-0.5" }),
                    e("span", { className: "text-slate-400 shrink-0" }, row.label + ":"),
                    e("span", { className: "text-slate-300 flex-1 min-w-0 truncate" }, row.value)
                  ))
                ),
                gMembers.length > 0 && e("div", { className: "flex flex-wrap gap-1.5" },
                  gMembers.map((s: any) => e("span", {
                    key: s.id,
                    className: "flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.03] pl-0.5 pr-2 py-0.5"
                  },
                    e("span", {
                      className: "w-[18px] h-[18px] rounded-full flex items-center justify-center text-[8px] font-bold text-white shrink-0",
                      style: { background: s.color }
                    }, s.avatar),
                    e("span", { className: "text-[10px] text-slate-300" }, s.name)
                  ))
                )
              );
            }),
            // 全部成员(在线状态 + 切换身份入口保持不变)
            e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
              e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, `${t("全部成员")} · ${staff.length}`),
              e("div", { className: "space-y-1.5" },
                staff.map((s: any) => e("div", {
                  key: s.id,
                  className: "flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-white/[0.04]"
                },
                  e("div", { className: "relative shrink-0" },
                    e("div", {
                      className: "w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-bold text-white",
                      style: { background: s.color }
                    }, s.avatar),
                    (s.online || s.user_online) ? e("span", {
                      className: "absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-emerald-400",
                      style: { boxShadow: "0 0 0 2px #0a1020" },
                      title: "在线"
                    }) : null
                  ),
                  e("div", { className: "flex-1 min-w-0" },
                    e("div", { className: "flex items-center gap-1.5" },
                      e("span", { className: "text-[11px] font-medium text-white" }, s.name),
                      (s.online || s.user_online) ? e("span", { className: "text-[8px] text-emerald-400" }, "● 在线") : null,
                      (s.isAdmin || s.role === "admin" || s.role === "owner") && e("span", { className: "text-[8px] uppercase tracking-wider px-1 py-0.5 rounded bg-purple-500/15 text-purple-300" }, "Admin")
                    ),
                    e("div", { className: "text-[10px] text-slate-500" }, `${s.title} · ${s.focus}`)
                  ),
                  s.id !== user.id && e("button", {
                    onClick: () => { onImpersonate(s); onClose(); },
                    className: "rounded-md border border-white/[0.08] px-2 py-1 text-[10px] text-slate-300 hover:bg-white/[0.05]"
                  }, t("切换身份(查看为)"))
                ))
              )
            )
          )
        : e("div", { className: "space-y-3" },
            // Staff 视角:看自己的卡片 + 团队联系人(去硬编码:直属上级删;所在分组读真值)。
            e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-4 flex items-center gap-3" },
              e("div", {
                className: "shrink-0 w-12 h-12 rounded-full flex items-center justify-center text-[16px] font-bold text-white",
                style: { background: user.avatarGradient }
              }, user.avatar),
              e("div", { className: "flex-1" },
                e("div", { className: "text-[12px] font-medium text-white" }, user.name),
                e("div", { className: "text-[10px] text-slate-500" }, user.email)
              )
            ),
            e("div", { className: "text-[10px] text-slate-500" },
              `${t("我所在的分组")}:${myGroupNames.length ? myGroupNames.join("、") : t("未加入任何分组")}`
            ),
            e("div", { className: "space-y-1" },
              staff.filter((s: any) => s.id !== user.id).map((s: any) => e("div", {
                key: s.id, className: "flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-white/[0.04]"
              },
                e("div", { className: "relative shrink-0" },
                  e("div", {
                    className: "w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white",
                    style: { background: s.color }
                  }, s.avatar),
                  (s.online || s.user_online) ? e("span", {
                    className: "absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full bg-emerald-400",
                    style: { boxShadow: "0 0 0 1.5px #0a1020" },
                    title: "在线"
                  }) : null
                ),
                e("div", { className: "flex-1 min-w-0" },
                  e("div", { className: "text-[11px] text-white" }, s.name),
                  (s.online || s.user_online) ? e("div", { className: "text-[10px] text-emerald-400" }, "在线") : e("div", { className: "text-[10px] text-slate-500" }, s.title)
                ),
                e("button", { onClick: () => mentionMember(s), disabled: !apiToken || mentioning === s.id, title: "给该成员发提及通知", className: "text-[10px] text-purple-300 hover:text-purple-200 disabled:text-white/25" }, mentioning === s.id ? "…" : t("@ 提及"))
              ))
            )
          ),
      // C2 共享管理区:admin/staff 两种视角都渲染(后端按 scope 过滤:管理层全量,成员只见自己相关)。
      e(ShareAdminSection, { apiToken, t })
    )
  );
}
