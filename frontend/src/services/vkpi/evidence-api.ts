import { apiFetch } from "../http";

type Row = Record<string, unknown>;

export async function uploadMarketingEvidenceFile(
  token: string,
  file: File,
  payload: { entityType?: string; entityId?: string; purpose?: string } = {},
) {
  const form = new FormData();
  form.append("file", file);
  form.append("entity_type", payload.entityType || "manual");
  form.append("entity_id", payload.entityId || "");
  form.append("purpose", payload.purpose || "note");
  return apiFetch<Row>("/api/marketing/evidence/uploads", { method: "POST", body: form }, token);
}
