import { apiFetch } from "../http";

// SKU 360° 板块页服务(件③ 改版)—— 四个只读端点,零写零采集:
//   GET /api/admin/vkpi/sku/list?query=&limit=                      SKU 选择器(与 GTM/发射台同源)
//   GET /api/admin/vkpi/sku/{sku}/profile                           360° 档案(纯聚合已有数据)
//   GET /api/admin/vkpi/sku/{sku}/persona                           产品知识库画像(vkpi_product_persona,
//                                                                   LLM 离线批产,本端点只读透传)
//   GET /api/admin/vkpi/industry-data/product-campaign-card?sku=    推广候选(启发式圈选,只读,人工复核)
// 红线:纯读;绝不触 viltrox_fit_score 写点 / rule_v0;响应契约宽进严出(缺字段防御)。

export type Row = Record<string, any>;

export interface Sku360ListItem {
  sku: string;
  model_name: string;
  marketing_name: string;
  category_main: string;
  category_detail: string;
  series: string;
  mount: string;
  price_usd: number | null;
  status: string;
}

function asStr(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return "";
}

function asNum(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

export async function listSku360Options(token: string, query: string, limit = 30): Promise<Sku360ListItem[]> {
  const res = await apiFetch<{ items?: Row[] }>(
    `/api/admin/vkpi/sku/list?query=${encodeURIComponent(query.trim())}&limit=${encodeURIComponent(String(limit))}`,
    { timeoutMs: 6000 },
    token,
  );
  const items = Array.isArray(res?.items) ? res.items : [];
  return items.map((r) => ({
    sku: asStr(r.sku),
    model_name: asStr(r.model_name),
    marketing_name: asStr(r.marketing_name),
    category_main: asStr(r.category_main),
    category_detail: asStr(r.category_detail),
    series: asStr(r.series),
    mount: asStr(r.mount),
    price_usd: asNum(r.price_usd),
    status: asStr(r.status),
  }));
}

/** 360° 档案:product 基础 + content(items/aggregate/content_fit_matches)+ comments + bh_reviews。 */
export async function getSku360Profile(token: string, skuOrCode: string): Promise<Row> {
  return apiFetch<Row>(`/api/admin/vkpi/sku/${encodeURIComponent(skuOrCode)}/profile`, { timeoutMs: 30000 }, token);
}

/** 产品知识库画像:persona=null 表示该 SKU 未生成画像(诚实空态,前端不得编造)。 */
export async function getSku360Persona(token: string, skuOrCode: string): Promise<Row> {
  return apiFetch<Row>(`/api/admin/vkpi/sku/${encodeURIComponent(skuOrCode)}/persona`, { timeoutMs: 10000 }, token);
}

/** 推广候选卡:启发式圈选(读 vkpi_product_spec_facts / vkpi_kol_pool / vkpi_competitor_signals),
 *  自带 policy.human_approval_required —— 前端必须如实标注「仅供人工参考,不自动推荐」。 */
export async function getSku360CampaignCard(token: string, sku: string): Promise<Row> {
  return apiFetch<Row>(
    `/api/admin/vkpi/industry-data/product-campaign-card?sku=${encodeURIComponent(sku)}`,
    { timeoutMs: 20000 },
    token,
  );
}
