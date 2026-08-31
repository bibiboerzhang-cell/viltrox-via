// M7(c):详情读缓存边界。坐实两件事——
//   ① 纯读投影进 45s 内存缓存(重复打开同一个 KOL 不再重发整串 GET),forceRefresh 能绕开;
//   ② 轮询型 / 写后重读型端点保持直透:content-fit(2-6s 追任务态)、account-dossier 与
//      detail-bundle 默认档(UrlSummary 6-10s 追管线)、cooperation(写完可能回读),
//      进了缓存会把「状态翻面」压住 45 秒。
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import { clearApiCache } from "../../lib/apiCache";
import {
  getKolCooperation,
  getKolPoolAccountDossier,
  getKolPoolContentFit,
  getKolPoolDetailBundle,
  getKolPoolIntelligenceCard,
  getKolPoolItem,
  getKolPoolLlmDeepAnalysis,
} from "./kolPool-api.detail";

beforeEach(() => {
  clearApiCache();
  apiFetch.mockReset().mockResolvedValue({});
});

describe("read-only projections share a 45s memory cache", () => {
  it("serves a repeat intelligence-card read from cache", async () => {
    await getKolPoolIntelligenceCard("tok-card", 42);
    await getKolPoolIntelligenceCard("tok-card", 42);

    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/kol-pool/42/intelligence-card?include_product_fit=true");
    // no-store 已去掉:留着会让浏览器层也拒绝复用,和内存缓存目的相反。
    expect(init).not.toMatchObject({ cache: "no-store" });
  });

  it("lets a manual refresh bypass the read cache", async () => {
    await getKolPoolLlmDeepAnalysis("tok-deep", 42, 20);
    await getKolPoolLlmDeepAnalysis("tok-deep", 42, 20);
    expect(apiFetch).toHaveBeenCalledTimes(1);

    await getKolPoolLlmDeepAnalysis("tok-deep", 42, 20, { forceRefresh: true });
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });

  it("keeps different identities and different item scopes on separate keys", async () => {
    await getKolPoolItem("tok-a", 42, true);
    await getKolPoolItem("tok-b", 42, true);
    await getKolPoolItem("tok-a", 42, false);

    expect(apiFetch).toHaveBeenCalledTimes(3);
  });
});

describe("polling and write-then-reread endpoints stay direct", () => {
  it("does not cache the content-fit job poll", async () => {
    await getKolPoolContentFit("tok-fit", 42, { jobId: 91 });
    await getKolPoolContentFit("tok-fit", 42, { jobId: 91 });

    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(apiFetch.mock.calls[0][1]).toMatchObject({ cache: "no-store" });
  });

  it("does not cache the account dossier or the cooperation read", async () => {
    await getKolPoolAccountDossier("tok-dos", 42);
    await getKolPoolAccountDossier("tok-dos", 42);
    await getKolCooperation("tok-coop", 42);
    await getKolCooperation("tok-coop", 42);

    expect(apiFetch).toHaveBeenCalledTimes(4);
  });

  it("leaves detail-bundle direct by default and cached only when a caller opts in", async () => {
    await getKolPoolDetailBundle("tok-b1", 42, { videoLimit: 10, llmLimit: 6 });
    await getKolPoolDetailBundle("tok-b1", 42, { videoLimit: 10, llmLimit: 6 });
    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(apiFetch.mock.calls[0][1]).toMatchObject({ cache: "no-store" });

    apiFetch.mockClear();
    await getKolPoolDetailBundle("tok-b2", 42, { cacheTtlMs: 45_000 });
    await getKolPoolDetailBundle("tok-b2", 42, { cacheTtlMs: 45_000 });
    expect(apiFetch).toHaveBeenCalledTimes(1);

    await getKolPoolDetailBundle("tok-b2", 42, { cacheTtlMs: 45_000, forceRefresh: true });
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });
});
