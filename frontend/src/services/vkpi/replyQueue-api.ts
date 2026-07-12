import { apiFetch } from "../http";

// 件3 · ReplyQueue 评论区销售员 v0 半自动客户端。
// 后端:backend/app/api/routers/vkpi_reply_queue.py(前缀 /api/admin/vkpi)。
//   GET  /reply-queue                -> { items: ReplyQueueItem[], count }
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
  count: number;
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
  opts: { status?: string; platform?: string; limit?: number } = {},
): Promise<ReplyQueueListResponse> {
  const params = new URLSearchParams();
  if (opts.status) params.set("status", opts.status);
  if (opts.platform) params.set("platform", opts.platform);
  params.set("limit", String(opts.limit ?? 100));
  const res = await apiFetch<ReplyQueueListResponse>(
    `/api/admin/vkpi/reply-queue?${params.toString()}`,
    { cache: "no-store" },
    token,
  );
  return {
    items: Array.isArray(res.items) ? res.items : [],
    count: typeof res.count === "number" ? res.count : 0,
  };
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
