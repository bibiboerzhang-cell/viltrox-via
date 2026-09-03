// Verbatim from CockpitApp.tsx — conservative leaf extraction (行为不变).
// 纯展示侧边栏:吃 props,不持有任何 state/effect。
// 波2门面:配色改吃 --ds-* token 类(bg-bg/text-ink/border-line/bg-accent-soft…),风格随全局切换。

import React from "react";
import { ChevronLeft, ChevronRight, Wrench } from "lucide-react";
import { DashboardTaskQueueCard } from "./components/DashboardTaskQueueCard";
import { NAV_ITEMS } from "./data/navItems";
import { usePermissions } from "../../../hooks/usePermissions";
import { useT } from "./lib/i18n";
import viltroxLogo from "../../../assets/viltrox-logo.png";
import viltroxMark from "../../../assets/viltrox-mark.jpg";

const e = React.createElement;

// 单个导航项的形态(navItems.ts 的元素;icon 是 lucide 组件)。
interface NavItem {
  key: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  badge?: string | null;
  group?: string | null;
  v2?: boolean;
  ops?: boolean;
}

export function CockpitSidebar({
  collapsed,
  setCollapsed,
  activeNav,
  setActiveNav,
  versionBadge,
  apiToken,
}: any) {
  // 按板块授权过滤导航:成员设为「无」的板块不渲染(owner 全见)。默认可见。
  const perms = usePermissions();
  const { t } = useT();
  // V2 组默认折叠;但若当前就停在某个 V2 页,自动展开以免活动项被藏起来。
  const [v2Open, setV2Open] = React.useState(() => NAV_ITEMS.some((i) => (i as NavItem).v2 && i.key === activeNav));
  // 智能运维组默认展开(已建可达页),便于发现 Wave1-4 新能力入口。
  const [opsOpen, setOpsOpen] = React.useState(true);

  // 单个导航按钮(主导航 + V2 / 运维 组共用)
  const navButton = ({ icon: Icon, label, badge, key }: NavItem) => {
    const active = activeNav === key;
    const localizedLabel = t(label);
    return e("button", {
      key,
      onClick: (event: React.MouseEvent<HTMLButtonElement>) => {
        event.currentTarget.blur();
        setActiveNav(key);
      },
      title: collapsed ? localizedLabel : undefined,
      className: `vkpi-rail__item group flex w-full items-center ${collapsed ? "justify-center px-2" : "px-3 gap-3"} rounded-lg py-2 text-sm transition ${
        active ? "bg-accent-soft text-ink" : "text-muted hover:bg-accent-soft hover:text-ink"
      } ${active ? "is-active" : ""}`,
    },
      e(Icon, { size: 16, className: active ? "text-accent" : "text-muted" }),
      !collapsed && e("span", { className: "flex-1 text-left" }, localizedLabel),
      !collapsed && badge && e("span", {
        className: "rounded px-1.5 py-0.5 text-[10px] bg-line text-ink-2",
      }, badge)
    );
  };

  // 生产收敛(2026-07-02):实验模块(智能运维组 triage/问数/市场趋势/SkillStudio + V2 待开发)
  // 默认隐藏,仅构建时 VITE_EXPERIMENTAL_NAV=1 才显示 —— 线上只保留业务功能,实验能力留本地/预发。
  const showExperimental = String((import.meta as any).env?.VITE_EXPERIMENTAL_NAV ?? "") === "1";
  const visible = (NAV_ITEMS as NavItem[]).filter(({ key }) => perms.canViewBoard(key));
  const primaryItems = visible.filter((i) => !i.v2 && !i.ops);
  const opsItems = showExperimental ? visible.filter((i) => i.ops) : [];
  const v2Items = showExperimental ? visible.filter((i) => i.v2) : [];

  return e("aside", {
    className: `vkpi-rail ${collapsed ? "w-[66px] is-collapsed" : "w-[236px]"} sticky top-0 hidden h-screen shrink-0 flex-col justify-between border-r border-line transition-all duration-300 md:flex`
  },
    e("button", {
      type: "button",
      onClick: () => setCollapsed(!collapsed),
      className: "vkpi-rail__toggle",
      title: collapsed ? t("展开侧栏") : t("收起侧栏"),
      "aria-label": collapsed ? t("展开侧栏") : t("收起侧栏"),
      "aria-expanded": !collapsed,
    }, e(ChevronLeft, { size: 13 })),
    e("div", null,
      e("div", { className: `vkpi-rail__brand flex items-center ${collapsed ? "justify-center" : ""}` },
        e("img", {
          src: collapsed ? viltroxMark : viltroxLogo,
          alt: "Viltrox",
          className: collapsed ? "vkpi-rail__logo-mark" : "vkpi-rail__logo",
        })
      ),
      e("nav", { className: `vkpi-rail__nav space-y-1 ${collapsed ? "px-2" : "px-0.5"}` },
        // 分组渲染:按 group 连续分区,每区一个小标题(折叠态用细分隔线替代)。
        ...(() => {
          const out: any[] = [];
          let last: string | undefined;
          primaryItems.forEach((it) => {
            const g = it.group || undefined;
            if (g && g !== last) {
              const firstCap = last === undefined;
              last = g;
              out.push(!collapsed
                ? e("div", { key: `cap-${g}`, className: `vkpi-rail__cap px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-[.14em] text-muted ${firstCap ? "pt-1" : "pt-4"}` }, t(g))
                : e("div", { key: `cap-${g}`, className: `mx-2 h-px bg-line ${firstCap ? "mb-2" : "my-2"}` }));
            }
            out.push(navButton(it));
          });
          return out;
        })(),
        // 智能运维:Wave1-4 已建可达页(triage / 问数 / 市场趋势 / Skill Studio),默认展开。
        opsItems.length > 0 && e("div", { key: "opsgroup", className: "mt-2 pt-2 border-t border-line" },
          e("button", {
            onClick: () => setOpsOpen(!opsOpen), title: collapsed ? t("智能运维") : undefined,
            className: `group flex w-full items-center ${collapsed ? "justify-center px-2" : "px-3 gap-2"} rounded-lg py-2 text-[11px] font-medium uppercase tracking-wider text-muted hover:bg-accent-soft hover:text-ink-2`,
          },
            e(Wrench, { size: 13, className: "shrink-0" }),
            !collapsed && e("span", { className: "flex-1 text-left tracking-normal" }, t("智能运维")),
            !collapsed && e("span", { className: "rounded bg-accent-soft px-1.5 py-0.5 text-[9px] tracking-normal text-accent" }, String(opsItems.length))
          ),
          opsOpen && e("div", { className: "mt-1 space-y-1" }, opsItems.map(navButton))
        ),
        // V2:挪到底部、收进可折叠组(默认收起,等 V2 再做)
        v2Items.length > 0 && e("div", { key: "v2group", className: "mt-2 pt-2 border-t border-line" },
          e("button", {
            onClick: () => setV2Open(!v2Open), title: collapsed ? "V2 · 待开发" : undefined,
            className: `group flex w-full items-center ${collapsed ? "justify-center px-2" : "px-3 gap-2"} rounded-lg py-2 text-[11px] font-medium uppercase tracking-wider text-muted hover:bg-accent-soft hover:text-ink-2`,
          },
            e(ChevronRight, { size: 13, className: `shrink-0 transition-transform ${v2Open ? "rotate-90" : ""}` }),
            !collapsed && e("span", { className: "flex-1 text-left" }, "V2"),
            !collapsed && e("span", { className: "rounded bg-line px-1.5 py-0.5 text-[9px] tracking-normal text-muted" }, `${v2Items.length} 待开发`)
          ),
          v2Open && e("div", { className: "mt-1 space-y-1" }, v2Items.map(navButton))
        )
      )
    ),
    e("div", { className: `vkpi-rail__footer flex flex-col gap-2 ${collapsed ? "px-2" : "px-1"}` },
      e(DashboardTaskQueueCard, { apiToken, compact: true }),
      // 版本徽标:仅展开态显示一行极淡文字,折叠态隐藏以免挤压窄轨
      !collapsed && versionBadge && versionBadge.shortSha && e("div", {
        className: "px-3 pt-1 text-[10px] leading-tight text-muted select-none",
        title: versionBadge.hasClient
          ? (versionBadge.inSync ? "前后端构建一致" : "前端与后端构建不一致")
          : "前端构建标记缺失",
      },
        `${versionBadge.shortSha} · ${versionBadge.inSync ? "✓同步" : "⚠不一致"}`
      ),
      // 页脚法务链接(chrome.md .railfoot 约定:钉在侧栏最底部;折叠态隐藏)。
      // 用 <a> 而非 router Link:法务页是独立门面(与 /activate 同款整页分发),侧栏也可能在无 Router 的壳里渲染。
      !collapsed && e("nav", {
        className: "vkpi-rail__legal flex flex-wrap gap-x-2 gap-y-0.5 px-3 pb-1 text-[10px] leading-tight text-muted select-none",
        "aria-label": t("法务与隐私"),
      },
        e("a", { key: "privacy", href: "/legal/privacy", className: "hover:text-ink-2" }, t("隐私声明")),
        e("a", { key: "terms", href: "/legal/terms", className: "hover:text-ink-2" }, t("服务条款")),
        e("a", { key: "sources", href: "/legal/data-sources", className: "hover:text-ink-2" }, t("数据来源声明")),
        e("a", { key: "request", href: "/legal/request", className: "hover:text-ink-2" }, t("删除或勿联系申请"))
      )
    )
  );
}
