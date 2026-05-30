// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";

const e = React.createElement;

export function ShortcutsModal({ onClose }) {
  const { t } = useT();
  const shortcuts = [
    { keys: ["⌘", "K"], desc: "全局搜索" },
    { keys: ["⌘", "?"], desc: "打开帮助" },
    { keys: ["⌘", "/"], desc: "聚焦搜索框" },
    { keys: ["G", "D"], desc: "Go to Dashboard" },
    { keys: ["G", "K"], desc: "Go to KOL Pool" },
    { keys: ["G", "P"], desc: "Go to Projects" },
    { keys: ["G", "R"], desc: "Go to Reports" },
    { keys: ["G", "I"], desc: "Go to Intelligence" },
    { keys: ["?"],      desc: "显示所有快捷键" },
    { keys: ["Esc"],    desc: "关闭弹窗 / 退出搜索" },
  ];
  return e(CenterModal, { onClose, maxWidth: "md" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("h2", { className: "text-sm font-semibold text-white" }, t("键盘快捷键")),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    e("div", { className: "p-5 space-y-1.5 max-h-[60vh] overflow-y-auto" },
      shortcuts.map((s, i) => e("div", {
        key: i, className: "flex items-center justify-between px-3 py-2 rounded-md hover:bg-white/[0.03]"
      },
        e("span", { className: "text-[11px] text-slate-300" }, s.desc),
        e("div", { className: "flex items-center gap-1" },
          s.keys.map((k, j) => e("kbd", {
            key: j,
            className: "px-2 py-0.5 text-[10px] font-mono border border-white/[0.1] bg-white/[0.04] rounded text-slate-300"
          }, k))
        )
      ))
    )
  );
}
