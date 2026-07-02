// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";

const e = React.createElement;

// 诚实化(2026-07 P0):原先列的 ⌘K / G+D / ? 等快捷键全部没有对应实现(纯装饰清单),
// 会误导用户按了没反应。改为如实说明快捷键体系尚未接入,接入后再恢复清单。
export function ShortcutsModal({ onClose }: { onClose?: () => void }) {
  const { t } = useT();
  return e(CenterModal, { onClose, maxWidth: "md" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("h2", { className: "text-sm font-semibold text-white" }, t("键盘快捷键")),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    e("div", { className: "p-6 text-center" },
      e("div", { className: "text-[12px] text-slate-300" }, "快捷键体系尚未接入"),
      e("div", { className: "mt-1 text-[10px] text-slate-500" }, "接入后这里会列出真实可用的快捷键")
    )
  );
}
