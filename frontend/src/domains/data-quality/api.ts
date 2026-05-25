import { apiFetch, jsonBody } from '../../services/http';
import type { DataQualityAction, DataQualityResponse } from './types';

export async function getDataQualitySummary(token: string, limit = 100) {
  return apiFetch<DataQualityResponse>(
    `/api/marketing/data-quality?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function actOnDataQualityIssue(
  token: string,
  issueId: string,
  action: DataQualityAction,
  reason?: string,
  metadata?: Record<string, unknown>,
) {
  return apiFetch<Record<string, unknown>>(
    `/api/marketing/data-quality/${encodeURIComponent(issueId)}/${action}`,
    { method: 'POST', body: jsonBody({ reason, metadata }) },
    token,
  );
}
