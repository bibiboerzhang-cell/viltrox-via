import { describe, expect, it } from "vitest";
import { executionStage, itemStage, receiptSummary } from "./actionInboxStatus";
import { makeT } from "../lib/i18n";
import { I18N_EN } from "../data/i18nEn";

describe("Action Inbox conservative presentation contract", () => {
  it.each([undefined, "", "new_backend_state", "provider_unknown"])("unknown status stays unknown: %s", (status) => {
    expect(itemStage({ status }).label).toBe("结果未知 · 待核对");
  });
  it.each(["queued", "already_queued", "already_running"])("enqueue %s is never task completion", (status) => {
    expect(executionStage("success", { enqueue: { status } }).label).toBe("任务已提交 · 结果待核验");
  });
  it("non-executed ledger mode cannot prove execution", () => {
    expect(executionStage("success", {}, "dry_run").label).toBe("结果未知 · 待核对");
  });
  it("contradictory receipt keeps outcome unknown", () => {
    expect(executionStage("success", { result_checklist: { outcome: "failed" } }).label).toBe("结果未知 · 待核对");
  });
  it("zero and positive costs are estimates, never verified charges", () => {
    for (const cost of [0, 25]) {
      const text = receiptSummary({ jobs_created: 0, rows_written: 0, cost_spent_cents: cost }, (key) => key);
      expect(text).toContain(`回执估算费用: $${(cost / 100).toFixed(2)}`);
      expect(text).toContain("实际费用待成本台账核对");
      expect(text).not.toContain("未花钱");
    }
  });
  it.each([undefined, null, NaN, Infinity, -1, "0", false])("missing or malformed metrics are unknown: %s", (value) => {
    const text = receiptSummary({ jobs_created: value, rows_written: value, cost_spent_cents: value }, (key) => key);
    expect(text).toContain("任务记录: 未知");
    expect(text).toContain("写入行数: 未知");
    expect(text).toContain("费用未知");
  });
  it("missing receipt never invents counts or free execution", () => {
    expect(receiptSummary(undefined, (key) => key)).toBe("执行回执缺失 · 行数与费用未知，请核对台账。");
  });
  it("dynamic stage labels and receipt wording have real English translations", () => {
    const t = makeT("en", I18N_EN);
    const stages = [
      ...["suggested", "approved", "queued", "executing", "executed", "failed", "unknown"].map(
        (status) => itemStage({ status, requires_approval: true }),
      ),
      itemStage({ status: "suggested", requires_approval: false }),
      itemStage({ status: "suggested", category: "gtm_verdict" }),
      itemStage({ status: "approved", category: "gtm_bet" }),
      executionStage("skipped", {}),
    ];
    for (const stage of stages) {
      expect(t(stage.label)).not.toMatch(/[\u3400-\u9fff]/);
      expect(t(stage.next)).not.toMatch(/[\u3400-\u9fff]/);
    }
    expect(receiptSummary({ rows_written: 0, cost_spent_cents: 0 }, t)).not.toMatch(/[\u3400-\u9fff]/);
    expect(receiptSummary(undefined, t)).not.toMatch(/[\u3400-\u9fff]/);
  });
});
