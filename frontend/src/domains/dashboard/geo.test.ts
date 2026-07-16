import { describe, it, expect } from "vitest";
import {
  countryCentroid,
  eventCoords,
  normalizeEventsHierarchy,
  normalizeDealersHierarchy,
  normalizeMapHierarchy,
} from "./geo";

// frontend-domainize 第一刀:cockpit normalizers 的地理/地图层级纯逻辑下沉到此。
// 行为应与原 normalizers 一致;涉及 jitter 的只断言字段存在/类型,不断言精确值。

describe("countryCentroid", () => {
  it("UK 归一为 GB 质心", () => {
    expect(countryCentroid("UK")).toEqual(countryCentroid("GB"));
  });
  it("未知代码 → null", () => {
    expect(countryCentroid("ZZ")).toBeNull();
  });
});

describe("eventCoords 落点逻辑", () => {
  it("显式 lat/lng 直接返回", () => {
    expect(eventCoords("US", 12.5, -34.2)).toEqual({ lat: 12.5, lng: -34.2 });
  });
  it("无经纬 + 无可识别国家 → null", () => {
    expect(eventCoords("", null, null)).toBeNull();
    expect(eventCoords("ZZ", null, null)).toBeNull();
  });
  it("只有国家不得伪造精确点", () => {
    expect(eventCoords("US", null, null, "seed")).toBeNull();
  });
});

describe("normalizeEventsHierarchy", () => {
  it("无定位活动 → null", () => {
    expect(normalizeEventsHierarchy([{ name: "x" }])).toBeNull();
  });
  it("只有 location_country 仍不上点位层", () => {
    const out = normalizeEventsHierarchy([
      { location_country: "us" },
      { location_country: "US" },
    ]);
    expect(out).toBeNull();
  });
  it("精确经纬度才上图,count 累加", () => {
    const out = normalizeEventsHierarchy([
      { location_country: "US", location_lat: 40, location_lng: -74, location_city: "NYC" },
      { location_country: "US", location_lat: 34, location_lng: -118, location_city: "LA" },
    ]);
    expect(out).not.toBeNull();
    expect(out!.US.count).toBe(2);
    expect(out!.US.mapPrecision).toBe("exact_coordinates");
  });
});

describe("normalizeDealersHierarchy", () => {
  it("无经纬 → null", () => {
    expect(normalizeDealersHierarchy([{ city: "NoGeo" }])).toBeNull();
  });
  it("有经纬 → 归并到 US,城市分桶", () => {
    const out = normalizeDealersHierarchy([{ lat: 40, lng: -74, city: "NYC" }]);
    expect(out).not.toBeNull();
    expect(out!.US.count).toBe(1);
    expect(out!.US.cities.NYC.count).toBe(1);
  });
});

describe("normalizeMapHierarchy", () => {
  it("空输入 → null", () => {
    expect(normalizeMapHierarchy({}, [])).toBeNull();
  });
  it("国家分布 → 层级,KOL pins 含 viltrox_fit_score 展示(只读不写)", () => {
    const out = normalizeMapHierarchy(
      {
        resource: "dashboard.kol_distribution_pack",
        schema_version: 1,
        is_real: true,
        stats: { mapped_kol_count: 1 },
        countries: [
          {
            code: "US",
            count: 1,
            cities: { NYC: { lat: 40, lng: -74, count: 1, sample_kols: [{ id: 1, handle: "@a", viltrox_fit_score: 88 }] } },
          },
        ],
      },
      [],
    );
    expect(out).not.toBeNull();
    expect(out!.US.cities.NYC.kols[0].engagement).toBe("Fit 88");
  });

  it("有效服务端 pack 是唯一来源,不与 kolRows 双重合并", () => {
    const out = normalizeMapHierarchy(
      {
        resource: "dashboard.kol_distribution_pack",
        schema_version: 1,
        is_real: true,
        scope: { mode: "global" },
        stats: { mapped_kol_count: 2 },
        countries: [
          {
            code: "US",
            lat: 39.8,
            lng: -98.6,
            count: 2,
            cities: [{ name: "NYC", lat: 40, lng: -74, count: 2, sample_kols: [] }],
          },
        ],
      },
      [
        { id: 1, country: "US", city: "NYC" },
        { id: 2, country: "DE", city: "Berlin" },
      ],
    );

    expect(out).not.toBeNull();
    expect(out!.US.count).toBe(2);
    expect(out!.US.cities.NYC.count).toBe(2);
    expect(out!.DE).toBeUndefined();
  });

  it("pack 缺失时才用 kolRows fallback,并且每行只计一次", () => {
    const out = normalizeMapHierarchy({}, [
      { id: 1, country: "US", city: "NYC" },
      { id: 2, country: "US", city: "NYC" },
      { id: 3, country: "DE", city: "Berlin" },
    ]);

    expect(out).not.toBeNull();
    expect(out!.US.count).toBe(2);
    expect(out!.US.cities.NYC.count).toBe(2);
    expect(out!.DE.count).toBe(1);
  });

  it("is_real=false 或契约损坏的 pack 使用 kolRows fallback", () => {
    const out = normalizeMapHierarchy(
      {
        resource: "dashboard.kol_distribution_pack",
        schema_version: 1,
        is_real: false,
        countries: [{ code: "US", lat: 39.8, lng: -98.6, count: 99 }],
      },
      [{ id: 1, country: "DE", city: "Berlin" }],
    );

    expect(out).not.toBeNull();
    expect(out!.US).toBeUndefined();
    expect(out!.DE.count).toBe(1);
  });

  it("有效空 pack 保留服务端空结论,不用 kolRows 改写分母", () => {
    const out = normalizeMapHierarchy(
      {
        resource: "dashboard.kol_distribution_pack",
        schema_version: 1,
        is_real: true,
        stats: { mapped_kol_count: 0 },
        countries: [],
      },
      [{ id: 1, country: "US", city: "NYC" }],
    );

    expect(out).toBeNull();
  });
});
