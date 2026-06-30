// Pure helpers / types / constants extracted from ShopifyHubPage.tsx.
// Behavior-preserving move: function bodies are verbatim. No container state here.

export type Row = Record<string, unknown>;
export type TabKey = "connect" | "generate" | "track";

// 自建短链生成器退役开关 —— true=Region ② 整块不渲染(代码留着,可回滚)。
// 短链生成已迁移到 GOAFFPRO:每个 KOL 注册为 affiliate 后自动获得追踪链 + 优惠码。
export const RETIRE_SELF_LINK = true;

// ---------------------------------------------------------------------------
// Small field helpers (defensive reads — list shapes vary across endpoints).
// ---------------------------------------------------------------------------

export function pickStr(row: Row, keys: string[], fallback = ""): string {
  for (const k of keys) {
    const v = row[k];
    if (v !== undefined && v !== null && String(v).trim() !== "") return String(v);
  }
  return fallback;
}

export function pickNum(row: Row, keys: string[]): number | null {
  for (const k of keys) {
    const v = row[k];
    if (v !== undefined && v !== null && v !== "" && !Number.isNaN(Number(v))) return Number(v);
  }
  return null;
}

export function fmtMoney(v: number | null): string {
  if (v === null) return "—";
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function fmtRoi(v: number | null): string {
  if (v === null) return "—";
  return `${v.toFixed(2)}x`;
}
