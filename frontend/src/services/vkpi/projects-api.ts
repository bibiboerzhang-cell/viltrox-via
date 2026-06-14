import { apiFetch, jsonBody } from "../http";
import type { VkpiProjectDetail } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

export interface VkpiAnalysisCacheEntry {
  target_type: string;
  target_id: string;
  derive_method: string;
  model?: string | null;
  cost?: number | null;
  status: string;
  triggered_by_user_id?: number | null;
  result?: unknown;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface VkpiAnalysisCacheResponse {
  target_type: string;
  target_id: string;
  derive_method?: string | null;
  state: "ready" | "pending";
  entry?: VkpiAnalysisCacheEntry | null;
}

export interface VkpiProjectVideoAnalysisCacheItem {
  assignment_id?: number | null;
  kol_pool_id?: number | null;
  kol_name?: string | null;
  handle?: string | null;
  platform?: string | null;
  evidence_id?: number | null;
  content_url?: string | null;
  title?: string | null;
  thumbnail_url?: string | null;
  view_count?: number | null;
  like_count?: number | null;
  comment_count?: number | null;
  publish_date?: string | null;
  state: "ready" | "pending";
  entry?: VkpiAnalysisCacheEntry | null;
}

export interface VkpiProjectVideoAnalysisCacheResponse {
  project_id: number;
  derive_method: string;
  items: VkpiProjectVideoAnalysisCacheItem[];
  summary: {
    evidence_count: number;
    ready_count: number;
    pending_count: number;
  };
}

export interface VkpiProjectContract {
  id: number;
  project_id: number;
  assignment_id?: number | null;
  kol_pool_id?: number | null;
  file_name?: string | null;
  r2_key?: string | null;
  file_size_bytes?: number | null;
  mime_type?: string | null;
  status?: "draft" | "needs_review" | "extracted" | "confirmed" | "failed" | string;
  extraction_status?: "processing" | "ready" | "failed" | "skipped" | string;
  fee_amount?: number | string | null;
  fee_currency?: string | null;
  contract_duration?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  platforms_json?: unknown[] | null;
  deliverable_count?: number | string | null;
  deliverables_json?: unknown[] | null;
  must_include_json?: unknown[] | null;
  usage_rights?: string | null;
  exclusivity?: string | null;
  buyout_rights?: string | null;
  breach_terms?: string | null;
  payment_terms?: string | null;
  cancellation_terms?: string | null;
  revision_terms?: string | null;
  raw_extracted_json?: Record<string, unknown> | null;
  field_confidence_json?: Record<string, number> | null;
  field_confirmed_json?: Record<string, unknown> | null;
  manual_overrides_json?: Record<string, unknown> | null;
  /** 签署版关联:本合同是 #N(GEN 草稿)的签署版 */
  signed_version_of?: number | null;
  /** 本草稿已有签署版 #M(被取代) */
  superseded_by?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface VkpiProjectContractsResponse {
  project_id: number;
  count: number;
  items: VkpiProjectContract[];
}

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
  assignedStaffId?: string;
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

export async function getAnalysisCache(token: string, targetType: string, targetId: string, deriveMethod?: string) {
  const params = new URLSearchParams({
    target_type: targetType,
    target_id: targetId,
    _ts: String(Date.now()),
  });
  if (deriveMethod) params.set("derive_method", deriveMethod);
  return apiFetch<VkpiAnalysisCacheResponse>(
    `/api/admin/vkpi/analysis-cache?${params.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export async function getProjectVideoAnalysisCache(token: string, projectId: string, deriveMethod = "video_analysis_final_v1") {
  const params = new URLSearchParams({
    derive_method: deriveMethod,
    _ts: String(Date.now()),
  });
  return apiFetch<VkpiProjectVideoAnalysisCacheResponse>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/video-analysis-cache?${params.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export interface VkpiProjectRetrospectiveResult {
  insight_text?: string;
  highlights?: string[];
  risks?: string[];
  next_steps?: string[];
  provenance?: { video_count?: number; model?: string; provider?: string; generated_at?: string; selection?: string; top_n?: number };
}

export interface VkpiProjectRetrospectiveResponse {
  project_id: number;
  retrospective: { result?: VkpiProjectRetrospectiveResult; model?: string; status?: string; updated_at?: string } | null;
  active_job: Record<string, unknown> | null;
  last_job: { id?: number; status?: string; last_error?: string | null } | null;
}

export async function generateProjectRetrospective(token: string, projectId: string) {
  return apiFetch<{ status?: string; job?: Record<string, unknown> | null }>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/retrospective/generate`,
    { method: "POST" },
    token,
  );
}

export async function getProjectRetrospective(token: string, projectId: string) {
  return apiFetch<VkpiProjectRetrospectiveResponse>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/retrospective?_ts=${Date.now()}`,
    { cache: "no-store" },
    token,
  );
}

export async function getProjectContracts(token: string, projectId: string) {
  return apiFetch<VkpiProjectContractsResponse>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/contracts?_ts=${Date.now()}`,
    { cache: "no-store" },
    token,
  );
}

export async function uploadProjectContract(
  token: string,
  projectId: string,
  file: File,
  payload: { assignmentId?: string | number | null; kolPoolId?: string | number | null; relatedContractId?: string | number | null } = {},
) {
  const body = new FormData();
  body.append("file", file);
  if (payload.assignmentId) body.append("assignment_id", String(payload.assignmentId));
  if (payload.kolPoolId) body.append("kol_pool_id", String(payload.kolPoolId));
  // 签署版上传:携带被签署的 GEN 草稿 id,后端把本次上传标记为其签署版(双记录留痕)。
  if (payload.relatedContractId) body.append("related_contract_id", String(payload.relatedContractId));
  return apiFetch<{ status?: string; contract?: VkpiProjectContract; job?: Record<string, unknown> | null; extraction?: Record<string, unknown>; budget?: Record<string, unknown> }>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/contracts/upload`,
    { method: "POST", body, timeoutMs: 150000 },
    token,
  );
}

export async function downloadProjectContract(token: string, projectId: string, contractId: number | string) {
  return apiFetch<{ contract_id: number; r2_key?: string | null; url: string }>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/contracts/${encodeURIComponent(String(contractId))}/download`,
    { cache: "no-store" },
    token,
  );
}

export async function patchProjectContract(token: string, projectId: string, contractId: number | string, payload: Row) {
  return apiFetch<VkpiProjectContract>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/contracts/${encodeURIComponent(String(contractId))}`,
    { method: "PATCH", body: jsonBody(payload) },
    token,
  );
}

export async function confirmProjectContract(token: string, projectId: string, contractId: number | string, payload: Row) {
  return apiFetch<VkpiProjectContract>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/contracts/${encodeURIComponent(String(contractId))}/confirm`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function deleteProjectContract(token: string, projectId: string, contractId: number | string) {
  return apiFetch<{ deleted: boolean; contract_id: number; r2_deleted?: boolean }>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/contracts/${encodeURIComponent(String(contractId))}`,
    { method: "DELETE" },
    token,
  );
}

export async function extractProjectContract(token: string, projectId: string, contractId: number | string) {
  // 异步入队:立即返回 {status:'queued'|'already_*'|'skipped', job, contract},不再阻塞等待提取
  return apiFetch<{ status?: string; contract?: VkpiProjectContract; job?: Record<string, unknown> | null; extraction?: Record<string, unknown>; budget?: Record<string, unknown> }>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/contracts/${encodeURIComponent(String(contractId))}/extract`,
    { method: "POST" },
    token,
  );
}

export interface VkpiProjectVideoAnalysisCacheMulti {
  project_id: number;
  by_method: Record<string, VkpiProjectVideoAnalysisCacheResponse>;
}

export async function getProjectVideoAnalysisCacheMulti(token: string, projectId: string, deriveMethods: string[]) {
  const params = new URLSearchParams({ derive_method: deriveMethods.join(","), _ts: String(Date.now()) });
  return apiFetch<VkpiProjectVideoAnalysisCacheMulti>(
    `/api/admin/vkpi/projects/${encodeURIComponent(projectId)}/video-analysis-cache?${params.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export async function getAvailableProjectKols(token: string, projectId: string, query = "", scope = "favorites") {
  const params = new URLSearchParams({
    project_id: projectId,
    limit: "500",
  });
  if (query.trim()) params.set("query", query.trim());
  if (scope) params.set("scope", scope);
  return apiFetch<{ kols?: Row[]; project_id?: number; scope?: string; total_available?: number; returned?: number; has_more?: boolean }>(`/api/marketing/kol-pool/available?${params.toString()}`, {}, token);
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
        assigned_staff_id: payload.assignedStaffId ? Number(payload.assignedStaffId) : undefined,
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

export async function enqueueKolOutreachDraft(token: string, kolPoolId: number, projectId?: number) {
  return apiFetch<Row>(
    "/api/marketing/kol-pool/outreach-draft/enqueue",
    { method: "POST", body: jsonBody({ kol_pool_id: kolPoolId, project_id: projectId }) },
    token,
  );
}

export async function getKolOutreachDraft(token: string, kolPoolId: number) {
  return apiFetch<{ state?: string; draft?: Row }>(
    `/api/marketing/kol-pool/${encodeURIComponent(String(kolPoolId))}/outreach-draft`,
    {},
    token,
  );
}

export async function createMarketingMessage(token: string, payload: Row) {
  return apiFetch<Row>("/api/marketing/messages", { method: "POST", body: jsonBody(payload) }, token);
}

export interface VkpiContractTemplateSlot {
  key: string;
  label: string;
  group?: string;
  type?: string;
  options?: string[];
  required?: boolean;
}

export interface VkpiContractTemplate {
  key: string;
  label: string;
  title: string;
  slots: VkpiContractTemplateSlot[];
}

export async function getContractTemplates(token: string) {
  return apiFetch<{ party_a?: string; templates?: VkpiContractTemplate[] }>(
    "/api/marketing/projects/contract-templates",
    {},
    token,
  );
}

export async function generateProjectContract(
  token: string,
  projectId: string,
  body: { template_key: string; fields: Record<string, string>; assignment_id?: string | number | null; kol_pool_id?: string | number | null },
) {
  return apiFetch<Row>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/contracts/generate`,
    { method: "POST", body: jsonBody(body) },
    token,
  );
}

export async function enqueueLogisticsSync(token: string, projectId?: number) {
  return apiFetch<{ status?: string; job_id?: number; message?: string }>(
    "/api/marketing/projects/logistics-sync/enqueue",
    { method: "POST", body: jsonBody({ project_id: projectId }) },
    token,
  );
}

// ---- 履约待办 / Due List(后端已真:物流签收 N 天后未推进的项目;只读看板) ----

export interface VkpiProjectDueListItem {
  project_id: number;
  project_name?: string | null;
  product_name?: string | null;
  delivered_at?: string | null;
  days_since_delivered?: number | null;
  assignment_count?: number | null;
}

export interface VkpiProjectDueListResponse {
  status?: string;
  days_overdue?: number;
  count?: number;
  items?: VkpiProjectDueListItem[];
  note?: string | null;
}

export interface VkpiDeliverableStage {
  stage?: string | null;
  stage_status?: string | null;
  n?: number | null;
}

export interface VkpiDeliverableStagesSummaryResponse {
  total?: number;
  stages?: VkpiDeliverableStage[];
  raw_stages?: unknown;
}

export async function getProjectsDueList(token: string, daysOverdue = 7) {
  const params = new URLSearchParams({
    days_overdue: String(daysOverdue),
    _ts: String(Date.now()),
  });
  return apiFetch<VkpiProjectDueListResponse>(
    `/api/admin/vkpi/projects/due-list?${params.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export async function getDeliverableStagesSummary(token: string) {
  return apiFetch<VkpiDeliverableStagesSummaryResponse>(
    `/api/admin/vkpi/projects/deliverable-stages-summary?_ts=${Date.now()}`,
    { cache: "no-store" },
    token,
  );
}

// ---- 发票提取(LLM 经 apify_jobs 队列;产物 llm_ 标注,人工核对后才算真值) ----

export interface VkpiInvoiceExtractResult {
  party_name?: string | null;
  address?: string | null;
  account_name?: string | null;
  account_number_or_iban?: string | null;
  swift?: string | null;
  bank_name?: string | null;
  bank_address?: string | null;
  amount?: string | number | null;
  [key: string]: unknown;
}

export async function enqueueInvoiceExtract(token: string, projectId: string, fileUrl: string) {
  return apiFetch<{ status?: string; extract_key?: string; key?: string; job?: Row | null; message?: string }>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/invoice-extract/enqueue`,
    { method: "POST", body: jsonBody({ file_url: fileUrl }) },
    token,
  );
}

export async function getInvoiceExtract(token: string, extractKey: string) {
  return apiFetch<{ state?: string; status?: string; result?: VkpiInvoiceExtractResult | null; error?: string | null }>(
    `/api/marketing/projects/invoice-extract/${encodeURIComponent(extractKey)}?_ts=${Date.now()}`,
    { cache: "no-store" },
    token,
  );
}

// ---- 合同槽位 AI 润色(LLM 经队列;差异预览人工「应用」后才写回表单) ----

export async function enqueueContractPolish(token: string, projectId: string, templateKey: string, textFields: Record<string, string>) {
  return apiFetch<{ status?: string; polish_key?: string; key?: string; job?: Row | null; message?: string }>(
    `/api/marketing/projects/${encodeURIComponent(projectId)}/contract-polish/enqueue`,
    { method: "POST", body: jsonBody({ template_key: templateKey, fields: textFields }) },
    token,
  );
}

export async function getContractPolish(token: string, polishKey: string) {
  return apiFetch<{ state?: string; status?: string; result?: { fields?: Record<string, string> } & Record<string, unknown> | null; error?: string | null }>(
    `/api/marketing/projects/contract-polish/${encodeURIComponent(polishKey)}?_ts=${Date.now()}`,
    { cache: "no-store" },
    token,
  );
}
