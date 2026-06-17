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
  product_sku?: string;
  product_name?: string;
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
  product_sku?: string;
  product_name?: string;
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
  product_sku?: string;
  product_name?: string;
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
  // 后端 due_date 是 Postgres DATE DEFAULT NULL 列;空值必须缺省/NULL,
  // 传 "" 会触发 invalid input syntax for type date。故此字段可选,无日期时整字段省略。
  due_date?: string;
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

export interface VkpiEventDetail {
  item: VkpiEvent | null;
  tasks: VkpiEventTask[];
  expenses: VkpiEventExpense[];
  invites: VkpiEventKolInvite[];
}

/** GET 单活动详情(item + 内嵌 tasks/expenses/invites)。 */
export async function getEventDetail(
  token: string,
  eventId: string,
): Promise<VkpiEventDetail> {
  return apiFetch<VkpiEventDetail>(
    `/api/admin/vkpi/events/${encodeURIComponent(eventId)}`,
    {},
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
  productSku?: string;
  productName?: string;
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
    productSku: (row as any).product_sku || "",
    productName: (row as any).product_name || "",
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
    product_sku: String(data.productSku || ""),
    product_name: String(data.productName || ""),
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
  if (ui.productSku !== undefined) out.product_sku = ui.productSku;
  if (ui.productName !== undefined) out.product_name = ui.productName;
  if (ui.roi !== undefined) out.roi = ui.roi;
  if (ui.retrospective !== undefined) out.retrospective = ui.retrospective;
  return out;
}

// ── Detail-tab adapters:后端 snake_case ↔ tab 期望的 camelCase ───────────────
// TasksTab / BudgetExpensesTab / KolsTab 用 dueDate/doneAt/paidBy/kolId… 形态;
// 后端是 due_date/done_at/paid_by/kol_id…。注:后端无 expense.date/receipt 列,
// 用 created_at 派生展示日期;notes 收纳进 task.details.notes。

export interface UiEventTask {
  id: string;
  phase: string;
  title: string;
  owner: string;
  collaborators: string[];
  dueDate: string;
  kind?: string;
  checklist?: Array<{ label: string; done: boolean; value?: string }>;
  details?: Record<string, unknown>;
  notes?: string;
  done: boolean;
  doneAt?: string;
  doneBy?: string;
}

/** 后端 due_date(可能是 ISO / "MM/DD" / 空)→ tab 显示用短日期 "M/D"。 */
function shortDate(raw: unknown): string {
  if (!raw) return "";
  const s = String(raw);
  // 已是 "M/D" 之类的就原样返回
  if (/^\d{1,2}\/\d{1,2}/.test(s)) return s;
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** 后端 created_at(ISO)→ "M/D HH:MM" 展示。 */
function shortDateTime(raw: unknown): string {
  if (!raw) return "";
  const d = new Date(String(raw));
  if (Number.isNaN(d.getTime())) return String(raw);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function toUiTask(row: VkpiEventTask): UiEventTask {
  const details = (row.details && typeof row.details === "object" ? row.details : {}) as Record<string, unknown>;
  return {
    id: row.id,
    phase: row.phase || "prep",
    title: row.title || "",
    owner: row.owner || "All",
    collaborators: Array.isArray(row.collaborators) ? row.collaborators : [],
    dueDate: shortDate(row.due_date),
    kind: row.kind || undefined,
    checklist: Array.isArray(row.checklist) ? row.checklist : undefined,
    details,
    notes: typeof details.notes === "string" ? details.notes : undefined,
    done: !!row.done,
    doneAt: row.done_at ? shortDate(row.done_at) : undefined,
    doneBy: row.done_by || undefined,
  };
}

/** NewTaskModal onSubmit(camel)→ 创建 payload(snake);notes 折叠进 details.notes。 */
export function fromUiTaskCreate(t: Record<string, any>): VkpiEventTaskCreatePayload {
  const details: Record<string, unknown> = { ...(t.details || {}) };
  if (t.notes) details.notes = t.notes;
  return {
    title: String(t.title || ""),
    phase: String(t.phase || "prep"),
    owner: String(t.owner || "All"),
    collaborators: Array.isArray(t.collaborators) ? t.collaborators : [],
    due_date: t.dueDate ? String(t.dueDate) : undefined,
    kind: t.kind || undefined,
    checklist: Array.isArray(t.checklist) ? t.checklist : undefined,
    details,
  };
}

export interface UiEventExpense {
  id: string;
  amount: number;
  category: string;
  description: string;
  paidBy: string;
  paymentMethod: string;
  reimbursementStatus: string;
  date: string;
  receipt: boolean;
}

export function toUiExpense(row: VkpiEventExpense): UiEventExpense {
  return {
    id: row.id,
    amount: Number(row.amount || 0),
    category: row.category || "other",
    description: row.description || "",
    paidBy: row.paid_by || "",
    paymentMethod: row.payment_method || "other",
    reimbursementStatus: row.reimbursement_status || "n/a",
    date: shortDateTime(row.created_at),
    receipt: false, // 后端暂无票据列
  };
}

/** ExpenseEntryModal onSubmit(camel)→ 创建 payload(snake)。 */
export function fromUiExpenseCreate(x: Record<string, any>): VkpiEventExpenseCreatePayload {
  return {
    amount: Number(x.amount || 0),
    category: String(x.category || "other"),
    description: String(x.description || ""),
    paid_by: String(x.paidBy || ""),
    payment_method: String(x.paymentMethod || "other"),
    reimbursement_status: String(x.reimbursementStatus || "pending"),
  };
}

export interface UiEventInvite {
  id: string;
  kolId: string;
  status: string;
  days: string;
  travel: string;
}

export function toUiInvite(row: VkpiEventKolInvite): UiEventInvite {
  return {
    id: row.id,
    kolId: row.kol_id || "",
    status: row.status || "pending",
    days: row.days || "",
    travel: row.travel_status || "",
  };
}

/** InviteKolModal onSubmit({id,status})→ 创建 payload(kol_id)。 */
export function fromUiInviteCreate(k: Record<string, any>): VkpiEventKolInviteCreatePayload {
  return {
    kol_id: String(k.kolId || k.id || ""),
    status: String(k.status || "pending"),
    days: k.days ? String(k.days) : undefined,
    travel_status: k.travel ? String(k.travel) : undefined,
  };
}
