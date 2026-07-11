// 市场之声 API 封装(板块页范式 V0a)。
// - getVoiceReport:GET /api/admin/vkpi/market/voice-report?month=YYYY-MM
//     纯词表聚合月报(lexicon_v0,零 LLM),逐源诚实标 status。
// - getVoiceFeed:GET /api/admin/vkpi/market/voice-feed?offset=&limit=
//     vkpi_comments 分页原声流(契约与后端并行开发,固定):
//     {items:[{id,source_table,platform,text,language,identity,identity_ref,
//       post_url,likes,created_at,prov:{fetched_at,post_table,post_id}}],total,offset,limit}
//     后端内部异常不 500 → 回契约形状 + status:"error"(调用方诚实降级,绝不编数据)。
// - enqueueReplyQueueComment:POST /api/admin/vkpi/reply-queue/enqueue-comment
//     V0e 闭环「转回复队列」:按 vkpi_comments.id 单条手动入 vkpi_reply_queue(幂等,
//     已在队返回已有行 already_queued=true;失败 4xx 由 apiFetch 抛错,调用方如实展示)。
// 红线:不触 viltrox_fit_score / rule_v0。

import { apiFetch, jsonBody } from "../http";

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

// V0e 闭环:转回复队列(后端契约 reply_queue.enqueue_comment)
export interface ReplyQueueEnqueueItem {
  id: number;
  platform: string;
  comment_external_id: string;
  intent_tag: string;
  lang: string;
  status: string;
  created_at: string;
}

export interface ReplyQueueEnqueueResult {
  ok: boolean;
  already_queued: boolean;
  comment_id: number;
  item: ReplyQueueEnqueueItem;
}

export async function enqueueReplyQueueComment(token: string, commentId: number) {
  return apiFetch<ReplyQueueEnqueueResult>(
    "/api/admin/vkpi/reply-queue/enqueue-comment",
    { method: "POST", body: jsonBody({ comment_id: commentId }) },
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
