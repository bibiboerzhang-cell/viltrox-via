// 移动端导航(< md 断点)。
// 背景:桌面侧边栏 CockpitSidebar 是 `hidden md:flex`,小屏(手机)整条导航消失,
//   而全站此前无任何汉堡/移动抽屉替代 —— 员工用手机进来只能停在 Dashboard,点不进
//   KOL Pool / Projects / GTM 等页。本组件补齐:汉堡按钮(md:hidden)打开左侧滑出抽屉,
//   复用与侧边栏同一套 NAV_ITEMS + canViewBoard 权限过滤;点选即切页并关闭。
// 桌面端(≥ md)整体不渲染(汉堡与抽屉容器都带 md:hidden),不影响现有桌面布局。
import React from "react";
import { Menu, X } from "lucide-react";

import { NAV_ITEMS } from "./data/navItems";
import { usePermissions } from "../../../hooks/usePermissions";

interface NavItem {
  key: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  badge?: string | null;
  v2?: boolean;
  ops?: boolean;
}

interface CockpitMobileNavProps {
  activeNav: string;
  setActiveNav: (key: string) => void;
}

export function CockpitMobileNav({ activeNav, setActiveNav }: CockpitMobileNavProps) {
  const [open, setOpen] = React.useState(false);
  const perms = usePermissions();

  // 打开时:ESC 关闭 + 锁 body 滚动(抽屉遮罩下背景不应跟着滚)。
  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open]);

  // 与侧边栏 primaryItems 同口径:剔除实验性 v2/ops,按 canViewBoard 过滤(owner 全见)。
  const items = (NAV_ITEMS as NavItem[]).filter(
    (item) => !item.v2 && !item.ops && perms.canViewBoard(item.key),
  );

  const go = (key: string) => {
    setActiveNav(key);
    setOpen(false);
  };

  return (
    <>
      <button
        type="button"
        aria-label="打开导航菜单"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        className="fixed left-3 top-3 z-50 flex h-10 w-10 items-center justify-center rounded-lg border border-line bg-panel text-ink-2 backdrop-blur md:hidden"
      >
        <Menu size={20} />
      </button>

      <div
        className={`fixed inset-0 z-[60] md:hidden ${open ? "" : "pointer-events-none"}`}
        aria-hidden={!open}
      >
        <div
          onClick={() => setOpen(false)}
          className={`absolute inset-0 bg-black/60 transition-opacity duration-300 motion-reduce:transition-none ${
            open ? "opacity-100" : "opacity-0"
          }`}
        />
        <nav
          role="dialog"
          aria-modal="true"
          aria-label="主导航"
          className={`absolute left-0 top-0 flex h-full w-[280px] max-w-[85vw] flex-col border-r border-line bg-panel shadow-2xl transition-transform duration-300 motion-reduce:transition-none ${
            open ? "translate-x-0" : "-translate-x-full"
          }`}
        >
          <div className="flex h-16 items-center justify-between px-5">
            <span className="text-2xl font-black tracking-[.18em] text-ink">VILTROX</span>
            <button
              type="button"
              aria-label="关闭导航菜单"
              onClick={() => setOpen(false)}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:text-ink"
            >
              <X size={18} />
            </button>
          </div>
          <div className="flex-1 space-y-1 overflow-y-auto px-3 pb-6">
            {items.map(({ key, icon: Icon, label, badge }) => {
              const active = activeNav === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => go(key)}
                  className={`group flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition ${
                    active
                      ? "bg-accent-soft text-accent"
                      : "text-muted hover:bg-accent-soft hover:text-ink-2"
                  }`}
                >
                  <Icon size={16} className={active ? "text-accent" : "text-muted"} />
                  <span className="flex-1 text-left">{label}</span>
                  {badge ? (
                    <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10px] text-accent">
                      {badge}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        </nav>
      </div>
    </>
  );
}
