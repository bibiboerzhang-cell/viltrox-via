import { apiFetch, jsonBody } from "../http";
import type { VkpiAlertDetail } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

export async function getMarketingAlertDetail(token: string, alertId: string) {
  return apiFetch<VkpiAlertDetail>(`/api/marketing/alerts/${encodeURIComponent(alertId)}`, {}, token);
}

export async function resolveMarketingAlert(token: string, alertId: string) {
  return apiFetch<Row>(
    `/api/marketing/alerts/${encodeURIComponent(alertId)}/resolve`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}
