import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CampaignPlanPresentation } from "./CampaignPlanPresentation";
import { candidateCoverage, candidateEvidence, EVIDENCE_FIELDS, EVIDENCE_GAPS } from "./campaignPlanCandidateEvidence";
import { I18nContext, makeT } from "../cockpit/lib/i18n";
import { I18N_EN } from "../cockpit/data/i18nEn";

const draft = () => ({
  meta: {
    product: "Synthetic Lens", market: "US", goal: "awareness", budget_cents: 10001,
    budget_validation: { input_status: "valid", allocation_status: "model_validated",
      allocated_cents: 7501, unallocated_cents: 2500, warnings: [] },
    model_cost: { status: "unknown", source: "injected_model_fn" },
  },
  plan: {
    budget_allocation: [
      { bucket: "creator_fees", pct: 0.50005, amount_cents: 5001 },
      { bucket: "ops_buffer", pct: 0.25, amount_cents: 2500 },
    ],
    timeline: [{ phase: "seed", week: 1, focus: "Synthetic draft briefing" }],
    creator_mix: [{ tier: "mid", share: 0.5, count: 4,
      sample_creators: [{ id: 7, handle: "synthetic-creator", platform: "youtube", fit: 99 }] }],
    content_angles: [{ angle: "Synthetic angle", why: "Synthetic rationale", market_signal: "Synthetic source" }],
  },
  risks: [{ risk: "Synthetic risk", mitigation: "Synthetic mitigation" }],
});

const evidenceSample = () => ({
  id: 7, handle: "synthetic-creator", platform: "youtube", fit: 99,
  evidence: {
    schema_version: "campaign_candidate_evidence.v1", status: "partial", claim_status: "descriptive_only",
    match_status: "unverified", observed_at: null,
    facts: [{ field: "bio", value: "Synthetic profile bio, not a verified match", source_ref: "kol_pool:7" }],
    source_refs: [{ ref: "kol_pool:7", kind: "kol_pool", record_id: 7,
      url: "https://www.youtube.com/@synthetic-creator", record_updated_at: "2026-09-01T12:30:00+00:00" }],
    gaps: [{ code: "match_reason_unverified", message: "Synthetic source message" },
      { code: "collection_time_unknown", message: "Synthetic source message" }],
  },
});
function withCandidate(candidate: unknown) {
  const data = draft();
  return { ...data, plan: { ...data.plan, creator_mix: [{ ...data.plan.creator_mix[0], sample_creators: [candidate],
    candidate_coverage: { planned_count: 4, unique_sample_count: 1, status: "insufficient",
      gaps: [{ code: "candidate_samples_insufficient", message: "Synthetic shortfall" }] } }] } };
}
const expandEvidence = () => fireEvent.click(screen.getByText("查看候选依据与缺口"));

describe("candidate material review", () => {
  it("keeps individual source material collapsed, states shortfall and never promotes fit to a match", () => {
    const sample = { ...evidenceSample(), reason: "INVENTED MODEL REASON", citations: ["INVENTED SOURCE"] };
    render(<CampaignPlanPresentation output={withCandidate(sample)} />);
    expect(screen.getByText("资料依据部分可审阅 · 匹配未核验")).toBeVisible();
    expect(screen.getByText("当前展示样本 1 · 与规划人数差额 3")).toBeVisible();
    const disclosure = screen.getByText("查看候选依据与缺口").closest("details")!;
    expect(disclosure).not.toHaveAttribute("open");
    expect(screen.getByText(/Synthetic profile bio/)).not.toBeVisible();
    expandEvidence();
    expect(screen.getByText(/Synthetic profile bio/)).toBeVisible();
    expect(screen.getByText("https://www.youtube.com/@synthetic-creator")).toBeVisible();
    expect(screen.getByText("2026-09-01T12:30:00+00:00")).toHaveAttribute("datetime", "2026-09-01T12:30:00+00:00");
    expect(screen.getByText(/资料更新时间不能替代采集时间/)).toBeVisible();
    expect(screen.getByText(/不是独立核实的个人匹配理由/)).toBeVisible();
    expect(screen.getByText(/个人匹配理由未核实/)).toBeVisible();
    expect(document.body.textContent).not.toMatch(/INVENTED|Synthetic source message/);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("labels legacy candidates as unverified without inventing sources or dates", () => {
    render(<CampaignPlanPresentation output={draft()} />);
    expect(screen.getByText("资料依据待核验")).toBeVisible();
    expect(screen.getByText(/展示样本数量待核验/)).toBeVisible();
    expandEvidence();
    expect(screen.getByText(/旧结果未提供逐候选证据/)).toBeVisible();
    expect(document.querySelector("time")).toBeNull();
    expect(screen.queryByText(/资料记录来源：/)).not.toBeInTheDocument();
  });

  it.each([
    ["schema_version", "future.v2"], ["claim_status", "verified"], ["match_status", "matched"],
    ["status", "ready"], ["observed_at", "2026-09-01T00:00:00Z"], ["facts", "bad"],
    ["source_refs", null], ["gaps", []], ["gaps", [null]], ["source_refs", Array(9).fill({})],
  ])("fails closed for incompatible evidence %s", (key, value) => {
    const sample = evidenceSample();
    const candidate = { ...sample, evidence: { ...sample.evidence, [String(key)]: value } };
    render(<CampaignPlanPresentation output={withCandidate(candidate)} />);
    expect(screen.getByText("资料依据待核验")).toBeVisible();
    expandEvidence();
    expect(screen.getByText(/部分依据格式、状态或引用异常/)).toBeVisible();
    expect(screen.queryByText(/Synthetic profile bio/)).not.toBeInTheDocument();
    expect(document.querySelector("time")).toBeNull();
  });

  it("does not display another candidate's record or any facts referencing it", () => {
    const sample = evidenceSample();
    sample.evidence.source_refs[0].record_id = 8;
    render(<CampaignPlanPresentation output={withCandidate(sample)} />);
    expandEvidence();
    expect(screen.queryByText(/Synthetic profile bio/)).not.toBeInTheDocument();
    expect(screen.queryByText("https://www.youtube.com/@synthetic-creator")).not.toBeInTheDocument();
    expect(screen.getByText("资料依据待核验")).toBeVisible();
  });

  it("omits broken citations and disallowed score/reason fields without hiding the gaps", () => {
    const sample = evidenceSample();
    sample.evidence.facts = [
      { field: "bio", value: "WRONG ACCOUNT BIO", source_ref: "kol_pool:8" },
      { field: "viltrox_fit_reason", value: "INVENTED REASON", source_ref: "kol_pool:7" },
      { field: "fit", value: "99", source_ref: "kol_pool:7" },
    ];
    render(<CampaignPlanPresentation output={withCandidate(sample)} />);
    expandEvidence();
    expect(document.body.textContent).not.toMatch(/WRONG ACCOUNT|INVENTED REASON/);
    expect(screen.getByText(/当前没有可展示的资料事实/)).toBeVisible();
    expect(screen.getByText(/部分依据格式、状态或引用异常/)).toBeVisible();
    expect(screen.getByText(/个人匹配理由未核实/)).toBeVisible();
  });

  it.each(["javascript:alert(1)", "https://synthetic-secret@example.com/profile", "not a url",
    "https://example.com/profile?synthetic_secret=1", "https://example.com/profile#synthetic_secret"])("omits unusable source URL %s", url => {
    const sample = evidenceSample();
    sample.evidence.source_refs[0].url = url;
    render(<CampaignPlanPresentation output={withCandidate(sample)} />);
    expandEvidence();
    expect(screen.getByText("公开资料网址待核验")).toBeVisible();
    expect(document.body.textContent).not.toContain(url);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it.each([null, "2026-02-30T12:00:00Z", "2026-09-01T25:00:00Z", "2026-09-01T12:00:00+25:00", "invalid"])("does not invent an update or collection time from %s", date => {
    const sample = evidenceSample();
    const candidate = { ...sample, evidence: { ...sample.evidence,
      source_refs: [{ ...sample.evidence.source_refs[0], record_updated_at: date }] } };
    render(<CampaignPlanPresentation output={withCandidate(candidate)} />);
    expandEvidence();
    expect(document.querySelector("time")).toBeNull();
    expect(screen.getByText(/采集时间待核验/)).toBeVisible();
  });

  it.each(["2026-09-01", "2026-09-01 12:30:00", "2026-09-01T12:30:00", "2026-09-01T12:30:00.123456-04:00"])("preserves record update precision and timezone without inventing either: %s", date => {
    const sample = evidenceSample();
    sample.evidence.source_refs[0].record_updated_at = date;
    render(<CampaignPlanPresentation output={withCandidate(sample)} />);
    expandEvidence();
    expect(screen.getByText(date)).toHaveAttribute("datetime", date);
    expect(screen.getByText("按原记录显示，未补时区或精度。")).toBeVisible();
    if (!date.endsWith("-04:00")) expect(screen.getByText("原记录未提供时区。")).toBeVisible();
    else expect(screen.queryByText("原记录未提供时区。")).not.toBeInTheDocument();
    expect(screen.getByText(/采集时间待核验/)).toBeVisible();
  });

  it("keeps unknown material unknown and does not translate it to ready", () => {
    const sample = evidenceSample();
    expect(candidateEvidence({ ...sample, evidence: { ...sample.evidence, status: "unknown" } }).state).toBe("invalid");
    const candidate = { ...sample, evidence: { ...sample.evidence, status: "unknown", facts: [],
      source_refs: [{ ...sample.evidence.source_refs[0], url: null, record_updated_at: null }] } };
    expect(candidateEvidence(candidate).state).toBe("unknown");
    render(<CampaignPlanPresentation output={withCandidate(candidate)} />);
    expect(screen.getByText("资料依据待核验")).toBeVisible();
    expect(screen.queryByText("资料依据部分可审阅 · 匹配未核验")).not.toBeInTheDocument();
  });

  it("verifies sample counts, rejects duplicate or inconsistent coverage and preserves planned zero", () => {
    const base = withCandidate(evidenceSample()).plan.creator_mix[0];
    expect(candidateCoverage(base)).toEqual({ samples: 1, missing: 3 });
    expect(candidateCoverage({ ...base, sample_creators: [evidenceSample(), evidenceSample()] })).toBeNull();
    expect(candidateCoverage({ ...base, candidate_coverage: { ...base.candidate_coverage, status: "count_met" } })).toBeNull();
    expect(candidateCoverage({ ...base, candidate_coverage: { ...base.candidate_coverage, gaps: [] } })).toBeNull();
    expect(candidateCoverage({ count: 0, sample_creators: [], candidate_coverage: {
      planned_count: 0, unique_sample_count: 0, status: "count_met", gaps: [],
    } })).toEqual({ samples: 0, missing: 0 });
  });

  it("translates the new review contract to English, including dynamic field and gap labels", () => {
    const sample = evidenceSample();
    sample.evidence.facts = Object.keys(EVIDENCE_FIELDS).map(field => ({ field, value: `Synthetic ${field}`, source_ref: "kol_pool:7" }));
    sample.evidence.gaps = Object.keys(EVIDENCE_GAPS).map(code => ({ code, message: "Ignored message" }));
    render(<I18nContext.Provider value={{ lang: "en", setLang: () => {}, t: makeT("en", I18N_EN) }}>
      <CampaignPlanPresentation output={withCandidate(sample)} />
    </I18nContext.Provider>);
    fireEvent.click(screen.getByText("Review candidate material and gaps"));
    expect(screen.getByText("Partial profile material available · match unverified")).toBeVisible();
    expect(screen.getByText(/Profile record updated/)).toBeVisible();
    expect(document.body.textContent).not.toMatch(/[\u3400-\u9fff]/);
    expect(fetch).not.toHaveBeenCalled();
  });
});

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Presentation must not issue requests"); }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("read-only campaign plan projection", () => {
  it("shows summary, allocation, relative weeks, sample creators and rationale without execution controls", () => {
    render(<CampaignPlanPresentation output={draft()} />);
    expect(screen.getByRole("region", { name: "营销规划草案" })).toBeInTheDocument();
    expect(screen.getByText("Synthetic Lens")).toBeInTheDocument();
    expect(screen.getByText("100.01")).toBeInTheDocument();
    expect(screen.getByText("50.01")).toBeInTheDocument();
    expect(screen.getByText("75.01")).toBeInTheDocument();
    expect(screen.getByText(/第 1 周/)).toBeInTheDocument();
    expect(screen.getByText(/synthetic-creator.*youtube.*#7/)).toBeInTheDocument();
    expect(screen.getByText(/规划人数.*4/)).toBeInTheDocument();
    expect(screen.getByText(/逐人匹配理由未提供/)).toBeInTheDocument();
    expect(screen.getByText("Synthetic rationale")).toBeInTheDocument();
    expect(screen.getByText("Synthetic risk")).toBeInTheDocument();
    expect(screen.getByText("模型调用成本未知")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/USD|CNY|￥|\$/);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each([undefined, null, "100", true, -1, 1.25, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])(
    "never rounds/coerces invalid or missing cents (%s)", (value) => {
      const data = draft();
      const changed = { ...data, meta: { ...data.meta, budget_cents: value },
        plan: { ...data.plan, budget_allocation: [{ bucket: "creator_fees", pct: 0.5, amount_cents: value }] } };
      render(<CampaignPlanPresentation output={changed} />);
      const table = screen.getByRole("table");
      expect(within(table).getByText("待核验")).toBeInTheDocument();
      expect(screen.getByText(/预算校验信息缺失或与金额不一致/)).toBeInTheDocument();
      expect(document.body.textContent).not.toMatch(/NaN|Infinity|undefined/);
    },
  );

  it.each([[1, "0.01"], [29, "0.29"], [101, "1.01"], [123456, "1,234.56"],
    [Number.MAX_SAFE_INTEGER, "90,071,992,547,409.91"]])("formats %i cents exactly as %s", (cents, expected) => {
    render(<CampaignPlanPresentation output={{ meta: { budget_cents: cents } }} />);
    expect(screen.getByText(expected)).toBeInTheDocument();
  });

  it("preserves known zero and does not confuse a plan allocation with model fees", () => {
    const data = draft();
    render(<CampaignPlanPresentation output={{ ...data,
      meta: { ...data.meta, budget_cents: 0,
        budget_validation: { input_status: "valid", allocation_status: "rule", allocated_cents: 0, unallocated_cents: 0 },
        model_cost: { status: "not_applicable", source: "rule_only" } },
      plan: { ...data.plan, budget_allocation: [{ bucket: "creator_fees", pct: 0, amount_cents: 0 }] },
    }} />);
    expect(within(screen.getByRole("table")).getByText("0.00")).toBeInTheDocument();
    expect(screen.getByText("规则生成（未调用模型）")).toBeInTheDocument();
    expect(screen.getByText(/规划预算不是实际模型费用/)).toBeInTheDocument();
    expect(screen.getByText("规则分配草案")).toBeInTheDocument();
  });

  it.each(["over_budget", "wrong_server_total", "fractional_server_total"])("flags inconsistent budget arithmetic: %s", (kind) => {
    const data = draft();
    if (kind === "over_budget") data.plan.budget_allocation[0].amount_cents = 10002;
    else data.meta.budget_validation.allocated_cents = kind === "wrong_server_total" ? 3 : 7501.5;
    render(<CampaignPlanPresentation output={data} />);
    expect(screen.getByText(/预算校验信息缺失或与金额不一致/)).toBeInTheDocument();
    expect(screen.queryByText("模型分配草案")).not.toBeInTheDocument();
  });

  it.each([null, undefined, [], "legacy", {}, { plan: null, meta: [] }, {
    meta: { product: {}, budget_cents: false },
    plan: { budget_allocation: [null, "bad"], timeline: [null, { week: -2 }],
      creator_mix: [null, { count: "4", share: Infinity, sample_creators: [null] }], content_angles: [null] },
    risks: [null],
  }])("handles legacy and malformed output without inventing evidence (%#)", (output) => {
    render(<CampaignPlanPresentation output={output} />);
    expect(screen.getByRole("region", { name: "营销规划草案" })).toHaveTextContent("待核验");
    expect(screen.getByText("模型调用成本待核验")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows known old amounts as unverified when additive validation is missing", () => {
    const data = draft();
    render(<CampaignPlanPresentation output={{ ...data, meta: { product: "Legacy", budget_cents: 10001 } }} />);
    expect(screen.getByText("50.01")).toBeInTheDocument();
    expect(screen.getByText(/旧结果未提供预算校验记录/)).toBeInTheDocument();
    expect(screen.getByText("模型调用成本待核验")).toBeInTheDocument();
  });

  it.each([1.1, -0.1, "0.5", NaN])("rejects malformed percentage %s without clamping", (pct) => {
    const data = draft();
    render(<CampaignPlanPresentation output={{ ...data,
      plan: { ...data.plan, budget_allocation: [{ bucket: "creator_fees", pct, amount_cents: 5001 }] },
    }} />);
    expect(within(screen.getByRole("table")).getByText("待核验")).toBeInTheDocument();
  });
});
