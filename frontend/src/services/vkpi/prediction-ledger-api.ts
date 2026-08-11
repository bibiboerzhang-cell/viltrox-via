import { apiFetch, jsonBody } from "../http";

export type PredictionEvidenceField =
  | "actual_result"
  | "window_7d"
  | "window_14d"
  | "window_28d";

export interface PredictionActualRequest {
  outcome_id: number;
  evidence_field: PredictionEvidenceField;
  metric_path: string;
  correlation_id: string;
  notes?: string;
}

export interface PredictionActualReceipt {
  ok: boolean;
  id: number | null;
  deduped: boolean;
  error_abs?: number | null;
  error_pct?: number | null;
  interval_hit?: boolean | null;
  direction_hit?: boolean | null;
}

export async function recordPredictionActual(
  token: string,
  runId: string,
  payload: PredictionActualRequest,
): Promise<PredictionActualReceipt> {
  return apiFetch<PredictionActualReceipt>(
    `/api/admin/vkpi/prediction-ledger/runs/${encodeURIComponent(runId)}/actual-from-outcome`,
    { method: "POST", cache: "no-store", body: jsonBody(payload) },
    token,
  );
}
