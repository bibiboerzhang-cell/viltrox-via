// U2 动效基元:骨架屏(shimmer 微光扫过),替代「加载中…」文字(全库通用)。
// 只在加载期渲染,内容到位即消失;沿用 slate/白低透明度体系(motion.css);
// prefers-reduced-motion 降级为静态灰块。aria-hidden(对读屏安静)。
import React from "react";
import "./motion.css";

export interface SkeletonBlockProps {
  /** 尺寸/圆角交给调用方,如 "h-3 w-24 rounded" / "h-5 w-5 rounded-full" */
  className?: string;
  /** 便捷:渲染 n 行文字形骨架(末行 2/3 宽);>1 时忽略 className 的单块语义 */
  lines?: number;
}

export function SkeletonBlock({ className = "h-3 w-full rounded", lines }: SkeletonBlockProps) {
  if (lines != null && lines > 1) {
    return (
      <div aria-hidden data-ui="skeleton" className="space-y-1.5">
        {Array.from({ length: lines }).map((_, i) => (
          <div key={i} className={"vk-shimmer h-2.5 rounded " + (i === lines - 1 ? "w-2/3" : "w-full")} />
        ))}
      </div>
    );
  }
  return <div aria-hidden data-ui="skeleton" className={"vk-shimmer " + className} />;
}
