// 纯重构:从 KOLDetailDrawer.tsx 抽出的共享纯 helper(行为逐字搬运,零改)。
// 主文件与各 KOLDrawer* 子组件统一从此处 import,对外行为不变。

export function asArray(value: any) {
  return Array.isArray(value) ? value : [];
}

export function numberOr(value: any, fallback: any = null) {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string" && value.trim() === "") return fallback;
  const numeric = typeof value === "number" ? value : Number(String(value ?? "").replace(/[% ,]/g, ""));
  return Number.isFinite(numeric) ? numeric : fallback;
}

export function fixedOrDash(value: any, digits = 2) {
  const numeric = numberOr(value);
  return numeric == null ? "—" : numeric.toFixed(digits);
}

export function pctOrZero(value: any) {
  return numberOr(value, 0) * 100;
}

export function scoreValue(value: any, fallback = 0) {
  const numeric = numberOr(value);
  if (numeric == null) return fallback;
  return Math.max(0, Math.min(100, numeric));
}

export function scoreText(value: any) {
  const numeric = numberOr(value);
  if (numeric == null) return "—";
  return String(Math.round(Math.max(0, Math.min(100, numeric))));
}

export function recordOr(value: any): any {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function compactText(value: any, max = 180) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

export function concernLabel(value: any) {
  const text = String(value || "").trim();
  const labels = {
    contact_missing: "联系方式缺失",
    missing_kol_profile: "主表画像缺失",
    no_cooperation_history: "暂无合作历史",
    risk_watchlist: "风险观察名单",
  };
  return (labels as any)[text] || text.replace(/_/g, " ");
}
