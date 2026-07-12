import { apiFetch } from "../http";

// 件3 · ReplyQueue 评论区销售员 v0 半自动客户端。
// 后端:backend/app/api/routers/vkpi_reply_queue.py(前缀 /api/admin/vkpi)。
//   GET  /reply-queue                -> { items: ReplyQueueItem[], count, total, offset, limit }
//                                       (服务端分页:count=本页行数,total=过滤后总数)
//   GET  /reply-queue/kpi-series     -> { status, series:{按日 0 填齐}, prev:{环比 delta_pct} }
//   POST /reply-queue/screen         -> { ok, scanned, matched, enqueued }
//   POST /reply-queue/{id}/draft     -> { ok, provider, draft_reply, retrieved_skus }
//   POST /reply-queue/{id}/mark      -> { ok, status }
// v0 铁律:前端只列表 + 一键复制 + 标记,绝不自动发帖。

export type ReplyQueueStatus = "pending" | "drafted" | "replied" | "dismissed";

export interface ReplyQueueItem {
  id: number;
  platform: string;
  kol_pool_id: number | null;
  comment_external_id: string;
  comment_text: string;
  intent_tag: string;
  lang: string;
  draft_reply: string;
  status: ReplyQueueStatus | string;
  /** 认领人 staff_id(后端 list_queue 回传;未认领 = null) */
  claimed_by?: number | null;
  claimed_at?: string;
  created_at: string;
  updated_at: string;
}

export interface ReplyQueueListResponse {
  items: ReplyQueueItem[];
  /** 本页行数(旧契约键,零破坏) */
  count: number;
  /** 过滤后总数(服务端 COUNT,与页无关;「已显 X/Y」的真分母) */
  total: number;
  offset: number;
  limit: number;
}

export interface ScreenResult {
  ok: boolean;
  scanned?: number;
  matched?: number;
  enqueued?: number;
  reason?: string;
}

export interface DraftResult {
  ok: boolean;
  id: number;
  status?: string;
  provider?: string;
  draft_reply?: string;
  retrieved_skus?: string[];
  reason?: string;
}

export interface MarkResult {
  ok: boolean;
  id: number;
  status?: string;
  reason?: string;
}

export async function fetchReplyQueue(
  token: string,
  opts: { status?: string; platform?: string; limit?: number; offset?: number } = {},
): Promise<ReplyQueueListResponse> {
  const params = new URLSearchParams();
  if (opts.status) params.set("status", opts.status);
  if (opts.platform) params.set("platform", opts.platform);
  params.set("limit", String(opts.limit ?? 100));
  params.set("offset", String(opts.offset ?? 0));
  const res = await apiFetch<ReplyQueueListResponse>(
    `/api/admin/vkpi/reply-queue?${params.toString()}`,
    { cache: "no-store" },
    token,
  );
  const items = Array.isArray(res.items) ? res.items : [];
  return {
    items,
    count: typeof res.count === "number" ? res.count : items.length,
    // total 缺失(旧后端)→ 回落本页行数:hasMore 恒 false,行为退化为旧全量单页(零破坏)
    total: typeof res.total === "number" ? res.total : items.length,
    offset: typeof res.offset === "number" ? res.offset : (opts.offset ?? 0),
    limit: typeof res.limit === "number" ? res.limit : (opts.limit ?? 100),
  };
}

/* ============ KPI 时序(kpi-series · voice-report-ext kpi_series 同模式) ============ */

export interface KpiSeriesPoint {
  date: string;
  count: number;
}

export interface KpiSeriesDelta {
  current: number;
  previous: number;
  /** 环比百分比;上窗 0 → null(KpiCard 诚实省略药丸) */
  delta_pct: number | null;
}

export type ReplyQueueKpiMeasure = "enqueued" | "pending" | "drafted" | "replied" | "price";

export interface ReplyQueueKpiSeries {
  status: string;
  granularity?: string;
  days?: number;
  window?: { since?: string; until?: string };
  window_prev?: { since?: string; until?: string };
  /** 按日 0 填齐序列(UTC 日轴,右沿钳 now,无未来日) */
  series?: Partial<Record<ReplyQueueKpiMeasure, KpiSeriesPoint[]>>;
  /** 上一等长窗环比 */
  prev?: Partial<Record<ReplyQueueKpiMeasure, KpiSeriesDelta>>;
  basis?: Record<string, string>;
  reason?: string;
}

export async function fetchReplyQueueKpiSeries(
  token: string,
  opts: { days?: number } = {},
): Promise<ReplyQueueKpiSeries> {
  const params = new URLSearchParams();
  params.set("days", String(opts.days ?? 30));
  return apiFetch<ReplyQueueKpiSeries>(
    `/api/admin/vkpi/reply-queue/kpi-series?${params.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export async function screenReplyQueue(
  token: string,
  opts: { limit?: number; platform?: string } = {},
): Promise<ScreenResult> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 500));
  if (opts.platform) params.set("platform", opts.platform);
  return apiFetch<ScreenResult>(
    `/api/admin/vkpi/reply-queue/screen?${params.toString()}`,
    { method: "POST", timeoutMs: 30000 },
    token,
  );
}

export async function draftReply(token: string, id: number): Promise<DraftResult> {
  return apiFetch<DraftResult>(
    `/api/admin/vkpi/reply-queue/${id}/draft`,
    { method: "POST", timeoutMs: 30000 },
    token,
  );
}

export async function markReply(
  token: string,
  id: number,
  status: ReplyQueueStatus,
  /** 乐观锁(后端 expected_status):前端最后看到的状态;他人已改动 → 409 status_conflict。
   *  可选参数,旧调用方(ReplyQueuePage 回滚垫)零改动。 */
  expectedStatus?: string,
): Promise<MarkResult> {
  const params = new URLSearchParams();
  params.set("status", status);
  if (expectedStatus) params.set("expected_status", expectedStatus);
  return apiFetch<MarkResult>(
    `/api/admin/vkpi/reply-queue/${id}/mark?${params.toString()}`,
    { method: "POST" },
    token,
  );
}
