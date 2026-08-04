import { describe, expect, it } from "vitest";

import {
  candidateBusinessLane,
  hasExplicitBusinessLanes,
  recallDisplayCounts,
  resolvedProductSkuFromPlan,
  withResolvedProductSku,
} from "./SmartKolInputPanel.TextResult";

describe("SmartKolInputPanel product scope propagation", () => {
  it("reads the exact resolved catalog SKU and attaches it without mutating the candidate", () => {
    const item = { kol_pool_id: 42, handle: "creator" };

    const sku = resolvedProductSkuFromPlan({
      resolved_product: { sku: "AF-35MM-F18-PRO-FE" },
    });
    const scoped = withResolvedProductSku(item, sku);

    expect(sku).toBe("AF-35MM-F18-PRO-FE");
    expect(scoped).toEqual({ ...item, product_sku: "AF-35MM-F18-PRO-FE" });
    expect(item).not.toHaveProperty("product_sku");
  });

  it("does not invent a product scope when the planner did not resolve one", () => {
    const item = { kol_pool_id: 42 };
    expect(withResolvedProductSku(item, resolvedProductSkuFromPlan({}))).toBe(item);
  });
});

describe("SmartKolInputPanel candidate business lanes", () => {
  it("prefers the explicit backend lane and keeps backfill separate", () => {
    expect(candidateBusinessLane({ candidate_bucket: "core_vertical", bucket: "creator" } as any)).toBe("core");
    expect(candidateBusinessLane({ candidate_bucket: "expansion", bucket: "reviewer" } as any)).toBe("expansion");
    expect(candidateBusinessLane({ candidate_bucket: "core_vertical", match_tier: "backfill" } as any)).toBe("exploration");
  });

  it("labels old-service mapping as compatibility instead of claiming new business lanes", () => {
    const legacy = [{ bucket: "reviewer" }, { bucket: "creator" }] as any[];
    expect(hasExplicitBusinessLanes(legacy)).toBe(false);
    expect(candidateBusinessLane(legacy[0] as any)).toBe("core");
    expect(candidateBusinessLane(legacy[1] as any)).toBe("expansion");
  });

  it("uses the 30 visible filtered candidates when a sparse polling snapshot reports zero counts", () => {
    const items = Array.from({ length: 30 }, (_, index) => ({
      kol_pool_id: index + 1,
      bucket: index < 6 ? "creator" : "reviewer",
    })) as any[];

    expect(recallDisplayCounts(items, {
      candidate_count: 0,
      creator_returned: 0,
      reviewer_returned: 0,
      final_count: 30,
      returned_count: 30,
      requested_count: 30,
    })).toEqual({ total: 30, creator: 6, reviewer: 24 });
  });
});
