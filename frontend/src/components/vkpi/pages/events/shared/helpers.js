// Events 模块 · 工具函数
// 接入时: TODAY 在生产环境应改为 new Date()

export const TODAY = new Date("2026-05-26");

export function daysUntil(dateStr) {
  const d = new Date(dateStr);
  return Math.ceil((d - TODAY) / 86400000);
}

export const fmtMoney = (n) => "$" + Math.round(n).toLocaleString();
export const fmtMoneyShort = (n) => {
  if (n >= 1000) return "$" + (n/1000).toFixed(n >= 10000 ? 0 : 1) + "K";
  return "$" + n;
};
export const sum = (obj, key) => Object.values(obj).reduce((s, v) => s + (v[key] || 0), 0);

export function healthColor(s) {
  if (s >= 85) return "#10b981";
  if (s >= 70) return "#fbbf24";
  return "#ef4444";
}
