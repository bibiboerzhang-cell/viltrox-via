import { describe, expect, it } from "vitest";
import { resolveDashboardMapSelection } from "./mapViewSelection";

describe("resolveDashboardMapSelection", () => {
  it("selects the first non-empty real source in KOL, Dealer, Events order", () => {
    expect(resolveDashboardMapSelection(null, {
      kols: { available: true },
      dealers: { available: true },
      events: { available: true },
    }).mode).toBe("kols");

    expect(resolveDashboardMapSelection(null, {
      kols: { available: false },
      dealers: { available: true },
      events: { available: true },
    }).mode).toBe("dealers");

    expect(resolveDashboardMapSelection(null, {
      kols: { available: false },
      dealers: { available: false },
      events: { available: true },
    }).mode).toBe("events");
  });

  it("retains a valid user choice across refresh even when a higher-priority source exists", () => {
    expect(resolveDashboardMapSelection("dealers", {
      kols: { available: true },
      dealers: { available: true },
      events: { available: true },
    })).toEqual({ mode: "dealers", pending: false, allUnavailable: false, error: "" });
  });

  it("waits for an unresolved higher-priority source instead of briefly selecting another source", () => {
    expect(resolveDashboardMapSelection(null, {
      kols: { available: false, loading: true },
      dealers: { available: true },
      events: { available: true },
    })).toEqual({ mode: null, pending: true, allUnavailable: false, error: "" });

    expect(resolveDashboardMapSelection("dealers", {
      kols: { available: true },
      dealers: { available: false, loading: true },
      events: { available: true },
    })).toEqual({ mode: "dealers", pending: true, allUnavailable: false, error: "" });
  });

  it("returns the chooser state only when every real source is settled and empty", () => {
    expect(resolveDashboardMapSelection(null, {
      kols: { available: false },
      dealers: { available: false },
      events: { available: false },
    })).toEqual({ mode: null, pending: false, allUnavailable: true, error: "" });
  });

  it("keeps settled source errors distinct from an empty result", () => {
    expect(resolveDashboardMapSelection(null, {
      kols: { available: false, error: "KOL failed" },
      dealers: { available: false, error: "Dealer failed" },
      events: { available: false },
    })).toEqual({
      mode: null,
      pending: false,
      allUnavailable: true,
      error: "KOL failed | Dealer failed",
    });
  });
});
