import { beforeEach, describe, expect, it, vi } from "vitest";

// dataWatchMyKolVideo 走 ../http.apiFetch 单出口(兄弟件 kolMemory-api.test.ts 同款 mock seam);
// jsonBody 等其余导出保持真实现,本文件的纯函数用例零受影响。
const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("../http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import {
  classifyVideoRow,
  dataWatchMyKolVideo,
  enableMyKolContentMonitoring,
  enqueueMyKolVideoKeyframeQa,
  filterClassifiedVideos,
  getMyKolContentMonitoring,
  pauseMyKolContentMonitoring,
  sortClassifiedVideos,
  summarizeKolVideos,
  videoTrendText,
  type VkpiKolPoolVideoRow,
} from "./myKolBoard-api";

const row = (id: number, extra: Partial<VkpiKolPoolVideoRow> = {}): VkpiKolPoolVideoRow => ({
  evidence_id: id,
  title: `clip ${id}`,
  publish_date: `2026-08-${String(id).padStart(2, "0")}`,
  ...extra,
});

describe("Viltrox video evidence classification", () => {
  it("uses the strongest bounded evidence and keeps explicit negative separate from unknown", () => {
    expect(classifyVideoRow(row(1, { project_id: 9, llm_viltrox_detected: false }))).toBe("cooperation");
    expect(classifyVideoRow(row(2, { llm_viltrox_detected: true }))).toBe("analysis_confirmed");
    expect(classifyVideoRow(row(3, { llm_viltrox_products: ["AF 85mm F1.4 Pro"] }))).toBe("analysis_confirmed");
    expect(classifyVideoRow(row(4, { title: "唯卓仕 AF 35mm 实拍" }))).toBe("title_mention");
    expect(classifyVideoRow(row(5, { llm_viltrox_detected: false }))).toBe("not_related");
    expect(classifyVideoRow(row(6))).toBe("undetermined");
  });

  it("reads the structured brand-evidence tri-state before the legacy boolean (staff feedback #4)", () => {
    // present(画面/字幕/口播带时间戳证据)→ 画面/口播识别 V,哪怕旧布尔为 false
    expect(classifyVideoRow(row(11, { llm_viltrox_status: "present", llm_viltrox_detected: false }))).toBe("analysis_confirmed");
    // unknown(未完整检查 / 画面不清 / 口播不可辨)→ 待深析,绝不因旧布尔 false 当成不相关
    expect(classifyVideoRow(row(12, { llm_viltrox_status: "unknown", llm_viltrox_detected: false }))).toBe("undetermined");
    expect(classifyVideoRow(row(13, { llm_viltrox_status: "unknown", llm_viltrox_detected: true }))).toBe("undetermined");
    // absent(完整查过画面+音频)→ 深析未见 V;但标题品牌词仍优先于 absent(与后端 CTE 同序)
    expect(classifyVideoRow(row(14, { llm_viltrox_status: "absent" }))).toBe("not_related");
    expect(classifyVideoRow(row(15, { llm_viltrox_status: "absent", title: "Viltrox AF 75mm review" }))).toBe("title_mention");
    // 服务端已下发 v_tier 时原样采信,不在前端二次推断
    expect(classifyVideoRow(row(16, { v_tier: "not_related", llm_viltrox_status: "present" }))).toBe("not_related");
    // 结构化块缺席(旧行)才回退旧布尔
    expect(classifyVideoRow(row(17, { llm_viltrox_status: null, llm_viltrox_detected: false }))).toBe("not_related");
  });

  it("never counts analyzed-negative or unknown rows as Viltrox related", () => {
    const videos = [
      row(1, { project_id: 4, view_count: 100 }),
      row(2, { llm_viltrox_detected: true, view_count: 200, has_final_v1_cache: true }),
      row(3, { title: "Viltrox portrait test", view_count: null }),
      row(4, { llm_viltrox_detected: false, view_count: 400, has_final_v1_cache: true }),
      row(5, { view_count: null }),
    ];
    const summary = summarizeKolVideos(videos);

    expect(summary.vRelatedCount).toBe(3);
    expect(summary.unrelatedCount).toBe(1);
    expect(summary.undeterminedCount).toBe(1);
    expect(summary.measuredCount).toBe(3);
    expect(summary.viewsTotal).toBe(700);
    expect(filterClassifiedVideos(summary.classified, "viltrox").map(({ video }) => video.evidence_id)).toEqual([1, 2, 3]);
    expect(filterClassifiedVideos(summary.classified, "not_related").map(({ video }) => video.evidence_id)).toEqual([4]);
    expect(filterClassifiedVideos(summary.classified, "undetermined").map(({ video }) => video.evidence_id)).toEqual([5]);
  });

  it("sorts measured views first without converting missing measurements to zero", () => {
    const videos = [row(1, { view_count: null }), row(2, { view_count: 0 }), row(3, { view_count: 12 })];
    const classified = summarizeKolVideos(videos).classified;
    expect(sortClassifiedVideos(classified, "all", "views").map(({ video }) => video.evidence_id)).toEqual([3, 2, 1]);
  });

  it("renders actual snapshot fields without claiming real-time data", () => {
    expect(videoTrendText(row(1, {
      tracking_status: "tracked",
      freshness: "fresh",
      views_delta_24h: 1200,
      views_delta_7d: -50,
      delta_24h_status: "ready",
      delta_7d_status: "ready",
      last_success: { fetched_at: "2026-08-21T12:30:00+00:00", status: "success" },
    }))).toBe("24h +1,200 · 7d -50 · 最后刷新 2026-08-21 12:30");
    expect(videoTrendText(row(2, {
      tracking_status: "failed",
      last_success: { fetched_at: "2026-08-20T08:00:00Z", status: "success" },
    }))).toBe("刷新失败 · 上次成功 2026-08-20 08:00");
    expect(videoTrendText(row(3, {
      tracking_status: "insufficient_history",
      last_success: { fetched_at: "2026-08-21T09:00:00Z", status: "legacy_current_only" },
    }))).toBe("趋势待积累");
    expect(videoTrendText(row(4, {
      tracking_status: "stale",
      freshness: "stale",
      views_delta_24h: 5,
      delta_24h_status: "ready",
      delta_7d_status: "insufficient_history",
      last_success: { fetched_at: "2026-08-18T09:00:00Z", status: "success" },
    }))).toBe("数据已陈旧 · 24h +5 · 7d 待积累 · 最后刷新 2026-08-18 09:00");
  });
});

describe("dataWatchMyKolVideo(一键数据关注 POST 契约)", () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({ status: "tracking" });
  });

  it("POST 到 data-watch 端点;缺省发空体,让服务端自动认 SKU", async () => {
    await dataWatchMyKolVideo("tok", 101, 901);
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/api/admin/vkpi/my-kol/101/videos/901/data-watch",
      { method: "POST", body: JSON.stringify({}) },
      "tok",
    );
  });

  it("显式 SKU 列表如实透传 product_skus", async () => {
    await dataWatchMyKolVideo("tok", "101", 901, ["AF-85-F14", "AF-35-F18"]);
    const [path, init] = apiFetchMock.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/my-kol/101/videos/901/data-watch");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ product_skus: ["AF-85-F14", "AF-35-F18"] });
  });

  it("字符串 id 经 encodeURIComponent 归一(路径安全)", async () => {
    await dataWatchMyKolVideo("tok", "a b", "9/1");
    expect(String(apiFetchMock.mock.calls[0][0])).toBe("/api/admin/vkpi/my-kol/a%20b/videos/9%2F1/data-watch");
  });

  it("sku_required 响应(HTTP 200 携 candidates)原样返回,不吞不改", async () => {
    apiFetchMock.mockResolvedValue({
      status: "sku_required",
      evidence_id: 901,
      candidates: [{ sku_code: "AF-85-F14", sku_name: "AF 85mm F1.4 Pro" }],
    });
    const resp = await dataWatchMyKolVideo("tok", 101, 901);
    expect(resp.status).toBe("sku_required");
    expect(resp.candidates?.[0]?.sku_code).toBe("AF-85-F14");
  });
});

describe("MY KOL content-monitoring explicit subscription contract", () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({ status: "ready", provider_calls_performed: false });
  });

  it("keeps pure read, explicit enable, and pause on one encoded endpoint", async () => {
    await getMyKolContentMonitoring("tok", "a b");
    await enableMyKolContentMonitoring("tok", "a b", 48);
    await pauseMyKolContentMonitoring("tok", "a b");

    const path = "/api/admin/vkpi/my-kol/a%20b/content-monitoring";
    expect(apiFetchMock).toHaveBeenNthCalledWith(1, path, {}, "tok");
    expect(apiFetchMock).toHaveBeenNthCalledWith(
      2,
      path,
      { method: "POST", body: JSON.stringify({ cadence_hours: 48 }) },
      "tok",
    );
    expect(apiFetchMock).toHaveBeenNthCalledWith(3, path, { method: "DELETE" }, "tok");
  });
});

describe("MY KOL keyframe QA queue contract", () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({ status: "queued", provider_calls: false });
  });

  it("POSTs one encoded evidence target without claiming provider completion", async () => {
    const response = await enqueueMyKolVideoKeyframeQa("tok", "a b", "9/1");
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/api/admin/vkpi/kol-pool/a%20b/enqueue-video-keyframe-qa",
      { method: "POST", body: JSON.stringify({ evidence_id: "9/1" }) },
      "tok",
    );
    expect(response).toEqual({ status: "queued", provider_calls: false });
  });
});
