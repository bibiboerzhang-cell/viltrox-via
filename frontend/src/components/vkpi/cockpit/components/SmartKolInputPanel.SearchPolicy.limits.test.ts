import { describe, expect, it } from "vitest";

import {
  KOL_SEARCH_PER_PLATFORM_LIMITS,
  KOL_SEARCH_STRATEGIES,
} from "./SmartKolInputPanel.SearchPolicy";

/**
 * 车道 2·B3 三档预设契约。
 *
 * YouTube 那条腿走 YouTube Data API search.list —— 同一次调用无论 maxResults 取 1
 * 还是 50 都恒定 100 quota units、仍是一次 HTTP 往返，所以 20→50 配额不变、延迟不变、
 * 零 Apify 花费（prod 实测该腿 <2s）。Instagram / TikTok 走按结果计费的 Apify actor，
 * prod 14 天实测 instagram-hashtag-scraper 一家吃掉在线发现总花费的 93.7%
 * （29 次 run／7947 items／$16.57），且它的 resultsLimit 是按 tag 计的
 * （单次 dataset 实测 240~300 条 = 4~5 个 tag × 60）——跟着提到 50 会让成本同步翻 2.5 倍。
 * 所以本批只提 YouTube。这条测试就是那个决定的守门人：谁想动 IG/TT 的 20，
 * 先在这里写清楚为什么。
 */
describe("KOL 搜索三档预设的每平台上限", () => {
  it("三档共用同一份每平台上限，避免三处各写一份漂移", () => {
    for (const strategy of Object.values(KOL_SEARCH_STRATEGIES)) {
      expect(strategy.perPlatformLimits).toBe(KOL_SEARCH_PER_PLATFORM_LIMITS);
    }
  });

  it("YouTube 提到 50，Instagram / TikTok 保持 20", () => {
    expect(KOL_SEARCH_PER_PLATFORM_LIMITS.youtube).toBe(50);
    expect(KOL_SEARCH_PER_PLATFORM_LIMITS.instagram).toBe(20);
    expect(KOL_SEARCH_PER_PLATFORM_LIMITS.tiktok).toBe(20);
  });

  it("标量兜底仍是 20：未在覆盖表里列出的平台不会被顺带放大", () => {
    for (const strategy of Object.values(KOL_SEARCH_STRATEGIES)) {
      expect(strategy.perPlatformLimit).toBe(20);
    }
    expect(KOL_SEARCH_PER_PLATFORM_LIMITS.facebook).toBeUndefined();
  });

  it("每平台上限不许超过后端硬顶 50", () => {
    for (const limit of Object.values(KOL_SEARCH_PER_PLATFORM_LIMITS)) {
      expect(limit).toBeGreaterThanOrEqual(1);
      expect(limit).toBeLessThanOrEqual(50);
    }
  });

  it("覆盖表冻结，防止运行时被就地改写", () => {
    expect(Object.isFrozen(KOL_SEARCH_PER_PLATFORM_LIMITS)).toBe(true);
  });
});
