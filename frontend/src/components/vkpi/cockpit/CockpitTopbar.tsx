// Verbatim from CockpitApp.tsx — conservative leaf extraction (行为不变).
// 纯展示顶栏:吃 props,不持有任何 state/effect。
// 例外:全局搜索框(X3 跨域搜索,2026-07)——GlobalSearchBox 自持 state:
// 输入防抖 300ms 调 /api/admin/vkpi/global-search,下拉分组展示 KOL/项目/活动;
// 点结果跳转走既有管道:KOL=pending-kolpool-open-id + vkpi:open-kol-pool-item(开抽屉)、
// 项目=vkpi:open-project-task(直达项目详情)、活动=?cockpit=events 深链(CockpitApp
// 只监听前两类事件,Events 无事件管道,沿用 urlNav 深链整页跳,取舍见任务说明)。
// 回车仍保留原行为:关键词写 localStorage 并派发 vkpi:open-kol-pool-search 兜底。

import React from "react";
import { Bell, Briefcase, Calendar, ChevronDown, FileText, HelpCircle, MessageCircle, Search, Users } from "lucide-react";
import { Avatar } from "./components/Avatar";
import { NAV_ITEMS } from "./data/navItems";
import { globalSearch } from "../../../services/vkpi/globalSearch-api";
import type {
  GlobalSearchEvent,
  GlobalSearchKol,
  GlobalSearchProject,
  GlobalSearchResult,
} from "../../../services/vkpi/globalSearch-api";

const e = React.createElement;

// ── X3 全局搜索框(自持 state 的受控组件,不吃任何 props)────────────────────
function GlobalSearchBox() {
  const [q, setQ] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [results, setResults] = React.useState<GlobalSearchResult | null>(null);
  const boxRef = React.useRef<HTMLDivElement | null>(null);

  // 防抖 300ms 拉后端;输入变化/卸载时取消上一发(AbortController)。失败静默回空组。
  React.useEffect(() => {
    const keyword = q.trim();
    if (!keyword) {
      setResults(null);
      setLoading(false);
      setOpen(false);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      globalSearch(keyword, { signal: controller.signal })
        .then((res) => { setResults(res); setOpen(true); })
        .catch(() => {
          if (!controller.signal.aborted) {
            setResults({ kols: [], projects: [], events: [] });
            setOpen(true);
          }
        })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [q]);

  // 点击下拉/输入框以外区域 → 关闭下拉(结果保留,聚焦可再展开)。
  React.useEffect(() => {
    if (!open) return;
    const onDown = (ev: MouseEvent) => {
      const node = boxRef.current;
      if (node && ev.target instanceof Node && !node.contains(ev.target)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  const reset = () => { setQ(""); setResults(null); setOpen(false); };

  // KOL → 切 KOL Pool 并按 id 开详情抽屉(TaskProgressBoard 同款管道)。
  const jumpKol = (kol: GlobalSearchKol) => {
    try { window.localStorage.setItem("vkpi:pending-kolpool-open-id", String(kol.id)); } catch { /* localStorage 不可用忽略 */ }
    window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-item", { detail: { kolPoolId: kol.id } }));
    reset();
  };
  // 项目 → 切 Projects 并直达项目详情(泳道任务同款管道,CockpitApp 已监听)。
  const jumpProject = (project: GlobalSearchProject) => {
    window.dispatchEvent(new CustomEvent("vkpi:open-project-task", { detail: { projectId: String(project.id) } }));
    reset();
  };
  // 活动 → Events 板块:无事件管道(EventsMockupPage 的 initialEventId 只来自
  // CockpitApp 内部 state),走 ?cockpit=events 深链整页跳(urlNav 既有语义)。
  const jumpEvent = (_event: GlobalSearchEvent) => {
    reset();
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("cockpit", "events");
      window.location.assign(url.toString());
    } catch { /* URL 不可用忽略 */ }
  };

  const kols = results?.kols || [];
  const projects = results?.projects || [];
  const events = results?.events || [];
  const total = kols.length + projects.length + events.length;

  const groupLabel = (icon: any, label: string) =>
    e("div", { className: "flex items-center gap-1.5 px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500" },
      e(icon, { size: 11 }),
      label
    );
  const itemCls = "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-slate-200 hover:bg-white/[0.06]";
  // onMouseDown preventDefault:避免点击先触发 input blur/外点关闭,click 落空。
  const noBlur = (ev: any) => ev.preventDefault();

  return e("div", { ref: boxRef, className: "relative" },
    e(Search, { size: 14, className: "absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" }),
    e("input", {
      value: q,
      placeholder: "全局搜索 KOL / 项目 / 活动...",
      title: "输入即搜(下拉点选跳转);回车 → 跳到 KOL Pool 按关键词筛选;Esc 关闭",
      "aria-label": "Global Search",
      className: "w-full rounded-lg border border-white/[0.08] bg-white/[0.03] py-1.5 pl-9 pr-3 text-xs text-white placeholder-slate-500 focus:border-blue-500/40 focus:outline-none",
      onChange: (ev: any) => setQ(String(ev.currentTarget.value || "")),
      onFocus: () => { if (q.trim() && results) setOpen(true); },
      onKeyDown: (ev: any) => {
        if (ev.key === "Escape") {
          setOpen(false);
          ev.currentTarget.blur();
          return;
        }
        if (ev.key !== "Enter") return;
        const kw = q.trim();
        if (!kw) return;
        // 保留原回车行为:跳 KOL Pool 按关键词筛选(下拉是增量能力,回车兜底不变)。
        try { window.localStorage.setItem("vkpi:pending-kolpool-search", kw); } catch { /* localStorage 不可用忽略 */ }
        window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-search"));
        reset();
        ev.currentTarget.blur();
      },
    }),
    open && e("div", { className: "absolute left-0 right-0 top-full z-50 mt-1 max-h-96 overflow-y-auto rounded-lg border border-white/[0.08] bg-[#0a0f1e] py-1 shadow-2xl shadow-black/40" },
      loading && total === 0 && e("div", { className: "px-3 py-2 text-xs text-slate-500" }, "搜索中..."),
      !loading && total === 0 && e("div", { className: "px-3 py-2 text-xs text-slate-500" }, "无匹配的 KOL / 项目 / 活动"),
      kols.length > 0 && e(React.Fragment, { key: "g-kol" },
        groupLabel(Users, "KOL"),
        ...kols.map((kol) => {
          const name = String(kol.display_name || kol.handle || `KOL #${kol.id}`);
          const handle = String(kol.handle || "");
          const meta = [String(kol.platform || ""), handle && (handle.startsWith("@") ? handle : `@${handle}`)].filter(Boolean).join(" · ");
          return e("button", { key: `kol-${kol.id}`, type: "button", className: itemCls, onMouseDown: noBlur, onClick: () => jumpKol(kol) },
            kol.avatar_url
              ? e("img", { src: kol.avatar_url, alt: "", className: "h-5 w-5 shrink-0 rounded-full object-cover" })
              : e("div", { className: "flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-500/20 text-[9px] text-blue-300" }, name.charAt(0).toUpperCase()),
            e("span", { className: "truncate" }, name),
            meta && e("span", { className: "ml-auto shrink-0 text-[10px] text-slate-500" }, meta)
          );
        })
      ),
      projects.length > 0 && e(React.Fragment, { key: "g-project" },
        groupLabel(Briefcase, "项目"),
        ...projects.map((project) => e("button", {
          key: `project-${project.id}`, type: "button", className: itemCls, onMouseDown: noBlur, onClick: () => jumpProject(project),
        },
          e("span", { className: "truncate" }, String(project.project_name || project.project_uid || `项目 #${project.id}`)),
          project.stage && e("span", { className: "ml-auto shrink-0 rounded bg-white/[0.05] px-1.5 py-0.5 text-[10px] text-slate-400" }, String(project.stage))
        ))
      ),
      events.length > 0 && e(React.Fragment, { key: "g-event" },
        groupLabel(Calendar, "活动"),
        ...events.map((event) => e("button", {
          key: `event-${event.id}`, type: "button", className: itemCls, onMouseDown: noBlur, onClick: () => jumpEvent(event),
        },
          e("span", { className: "truncate" }, String(event.title || `活动 ${event.id}`)),
          e("span", { className: "ml-auto shrink-0 text-[10px] text-slate-500" },
            [String(event.start_date || ""), String(event.status || "")].filter(Boolean).join(" · ")
          )
        ))
      )
    )
  );
}

export function CockpitTopbar({
  activeNav,
  helpBtnRef,
  setShowHelp,
  messagesBtnRef,
  setShowMessages,
  activeReminders,
  setReportOpen,
  notifsBtnRef,
  setShowNotifs,
  runtimeNotifications,
  userMenuBtnRef,
  setShowUserMenu,
  viewingAs,
  currentUser,
  t,
}: any) {
  return e("header", { className: "sticky top-0 z-40 flex h-16 items-center justify-between border-b border-white/[0.06] bg-[#050810]/80 px-4 backdrop-blur-xl md:px-6" },
    e("div", { className: "text-sm font-semibold tracking-wide text-white" }, (NAV_ITEMS.find(n => n.key === activeNav)?.label) || "Dashboard"),
    e("div", { className: "mx-4 hidden max-w-md flex-1 md:block" },
      e(GlobalSearchBox)
    ),
    e("div", { className: "flex items-center gap-2 md:gap-3" },
      // V6.14: Help & Messages 按钮(加 onClick + ref)
      e("button", {
        ref: helpBtnRef,
        onClick: () => setShowHelp(true),
        "aria-label": "Help",
        className: "hidden rounded-lg p-2 text-slate-400 hover:bg-white/[0.04] hover:text-white md:block"
      }, e(HelpCircle, { size: 16 })),
      e("button", {
        ref: messagesBtnRef,
        onClick: () => setShowMessages(true),
        "aria-label": "Work Reminders",
        className: "hidden relative rounded-lg p-2 text-slate-400 hover:bg-white/[0.04] hover:text-white md:block"
      },
        e(MessageCircle, { size: 16 }),
        activeReminders.filter((r: any) => r.status === "todo").length > 0 && e("span", { className: "absolute right-1 top-1 h-2 w-2 rounded-full bg-rose-500" })
      ),
      // V6.10: Generate Report 按钮
      e("button", {
        onClick: () => setReportOpen(true),
        "aria-label": "Generate Report",
        className: "hidden md:flex items-center gap-1.5 rounded-lg border border-purple-500/30 bg-purple-500/[0.08] px-2.5 py-1.5 text-xs text-purple-200 hover:bg-purple-500/[0.15] hover:border-purple-500/50"
      },
        e(FileText, { size: 13 }),
        e("span", null, "Report")
      ),
      // V6.14: Notifications(加 onClick + ref)
      e("button", {
        ref: notifsBtnRef,
        onClick: () => setShowNotifs(true),
        "aria-label": "Notifications",
        className: "relative rounded-lg p-2 text-slate-400 hover:bg-white/[0.04] hover:text-white"
      },
        e(Bell, { size: 16 }),
        runtimeNotifications.filter((n: any) => n.unread).length > 0 && e("span", { className: "absolute right-1 top-1 h-2 w-2 rounded-full bg-rose-500" })
      ),
      // V6.14: User Menu(整个 wrap 加 onClick + ref)
      e("button", {
        ref: userMenuBtnRef,
        onClick: () => setShowUserMenu(true),
        "aria-label": "User Menu",
        className: "flex items-center gap-2 border-l border-white/[0.06] pl-3 rounded-r-lg hover:bg-white/[0.02] py-1 pr-2"
      },
        e(Avatar, {
  src: viewingAs ? null : currentUser.avatarUrl,
  alt: currentUser.name, size: 32,
  fallback: viewingAs ? viewingAs.avatar : currentUser.avatar,
  gradient: viewingAs ? viewingAs.color : currentUser.avatarGradient
        }),
        e("div", { className: "hidden text-xs sm:block text-left" },
          e("div", { className: "text-white flex items-center gap-1" },
            viewingAs ? viewingAs.name : currentUser.name,
            viewingAs && e("span", { className: "text-[8px] px-1 py-0.5 rounded bg-blue-500/20 text-blue-300" }, t("正在以"))
          ),
          e("div", { className: "text-slate-500" },
            viewingAs ? viewingAs.title : (currentUser.role === "admin" ? t("Admin") : t("成员"))
          )
        ),
        e(ChevronDown, { size: 14, className: "hidden text-slate-500 sm:block" })
      )
    )
  );
}
