// U2 动效基元:数据新鲜度呼吸灯(全库通用,治「数据滞后没人知道」暗病)。
// 口径:绿=数据 ≤ freshMs(默认 24h,轻呼吸)/ 黄= ≤ warnMs(默认 72h,静态)/
//       红或灰=stale(staleTone 决定语气:数据断供=红;节点离线是常态=灰)/
//       无时间戳=空心灰圈(诚实「不知道」,绝不装 live)。
// title 永远给具体时间(timeLocal.freshnessLabel,观看者时区);呼吸动画在
// prefers-reduced-motion 下降级为静态(motion.css)。纯展示,零请求,零评分。
import React from "react";
import { freshnessLabel } from "../../../lib/timeLocal";
import "./motion.css";

export type FreshnessState = "fresh" | "warn" | "stale" | "unknown";

const FRESH_MS_DEFAULT = 24 * 60 * 60 * 1000;
const WARN_MS_DEFAULT = 72 * 60 * 60 * 1000;

/** 由时间戳算新鲜度档位(导出供测试/调用方复用)。 */
export function computeFreshness(
  ts?: string | number | Date | null,
  freshMs: number = FRESH_MS_DEFAULT,
  warnMs: number = WARN_MS_DEFAULT,
): FreshnessState {
  if (ts == null || ts === "") return "unknown";
  const t = ts instanceof Date ? ts.getTime() : new Date(ts).getTime();
  if (!Number.isFinite(t)) return "unknown";
  const age = Date.now() - t;
  if (age <= freshMs) return "fresh";
  if (age <= warnMs) return "warn";
  return "stale";
}

export interface FreshnessDotProps {
  /** 最新数据时间戳(UTC/ISO 皆可) */
  ts?: string | number | Date | null;
  /** 绿灯窗口(毫秒),默认 24h */
  freshMs?: number;
  /** 黄灯窗口(毫秒),默认 72h */
  warnMs?: number;
  /** 显式覆盖档位(调用方自有判定时用,如 worker 5min 心跳在线) */
  state?: FreshnessState;
  /** stale 的语气:danger=红(数据断供是事故,默认);muted=灰(离线是常态) */
  staleTone?: "danger" | "muted";
  /** title 前缀,如「最新证据」「晨报数据」 */
  label?: string;
  /** 完全自定义 title(给了就不再拼 freshnessLabel) */
  title?: string;
  className?: string;
}

export function FreshnessDot({
  ts,
  freshMs = FRESH_MS_DEFAULT,
  warnMs = WARN_MS_DEFAULT,
  state,
  staleTone = "danger",
  label,
  title,
  className,
}: FreshnessDotProps) {
  const st: FreshnessState = state || computeFreshness(ts, freshMs, warnMs);
  const finalTitle =
    title != null && title !== ""
      ? title
      : (label ? label + " · " : "") + freshnessLabel(ts);
  const toneCls =
    st === "fresh"
      ? "bg-emerald-400 vk-breathe"
      : st === "warn"
        ? "bg-amber-400"
        : st === "stale"
          ? staleTone === "muted"
            ? "bg-slate-600"
            : "bg-rose-500"
          : "border border-slate-600 bg-transparent";
  return (
    <span
      data-ui="freshness-dot"
      data-state={st}
      role="img"
      aria-label={finalTitle}
      title={finalTitle}
      className={
        "inline-block h-1.5 w-1.5 shrink-0 rounded-full " + toneCls + (className ? " " + className : "")
      }
      style={st === "fresh" ? { boxShadow: "0 0 6px rgba(52, 211, 153, 0.45)" } : undefined}
    />
  );
}
