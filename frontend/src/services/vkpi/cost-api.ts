import { apiFetch, jsonBody } from "../http";
import type { VkpiCostDetail } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

export interface VkpiCostPayload {
  projectId: string;
  costType: string;
  amountUsd: number;
  note?: string;
  sourceRef?: string;
  metadata?: Record<string, unknown>;
}

export interface VkpiManualAuthorizationEvidence {
  authorizationRef: string;
  reason: string;
  confirmedByHuman: boolean;
}

export type VkpiCostUpdatePayload = Partial<VkpiCostPayload> & {
  authorizationEvidence: VkpiManualAuthorizationEvidence;
};

export interface VkpiProductCostPayload {
  productSku: string;
  productName?: string;
  unitCostUsd: number;
  currency?: string;
  active?: boolean;
  note?: string;
}

export interface VkpiProductCostRow {
  id?: number;
  product_sku?: string;
  product_name?: string;
  unit_cost_cents?: number;
  currency?: string;
  active?: boolean | number;
  row_version?: number;
  verification_status?: "reference_unverified" | "verified" | string;
  source_type?: string | null;
  source_ref?: string | null;
  source_observed_at?: string | null;
  verified_by_staff_id?: number | null;
  verified_at?: string | null;
  updated_at?: string | null;
}

export interface VkpiProductCostVerificationPayload {
  sourceType: string;
  sourceRef: string;
  sourceObservedAt: string;
  authorizationRef: string;
  reason: string;
  confirmedByHuman: boolean;
  expectedId: number;
  expectedUnitCostCents: number;
  expectedCurrency: string;
  expectedRowVersion: number;
  expectedUpdatedAt: string;
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
        metadata: payload.metadata,
      }),
    },
    token,
  );
}

function authorizationEvidenceBody(evidence: VkpiManualAuthorizationEvidence) {
  return {
    authorization_ref: evidence.authorizationRef,
    reason: evidence.reason,
    confirmed_by_human: evidence.confirmedByHuman,
  };
}

export async function updateMarketingCost(token: string, costId: string, payload: VkpiCostUpdatePayload) {
  return apiFetch<Row>(
    `/api/marketing/costs/${encodeURIComponent(costId)}`,
    {
      method: "PATCH",
      body: jsonBody({
        cost_type: payload.costType,
        amount_usd: payload.amountUsd,
        note: payload.note,
        source_ref: payload.sourceRef,
        metadata: payload.metadata,
        authorization_evidence: authorizationEvidenceBody(payload.authorizationEvidence),
      }),
    },
    token,
  );
}

export async function approveMarketingCost(
  token: string,
  costId: string,
  note: string,
  authorizationEvidence: VkpiManualAuthorizationEvidence,
) {
  return apiFetch<Row>(
    `/api/marketing/costs/${encodeURIComponent(costId)}/approve`,
    {
      method: "POST",
      body: jsonBody({ note, authorization_evidence: authorizationEvidenceBody(authorizationEvidence) }),
    },
    token,
  );
}

export async function voidMarketingCost(
  token: string,
  costId: string,
  reason: string,
  authorizationEvidence: VkpiManualAuthorizationEvidence,
) {
  return apiFetch<Row>(
    `/api/marketing/costs/${encodeURIComponent(costId)}/void`,
    {
      method: "POST",
      body: jsonBody({ reason, authorization_evidence: authorizationEvidenceBody(authorizationEvidence) }),
    },
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

export async function listProductCosts(
  token: string,
  options: { limit?: number; includeInactive?: boolean; signal?: AbortSignal } = {},
) {
  const params = new URLSearchParams({
    limit: String(options.limit || 200),
    include_inactive: String(Boolean(options.includeInactive)),
  });
  return apiFetch<{ product_costs?: VkpiProductCostRow[] }>(
    `/api/admin/vkpi/product-costs?${params.toString()}`,
    { signal: options.signal },
    token,
  );
}

export async function verifyProductCost(
  token: string,
  productSku: string,
  payload: VkpiProductCostVerificationPayload,
) {
  return apiFetch<{ product_cost?: VkpiProductCostRow; verified?: boolean }>(
    `/api/admin/vkpi/product-costs/${encodeURIComponent(productSku)}/verify`,
    {
      method: "POST",
      body: jsonBody({
        source_type: payload.sourceType,
        source_ref: payload.sourceRef,
        source_observed_at: payload.sourceObservedAt,
        authorization_evidence: {
          authorization_ref: payload.authorizationRef,
          reason: payload.reason,
          confirmed_by_human: payload.confirmedByHuman,
        },
        expected_id: payload.expectedId,
        expected_unit_cost_cents: payload.expectedUnitCostCents,
        expected_currency: payload.expectedCurrency,
        expected_row_version: payload.expectedRowVersion,
        expected_updated_at: payload.expectedUpdatedAt,
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
