import { describe, expect, it } from "vitest";

import {
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
