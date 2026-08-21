import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  generate: vi.fn(),
  commission: vi.fn(),
  coupon: vi.fn(),
}));

vi.mock("../../../services/vkpi/goaffpro-api", () => ({
  getKolGoaffproLink: api.get,
  generateKolGoaffproLink: api.generate,
  updateKolCommission: api.commission,
  updateKolCoupon: api.coupon,
}));

import { GoaffproLinkSection } from "./GoaffproLinkSection";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("GoaffproLinkSection shared read-only boundary", () => {
  it("does not expose generation when a shared KOL has no mapping", async () => {
    api.get.mockResolvedValue({ linked: false, kol_pool_id: 73 });

    render(<GoaffproLinkSection apiToken="token" kolPoolId={73} readOnly />);

    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/共享 KOL 为只读/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /生成追踪链/ })).not.toBeInTheDocument();
    expect(api.generate).not.toHaveBeenCalled();
  });

  it("shows a persisted link but hides regenerate, coupon, and commission writes", async () => {
    api.get.mockResolvedValue({
      linked: true,
      kol_pool_id: 73,
      affiliate_id: "aff-73",
      ref_code: "BOUNDARY73",
      tracking_url: "https://store.test/?ref=BOUNDARY73",
      coupon: "BOUNDARY10",
      commission_rate: "10%",
      needs_regenerate: false,
    });

    render(<GoaffproLinkSection apiToken="token" kolPoolId={73} readOnly />);

    await screen.findByText("GOAFFPRO 追踪链已就绪");
    expect(screen.getByDisplayValue("https://store.test/?ref=BOUNDARY73")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /重新生成/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "改码" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "设码" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "调整" })).not.toBeInTheDocument();
    expect(api.generate).not.toHaveBeenCalled();
    expect(api.commission).not.toHaveBeenCalled();
    expect(api.coupon).not.toHaveBeenCalled();
  });
});
