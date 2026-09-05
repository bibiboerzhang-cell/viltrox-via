// Presentation only: an accepted execution or queue receipt is not business acceptance.
type RecordValue = Record<string, unknown>;
export type ActionStage = { label: string; next: string; tone: "info" | "warning" | "error" };

export function asRecord(value: unknown): RecordValue {
  return value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

const UNKNOWN: ActionStage = {
  label: "结果未知 · 待核对",
  next: "请刷新状态并核对执行台账；确认前不要重复执行。",
  tone: "warning",
};
const QUEUED: ActionStage = {
  label: "任务已提交 · 结果待核验",
  next: "入队不代表任务完成；请核对后续任务结果与回执。",
  tone: "info",
};

export function executionStage(outcome: unknown, detailValue: unknown, mode?: unknown): ActionStage {
  const detail = asRecord(detailValue);
  const receipt = asRecord(detail.result_checklist);
  if (detail.manual_reconciliation_required === true) return UNKNOWN;
  if (receipt.outcome && receipt.outcome !== outcome) return UNKNOWN;
  if (outcome === "failed") return {
    label: "执行失败", next: "查看失败原因与台账，处理后由负责人决定下一步。", tone: "error",
  };
  if (outcome === "skipped") return {
    label: "未执行 · 待处理", next: "先处理阻断原因，再由负责人确认是否执行。", tone: "warning",
  };
  if (outcome !== "success" || (mode != null && mode !== "executed")) return UNKNOWN;
  const enqueue = asRecord(detail.enqueue);
  if ((count(receipt.jobs_created) ?? 0) > 0 || detail.requeued === true ||
      ["queued", "already_queued", "already_running"].includes(String(enqueue.status ?? "")) ||
      enqueue.job != null || enqueue.job_id != null || enqueue.id != null) return QUEUED;
  return {
    label: "执行已返回 · 待验收",
    next: "请核对执行回执；业务效果仍需验收。",
    tone: "info",
  };
}

export function itemStage(itemValue: unknown): ActionStage {
  const item = asRecord(itemValue);
  if (item.execution_response_unknown === true) return UNKNOWN;
  if (item.status === "executing") return {
    label: "执行中 · 结果待核对", next: "尚无确认终态；请核对回执，禁止自动重试。", tone: "warning",
  };
  if (item.status === "queued") return QUEUED;
  if (item.status === "approved" && item.execution_blocked === true) return executionStage("skipped", {});
  if (item.status === "executed" || item.status === "failed") return executionStage(
    item.status === "executed" ? "success" : "failed",
    { ...asRecord(item.execution_detail), result_checklist: item.result_checklist_json },
  );
  if (item.status === "approved") return {
    label: "已批准 · 尚未执行",
    next: item.category === "gtm_bet"
      ? "先在线下完成业务动作，再标记执行；效果仍需后续核验。"
      : "批准不触发执行；请确认影响与费用后再执行。",
    tone: "info",
  };
  if (item.status === "suggested") return {
    label: item.requires_approval ? "建议待审批" : "提醒待处理",
    next: item.category === "gtm_verdict" ? "请核对预期与实际结果后完成人工裁决。"
      : item.requires_approval ? "先查看证据、预期收益与验证计划，再决定是否批准。"
      : "确认提醒或稍后处理，不会自动执行业务动作。",
    tone: "info",
  };
  return UNKNOWN;
}

export function receiptSummary(value: unknown, t: (key: string) => string): string {
  const receipt = asRecord(value);
  if (!Object.keys(receipt).length) return t("执行回执缺失 · 行数与费用未知，请核对台账。");
  const jobs = count(receipt.jobs_created);
  const rows = count(receipt.rows_written);
  const cost = count(receipt.cost_spent_cents);
  return [
    `${t("任务记录")}: ${jobs ?? t("未知")}`,
    `${t("写入行数")}: ${rows ?? t("未知")}`,
    cost === null ? t("费用未知") : `${t("回执估算费用")}: $${(cost / 100).toFixed(2)}`,
    t("实际费用待成本台账核对"),
  ].join(" · ");
}
