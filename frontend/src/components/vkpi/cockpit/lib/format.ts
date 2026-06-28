// Verbatim from vkpi_v6.15.7_integrated.html


export function formatNumber(n: any) {
  if (n === null || n === undefined) return "—";
  const value = typeof n === "number" ? n : Number(String(n).replace(/,/g, ""));
  if (!Number.isFinite(value)) return "—";
  if (value >= 1e9) return (value / 1e9).toFixed(2) + "B";
  if (value >= 1e6) return (value / 1e6).toFixed(2) + "M";
  if (value >= 1e3) return (value / 1e3).toFixed(1) + "K";
  return String(value);
}

export function formatPercent(n: any, digits = 2) {
  if (n === null || n === undefined) return "—";
  const value = typeof n === "number" ? n : Number(String(n).replace(/%/g, ""));
  return Number.isFinite(value) ? value.toFixed(digits) + "%" : "—";
}

export function formatMoney(cents: any) {
  if (cents === null || cents === undefined) return "—";
  const dollars = cents / 100;
  if (dollars >= 1e6) return "$" + (dollars / 1e6).toFixed(1) + "M";
  if (dollars >= 1e3) return "$" + (dollars / 1e3).toFixed(0) + "k";
  return "$" + Math.round(dollars);
}
