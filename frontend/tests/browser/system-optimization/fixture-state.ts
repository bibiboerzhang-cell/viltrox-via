import type { ActionInboxItem, ActionInboxResponse, ActionLedgerItem, ActionReconcileRequest } from "../../../src/services/vkpi/actionInbox-api";
import type { MarketBrainSummary } from "../../../src/services/vkpi/gtmCommand-api";

export const FIXTURE_TOKEN = "SYNTHETIC_FIXTURE_ONLY_NOT_A_REAL_TOKEN";
export const SCENARIOS = {
  source_failure: { title: "来源全部失败", instruction: "本周信号应为待核验而非 0；信号区明确读取失败，不推断市场无需求。" },
  source_partial: { title: "仅部分来源结果", instruction: "保留合成信号并显示来源不完整；本周信号不能冒充完整计数。" },
  source_empty: { title: "完整读取后为空（合成真空态）", instruction: "三个来源均读完，窗口内无信号：可显示 0，但不能解释为真实市场没有需求。" },
  suggested: { title: "建议待批 → 合成审批", instruction: "点击“通过”只模拟批准，必须仍显示尚未执行；不会自动调用执行。" },
  approved: { title: "批准但尚未执行", instruction: "初始是合成 approved；观察当前阶段与下一步，不点击时不能新增执行记录。" },
  queued: { title: "点击后模拟入队", instruction: "点击真实组件的“执行”并确认合成模拟：仅返回任务提交回执，不得显示业务完成或真实零费用。" },
  unknown: { title: "点击后模拟响应丢失", instruction: "点击“执行”并确认：本地假实现抛出响应丢失；刷新仍保留未知，不出现重复执行按钮。" },
  read_failure: { title: "读取失败后暂停操作", instruction: "先打开“人工对账”并填原因和证据，再点下方“模拟读取失败”，最后点击组件刷新；提交对账应暂停。" },
  plan_gap: { title: "计划缺证据", instruction: "只读展示合成 planning_readiness：先补证据，不表示项目已创建、批准或执行。" },
  plan_review: { title: "规划草案结构化审阅", instruction: "全宽展示完整合成规划：核对分配合计、未分配额和模型费用未知；只是待人工审阅的草案，没有执行按钮。" },
} as const;
export type Scenario = keyof typeof SCENARIOS;
type State = {
  scenario: Scenario; epoch: number; items: ActionInboxItem[]; ledger: ActionLedgerItem[];
  readsFail: boolean; approved: number; executed: number; events: string[]; denied: number;
};
const listeners = new Set<() => void>();
let state: State;
const stamp = "2026-09-05T00:00:00Z";

function seedItem(status: string): ActionInboxItem {
  return {
    id: 900001, dedupe_key: "synthetic-fixture-900001", category: "kol_profile",
    title: "【合成】资料补齐任务，不关联真实达人", detail: "仅用于检查审批、执行和回执展示；不读取或写入业务系统。",
    priority: "high", entity_type: "synthetic", entity_id: "fixture-entity",
    suggested_endpoint: "fixture-only", estimated_cost_cents: 25, writes_business_data: true,
    uses_llm: false, requires_approval: true, owner_staff_id: null,
    reason: "合成：验证状态与下一步的表述", payload_json: {}, status,
    expected_gain: "仅验证 UI 合同，不代表营销收益", risk_level: "low",
    evidence_refs_json: [{ source: "synthetic_fixture", reference: "fixture-only-evidence" }],
    verification_plan_json: ["合成：检查回执，不访问供应商或业务数据库"],
    created_at: stamp, updated_at: stamp,
    ...(status === "executing" ? { manual_reconciliation_required: true } : {}),
  };
}

function publish(patch: Partial<State>) {
  state = { ...state, ...patch };
  listeners.forEach((notify) => notify());
}
export function resetScenario(scenario: Scenario) {
  const hasAction = ["suggested", "approved", "queued", "unknown", "read_failure"].includes(scenario);
  const status = scenario === "suggested" ? "suggested" : scenario === "read_failure" ? "executing" : "approved";
  state = { scenario, epoch: (state?.epoch ?? 0) + 1, items: hasAction ? [seedItem(status)] : [],
    ledger: [], readsFail: false, approved: hasAction && status !== "suggested" ? 1 : 0,
    executed: 0, events: [], denied: 0 };
  listeners.forEach((notify) => notify());
}
resetScenario("source_failure");
export const subscribe = (notify: () => void) => { listeners.add(notify); return () => { listeners.delete(notify); }; };
export const getSnapshot = () => state;
export function deny(operation: string): never {
  publish({ denied: state.denied + 1, events: [...state.events, `拒绝：${operation}`].slice(-40) });
  throw new Error(`Synthetic fixture denied ${operation}`);
}
function call(operation: string, token: string) {
  if (token !== FIXTURE_TOKEN) return deny("unexpected token");
  publish({ events: [...state.events, `合成内存调用：${operation}`].slice(-40) });
}
function requireItem(id: number, status?: string) {
  const item = state.items.find((candidate) => candidate.id === id);
  if (!item || (status && item.status !== status)) return deny("invalid synthetic action state");
  return item;
}
function updateItem(id: number, status: string) {
  publish({ items: state.items.map((item) => item.id === id ? { ...item, status } : item) });
}
export function inboxSnapshot(status = "suggested", limit = 6): ActionInboxResponse {
  return { available: true, scope: "own", items: structuredClone(state.items.filter((item) => item.status === status).slice(0, limit)),
    today_summary: { today_approved_count: state.approved, today_executed_count: state.executed } };
}
export function listInbox(token: string, params: { status?: string; limit?: number; category?: string } = {}) {
  const status = params.status ?? "suggested";
  const limit = params.limit ?? 6;
  call(`listActionInbox(${status})`, token);
  if (!["suggested", "approved", "executing"].includes(status) || !Number.isInteger(limit) || limit < 1 || limit > 50 || params.category) return deny("unsupported inbox query");
  if (state.readsFail) throw new Error("合成：来源读取失败（未发起网络请求）");
  return inboxSnapshot(status, limit);
}
export function transition(token: string, id: number, status: "approved" | "dismissed" | "snoozed") {
  call(`transition(${status})`, token);
  requireItem(id, "suggested");
  updateItem(id, status);
  if (status === "approved") publish({ approved: state.approved + 1 });
  return { ok: true, status, action_id: id };
}
function appendLedger(id: number, outcome: string, detail: Record<string, unknown>) {
  const ledgerId = 990001 + state.ledger.length;
  publish({ ledger: [...state.ledger, { id: ledgerId, action_id: id, category: "kol_profile", dedupe_key: "synthetic-fixture",
    actor_staff_id: null, mode: "executed", outcome, endpoint: "fixture-only", cost_cents: 0,
    error: "", detail_json: detail, created_at: stamp }] });
  return ledgerId;
}
export function execute(token: string, id: number) {
  call("executeAction (synthetic)", token);
  requireItem(id, "approved");
  if (state.scenario === "unknown") throw new Error("合成：执行响应丢失（未连接任何外部系统）");
  if (!["suggested", "approved", "queued"].includes(state.scenario)) return deny("execution not allowed in scenario");
  const detail = { enqueue: { status: "queued", job_id: "synthetic-job-1" }, result_checklist: {
    outcome: "success", jobs_created: 1, rows_written: 0, cost_spent_cents: 0,
    before_after: [{ table: "synthetic_fixture_only", before: 0, after: 0, delta: 0 }],
  } };
  updateItem(id, "executed");
  const ledger_id = appendLedger(id, "success", detail);
  publish({ executed: state.executed + 1 });
  return { ok: true, outcome: "success", category: "kol_profile", ledger_id, detail };
}
export function reconcile(token: string, id: number, payload: ActionReconcileRequest) {
  call("reconcileAction (synthetic)", token);
  requireItem(id, "executing");
  if (state.readsFail || !payload.reason?.trim() || !payload.evidence?.length || !payload.correlation_id) return deny("reconciliation paused or incomplete");
  const status = { unknown: "executing", succeeded: "executed", failed: "failed" }[payload.decision];
  if (!status) return deny("unsupported reconciliation decision");
  updateItem(id, status);
  const ledger_id = appendLedger(id, payload.decision === "succeeded" ? "success" : payload.decision, { synthetic: true });
  return { ok: true, action_id: id, decision: payload.decision, status, ledger_id, correlation_id: payload.correlation_id, idempotent: false };
}
export function recentLedger(token: string, limit = 20) {
  call("listRecentExecutionLedger", token);
  if (!Number.isInteger(limit) || limit < 1 || limit > 50) return deny("unsupported ledger query");
  if (state.readsFail) throw new Error("合成：台账读取失败（未发起网络请求）");
  return { items: structuredClone(state.ledger.slice(-limit)), available: true };
}
export function setReadFailure(failed: boolean) {
  if (state.scenario !== "read_failure") return deny("read failure toggle outside scenario");
  publish({ readsFail: failed });
}
export function marketSummary(): MarketBrainSummary {
  const error = state.scenario === "source_failure";
  const partial = state.scenario === "source_partial";
  return { status: "ok", reason: "合成数据夹具", claim_status: "descriptive_only", organization_id: null,
    organization_scope_status: "synthetic_fixture", generated_at: stamp,
    weekly_signals: { status: error ? "error" : partial ? "partial" : "empty",
      items: partial ? [{ signal: "【合成】样本评论关注轻量化，不代表真实市场趋势", kind: "synthetic_fixture", freshness: "合成固定窗口", sample_size: 12, confidence: "low" }] : [],
      sources_note: "全部为合成数据；不是实时市场、生产查询或营销效果证据。",
      sources: { brand_pulse: { status: error ? "error" : "ready" }, category_tracks: { status: error || partial ? "error" : "ready" }, market_voice: { status: error ? "error" : "ready" } } },
    product_opportunities: { items: [], note: "合成空列表", status: "empty" }, recommended_actions: { items: [], note: "合成空列表", status: "empty" },
    strategy_defaults: { sku_hint: "", budget_hint: null, note: "合成夹具，不运行策略", status: "empty" },
    learning_digest: { validated: [], effective_styles: [], dropped_channels: [], next_change: "", honesty_note: "无真实效果证据", status: "empty" },
  };
}
export const planningOutput = { planning_readiness: {
  status: "needs_evidence", executable: false, approval_status: "not_requested", claim_status: "descriptive_only",
  gaps: [{ code: "fixture_gap", message: "合成：目标市场证据未补齐" }],
  next_steps: [{ code: "fixture_next", title: "合成：先核对来源", reason: "这条数据仅用于验收，不代表真实市场判断" }],
} };

export const planningReviewOutput = {
  status: "ok",
  planning_readiness: {
    status: "draft_for_review", executable: false, approval_status: "not_requested", claim_status: "descriptive_only",
    gaps: [], next_steps: [{ code: "fixture_review", title: "合成：负责人审阅预算和候选", reason: "样例未获批准，也未创建项目或联系任何创作者" }],
  },
  meta: {
    product: "【合成】轻量定焦镜头", market: "【合成】北美样例市场", goal: "launch", budget_cents: 100001,
    model_used: "synthetic_injected_model", signal_coverage: "5/5",
    budget_validation: { input_status: "valid", allocation_status: "model_validated", allocated_cents: 75001,
      unallocated_cents: 25000, warnings: ["model_budget_pct_recomputed"] },
    output_warnings: [], model_cost: { status: "unknown", source: "injected_model_fn" },
  },
  plan: {
    creator_mix: [
      { tier: "mid", share: 0.6, count: 3, sample_creators: [{ id: 910001, handle: "synthetic-lens-review", platform: "youtube", fit: 88 }] },
      { tier: "micro", share: 0.4, count: 2, sample_creators: [{ id: 910002, handle: "synthetic-street-photo", platform: "instagram", fit: 82 }] },
    ],
    budget_allocation: [
      { bucket: "creator_fees", pct: 50001 / 100001, amount_cents: 50001 },
      { bucket: "content_boost", pct: 15000 / 100001, amount_cents: 15000 },
      { bucket: "ops_buffer", pct: 10000 / 100001, amount_cents: 10000 },
    ],
    timeline: [
      { phase: "seed", week: 1, focus: "合成：核对简报、样品与候选可用档期" },
      { phase: "ramp", week: 2, focus: "合成：审阅样稿与内容方向，不对外发布" },
      { phase: "peak", week: 3, focus: "合成：负责人另行批准后再确认发布时间" },
      { phase: "harvest", week: 4, focus: "合成：设计复盘口径，不填造实际效果" },
    ],
    content_angles: [
      { angle: "合成：轻装街拍体验", why: "合成：展示体积与使用场景的取舍", market_signal: "synthetic_fixture:portable-use-case" },
      { angle: "合成：低光拍摄对比", why: "合成：先核实样片条件与可复现方法", market_signal: "synthetic_fixture:low-light-example" },
    ],
  },
  risks: [{ risk: "合成：候选档期与逐人匹配理由尚未核对", severity: "medium", mitigation: "合成：人工核对后再申请批准；本夹具不联系候选" }],
};
