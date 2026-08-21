import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import { analyzeKolPoolContentFit, getKolPoolContentFit } from "./kolPool-api.detail";

beforeEach(() => {
  apiFetch.mockReset().mockResolvedValue({});
});

describe("content-fit read and paid analysis boundary", () => {
  it("keeps GET cache-only with product scope", async () => {
    await getKolPoolContentFit("token", 42, { productSku: "AF-35-PRO" });

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/kol-pool/42/content-fit?product_sku=AF-35-PRO",
      { cache: "no-store" },
      "token",
    );
  });

  it("uses explicit POST for analysis", async () => {
    await analyzeKolPoolContentFit("token", 42, {
      force: true,
      productSku: "AF-35-PRO",
    });

    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/kol-pool/42/content-fit/analyze");
    expect(init).toMatchObject({ method: "POST", cache: "no-store" });
    expect(JSON.parse(init.body)).toEqual({
      force: true,
      product_sku: "AF-35-PRO",
    });
    expect(token).toBe("token");
  });
});
