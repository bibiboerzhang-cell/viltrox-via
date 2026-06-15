import { apiFetch, jsonBody } from "../http";

// W1 Action Inbox(只读)。后端 GET /api/admin/vkpi/actions/inbox 已按 scope 过滤
//(管理层看全局,成员只看自己 owner 的)。前端不做执行,只展示「今日建议」。
// W2 在此之上加状态流转(approve/dismiss/snooze)与执行(execute)。
// 红线:approve→execute 两步;execute 仍由后端 validators 双闸(approved + touches_v6_fit=False
//        + budget + entity 存在),前端只触发,不绕审批。

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

// ── W2 状态流转 + 执行 ──────────────────────────────────────────────
// 后端契约(全部 prefix /api/admin/vkpi/actions,require_tab vkpi:write):
//   POST /{id}/approve  → {ok,status:"approved",action_id,reason?}
//   POST /{id}/dismiss  → {ok,status:"dismissed",action_id}
//   POST /{id}/snooze   → body{minutes} → {ok,status:"snoozed",action_id,snooze_until}
//   POST /{id}/execute  → {ok,outcome:"success|failed|skipped",category,reason?,ledger_id?,detail}

export interface ActionTransitionResponse {
  ok: boolean;
  status?: string;
  action_id?: number;
  reason?: string;
  snooze_until?: string;
}

export interface ActionExecuteResponse {
  ok: boolean;
  outcome?: "success" | "failed" | "skipped" | string;
  category?: string;
  reason?: string;
  ledger_id?: number;
  detail?: Record<string, unknown>;
}

export async function approveAction(
  token: string,
  id: number,
): Promise<ActionTransitionResponse> {
  return apiFetch<ActionTransitionResponse>(
    `/api/admin/vkpi/actions/${id}/approve`,
    { method: "POST", cache: "no-store" },
    token,
  );
}

export async function dismissAction(
  token: string,
  id: number,
): Promise<ActionTransitionResponse> {
  return apiFetch<ActionTransitionResponse>(
    `/api/admin/vkpi/actions/${id}/dismiss`,
    { method: "POST", cache: "no-store" },
    token,
  );
}

export async function snoozeAction(
  token: string,
  id: number,
  minutes = 1440,
): Promise<ActionTransitionResponse> {
  return apiFetch<ActionTransitionResponse>(
    `/api/admin/vkpi/actions/${id}/snooze`,
    { method: "POST", body: jsonBody({ minutes }), cache: "no-store" },
    token,
  );
}

export async function executeAction(
  token: string,
  id: number,
): Promise<ActionExecuteResponse> {
  return apiFetch<ActionExecuteResponse>(
    `/api/admin/vkpi/actions/${id}/execute`,
    { method: "POST", cache: "no-store" },
    token,
  );
}
