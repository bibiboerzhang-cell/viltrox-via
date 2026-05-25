import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;
type LegacyRepairItem = {
  [key: string]: any;
  acceptance?: string[];
  evidence?: string[];
  fields?: LegacyRepairItem[];
  requiredBeforeWrite?: string[];
  required_before_write?: string[];
  validationCommands?: string[];
  validation_commands?: string[];
};
type LegacyRepairPayload = {
  [key: string]: any;
  proposals?: LegacyRepairItem[];
  rows?: LegacyRepairItem[];
  gates?: LegacyRepairItem[];
  tables?: LegacyRepairItem[];
  endpoints?: LegacyRepairItem[];
  blockedBy?: string[];
  blocked_by?: string[];
  artifacts?: LegacyRepairItem[];
  steps?: LegacyRepairItem[];
  upSql?: LegacyRepairItem[];
  up_sql?: LegacyRepairItem[];
  downSql?: LegacyRepairItem[];
  down_sql?: LegacyRepairItem[];
  reviewCommands?: string[];
  review_commands?: string[];
  events?: LegacyRepairItem[];
  drafts?: LegacyRepairItem[];
  refs?: LegacyRepairItem[];
  fields?: LegacyRepairItem[];
  requiredBeforePersist?: string[];
  required_before_persist?: string[];
  persistencePreview?: LegacyRepairPayload;
  persistence_preview?: LegacyRepairPayload;
};

export type VkpiRepairProposalsResponse = LegacyRepairPayload;
export type VkpiRepairPersistencePreview = LegacyRepairPayload;
export type VkpiRepairProposalRecord = LegacyRepairPayload;
export type VkpiRepairPersistenceReadinessResponse = LegacyRepairPayload;
export type VkpiRepairPersistenceBoundaryResponse = LegacyRepairPayload;
export type VkpiRepairPersistenceApprovalEnvelopePayload = LegacyRepairPayload;
export type VkpiRepairPersistenceApprovalEnvelopeResponse = LegacyRepairPayload;
export type VkpiRepairMigrationPreflightManifestResponse = LegacyRepairPayload;
export type VkpiRepairMigrationDraftPackageResponse = LegacyRepairPayload;
export type VkpiRepairClosureStatusResponse = LegacyRepairPayload;
export type VkpiRepairAuditWritePreviewResponse = LegacyRepairPayload;
export type VkpiRepairTaskDraftPreviewResponse = LegacyRepairPayload;
export type VkpiRepairEvidenceRefPreviewResponse = LegacyRepairPayload;
export type VkpiRepairProposalCreatePayload = LegacyRepairPayload;
export type VkpiRepairProposalDryRunResponse = LegacyRepairPayload;
export type VkpiRepairProposalCancelPayload = LegacyRepairPayload;
export type VkpiRepairProposalCancelResponse = LegacyRepairPayload;
export type VkpiRepairPreflightStatePayload = LegacyRepairPayload;
export type VkpiRepairPreflightResponse = LegacyRepairPayload;
export type VkpiRepairImplementationPackagePayload = LegacyRepairPayload;
export type VkpiRepairImplementationPackageResponse = LegacyRepairPayload;
export type VkpiRepairHandoffDryRunPayload = LegacyRepairPayload;
export type VkpiRepairHandoffDryRunResponse = LegacyRepairPayload;
export type VkpiRepairFutureWritePreviewPayload = LegacyRepairPayload;
export type VkpiRepairFutureWritePreviewResponse = LegacyRepairPayload;

export async function getRepairProposals(token: string, opts: { category?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ category: opts.category || "all", limit: String(opts.limit || 20) });
  return apiFetch<VkpiRepairProposalsResponse>(`/api/admin/vkpi/repair/proposals?${params.toString()}`, {}, token);
}

export async function getRepairPersistenceReadiness(token: string) {
  return apiFetch<VkpiRepairPersistenceReadinessResponse>("/api/admin/vkpi/repair/persistence-readiness", {}, token);
}

export async function getRepairPersistenceBoundary(token: string) {
  return apiFetch<VkpiRepairPersistenceBoundaryResponse>("/api/admin/vkpi/repair/persistence-boundary", {}, token);
}

export async function previewRepairPersistenceApprovalEnvelope(
  token: string,
  payload: VkpiRepairPersistenceApprovalEnvelopePayload = {},
) {
  return apiFetch<VkpiRepairPersistenceApprovalEnvelopeResponse>(
    "/api/admin/vkpi/repair/persistence-approval-envelope",
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function getRepairMigrationPreflightManifest(token: string) {
  return apiFetch<VkpiRepairMigrationPreflightManifestResponse>(
    "/api/admin/vkpi/repair/migration-preflight-manifest",
    {},
    token,
  );
}

export async function getRepairMigrationDraftPackage(token: string) {
  return apiFetch<VkpiRepairMigrationDraftPackageResponse>(
    "/api/admin/vkpi/repair/migration-draft-package",
    {},
    token,
  );
}

export async function getRepairClosureStatus(token: string) {
  return apiFetch<VkpiRepairClosureStatusResponse>("/api/admin/vkpi/repair/closure-status", {}, token);
}

export async function getRepairAuditWritePreview(token: string) {
  return apiFetch<VkpiRepairAuditWritePreviewResponse>("/api/admin/vkpi/repair/audit-write-preview", {}, token);
}

export async function getRepairTaskDraftPreview(token: string) {
  return apiFetch<VkpiRepairTaskDraftPreviewResponse>("/api/admin/vkpi/repair/task-draft-preview", {}, token);
}

export async function getRepairEvidenceRefPreview(token: string) {
  return apiFetch<VkpiRepairEvidenceRefPreviewResponse>("/api/admin/vkpi/repair/evidence-ref-preview", {}, token);
}

export async function createRepairProposalDryRun(token: string, payload: VkpiRepairProposalCreatePayload) {
  return apiFetch<VkpiRepairProposalDryRunResponse>(
    "/api/admin/vkpi/repair/proposals?persist=false",
    { method: "POST", body: jsonBody({ version: "repair_proposal.local.v0", ...payload }) },
    token,
  );
}

export async function cancelRepairProposalDryRun(
  token: string,
  proposalId: string,
  payload: VkpiRepairProposalCancelPayload,
) {
  return apiFetch<VkpiRepairProposalCancelResponse>(
    `/api/admin/vkpi/repair/proposals/${encodeURIComponent(proposalId)}/cancel?persist=false`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function updateRepairPreflightDryRun(
  token: string,
  proposalId: string,
  payload: VkpiRepairPreflightStatePayload,
) {
  return apiFetch<VkpiRepairPreflightResponse>(
    `/api/admin/vkpi/repair/proposals/${encodeURIComponent(proposalId)}/preflight?persist=false`,
    { method: "PATCH", body: jsonBody(payload) },
    token,
  );
}

export async function createRepairImplementationPackageDryRun(
  token: string,
  proposalId: string,
  payload: VkpiRepairImplementationPackagePayload,
) {
  return apiFetch<VkpiRepairImplementationPackageResponse>(
    `/api/admin/vkpi/repair/proposals/${encodeURIComponent(proposalId)}/implementation-package?persist=false`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function createRepairHandoffDryRun(
  token: string,
  packageId: string,
  payload: VkpiRepairHandoffDryRunPayload,
) {
  return apiFetch<VkpiRepairHandoffDryRunResponse>(
    `/api/admin/vkpi/repair/implementation-packages/${encodeURIComponent(packageId)}/handoff?persist=false`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function createRepairFutureWritePreviewDryRun(
  token: string,
  logId: string,
  payload: VkpiRepairFutureWritePreviewPayload,
) {
  return apiFetch<VkpiRepairFutureWritePreviewResponse>(
    `/api/admin/vkpi/repair/handoff-log/${encodeURIComponent(logId)}/future-write-preview?persist=false`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}
