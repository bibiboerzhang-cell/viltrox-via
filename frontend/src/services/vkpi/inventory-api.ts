import { apiFetch, jsonBody } from "../http";

// 公司库存表(Company Inventory)真后端 API —— 镜像 events-api.ts 风格。
// 前缀 /api/admin/vkpi/inventory;sku 为资源键(PATCH/DELETE /{sku})。

export interface VkpiInventoryItem {
  id: string;
  sku: string;
  name: string;
  category: string;
  qty: number;
  location: string;
  note: string;
  is_sample: boolean;
  quantity_status?: "unverified" | "manual_confirmed" | "source_confirmed" | string;
  quantity_source?: string | null;
  quantity_source_ref?: string | null;
  quantity_source_observed_at?: string | null;
  quantity_evidence_sha256?: string | null;
  quantity_verified_by_staff_id?: number | null;
  quantity_verified_organization_id?: number | null;
  quantity_verified_at?: string | null;
  row_version?: number;
  created_by_staff_id?: number | null;
  created_at?: string;
  updated_at?: string;
}

export interface VkpiInventoryCreatePayload {
  sku: string;
  name: string;
  category?: string;
  qty?: number;
  location?: string;
  note?: string;
  is_sample?: boolean;
  reason?: string;
  event_id?: string;
}

export interface VkpiInventoryUpdatePayload {
  name?: string;
  category?: string;
  qty?: number;
  location?: string;
  note?: string;
  is_sample?: boolean;
  reason?: string;
  event_id?: string;
}

export interface VkpiInventoryMovement {
  id: string;
  inventory_sku: string;
  event_id?: string | null;
  action: "add" | "edit" | "delete" | "adjust" | string;
  delta_qty?: number | null;
  new_qty?: number | null;
  reason: string;
  moved_by_staff_id?: number | null;
  metadata_json?: Record<string, unknown>;
  created_at?: string;
}

export async function listInventory(
  token: string,
  options: { signal?: AbortSignal } = {},
): Promise<{ items?: VkpiInventoryItem[] }> {
  return apiFetch<{ items?: VkpiInventoryItem[] }>(
    "/api/admin/vkpi/inventory",
    { signal: options.signal },
    token,
  );
}

export interface VkpiInventoryAuthorizationEvidence {
  authorizationRef: string;
  reason: string;
  confirmedByHuman: boolean;
}

export interface VkpiInventoryQuantityCas {
  expectedId: string;
  expectedQty: number;
  expectedRowVersion: number;
  expectedUpdatedAt: string;
}

export interface VkpiInventoryQuantityVerificationPayload
  extends VkpiInventoryAuthorizationEvidence, VkpiInventoryQuantityCas {
  sourceType: "physical_count_sheet" | "warehouse_confirmation" | "wms_export" | "erp_export" | "shopify_inventory_snapshot";
  sourceRef: string;
  sourceObservedAt: string;
  evidenceSha256: string;
}

function authorizationBody(payload: VkpiInventoryAuthorizationEvidence) {
  return {
    authorization_evidence: {
      authorization_ref: payload.authorizationRef,
      reason: payload.reason,
      confirmed_by_human: payload.confirmedByHuman,
    },
  };
}

export async function verifyInventoryQuantity(
  token: string,
  sku: string,
  payload: VkpiInventoryQuantityVerificationPayload,
): Promise<{ item?: VkpiInventoryItem; verified: boolean; quantity_changed: boolean }> {
  return apiFetch(
    `/api/admin/vkpi/inventory/${encodeURIComponent(sku)}/verify`,
    {
      method: "POST",
      body: jsonBody({
        source_type: payload.sourceType,
        source_ref: payload.sourceRef,
        source_observed_at: payload.sourceObservedAt,
        evidence_sha256: payload.evidenceSha256,
        expected_id: payload.expectedId,
        expected_qty: payload.expectedQty,
        expected_row_version: payload.expectedRowVersion,
        expected_updated_at: payload.expectedUpdatedAt,
        ...authorizationBody(payload),
      }),
    },
    token,
  );
}

export async function revokeInventoryQuantityVerification(
  token: string,
  sku: string,
  payload: VkpiInventoryAuthorizationEvidence & VkpiInventoryQuantityCas,
): Promise<{ item?: VkpiInventoryItem; verified: boolean; quantity_changed: boolean }> {
  return apiFetch(
    `/api/admin/vkpi/inventory/${encodeURIComponent(sku)}/verification/revoke`,
    {
      method: "POST",
      body: jsonBody({
        expected_id: payload.expectedId,
        expected_qty: payload.expectedQty,
        expected_row_version: payload.expectedRowVersion,
        expected_updated_at: payload.expectedUpdatedAt,
        ...authorizationBody(payload),
      }),
    },
    token,
  );
}

export async function createInventoryItem(
  token: string,
  payload: VkpiInventoryCreatePayload,
): Promise<{ item?: VkpiInventoryItem }> {
  return apiFetch<{ item?: VkpiInventoryItem }>(
    "/api/admin/vkpi/inventory",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export async function updateInventoryItem(
  token: string,
  sku: string,
  payload: VkpiInventoryUpdatePayload,
): Promise<{ item?: VkpiInventoryItem }> {
  return apiFetch<{ item?: VkpiInventoryItem }>(
    `/api/admin/vkpi/inventory/${encodeURIComponent(sku)}`,
    {
      method: "PATCH",
      body: jsonBody(payload),
    },
    token,
  );
}

export async function deleteInventoryItem(
  token: string,
  sku: string,
  payload: { reason?: string; event_id?: string } = {},
): Promise<{ ok: boolean; sku?: string }> {
  return apiFetch<{ ok: boolean; sku?: string }>(
    `/api/admin/vkpi/inventory/${encodeURIComponent(sku)}`,
    {
      method: "DELETE",
      body: jsonBody(payload),
    },
    token,
  );
}

export async function getInventoryMovements(
  token: string,
  sku?: string | null,
  limit = 100,
): Promise<{ items?: VkpiInventoryMovement[] }> {
  // sku 给定 → 该 SKU 的调动流水;否则后端无全量 movements 端点(只暴露 /{sku}/movements)。
  const target = sku ? encodeURIComponent(sku) : "";
  if (!target) return { items: [] };
  const query = new URLSearchParams({ limit: String(limit) });
  return apiFetch<{ items?: VkpiInventoryMovement[] }>(
    `/api/admin/vkpi/inventory/${target}/movements?${query.toString()}`,
    {},
    token,
  );
}

// ── Groups 组团(逻辑分组:箱子里装了哪些;不扣库存)─────────────────────────
export interface VkpiInventoryGroupItem {
  id: string;
  inventory_sku: string;
  qty_in_group: number;
  note?: string;
  item_name?: string;
  item_category?: string;
  total_available?: number | null;
}
export interface VkpiInventoryGroup {
  id: string;
  name: string;
  note?: string;
  location?: string;
  event_id?: string | null;
  item_count?: number;
  total_units?: number;
  items?: VkpiInventoryGroupItem[];
  created_at?: string;
}

export async function listInventoryGroups(token: string): Promise<{ groups?: VkpiInventoryGroup[] }> {
  return apiFetch<{ groups?: VkpiInventoryGroup[] }>("/api/admin/vkpi/inventory/groups", {}, token);
}
export async function createInventoryGroup(
  token: string,
  payload: { name: string; note?: string; location?: string; event_id?: string; items?: { sku: string; qty?: number; note?: string }[] },
): Promise<{ group?: VkpiInventoryGroup }> {
  return apiFetch<{ group?: VkpiInventoryGroup }>(
    "/api/admin/vkpi/inventory/groups",
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}
export async function updateInventoryGroup(
  token: string, groupId: string, payload: { name?: string; note?: string; location?: string; event_id?: string },
): Promise<{ group?: VkpiInventoryGroup }> {
  return apiFetch<{ group?: VkpiInventoryGroup }>(
    `/api/admin/vkpi/inventory/groups/${encodeURIComponent(groupId)}`,
    { method: "PATCH", body: jsonBody(payload) },
    token,
  );
}
export async function deleteInventoryGroup(token: string, groupId: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(
    `/api/admin/vkpi/inventory/groups/${encodeURIComponent(groupId)}`,
    { method: "DELETE", body: jsonBody({}) },
    token,
  );
}
export async function addToInventoryGroup(
  token: string, groupId: string, sku: string, qty = 1, note = "",
): Promise<{ group?: VkpiInventoryGroup }> {
  return apiFetch<{ group?: VkpiInventoryGroup }>(
    `/api/admin/vkpi/inventory/groups/${encodeURIComponent(groupId)}/items`,
    { method: "POST", body: jsonBody({ sku, qty, note }) },
    token,
  );
}
export async function removeFromInventoryGroup(token: string, groupId: string, itemId: string): Promise<{ group?: VkpiInventoryGroup }> {
  return apiFetch<{ group?: VkpiInventoryGroup }>(
    `/api/admin/vkpi/inventory/groups/${encodeURIComponent(groupId)}/items/${encodeURIComponent(itemId)}`,
    { method: "DELETE" },
    token,
  );
}
export async function importInventoryFromCatalog(token: string): Promise<{ ok: boolean; imported: number }> {
  return apiFetch<{ ok: boolean; imported: number }>(
    "/api/admin/vkpi/inventory/import-from-catalog",
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

// ── Adapter:后端 snake_case(is_sample/created_at)↔ 前端 stock 形态(isSample)──────
// stock.js / StockManagerModal 用 { id, name, category, qty, location, sku, note, isSample }。
export interface UiStockItem {
  id: string;
  name: string;
  category: string;
  qty: number;
  location: string;
  sku: string;
  note: string;
  isSample: boolean;
  quantityStatus: string;
  quantitySource: string;
}

export function toUiStock(row: VkpiInventoryItem): UiStockItem {
  return {
    id: row.id,
    name: row.name || "",
    category: row.category || "lens",
    qty: Number(row.qty || 0),
    location: row.location || "",
    sku: row.sku,
    note: row.note || "",
    isSample: !!row.is_sample,
    quantityStatus: row.quantity_status || "unverified",
    quantitySource: row.quantity_source || "unknown",
  };
}
