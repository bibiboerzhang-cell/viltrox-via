import { describe, expect, it } from "vitest";

import { toCockpitKolPoolRows } from "./kolPoolRuntime";

describe("toCockpitKolPoolRows geo tier truth", () => {
  it("keeps a missing country unknown instead of labeling it CN", () => {
    const [row] = toCockpitKolPoolRows([{ id: 1, handle: "unknown-geo", country: "" } as any]);

    expect(row.country).toBe("");
    expect(row.geo_tier).toBeNull();
  });

  it("uses X only for mainland China", () => {
    const [china, usa] = toCockpitKolPoolRows([
      { id: 1, handle: "cn", country: "CN" },
      { id: 2, handle: "us", country: "US" },
    ] as any);

    expect(china.geo_tier).toBe("X");
    expect(usa.geo_tier).toBe("A");
  });
});

describe("toCockpitKolPoolRows fit score transport types", () => {
  it("keeps PostgreSQL numeric strings as real fit scores", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 3,
      handle: "decimal-fit",
      viltrox_fit_score: "95.000",
    } as any]);

    expect(row.v6_fit).toBe(95);
    expect(row.loyalty_score).toBe(0.95);
  });

  it("does not coerce blank or malformed score strings to zero", () => {
    const [blank, malformed] = toCockpitKolPoolRows([
      { id: 4, handle: "blank-fit", viltrox_fit_score: "  " },
      { id: 5, handle: "bad-fit", viltrox_fit_score: "not-a-score" },
    ] as any);

    expect(blank.v6_fit).toBeNull();
    expect(malformed.v6_fit).toBeNull();
  });
});
