// 市场之声 API 封装(板块页范式 V0a)。
// - getVoiceReport:GET /api/admin/vkpi/market/voice-report?month=YYYY-MM
//     纯词表聚合月报(lexicon_v0,零 LLM),逐源诚实标 status。
// - getVoiceFeed:GET /api/admin/vkpi/market/voice-feed?offset=&limit=
//     vkpi_comments 分页原声流(契约与后端并行开发,固定):
//     {items:[{id,source_table,platform,text,language,identity,identity_ref,
//       post_url,likes,created_at,prov:{fetched_at,post_table,post_id}}],total,offset,limit}
//     后端内部异常不 500 → 回契约形状 + status:"error"(调用方诚实降级,绝不编数据)。
// 红线:纯读展示,不触 viltrox_fit_score / rule_v0。

import { apiFetch } from "../http";

export type VoiceIdentity = "kol" | "owned" | "user";

export interface VoiceFeedProv {
  fetched_at: string | null;
  post_table: string;
  post_id: number | null;
}

export interface VoiceFeedItem {
  id: number;
  source_table: string;
  platform: string;
  text: string;
  language: string;
  identity: VoiceIdentity;
  identity_ref: string;
  post_url: string | null;
  likes: number;
  created_at: string | null;
  prov: VoiceFeedProv;
}

export interface VoiceFeedResponse {
  items: VoiceFeedItem[];
  total: number;
  offset: number;
  limit: number;
  // 后端诚实降级形状(聚合内部异常不 500)
  status?: string;
  reason?: string;
}

export interface VoiceFeedOptions {
  offset?: number;
  limit?: number;
  identity?: VoiceIdentity | "";
  sentiment?: string;
}

export async function getVoiceFeed(token: string, options: VoiceFeedOptions = {}) {
  const params = new URLSearchParams({
    offset: String(options.offset ?? 0),
    limit: String(options.limit ?? 20),
  });
  if (options.identity) params.set("identity", options.identity);
  if (options.sentiment) params.set("sentiment", options.sentiment);
  return apiFetch<VoiceFeedResponse>(
    `/api/admin/vkpi/market/voice-feed?${params.toString()}`,
    {},
    token,
  );
}

export type VoiceReport = Record<string, any>;

export async function getVoiceReport(token: string, month = "") {
  const query = month ? `?month=${encodeURIComponent(month)}` : "";
  return apiFetch<VoiceReport>(
    `/api/admin/vkpi/market/voice-report${query}`,
    { timeoutMs: 30000 },
    token,
  );
}
