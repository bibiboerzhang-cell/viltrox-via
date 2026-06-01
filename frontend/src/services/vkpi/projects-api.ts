import { apiFetch, jsonBody } from "../http";
import type { VkpiProjectDetail } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

export interface VkpiCreateProjectPayload {
  projectName: string;
  kolId?: string;
  productSku?: string;
  productName?: string;
  productSkus?: string[];
  products?: Array<{ productSku: string; productName?: string }>;
  platform?: string;
  marketplace?: string;
  sourceType?: string;
  note?: string;
}

export interface VkpiUpdateProjectPayload {
  projectName?: string;
  productSku?: string;
  productName?: string;
  products?: Array<{ productSku: string; productName?: string }>;
  platform?: string;
  marketplace?: string;
  priority?: string;
  shopifyLink?: string;
  targetPostDate?: string;
  dueAt?: string;
  note?: string;
}

export interface VkpiStagePayload {
  toStage: string;
  note?: string;
  trackingNumber?: string;
  sampleStatus?: string;
  sourceRefType?: string;
  sourceRefId?: string;
}

export async function getProjectDetail(token: string, projectId: string) {
  return apiFetch<VkpiProjectDetail>(`/api/marketing/projects/${encodeURIComponent(projectId)}`, {}, token);
}

export async function getAvailableProjectKols(token: string, projectId: string, query = "") {
  const params = new URLSearchParams({
    project_id: projectId,
    limit: "500",
  });
  if (query.trim()) params.set("query", query.trim());
  return apiFetch<{ kols?: Row[]; project_id?: number }>(`/api/marketing/kol-pool/available?${params.toString()}`, {}, token);
}

export async function addKolsToProject(token: string, projectId: string, kolPoolIds: string[], assignedStaffId?: string) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/kols`,
    {
      method: "POST",
      body: jsonBody({
        kol_pool_ids: kolPoolIds.map((id) => Number(id)).filter((id) => Number.isFinite(id) && id > 0),
        assigned_staff_id: assignedStaffId ? Number(assignedStaffId) : undefined,
      }),
    },
    token,
  );
}

export async function createProject(token: string, payload: VkpiCreateProjectPayload) {
  return apiFetch<Row>(
    "/api/marketing/projects",
    {
      method: "POST",
      body: jsonBody({
        project_name: payload.projectName,
        kol_id: payload.kolId ? Number(payload.kolId) : undefined,
        product_sku: payload.productSku,
        product_name: payload.productName,
        product_skus: payload.productSkus,
        products: payload.products,
        platform: payload.platform,
        marketplace: payload.marketplace,
        source_type: payload.sourceType || "v615_projects_ui",
        note: payload.note,
      }),
    },
    token,
  );
}

export async function updateProject(token: string, projectId: string, payload: VkpiUpdateProjectPayload) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}`,
    {
      method: "PATCH",
      body: jsonBody({
        project_name: payload.projectName,
        product_sku: payload.productSku,
        product_name: payload.productName,
        products: payload.products,
        platform: payload.platform,
        marketplace: payload.marketplace,
        priority: payload.priority,
        shopify_link: payload.shopifyLink,
        target_post_date: payload.targetPostDate,
        due_at: payload.dueAt,
        note: payload.note,
      }),
    },
    token,
  );
}

export async function transitionProjectStage(token: string, projectId: string, payload: VkpiStagePayload) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/stage`,
    {
      method: "POST",
      body: jsonBody({
        to_stage: payload.toStage,
        note: payload.note,
        tracking_number: payload.trackingNumber,
        sample_status: payload.sampleStatus,
        source_ref_type: payload.sourceRefType,
        source_ref_id: payload.sourceRefId,
      }),
    },
    token,
  );
}

export async function updateProjectFollowStatus(token: string, projectId: string, followStatus: 'active' | 'paused') {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/follow-status`,
    { method: "PATCH", body: jsonBody({ follow_status: followStatus }) },
    token,
  );
}

export async function updateProjectStar(token: string, projectId: string, starred: boolean) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/star`,
    { method: "PATCH", body: jsonBody({ starred }) },
    token,
  );
}

export async function deleteProject(token: string, projectId: string, reason = "前端删除项目") {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}`,
    { method: "DELETE", body: jsonBody({ reason }) },
    token,
  );
}

export async function addProjectMessage(token: string, projectId: string, payload: Row) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/messages`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function addProjectContent(token: string, projectId: string, payload: Row) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/content`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function upsertProjectTerms(token: string, projectId: string, payload: Row) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/terms`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function addProjectShipment(token: string, projectId: string, payload: Row) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/shipments`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function advanceProjectKol(token: string, projectId: string, kolRef: string, payload: Row) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/kols/${encodeURIComponent(kolRef)}/advance`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function updateProjectKolShipping(token: string, projectId: string, kolRef: string, payload: Row) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/kols/${encodeURIComponent(kolRef)}/shipping`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function submitProjectKolActionStub(token: string, projectId: string, kolRef: string, actionKind: "screenshot" | "video" | "contract", payload: Row) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/kols/${encodeURIComponent(kolRef)}/${actionKind}`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function listCampaigns(token: string) {
  return apiFetch<{ campaigns?: Row[] }>("/api/marketing/campaigns?limit=100", {}, token);
}

export async function createCampaign(token: string, payload: Row) {
  return apiFetch<Row>("/api/marketing/campaigns", { method: "POST", body: jsonBody(payload) }, token);
}

export async function addCampaignProject(token: string, campaignId: string, projectId: string) {
  return apiFetch<Row>(
    `/api/marketing/campaigns/${encodeURIComponent(campaignId)}/projects`,
    { method: "POST", body: jsonBody({ project_id: Number(projectId) }) },
    token,
  );
}

export async function listBudgetPools(token: string) {
  return apiFetch<{ budget_pools?: Row[] }>("/api/marketing/budget-pools?limit=100", {}, token);
}

export async function createBudgetPool(token: string, payload: Row) {
  return apiFetch<Row>("/api/marketing/budget-pools", { method: "POST", body: jsonBody(payload) }, token);
}

export async function initiateOffboarding(token: string, staffId: string, newOwnerStaffId?: string) {
  return apiFetch<Row>(
    `/api/marketing/staff/${encodeURIComponent(staffId)}/offboard/initiate`,
    {
      method: "POST",
      body: jsonBody({ new_owner_staff_id: newOwnerStaffId ? Number(newOwnerStaffId) : undefined }),
    },
    token,
  );
}
