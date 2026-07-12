import { apiFetch } from "../http";

// Shopify 板块页范式专用只读端点(全真,零编造):
//   GET /api/admin/vkpi/shopify/gmv —— 对账口径 GMV:订单台账(vkpi_shopify_orders)
//       有行用台账,否则回落归因台账净额(vkpi_sales_attributions,confirmed+refund,
//       退款负行冲抵);两台账都空 → 全 0 诚实待接入,gmv_source 标注真实来源。
// 金额红线:*_cents 字段展示前必须走 projectsBoard-api 的 centsToUsd 唯一换算点
// (历史 100 倍缺陷,带守卫单测);GOAFFPRO summary 的 gmv_usd/commission_usd 服务端
// 已是美元(缓存 gmv_cents ÷ 100 后端换算)—— 前端只许 asUsd 直读,绝不二次 ÷100
// (双向守卫单测见 ShopifyBoardPage.smoke.test.tsx)。

export interface ShopifyGmvSummary {
  gmv_cents?: number;
  order_count?: number;
  currency?: string | null;
  mixed_currency?: boolean;
  // vkpi_shopify_orders.total_price_cents[ingested] |
  // vkpi_sales_attributions.revenue_cents[shopify,confirmed+refund,net] | awaiting_data
  gmv_source?: string;
  attribution_gmv_cents?: number;
  order_ledger_gmv_cents?: number;
}

export function getShopifyGmv(token: string): Promise<ShopifyGmvSummary> {
  return apiFetch<ShopifyGmvSummary>("/api/admin/vkpi/shopify/gmv", { timeoutMs: 15000 }, token);
}

/** 已是美元的数值直读(GOAFFPRO gmv_usd/commission_usd 等 *_usd 字段专用)。
 *  非有限值 → null 诚实缺席;绝不做 ÷100 —— 那是 *_cents 字段(centsToUsd)的事。 */
export function asUsd(v: unknown): number | null {
  const n = Number(v);
  if (v === null || v === undefined || v === "" || !Number.isFinite(n)) return null;
  return n;
}

/** GOAFFPRO 连接状态门面(真状态词 → 人话;未知状态原样透出不装)。 */
export const GOAFF_STATUS_META: Record<string, { label: string; cls: string }> = {
  connected: { label: "已连接", cls: "border-good bg-good-soft text-good" },
  pending: { label: "待验证", cls: "border-warn bg-warn-soft text-warn" },
  not_configured: { label: "未配置", cls: "border-warn bg-warn-soft text-warn" },
  error: { label: "连接异常", cls: "border-crit bg-crit-soft text-crit" },
  revoked: { label: "已吊销", cls: "border-crit bg-crit-soft text-crit" },
};

/** 凭据来源门面(db=加密落库 / env=环境变量 / none=未配置)。 */
export const GOAFF_SOURCE_LABEL: Record<string, string> = {
  db: "加密落库",
  env: "环境变量",
  none: "未配置",
};
