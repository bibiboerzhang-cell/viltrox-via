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
  it("有国家 → 质心 + jitter,lat/lng 为 number", () => {
    const coords = eventCoords("US", null, null, "seed");
    expect(coords).not.toBeNull();
    expect(typeof coords!.lat).toBe("number");
    expect(typeof coords!.lng).toBe("number");
  });
});

describe("normalizeEventsHierarchy", () => {
  it("无定位活动 → null", () => {
    expect(normalizeEventsHierarchy([{ name: "x" }])).toBeNull();
  });
  it("带 location_country → 上图,count 累加", () => {
    const out = normalizeEventsHierarchy([
      { location_country: "us" },
      { location_country: "US" },
    ]);
    expect(out).not.toBeNull();
    expect(out!.US.count).toBe(2);
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
});
