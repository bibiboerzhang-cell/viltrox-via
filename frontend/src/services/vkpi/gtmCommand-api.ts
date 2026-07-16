import { apiFetch, jsonBody } from "../http";

// GTM-1 · W3 · GTM Command 页专属 API 层。
//   读端点(W1/W2 并行在建,本层按规格合约先行,键缺失全兜底):
//     A. GET /api/admin/vkpi/market-brain/summary            —— 全局五卡
//     B. GET /api/admin/vkpi/market-brain/gtm-plan/preview   —— SKU 作战预览(public_plan 11 段)
//   另复用既有纯读端点 /api/admin/vkpi/sku/list 做顶部 SKU 选择器(与 SKU 360°/模拟器同源)。
//   写端点(GTM-Loop L1,本层唯一写路径):
//     C. POST /api/admin/vkpi/market-brain/gtm-plan/materialize —— bet 落 Action Inbox。
//        dry_run 默认 true(零写库,只出幂等对账);真落库必须显式 dry_run=false,
//        且落库的每条 bet requires_approval=True 走人审,前端不绕审批不自动执行。
//   【红线】零 LLM 零采集;绝不调用 marketing-brain/daily 与 market/trends 的 GET
//   (两者有隐藏写入,GTM-1 规格明令禁碰)。除 C 之外全为纯读 GET。
//   【显示层宪法】页面只吃 public_plan;本层对响应做深度剥私:private_evidence /
//   score_details / raw_* / competitor_notes / model_features / debug_trace / 黑名单类
//   字段即使后端多给也在进入组件树之前整体剔除,页面永远拿不到。

type Row = Record<string, any>;

export type GtmGoal = "exposure" | "conversion" | "content" | "channel";

// ---------------------------------------------------------------------------
// 深度剥私:显示层宪法的技术执行点。命中键名(含前缀规则)整棵子树丢弃。
// ---------------------------------------------------------------------------

const PRIVATE_KEYS = new Set([
  "private_evidence",
  "score_details",
  "score_detail",
  "score_breakdown",
  "raw",
  "raw_sources",
  "raw_source",
  "raw_comments",
  "raw_comment",
  "raw_notes",
  "raw_evidence",
  "competitor_notes",
  "competitor_note",
  "model_features",
  "debug_trace",
  "debug",
  "blacklist",
  "black_list",
  "blocklist",
]);

function isPrivateKey(key: string): boolean {
  const k = key.toLowerCase();
  if (PRIVATE_KEYS.has(k)) return true;
  if (k.startsWith("raw_")) return true;
  if (k.startsWith("private_") || k.startsWith("_private")) return true;
  if (k.startsWith("debug_")) return true;
  return false;
}

export function stripPrivateFields<T>(value: T): T {
  if (Array.isArray(value)) {
    return (value as unknown[]).map((v) => stripPrivateFields(v)) as unknown as T;
  }
  if (value && typeof value === "object") {
    const out: Row = {};
    for (const [k, v] of Object.entries(value as Row)) {
      if (isPrivateKey(k)) continue;
      out[k] = stripPrivateFields(v);
    }
    return out as unknown as T;
  }
  return value;
}

// ---------------------------------------------------------------------------
// 宽容取值小工具:后端任一键缺失/漂移形状都不许炸页面。
// ---------------------------------------------------------------------------

function asStr(v: unknown, fallback = ""): string {
  if (v === null || v === undefined) return fallback;
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return fallback;
}

function asNum(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return null;
}

function asRow(v: unknown): Row {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {};
}

function asRows(v: unknown): Row[] {
  if (Array.isArray(v)) return v.filter((x) => x && typeof x === "object") as Row[];
  return [];
}

function asStrArr(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v
    .map((x) => (typeof x === "string" ? x : asStr(asRow(x).text || asRow(x).note || asRow(x).statement)))
    .filter((s) => s.length > 0);
}

// items 容错:{items:[...]} 包壳或裸数组都吃。
function sectionItems(v: unknown): Row[] {
  if (Array.isArray(v)) return asRows(v);
  const row = asRow(v);
  return asRows(row.items);
}

// ---------------------------------------------------------------------------
// A. /market-brain/summary —— 全局五卡合约
// ---------------------------------------------------------------------------

export interface WeeklySignalItem {
  signal: string;
  kind: string;
  freshness: string;
  sample_size: number | null;
  confidence: string;
}

export interface ProductOpportunityItem {
  sku: string;
  market: string;
  persona: string;
  content_angle: string;
  opportunity_score: number | null;
  basis: string;
}

// 六要素行动行(summary.recommended_actions 与 preview.action_inbox_items 共用形状)。
export interface GtmActionItem {
  action: string;
  reason: string;
  evidence_summary: string;
  cost_note: string;
  risk: string;
  expected_gain: string;
  ref: string;
}

export interface LearningDigest {
  validated: string[];
  effective_styles: string[];
  dropped_channels: string[];
  next_change: string;
  honesty_note: string;
  status: string;
}

export interface MarketBrainSummary {
  status: string;
  reason: string;
  claim_status: string;
  organization_id: number | null;
  organization_scope_status: string;
  weekly_signals: { items: WeeklySignalItem[]; sources_note: string; status: string };
  product_opportunities: { items: ProductOpportunityItem[]; note: string; status: string };
  recommended_actions: { items: GtmActionItem[]; note: string; status: string };
  strategy_defaults: { sku_hint: string; budget_hint: number | null; note: string; status: string };
  learning_digest: LearningDigest;
  generated_at: string;
}

function normalizeActionItem(row: Row): GtmActionItem {
  return {
    action: asStr(row.action || row.title || row.suggestion),
    reason: asStr(row.reason || row.why),
    evidence_summary: asStr(row.evidence_summary || row.evidence),
    cost_note: asStr(row.cost_note || row.cost),
    risk: asStr(row.risk || row.risk_note),
    expected_gain: asStr(row.expected_gain || row.gain),
    ref: asStr(row.ref || row.ref_id || row.id),
  };
}

// 单段失败诚实 {status:"error"} 不拖垮整卡 —— status 一律透传给页面判空。
function sectionStatus(v: unknown): string {
  const row = asRow(v);
  return asStr(row.status || (Array.isArray(v) ? "" : ""));
}

export function normalizeMarketBrainSummary(raw: unknown): MarketBrainSummary {
  const data = stripPrivateFields(asRow(raw));
  const ws = asRow(data.weekly_signals);
  const po = asRow(data.product_opportunities);
  const ra = asRow(data.recommended_actions);
  const sd = asRow(data.strategy_defaults);
  const entry = asRow(sd.simulate_entry);
  const ld = asRow(data.learning_digest);
  return {
    status: asStr(data.status),
    reason: asStr(data.reason),
    claim_status: asStr(data.claim_status),
    organization_id: asNum(data.organization_id),
    organization_scope_status: asStr(data.organization_scope_status),
    weekly_signals: {
      items: sectionItems(data.weekly_signals).map((r) => ({
        signal: asStr(r.signal || r.statement || r.text),
        kind: asStr(r.kind || r.type),
        freshness: asStr(r.freshness),
        sample_size: asNum(r.sample_size),
        confidence: asStr(r.confidence),
      })),
      sources_note: asStr(ws.sources_note || ws.note),
      status: sectionStatus(data.weekly_signals),
    },
    product_opportunities: {
      items: sectionItems(data.product_opportunities).map((r) => ({
        sku: asStr(r.sku),
        market: asStr(r.market),
        persona: asStr(r.persona),
        content_angle: asStr(r.content_angle || r.angle),
        opportunity_score: asNum(r.opportunity_score),
        basis: asStr(r.basis),
      })),
      note: asStr(po.note),
      status: sectionStatus(data.product_opportunities),
    },
    recommended_actions: {
      items: sectionItems(data.recommended_actions).map(normalizeActionItem),
      note: asStr(ra.note),
      status: sectionStatus(data.recommended_actions),
    },
    strategy_defaults: {
      sku_hint: asStr(entry.sku_hint || sd.sku_hint),
      budget_hint: asNum(entry.budget_hint ?? sd.budget_hint),
      note: asStr(sd.note),
      status: sectionStatus(data.strategy_defaults),
    },
    learning_digest: {
      validated: asStrArr(ld.validated),
      effective_styles: asStrArr(ld.effective_styles),
      dropped_channels: asStrArr(ld.dropped_channels),
      next_change: asStr(ld.next_change),
      honesty_note: asStr(ld.honesty_note),
      status: sectionStatus(data.learning_digest),
    },
    generated_at: asStr(data.generated_at),
  };
}

// ---------------------------------------------------------------------------
// B. /market-brain/gtm-plan/preview —— public_plan 11 段合约
// ---------------------------------------------------------------------------

export interface GtmThesis {
  go_nogo: string;
  market: string;
  persona: string;
  mainline: string;
  confidence: string;
  basis_summary: string;
  status: string;
}

// 条件化预判(强制模板):预判 / 依据+置信 / 触发加码 / 撤退条件。缺条件也透传,页面标"条件缺失"。
export interface GtmForecastItem {
  horizon_days: number | null;
  statement: string;
  signals_summary: string;
  confidence: string;
  escalate_if: string;
  retreat_if: string;
}

export interface GtmRoadmapChannelPlay {
  channel: string;
  play: string;
}

export interface GtmRoadmapPhase {
  key: string;
  label: string;
  items: string[];
  channels: GtmRoadmapChannelPlay[];
  note: string;
}

// 通用段:kol_candidates / dealer_targets / official_channel_actions / shopify_indie_site_actions
// / content_angles / market_opportunity / budget_mix。形状由后端定,页面只点名白名单键渲染。
export interface GtmPlanSection {
  status: string;
  note: string;
  items: Row[];
  extra: Row;
}

export interface GtmSuccessMetric {
  metric: string;
  threshold: string;
  confidence: string;
  basis: string;
}

export interface GtmPublicPlan {
  thesis: GtmThesis;
  forecast: GtmForecastItem[];
  roadmap: GtmRoadmapPhase[];
  market_opportunity: GtmPlanSection;
  kol_candidates: GtmPlanSection;
  dealer_targets: GtmPlanSection;
  official_channel_actions: GtmPlanSection;
  shopify_indie_site_actions: GtmPlanSection;
  content_angles: GtmPlanSection;
  budget_mix: GtmPlanSection;
  risks: string[];
  data_gaps: string[];
  action_inbox_items: GtmActionItem[];
  success_metrics: GtmSuccessMetric[];
}

export interface GtmPlanMeta {
  generated_at: string;
  coverage: Row;
  data_gaps: string[];
}

export interface GtmPlanPreview {
  status: string;
  reason: string;
  claim_status: string;
  organization_id: number | null;
  organization_scope_status: string;
  writes: boolean;
  public_plan: GtmPublicPlan;
  meta: GtmPlanMeta;
}

function normalizeSection(v: unknown): GtmPlanSection {
  const row = asRow(v);
  const items = sectionItems(v);
  const extra: Row = {};
  for (const [k, val] of Object.entries(row)) {
    if (k === "items" || k === "status" || k === "note") continue;
    extra[k] = val;
  }
  return {
    status: asStr(row.status),
    note: asStr(row.note || row.reason),
    items,
    extra,
  };
}

const ROADMAP_PHASE_DEFS: Array<{ key: string; aliases: string[]; label: string }> = [
  { key: "w1", aliases: ["w1", "week1", "week_1"], label: "W1 · 首周点火" },
  { key: "w2_4", aliases: ["w2_4", "w2-4", "w2w4", "week2_4", "weeks_2_4"], label: "W2-4 · 放大验证" },
  { key: "m2_3", aliases: ["m2_3", "m2-3", "m2m3", "month2_3", "months_2_3"], label: "M2-3 · 沉淀续推" },
];

function normalizePhaseBody(key: string, label: string, v: unknown): GtmRoadmapPhase {
  const row = asRow(v);
  const rawItems = Array.isArray(v) ? v : row.items ?? row.actions;
  const items = asStrArr(rawItems).concat(
    asRows(rawItems)
      .map((r) => asStr(r.action || r.play || r.item))
      .filter((s) => s.length > 0),
  );
  const channels = asRows(row.channels).map((c) => ({
    channel: asStr(c.channel || c.name),
    play: asStr(c.play || c.action || c.note),
  }));
  return {
    key,
    label: asStr(row.label, label) || label,
    items: Array.from(new Set(items)),
    channels,
    note: asStr(row.note),
  };
}

// roadmap 容错:数组形([{phase,...}])与对象形({w1:…,w2_4:…,m2_3:…})都吃;缺失 → []。
export function normalizeRoadmap(v: unknown): GtmRoadmapPhase[] {
  if (Array.isArray(v)) {
    return asRows(v).map((r, i) => {
      const rawKey = asStr(r.phase || r.key, `phase_${i + 1}`).toLowerCase();
      const def = ROADMAP_PHASE_DEFS.find((d) => d.aliases.includes(rawKey));
      return normalizePhaseBody(def ? def.key : rawKey, def ? def.label : asStr(r.label, rawKey), r);
    });
  }
  const row = asRow(v);
  const phases: GtmRoadmapPhase[] = [];
  for (const def of ROADMAP_PHASE_DEFS) {
    const hit = def.aliases.find((a) => row[a] !== undefined);
    if (hit !== undefined) phases.push(normalizePhaseBody(def.key, def.label, row[hit]));
  }
  return phases;
}

function asGapArr(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v
    .map((x) =>
      typeof x === "string" ? x : asStr(asRow(x).gap || asRow(x).note || asRow(x).name || asRow(x).text),
    )
    .filter((s) => s.length > 0);
}

export function normalizeGtmPlanPreview(raw: unknown): GtmPlanPreview {
  // 先整树剥私(private_evidence 本就不该出现在此端点,防御性再剥一遍),再点名归一。
  const data = stripPrivateFields(asRow(raw));
  const plan = asRow(data.public_plan);
  const thesisRow = asRow(plan.thesis);
  const meta = asRow(data.meta);
  return {
    status: asStr(data.status),
    reason: asStr(data.reason),
    claim_status: asStr(data.claim_status),
    organization_id: asNum(data.organization_id),
    organization_scope_status: asStr(data.organization_scope_status),
    writes: data.writes === true,
    public_plan: {
      thesis: {
        go_nogo: asStr(thesisRow.go_nogo),
        market: asStr(thesisRow.market),
        persona: asStr(thesisRow.persona),
        mainline: asStr(thesisRow.mainline),
        confidence: asStr(thesisRow.confidence),
        basis_summary: asStr(thesisRow.basis_summary || thesisRow.decision_basis_summary),
        status: asStr(thesisRow.status),
      },
      forecast: sectionItems(plan.forecast).map((r) => ({
        horizon_days: asNum(r.horizon_days),
        statement: asStr(r.statement),
        signals_summary: asStr(r.signals_summary),
        confidence: asStr(r.confidence),
        escalate_if: asStr(r.escalate_if),
        retreat_if: asStr(r.retreat_if),
      })),
      roadmap: normalizeRoadmap(plan.roadmap),
      market_opportunity: normalizeSection(plan.market_opportunity),
      kol_candidates: normalizeSection(plan.kol_candidates),
      dealer_targets: normalizeSection(plan.dealer_targets),
      official_channel_actions: normalizeSection(plan.official_channel_actions),
      shopify_indie_site_actions: normalizeSection(plan.shopify_indie_site_actions),
      content_angles: normalizeSection(plan.content_angles),
      budget_mix: normalizeSection(plan.budget_mix),
      risks: asGapArr(plan.risks),
      data_gaps: asGapArr(plan.data_gaps),
      action_inbox_items: sectionItems(plan.action_inbox_items).map(normalizeActionItem),
      success_metrics: sectionItems(plan.success_metrics).map((r) => ({
        metric: asStr(r.metric || r.name || r.stage),
        threshold: asStr(r.threshold ?? r.target),
        confidence: asStr(r.confidence),
        basis: asStr(r.basis),
      })),
    },
    meta: {
      generated_at: asStr(meta.generated_at),
      coverage: asRow(meta.coverage),
      data_gaps: asGapArr(meta.data_gaps),
    },
  };
}

// ---------------------------------------------------------------------------
// 请求函数
// ---------------------------------------------------------------------------

export interface SkuListItem {
  sku: string;
  model_name: string;
  marketing_name: string;
  category_main: string;
  mount: string;
  price_usd: number | null;
}

export async function listSkuOptions(token: string, query: string, limit = 30): Promise<SkuListItem[]> {
  const res = await apiFetch<{ items?: Row[] }>(
    `/api/admin/vkpi/sku/list?query=${encodeURIComponent(query.trim())}&limit=${encodeURIComponent(String(limit))}`,
    { timeoutMs: 6000 },
    token,
  );
  return asRows(res?.items).map((r) => ({
    sku: asStr(r.sku),
    model_name: asStr(r.model_name),
    marketing_name: asStr(r.marketing_name),
    category_main: asStr(r.category_main),
    mount: asStr(r.mount),
    price_usd: asNum(r.price_usd),
  }));
}

export async function getMarketBrainSummary(token: string): Promise<MarketBrainSummary> {
  const res = await apiFetch<Row>("/api/admin/vkpi/market-brain/summary", { timeoutMs: 15000 }, token);
  return normalizeMarketBrainSummary(res);
}

export interface GtmPlanPreviewParams {
  sku: string;
  country?: string;
  budgetUsd?: number;
  goal?: GtmGoal;
  windowDays?: number;
}

export async function getGtmPlanPreview(token: string, opts: GtmPlanPreviewParams): Promise<GtmPlanPreview> {
  const params = new URLSearchParams({ sku: opts.sku });
  if (opts.country && opts.country.trim()) params.set("country", opts.country.trim().toUpperCase());
  params.set("budget_usd", String(opts.budgetUsd && opts.budgetUsd > 0 ? opts.budgetUsd : 3000));
  if (opts.goal) params.set("goal", opts.goal);
  params.set("window_days", String(opts.windowDays && opts.windowDays > 0 ? opts.windowDays : 30));
  const res = await apiFetch<Row>(
    `/api/admin/vkpi/market-brain/gtm-plan/preview?${params.toString()}`,
    { timeoutMs: 60000 },
    token,
  );
  return normalizeGtmPlanPreview(res);
}

// ---------------------------------------------------------------------------
// C. /market-brain/gtm-plan/materialize —— bet 落库(GTM-Loop L1,本层唯一写端点)
//    dry_run=true:零写库,只回 bet 预览 + 幂等对账(would_insert / already_present);
//    dry_run=false:经 persist_suggestions 落 vkpi_action_inbox,逐条 requires_approval。
// ---------------------------------------------------------------------------

export interface GtmMaterializeBetPreview {
  dedupe_key: string;
  title: string;
  requires_approval: boolean;
}

export interface GtmMaterializeResult {
  ok: boolean;
  status: string;
  reason: string;
  dry_run: boolean;
  gtm_plan_id: string;
  sku: string;
  goal: string;
  bets_total: number;
  already_present: number;
  // dry-run 才有 would_insert;真落库才有 persisted / inserted_new(缺失 → null,页面判空)。
  would_insert: number | null;
  persisted: number | null;
  inserted_new: number | null;
  skipped_incomplete: number;
  requires_approval_all: boolean;
  note: string;
  bets: GtmMaterializeBetPreview[];
}

export function normalizeGtmMaterializeResult(raw: unknown): GtmMaterializeResult {
  const data = stripPrivateFields(asRow(raw));
  return {
    // 后端内部异常回 {status:"error",reason}(无 ok 键)→ ok 按严格 true 判,页面走错误提示。
    ok: data.ok === true,
    status: asStr(data.status),
    reason: asStr(data.reason),
    // 缺失时按 dry_run 处理(保守:绝不把未知响应误报成"已落库")。
    dry_run: data.dry_run !== false,
    gtm_plan_id: asStr(data.gtm_plan_id),
    sku: asStr(data.sku),
    goal: asStr(data.goal),
    bets_total: asNum(data.bets_total) ?? 0,
    already_present: asNum(data.already_present) ?? 0,
    would_insert: asNum(data.would_insert),
    persisted: asNum(data.persisted),
    inserted_new: asNum(data.inserted_new),
    skipped_incomplete: Array.isArray(data.skipped_incomplete) ? data.skipped_incomplete.length : 0,
    requires_approval_all: data.requires_approval_all === true,
    note: asStr(data.note),
    bets: sectionItems(data.bets).map((r) => ({
      dedupe_key: asStr(r.dedupe_key),
      title: asStr(r.title),
      requires_approval: r.requires_approval === true,
    })),
  };
}

export async function materializeGtmPlan(
  token: string,
  opts: GtmPlanPreviewParams,
  dryRun = true,
): Promise<GtmMaterializeResult> {
  const budget = opts.budgetUsd && opts.budgetUsd > 0 ? opts.budgetUsd : 3000;
  const body: Row = {
    sku: opts.sku,
    budget_usd: budget,
    goal: opts.goal || "exposure",
    window_days: opts.windowDays && opts.windowDays > 0 ? opts.windowDays : 30,
    // 人审红线同向:默认 dry-run 零写库,真落库必须显式传 false。
    dry_run: dryRun !== false,
  };
  if (opts.country && opts.country.trim()) body.country = opts.country.trim().toUpperCase();
  const res = await apiFetch<Row>(
    "/api/admin/vkpi/market-brain/gtm-plan/materialize",
    { method: "POST", body: jsonBody(body), timeoutMs: 60000 },
    token,
  );
  return normalizeGtmMaterializeResult(res);
}
