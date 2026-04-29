import { apiFetch, jsonBody } from "./http";
import type {
  AdminStats,
  AdminSubmission,
  AdminSubmissionsResponse,
  AdminRewardsResponse,
  RewardItem,
  SystemHealthSnapshot,
} from "../types/api";

export interface AdminRequestIssue {
  source: string;
  message: string;
}

export interface AdminSnapshotEnvelope<T> {
  data: T;
  issues?: AdminRequestIssue[];
}

export type AdminSnapshotPayload<T> = T | AdminSnapshotEnvelope<T>;

export function unwrapAdminSnapshotPayload<T>(payload: AdminSnapshotPayload<T>): T {
  if (typeof payload === "object" && payload !== null && "data" in payload) {
    return payload.data;
  }
  return payload;
}

/**
 * PATCH 2026-04-20: safePromiseAll
 *
 * Historically admin snapshot fetchers used Promise.all across ~8-10 parallel
 * API calls. Because Promise.all rejects on the FIRST rejection, a single 500
 * (e.g. scheduler didn't run so /api/admin/market/gaps returns empty shape)
 * blanked the entire Operations/Via/Analytics tab.
 *
 * safePromiseAll awaits all, returns the resolved values, and substitutes a
 * provided fallback for each rejected promise so the UI can keep rendering.
 */
async function safePromiseAll<T extends readonly unknown[]>(
  tasks: { [K in keyof T]: Promise<T[K]> },
  fallbacks: { [K in keyof T]: T[K] },
): Promise<T> {
  const settled = await Promise.allSettled(tasks as unknown as Promise<unknown>[]);
  return settled.map((r, i) => {
    if (r.status === "fulfilled") return r.value;
    // eslint-disable-next-line no-console
    console.warn(`[admin.service] parallel task ${i} failed, using fallback:`, r.reason);
    return (fallbacks as unknown as unknown[])[i];
  }) as unknown as T;
}

function normalizeAdminError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function settleAdminFetch<T>(
  source: string,
  task: Promise<T>,
  fallback: T,
): Promise<{ value: T; issue?: AdminRequestIssue }> {
  try {
    return { value: await task };
  } catch (error) {
    const message = normalizeAdminError(error);
    // eslint-disable-next-line no-console
    console.error(`[admin.service] ${source} failed:`, error);
    return {
      value: fallback,
      issue: { source, message },
    };
  }
}

function buildAdminSnapshot<T>(
  data: T,
  partials: Array<{ issue?: AdminRequestIssue }>,
): AdminSnapshotEnvelope<T> {
  const issues = partials
    .map((partial) => partial.issue)
    .filter((issue): issue is AdminRequestIssue => Boolean(issue));
  return issues.length ? { data, issues } : { data };
}

function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Array<Record<string, unknown>> : [];
}

function text(value: unknown, fallback = ""): string {
  const next = String(value ?? "").trim();
  return next || fallback;
}

function normalizeSignalTone(value: unknown): string {
  const tone = text(value, "info").toLowerCase();
  if (["critical", "high", "risk", "behind", "negative"].includes(tone)) return "high";
  if (["warning", "amber", "competitive", "mid"].includes(tone)) return "warning";
  if (["positive", "green", "lead", "leading", "opp", "opportunity"].includes(tone)) return "info";
  return tone;
}

function normalizeBrandAccount(row: Record<string, unknown>): Record<string, unknown> {
  const scanStatus = text(row.latest_scan_status || row.status, "not_scanned");
  const posts = Number(row.posts ?? row.posts_30d ?? 0);
  return {
    ...row,
    account: row.account || row.handle,
    region: row.region || row.market || row.name || scanStatus,
    posts_30d: posts,
    health: row.health || scanStatus,
    status: row.status || scanStatus,
    followers: Number(row.followers || 0),
  };
}

function normalizeMarketObservation(row: Record<string, unknown>): Record<string, unknown> {
  const title = text(row.title || row.event_title || row.summary || row.notes, "Untitled signal");
  const source = text(row.source || row.source_platform || row.source_url || row.region_code, "market");
  const severity = normalizeSignalTone(row.severity || row.impact || row.status);
  return {
    ...row,
    title,
    label: row.label || title,
    source,
    category: row.category || row.event_kind || row.subject_type || source,
    severity,
    created_at: row.created_at || row.observed_at || row.generated_at || row.updated_at,
  };
}

export interface AdminDashboardSnapshot {
  stats: AdminStats | null;
  submissions: AdminSubmission[];
  submissionsTotal: number;
  rewards: RewardItem[];
  health: SystemHealthSnapshot | null;
  vios: ViosDashboardSnapshot | null;
  leaderboardMonth: Array<Record<string, unknown>>;
}

export interface AdminUserRecord {
  id: number;
  email?: string;
  name?: string;
  creator_code?: string;
  status?: string;
  role?: string;
  points_balance?: number;
  points_total?: number;
  created_at?: string;
  last_login?: string;
  note?: string;
}

export interface AdminSocialAccountRecord {
  id: number;
  user_id?: number;
  platform?: string;
  handle?: string;
  verified?: number | boolean;
  user_name?: string;
  email?: string;
  verify_code?: string;
}

export interface AdminVerificationRecord {
  id: number;
  platform?: string;
  handle?: string;
  status?: string;
  user_name?: string;
  generated_comment?: string;
  comment_job_id?: string;
}

export interface AdminRedemptionRecord {
  id: number;
  user_id?: number;
  status?: string;
  item_name?: string;
  user_name?: string;
  email?: string;
  creator_code?: string;
  points_cost?: number;
  tracking_number?: string;
  admin_note?: string;
  created_at?: string;
}

export interface AdminOperationsSnapshot {
  users: AdminUserRecord[];
  socials: AdminSocialAccountRecord[];
  verifications: AdminVerificationRecord[];
  reviewQueue: AdminSubmission[];
  verifyQueue: AdminVerificationRecord[];
  verifyStats: Record<string, unknown> | null;
  redemptions: AdminRedemptionRecord[];
  creators: Array<Record<string, unknown>>;
  affiliate: Record<string, unknown> | null;
  pointsLog: Array<Record<string, unknown>>;
}

export interface StudentSchoolRecord {
  school_id: string;
  school_code?: string;
  school_name?: string;
  region?: string;
  country?: string;
  partnership_status?: string;
  activation_rate?: number;
  issued_count?: number;
  activated_count?: number;
  stats?: {
    issued?: number;
    bound?: number;
    active_students?: number;
  };
}

export interface StudentBatchProgressRecord {
  school_id?: string;
  school_name?: string;
  batch_name?: string;
  issued_count?: number;
  activated_count?: number;
  pending_count?: number;
  revoked_count?: number;
  issued?: number;
  activated?: number;
  pending?: number;
  revoked?: number;
  activation_rate?: number;
  last_activity_at?: string;
  last_issued_at?: string;
}

export interface StudentRosterRecord {
  user_id: number;
  school_id?: string;
  school_name?: string;
  student_id_code?: string;
  status?: string;
  creator_code?: string;
  email?: string;
  name?: string;
}

export interface StudentOverviewSnapshot {
  schools: StudentSchoolRecord[];
  batch_progress: StudentBatchProgressRecord[];
  students: StudentRosterRecord[];
  recent_events: Array<Record<string, unknown>>;
  recent_anomalies: Array<Record<string, unknown>>;
  recent_audit: Array<Record<string, unknown>>;
}

export interface StudentFunnelsSnapshot {
  batch_progress: StudentBatchProgressRecord[];
  school_funnels: Array<Record<string, unknown>>;
  recent_events: Array<Record<string, unknown>>;
  recent_anomalies: Array<Record<string, unknown>>;
}

export interface AdminStudentSnapshot {
  overview: StudentOverviewSnapshot | null;
  funnels: StudentFunnelsSnapshot | null;
  schools: StudentSchoolRecord[];
}

export interface ViaControlOverviewSnapshot {
  metrics?: Record<string, number>;
  recent_decisions?: Array<Record<string, unknown>>;
  recent_outcomes?: Array<Record<string, unknown>>;
  recent_reward_traces?: Array<Record<string, unknown>>;
  reward_trace_types?: Record<string, number>;
  providers?: Record<string, number>;
}

export interface ViaProposalRecord {
  proposal_key: string;
  policy_key?: string;
  status?: string;
  target?: string;
  created_at?: string;
  audit_actor?: string;
  candidate_config?: Record<string, unknown>;
}

export interface ViaPolicyVersionRecord {
  version_key: string;
  policy_key?: string;
  version_label?: string;
  status?: string;
  config?: Record<string, unknown>;
}

export interface AdminViaSnapshot {
  overview: ViaControlOverviewSnapshot | null;
  proposals: ViaProposalRecord[];
  livePolicies: ViaPolicyVersionRecord[];
  policyHistory: Array<Record<string, unknown>>;
  shadowReadiness: Array<Record<string, unknown>>;
  liveRolloutHealth: Array<Record<string, unknown>>;
  rolloutAlerts: Array<Record<string, unknown>>;
  retrievalEvidence: Array<Record<string, unknown>>;
  routingLearner: Array<Record<string, unknown>>;
  memoryRetention: Array<Record<string, unknown>>;
}

export interface AdminRuntimeSnapshot {
  runtime: Record<string, unknown> | null;
  cache: Record<string, unknown> | null;
  rateLimit: Record<string, unknown> | null;
  systemHealth: Record<string, unknown> | null;
}

export interface ViosDashboardProductRecord {
  series?: string;
  count?: number;
  views?: number;
  likes?: number;
  avg_score?: number;
}

export interface ViosDashboardCreatorRecord {
  handle?: string;
  platform?: string;
  submissions?: number;
  avg_creator?: number;
  total_views?: number;
  total_likes?: number;
  best_score?: number;
  status?: string;
}

export interface ViosDashboardTrendRecord {
  date?: string;
  count?: number;
  views?: number;
  likes?: number;
}

export interface ViosDashboardPlatformRecord {
  platform?: string;
  count?: number;
  views?: number;
  likes?: number;
  avg_creator?: number;
}

export interface ViosDashboardSnapshot {
  summary?: Record<string, unknown>;
  products?: ViosDashboardProductRecord[];
  platforms?: ViosDashboardPlatformRecord[];
  creators?: ViosDashboardCreatorRecord[];
  trend?: ViosDashboardTrendRecord[];
  recent?: Array<Record<string, unknown>>;
  generated_at?: string;
}

export interface AdminCreatorsSnapshot {
  roster: Array<Record<string, unknown>>;
  dashboard: ViosDashboardSnapshot | null;
  growth: Record<string, unknown> | null;
}

export interface AdminShopHero {
  id: string;
  title: string;
  subtitle?: string;
  imageUrl: string;
  targetUrl: string;
  badge?: string;
  source?: string;
  isActive?: boolean;
  sortOrder?: number;
}

export interface AdminShopHeroPayload {
  id?: string;
  user_id: number;
  title: string;
  subtitle?: string;
  imageUrl: string;
  targetUrl: string;
  badge?: string;
  isActive?: boolean;
  sortOrder?: number;
}

export interface AdminProductsSnapshot {
  dashboard: ViosDashboardSnapshot | null;
  catalog: Array<Record<string, unknown>>;
}

export interface AdminAnalyticsSnapshot {
  insights: Record<string, unknown> | null;
  benchmarks: Record<string, unknown> | null;
  leaderboardMonth: Array<Record<string, unknown>>;
  leaderboardYear: Array<Record<string, unknown>>;
  dashboard: ViosDashboardSnapshot | null;
  learningStats: Record<string, unknown> | null;
  corrections: Array<Record<string, unknown>>;
  pointsLog: Array<Record<string, unknown>>;
  trends: Record<string, Array<Record<string, unknown>>>;
  correlations: Array<Record<string, unknown>>;
  pipeline: Array<Record<string, unknown>>;
  pipelineSummary: Record<string, unknown> | null;
  rejectionReasons: Array<Record<string, unknown>>;
  seriesPerformance: Array<Record<string, unknown>>;
  cohorts: Array<Record<string, unknown>>;
  creatorRankings: Array<Record<string, unknown>>;
}

export interface AdminCommerceSnapshot {
  orders: Array<Record<string, unknown>>;
  ordersSummary: Record<string, unknown> | null;
  webhookEvents: Array<Record<string, unknown>>;
  webhookSummary: Record<string, unknown> | null;
  attributionOverview: Record<string, unknown> | null;
  attributionFunnel: Record<string, unknown> | null;
  attributionByCreator: Array<Record<string, unknown>>;
  attributionByPlatform: Array<Record<string, unknown>>;
  attributionUtmSources: Array<Record<string, unknown>>;
  payoutCycles: Array<Record<string, unknown>>;
  payoutCurrentCycle: Record<string, unknown> | null;
  payoutDisputes: Array<Record<string, unknown>>;
}

export interface AdminBrandSnapshot {
  matrix: Array<Record<string, unknown>>;
  posts: Array<Record<string, unknown>>;
  insights: Array<Record<string, unknown>>;
  voice: Record<string, unknown> | null;
}

export interface AdminMarketSnapshot {
  heatmap: Array<Record<string, unknown>>;
  observations: Array<Record<string, unknown>>;
  benchmarks: Array<Record<string, unknown>>;
  gaps: Array<Record<string, unknown>>;
}

export interface AdminSystemSnapshot {
  integrationsByCategory: Record<string, Array<Record<string, unknown>>>;
  integrationsSummary: Record<string, unknown> | null;
  trustUsers: Array<Record<string, unknown>>;
  trustEvents: Array<Record<string, unknown>>;
  trustRules: Record<string, unknown> | null;
  staffMembers: Array<Record<string, unknown>>;
  staffRoles: Array<Record<string, unknown>>;
  auditLog: Array<Record<string, unknown>>;
  apiTokens: Array<Record<string, unknown>>;
}

export interface KolOpsSnapshot {
  items: Array<Record<string, unknown>>;
  summary?: Record<string, unknown>;
  page?: Record<string, unknown>;
}

export interface KolCandidateSnapshot {
  items: Array<Record<string, unknown>>;
  page?: Record<string, unknown>;
}

export interface KolDetailSnapshot {
  kol: Record<string, unknown>;
  outreach: Array<Record<string, unknown>>;
  campaigns: Array<Record<string, unknown>>;
  content: Array<Record<string, unknown>>;
  attribution: Array<Record<string, unknown>>;
}

export interface KolDashboardSnapshot {
  items: Array<Record<string, unknown>>;
}

export interface SystemProvidersSnapshot {
  providers: Array<Record<string, unknown>>;
}

export interface SystemModelsSnapshot {
  available_models: Record<string, string[]>;
  task_model_binding: Record<string, string>;
  pricing_usd_per_1m_tokens: Record<string, { input: number; output: number }>;
}

export interface SystemUsageSnapshot {
  window_days: number;
  today: Record<string, unknown>;
  by_provider: Array<Record<string, unknown>>;
  by_task: Array<Record<string, unknown>>;
  daily: Array<Record<string, unknown>>;
  cost_basis: string;
}

export async function fetchAdminDashboard(token: string): Promise<AdminSnapshotPayload<AdminDashboardSnapshot>> {
  const [stats, submissionsResponse, rewardsResponse, health, vios, leaderboardMonth] = await Promise.all([
    settleAdminFetch("dashboard.stats", apiFetch<AdminStats>("/api/admin/stats", {}, token), {} as AdminStats),
    settleAdminFetch(
      "dashboard.submissions",
      apiFetch<AdminSubmissionsResponse>("/api/admin/submissions?limit=100", {}, token),
      { items: [], total: 0 } as AdminSubmissionsResponse,
    ),
    settleAdminFetch(
      "dashboard.rewards",
      apiFetch<AdminRewardsResponse>("/api/admin/rewards", {}, token),
      { items: [], rewards: [] } as AdminRewardsResponse,
    ),
    settleAdminFetch("dashboard.health", apiFetch<SystemHealthSnapshot>("/health", {}, token), {} as SystemHealthSnapshot),
    settleAdminFetch("dashboard.vios", apiFetch<ViosDashboardSnapshot>("/api/vios/dashboard", {}, token), {} as ViosDashboardSnapshot),
    settleAdminFetch(
      "dashboard.leaderboard_month",
      apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/leaderboard?period=month", {}, token),
      { items: [] },
    ),
  ]);

  return buildAdminSnapshot(
    {
      stats: stats.value,
      submissions: submissionsResponse.value.items || [],
      submissionsTotal: submissionsResponse.value.total || submissionsResponse.value.items?.length || 0,
      rewards: rewardsResponse.value.rewards || rewardsResponse.value.items || [],
      health: health.value,
      vios: vios.value,
      leaderboardMonth: leaderboardMonth.value.items || [],
    },
    [stats, submissionsResponse, rewardsResponse, health, vios, leaderboardMonth],
  );
}

export async function fetchAdminOperationsSnapshot(token: string): Promise<AdminOperationsSnapshot> {
  // PATCH 2026-04-20: each parallel fetch has a .catch() so one failure no longer blanks the tab.
  const [users, socials, verifications, reviewQueue, verifyQueue, verifyStats, redemptions, creators, affiliate, pointsLog] = await Promise.all([
    apiFetch<{ users?: AdminUserRecord[] }>("/api/admin/users", {}, token).catch(e => { console.warn("[admin.service] users failed:", e); return {} as { users?: AdminUserRecord[] }; }),
    apiFetch<{ accounts?: AdminSocialAccountRecord[] }>("/api/admin/social-accounts", {}, token).catch(e => { console.warn("[admin.service] social-accounts failed:", e); return {} as { accounts?: AdminSocialAccountRecord[] }; }),
    apiFetch<{ items?: AdminVerificationRecord[] }>("/api/admin/verifications", {}, token).catch(e => { console.warn("[admin.service] verifications failed:", e); return {} as { items?: AdminVerificationRecord[] }; }),
    apiFetch<AdminSubmissionsResponse>("/api/admin/submissions?limit=16", {}, token).catch(e => { console.warn("[admin.service] submissions failed:", e); return { items: [], total: 0, page: 1, limit: 16 } as AdminSubmissionsResponse; }),
    apiFetch<{ queue?: AdminVerificationRecord[] }>("/api/verify/queue", {}, token).catch(e => { console.warn("[admin.service] verify/queue failed:", e); return {} as { queue?: AdminVerificationRecord[] }; }),
    apiFetch<Record<string, unknown>>("/api/verify/admin/stats", {}, token).catch(e => { console.warn("[admin.service] verify/stats failed:", e); return {} as Record<string, unknown>; }),
    apiFetch<{ items?: AdminRedemptionRecord[] }>("/api/admin/redemptions", {}, token).catch(e => { console.warn("[admin.service] redemptions failed:", e); return {} as { items?: AdminRedemptionRecord[] }; }),
    apiFetch<{ creators?: Array<Record<string, unknown>> }>("/api/admin/creators", {}, token).catch(e => { console.warn("[admin.service] creators failed:", e); return {} as { creators?: Array<Record<string, unknown>> }; }),
    apiFetch<Record<string, unknown>>("/api/admin/affiliate", {}, token).catch(e => { console.warn("[admin.service] affiliate failed:", e); return {} as Record<string, unknown>; }),
    apiFetch<{ log?: Array<Record<string, unknown>> }>("/api/admin/points-log", {}, token).catch(e => { console.warn("[admin.service] points-log failed:", e); return {} as { log?: Array<Record<string, unknown>> }; }),
  ]);
  return {
    users: users.users || [],
    socials: socials.accounts || [],
    verifications: verifications.items || [],
    reviewQueue: reviewQueue.items || [],
    verifyQueue: verifyQueue.queue || [],
    verifyStats,
    redemptions: redemptions.items || [],
    creators: creators.creators || [],
    affiliate,
    pointsLog: pointsLog.log || [],
  };
}

export function runAdminUserAction(
  token: string,
  userId: number,
  action: "approve" | "reject" | "block" | "unblock",
  note = "",
) {
  const isModerationAction = action === "block" || action === "unblock";
  return apiFetch<Record<string, unknown>>(
    `/api/admin/users/${userId}/${action}`,
    {
      method: "POST",
      body: jsonBody(isModerationAction ? { reason: note } : { note }),
    },
    token,
  );
}

export function runAdminSocialAction(
  token: string,
  accountId: number,
  action: "verify" | "reject",
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/social-accounts/${accountId}/${action}`,
    {
      method: "POST",
    },
    token,
  );
}

export function runAdminVerificationAction(
  token: string,
  verificationId: number,
  action: "approve" | "reject",
  reason = "",
) {
  return apiFetch<Record<string, unknown>>(
    `/api/verify/${verificationId}/${action}`,
    {
      method: "POST",
      body: action === "reject" ? jsonBody({ reason }) : undefined,
    },
    token,
  );
}

export function updateAdminRedemption(
  token: string,
  redemptionId: number,
  payload: { status: string; tracking_number?: string; admin_note?: string },
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/redemptions/${redemptionId}/update`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function createAdminReward(
  token: string,
  payload: {
    title: string;
    description: string;
    category: string;
    points_cost: number;
    meta_label: string;
    image_url: string;
    stock: number;
    sort_order: number;
    status: string;
  },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/rewards",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function updateAdminReward(
  token: string,
  rewardId: number,
  payload: {
    title: string;
    description: string;
    category: string;
    points_cost: number;
    meta_label: string;
    image_url: string;
    stock: number;
    sort_order: number;
    status: string;
  },
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/rewards/${rewardId}`,
    {
      method: "PATCH",
      body: jsonBody(payload),
    },
    token,
  );
}

export function runAdminRewardAction(
  token: string,
  rewardId: number,
  action: "publish" | "archive" | "delete",
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/rewards/${rewardId}${action === "delete" ? "" : `/${action}`}`,
    {
      method: action === "delete" ? "DELETE" : "POST",
    },
    token,
  );
}

export function adjustAdminPoints(
  token: string,
  userId: number,
  payload: { delta: number; reason: string },
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/users/${userId}/adjust_points`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function grantAdminPoints(
  token: string,
  userId: number,
  payload: { points: number; reason: string },
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/users/${userId}/grant_points`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function correctAdminSubmissionProduct(
  token: string,
  submissionId: number,
  payload: { correct_series: string; correct_label: string; note: string },
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/submissions/${submissionId}/correct`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function createManualAdminSubmission(
  token: string,
  payload: {
    platform: string;
    extracted_handle: string;
    url: string;
    title: string;
    detection_status: string;
    product_series: string;
    product_label: string;
    final_score: number;
    creator_score: number;
    overall_score: number;
    views: number;
    likes: number;
    comments: number;
    shares: number;
    recommendation: string;
    memo: string;
  },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/submissions/manual",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function deleteAdminSubmission(token: string, submissionId: number) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/submissions/${submissionId}`,
    {
      method: "DELETE",
    },
    token,
  );
}

export function approveAdminSubmission(
  token: string,
  submissionId: number,
  payload: {
    campaign_score?: number;
    creator_score?: number;
    overall_score?: number;
    product_series?: string;
    product_label?: string;
    memo_append?: string;
  },
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/approve/${submissionId}`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function rejectAdminSubmission(token: string, submissionId: number, note: string) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/reject/${submissionId}`,
    {
      method: "POST",
      body: jsonBody({ note }),
    },
    token,
  );
}

export function reanalyzeAdminSubmission(token: string, submissionId: number) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/reanalyze/${submissionId}`,
    {
      method: "POST",
      body: jsonBody({}),
    },
    token,
  );
}

export async function fetchAdminStudentSnapshot(token: string): Promise<AdminStudentSnapshot> {
  const [overview, funnels, schools] = await Promise.all([
    apiFetch<StudentOverviewSnapshot>("/api/admin/intel/student/overview", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<StudentFunnelsSnapshot>("/api/admin/intel/student/funnels", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<{ items?: StudentSchoolRecord[] }>("/api/admin/intel/student/schools", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
  ]);
  return {
    overview,
    funnels,
    schools: schools.items || [],
  };
}

export function createStudentSchool(
  token: string,
  payload: {
    school_id: string;
    school_code: string;
    school_name: string;
    region?: string;
    country?: string;
    partnership_status?: string;
    primary_color?: string;
    accent_color?: string;
  },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/intel/student/schools",
    {
      method: "POST",
      body: jsonBody({
        school_id: payload.school_id,
        school_code: payload.school_code,
        school_name: payload.school_name,
        region: payload.region,
        country: payload.country,
        partnership_status: payload.partnership_status,
        visual_theme: {
          primary_color: payload.primary_color,
          accent_color: payload.accent_color,
        },
      }),
    },
    token,
  );
}

export function createStudentBatch(
  token: string,
  payload: {
    school_id: string;
    batch_name: string;
    count: number;
    roster_csv?: string;
  },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/intel/student/batches",
    {
      method: "POST",
      body: jsonBody({
        school_id: payload.school_id,
        batch_name: payload.batch_name,
        count: payload.count,
        roster_csv: payload.roster_csv || "",
        qr_only: true,
      }),
    },
    token,
  );
}

export function fetchStudentBatchDetail(
  token: string,
  schoolId: string,
  batchName: string,
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/intel/student/batches/detail?school_id=${encodeURIComponent(schoolId)}&batch_name=${encodeURIComponent(batchName)}&limit=240`,
    {},
    token,
  );
}

export function revokeStudentCard(token: string, qrId: string, reason: string) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/intel/student/cards/${encodeURIComponent(qrId)}/revoke`,
    {
      method: "POST",
      body: jsonBody({ reason }),
    },
    token,
  );
}

export function reissueStudentCard(token: string, qrId: string) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/intel/student/cards/${encodeURIComponent(qrId)}/reissue`,
    { method: "POST" },
    token,
  );
}

export async function fetchAdminViaSnapshot(token: string): Promise<AdminViaSnapshot> {
  const [overview, proposals, livePolicies, policyHistory, shadowReadiness, liveRolloutHealth, rolloutAlerts, retrievalEvidence, routingLearner, memoryRetention] =
    await Promise.all([
      apiFetch<ViaControlOverviewSnapshot>("/api/admin/intel/via/control-overview", {}, token),
      apiFetch<{ items?: ViaProposalRecord[] }>("/api/admin/intel/via/proposals", {}, token),
      apiFetch<{ items?: ViaPolicyVersionRecord[] }>("/api/admin/intel/via/policies/live", {}, token),
      apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/intel/via/policies/history", {}, token),
      apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/intel/via/policies/shadow-readiness", {}, token),
      apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/intel/via/policies/live-rollout-health", {}, token),
      apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/intel/via/policies/rollout-alerts", {}, token),
      apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/intel/via/retrieval-evidence", {}, token),
      apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/intel/via/routing-learner", {}, token),
      apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/intel/via/memory-retention", {}, token),
    ]);
  return {
    overview,
    proposals: proposals.items || [],
    livePolicies: livePolicies.items || [],
    policyHistory: policyHistory.items || [],
    shadowReadiness: shadowReadiness.items || [],
    liveRolloutHealth: liveRolloutHealth.items || [],
    rolloutAlerts: rolloutAlerts.items || [],
    retrievalEvidence: retrievalEvidence.items || [],
    routingLearner: routingLearner.items || [],
    memoryRetention: memoryRetention.items || [],
  };
}

export function runViaEvaluation(token: string) {
  return apiFetch<Record<string, unknown>>("/api/admin/intel/via/evaluate-now", { method: "POST" }, token);
}

export function runViaProposalAction(
  token: string,
  proposalKey: string,
  action: "approve" | "reject" | "apply" | "stage",
  note: string,
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/intel/via/proposals/${encodeURIComponent(proposalKey)}/${action}`,
    {
      method: "POST",
      body: jsonBody({ note }),
    },
    token,
  );
}

export function runViaPolicyAction(
  token: string,
  versionKey: string,
  action: "promote" | "rollback" | "advance-rollout",
  note: string,
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/intel/via/policies/${encodeURIComponent(versionKey)}/${action}`,
    {
      method: "POST",
      body: jsonBody({ note }),
    },
    token,
  );
}

export async function fetchAdminRuntimeSnapshot(token: string): Promise<AdminRuntimeSnapshot> {
  const [runtime, cache, rateLimit, systemHealth] = await Promise.all([
    apiFetch<Record<string, unknown>>("/api/admin/runtime/metrics", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/intel/system/cache", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/intel/system/rate-limit", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/intel/system/health", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
  ]);
  return { runtime, cache, rateLimit, systemHealth };
}

export function clearAdminSystemCache(token: string, prefix = "") {
  const query = prefix ? `?prefix=${encodeURIComponent(prefix)}` : "";
  return apiFetch<Record<string, unknown>>(
    `/api/admin/intel/system/cache/clear${query}`,
    { method: "POST" },
    token,
  );
}

export function clearRuntimeCacheTier(token: string, tier: string) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/runtime/cache/${encodeURIComponent(tier)}/clear`,
    { method: "POST" },
    token,
  );
}

export function runAdminIntegrationAction(
  token: string,
  integrationId: number,
  action: "enable" | "disable" | "test",
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/integrations/${integrationId}/${action}`,
    { method: "POST" },
    token,
  );
}

export function runAdminIntegrationHealth(token: string, integrationId: number) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/integrations/${integrationId}/health`,
    {},
    token,
  );
}

export function runAdminIntegrationHealthAll(token: string) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/integrations/health-check-all",
    { method: "POST" },
    token,
  );
}

export function updateAdminTrustRule(
  token: string,
  ruleId: number,
  payload: { delta?: number; description?: string; enabled?: boolean },
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/trust/rules/${ruleId}`,
    {
      method: "PUT",
      body: jsonBody(payload),
    },
    token,
  );
}

export function fetchTrustDistribution(token: string) {
  return apiFetch<Record<string, unknown>>("/api/admin/trust/distribution", {}, token);
}

export function fetchTrustUserDetail(token: string, userId: number) {
  return apiFetch<Record<string, unknown>>(`/api/admin/trust/users/${encodeURIComponent(String(userId))}`, {}, token);
}

export function updateTrustThresholds(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/trust/thresholds",
    {
      method: "PUT",
      body: jsonBody(payload),
    },
    token,
  );
}

export function runTrustUserAction(
  token: string,
  userId: number,
  action: "block" | "unblock" | "flag" | "clear-flag" | "adjust-score",
  payload: Record<string, unknown> = {},
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/users/${encodeURIComponent(String(userId))}/${action}`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function inviteAdminStaff(
  token: string,
  payload: { email: string; name?: string; role: string },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/staff",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function updateAdminStaff(
  token: string,
  staffId: number,
  payload: { role?: string; mfa_enabled?: boolean; permissions_override?: Record<string, unknown> },
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/staff/${staffId}`,
    {
      method: "PATCH",
      body: jsonBody(payload),
    },
    token,
  );
}

export function suspendAdminStaff(token: string, staffId: number, reason: string) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/staff/${staffId}/suspend`,
    {
      method: "POST",
      body: jsonBody({ reason }),
    },
    token,
  );
}

export function reactivateAdminStaff(token: string, staffId: number) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/staff/${staffId}/reactivate`,
    { method: "POST" },
    token,
  );
}

export function createAdminApiToken(
  token: string,
  payload: { name: string; scope: string; expires_days: number },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/staff/api-tokens",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function revokeAdminApiToken(token: string, tokenId: number) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/staff/api-tokens/${tokenId}`,
    { method: "DELETE" },
    token,
  );
}

export async function fetchAdminCreatorsSnapshot(token: string, handle = ""): Promise<AdminCreatorsSnapshot> {
  const [roster, dashboard, growth] = await Promise.all([
    apiFetch<{ creators?: Array<Record<string, unknown>> }>("/api/admin/creators", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<ViosDashboardSnapshot>("/api/vios/dashboard", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    handle
      ? apiFetch<Record<string, unknown>>(`/api/admin/creator/${encodeURIComponent(handle)}/growth`, {}, token)
      : Promise.resolve(null),
  ]);
  return {
    roster: roster.creators || [],
    dashboard,
    growth,
  };
}

export async function fetchAdminCreatorShopHeroes(token: string, userId: number): Promise<AdminShopHero[]> {
  const response = await apiFetch<{ shopHeroes?: AdminShopHero[] }>(
    `/api/admin/creator-public/shop-heroes?user_id=${encodeURIComponent(String(userId))}`,
    {},
    token,
  );
  return response.shopHeroes || [];
}

export async function saveAdminCreatorShopHero(token: string, payload: AdminShopHeroPayload): Promise<AdminShopHero> {
  const response = await apiFetch<{ shopHero?: AdminShopHero }>("/api/admin/creator-public/shop-heroes", {
    method: "POST",
    body: jsonBody(payload),
  }, token);
  if (!response.shopHero) {
    throw new Error("Shop hero save failed");
  }
  return response.shopHero;
}

export async function deleteAdminCreatorShopHero(token: string, heroId: string): Promise<void> {
  await apiFetch<Record<string, unknown>>(
    `/api/admin/creator-public/shop-heroes/${encodeURIComponent(heroId)}`,
    { method: "DELETE" },
    token,
  );
}

export async function fetchAdminProductsSnapshot(token: string): Promise<AdminSnapshotPayload<AdminProductsSnapshot>> {
  const [dashboard, catalog] = await Promise.all([
    settleAdminFetch("products.dashboard", apiFetch<ViosDashboardSnapshot>("/api/vios/dashboard", {}, token), {} as ViosDashboardSnapshot),
    settleAdminFetch(
      "products.catalog",
      apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/product_catalog", {}, token),
      { items: [] },
    ),
  ]);
  return buildAdminSnapshot(
    {
      dashboard: dashboard.value,
      catalog: catalog.value.items || [],
    },
    [dashboard, catalog],
  );
}

export async function fetchAdminAnalyticsSnapshot(token: string): Promise<AdminAnalyticsSnapshot> {
  const [
    insights,
    benchmarks,
    leaderboardMonth,
    leaderboardYear,
    dashboard,
    learningStats,
    corrections,
    pointsLog,
    trendSubmissions,
    trendGmv,
    trendScore,
    trendActiveCreators,
    correlations,
    pipeline,
    rejectionReasons,
    seriesPerformance,
    cohorts,
    creatorRankings,
  ] = await Promise.all([
    apiFetch<Record<string, unknown>>("/api/admin/insights", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/benchmarks", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/leaderboard?period=month", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/leaderboard?period=year", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<ViosDashboardSnapshot>("/api/vios/dashboard", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/learning/stats", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<{ items?: Array<Record<string, unknown>> }>("/api/admin/learning/corrections", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<{ log?: Array<Record<string, unknown>> }>("/api/admin/points-log", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/analytics/trend?metric=submissions&window_days=30", {}, token).catch(e => { console.warn("[admin.service] analytics trend failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/analytics/trend?metric=gmv&window_days=30", {}, token).catch(e => { console.warn("[admin.service] analytics trend failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/analytics/trend?metric=score&window_days=30", {}, token).catch(e => { console.warn("[admin.service] analytics trend failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/analytics/trend?metric=active_creators&window_days=30", {}, token).catch(e => { console.warn("[admin.service] analytics trend failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/analytics/correlations", {}, token).catch(e => { console.warn("[admin.service] analytics correlations failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/analytics/pipeline?window=30d", {}, token).catch(e => { console.warn("[admin.service] analytics pipeline failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/analytics/rejection-reasons", {}, token).catch(e => { console.warn("[admin.service] analytics rejection reasons failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/analytics/series-performance", {}, token).catch(e => { console.warn("[admin.service] analytics series failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/analytics/cohorts", {}, token).catch(e => { console.warn("[admin.service] analytics cohorts failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/analytics/creator-rankings?metric=combined&limit=50", {}, token).catch(e => { console.warn("[admin.service] analytics creators failed:", e); return {} as any; }),
  ]);
  return {
    insights,
    benchmarks,
    leaderboardMonth: leaderboardMonth.items || [],
    leaderboardYear: leaderboardYear.items || [],
    dashboard,
    learningStats,
    corrections: corrections.items || [],
    pointsLog: pointsLog.log || [],
    trends: {
      submissions: Array.isArray(trendSubmissions.series) ? (trendSubmissions.series as Array<Record<string, unknown>>) : [],
      gmv: Array.isArray(trendGmv.series) ? (trendGmv.series as Array<Record<string, unknown>>) : [],
      score: Array.isArray(trendScore.series) ? (trendScore.series as Array<Record<string, unknown>>) : [],
      active_creators: Array.isArray(trendActiveCreators.series) ? (trendActiveCreators.series as Array<Record<string, unknown>>) : [],
    },
    correlations: Array.isArray(correlations.correlations) ? (correlations.correlations as Array<Record<string, unknown>>) : [],
    pipeline: Array.isArray(pipeline.stages) ? (pipeline.stages as Array<Record<string, unknown>>) : [],
    pipelineSummary: pipeline,
    rejectionReasons: Array.isArray(rejectionReasons.reasons) ? (rejectionReasons.reasons as Array<Record<string, unknown>>) : [],
    seriesPerformance: Array.isArray(seriesPerformance.series) ? (seriesPerformance.series as Array<Record<string, unknown>>) : [],
    cohorts: Array.isArray(cohorts.cohorts) ? (cohorts.cohorts as Array<Record<string, unknown>>) : [],
    creatorRankings: Array.isArray(creatorRankings.creators) ? (creatorRankings.creators as Array<Record<string, unknown>>) : [],
  };
}

export function generateMarketGaps(token: string) {
  return apiFetch<Record<string, unknown>>(
    "/api/intelligence/market/gaps/generate",
    { method: "POST" },
    token,
  );
}

export function generateBrandInsights(token: string) {
  return apiFetch<Record<string, unknown>>(
    "/api/intelligence/brand/insights/generate",
    { method: "POST" },
    token,
  );
}

export async function fetchAdminCommerceSnapshot(token: string): Promise<AdminCommerceSnapshot> {
  const [orders, webhookEvents, attributionOverview, attributionFunnel, attributionByCreator, attributionByPlatform, attributionUtmSources, payoutCycles, payoutDisputes] =
    await Promise.all([
      apiFetch<Record<string, unknown>>("/api/admin/orders", {}, token),
      apiFetch<Record<string, unknown>>("/api/admin/webhook-events", {}, token),
      apiFetch<Record<string, unknown>>("/api/admin/attribution/overview", {}, token),
      apiFetch<Record<string, unknown>>("/api/admin/attribution/funnel", {}, token),
      apiFetch<Record<string, unknown>>("/api/admin/attribution/by-creator", {}, token),
      apiFetch<Record<string, unknown>>("/api/admin/attribution/by-platform", {}, token),
      apiFetch<Record<string, unknown>>("/api/admin/attribution/utm-sources", {}, token),
      apiFetch<Record<string, unknown>>("/api/admin/payouts/cycles", {}, token),
      apiFetch<Record<string, unknown>>("/api/admin/payouts/disputes", {}, token),
    ]);

  const cycles = Array.isArray(payoutCycles.cycles) ? (payoutCycles.cycles as Array<Record<string, unknown>>) : [];
  const currentCycleId = String(cycles[0]?.id || "").trim();
  const payoutCurrentCycle = currentCycleId
    ? await apiFetch<Record<string, unknown>>(`/api/admin/payouts/cycle/${encodeURIComponent(currentCycleId)}`, {}, token)
    : null;

  return {
    orders: Array.isArray(orders.orders) ? (orders.orders as Array<Record<string, unknown>>) : [],
    ordersSummary: orders,
    webhookEvents: Array.isArray(webhookEvents.events) ? (webhookEvents.events as Array<Record<string, unknown>>) : [],
    webhookSummary: webhookEvents,
    attributionOverview,
    attributionFunnel,
    attributionByCreator: Array.isArray(attributionByCreator.creators) ? (attributionByCreator.creators as Array<Record<string, unknown>>) : [],
    attributionByPlatform: Array.isArray(attributionByPlatform.platforms) ? (attributionByPlatform.platforms as Array<Record<string, unknown>>) : [],
    attributionUtmSources: Array.isArray(attributionUtmSources.utm_sources) ? (attributionUtmSources.utm_sources as Array<Record<string, unknown>>) : [],
    payoutCycles: cycles,
    payoutCurrentCycle,
    payoutDisputes: Array.isArray(payoutDisputes.disputes) ? (payoutDisputes.disputes as Array<Record<string, unknown>>) : [],
  };
}

export function backfillAdminOrders(token: string, limit = 500) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/orders/backfill",
    {
      method: "POST",
      body: jsonBody({ limit }),
    },
    token,
  );
}

export function fetchAdminOrderDetail(token: string, orderId: number) {
  return apiFetch<Record<string, unknown>>(`/api/admin/orders/${encodeURIComponent(String(orderId))}`, {}, token);
}

export function attributeAdminOrder(token: string, orderId: number, payload: { creator_handle: string; reason?: string }) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/orders/${encodeURIComponent(String(orderId))}/attribute`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function flagAdminOrder(token: string, orderId: number, reason: "fraud" | "bot" | "duplicate") {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/orders/${encodeURIComponent(String(orderId))}/flag`,
    {
      method: "POST",
      body: jsonBody({ reason }),
    },
    token,
  );
}

export function runPayoutCycleAction(token: string, cycleId: string, action: "approve-all" | "process") {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/payouts/cycle/${encodeURIComponent(cycleId)}/${action}`,
    { method: "POST" },
    token,
  );
}

export function runPayoutAction(
  token: string,
  payoutId: number,
  action: "approve" | "hold" | "release" | "adjust",
  payload: Record<string, unknown> = {},
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/payouts/${encodeURIComponent(String(payoutId))}/${action}`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function resolvePayoutDispute(token: string, disputeId: number, payload: { resolution: "uphold" | "overturn"; note?: string }) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/payouts/disputes/${encodeURIComponent(String(disputeId))}/resolve`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export async function fetchAdminBrandSnapshot(token: string): Promise<AdminBrandSnapshot> {
  const [matrix, posts, insights, voice] = await Promise.all([
    apiFetch<Record<string, unknown>>("/api/intelligence/brand/matrix", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/intelligence/brand/posts", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/intelligence/brand/insights", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/intelligence/brand/voice", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
  ]);
  return {
    matrix: records(matrix.accounts).map(normalizeBrandAccount),
    posts: records(posts.posts),
    insights: records(insights.insights),
    voice,
  };
}

export async function fetchAdminMarketSnapshot(token: string): Promise<AdminMarketSnapshot> {
  const [heatmap, observations, benchmarks, gaps] = await Promise.all([
    apiFetch<Record<string, unknown>>("/api/intelligence/market/heatmap", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/intelligence/market/observations", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/intelligence/market/benchmarks", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/intelligence/market/gaps", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
  ]);
  return {
    heatmap: records(heatmap.segments),
    observations: records(observations.observations).map(normalizeMarketObservation),
    benchmarks: records(benchmarks.genres),
    gaps: records(gaps.insights).map(normalizeMarketObservation),
  };
}

export async function fetchAdminSystemSnapshot(token: string): Promise<AdminSystemSnapshot> {
  const [integrations, trustUsers, trustEvents, trustRules, staff, staffRoles, auditLog, apiTokens] = await Promise.all([
    apiFetch<Record<string, unknown>>("/api/admin/integrations", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/trust/users", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/trust/events", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/trust/rules", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/staff", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/staff/roles", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/staff/audit-log", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
    apiFetch<Record<string, unknown>>("/api/admin/staff/api-tokens", {}, token).catch(e => { console.warn("[admin.service] fetch failed:", e); return {} as any; }),
  ]);
  return {
    integrationsByCategory:
      (integrations.integrations_by_category as Record<string, Array<Record<string, unknown>>>) || {},
    integrationsSummary: integrations,
    trustUsers: Array.isArray(trustUsers.users) ? (trustUsers.users as Array<Record<string, unknown>>) : [],
    trustEvents: Array.isArray(trustEvents.events) ? (trustEvents.events as Array<Record<string, unknown>>) : [],
    trustRules,
    staffMembers: Array.isArray(staff.members) ? (staff.members as Array<Record<string, unknown>>) : [],
    staffRoles: Array.isArray(staffRoles.roles) ? (staffRoles.roles as Array<Record<string, unknown>>) : [],
    auditLog: Array.isArray(auditLog.entries) ? (auditLog.entries as Array<Record<string, unknown>>) : [],
    apiTokens: Array.isArray(apiTokens.tokens) ? (apiTokens.tokens as Array<Record<string, unknown>>) : [],
  };
}

export function fetchSystemProviders(token: string): Promise<SystemProvidersSnapshot> {
  return apiFetch<SystemProvidersSnapshot>("/api/admin/system/providers", {}, token);
}

export function probeSystemProvider(token: string, provider: string, apiKey = "") {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/system/providers/${encodeURIComponent(provider)}/probe`,
    {
      method: "POST",
      body: jsonBody(apiKey ? { api_key: apiKey } : {}),
    },
    token,
  );
}

export function fetchSystemModels(token: string): Promise<SystemModelsSnapshot> {
  return apiFetch<SystemModelsSnapshot>("/api/admin/system/models", {}, token);
}

export function fetchSystemUsage(token: string, days = 7): Promise<SystemUsageSnapshot> {
  return apiFetch<SystemUsageSnapshot>(`/api/admin/system/usage?days=${encodeURIComponent(days)}`, {}, token);
}

export function requestSystemModelSwitch(
  token: string,
  payload: { task: string; model: string; confirm_password: string },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/system/models/switch",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function rotateSystemProviderKey(
  token: string,
  payload: { provider: string; new_key: string; confirm_password: string; move_current_to_previous?: boolean },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/system/keys/rotate",
    {
      method: "POST",
      body: jsonBody({
        move_current_to_previous: true,
        ...payload,
      }),
    },
    token,
  );
}

export function restartSystemRoles(
  token: string,
  payload: { roles: string[]; confirm_password: string },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/system/restart",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function inviteStaffMember(
  token: string,
  payload: { email: string; role: string; permissions: Record<string, string> },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/staff/invite",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function updateStaffPermissions(
  token: string,
  staffId: number,
  permissions: Record<string, string>,
) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/staff/${staffId}/permissions`,
    {
      method: "POST",
      body: jsonBody({ permissions }),
    },
    token,
  );
}

export function fetchKolOpsSnapshot(token: string, query = ""): Promise<KolOpsSnapshot> {
  return apiFetch<KolOpsSnapshot>(`/api/admin/kol/kols${query}`, {}, token);
}

export function searchKolPlatform(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/kol/search/platform",
    {
      method: "POST",
      timeoutMs: 300000,
      body: jsonBody(payload),
    },
    token,
  );
}

export function fetchKolCandidates(token: string, query = ""): Promise<KolCandidateSnapshot> {
  return apiFetch<KolCandidateSnapshot>(`/api/admin/kol/candidates${query}`, {}, token);
}

export function updateKolCandidate(token: string, candidateId: number, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/kol/candidates/${candidateId}`,
    {
      method: "PATCH",
      body: jsonBody(payload),
    },
    token,
  );
}

export function promoteKolCandidate(token: string, candidateId: number, payload: Record<string, unknown> = {}) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/kol/candidates/${candidateId}/promote`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function fetchKolDetail(token: string, kolId: number): Promise<KolDetailSnapshot> {
  return apiFetch<KolDetailSnapshot>(`/api/admin/kol/kols/${kolId}`, {}, token);
}

export function createKol(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/kol/kols",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function importKolCsv(token: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<Record<string, unknown>>(
    "/api/admin/kol/kols/import-csv",
    {
      method: "POST",
      body: form,
    },
    token,
  );
}

export function addKolOutreach(token: string, kolId: number, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/kol/kols/${kolId}/outreach`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function createKolCampaign(token: string, kolId: number, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/kol/kols/${kolId}/campaigns`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function createKolContent(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/kol/content",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export function scoreKolContent(token: string, contentId: number) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/kol/content/${contentId}/score`,
    {
      method: "POST",
    },
    token,
  );
}

export function fetchKolStaffPerformance(token: string): Promise<KolDashboardSnapshot> {
  return apiFetch<KolDashboardSnapshot>("/api/admin/kol/dashboard/staff-performance", {}, token);
}

export function fetchKolSuggestions(token: string, kolId: number) {
  return apiFetch<Record<string, unknown>>(
    `/api/admin/kol/kols/${kolId}/ai-suggestions`,
    {
      method: "POST",
    },
    token,
  );
}

export function scanIntelligenceAccount(
  token: string,
  payload: { platform: string; handle: string; max_posts?: number; sync?: boolean },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/intel/scan-account",
    {
      method: "POST",
      timeoutMs: 240000,
      body: jsonBody({ sync: true, max_posts: 100, ...payload }),
    },
    token,
  );
}

export function scanIntelligenceMatrix(
  token: string,
  payload: { accounts: Array<{ platform: string; handle: string; name?: string }>; max_posts_per_account?: number; sync?: boolean },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/intel/scan-matrix",
    {
      method: "POST",
      timeoutMs: 300000,
      body: jsonBody({ sync: true, max_posts_per_account: 50, ...payload }),
    },
    token,
  );
}

export function monitorLensMarket(
  token: string,
  payload: { query: string; max_videos?: number; platform?: string; market?: string; date_from?: string; date_to?: string },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/intel/monitor",
    {
      method: "POST",
      timeoutMs: 300000,
      body: jsonBody(payload),
    },
    token,
  );
}

export function compareLensMarket(
  token: string,
  payload: { lens_a: string; lens_b: string; max_videos?: number; platform?: string; market?: string; date_from?: string; date_to?: string },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/intel/compare",
    {
      method: "POST",
      timeoutMs: 300000,
      body: jsonBody(payload),
    },
    token,
  );
}

export function learnIntelligenceUrl(
  token: string,
  payload: { url: string; source_platform?: string; note?: string; region_code?: string },
) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/intel/learn/url",
    {
      method: "POST",
      timeoutMs: 60000,
      body: jsonBody(payload),
    },
    token,
  );
}

export function fetchDeepSightHealth(token: string) {
  return apiFetch<Record<string, unknown>>("/api/admin/deepsight/health", {}, token);
}

export function runDeepSightEvidencePack(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/deepsight/evidence-pack",
    {
      method: "POST",
      timeoutMs: 300000,
      body: jsonBody(payload),
    },
    token,
  );
}

export function runDeepSightDiagnose(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/deepsight/diagnose",
    {
      method: "POST",
      timeoutMs: 300000,
      body: jsonBody(payload),
    },
    token,
  );
}

export function scanDeepSightOfficialMatrix(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/deepsight/scan-official-matrix",
    {
      method: "POST",
      timeoutMs: 300000,
      body: jsonBody(payload),
    },
    token,
  );
}

export function fetchDeepSightCacheStats(token: string) {
  return apiFetch<Record<string, unknown>>("/api/admin/deepsight/cache/stats", {}, token);
}

export function clearDeepSightCache(token: string) {
  return apiFetch<Record<string, unknown>>(
    "/api/admin/deepsight/cache/clear",
    { method: "POST" },
    token,
  );
}
