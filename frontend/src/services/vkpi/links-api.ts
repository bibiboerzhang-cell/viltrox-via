import { apiFetch, jsonBody } from "../http";
import type { VkpiLinkDetail } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

export interface VkpiCreateLinkPayload {
  destinationUrl: string;
  slug?: string;
  projectId?: string;
  kolId?: string;
  platform?: string;
  productSku?: string;
  campaignName?: string;
  utmSource?: string;
  utmMedium?: string;
  utmCampaign?: string;
  utmContent?: string;
}

export async function createMarketingLink(token: string, payload: VkpiCreateLinkPayload) {
  return apiFetch<Row>(
    "/api/marketing/links",
    {
      method: "POST",
      body: jsonBody({
        destination_url: payload.destinationUrl,
        slug: payload.slug,
        project_id: payload.projectId ? Number(payload.projectId) : undefined,
        kol_id: payload.kolId ? Number(payload.kolId) : undefined,
        platform: payload.platform,
        product_sku: payload.productSku,
        campaign_name: payload.campaignName,
        utm_source: payload.utmSource,
        utm_medium: payload.utmMedium,
        utm_campaign: payload.utmCampaign,
        utm_content: payload.utmContent,
      }),
    },
    token,
  );
}

export async function getMarketingLinkDetail(token: string, linkId: string) {
  return apiFetch<VkpiLinkDetail>(`/api/marketing/links/${encodeURIComponent(linkId)}`, {}, token);
}

export async function pauseMarketingLink(token: string, linkId: string) {
  return apiFetch<Row>(
    `/api/marketing/links/${encodeURIComponent(linkId)}/pause`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

export async function archiveMarketingLink(token: string, linkId: string) {
  return apiFetch<Row>(
    `/api/marketing/links/${encodeURIComponent(linkId)}/archive`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

export async function healthCheckMarketingLink(token: string, linkId: string) {
  return apiFetch<Row>(
    `/api/marketing/links/${encodeURIComponent(linkId)}/health-check`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}
