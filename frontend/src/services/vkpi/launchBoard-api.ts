import { apiFetch, jsonBody } from "../http";

// 发射台 · 板块页范式改版 服务层(LaunchPadBoardPage 族唯一网络出口;闭环三动作
// 复用现成 publish-api.ts,不在此重写)。全部只读 GET,零 LLM 零写库:
//   GET /api/admin/vkpi/sku/list                        —— SKU 选择器(与 SKU 360° 同源)
//   GET /api/admin/vkpi/launch/assemble                 —— 新品一键全案六段(纯聚合)
//   GET /api/admin/vkpi/publish/pending                 —— 发布审批条目(vkpi_publish_approvals · 迁移173)
//   GET /api/admin/vkpi/product-analysis/launches       —— 发布计划(vkpi_product_launches)
//   GET /api/admin/vkpi/projects/content-posts          —— 内容排期/内容候选(vkpi_project_content_posts)
//   GET /api/admin/vkpi/projects/deliverable-stages-summary —— 履约阶段分布(vkpi_project_kol_assignments)
// 红线:纯读;不触 viltrox_fit_score / rule_v0;端点失败由调用方诚实展示,不在这层吞错。

export type Row = Record<string, any>;

export interface SkuListItem {
  sku: string;
  model_name: string;
  marketing_name: string;
  category_main: string;
  category_detail: string;
  series: string;
  mount: string;
  price_usd: number | null;
  status: string;
}

export function listSkus(token: string, query: string, limit = 30): Promise<{ items?: SkuListItem[] }> {
  return apiFetch<{ items?: SkuListItem[] }>(
    `/api/admin/vkpi/sku/list?query=${encodeURIComponent(query.trim())}&limit=${limit}`,
    { timeoutMs: 6000 },
    token,
  );
}

/** 一键全案(六段编排,候选打分 + 逐人聚合较重 → 90s 超时与旧页一致)。 */
export function assembleLaunchPlan(token: string, sku: string, maxRoster: number): Promise<Row> {
  return apiFetch<Row>(
    `/api/admin/vkpi/launch/assemble?sku=${encodeURIComponent(sku)}&max_roster=${maxRoster}`,
    { timeoutMs: 90000 },
    token,
  );
}

/** 发布审批条目(status=all 全态;表 0 行 = 已建未用如实;available=false = 迁移173未应用)。 */
export function getPublishApprovals(
  token: string,
  status: "pending" | "approved" | "scheduled" | "all" = "all",
  limit = 200,
): Promise<{ items?: Row[]; available?: boolean; count?: number; status_filter?: string; reason?: string }> {
  return apiFetch(`/api/admin/vkpi/publish/pending?status=${status}&limit=${limit}`, { timeoutMs: 15000 }, token);
}

/** 发布计划(vkpi_product_launches 未删行,按更新时间倒序)。 */
export function listProductLaunches(token: string, limit = 100): Promise<{ launches?: Row[] }> {
  return apiFetch(`/api/admin/vkpi/product-analysis/launches?limit=${limit}`, { timeoutMs: 15000 }, token);
}

/** 内容排期/候选(own-only 由后端 scope 强制;status=all 全态一次拉,client 分桶)。 */
export function listContentPosts(
  token: string,
  status = "all",
): Promise<{ status?: string; count?: number; items?: Row[]; note?: string }> {
  return apiFetch(`/api/admin/vkpi/projects/content-posts?status=${encodeURIComponent(status)}`, { timeoutMs: 15000 }, token);
}

/** 内容候选人工复核(唯一写:本帖行 status;matched 时后端回填观察窗口)。 */
export function reviewContentPost(
  token: string,
  postId: number,
  action: "matched" | "rejected" | "needs_review",
  note = "",
): Promise<{ status?: string; action?: string; error?: string; post?: Row }> {
  return apiFetch(
    `/api/admin/vkpi/projects/content-posts/${postId}`,
    { method: "PATCH", body: jsonBody({ action, note }), timeoutMs: 15000 },
    token,
  );
}

/** 履约阶段分布(assignment 归一词表读侧聚合)。 */
export function getStagesSummary(
  token: string,
): Promise<{ status?: string; total?: number; stages?: Array<{ stage: string; stage_status: string; n: number }> }> {
  return apiFetch(`/api/admin/vkpi/projects/deliverable-stages-summary`, { timeoutMs: 15000 }, token);
}

/* ============ 展示口径表(label 门面人话;真表名只进 SrcChip/溯源) ============ */

export const CONTENT_POST_STATUS: Record<string, { label: string; cls: string }> = {
  candidate: { label: "待人核", cls: "border-warn bg-warn-soft text-warn" },
  matched: { label: "已确认", cls: "border-good bg-good-soft text-good" },
  needs_review: { label: "需复核", cls: "border-info bg-info-soft text-info" },
  rejected: { label: "已剔除", cls: "border-line text-muted" },
  retrospective_ready: { label: "待复盘", cls: "border-accent bg-accent-soft text-accent" },
};

export const APPROVAL_STATUS: Record<string, { label: string; cls: string }> = {
  pending: { label: "待审批", cls: "border-warn bg-warn-soft text-warn" },
  approved: { label: "已通过", cls: "border-good bg-good-soft text-good" },
  scheduled: { label: "已重排", cls: "border-accent bg-accent-soft text-accent" },
};

export const LAUNCH_STATUS: Record<string, { label: string; cls: string }> = {
  active: { label: "进行中", cls: "border-good bg-good-soft text-good" },
  draft: { label: "草稿", cls: "border-line text-muted" },
  paused: { label: "已暂停", cls: "border-warn bg-warn-soft text-warn" },
  done: { label: "已完成", cls: "border-accent bg-accent-soft text-accent" },
  completed: { label: "已完成", cls: "border-accent bg-accent-soft text-accent" },
};

/** assignment 规范阶段词 → 门面人话(deliverable-stages-summary 已读侧归一)。 */
export const STAGE_LABELS: Record<string, string> = {
  discovered: "发现",
  contacted: "已联系",
  replied: "已回复",
  agreed: "已同意",
  device_sent: "已寄样",
  arrived: "已签收",
  content_posted: "已发内容",
  reviewed: "已复盘",
  closed: "已关闭",
  churned: "已流失",
  cancelled: "已取消",
  "(空)": "(空)",
};

export const CONFIDENCE_META: Record<string, { label: string; cls: string }> = {
  high: { label: "置信高", cls: "border-good bg-good-soft text-good" },
  medium: { label: "置信中", cls: "border-warn bg-warn-soft text-warn" },
  low: { label: "置信低", cls: "border-crit bg-crit-soft text-crit" },
};

/* ============ 成员合并:六段按 kol_pool_id 收拢成一人一档(详情弹窗连续翻的数据面) ============ */

export interface LaunchMember {
  kolPoolId: number;
  displayName: string;
  handle: string;
  platform: string;
  country: string;
  score: number | null;
  roster: Row; // ① 名单行(signature/focal_coverage 原样)
  budget: Row | null; // ② 预算行(estimate)
  schedule: Row | null; // ③ 排期行(publish_week/leadtime_basis)
  playbook: Row | null; // ④ 打法行(line/basis)
  forecast: Row | null; // ＋ 预测行(forecast)
}

const byKol = (items: unknown): Map<number, Row> => {
  const map = new Map<number, Row>();
  (Array.isArray(items) ? (items as Row[]) : []).forEach((item) => {
    const id = Number(item?.kol_pool_id);
    if (Number.isFinite(id)) map.set(id, item);
  });
  return map;
};

/** 全案六段 → 每人一档(顺序 = roster 名单原序;缺段 = null 如实)。 */
export function mergeLaunchMembers(plan: Row | null): LaunchMember[] {
  if (!plan) return [];
  const members: Row[] = Array.isArray(plan.roster?.members) ? plan.roster.members : [];
  const budgets = byKol(plan.budgets?.items);
  const schedules = byKol(plan.schedule?.items);
  const playbooks = byKol(plan.playbooks?.items);
  const forecasts = byKol(plan.forecast?.items);
  return members.map((m) => {
    const id = Number(m.kol_pool_id) || 0;
    return {
      kolPoolId: id,
      displayName: String(m.display_name || m.handle || `KOL ${id}`),
      handle: String(m.handle || ""),
      platform: String(m.platform || ""),
      country: String(m.country || ""),
      score: typeof m.score === "number" && Number.isFinite(m.score) ? m.score : null,
      roster: m,
      budget: budgets.get(id) || null,
      schedule: schedules.get(id) || null,
      playbook: playbooks.get(id) || null,
      forecast: forecasts.get(id) || null,
    };
  });
}

/* ============ 数字格式化(旧页 fmtNum/fmtUsd 原口径搬家) ============ */

export function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  const n = Number(v);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  return `$${Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}
