// Verbatim from vkpi_v6.15.7_integrated.html
// P-GROUP-34:共享 Projects 从「文本框」改成「真多选器」——选中存项目 id 数组,
//   后端 staff_groups/service.py 据此把「组级 shared_projects × 组成员」展开成真
//   vkpi_project_members 行,scope.project_filter 复用现成 enforce 让组成员看到被共享项目。
// P-GROUP-7 扩展:共享 KOL 池同样从「文本框」改成「真多选器」——选中存 kol_pool_id 数组到
//   permissions.shared_kol_pool_ids,后端把「组级 shared_kol_pool_ids × 组成员」展开成真
//   vkpi_kol_pool_members 行(迁移 161),scope.member_shared_kol_ids 让组成员只读看到被共享 KOL。


import React, { useEffect, useState } from "react";
import { Bell, Target, TrendingUp, Users, X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";
import { apiFetch } from "../../../../../services/http";

const e = React.createElement;

type ProjectOption = { id: string; name: string };
type KolOption = { id: string; name: string; sub: string };

// 把后端项目行(p.* 形态)归一成 {id, name}:id 用 id,name 优先 project_name。
function normalizeProjectOption(row: any): ProjectOption | null {
  if (!row || typeof row !== "object") return null;
  const rawId = row.id ?? row.project_id ?? row.project_uid;
  if (rawId === undefined || rawId === null || rawId === "") return null;
  const id = String(rawId);
  const name = String(row.project_name || row.name || row.title || `项目 ${id}`);
  return { id, name };
}

// 把后端 KOL Pool 行(/api/admin/vkpi/kol-pool 的 items 形态)归一成 {id, name, sub}:
//   id=kol_pool_id;name 优先 display_name → handle;sub 展示 平台/@handle 辅助识别。
function normalizeKolOption(row: any): KolOption | null {
  if (!row || typeof row !== "object") return null;
  const rawId = row.id ?? row.kol_pool_id ?? row.pool_uid;
  if (rawId === undefined || rawId === null || rawId === "") return null;
  const id = String(rawId);
  const handle = String(row.handle || "").trim();
  const name = String(row.display_name || handle || `KOL ${id}`);
  const platform = String(row.platform || "").trim();
  const sub = [platform, handle ? `@${handle}` : ""].filter(Boolean).join(" · ");
  return { id, name, sub };
}

export function EditGroupModal({ groupName = "KOL Operations", mode = "edit", staff, initialMembers, initialDesc, permissions, projects, apiToken, onClose, onSave }: any) {
  const { t } = useT();
  const [name, setName] = useState(groupName);
  const [desc, setDesc] = useState(mode === "new" ? "" : (initialDesc || ""));
  const [members, setMembers] = useState(mode === "new" ? [] : (Array.isArray(initialMembers) ? initialMembers : []));
  // 组级权限可编辑(落库到 vkpi_staff_groups.permissions_json):共享 Projects(id 数组,真生效)
  //   / 共享 KOL 池(文本)/ KPI 目标 / 提醒规则。
  // shared_projects 初值:permissions.shared_projects 现存的是 id 数组(P-GROUP-34 后);
  //   历史可能是项目名文本 —— 统一 String() 后只与「真项目 id 列表」交集匹配,匹配不上的旧文本
  //   不再勾选(诚实降级:旧文本不再生效,需在此重选)。
  const [selectedProjectIds, setSelectedProjectIds] = useState<string[]>(
    Array.isArray(permissions?.shared_projects) ? permissions.shared_projects.map((x: any) => String(x)) : []
  );
  // 共享 KOL 池初值:permissions.shared_kol_pool_ids 现存的是 kol_pool_id 数组(P-GROUP-7 扩展后);
  //   历史文本字段 shared_kol_pool(池名)无法映射成 id,不再回填(诚实降级:旧文本不生效,需重选)。
  const [selectedKolIds, setSelectedKolIds] = useState<string[]>(
    Array.isArray(permissions?.shared_kol_pool_ids) ? permissions.shared_kol_pool_ids.map((x: any) => String(x)) : []
  );
  const [permKpi, setPermKpi] = useState((permissions && permissions.kpi_goal) || "");
  const [permReminder, setPermReminder] = useState((permissions && permissions.reminder_rule) || "");

  // 项目候选列表:优先用父级传入的 projects(零网络);否则自取(/api/admin/vkpi/projects)。
  //   不改装配文件(CockpitApp.tsx),故 projects/apiToken 都是可选 prop —— 缺省时自取,
  //   apiToken 缺省也行:apiFetch 走 credentials(session cookie)兜底。
  const [projectOptions, setProjectOptions] = useState<ProjectOption[]>(() => {
    const seed = Array.isArray(projects) ? projects : [];
    return seed.map(normalizeProjectOption).filter(Boolean) as ProjectOption[];
  });
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [projectsError, setProjectsError] = useState("");

  // KOL 候选列表:自取 /api/admin/vkpi/kol-pool?limit=200(零父级依赖;apiFetch 走 session cookie 兜底)。
  const [kolOptions, setKolOptions] = useState<KolOption[]>([]);
  const [kolsLoading, setKolsLoading] = useState(false);
  const [kolsError, setKolsError] = useState("");

  useEffect(() => {
    if (Array.isArray(projects) && projects.length > 0) return; // 父级已给,跳过自取。
    let alive = true;
    setProjectsLoading(true);
    setProjectsError("");
    apiFetch<{ projects?: any[]; items?: any[] }>("/api/admin/vkpi/projects?limit=200", { timeoutMs: 6000 }, apiToken)
      .then((resp) => {
        if (!alive) return;
        const rows = Array.isArray(resp?.projects) ? resp.projects : Array.isArray(resp?.items) ? resp.items : [];
        setProjectOptions(rows.map(normalizeProjectOption).filter(Boolean) as ProjectOption[]);
      })
      .catch(() => {
        if (!alive) return;
        setProjectsError("项目列表加载失败,可稍后重试");
      })
      .finally(() => {
        if (alive) setProjectsLoading(false);
      });
    return () => {
      alive = false;
    };
    // 仅挂载时取一次(apiToken 变化罕见;父级传 projects 时此 effect 直接 return)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let alive = true;
    setKolsLoading(true);
    setKolsError("");
    apiFetch<{ items?: any[] }>("/api/admin/vkpi/kol-pool?limit=200", { timeoutMs: 6000 }, apiToken)
      .then((resp) => {
        if (!alive) return;
        const rows = Array.isArray(resp?.items) ? resp.items : [];
        setKolOptions(rows.map(normalizeKolOption).filter(Boolean) as KolOption[]);
      })
      .catch(() => {
        if (!alive) return;
        setKolsError("KOL 列表加载失败,可稍后重试");
      })
      .finally(() => {
        if (alive) setKolsLoading(false);
      });
    return () => {
      alive = false;
    };
    // 仅挂载时取一次。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleMember = (id: any) => setMembers((prev: any) => prev.includes(id) ? prev.filter((x: any) => x !== id) : [...prev, id]);
  const toggleProject = (id: string) =>
    setSelectedProjectIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  const toggleKol = (id: string) =>
    setSelectedKolIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const save = () => {
    const perms = {
      // P-GROUP-34:存项目 id 数组(后端展开成真 vkpi_project_members 行)。
      shared_projects: selectedProjectIds.slice(),
      // P-GROUP-7 扩展:存 kol_pool_id 数组(后端展开成真 vkpi_kol_pool_members 行)。
      shared_kol_pool_ids: selectedKolIds.slice(),
      kpi_goal: permKpi.trim(),
      reminder_rule: permReminder.trim(),
    };
    onSave && onSave({ mode, name, desc, members, permissions: perms });
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
        e("input", { type: "text", value: name, onChange: (ev: any) => setName(ev.target.value),
          className: "w-full rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-[12px] text-white outline-none" })
      ),
      e("div", null,
        e("label", { className: "text-[10px] text-slate-500 mb-1 block" }, t("组描述")),
        e("textarea", { value: desc, onChange: (ev: any) => setDesc(ev.target.value), rows: 2,
          className: "w-full rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-[11px] text-white outline-none resize-none" })
      ),
      // Members
      e("div", null,
        e("div", { className: "flex items-center justify-between mb-2" },
          e("label", { className: "text-[10px] text-slate-500" }, t("成员列表")),
          e("button", { className: "text-[10px] text-purple-300 hover:text-purple-200", onClick: () => document.getElementById("edit-group-member-list")?.scrollIntoView({ behavior: "smooth" }) }, "+ " + t("添加成员"))
        ),
        e("div", { id: "edit-group-member-list", className: "space-y-1" },
          staff.map((s: any) => {
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
      // 共享 Projects — 真多选器(P-GROUP-34):勾选哪些项目共享给组成员,存项目 id 数组。
      e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-3 space-y-2.5" },
        e("div", { className: "flex items-center justify-between mb-1" },
          e("div", { className: "flex items-center gap-2" },
            e(Target, { size: 11, className: "text-purple-300/80 shrink-0" }),
            e("div", { className: "text-[11px] text-white" }, t("共享 Projects"))
          ),
          e("div", { className: "text-[9px] text-slate-600" }, `${selectedProjectIds.length} ${t("已选")}`)
        ),
        e("div", { className: "text-[9px] text-slate-600 mb-1.5" }, t("勾选的项目将共享给本组全部成员(只读)")),
        projectsLoading
          ? e("div", { className: "text-[10px] text-slate-500 px-1 py-2" }, t("加载项目列表…"))
          : projectsError
            ? e("div", { className: "text-[10px] text-amber-400/80 px-1 py-2" }, projectsError)
            : projectOptions.length === 0
              ? e("div", { className: "text-[10px] text-slate-500 px-1 py-2" }, t("暂无可共享项目"))
              : e("div", { className: "space-y-0.5 max-h-[180px] overflow-y-auto rounded-md border border-white/[0.05] bg-black/20 p-1" },
                  projectOptions.map((p) => {
                    const checked = selectedProjectIds.includes(p.id);
                    return e("label", {
                      key: p.id,
                      className: "flex items-center gap-2.5 rounded px-2 py-1 hover:bg-white/[0.03] cursor-pointer"
                    },
                      e("input", { type: "checkbox", checked, onChange: () => toggleProject(p.id),
                        className: "shrink-0 accent-purple-500" }),
                      e("div", { className: "flex-1 min-w-0 text-[11px] text-white truncate" }, p.name),
                      e("div", { className: "shrink-0 text-[9px] text-slate-600" }, `#${p.id}`)
                    );
                  })
                )
      ),
      // 共享 KOL 池 — 真多选器(P-GROUP-7 扩展):勾选哪些 KOL 共享给组成员,存 kol_pool_id 数组。
      e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-3 space-y-2.5" },
        e("div", { className: "flex items-center justify-between mb-1" },
          e("div", { className: "flex items-center gap-2" },
            e(Users, { size: 11, className: "text-purple-300/80 shrink-0" }),
            e("div", { className: "text-[11px] text-white" }, t("共享 KOL 池"))
          ),
          e("div", { className: "text-[9px] text-slate-600" }, `${selectedKolIds.length} ${t("已选")}`)
        ),
        e("div", { className: "text-[9px] text-slate-600 mb-1.5" }, t("勾选的 KOL 将共享给本组全部成员(只读)")),
        kolsLoading
          ? e("div", { className: "text-[10px] text-slate-500 px-1 py-2" }, t("加载 KOL 列表…"))
          : kolsError
            ? e("div", { className: "text-[10px] text-amber-400/80 px-1 py-2" }, kolsError)
            : kolOptions.length === 0
              ? e("div", { className: "text-[10px] text-slate-500 px-1 py-2" }, t("暂无可共享 KOL"))
              : e("div", { className: "space-y-0.5 max-h-[180px] overflow-y-auto rounded-md border border-white/[0.05] bg-black/20 p-1" },
                  kolOptions.map((k) => {
                    const checked = selectedKolIds.includes(k.id);
                    return e("label", {
                      key: k.id,
                      className: "flex items-center gap-2.5 rounded px-2 py-1 hover:bg-white/[0.03] cursor-pointer"
                    },
                      e("input", { type: "checkbox", checked, onChange: () => toggleKol(k.id),
                        className: "shrink-0 accent-purple-500" }),
                      e("div", { className: "flex-1 min-w-0" },
                        e("div", { className: "text-[11px] text-white truncate" }, k.name),
                        k.sub && e("div", { className: "text-[9px] text-slate-600 truncate" }, k.sub)
                      ),
                      e("div", { className: "shrink-0 text-[9px] text-slate-600" }, `#${k.id}`)
                    );
                  })
                )
      ),
      // 其余组级权限(KPI 目标 / 提醒规则)— 仍为文本,落 permissions_json。
      e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-3 space-y-2.5" },
        e("div", { className: "flex items-center justify-between" },
          e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, t("组级权限")),
          e("div", { className: "text-[9px] text-slate-600" }, t("组内成员共享"))
        ),
        [
          { icon: TrendingUp, label: t("共同 KPI 目标"),     value: permKpi,      set: setPermKpi,      ph: "如 Q2 新增 50 个高活 KOL" },
          { icon: Bell,       label: t("内部 @ 提醒规则"),   value: permReminder, set: setPermReminder, ph: "如 组内变更自动通知" },
        ].map((row: any, i: any) => e("div", { key: i, className: "flex items-center gap-2.5" },
          e(row.icon, { size: 11, className: "text-purple-300/80 shrink-0" }),
          e("div", { className: "w-[88px] shrink-0 text-[11px] text-white" }, row.label),
          e("input", {
            type: "text", value: row.value, placeholder: row.ph,
            onChange: (ev: any) => row.set(ev.target.value),
            className: "flex-1 min-w-0 rounded-md border border-white/[0.08] bg-white/[0.025] px-2.5 py-1 text-[10.5px] text-white placeholder-slate-600 outline-none focus:border-purple-500/40"
          })
        ))
      )
    ),
    e("div", { className: "px-5 py-2.5 border-t border-white/[0.06] flex justify-end gap-2" },
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 px-3 py-1 text-[11px] text-slate-300 hover:bg-white/[0.04]" }, t("取消")),
      e("button", { onClick: save, className: "rounded-md bg-purple-600 hover:bg-purple-500 px-3 py-1 text-[11px] font-medium text-white" }, t("保存分组"))
    )
  );
}
