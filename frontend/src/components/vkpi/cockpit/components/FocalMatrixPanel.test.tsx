import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const apiFetch = vi.fn();
vi.mock("../../../../services/http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

import { FocalMatrixPanel } from "./FocalMatrixPanel";

beforeEach(() => {
  apiFetch.mockReset();
  window.localStorage.clear();
});

describe("FocalMatrixPanel personalized product opportunities", () => {
  it("shows mount/gear/stage evidence and a series-diverse opportunity list instead of the old EPIC price proxy", async () => {
    apiFetch.mockResolvedValue({
      status: "ready",
      basis: { evidence_count: 6, deep_analyzed_count: 0 },
      matrix: {
        focals: [
          { focal: "35mm", mm: 35, in_catalog: true, covered: false },
          { focal: "75mm", mm: 75, in_catalog: true, covered: false },
          { focal: "105mm", mm: 105, in_catalog: true, covered: true, video_count: 1, avg_views: 409 },
        ],
        product_lines: [],
      },
      covered: { status: "ready", focal_count: 1 },
      gaps: {
        status: "ready",
        recommendation_status: "ready",
        creator_context: {
          camera_body: "Sony FX3",
          mount: "Sony E/FE",
          mount_status: "known",
          mount_evidence: "profile gear: Sony FX3",
          lens_brands: ["Sigma"],
          content_lane: "hybrid",
          catalog_price_ceiling_proxy_usd: 1200,
          recommendation_stage: "profile_preliminary",
          deep_evidence_count: 0,
        },
        recommendations: [
          { sku: "evo-35-fe", focal: "35mm", product_name: "Viltrox AF 35mm F1.2 EVO FE", series: ["EVO"], mount: "Sony E/FE", price_usd: 999, confidence: "high", recommendation_score: 92, reasons: ["Sony E/FE 卡口兼容", "补齐 35mm"] },
          { sku: "pro-75-fe", focal: "75mm", product_name: "Viltrox AF 75mm F1.2 Pro FE", series: ["Pro"], mount: "Sony E/FE", price_usd: 899, confidence: "high", recommendation_score: 88, reasons: ["Sony E/FE 卡口兼容", "价格带匹配"] },
          { sku: "lab-35-fe", focal: "35mm", product_name: "Viltrox AF 35mm F1.2 LAB FE", series: ["LAB"], mount: "Sony E/FE", price_usd: 1099, confidence: "medium", recommendation_score: 83, reasons: ["混合创作适配"] },
        ],
      },
      matched_products: { status: "empty", reason: "未命中我方 SKU" },
    });

    render(<FocalMatrixPanel apiToken="tok" kolPoolId={42} />);

    expect(await screen.findByText("Viltrox AF 35mm F1.2 EVO FE")).toBeInTheDocument();
    expect(screen.getByText("Viltrox AF 75mm F1.2 Pro FE")).toBeInTheDocument();
    expect(screen.getByText("Viltrox AF 35mm F1.2 LAB FE")).toBeInTheDocument();
    expect(screen.getByText("Sony FX3")).toBeInTheDocument();
    expect(screen.getAllByText("Sony E/FE").length).toBeGreaterThan(0);
    expect(screen.getByText("常用 Sigma")).toBeInTheDocument();
    expect(screen.getByText("初步推荐")).toBeInTheDocument();
    expect(screen.queryByText(/EPIC 25mm\/35mm\/50mm/)).not.toBeInTheDocument();
    expect(screen.queryByText(/按官方 SKU 数×价格合计排序/)).not.toBeInTheDocument();
  });

  it("renders an honest empty state when the camera mount is unknown", async () => {
    apiFetch.mockResolvedValue({
      status: "ready",
      basis: { evidence_count: 1, deep_analyzed_count: 0 },
      matrix: { focals: [{ focal: "35mm", in_catalog: true, covered: false }], product_lines: [] },
      covered: { status: "ready", focal_count: 0 },
      gaps: {
        status: "ready",
        recommendation_status: "insufficient_evidence",
        recommendation_reason: "机身/卡口/常用镜头证据不足，暂不生成个性化 Top1。",
        creator_context: { mount_status: "unknown", recommendation_stage: "profile_preliminary" },
        recommendations: [],
      },
      matched_products: { status: "empty", reason: "未命中我方 SKU" },
    });

    render(<FocalMatrixPanel apiToken="tok" kolPoolId={43} />);

    expect(await screen.findByText("机身待补")).toBeInTheDocument();
    expect(screen.getByText("卡口待核验")).toBeInTheDocument();
    expect(screen.getByText("机身/卡口/常用镜头证据不足，暂不生成个性化 Top1。")).toBeInTheDocument();
  });
});
