import { describe, it, expect, vi, beforeEach } from "vitest";

// gtmCommand-api 单测(GTM-1 W3):入参拼装 + 键缺失全兜底 + 显示层宪法剥私。
//   后端两端点 W1/W2 并行在建 —— 本测试用规格合约 mock 响应,保证 API 层先行合格。
const apiFetch = vi.fn();
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import {
  getGtmPlanPreview,
  getMarketBrainSummary,
  listSkuOptions,
  normalizeGtmPlanPreview,
  normalizeMarketBrainSummary,
  normalizeRoadmap,
  stripPrivateFields,
} from "./gtmCommand-api";

beforeEach(() => {
  apiFetch.mockReset();
});

describe("stripPrivateFields(显示层宪法)", () => {
  it("整树剥除 private_evidence / score_details / raw_* / competitor_notes / 黑名单类键", () => {
    const dirty = {
      public_plan: {
        thesis: { go_nogo: "GO", score_details: { w1: 0.7 } },
        kol_candidates: {
          items: [
            {
              handle: "@a",
              risk_labels: ["合作历史短"],
              raw_comments: ["大段原始评论"],
              blacklist: ["某KOL"],
              private_note: "内部",
            },
          ],
        },
      },
      private_evidence: { debug_trace: ["x"], competitor_notes: "机密" },
    };
    const clean = stripPrivateFields(dirty) as Record<string, any>;
    expect(clean.private_evidence).toBeUndefined();
    expect(clean.public_plan.thesis.score_details).toBeUndefined();
    expect(clean.public_plan.thesis.go_nogo).toBe("GO");
    const item = clean.public_plan.kol_candidates.items[0];
    expect(item.raw_comments).toBeUndefined();
    expect(item.blacklist).toBeUndefined();
    expect(item.private_note).toBeUndefined();
    expect(item.risk_labels).toEqual(["合作历史短"]); // 风险只出标签,标签保留
    expect(JSON.stringify(clean)).not.toContain("机密");
  });
});

describe("getMarketBrainSummary", () => {
  it("打对 URL,空响应 {} 全键兜底不炸", async () => {
    apiFetch.mockResolvedValue({});
    const r = await getMarketBrainSummary("tok");
    expect(apiFetch).toHaveBeenCalledWith("/api/admin/vkpi/market-brain/summary", { timeoutMs: 15000 }, "tok");
    expect(r.weekly_signals.items).toEqual([]);
    expect(r.product_opportunities.items).toEqual([]);
    expect(r.recommended_actions.items).toEqual([]);
    expect(r.strategy_defaults.sku_hint).toBe("");
    expect(r.learning_digest.validated).toEqual([]);
    expect(r.learning_digest.honesty_note).toBe("");
  });

  it("合约形状正常归一(含 simulate_entry 包壳)", () => {
    const r = normalizeMarketBrainSummary({
      weekly_signals: {
        items: [{ signal: "85mm 赛道热度上行", kind: "category", freshness: "7d", sample_size: 42, confidence: "medium" }],
        sources_note: "内部信号缓存;外部雷达待接",
      },
      recommended_actions: {
        items: [{ action: "补 3 个候选", reason: "池子薄", evidence_summary: "近 30d 无新增", cost_note: "$0", risk: "低", expected_gain: "候选+3", ref: "inbox:12" }],
      },
      strategy_defaults: { simulate_entry: { sku_hint: "AF-85MM-F14-PRO-FE", budget_hint: 3000 }, note: "n" },
      learning_digest: { validated: ["预判 7d 命中 2/3"], effective_styles: [], dropped_channels: [], next_change: "降低长尾权重", honesty_note: "样本仍小" },
    });
    expect(r.weekly_signals.items[0].sample_size).toBe(42);
    expect(r.weekly_signals.sources_note).toContain("外部雷达待接");
    expect(r.recommended_actions.items[0].expected_gain).toBe("候选+3");
    expect(r.strategy_defaults.sku_hint).toBe("AF-85MM-F14-PRO-FE");
    expect(r.strategy_defaults.budget_hint).toBe(3000);
    expect(r.learning_digest.next_change).toBe("降低长尾权重");
  });

  it("单段失败 {status:error} 透传,不拖垮其他段", () => {
    const r = normalizeMarketBrainSummary({
      weekly_signals: { status: "error", note: "brand_pulse 超时" },
      product_opportunities: { items: [{ sku: "X", opportunity_score: "7.5" }] },
    });
    expect(r.weekly_signals.status).toBe("error");
    expect(r.weekly_signals.items).toEqual([]);
    expect(r.product_opportunities.items[0].opportunity_score).toBe(7.5); // 字符串数字宽容
  });
});

describe("getGtmPlanPreview", () => {
  it("参数拼装:sku 必填编码,budget/window 缺省 3000/30,country 大写,goal 透传", async () => {
    apiFetch.mockResolvedValue({});
    await getGtmPlanPreview("tok", { sku: "AF 85", country: "us", goal: "conversion" });
    const url = String(apiFetch.mock.calls[0][0]);
    expect(url).toContain("/api/admin/vkpi/market-brain/gtm-plan/preview?");
    expect(url).toContain("sku=AF+85");
    expect(url).toContain("country=US");
    expect(url).toContain("budget_usd=3000");
    expect(url).toContain("goal=conversion");
    expect(url).toContain("window_days=30");
  });

  it("空响应 11 段全兜底;dealer 诚实占位透传", async () => {
    apiFetch.mockResolvedValue({
      public_plan: { dealer_targets: { status: "data_missing", note: "Dealer 表 0 行,GTM-2 导入后激活" } },
    });
    const r = await getGtmPlanPreview("tok", { sku: "S" });
    expect(r.public_plan.thesis.go_nogo).toBe("");
    expect(r.public_plan.forecast).toEqual([]);
    expect(r.public_plan.roadmap).toEqual([]);
    expect(r.public_plan.dealer_targets.status).toBe("data_missing");
    expect(r.public_plan.dealer_targets.note).toContain("GTM-2");
    expect(r.public_plan.action_inbox_items).toEqual([]);
    expect(r.meta.generated_at).toBe("");
  });

  it("forecast 条件化四段式字段归一,缺 escalate/retreat 不炸(留空给页面标不合规)", () => {
    const r = normalizeGtmPlanPreview({
      public_plan: {
        forecast: [
          { horizon_days: 7, statement: "有内容放大机会", signals_summary: "完播上行", confidence: "medium", escalate_if: "48h 完播进前 25%", retreat_if: "回复率低于目标" },
          { horizon_days: "14", statement: "仅陈述" },
        ],
      },
    });
    expect(r.public_plan.forecast).toHaveLength(2);
    expect(r.public_plan.forecast[0].escalate_if).toContain("48h");
    expect(r.public_plan.forecast[1].horizon_days).toBe(14);
    expect(r.public_plan.forecast[1].escalate_if).toBe("");
    expect(r.public_plan.forecast[1].retreat_if).toBe("");
  });

  it("preview 响应即使多给 private_evidence 也绝不外泄", () => {
    const r = normalizeGtmPlanPreview({
      public_plan: { thesis: { go_nogo: "GO", score_details: { secret: 1 } } },
      private_evidence: { raw_sources: ["机密原文"] },
    });
    expect(JSON.stringify(r)).not.toContain("机密原文");
    expect(JSON.stringify(r)).not.toContain("score_details");
    expect(r.public_plan.thesis.go_nogo).toBe("GO");
  });
});

describe("normalizeRoadmap", () => {
  it("对象形 {w1,w2_4,m2_3} → 三段,渠道配合归一", () => {
    const phases = normalizeRoadmap({
      w1: { channels: [{ channel: "KOL", play: "寄样 10 人" }], items: ["建短链"] },
      "w2-4": ["两段式放大"],
      m2_3: { note: "视 W2-4 结果定" },
    });
    expect(phases.map((p) => p.key)).toEqual(["w1", "w2_4", "m2_3"]);
    expect(phases[0].channels[0]).toEqual({ channel: "KOL", play: "寄样 10 人" });
    expect(phases[0].items).toContain("建短链");
    expect(phases[1].items).toEqual(["两段式放大"]);
    expect(phases[2].note).toContain("W2-4");
  });

  it("数组形 [{phase:'w1',…}] 与未知 phase 都吃;缺失 → []", () => {
    const phases = normalizeRoadmap([
      { phase: "w1", items: ["a"] },
      { phase: "custom", label: "自定义", items: ["b"] },
    ]);
    expect(phases).toHaveLength(2);
    expect(phases[0].key).toBe("w1");
    expect(phases[1].label).toBe("自定义");
    expect(normalizeRoadmap(undefined)).toEqual([]);
    expect(normalizeRoadmap("oops")).toEqual([]);
  });
});

describe("listSkuOptions", () => {
  it("复用 /sku/list,项归一", async () => {
    apiFetch.mockResolvedValue({ items: [{ sku: "AF-85MM-F14-PRO-FE", model_name: "AF 85mm F1.4 Pro", price_usd: "549" }] });
    const r = await listSkuOptions("tok", " 85 ");
    expect(String(apiFetch.mock.calls[0][0])).toBe("/api/admin/vkpi/sku/list?query=85&limit=30");
    expect(r[0].price_usd).toBe(549);
  });

  it("响应无 items → 空数组", async () => {
    apiFetch.mockResolvedValue({});
    expect(await listSkuOptions("tok", "")).toEqual([]);
  });
});
