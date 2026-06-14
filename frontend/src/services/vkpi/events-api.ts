import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

export interface EventLocation {
  name: string;
  city: string;
  country: string;
  lat?: number;
  lng?: number;
}

export interface VkpiEvent {
  id: string;
  title: string;
  type_key: string;
  status: "planning" | "prep_ready" | "in_progress" | "done" | string;
  health_score?: number;
  note?: string;
  start_date: string;
  end_date: string;
  location_name: string;
  location_city: string;
  location_country: string;
  location_lat?: number;
  location_lng?: number;
  budget_total: number;
  budget_json?: Record<string, { plan: number; spent: number }>;
  owner_id: string;
  team_ids?: string[];
  related_project_ids?: string[];
  invited_kols_json?: Array<{
    kol_id: string;
    status: "confirmed" | "pending" | "declined" | string;
    days?: string;
    travel_status?: string;
  }>;
  roi?: number;
  leads?: number;
  videos?: number;
  retrospective?: string;
  created_at?: string;
  updated_at?: string;
}

export interface VkpiEventTask {
  id: string;
  event_id: string;
  title: string;
  phase: string;
  owner: string;
  collaborators?: string[];
  due_date: string;
  kind?: "equipment" | "materials" | "logistic" | string;
  checklist?: Array<{ label: string; done: boolean; value?: string }>;
  details?: Record<string, unknown>;
  done: boolean;
  done_at?: string;
  done_by?: string;
  created_at?: string;
  updated_at?: string;
}

export interface VkpiEventExpense {
  id: string;
  event_id: string;
  amount: number;
  category: string;
  description: string;
  paid_by: string;
  payment_method: string;
  reimbursement_status: string;
  created_at?: string;
  updated_at?: string;
}

export interface VkpiEventKolInvite {
  id: string;
  event_id: string;
  kol_id: string;
  status: "confirmed" | "pending" | "declined" | string;
  days?: string;
  travel_status?: string;
  created_at?: string;
  updated_at?: string;
}

export interface VkpiEventCreatePayload {
  title: string;
  type_key: string;
  status?: string;
  start_date: string;
  end_date: string;
  location_name: string;
  location_city: string;
  location_country: string;
  location_lat?: number;
  location_lng?: number;
  budget_total: number;
  budget_json?: Record<string, { plan: number; spent: number }>;
  owner_id: string;
  team_ids?: string[];
  related_project_ids?: string[];
  note?: string;
}

export interface VkpiEventUpdatePayload {
  title?: string;
  type_key?: string;
  status?: string;
  health_score?: number;
  note?: string;
  start_date?: string;
  end_date?: string;
  location_name?: string;
  location_city?: string;
  location_country?: string;
  location_lat?: number;
  location_lng?: number;
  budget_total?: number;
  budget_json?: Record<string, { plan: number; spent: number }>;
  owner_id?: string;
  team_ids?: string[];
  related_project_ids?: string[];
  roi?: number;
  leads?: number;
  videos?: number;
  retrospective?: string;
}

export interface VkpiEventTaskCreatePayload {
  title: string;
  phase: string;
  owner: string;
  collaborators?: string[];
  due_date: string;
  kind?: string;
  checklist?: Array<{ label: string; done: boolean; value?: string }>;
  details?: Record<string, unknown>;
}

export interface VkpiEventTaskUpdatePayload {
  title?: string;
  phase?: string;
  owner?: string;
  collaborators?: string[];
  due_date?: string;
  kind?: string;
  checklist?: Array<{ label: string; done: boolean; value?: string }>;
  details?: Record<string, unknown>;
  done?: boolean;
  done_by?: string;
}

export interface VkpiEventExpenseCreatePayload {
  amount: number;
  category: string;
  description: string;
  paid_by: string;
  payment_method: string;
  reimbursement_status?: string;
}

export interface VkpiEventKolInviteCreatePayload {
  kol_id: string;
  status?: string;
  days?: string;
  travel_status?: string;
}

export interface ApiResponse<T> {
  status?: string;
  message?: string;
  items?: T[];
  item?: T;
  ok?: boolean;
  data?: T;
  error?: string;
}

export async function listEvents(
  token: string,
  params: { limit?: number; offset?: number; status?: string; owner_id?: string } = {},
): Promise<{ items?: VkpiEvent[] }> {
  const query = new URLSearchParams({ limit: String(params.limit || 100) });
  if (typeof params.offset === "number") query.set("offset", String(params.offset));
  if (params.status) query.set("status", params.status);
  if (params.owner_id) query.set("owner_id", params.owner_id);
  return apiFetch<{ items?: VkpiEvent[] }>(
    `/api/admin/vkpi/events?${query.toString()}`,
    {},
    token,
  );
}

export async function createEvent(
  token: string,
  payload: VkpiEventCreatePayload,
): Promise<{ item?: VkpiEvent } | VkpiEvent> {
  return apiFetch<{ item?: VkpiEvent } | VkpiEvent>(
    "/api/admin/vkpi/events",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export async function updateEvent(
  token: string,
  eventId: string,
  payload: VkpiEventUpdatePayload,
): Promise<{ item?: VkpiEvent } | VkpiEvent> {
  return apiFetch<{ item?: VkpiEvent } | VkpiEvent>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}`,
    {
      method: "PATCH",
      body: jsonBody(payload),
    },
    token,
  );
}

export async function deleteEvent(
  token: string,
  eventId: string,
): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}`,
    {
      method: "DELETE",
    },
    token,
  );
}

export async function addEventTask(
  token: string,
  eventId: string,
  payload: VkpiEventTaskCreatePayload,
): Promise<{ item?: VkpiEventTask } | VkpiEventTask> {
  return apiFetch<{ item?: VkpiEventTask } | VkpiEventTask>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/tasks`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export async function updateEventTask(
  token: string,
  eventId: string,
  taskId: string,
  payload: VkpiEventTaskUpdatePayload,
): Promise<{ item?: VkpiEventTask } | VkpiEventTask> {
  return apiFetch<{ item?: VkpiEventTask } | VkpiEventTask>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/tasks/${encodeURIComponent(taskId)}`,
    {
      method: "PATCH",
      body: jsonBody(payload),
    },
    token,
  );
}

export async function deleteEventTask(
  token: string,
  eventId: string,
  taskId: string,
): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/tasks/${encodeURIComponent(taskId)}`,
    {
      method: "DELETE",
    },
    token,
  );
}

export async function addEventExpense(
  token: string,
  eventId: string,
  payload: VkpiEventExpenseCreatePayload,
): Promise<{ item?: VkpiEventExpense } | VkpiEventExpense> {
  return apiFetch<{ item?: VkpiEventExpense } | VkpiEventExpense>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/expenses`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export async function deleteEventExpense(
  token: string,
  eventId: string,
  expenseId: string,
): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/expenses/${encodeURIComponent(expenseId)}`,
    {
      method: "DELETE",
    },
    token,
  );
}

export async function inviteKolToEvent(
  token: string,
  eventId: string,
  payload: VkpiEventKolInviteCreatePayload,
): Promise<{ item?: VkpiEventKolInvite } | VkpiEventKolInvite> {
  return apiFetch<{ item?: VkpiEventKolInvite } | VkpiEventKolInvite>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/kols`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export async function removeKolFromEvent(
  token: string,
  eventId: string,
  inviteId: string,
): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/kols/${encodeURIComponent(inviteId)}`,
    {
      method: "DELETE",
    },
    token,
  );
}

export async function addEventMember(
  token: string,
  eventId: string,
  userId: string,
): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/members`,
    {
      method: "POST",
      body: jsonBody({ user_id: userId }),
    },
    token,
  );
}

export async function removeEventMember(
  token: string,
  eventId: string,
  userId: string,
): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}/members/${encodeURIComponent(userId)}`,
    {
      method: "DELETE",
    },
    token,
  );
}

// ── Adapter:后端 snake/flat ↔ 前端 camel/nested ────────────────────────────
// EventsPage/EventCard/Modal 用 camelCase 嵌套形态(typeKey/location:{}/teamUserIds…);
// 后端是 snake_case 扁平。owner_id 是 staff FK(由后端按登录人填,前端不传);
// UI 的 ownerId(字母身份)从 team_ids[0] 还原(NewEventModal 本就 ownerId = teamIds[0])。

export interface UiEvent {
  id: string;
  title: string;
  typeKey: string;
  status: string;
  healthScore: number;
  startDate: string;
  endDate: string;
  location: { name: string; city: string; country: string; lat?: number; lng?: number };
  budgetTotal: number;
  budgetByCategory: Record<string, unknown>;
  ownerId: string;
  teamUserIds: string[];
  relatedProjectIds: string[];
  invitedKols: unknown[];
  note: string;
  roi?: number;
  retrospective?: string;
  updatedAt: string;
}

/** 解包 {item} | {event} | 裸对象 → 单实体 row */
export function unwrapItem<T = VkpiEvent>(res: unknown): T | null {
  if (!res || typeof res !== "object") return null;
  const r = res as Row;
  return (r.item ?? r.event ?? r.task ?? r.expense ?? r.invite ?? r) as T;
}

export function toUiEvent(row: VkpiEvent): UiEvent {
  const team = (Array.isArray(row.team_ids) ? row.team_ids : []).map(String);
  let updated = "刚刚";
  if (row.updated_at) {
    const d = new Date(row.updated_at);
    if (!Number.isNaN(d.getTime())) {
      updated = `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    }
  }
  return {
    id: row.id,
    title: row.title,
    typeKey: row.type_key,
    status: row.status,
    healthScore: row.health_score ?? 100,
    startDate: row.start_date,
    endDate: row.end_date,
    location: {
      name: row.location_name || "",
      city: row.location_city || "",
      country: row.location_country || "",
      lat: row.location_lat,
      lng: row.location_lng,
    },
    budgetTotal: row.budget_total ?? 0,
    budgetByCategory: (row.budget_json as Record<string, unknown>) || {},
    ownerId: String(team[0] ?? row.owner_id ?? "j"),
    teamUserIds: team,
    relatedProjectIds: (Array.isArray(row.related_project_ids) ? row.related_project_ids : []).map(String),
    invitedKols: Array.isArray(row.invited_kols_json) ? row.invited_kols_json : [],
    note: row.note || "",
    roi: row.roi,
    retrospective: row.retrospective,
    updatedAt: updated,
  };
}

/** NewEventModal 的 onSubmit data → 创建 payload(owner_id 留空,后端按登录人填 FK)。 */
export function fromUiCreate(data: Record<string, any>): VkpiEventCreatePayload {
  return {
    title: String(data.title || "未命名活动"),
    type_key: String(data.typeKey || "other"),
    status: "planning",
    start_date: String(data.startDate || ""),
    end_date: String(data.endDate || data.startDate || ""),
    location_name: String(data.locName || ""),
    location_city: String(data.city || ""),
    location_country: String(data.country || ""),
    location_lat: data.lat,
    location_lng: data.lng,
    budget_total: Number(data.budget || 0),
    team_ids: (data.teamIds || []) as string[],
    related_project_ids: (data.projectIds || []) as string[],
    note: String(data.note || ""),
    owner_id: "", // 后端忽略空串,按 require_tab 注入的登录 staff 填
  };
}

/** UI 嵌套 event → 更新 payload(回写各字段)。 */
export function fromUiUpdate(ui: Record<string, any>): VkpiEventUpdatePayload {
  const loc = ui.location || {};
  const out: VkpiEventUpdatePayload = {};
  if (ui.title !== undefined) out.title = ui.title;
  if (ui.typeKey !== undefined) out.type_key = ui.typeKey;
  if (ui.status !== undefined) out.status = ui.status;
  if (ui.healthScore !== undefined) out.health_score = ui.healthScore;
  if (ui.note !== undefined) out.note = ui.note;
  if (ui.startDate !== undefined) out.start_date = ui.startDate;
  if (ui.endDate !== undefined) out.end_date = ui.endDate;
  if (loc.name !== undefined) out.location_name = loc.name;
  if (loc.city !== undefined) out.location_city = loc.city;
  if (loc.country !== undefined) out.location_country = loc.country;
  if (loc.lat !== undefined) out.location_lat = loc.lat;
  if (loc.lng !== undefined) out.location_lng = loc.lng;
  if (ui.budgetTotal !== undefined) out.budget_total = ui.budgetTotal;
  if (ui.budgetByCategory !== undefined) out.budget_json = ui.budgetByCategory;
  if (ui.teamUserIds !== undefined) out.team_ids = ui.teamUserIds;
  if (ui.relatedProjectIds !== undefined) out.related_project_ids = ui.relatedProjectIds;
  if (ui.roi !== undefined) out.roi = ui.roi;
  if (ui.retrospective !== undefined) out.retrospective = ui.retrospective;
  return out;
}
