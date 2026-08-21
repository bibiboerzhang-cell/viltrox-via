import { describe, expect, it } from "vitest";

import {
  classifyVideoRow,
  filterClassifiedVideos,
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
