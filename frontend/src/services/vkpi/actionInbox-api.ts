import { apiFetch } from "../http";

// W1 Action Inbox(只读)。后端 GET /api/admin/vkpi/actions/inbox 已按 scope 过滤
//(管理层看全局,成员只看自己 owner 的)。前端不做执行,只展示「今日建议」。

export interface ActionInboxItem {
  id: number;
  dedupe_key: string;
  category: string;
  title: string;
  detail: string;
  priority: "high" | "medium" | "low" | string;
  entity_type: string;
  entity_id: string;
  suggested_endpoint: string;
  estimated_cost_cents: number;
  writes_business_data: boolean;
  uses_llm: boolean;
  requires_approval: boolean;
  owner_staff_id: number | null;
  reason: string;
  payload_json: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ActionInboxResponse {
  items: ActionInboxItem[];
  available: boolean;
  count?: number;
  by_category?: Record<string, number>;
  scope?: "all" | "own" | string;
  reason?: string;
}

export async function listActionInbox(
  token: string,
  params: { limit?: number; category?: string; status?: string } = {},
): Promise<ActionInboxResponse> {
  const query = new URLSearchParams();
  query.set("limit", String(params.limit ?? 50));
  if (params.category) query.set("category", params.category);
  if (params.status) query.set("status", params.status);
  return apiFetch<ActionInboxResponse>(
    `/api/admin/vkpi/actions/inbox?${query.toString()}`,
    { cache: "no-store" },
    token,
  );
}
