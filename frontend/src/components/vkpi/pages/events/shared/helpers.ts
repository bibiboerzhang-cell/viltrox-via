// Events 模块 · 工具函数
// 收尾波(2026-07-12):旧页族退役,假时钟 TODAY=new Date("2026-05-26") 随之清除;
// EventDetailView/tabs 仍被 EventsBoardPage.embeds 消费,倒计时改真实当前时间。

import type { BudgetCell } from "./types";

export const TODAY = new Date();

export function daysUntil(dateStr: string): number {
  const today = calendarDaySerial(new Date());
  const target = calendarDaySerial(dateStr);
  return today == null || target == null ? 0 : Math.round((target - today) / DAY_MS);
}

export type EventTimingPhase = "upcoming" | "starts_today" | "ongoing" | "ended" | "invalid";

export interface EventTiming {
  phase: EventTimingPhase;
  /** Upcoming = days until start; ongoing = days since start; ended = days since end. */
  days: number;
  label: string;
}

const DAY_MS = 86_400_000;

function calendarDaySerial(raw: string | Date): number | null {
  if (raw instanceof Date) {
    if (Number.isNaN(raw.getTime())) return null;
    return Date.UTC(raw.getFullYear(), raw.getMonth(), raw.getDate());
  }
  const value = String(raw || "").trim();
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (dateOnly) {
    const year = Number(dateOnly[1]);
    const month = Number(dateOnly[2]);
    const day = Number(dateOnly[3]);
    const serial = Date.UTC(year, month - 1, day);
    const checked = new Date(serial);
    if (
      checked.getUTCFullYear() === year
      && checked.getUTCMonth() === month - 1
      && checked.getUTCDate() === day
    ) return serial;
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return Date.UTC(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
}

/**
 * Calendar-day event state based on both start and end dates.
 * This is display-only: it never mutates the operator-managed Event status.
 */
export function eventTiming(startDate: string, endDate: string, now: Date = new Date()): EventTiming {
  const today = calendarDaySerial(now);
  const start = calendarDaySerial(startDate);
  const end = calendarDaySerial(endDate || startDate);
  if (today == null || start == null || end == null || end < start) {
    return { phase: "invalid", days: 0, label: "日期待确认" };
  }
  const untilStart = Math.round((start - today) / DAY_MS);
  if (untilStart > 0) return { phase: "upcoming", days: untilStart, label: `倒计时 ${untilStart} 天` };
  if (untilStart === 0) return { phase: "starts_today", days: 0, label: "今天开幕!" };
  const sinceEnd = Math.round((today - end) / DAY_MS);
  if (sinceEnd > 0) return { phase: "ended", days: sinceEnd, label: `已结束 ${sinceEnd} 天` };
  const sinceStart = Math.max(1, Math.round((today - start) / DAY_MS) + 1);
  return { phase: "ongoing", days: sinceStart, label: `进行中 · 第 ${sinceStart} 天` };
}

/** Returns null when no positive denominator exists, so callers never render NaN/Infinity. */
export function percentOf(value: number, total: number): number | null {
  const numerator = Number(value);
  const denominator = Number(total);
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) return null;
  return Math.round(numerator / denominator * 100);
}

export const fmtMoney = (n: number): string => "$" + Math.round(n).toLocaleString();
export const fmtMoneyShort = (n: number): string => {
  if (n >= 1000) return "$" + (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "K";
  return "$" + n;
};
export const sum = (obj: Record<string, BudgetCell>, key: string): number =>
  Object.values(obj).reduce((s, v) => s + ((v as Record<string, number>)[key] || 0), 0);

export function healthColor(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s)) return "#64748b";
  if (s >= 85) return "#10b981";
  if (s >= 70) return "#fbbf24";
  return "#ef4444";
}
