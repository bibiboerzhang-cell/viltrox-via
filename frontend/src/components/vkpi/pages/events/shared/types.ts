// Events 模块 · 共享类型
// .js → .ts 化时新增。集中放各组件 props / 视图模型(VM)的形状,避免重复声明。
// 这些 VM 类型对 mock + 真后端两条数据源都成立:对松散字段保持宽松(可选 / unknown),
// 行为保持不变,只把"隐式 any"显式化。

import type { LucideIcon } from "lucide-react";

// ── 基础视图模型 ───────────────────────────────────────────────────────────

/** 预算分类单元格(budgetByCategory 的 value)。 */
export interface BudgetCell {
  plan?: number;
  spent?: number;
  [k: string]: unknown;
}

/** 活动地点。 */
export interface EventLocationVm {
  name?: string;
  city?: string;
  country?: string;
  lat?: number;
  lng?: number;
}

/** 受邀 KOL(嵌在 event.invitedKols 里的简记,仅 UI 计数用)。 */
export interface InvitedKolVm {
  id?: string;
  status?: string;
  [k: string]: unknown;
}

/**
 * 活动视图模型 —— 兼容 mock 与真后端两套字段。
 * events-api 也导出了 UiEvent,但各 tab 还会读它没列出的字段(ownerName/leads…),
 * 且把松散字段(budgetByCategory/invitedKols)当结构化用,故此处放一个更贴合 UI 的宽松 VM。
 */
export interface EventVm {
  id: string;
  title: string;
  typeKey: string;
  status: string;
  /** Nullable until an operator or a sourced scoring process actually assesses it. */
  healthScore: number | null;
  startDate: string;
  endDate: string;
  location: EventLocationVm;
  budgetTotal: number;
  budgetByCategory: Record<string, BudgetCell>;
  ownerId: string;
  ownerName?: string;
  teamUserIds: string[];
  relatedProjectIds: string[];
  productSku?: string;
  productName?: string;
  invitedKols: InvitedKolVm[];
  note?: string;
  roi?: number;
  leads?: number;
  retrospective?: string;
  updatedAt: string;
  [k: string]: unknown;
}

/** 登录态 / events 内部使用的当前用户(由 EventsMockupPage 派生)。 */
export interface CurrentUserVm {
  id: string;
  name: string;
  initial?: string;
  color?: string;
  [k: string]: unknown;
}

/** 真成员(导航传入的 staff[])。 */
export interface UiStaff {
  id: string | number;
  name: string;
  email?: string;
  isAdmin?: boolean;
  avatar?: string;
  color?: string;
  title?: string;
  [k: string]: unknown;
}

/** lookups.memberFromStaff / ownerById 统一返回的成员渲染形状。 */
export interface MemberVm {
  id: string | number;
  name: string;
  color: string;
  initial: string;
}

/**
 * ownerByInitial 的返回:命中 TEAM 时是完整 MemberVm,命不中时只有 initial/color。
 * name/id 可选 —— 保留原行为(回退分支无 name,渲染处用 `name || initial` 兜底)。
 */
export interface MemberLike {
  id?: string | number;
  name?: string;
  color: string;
  initial: string;
}

/** 项目(关联 Project)。 */
export interface ProjectVm {
  id: string;
  title: string;
}

// ── 库存 / 分组 / 调动 ──────────────────────────────────────────────────────

/** 公司库存项(UiStockItem 的 UI 侧形状,字段宽松兼容)。 */
export interface StockItem {
  id: string;
  name: string;
  category: string;
  qty: number;
  location: string;
  sku: string;
  note?: string;
  isSample?: boolean;
  quantityStatus?: string;
  quantitySource?: string;
}

/** 库存分组成员。 */
export interface StockGroupItem {
  id: string;
  inventory_sku: string;
  qty_in_group?: number;
  item_name?: string;
  total_available?: number | null;
}

/** 库存分组。 */
export interface StockGroup {
  id: string;
  name: string;
  location?: string;
  item_count?: number;
  total_units?: number;
  items?: StockGroupItem[];
}

/** 调动记录。 */
export interface StockMovement {
  id: string;
  inventory_sku?: string;
  action?: string;
  delta_qty?: number | null;
  new_qty?: number | null;
  reason?: string;
  moved_by_staff_id?: number | null;
  created_at?: string;
}

// ── 物料 / 产品准备 ─────────────────────────────────────────────────────────

/** 营销物料行。 */
export interface MaterialVm {
  id: string;
  name: string;
  category: string;
  qty: number;
  source: string;
  status: string;
  owner: string;
  note?: string;
  trackingNo?: string;
  updatedAt?: string;
  alert?: string;
}

/** 产品准备行。 */
export interface ProductVm {
  id: string;
  name: string;
  category: string;
  source: string;
  status: string;
  qty: number;
  owner: string;
  note?: string;
  trackingNo?: string;
  arriveBy?: string;
  returnAfter?: boolean;
}

// ── 任务 ────────────────────────────────────────────────────────────────────

/** checklist 子项。 */
export interface ChecklistItem {
  label: string;
  done: boolean;
  value?: string;
}

/** 设备 / 物料清单子项(task.details.items)。 */
export interface DetailItem {
  name: string;
  qty: number;
  source: string;
  status: string;
  note?: string;
  [k: string]: unknown;
}

/** 任务行(兼容 mock 与真后端 UiEventTask)。 */
export interface TaskVm {
  id: string;
  phase: string;
  title: string;
  owner: string;
  collaborators?: string[];
  dueDate?: string;
  kind?: string;
  checklist?: ChecklistItem[];
  details?: { items?: DetailItem[]; notes?: string; [k: string]: unknown };
  notes?: string;
  done: boolean;
  doneAt?: string;
  doneBy?: string;
  alert?: string;
}

// ── 费用 / KOL 邀约 ─────────────────────────────────────────────────────────

/** 费用行。 */
export interface ExpenseVm {
  id: string;
  amount: number;
  category: string;
  description: string;
  paidBy: string;
  paymentMethod: string;
  reimbursementStatus: string;
  date: string;
  receipt?: boolean;
}

/** KOL 邀约行。 */
export interface InviteVm {
  id: string;
  kolId: string;
  status: string;
  days?: string;
  travel?: string;
}

/** 归一化后的 KOL 池记录。 */
export interface KolPoolEntry {
  id: string;
  name: string;
  handle: string;
  platform: string;
  followers: number;
}

// ── 配置常量项 ─────────────────────────────────────────────────────────────

/** 带 icon 的配置项(EVENT_TYPES / MATERIAL_CATEGORIES… 的 value)。 */
export interface IconConfig {
  label: string;
  icon: LucideIcon;
  color: string;
  side?: string;
}

/** 仅 label/color 的配置项(EVENT_STATUS / *_SOURCE / ITEM_STATUS…)。 */
export interface LabelConfig {
  label: string;
  color: string;
  side?: string;
}
