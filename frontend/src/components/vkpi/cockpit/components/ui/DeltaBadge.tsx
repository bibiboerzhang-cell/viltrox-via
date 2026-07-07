// U2 动效基元:数字较上次变化 ↑↓ 徽章(全库通用,供 W3-W6 复用)。
// 基线两种来源:① prev 显式传入(调用方自管基线);② id → localStorage 记住
// 「上次看到的值」(跨访问的「较上次」,本次值渲染后自动写回)。
// 诚实与安静:无基线/变化为 0 → 不渲染(绝不编「持平」徽章);出现或变化时
// 300ms 高亮一次(vk-delta-flash,reduced-motion 降级为静态,见 motion.css)。
// 纯展示,零请求,零评分。
import React from "react";
import "./motion.css";

const STORE_PREFIX = "vkpi:delta:";

function readBaseline(id?: string): number | null {
  if (!id) return null;
  try {
    const raw = localStorage.getItem(STORE_PREFIX + id);
    if (raw == null || raw === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

export interface DeltaBadgeProps {
  /** 当前值(非数值/缺失 → 安静不渲染) */
  value: number | null | undefined;
  /** 显式基线;与 id 二选一(都给以 prev 为准) */
  prev?: number | null;
  /** localStorage 基线键(如 "brief.scrape"),跨访问记住「上次」 */
  id?: string;
  /** 哪个方向是好消息(决定绿/红着色),默认 up(计数增长=好);告警类传 "down" */
  good?: "up" | "down";
  className?: string;
  title?: string;
}

export function DeltaBadge({ value, prev, id, good = "up", className = "", title }: DeltaBadgeProps) {
  // 挂载时读一次 localStorage 基线(session 内保持,避免自己追着自己跑)
  const [stored] = React.useState<number | null>(() => readBaseline(id));
  // 渲染后把本次值写回,供下次访问「较上次」(仅 id 模式)
  React.useEffect(() => {
    if (!id || value == null || !Number.isFinite(Number(value))) return;
    try {
      localStorage.setItem(STORE_PREFIX + id, String(Number(value)));
    } catch {
      /* ignore */
    }
  }, [id, value]);

  const baseline = prev != null && Number.isFinite(Number(prev)) ? Number(prev) : stored;
  if (value == null || !Number.isFinite(Number(value)) || baseline == null) return null;
  const delta = Number(value) - baseline;
  if (delta === 0) return null;

  const up = delta > 0;
  const isGood = up === (good === "up");
  const mag = Math.round(Math.abs(delta) * 100) / 100;
  const finalTitle = title || `较上次 ${baseline} → ${Number(value)}`;
  return (
    <span data-ui="delta-badge" title={finalTitle} className={"inline-flex " + className}>
      {/* key=delta:变化时重挂载,让 300ms 高亮动画重放一次 */}
      <span
        key={String(delta)}
        className={
          "vk-delta-flash inline-flex items-center rounded px-1 py-0.5 text-[9px] font-semibold tabular-nums " +
          (isGood ? "bg-emerald-400/10 text-emerald-300" : "bg-rose-400/10 text-rose-300")
        }
      >
        {(up ? "↑" : "↓") + mag}
      </span>
    </span>
  );
}
