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

// 抓取器原始载荷(raw_platform_data 及其嵌套 raw)是 provider 说的话,不是我们的列。
// 它得来的语言最多只能说到「来源不明」——「他自己填的」这句话必须由本地列/后端裁决来说。
describe("toCockpitKolPoolRows language provenance source tiering", () => {
  it("never calls a scraper-payload language self-reported", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 1,
      handle: "provider-only",
      raw_platform_data: { language: "ja" },
    }] as any);

    expect(row.language_provenance.origin).toBe("projected");
    expect(row.language_provenance.originLabel).toBe("来源不明");
    expect(row.language_provenance.codes).toEqual(["ja"]);
    // 「他自己填的是……」这半句一个字都不许出现。
    expect(row.language_provenance.selfReportedCodes).toEqual([]);
    expect(row.language_provenance.divergenceLabel).toBe("");
  });

  it("treats the payload's own nested raw the same way", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 2,
      handle: "nested-provider-only",
      raw_platform_data: { raw: { language: "ko" } },
    }] as any);

    expect(row.language_provenance.origin).toBe("projected");
    expect(row.language_provenance.selfReportedCodes).toEqual([]);
  });

  it("does not launder a payload value into the self-reported bucket beside a local column", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 3,
      handle: "local-plus-provider",
      language: "en",
      raw_platform_data: { language: "ja" },
    }] as any);

    // 本地列说得出话 → 照它说的,载荷根本不参与。
    expect(row.language_provenance.codes).toEqual(["en"]);
    // 但「他自己填的是英语」这半句**没有人说过**:一个裸的 language 列只证明
    // 「资料里有这个值」,证不出是他自己填的。没有裁决的路上不许落自报。
    expect(row.language_provenance.origin).toBe("projected");
    expect(row.language_provenance.originLabel).toBe("来源不明");
    expect(row.language_provenance.selfReportedCodes).toEqual([]);
  });

  // 行上的 language_origin / language_source 是**原料上的记号**(后端把它当输入读),
  // 后端从不把裁决写到行上 —— 裁决落在 qualification_evidence.language 那个块里。
  it("keeps a backend verdict verbatim instead of letting the payload demote it", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 4,
      handle: "verdict-wins",
      language: {
        values: ["de"], origin: "inferred", inferred: true, inferred_values: ["de"],
        self_reported: false, self_reported_values: [], projected_values: [],
        source: "vkpi_kol_pool.language_inferred", evidence_fields: ["bio"],
      },
      raw_platform_data: { language: "ja" },
    }] as any);

    expect(row.language_provenance.hasServerVerdict).toBe(true);
    expect(row.language_provenance.origin).toBe("inferred");
    expect(row.language_provenance.codes).toEqual(["de"]);
  });

  it("treats a flat language_origin marker as raw material, not as a verdict", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 8,
      handle: "flat-origin-marker",
      language: "de",
      language_origin: "inferred",
      language_source: "content_inference",
      raw_platform_data: { language: "ja" },
    }] as any);

    // 记号读不出裁决 → 走降级路径:值照显示,归属只说得到「来源不明」。
    expect(row.language_provenance.hasServerVerdict).toBe(false);
    expect(row.language_provenance.origin).toBe("projected");
    expect(row.language_provenance.codes).toEqual(["de"]);
    expect(row.language_provenance.selfReportedCodes).toEqual([]);
  });

  // 红线:有裁决时照裁决渲染。裁决说未知就是未知 —— 不许改用载荷的值把它显示成别的档。
  it("keeps an unknown verdict unknown even when the payload still carries a language", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 9,
      handle: "unknown-verdict-plus-payload",
      language: {
        values: [], origin: "unknown", inferred: false, inferred_values: [],
        self_reported: false, self_reported_values: [], projected_values: [], source: "",
      },
      raw_platform_data: { language: "ja" },
    }] as any);

    expect(row.language_provenance.hasServerVerdict).toBe(true);
    expect(row.language_provenance.origin).toBe("unknown");
    expect(row.language_provenance.displayLabel).toBe("未知");
    expect(row.language_provenance.codes).toEqual([]);
  });

  // 第五种形态在池行上也不许升格:把握度没过门槛的那一票算未知,不挂「推断」。
  it("does not promote a below-floor inferred column into an inference", () => {
    const [withheld, admitted] = toCockpitKolPoolRows([{
      id: 10,
      handle: "below-floor",
      language_inferred: "ko",
      language_inferred_source: "video_titles",
      language_inferred_confidence: "low",
    }, {
      id: 11,
      handle: "above-floor",
      language_inferred: "ko",
      language_inferred_source: "video_titles",
      language_inferred_confidence: "high",
    }] as any);

    expect(withheld.language_provenance.origin).toBe("unknown");
    expect(withheld.language_provenance.inferenceWithheld).toBe(true);
    expect(withheld.language_provenance.codes).toEqual([]);
    expect(admitted.language_provenance.origin).toBe("inferred");
    expect(admitted.language_provenance.codes).toEqual(["ko"]);
  });

  it("does not promote a payload placeholder into a value", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 5,
      handle: "provider-placeholder",
      raw_platform_data: { language: "Unknown" },
    }] as any);

    expect(row.language_provenance.origin).toBe("unknown");
    expect(row.language_provenance.codes).toEqual([]);
  });

  it("stays unknown when neither our columns nor the payload carry a language", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 6,
      handle: "nothing-anywhere",
      raw_platform_data: { country: "JP" },
    }] as any);

    expect(row.language_provenance.origin).toBe("unknown");
    expect(row.language_provenance.originLabel).toBe("未知");
  });

  it("refuses to call a payload-side inference our own inference", () => {
    const [row] = toCockpitKolPoolRows([{
      id: 7,
      handle: "provider-inference",
      raw_platform_data: { language_evidence: { value: "th", source: "provider_detected", inferred: true } },
    }] as any);

    expect(row.language_provenance.origin).toBe("projected");
    expect(row.language_provenance.inferredCodes).toEqual([]);
  });
});
