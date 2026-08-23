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

describe("toCockpitKolPoolRows audience geo truth (波 C·C3)", () => {
  it("creator country no longer masquerades as audience geo: no geo breakdown → empty distribution", () => {
    const [row] = toCockpitKolPoolRows([{ id: 1, handle: "us-creator", country: "US" } as any]);

    expect(row.country).toBe("US");
    expect(row.geo_distribution).toEqual([]);
    expect(row.audience_geo).toBeNull();
  });

  it("insufficient_sample → empty distribution + honest meta (n/min_required)", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 2,
      handle: "few-comments",
      country: "US",
      audience_estimated: {
        sample_size: 40,
        // 旧口径的语言→市场假地理,必须被忽略
        top_countries: [{ code: "US", pct: 79 }],
        geo: { method: "insufficient_sample", sample_n: 40, determined_n: 7, min_required: 30, confidence: 0, top_countries: [], note: "不足" },
      },
    } as any]);

    expect(row.geo_distribution).toEqual([]);
    expect(row.audience_geo).toMatchObject({ method: "insufficient_sample", determined_n: 7, min_required: 30 });
  });

  it("commenter_country_v1 → top_countries pct → share 0-1", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 3,
      handle: "well-sampled",
      country: "CN",
      audience_estimated: {
        sample_size: 300,
        geo: { method: "commenter_country_v1", sample_n: 300, determined_n: 120, min_required: 30, confidence: 0.6, top_countries: [{ code: "US", pct: 55.5 }, { code: "de", pct: 20 }] },
      },
    } as any]);

    expect(row.geo_distribution).toEqual([{ country: "US", share: 0.555 }, { country: "DE", share: 0.2 }]);
    expect(row.audience_geo?.method).toBe("commenter_country_v1");
    // 创作者国别仍走 country / geo_tier,不混进受众地理
    expect(row.geo_tier).toBe("X");
  });
});

describe("toCockpitKolPoolRows employee contact projection", () => {
  it("keeps a full list projection intact for the authorized employee UI", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 2,
      handle: "creator",
      email: "manager@example.com",
      contact_masked: false,
    } as any]);

    expect(row.email).toBe("manager@example.com");
    expect(row.contact_masked).toBe(false);
  });

  it("keeps an older masked projection marked for modal compatibility reveal", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 3,
      handle: "creator",
      email: "m***@e***",
      contact_masked: true,
    } as any]);

    expect(row.email).toBe("m***@e***");
    expect(row.contact_masked).toBe(true);
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

describe("toCockpitKolPoolRows engagement truth labels", () => {
  it("never promotes a generic engagement rate to Real ER", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 20,
      handle: "generic-er",
      engagement_rate: 0.043,
    } as any]);

    expect(row.real_er_pct).toBeNull();
    expect(row.real_er_verified).toBe(false);
    expect(row.engagement_rate).toBe(4.3);
    expect(row.engagement_rate_displayable).toBe(false);
  });

  it("allows a generic engagement rate only when source and update time are present", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 21,
      handle: "sourced-er",
      engagement_rate: 4.3,
      source_type: "youtube_api",
      updated_at: "2026-08-01T12:00:00Z",
    } as any]);

    expect(row.real_er_pct).toBeNull();
    expect(row.engagement_rate).toBe(4.3);
    expect(row.engagement_rate_source).toBe("youtube_api");
    expect(row.engagement_rate_updated_at).toBe("2026-08-01T12:00:00Z");
    expect(row.engagement_rate_displayable).toBe(true);
  });

  it("accepts explicit Real ER only with a positive sample", () => {
    const [sampled, unsampled] = toCockpitKolPoolRows([{
      id: 22,
      handle: "sampled-real-er",
      real_er: 0.021,
      real_er_sample_n: 40,
      engagement_rate: 3.8,
    }, {
      id: 23,
      handle: "unsampled-real-er",
      real_er_pct: 2.6,
    }] as any);

    expect(sampled.real_er_pct).toBe(2.1);
    expect(sampled.real_er_sample_n).toBe(40);
    expect(sampled.real_er_verified).toBe(true);
    expect(sampled.engagement_rate).toBe(3.8);
    expect(unsampled.real_er_pct).toBeNull();
  });

  it("accepts an explicit backend verified truth without a sample count", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 24,
      handle: "verified-real-er",
      real_er_pct: 2.6,
      real_er_verified: true,
    }] as any);

    expect(row.real_er_pct).toBe(2.6);
    expect(row.real_er_sample_n).toBeNull();
    expect(row.real_er_verified).toBe(true);
  });
});
