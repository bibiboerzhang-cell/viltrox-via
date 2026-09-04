import { describe, expect, it } from "vitest";

import {
  candidateBusinessLane,
  hasExplicitBusinessLanes,
  recallDisplayCounts,
  recallReturnedCount,
  resolvedProductContextFromPlan,
  resolvedProductSkuFromPlan,
  withResolvedProductContext,
  withResolvedProductSku,
} from "./SmartKolInputPanel.TextResult";
import { recallDistributionView } from "./SmartKolInputPanel.evidence";

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

  it("keeps a no-SKU focal family as family context when a creator detail is opened", () => {
    const item = {
      kol_pool_id: 42,
      handle: "family_creator",
      product_sku: "OLD-HISTORICAL-SKU",
      productSku: "OLD-HISTORICAL-CAMEL-SKU",
      productFamily: "family:OLD-CAMEL",
      productFamilyName: "Old camel family",
    };
    const context = resolvedProductContextFromPlan({
      resolved_product: {
        sku: "",
        model_name: "Viltrox 35mm F1.2 family",
        series: "LAB",
        focal_family_skus: ["AF-35-LAB-FE", "AF-35-LAB-Z"],
      },
    });
    const scoped = withResolvedProductContext(item, context);

    expect(context).toMatchObject({
      kind: "product_family",
      identity: "family:Viltrox 35mm F1.2 family",
      label: "Viltrox 35mm F1.2 family",
      candidate_skus: ["AF-35-LAB-FE", "AF-35-LAB-Z"],
    });
    expect(scoped).toMatchObject({
      product_family: "family:Viltrox 35mm F1.2 family",
      product_family_name: "Viltrox 35mm F1.2 family",
      product_identity: "family:Viltrox 35mm F1.2 family",
    });
    expect(scoped).not.toHaveProperty("product_sku");
    expect(scoped).not.toHaveProperty("productSku");
    expect(scoped).not.toHaveProperty("productFamily");
    expect(scoped).not.toHaveProperty("productFamilyName");
    expect(item.product_sku).toBe("OLD-HISTORICAL-SKU");
  });

  it("lets an exact current SKU replace stale family metadata without mutating the candidate", () => {
    const item = {
      kol_pool_id: 43,
      productSku: "OLD-CAMEL-SKU",
      product_family: "family:OLD",
      product_family_name: "Old family",
      productFamily: "family:OLD-CAMEL",
      productFamilyName: "Old camel family",
    };
    const context = resolvedProductContextFromPlan({
      resolved_product: {
        sku: "AF-35MM-F12-LAB-FE",
        model_name: "AF 35mm F1.2 LAB FE",
      },
    });
    const scoped = withResolvedProductContext(item, context);

    expect(scoped).toMatchObject({
      product_sku: "AF-35MM-F12-LAB-FE",
      product_identity: "AF-35MM-F12-LAB-FE",
    });
    expect(scoped).not.toHaveProperty("product_family");
    expect(scoped).not.toHaveProperty("product_family_name");
    expect(scoped).not.toHaveProperty("productSku");
    expect(scoped).not.toHaveProperty("productFamily");
    expect(scoped).not.toHaveProperty("productFamilyName");
    expect(item.product_family).toBe("family:OLD");
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

describe("SmartKolInputPanel truthful recall counts", () => {
  const result = (candidateCount: number, returnedCount?: number) => ({
    diagnostics: { candidate_count: candidateCount, returned_count: returnedCount },
  }) as any;

  it("uses the post-evidence returned count instead of the raw candidate pool", () => {
    expect(recallReturnedCount(result(100, 3), [{}, {}, {}] as any)).toBe(3);
    expect(recallReturnedCount(result(100, 0), [] as any)).toBe(0);
  });

  it("falls back to the rendered rows for older session payloads", () => {
    expect(recallReturnedCount(result(100), [{}, {}, {}] as any)).toBe(3);
  });
});

describe("SmartKolInputPanel descriptive candidate distribution", () => {
  it("renders only a denominator-bound descriptive candidate set", () => {
    expect(recallDistributionView({
      claim_status: "descriptive_only",
      denominator: 3,
      facets: {
        platform: { youtube: 2, instagram: 1 },
        country: { us: 2, unknown: 1 },
        language: { en: 3 },
        profile_type: { creator: 2, reviewer: 1 },
        contact_available: { yes: 1, no: 2 },
        video_evidence: { yes: 3 },
      },
    })).toMatchObject({
      denominator: 3,
      chips: expect.arrayContaining([
        { dimension: "country", label: "市场 US", count: 2 },
        { dimension: "contact_available", label: "联系方式 有", count: 1 },
      ]),
    });
  });

  it("rejects a distribution whose facet counts do not equal its denominator", () => {
    expect(recallDistributionView({
      claim_status: "descriptive_only",
      denominator: 3,
      facets: { platform: { youtube: 2 } },
    })).toBeNull();
  });
});
