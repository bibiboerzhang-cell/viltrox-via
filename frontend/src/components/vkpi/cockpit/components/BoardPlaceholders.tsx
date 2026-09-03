// 板块懒加载占位 + 「尚未接入」占位卡(U-B1,2026-09-02)。
// 原先散落在 CockpitApp.tsx 里的 20 处 Suspense fallback 与占位页写死 text-slate/text-white,
// 浅色主题下是白字白底。这里收口成两个 token 化组件:text-ink/text-ink-2/bg-panel/border-line,
// 三风格 × 明暗 6 组合都由 --ds-* 变量托底。
import React from "react";

const e = React.createElement;

export function BoardLoadingFallback({ label }: { label: string }) {
  return e(
    "div",
    { className: "min-h-[60vh] p-8 text-[12px] text-ink-2", role: "status", "aria-live": "polite" },
    `${label} 加载中...`,
  );
}

interface BoardPlaceholderCardProps {
  label: string;
  icon?: React.ComponentType<any> | null;
  onBack: () => void;
}

export function BoardPlaceholderCard({ label, icon, onBack }: BoardPlaceholderCardProps) {
  return e(
    "div",
    { className: "p-8 md:p-16 flex flex-col items-center justify-center text-center min-h-[60vh]" },
    e(
      "div",
      { className: "rounded-ds-lg border border-line bg-panel shadow-ds p-8 max-w-md w-full" },
      icon ? e(icon, { size: 32, className: "text-muted mx-auto mb-3" }) : null,
      e("div", { className: "text-base font-semibold text-ink mb-1" }, label || "Page"),
      e("div", { className: "text-[12px] text-ink-2 mb-4" }, "此页面尚未接入,在后续阶段完成。"),
      e(
        "button",
        {
          type: "button",
          onClick: onBack,
          className: "px-4 py-1.5 rounded-ds border border-line bg-card text-[11px] text-ink-2 hover:bg-accent-soft hover:text-ink",
        },
        "← 返回 Dashboard",
      ),
    ),
  );
}
