// 2026-07 门面重建:布局对齐 mockup(标题+实时副标 / 居中搜索 / 外观弹层 / 通知 / 账户),
// 配色全吃 --ds-* token(随全局 ThemeProvider 三风格×明暗切换)。搜索/进度中心/Help/消息/
// Report/通知/账户菜单等真功能与 handler 逻辑一字不改,仅换皮 + 新增「外观弹层」(接 ThemeProvider)。

import React from "react";
import { createPortal } from "react-dom";
import { Bell, ChevronDown, FileText, HelpCircle, MessageCircle, Minus, MoreHorizontal, Palette, PencilLine, Plus, Sparkles } from "lucide-react";
import { Avatar } from "./components/Avatar";
import { TopProgressCenter } from "./components/TopProgressCenter";
import { NAV_ITEMS } from "./data/navItems";
import { useTheme, type Style, type Theme } from "../../../app/providers/ThemeProvider";
import { AskCommandOverlay } from "./components/AskCommandOverlay";

const e = React.createElement;

// ── 外观弹层(玻璃/仪器/单色 × 浅/深,接全局 ThemeProvider)────────────────────
export function AppearancePopover() {
  const { theme, style, glassOpacity, setTheme, setStyle, setGlassOpacity } = useTheme();
  const [open, setOpen] = React.useState(false);
  const [position, setPosition] = React.useState({ top: 0, left: 0 });
  const triggerRef = React.useRef<HTMLButtonElement | null>(null);
  const menuRef = React.useRef<HTMLDivElement | null>(null);
  const menuId = React.useId();

  const updatePosition = React.useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const width = 248;
    const gap = 8;
    const edge = 12;
    const menuHeight = menuRef.current?.offsetHeight || 220;
    const below = rect.bottom + gap;
    const top = below + menuHeight <= window.innerHeight - edge
      ? below
      : Math.max(edge, rect.top - menuHeight - gap);
    const left = Math.min(
      Math.max(edge, rect.right - width),
      Math.max(edge, window.innerWidth - width - edge),
    );
    setPosition({ top, left });
  }, []);

  React.useLayoutEffect(() => {
    if (!open) return;
    updatePosition();
    const frame = window.requestAnimationFrame(updatePosition);
    return () => window.cancelAnimationFrame(frame);
  }, [open, style, theme, updatePosition]);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (ev: MouseEvent) => {
      if (!(ev.target instanceof Node)) return;
      if (triggerRef.current?.contains(ev.target) || menuRef.current?.contains(ev.target)) return;
      setOpen(false);
    };
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, updatePosition]);

  const styles: Array<[Style, string]> = [["glass", "玻璃"], ["instrument", "仪器"], ["commandos", "单色"]];
  const themes: Array<[Theme, string]> = [["light", "浅"], ["dark", "深"]];
  const menu = open && typeof document !== "undefined"
    ? createPortal(
        e("div", {
          ref: menuRef,
          id: menuId,
          role: "dialog",
          "aria-label": "桌面外观",
          className: "vkpi-appearance-menu",
          style: { position: "fixed", top: position.top, left: position.left },
        },
          e("div", { className: "vkpi-appearance-menu__label" }, "外观风格"),
          e("div", { className: "vkpi-appearance-menu__segment" }, styles.map(([value, label]) => e("button", {
            key: value,
            type: "button",
            onClick: () => setStyle(value),
            className: style === value ? "is-active" : "",
            "aria-pressed": style === value,
          }, label))),
          e("div", { className: "vkpi-appearance-menu__label" }, "明暗主题"),
          e("div", { className: "vkpi-appearance-menu__segment" }, themes.map(([value, label]) => e("button", {
            key: value,
            type: "button",
            onClick: () => setTheme(value),
            className: theme === value ? "is-active" : "",
            "aria-pressed": theme === value,
          }, label))),
          style === "glass" && e(React.Fragment, null,
            e("div", { className: "vkpi-appearance-menu__label vkpi-appearance-menu__label--value" },
              e("span", null, "面板清晰度"),
              e("output", { htmlFor: "vkpi-glass-opacity" }, `${glassOpacity}%`),
            ),
            e("div", { className: "vkpi-appearance-menu__range-control" },
              e("button", {
                type: "button",
                title: "降低面板清晰度",
                "aria-label": "降低玻璃面板清晰度",
                disabled: glassOpacity <= 60,
                onClick: () => setGlassOpacity(glassOpacity - 1),
              }, e(Minus, { size: 12 })),
              e("input", {
                id: "vkpi-glass-opacity",
                className: "vkpi-appearance-menu__range",
                type: "range",
                min: 60,
                max: 96,
                step: 1,
                value: glassOpacity,
                onInput: (event: React.FormEvent<HTMLInputElement>) => setGlassOpacity(Number(event.currentTarget.value)),
                onChange: (event: React.ChangeEvent<HTMLInputElement>) => setGlassOpacity(Number(event.target.value)),
                "aria-label": "玻璃面板清晰度",
                "aria-valuetext": `${glassOpacity}%`,
              }),
              e("button", {
                type: "button",
                title: "提高面板清晰度",
                "aria-label": "提高玻璃面板清晰度",
                disabled: glassOpacity >= 96,
                onClick: () => setGlassOpacity(glassOpacity + 1),
              }, e(Plus, { size: 12 })),
            ),
            e("div", { className: "vkpi-appearance-menu__range-labels", "aria-hidden": "true" },
              e("span", null, "更透"),
              e("span", null, "更清晰"),
            ),
          )
        ),
        document.body,
      )
    : null;

  return e(React.Fragment, null,
    e("div", { className: "vkpi-appearance-popover" },
      e("button", {
        ref: triggerRef,
        type: "button",
        onClick: () => setOpen((value) => !value),
        "aria-label": "外观 / 主题",
        "aria-haspopup": "dialog",
        "aria-expanded": open,
        "aria-controls": open ? menuId : undefined,
        title: "桌面外观",
        className: "vkpi-icon-button rounded-lg p-2 text-muted hover:bg-accent-soft hover:text-ink",
      }, e(Palette, { size: 16 }))
    ),
    menu,
  );
}

function ResponsiveToolsMenu({
  helpBtnRef,
  messagesBtnRef,
  onToggleHelp,
  onToggleMessages,
  activeReminders,
  setReportOpen,
}: any) {
  const [open, setOpen] = React.useState(false);
  const wrapRef = React.useRef<HTMLDivElement | null>(null);
  const triggerRef = React.useRef<HTMLButtonElement | null>(null);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (wrapRef.current && event.target instanceof Node && !wrapRef.current.contains(event.target)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  const openAnchored = (anchorRef: any, openPopover: () => void) => {
    if (anchorRef && triggerRef.current) anchorRef.current = triggerRef.current;
    setOpen(false);
    openPopover();
  };
  const reminderCount = activeReminders.filter((reminder: any) => reminder.status === "todo").length;

  return e("div", { ref: wrapRef, className: "vkpi-topbar-overflow" },
    e("button", {
      ref: triggerRef,
      type: "button",
      className: "vkpi-icon-button",
      onClick: () => setOpen((value) => !value),
      "aria-label": "More tools",
      "aria-expanded": open,
      title: "更多工具",
    }, e(MoreHorizontal, { size: 16 })),
    open && e("div", { className: "vkpi-topbar-overflow__menu", role: "menu" },
      e("div", { className: "vkpi-topbar-overflow__progress" },
        e("span", null, "任务进度"),
        e(TopProgressCenter)
      ),
      e("button", { type: "button", role: "menuitem", onClick: () => openAnchored(helpBtnRef, onToggleHelp) },
        e(HelpCircle, { size: 14 }), e("span", null, "Help")
      ),
      e("button", { type: "button", role: "menuitem", onClick: () => openAnchored(messagesBtnRef, onToggleMessages) },
        e(MessageCircle, { size: 14 }), e("span", null, "工作提醒"), reminderCount > 0 && e("small", null, reminderCount)
      ),
      e("button", { type: "button", role: "menuitem", onClick: () => { setOpen(false); setReportOpen(true); } },
        e(FileText, { size: 14 }), e("span", null, "生成 Report")
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
  apiToken,
  onNavigate,
  dashboardEditing,
  setDashboardEditing,
}: any) {
  const label = (NAV_ITEMS.find((n: any) => n.key === activeNav)?.label) || "Dashboard";
  const iconBtn = "vkpi-icon-button rounded-lg p-2 text-muted hover:bg-accent-soft hover:text-ink";
  const [askOpen, setAskOpen] = React.useState(false);
  const toggleExclusivePopover = React.useCallback((target: "help" | "messages" | "notifications" | "user") => {
    const setters = {
      help: setShowHelp,
      messages: setShowMessages,
      notifications: setShowNotifs,
      user: setShowUserMenu,
    };
    Object.entries(setters).forEach(([key, setter]) => {
      if (key !== target) setter(false);
    });
    setters[target]((current: boolean) => !current);
  }, [setShowHelp, setShowMessages, setShowNotifs, setShowUserMenu]);
  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setAskOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return e(React.Fragment, null,
   e("header", { className: "vkpi-topbar sticky top-0 z-40 flex h-[60px] items-center gap-4 border-b border-line px-4 md:px-[22px]" },
    // 左:标题 + 实时副标
    e("div", { className: "vkpi-topbar-title flex flex-none flex-col justify-center" },
      e("h1", { className: "text-[16px] font-semibold leading-none text-ink" }, label),
      e("div", { className: "mt-1 flex items-center gap-1.5 text-[10px] text-muted" },
        e("span", { className: "h-1.5 w-1.5 rounded-full bg-good" }),
        activeNav === "dashboard" ? "增长总览 · 实时" : (t ? t("实时") : "实时")
      )
    ),
    // 中:样品同款 Ask 入口;弹层内同时跑真全局搜索与 Intelligent 问答。
    e("button", {
      type: "button",
      className: "vkpi-ask-trigger mx-auto hidden w-full max-w-[440px] items-center md:flex",
      onClick: () => setAskOpen(true),
      "aria-label": "打开 Intelligent 问答与全局搜索",
    },
      e(Sparkles, { size: 14 }),
      e("span", null, "问一问 · AI 或搜索 KOL / 项目…"),
      e("kbd", null, "⌘K")
    ),
    // 右:操作簇(真功能全保留)+ 外观弹层 + 账户
    e("div", { className: "vkpi-topbar-actions flex flex-none items-center gap-1.5 md:gap-2" },
      activeNav === "dashboard" && e("button", {
        type: "button",
        onClick: () => setDashboardEditing?.(!dashboardEditing),
        className: `vkpi-layout-edit-button hidden items-center gap-1.5 md:flex ${dashboardEditing ? "is-active" : ""}`,
        "aria-pressed": Boolean(dashboardEditing),
      }, e(PencilLine, { size: 13 }), e("span", null, dashboardEditing ? "完成布局" : "编辑布局")),
      e(ResponsiveToolsMenu, {
        helpBtnRef,
        messagesBtnRef,
        onToggleHelp: () => toggleExclusivePopover("help"),
        onToggleMessages: () => toggleExclusivePopover("messages"),
        activeReminders,
        setReportOpen,
      }),
      e(AppearancePopover),
      e("button", { ref: notifsBtnRef, onClick: () => toggleExclusivePopover("notifications"), "aria-label": "Notifications", className: `relative ${iconBtn}` },
        e(Bell, { size: 16 }),
        runtimeNotifications.filter((n: any) => n.unread).length > 0 && e("span", { className: "absolute right-1 top-1 h-2 w-2 rounded-full bg-crit" })
      ),
      // 账户(唯一身份锚点,侧栏不再重复)
      e("button", { ref: userMenuBtnRef, onClick: () => toggleExclusivePopover("user"), "aria-label": "User Menu", className: "vkpi-account ml-1 flex items-center gap-2 rounded-lg py-1 pl-2 pr-1 hover:bg-accent-soft" },
        e(Avatar, {
          src: viewingAs ? null : currentUser.avatarUrl,
          alt: currentUser.name, size: 32,
          fallback: viewingAs ? viewingAs.avatar : currentUser.avatar,
          gradient: viewingAs ? viewingAs.color : currentUser.avatarGradient,
        }),
        e("div", { className: "vkpi-account__copy hidden text-left text-xs sm:block" },
          e("div", { className: "flex items-center gap-1 text-ink" },
            viewingAs ? viewingAs.name : currentUser.name,
            viewingAs && e("span", { className: "rounded bg-accent-soft px-1 py-0.5 text-[8px] text-accent" }, t("正在以"))
          ),
          e("div", { className: "text-muted" }, viewingAs ? viewingAs.title : (currentUser.role === "admin" ? t("Admin") : t("成员")))
        ),
        e(ChevronDown, { size: 14, className: "vkpi-account__chevron hidden text-muted sm:block" })
      )
    )
   ),
   e(AskCommandOverlay, { open: askOpen, onClose: () => setAskOpen(false), apiToken, onNavigate })
  );
}
