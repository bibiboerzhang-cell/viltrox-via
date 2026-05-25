import { apiFetch, jsonBody } from "../http";
import type { VkpiCostDetail } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

export interface VkpiCostPayload {
  projectId: string;
  costType: string;
  amountUsd: number;
  note?: string;
  sourceRef?: string;
}

export interface VkpiProductCostPayload {
  productSku: string;
  productName?: string;
  unitCostUsd: number;
  currency?: string;
  active?: boolean;
  note?: string;
}

export async function getMarketingCostDetail(token: string, costId: string) {
  return apiFetch<VkpiCostDetail>(`/api/marketing/costs/${encodeURIComponent(costId)}`, {}, token);
}

export async function addProjectCost(token: string, payload: VkpiCostPayload) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(payload.projectId)}/costs`,
    {
      method: "POST",
      body: jsonBody({
        cost_type: payload.costType,
        amount_usd: payload.amountUsd,
        note: payload.note,
        source_ref: payload.sourceRef,
      }),
    },
    token,
  );
}

export async function updateMarketingCost(token: string, costId: string, payload: Partial<VkpiCostPayload>) {
  return apiFetch<Row>(
    `/api/marketing/costs/${encodeURIComponent(costId)}`,
    {
      method: "PATCH",
      body: jsonBody({
        cost_type: payload.costType,
        amount_usd: payload.amountUsd,
        note: payload.note,
        source_ref: payload.sourceRef,
      }),
    },
    token,
  );
}

export async function approveMarketingCost(token: string, costId: string, note?: string) {
  return apiFetch<Row>(
    `/api/marketing/costs/${encodeURIComponent(costId)}/approve`,
    { method: "POST", body: jsonBody({ note }) },
    token,
  );
}

export async function voidMarketingCost(token: string, costId: string, reason?: string) {
  return apiFetch<Row>(
    `/api/marketing/costs/${encodeURIComponent(costId)}/void`,
    { method: "POST", body: jsonBody({ reason }) },
    token,
  );
}

export async function upsertProductCost(token: string, payload: VkpiProductCostPayload) {
  return apiFetch<Row>(
    "/api/marketing/product-costs",
    {
      method: "POST",
      body: jsonBody({
        product_sku: payload.productSku,
        product_name: payload.productName,
        unit_cost_usd: payload.unitCostUsd,
        currency: payload.currency || "USD",
        active: payload.active ?? true,
        note: payload.note,
      }),
    },
    token,
  );
}

export async function getAiBudgetStatus(token: string) {
  return apiFetch<{ budgets?: Row[]; summary?: Row }>("/api/admin/vkpi/budgets", {}, token);
}

export async function updateAiBudgetScope(token: string, scope: string, payload: Row) {
  return apiFetch<Row>(
    `/api/admin/vkpi/budgets/${encodeURIComponent(scope)}/update`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function getAiBudgetUsageByProvider(token: string, options: { limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 50) });
  return apiFetch<{ rows?: Row[] }>(
    `/api/admin/vkpi/budgets/usage-by-provider?${params.toString()}`,
    {},
    token,
  );
}

export async function getAiBudgetUsageByCron(token: string, options: { limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 50) });
  return apiFetch<{ rows?: Row[] }>(
    `/api/admin/vkpi/budgets/usage-by-cron?${params.toString()}`,
    {},
    token,
  );
}
