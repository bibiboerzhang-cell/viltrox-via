import { apiFetch, jsonBody } from "../http";

// 内容墙「去查最新内容」三端点封装(单独成文件:myKolBoard-api.ts 已 840 行)。
//   GET  /my-kol/wall-fetch/plan    报价——这次要去几个账号取内容,纯读零副作用
//   POST /my-kol/wall-fetch         派活——唯一花钱的一步,必须带报价指纹
//   GET  /my-kol/wall-fetch/status  回读——派出去的活到哪一步了,纯读零副作用
// 红线:前端**不做二次计算**。确认框上的每个数字都原样来自服务端报价,
// 并把 plan_hash + expected_count 回传;服务端重算对不上就 409,让人重看一眼。

export type WallFetchExactness = "date_pushdown" | "recent_only";

export type WallFetchPlanItem = {
  kol_pool_id: number;
  name: string;
  platform: string;
  window_exactness: WallFetchExactness;
};

export type WallFetchSkipItem = {
  kol_pool_id: number;
  name: string;
  platform: string;
  reason: string;
};

export type WallFetchSkipped = {
  shared_readonly: WallFetchSkipItem[];
  recently_fetched: WallFetchSkipItem[];
  per_click_cap: WallFetchSkipItem[];
  daily_cap: WallFetchSkipItem[];
};

export type WallFetchPlan = {
  status: string;
  days: number;
  window_label: string;
  kol_pool_id: number | null;
  scope: string;
  scope_label: string;
  planned_count: number;
  planned: WallFetchPlanItem[];
  /** 报价的计量单位=真实的平台取数次数(YouTube 一个账号 2 次),不是「一个账号一次」。 */
  fetch_calls: {
    total: number;
    max_total: number;
    by_platform: Record<string, { accounts: number; per_account: number; per_account_max: number; calls: number }>;
  };
  posts_per_account: number;
  followups_suppressed: boolean;
  requires_confirmation: boolean;
  skipped: WallFetchSkipped;
  skipped_counts: Record<string, number>;
  candidates_total: number;
  candidates_truncated: boolean;
  window: {
    since: string;
    max_posts: number;
    exactness_counts: Partial<Record<WallFetchExactness, number>>;
    exactness_labels: Record<string, string>;
  };
  limits: {
    per_click: number;
    daily: number;
    daily_used: number;
    daily_left: number;
    cooldown_hours: number;
  };
  budget: { configured: boolean; usage_ratio: number | null; hard_stopped: boolean };
  plan_hash: string;
};

export type WallFetchDispatchItem = WallFetchPlanItem & { job_id?: number | null; reason?: string };

export type WallFetchResult = {
  status: "dispatched" | "nothing_to_fetch" | string;
  plan: WallFetchPlan;
  queued: WallFetchDispatchItem[];
  already_queued: WallFetchDispatchItem[];
  failed: WallFetchDispatchItem[];
  counts?: { planned: number; queued: number; already_queued: number; failed: number };
};

const EMPTY_SKIPPED: WallFetchSkipped = {
  shared_readonly: [],
  recently_fetched: [],
  per_click_cap: [],
  daily_cap: [],
};

function normalizeSkipped(raw: unknown): WallFetchSkipped {
  const source = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const pick = (key: keyof WallFetchSkipped): WallFetchSkipItem[] =>
    Array.isArray(source[key]) ? (source[key] as WallFetchSkipItem[]) : [];
  return {
    shared_readonly: pick("shared_readonly"),
    recently_fetched: pick("recently_fetched"),
    per_click_cap: pick("per_click_cap"),
    daily_cap: pick("daily_cap"),
  };
}

function normalizeFetchCalls(raw: unknown): WallFetchPlan["fetch_calls"] {
  const source = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const total = Math.max(0, Number(source.total) || 0);
  return {
    total,
    // 上限缺席时退回 total,绝不凭空补一个更小的数字。
    max_total: Math.max(total, Number(source.max_total) || 0),
    by_platform: (source.by_platform && typeof source.by_platform === "object"
      ? source.by_platform
      : {}) as WallFetchPlan["fetch_calls"]["by_platform"],
  };
}

function normalizePlan(raw: WallFetchPlan): WallFetchPlan {
  return {
    ...raw,
    planned: Array.isArray(raw?.planned) ? raw.planned : [],
    planned_count: Math.max(0, Number(raw?.planned_count) || 0),
    fetch_calls: normalizeFetchCalls(raw?.fetch_calls),
    skipped: normalizeSkipped(raw?.skipped),
    skipped_counts: (raw?.skipped_counts && typeof raw.skipped_counts === "object" ? raw.skipped_counts : {}),
  };
}

/** 报价(纯读,可安全反复调用):这次要去几个账号取内容、几个刚取过跳过、额度还剩几成。 */
export async function getWallFetchPlan(
  token: string,
  params: { days?: number; kolPoolId?: number; staffId?: number } = {},
): Promise<WallFetchPlan> {
  const query = new URLSearchParams({ days: String(Math.max(0, Number(params.days) || 0)) });
  if (params.kolPoolId) query.set("kol_pool_id", String(params.kolPoolId));
  if (params.staffId) query.set("staff_id", String(params.staffId));
  const response = await apiFetch<WallFetchPlan>(
    `/api/admin/vkpi/my-kol/wall-fetch/plan?${query.toString()}`,
    {},
    token,
  );
  return normalizePlan(response);
}

export type WallFetchOutcomeState = "waiting" | "landed" | "stopped";

export type WallFetchOutcomeItem = {
  job_id: number;
  kol_pool_id: number | null;
  state: WallFetchOutcomeState;
  /** 没取到时的一句人话;机器码永远不会出现在这里。 */
  reason_human: string | null;
};

export type WallFetchOutcome = {
  status: string;
  items: WallFetchOutcomeItem[];
  counts: { waiting: number; landed: number; stopped: number; unknown: number };
  /** 服务端读不到的派单号:读不到就是读不到,不许当成已完成。 */
  unknown_job_ids: number[];
};

/** 回读派出去的活到哪一步了(纯读)。调用方必须自己限定次数——这里不做任何重试与轮询。 */
export async function fetchWallFetchOutcome(
  token: string,
  params: { jobIds: number[]; staffId?: number; signal?: AbortSignal },
): Promise<WallFetchOutcome> {
  const ids = (params.jobIds || []).filter((id) => Number.isFinite(id) && id > 0);
  const query = new URLSearchParams({ job_ids: ids.join(",") });
  if (params.staffId) query.set("staff_id", String(params.staffId));
  const response = await apiFetch<Partial<WallFetchOutcome>>(
    `/api/admin/vkpi/my-kol/wall-fetch/status?${query.toString()}`,
    { signal: params.signal },
    token,
  );
  const counts = (response?.counts || {}) as Partial<WallFetchOutcome["counts"]>;
  return {
    status: String(response?.status || "ok"),
    items: Array.isArray(response?.items) ? (response.items as WallFetchOutcomeItem[]) : [],
    counts: {
      waiting: Number(counts.waiting) || 0,
      landed: Number(counts.landed) || 0,
      stopped: Number(counts.stopped) || 0,
      unknown: Number(counts.unknown) || 0,
    },
    unknown_job_ids: Array.isArray(response?.unknown_job_ids) ? (response.unknown_job_ids as number[]) : [],
  };
}

/** 派活(唯一花钱的一步)。plan_hash / expected_count 原样回传服务端报价,绝不前端重算。 */
export async function startWallFetch(
  token: string,
  params: { days: number; kolPoolId?: number; planHash: string; expectedCount: number; staffId?: number },
): Promise<WallFetchResult> {
  const query = new URLSearchParams();
  if (params.staffId) query.set("staff_id", String(params.staffId));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const response = await apiFetch<WallFetchResult>(
    `/api/admin/vkpi/my-kol/wall-fetch${suffix}`,
    {
      method: "POST",
      body: jsonBody({
        days: Math.max(0, Number(params.days) || 0),
        kol_pool_id: params.kolPoolId || 0,
        plan_hash: params.planHash,
        expected_count: params.expectedCount,
      }),
    },
    token,
  );
  return {
    ...response,
    queued: Array.isArray(response?.queued) ? response.queued : [],
    already_queued: Array.isArray(response?.already_queued) ? response.already_queued : [],
    failed: Array.isArray(response?.failed) ? response.failed : [],
  };
}
