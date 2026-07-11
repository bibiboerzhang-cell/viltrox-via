// 市场之声 API 封装(板块页范式 V0a)。
// - getVoiceReport:GET /api/admin/vkpi/market/voice-report?month=YYYY-MM
//     纯词表聚合月报(lexicon_v0,零 LLM),逐源诚实标 status。
// - getVoiceFeed:GET /api/admin/vkpi/market/voice-feed?offset=&limit=
//     vkpi_comments 分页原声流(契约与后端并行开发,固定):
//     {items:[{id,source_table,platform,text,language,identity,identity_ref,
//       post_url,likes,created_at,prov:{fetched_at,post_table,post_id}}],total,offset,limit}
//     后端内部异常不 500 → 回契约形状 + status:"error"(调用方诚实降级,绝不编数据)。
//     category(纯增量,数据点下钻):COMPLAINT_CATEGORIES 六类 key + 'wishlist' 词族
//     过滤(后端 Python 层词族匹配,total=命中数;扫描封顶时响应带 note 如实说明)。
// - enqueueReplyQueueComment:POST /api/admin/vkpi/reply-queue/enqueue-comment
//     V0e 闭环「转回复队列」:按 vkpi_comments.id 单条手动入 vkpi_reply_queue(幂等,
//     已在队返回已有行 already_queued=true;失败 4xx 由 apiFetch 抛错,调用方如实展示)。
// - createPrdReferral / listPrdReferrals:POST/GET /api/admin/vkpi/market/prd-referrals
//     V1.1 真闭环「转产品部」:一条声音(vkpi_comments)/ 一条规则建议(lexicon_reco)
//     落 vkpi_market_prd_referrals 一行(迁移 234;UNIQUE(source_table,source_id) 幂等,
//     已转返回已有行 already_referred=true;评论不存在 404,其余失败 400 带 reason)。
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
  /** 溯源身份跳锚:kol=vkpi_kol_pool.id(KOL 档案页)/ owned=vkpi_employee_channels.id / user=null */
  identity_id: number | null;
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
  /** category 下钻增量:回显过滤词族 key;扫描封顶时 note 如实说明 total 覆盖范围 */
  category?: string;
  note?: string;
}

export interface VoiceFeedOptions {
  offset?: number;
  limit?: number;
  identity?: VoiceIdentity | "";
  sentiment?: string;
  /** 词族下钻(纯增量):COMPLAINT_CATEGORIES 六类 key + 'wishlist';非法值后端 400 */
  category?: string;
}

export async function getVoiceFeed(token: string, options: VoiceFeedOptions = {}) {
  const params = new URLSearchParams({
    offset: String(options.offset ?? 0),
    limit: String(options.limit ?? 20),
  });
  if (options.identity) params.set("identity", options.identity);
  if (options.sentiment) params.set("sentiment", options.sentiment);
  if (options.category) params.set("category", options.category);
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

// ===== V1.1 真闭环:转产品部(vkpi_market_prd_referrals,迁移 234)=====
export type PrdReferralSourceTable = "vkpi_comments" | "lexicon_reco";
export type PrdReferralStatus = "referred" | "accepted" | "rejected";

export interface PrdReferralItem {
  id: number;
  source_table: string;
  source_id: string;
  title: string;
  detail: string;
  category: string;
  status: string;
  created_by_staff_id: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PrdReferralCreateBody {
  source_table: PrdReferralSourceTable;
  /** 评论=vkpi_comments.id 文本化;规则建议=稳定 key(kind + 标题主干) */
  source_id: string;
  title: string;
  detail?: string;
  category?: string;
}

export interface PrdReferralCreateResult {
  ok: boolean;
  /** 幂等命中已有行 = true(端点真实返回;前端据此显「已转交」,绝不点击即绿) */
  already_referred: boolean;
  item: PrdReferralItem;
}

export async function createPrdReferral(token: string, body: PrdReferralCreateBody) {
  return apiFetch<PrdReferralCreateResult>(
    "/api/admin/vkpi/market/prd-referrals",
    { method: "POST", body: jsonBody(body) },
    token,
  );
}

export interface PrdReferralListResponse {
  ok: boolean;
  status: string; // ready / absent(表未建,诚实空列表)
  items: PrdReferralItem[];
  count: number;
  reason?: string;
}

export async function listPrdReferrals(
  token: string,
  options: { status?: PrdReferralStatus | ""; limit?: number } = {},
) {
  const params = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.status) params.set("status", options.status);
  return apiFetch<PrdReferralListResponse>(
    `/api/admin/vkpi/market/prd-referrals?${params.toString()}`,
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

// ===== V0h-ab:voice-report-ext(十组图形化真数据字段)=====
// GET /api/admin/vkpi/market/voice-report-ext?month=YYYY-MM(缺省近30天;窗口口径与
// voice-report 一致)。十组字段各自独立 status("ready"|"empty"|"error"|"ok"),单组
// 失败诚实降级 {status:"error",reason} 不拖全响应 —— 前端逐组降级,绝不编数据。
// 契约由后端 voice_report_ext.py 固定:
//   kpi_series        按 UTC 日计数序列(comments/queue,日轴 0 填齐,sparkline 直画)
//   kpi_prev          上一等长窗口环比(上窗 0 → delta_pct=null,诚实省略药丸)
//   sentiment_summary 情绪总量 + pos/neg share + 趋势双线(空期 share=null 断线)
//   alerts_state      告警评估(纯读不推送)+ vkpi_action_inbox 最近已推送
//   platform_dist     按 platform 分组计数
//   line_voice        产品线级声量榜(词表分桶 × 情绪回链;无逐 SKU 关联,如实)
//   topics            热点话题榜(lexicon_v0 词族命中 + 环比,Top12)
//   language_dist     语言分桶(und=待检;语言分布非地理)
//   competitor_voice  竞品声量(百家饭同口径;is_self=Viltrox 自家行)
//   prd_referrals     「已转产品部」窗口真计数 + 日序列 + 环比(vkpi_market_prd_referrals;
//                     表未建 → status=empty count=null,0 也如实 0)

export interface ExtSeriesPoint {
  date: string;
  count: number;
}

export interface ExtTrendPoint {
  period_start: string;
  total: number;
  pos_share: number | null;
  neg_share: number | null;
}

export interface ExtAlertCategory {
  category: string;
  label: string;
  negative_count: number;
  score: number;
  threshold: number;
  triggered: boolean;
  window_hours: number;
}

export interface ExtAlertPushed {
  id: number;
  dedupe_key: string;
  title: string;
  priority: string;
  status: string;
  created_at: string | null;
}

type ExtGroup<T> = T & { status?: string; reason?: string; basis?: unknown };

export interface VoiceReportExt {
  status?: string;
  month?: string | null;
  window?: { since: string; until: string; label: string };
  kpi_series?: ExtGroup<{
    granularity?: string;
    series?: { comments?: ExtSeriesPoint[]; queue?: ExtSeriesPoint[] };
  }>;
  kpi_prev?: ExtGroup<{
    metrics?: Record<string, { current?: number; previous?: number; delta_pct?: number | null; table?: string }>;
  }>;
  sentiment_summary?: ExtGroup<{
    done_total?: number;
    pos_total?: number;
    neu_total?: number;
    neg_total?: number;
    pos_share?: number | null;
    neg_share?: number | null;
    granularity?: string;
    trend?: ExtTrendPoint[];
  }>;
  alerts_state?: ExtGroup<{
    method?: string;
    window_hours?: number;
    threshold?: number;
    scanned?: number;
    categories?: ExtAlertCategory[];
    recent_pushed?: ExtAlertPushed[];
  }>;
  platform_dist?: ExtGroup<{ items?: Array<{ platform: string; count: number }> }>;
  line_voice?: ExtGroup<{
    items?: Array<{ key: string; label: string; count: number; annotated: number; pos_share: number | null }>;
  }>;
  topics?: ExtGroup<{
    items?: Array<{ key: string; label: string; count: number; delta_pct: number | null }>;
    scanned?: number;
  }>;
  language_dist?: ExtGroup<{ items?: Array<{ language: string; count: number }>; total?: number }>;
  competitor_voice?: ExtGroup<{
    sample_videos?: number;
    viltrox_videos?: number;
    competitor_exposures?: number;
    items?: Array<{ brand: string; count: number; share: number | null; is_self: boolean }>;
  }>;
  /** V1.1「已转产品部」真计数(vkpi_market_prd_referrals 窗口 COUNT;表未建 → status=empty count=null) */
  prd_referrals?: ExtGroup<{
    count?: number | null;
    previous?: number | null;
    delta_pct?: number | null;
    series?: ExtSeriesPoint[];
    table?: string;
  }>;
  method?: string;
  generated_at?: string;
}

export async function fetchVoiceReportExt(token: string, month = "") {
  const query = month ? `?month=${encodeURIComponent(month)}` : "";
  return apiFetch<VoiceReportExt>(
    `/api/admin/vkpi/market/voice-report-ext${query}`,
    { timeoutMs: 30000 },
    token,
  );
}
