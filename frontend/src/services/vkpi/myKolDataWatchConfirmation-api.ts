import { apiFetch, jsonBody } from "../http";
import type { VkpiDataWatchResponse } from "./myKolBoard-api";

/**
 * Confirm one SKU that the server already persisted as a system detection.
 * This is deliberately separate from the manual product_skus write contract:
 * the backend re-checks row ownership, the exact detected SKU and uniqueness
 * before it may append a confirmed provenance row.
 */
export async function confirmDetectedMyKolVideoSku(
  token: string,
  kolPoolId: number | string,
  evidenceId: number | string,
  productSkus: string[],
) {
  return apiFetch<VkpiDataWatchResponse>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/videos/${encodeURIComponent(String(evidenceId))}/data-watch`,
    { method: "POST", body: jsonBody({ confirm_detected_skus: productSkus }) },
    token,
  );
}
