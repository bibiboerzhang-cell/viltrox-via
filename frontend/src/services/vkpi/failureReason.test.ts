import { describe, expect, it } from "vitest";

import {
  etaLabel,
  etaLabelEn,
  etaSecondsOf,
  failureCategoryOf,
  failureGuidance,
  failureReasonForLocale,
  hasReadableFailure,
  isFailureStatus,
  normalizeFailureCategory,
} from "./failureReason";

describe("failureReason · 类别归一", () => {
  it("新字段六类原样通过,大小写/空白容错", () => {
    expect(normalizeFailureCategory(" Authorization ")).toBe("authorization");
    expect(normalizeFailureCategory("budget")).toBe("budget");
    expect(normalizeFailureCategory("")).toBe("unknown");
    expect(normalizeFailureCategory(null)).toBe("unknown");
  });

  it("旧 last_error_category 只做保守映射,认不出落 unknown", () => {
    expect(failureCategoryOf({ last_error_category: "provider_429" })).toBe("provider");
    expect(failureCategoryOf({ error_category: "media_download_failed" })).toBe("download");
    expect(failureCategoryOf({ error_category: "something_weird" })).toBe("unknown");
    expect(failureCategoryOf({})).toBe("unknown");
  });

  it("failure_category 优先于旧字段", () => {
    expect(failureCategoryOf({ failure_category: "budget", last_error_category: "provider_429" })).toBe("budget");
  });
});

describe("failureReason · 人话与动作", () => {
  it("中文优先后端 failure_reason_human,缺失按类别兜底", () => {
    expect(failureReasonForLocale({ failure_category: "download", failure_reason_human: "视频被平台删除" }, "zh")).toBe("视频被平台删除");
    expect(failureReasonForLocale({ failure_category: "download" }, "zh")).toContain("下载");
  });

  it("英文 locale 用类别映射英文短句,不透出中文", () => {
    const text = failureReasonForLocale({ failure_category: "authorization", failure_reason_human: "无权" }, "en");
    expect(text).toMatch(/authoris/i);
    expect(text).not.toMatch(/[一-鿿]/);
  });

  it("authorization → 从 MY KOL 重新发起;budget → 预算提示;download/provider → 自动重试", () => {
    expect(failureGuidance({ failure_category: "authorization" }).action).toBe("reissue_from_my_kol");
    expect(failureGuidance({ failure_category: "authorization" }).actionLabel).toBe("从 MY KOL 重新发起");
    expect(failureGuidance({ failure_category: "budget" }).action).toBe("check_budget");
    expect(failureGuidance({ failure_category: "download" }).action).toBe("auto_retry");
    expect(failureGuidance({ failure_category: "provider" }).action).toBe("auto_retry");
    expect(failureGuidance({ failure_category: "model" }).action).toBeNull();
  });

  it("只有新字段存在才算失败可读(旧数据不编故事)", () => {
    expect(hasReadableFailure({ failure_category: "budget" })).toBe(true);
    expect(hasReadableFailure({ failure_reason_human: "x" })).toBe(true);
    expect(hasReadableFailure({ last_error_category: "provider_429" })).toBe(false);
    expect(hasReadableFailure(null)).toBe(false);
  });

  it("终态判定覆盖 failed/blocked/triage,不把 queued 当失败", () => {
    expect(isFailureStatus("failed")).toBe(true);
    expect(isFailureStatus("BLOCKED")).toBe(true);
    expect(isFailureStatus("queued")).toBe(false);
  });
});

describe("failureReason · ETA 新口径", () => {
  it("只认 eta_seconds;旧 estimated_remaining_seconds 不渲染", () => {
    expect(etaSecondsOf({ eta_seconds: 90 })).toBe(90);
    expect(etaSecondsOf({ estimated_remaining_seconds: 90 })).toBeNull();
    expect(etaSecondsOf({ eta_seconds: null })).toBeNull();
    expect(etaSecondsOf({ eta_seconds: 0 })).toBeNull();
    expect(etaSecondsOf({ eta_seconds: "abc" })).toBeNull();
  });

  it("人性化:秒 / 分钟 / 小时;缺失为空串", () => {
    expect(etaLabel(30)).toBe("约 30 秒");
    expect(etaLabel(150)).toBe("约 3 分钟");
    expect(etaLabel(5400)).toBe("约 1.5 小时");
    expect(etaLabel(null)).toBe("");
    expect(etaLabelEn(150)).toBe("~3 min");
  });
});
