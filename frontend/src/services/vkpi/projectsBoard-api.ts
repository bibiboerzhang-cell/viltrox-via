import { apiFetch } from "../http";

// Projects 板块页范式专用只读端点(全真,零编造;own-only 由服务端 scope 裁剪):
//   GET /api/admin/vkpi/attribution                  —— vkpi_sales_attributions 行(revenue_cents 美分)
//   GET /api/admin/vkpi/projects/observation-windows —— vkpi_project_content_observation_windows
//       (不带 project_id = 全项目窗口;签收→开窗→内容匹配链的「开窗」环节)
// 金额红线:库存美分(*_cents),展示前必须 centsToUsd 唯一换算点(历史 100 倍缺陷,
// 换算与合计均带单测,见 ProjectsBoardPage.smoke.test.tsx)。

export type Row = Record<string, any>;

export interface VkpiSalesAttributionRow {
  id: number;
  source_platform: string | null;
  source_ref: string | null;
  project_id: number | null;
  kol_id: number | null;
  staff_id: number | null;
  product_sku: string | null;
  order_id: string | null;
  revenue_cents: number | null;
  commission_cents: number | null;
  currency: string | null;
  attribution_model: string | null;
  confidence: string | null;
  occurred_at: string | null;
  imported_at: string | null;
}

export function listSalesAttributions(
  token: string,
  limit = 500,
): Promise<{ attributions?: Row[] }> {
  return apiFetch(`/api/admin/vkpi/attribution?limit=${encodeURIComponent(String(limit))}`, { timeoutMs: 15000 }, token);
}

export function listAllObservationWindows(
  token: string,
  status = "all",
): Promise<{ status?: string; count?: number; items?: Row[]; note?: string }> {
  return apiFetch(
    `/api/admin/vkpi/projects/observation-windows?status=${encodeURIComponent(status)}`,
    { timeoutMs: 15000 },
    token,
  );
}

/** 分 → 美元(展示端唯一换算点;非有限值 → null 诚实缺席,绝不当 0)。 */
export function centsToUsd(cents: unknown): number | null {
  const n = Number(cents);
  if (cents === null || cents === undefined || cents === "" || !Number.isFinite(n)) return null;
  return n / 100;
}

/** 美元门面(固定两位小数;null/非数 → "—" 诚实缺席)。 */
export function fmtUsd2(usd: number | null | undefined): string {
  if (usd === null || usd === undefined || !Number.isFinite(Number(usd))) return "—";
  return `$${Number(usd).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** 归因行合计:Σ revenue_cents → USD。输入非数组 = 端点未到货 → null(pending);
 *  空数组 = 有链路零行 → 0(真零)。行内非数金额跳过不编数。 */
export function sumRevenueUsd(rows: Row[] | null | undefined): number | null {
  if (!Array.isArray(rows)) return null;
  let cents = 0;
  for (const row of rows) {
    const n = Number(row?.revenue_cents);
    if (Number.isFinite(n)) cents += n;
  }
  return cents / 100;
}

/** 观察窗口状态门面(真状态词 → 人话;未知状态原样透出不装)。 */
export const WINDOW_STATUS: Record<string, { label: string; cls: string }> = {
  scanning: { label: "观察中", cls: "border-warn bg-warn-soft text-warn" },
  matched: { label: "已匹配", cls: "border-good bg-good-soft text-good" },
  expired: { label: "已过期", cls: "border-line text-muted" },
  content_missing: { label: "未见内容", cls: "border-crit bg-crit-soft text-crit" },
};

/** 归因置信门面。 */
export const ATTRIBUTION_CONFIDENCE: Record<string, { label: string; cls: string }> = {
  confirmed: { label: "已确认", cls: "border-good bg-good-soft text-good" },
  estimated: { label: "估算", cls: "border-warn bg-warn-soft text-warn" },
  manual: { label: "手录", cls: "border-info bg-info-soft text-info" },
  unmatched: { label: "未匹配", cls: "border-line text-muted" },
};
