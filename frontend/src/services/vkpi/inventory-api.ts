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

export async function listInventory(token: string): Promise<{ items?: VkpiInventoryItem[] }> {
  return apiFetch<{ items?: VkpiInventoryItem[] }>(
    "/api/admin/vkpi/inventory",
    {},
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
  };
}
