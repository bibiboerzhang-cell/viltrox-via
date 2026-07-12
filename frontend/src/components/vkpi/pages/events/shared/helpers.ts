// Events 模块 · 工具函数
// 收尾波(2026-07-12):旧页族退役,假时钟 TODAY=new Date("2026-05-26") 随之清除;
// EventDetailView/tabs 仍被 EventsBoardPage.embeds 消费,倒计时改真实当前时间。

import type { BudgetCell } from "./types";

export const TODAY = new Date();

export function daysUntil(dateStr: string): number {
  const d = new Date(dateStr);
  return Math.ceil((d.getTime() - TODAY.getTime()) / 86400000);
}

export const fmtMoney = (n: number): string => "$" + Math.round(n).toLocaleString();
export const fmtMoneyShort = (n: number): string => {
  if (n >= 1000) return "$" + (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "K";
  return "$" + n;
};
export const sum = (obj: Record<string, BudgetCell>, key: string): number =>
  Object.values(obj).reduce((s, v) => s + ((v as Record<string, number>)[key] || 0), 0);

export function healthColor(s: number): string {
  if (s >= 85) return "#10b981";
  if (s >= 70) return "#fbbf24";
  return "#ef4444";
}
