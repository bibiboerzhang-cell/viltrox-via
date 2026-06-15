import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

vi.mock("../../../domains/attribution", () => ({
  getShopifyStatus: vi.fn().mockResolvedValue({ provider_status: "not_configured", env_vars: {}, sync_runs: [] }),
}));
vi.mock("../../../services/vkpi/shopifyHub-api", () => ({
  generatePromoLink: vi.fn().mockResolvedValue({}),
  getPromoAttributionSummary: vi.fn().mockResolvedValue({ rows: [] }),
  listPromoKols: vi.fn().mockResolvedValue({ kols: [] }),
  listPromoProducts: vi.fn().mockResolvedValue({ products: [] }),
  listSources: vi.fn().mockResolvedValue({ projects: [], events: [] }),
  saveShopifyCreds: vi.fn().mockResolvedValue({}),
}));
import { ShopifyHubPage } from "./ShopifyHubPage";

describe("ShopifyHubPage", () => {
  it("renders the 3-zone hub without crashing (guards the Card(...children)→{} legacyContext bug)", () => {
    render(React.createElement(ShopifyHubPage, { apiToken: "t" }));
    expect(screen.getByText("Shopify Hub")).toBeTruthy();
    expect(screen.getByText("① 连接 Shopify")).toBeTruthy();
  });
});
