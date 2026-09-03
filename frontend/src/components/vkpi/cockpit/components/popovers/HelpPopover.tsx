// 源自 vkpi_v6.15.7_integrated.html;2026-09-02(U-B2)重做:
//   · 底部「技术支持」块原来写死一位真人姓名/组别/电话 + 假「已认证」徽章,公测=把个人身份暴露给外部测试者。
//     现在改成「当前账号」块,读真实登录人(user 与账户菜单同源);没有登录人信息就不渲染。
//     支持联系人只在调用方显式传 supportContact 配置时才出现(目前无配置 → 不渲染)。
//   · 版本脚注改读构建信息(lib/buildInfo),不再写死过期的版本号/日期。
//   · 全部 white/slate/amber/emerald 写死色换 --ds-* token 类,浅色主题不再白字白底。
import React from "react";
import { BookOpen, Bug, ChevronRight, Keyboard, LifeBuoy } from "lucide-react";
import { frontendBuildInfo, shortBuildSha } from "../../../../../lib/buildInfo";
import { PopoverWrapper } from "./PopoverWrapper";

const e = React.createElement;

export interface HelpSupportContact {
  name: string;
  org?: string;
  note?: string;
}

interface HelpUser {
  name?: string;
  email?: string;
  role?: string;
  avatar?: string;
  avatarUrl?: string;
  avatarGradient?: string;
}

function isAdminRole(role: unknown): boolean {
  const raw = String(role || "");
  return ["admin", "owner"].includes(raw.toLowerCase()) || raw === "管理层";
}

function builtAtLabel(): string {
  const raw = String(frontendBuildInfo.builtAt || "");
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(raw);
  return match ? `${match[1]}/${match[2]}/${match[3]}` : "";
}

function renderCurrentUser(user: HelpUser | null | undefined, t: any) {
  const name = String(user?.name || "").trim();
  const email = String(user?.email || "").trim();
  if (!name && !email) return null;
  const initials = String(user?.avatar || name.slice(0, 1) || "V").toUpperCase();
  return e("div", { className: "border-t border-line p-3", "data-testid": "help-current-user" },
    e("div", { className: "text-[10px] uppercase tracking-wider text-muted mb-2" }, t("个人资料")),
    e("div", { className: "flex items-start gap-2.5" },
      e("div", {
        className: "shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-[12px] font-bold text-[color:var(--ds-on-accent)] bg-accent overflow-hidden",
        style: user?.avatarGradient ? { background: user.avatarGradient } : undefined,
        "aria-hidden": true,
      }, user?.avatarUrl ? e("img", { src: user.avatarUrl, alt: "", className: "w-full h-full object-cover" }) : initials),
      e("div", { className: "flex-1 min-w-0" },
        e("div", { className: "flex items-center gap-1.5 mb-0.5" },
          name && e("span", { className: "text-[11px] font-medium text-ink truncate" }, name),
          e("span", { className: "text-[9px] px-1 py-0.5 rounded bg-accent-soft text-accent" }, isAdminRole(user?.role) ? t("管理员") : t("成员"))
        ),
        email && e("div", { className: "text-[10px] text-ink-2 truncate" }, email)
      )
    )
  );
}

function renderSupportContact(contact: HelpSupportContact | null | undefined) {
  if (!contact || !String(contact.name || "").trim()) return null;
  return e("div", { className: "border-t border-line p-3 flex items-start gap-2.5", "data-testid": "help-support-contact" },
    e("div", { className: "shrink-0 w-7 h-7 rounded-md flex items-center justify-center bg-card" }, e(LifeBuoy, { size: 13, className: "text-muted" })),
    e("div", { className: "flex-1 min-w-0" },
      e("div", { className: "text-[11px] font-medium text-ink" }, contact.name),
      contact.org && e("div", { className: "text-[10px] text-ink-2" }, contact.org),
      contact.note && e("div", { className: "text-[10px] text-muted" }, contact.note)
    )
  );
}

export function HelpPopover({ onClose, anchorRef, t, user, supportContact, onOpenDocs, onOpenShortcuts, onOpenFeedback }: any) {
  const items = [
    { icon: BookOpen,    title: t("文档 & 指南"),       desc: t("KOL 找人到项目操作说明"), badge: "PDF", disabled: !onOpenDocs, onClick: onOpenDocs },
    { icon: Keyboard,    title: t("键盘快捷键"),        desc: "⌘ K / ⌘ ? / ⌘ /",       badge: null,    onClick: onOpenShortcuts },
    { icon: Bug,         title: t("提交反馈 / 报 bug"), desc: t("发送到管理通知列表"), badge: t("已接入"), onClick: onOpenFeedback },
  ];
  const built = builtAtLabel();
  return e(PopoverWrapper, { onClose, anchorRef, width: 320 },
    e("div", { className: "w-[300px]" },
      e("div", { className: "px-3 py-2 border-b border-line" },
        e("div", { className: "text-[11px] font-semibold text-ink" }, t("帮助 & 反馈"))
      ),
      e("div", { className: "py-1" },
        items.map((item: any, i: any) => e("button", {
          key: i,
          type: "button",
          disabled: item.disabled,
          onClick: () => {
            if (item.disabled) return;
            onClose();
            item.onClick && item.onClick();
          },
          className: `w-full flex items-center gap-3 px-3 py-2 text-left transition-colors ${item.disabled ? "cursor-not-allowed opacity-55" : "hover:bg-accent-soft"}`
        },
          e("div", { className: "shrink-0 w-7 h-7 rounded-md flex items-center justify-center bg-card" },
            e(item.icon, { size: 13, className: "text-muted" })
          ),
          e("div", { className: "flex-1 min-w-0" },
            e("div", { className: "flex items-center gap-1.5" },
              e("span", { className: "text-[12px] text-ink" }, item.title),
              item.badge && e("span", { className: "text-[8px] uppercase tracking-wider px-1 py-0.5 rounded bg-warn-soft text-warn" }, item.badge)
            ),
            e("div", { className: "text-[10px] text-muted" }, item.desc)
          ),
          !item.disabled && e(ChevronRight, { size: 12, className: "text-muted" })
        ))
      ),
      renderCurrentUser(user, t),
      renderSupportContact(supportContact),
      e("div", { className: "px-3 py-2 border-t border-line flex items-center justify-between" },
        e("span", { className: "text-[9px] text-muted" }, `V-KPI · ${shortBuildSha(frontendBuildInfo.gitSha)}`),
        built && e("span", { className: "text-[9px] text-muted" }, `${t("更新")} ${built}`)
      )
    )
  );
}
