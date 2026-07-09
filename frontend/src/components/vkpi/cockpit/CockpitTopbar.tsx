// 2026-07 门面重建:布局对齐 mockup(标题+实时副标 / 居中搜索 / 外观弹层 / 通知 / 账户),
// 配色全吃 --ds-* token(随全局 ThemeProvider 三风格×明暗切换)。搜索/进度中心/Help/消息/
// Report/通知/账户菜单等真功能与 handler 逻辑一字不改,仅换皮 + 新增「外观弹层」(接 ThemeProvider)。

import React from "react";
import { Bell, Briefcase, Calendar, ChevronDown, FileText, HelpCircle, MessageCircle, Palette, Search, Users } from "lucide-react";
import { Avatar } from "./components/Avatar";
import { TopProgressCenter } from "./components/TopProgressCenter";
import { NAV_ITEMS } from "./data/navItems";
import { useTheme, type Style, type Theme } from "../../../app/providers/ThemeProvider";
import { globalSearch } from "../../../services/vkpi/globalSearch-api";
import type {
  GlobalSearchEvent,
  GlobalSearchKol,
  GlobalSearchProject,
  GlobalSearchResult,
} from "../../../services/vkpi/globalSearch-api";

const e = React.createElement;

// ── X3 全局搜索框(自持 state 的受控组件,逻辑不变,仅换 token 皮)──────────────
function GlobalSearchBox() {
  const [q, setQ] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [results, setResults] = React.useState<GlobalSearchResult | null>(null);
  const boxRef = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    const keyword = q.trim();
    if (!keyword) { setResults(null); setLoading(false); setOpen(false); return; }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      globalSearch(keyword, { signal: controller.signal })
        .then((res) => { setResults(res); setOpen(true); })
        .catch(() => { if (!controller.signal.aborted) { setResults({ kols: [], projects: [], events: [] }); setOpen(true); } })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [q]);

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
  const jumpKol = (kol: GlobalSearchKol) => {
    try { window.localStorage.setItem("vkpi:pending-kolpool-open-id", String(kol.id)); } catch { /* localStorage 不可用忽略 */ }
    window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-item", { detail: { kolPoolId: kol.id } }));
    reset();
  };
  const jumpProject = (project: GlobalSearchProject) => {
    window.dispatchEvent(new CustomEvent("vkpi:open-project-task", { detail: { projectId: String(project.id) } }));
    reset();
  };
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
    e("div", { className: "flex items-center gap-1.5 px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted" }, e(icon, { size: 11 }), label);
  const itemCls = "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-ink-2 hover:bg-accent-soft";
  const noBlur = (ev: any) => ev.preventDefault();

  return e("div", { ref: boxRef, className: "relative" },
    e(Search, { size: 14, className: "absolute left-3 top-1/2 -translate-y-1/2 text-muted" }),
    e("input", {
      value: q,
      placeholder: "全局搜索 KOL / 项目 / 活动...",
      title: "输入即搜(下拉点选跳转);回车 → 跳到 KOL Pool 按关键词筛选;Esc 关闭",
      "aria-label": "Global Search",
      className: "w-full rounded-lg border border-line bg-panel py-2 pl-9 pr-3 text-xs text-ink placeholder-muted focus:border-accent focus:outline-none",
      onChange: (ev: any) => setQ(String(ev.currentTarget.value || "")),
      onFocus: () => { if (q.trim() && results) setOpen(true); },
      onKeyDown: (ev: any) => {
        if (ev.key === "Escape") { setOpen(false); ev.currentTarget.blur(); return; }
        if (ev.key !== "Enter") return;
        const kw = q.trim();
        if (!kw) return;
        try { window.localStorage.setItem("vkpi:pending-kolpool-search", kw); } catch { /* localStorage 不可用忽略 */ }
        window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-search"));
        reset();
        ev.currentTarget.blur();
      },
    }),
    open && e("div", { className: "absolute left-0 right-0 top-full z-50 mt-1 max-h-96 overflow-y-auto rounded-lg border border-line bg-card py-1 shadow-2xl shadow-black/40" },
      loading && total === 0 && e("div", { className: "px-3 py-2 text-xs text-muted" }, "搜索中..."),
      !loading && total === 0 && e("div", { className: "px-3 py-2 text-xs text-muted" }, "无匹配的 KOL / 项目 / 活动"),
      kols.length > 0 && e(React.Fragment, { key: "g-kol" },
        groupLabel(Users, "KOL"),
        ...kols.map((kol) => {
          const name = String(kol.display_name || kol.handle || `KOL #${kol.id}`);
          const handle = String(kol.handle || "");
          const meta = [String(kol.platform || ""), handle && (handle.startsWith("@") ? handle : `@${handle}`)].filter(Boolean).join(" · ");
          return e("button", { key: `kol-${kol.id}`, type: "button", className: itemCls, onMouseDown: noBlur, onClick: () => jumpKol(kol) },
            kol.avatar_url
              ? e("img", { src: kol.avatar_url, alt: "", className: "h-5 w-5 shrink-0 rounded-full object-cover" })
              : e("div", { className: "flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[9px] text-accent" }, name.charAt(0).toUpperCase()),
            e("span", { className: "truncate" }, name),
            meta && e("span", { className: "ml-auto shrink-0 text-[10px] text-muted" }, meta)
          );
        })
      ),
      projects.length > 0 && e(React.Fragment, { key: "g-project" },
        groupLabel(Briefcase, "项目"),
        ...projects.map((project) => e("button", { key: `project-${project.id}`, type: "button", className: itemCls, onMouseDown: noBlur, onClick: () => jumpProject(project) },
          e("span", { className: "truncate" }, String(project.project_name || project.project_uid || `项目 #${project.id}`)),
          project.stage && e("span", { className: "ml-auto shrink-0 rounded bg-panel px-1.5 py-0.5 text-[10px] text-muted" }, String(project.stage))
        ))
      ),
      events.length > 0 && e(React.Fragment, { key: "g-event" },
        groupLabel(Calendar, "活动"),
        ...events.map((event) => e("button", { key: `event-${event.id}`, type: "button", className: itemCls, onMouseDown: noBlur, onClick: () => jumpEvent(event) },
          e("span", { className: "truncate" }, String(event.title || `活动 ${event.id}`)),
          e("span", { className: "ml-auto shrink-0 text-[10px] text-muted" }, [String(event.start_date || ""), String(event.status || "")].filter(Boolean).join(" · "))
        ))
      )
    )
  );
}

// ── 外观弹层(玻璃/仪器/单色 × 浅/深,接全局 ThemeProvider)────────────────────
function AppearancePopover() {
  const { theme, style, setTheme, setStyle } = useTheme();
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    if (!open) return;
    const onDown = (ev: MouseEvent) => { const n = ref.current; if (n && ev.target instanceof Node && !n.contains(ev.target)) setOpen(false); };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);
  const styles: Array<[Style, string]> = [["glass", "玻璃"], ["instrument", "仪器"], ["commandos", "单色"]];
  const themes: Array<[Theme, string]> = [["light", "浅"], ["dark", "深"]];
  const seg = "flex gap-1 rounded-lg bg-panel p-1";
  const segBtn = (on: boolean) => `flex-1 rounded-md px-2 py-1 text-[11px] font-medium transition ${on ? "bg-accent-soft text-accent" : "text-muted hover:text-ink"}`;
  return e("div", { ref, className: "relative" },
    e("button", { onClick: () => setOpen((o) => !o), "aria-label": "外观 / 主题", title: "外观 / 主题", className: "rounded-lg p-2 text-muted hover:bg-accent-soft hover:text-ink" }, e(Palette, { size: 16 })),
    open && e("div", { className: "absolute right-0 top-full z-50 mt-2 w-48 rounded-xl border border-line bg-card p-3 shadow-2xl shadow-black/40" },
      e("div", { className: "mb-1.5 text-[9px] font-semibold uppercase tracking-wider text-muted" }, "外观风格"),
      e("div", { className: `mb-3 ${seg}` }, styles.map(([s, l]) => e("button", { key: s, onClick: () => setStyle(s), className: segBtn(style === s) }, l))),
      e("div", { className: "mb-1.5 text-[9px] font-semibold uppercase tracking-wider text-muted" }, "主题"),
      e("div", { className: seg }, themes.map(([tk, l]) => e("button", { key: tk, onClick: () => setTheme(tk), className: segBtn(theme === tk) }, l)))
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
  const label = (NAV_ITEMS.find((n: any) => n.key === activeNav)?.label) || "Dashboard";
  const iconBtn = "rounded-lg p-2 text-muted hover:bg-accent-soft hover:text-ink";
  return e("header", { className: "sticky top-0 z-40 flex h-16 items-center gap-4 border-b border-line bg-bg px-4 backdrop-blur-xl md:px-6" },
    // 左:标题 + 实时副标
    e("div", { className: "flex flex-none flex-col justify-center" },
      e("h1", { className: "text-sm font-semibold leading-none tracking-wide text-ink" }, label),
      e("div", { className: "mt-1 flex items-center gap-1.5 text-[10px] text-muted" },
        e("span", { className: "h-1.5 w-1.5 rounded-full bg-good" }),
        t ? t("实时") : "实时"
      )
    ),
    // 中:居中搜索
    e("div", { className: "mx-auto hidden w-full max-w-md md:block" }, e(GlobalSearchBox)),
    // 右:操作簇(真功能全保留)+ 外观弹层 + 账户
    e("div", { className: "flex flex-none items-center gap-1.5 md:gap-2" },
      e("div", { className: "hidden md:block" }, e(TopProgressCenter)),
      e("button", { ref: helpBtnRef, onClick: () => setShowHelp(true), "aria-label": "Help", className: `hidden md:block ${iconBtn}` }, e(HelpCircle, { size: 16 })),
      e("button", { ref: messagesBtnRef, onClick: () => setShowMessages(true), "aria-label": "Work Reminders", className: `relative hidden md:block ${iconBtn}` },
        e(MessageCircle, { size: 16 }),
        activeReminders.filter((r: any) => r.status === "todo").length > 0 && e("span", { className: "absolute right-1 top-1 h-2 w-2 rounded-full bg-crit" })
      ),
      e("button", { onClick: () => setReportOpen(true), "aria-label": "Generate Report", className: "hidden items-center gap-1.5 rounded-lg border border-accent bg-accent-soft px-2.5 py-1.5 text-xs text-accent hover:bg-accent-soft md:flex" },
        e(FileText, { size: 13 }),
        e("span", null, "Report")
      ),
      e("button", { ref: notifsBtnRef, onClick: () => setShowNotifs(true), "aria-label": "Notifications", className: `relative ${iconBtn}` },
        e(Bell, { size: 16 }),
        runtimeNotifications.filter((n: any) => n.unread).length > 0 && e("span", { className: "absolute right-1 top-1 h-2 w-2 rounded-full bg-crit" })
      ),
      e(AppearancePopover),
      // 账户(唯一身份锚点,侧栏不再重复)
      e("button", { ref: userMenuBtnRef, onClick: () => setShowUserMenu(true), "aria-label": "User Menu", className: "ml-1 flex items-center gap-2 rounded-lg border-l border-line py-1 pl-3 pr-2 hover:bg-accent-soft" },
        e(Avatar, {
          src: viewingAs ? null : currentUser.avatarUrl,
          alt: currentUser.name, size: 32,
          fallback: viewingAs ? viewingAs.avatar : currentUser.avatar,
          gradient: viewingAs ? viewingAs.color : currentUser.avatarGradient,
        }),
        e("div", { className: "hidden text-left text-xs sm:block" },
          e("div", { className: "flex items-center gap-1 text-ink" },
            viewingAs ? viewingAs.name : currentUser.name,
            viewingAs && e("span", { className: "rounded bg-accent-soft px-1 py-0.5 text-[8px] text-accent" }, t("正在以"))
          ),
          e("div", { className: "text-muted" }, viewingAs ? viewingAs.title : (currentUser.role === "admin" ? t("Admin") : t("成员")))
        ),
        e(ChevronDown, { size: 14, className: "hidden text-muted sm:block" })
      )
    )
  );
}
