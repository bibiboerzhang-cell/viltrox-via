// Dashboard geo / map-hierarchy domain module.
//
// 纯逻辑簇:把 cockpit normalizers 里「国家质心 + KOL/活动/经销商地图层级归一化」
// 从组件目录下沉到 domains/dashboard。行为与原 normalizers.ts 中对应函数逐字一致
// (私有原语 record/list/number/int/compact/jitter 在此自带副本,不复用别处的不同语义实现)。
//
// 公开导出:countryCentroid / normalizeKolPins / normalizeMapHierarchy /
// normalizeEventsHierarchy / normalizeDealersHierarchy / eventCoords。
// 红线:viltrox_fit_score 仅作展示读取(`engagement: Fit …`),绝不写回。
import { getCountryInfo } from "../../components/vkpi/cockpit/data/countryInfo";

const DASH = "—";

const COUNTRY_CENTROIDS = {
  AE: [24.0, 54.0], AT: [47.6, 14.1], AU: [-25.3, 133.8], BE: [50.5, 4.5],
  BR: [-14.2, -51.9], CA: [56.1, -106.3], CH: [46.8, 8.2], CN: [35.9, 104.2],
  DE: [51.2, 10.5], ES: [40.5, -3.7], FR: [46.2, 2.2], GB: [55.4, -3.4],
  HK: [22.3, 114.2], ID: [-0.8, 113.9], IE: [53.4, -8.2], IN: [20.6, 78.9],
  IT: [41.9, 12.6], JP: [36.2, 138.3], KR: [36.5, 127.8], MX: [23.6, -102.5],
  MY: [4.2, 101.9], NL: [52.1, 5.3], NZ: [-40.9, 174.9], PH: [12.9, 121.8],
  RU: [61.5, 105.3], SE: [60.1, 18.6], SG: [1.4, 103.8], TH: [15.9, 101.0],
  TW: [23.7, 121.0], UK: [55.4, -3.4], US: [39.8, -98.6], VN: [14.1, 108.3],
  ZA: [-30.6, 22.9],
};

function record(value: any): any {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function list(value: any): any[] {
  return Array.isArray(value) ? value : [];
}

function number(value: any) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function int(value: any) {
  const parsed = number(value);
  return parsed == null ? null : Math.round(parsed);
}

function compact(value: any) {
  const n = number(value);
  if (n == null) return DASH;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}

function jitter(seed: any, salt: any, spread = 0.18) {
  const raw = String(seed || "");
  let hash = salt * 997;
  for (const char of raw) hash = (hash * 31 + char.charCodeAt(0)) % 100000;
  return ((hash / 100000) - 0.5) * spread;
}

export function countryCentroid(code: any) {
  const normalized = code === "UK" ? "GB" : code;
  const pair = (COUNTRY_CENTROIDS as any)[normalized] || (COUNTRY_CENTROIDS as any)[code];
  return pair ? { lat: pair[0], lng: pair[1] } : null;
}

function asCityEntries(value: any): any[] {
  if (Array.isArray(value)) {
    return value
      .map((item: any) => {
        const row = record(item);
        const name = String(row.name || row.city || row.label || "");
        return name ? [name, row] : null;
      })
      .filter(Boolean);
  }
  return Object.entries(record(value));
}

function isUsableDistributionCountry(value: any) {
  const item = record(value);
  const code = String(item.code || item.country_code || item.name || "").trim().toUpperCase();
  if (!code) return false;
  const centroid = countryCentroid(code);
  const lat = number(item.lat) ?? number(item.latitude) ?? centroid?.lat ?? null;
  const lng = number(item.lng) ?? number(item.longitude) ?? centroid?.lng ?? null;
  const count = int(item.count) ?? int(item.kol_count);
  return lat != null && lng != null && count != null && count >= 0;
}

// Server distribution-pack is authoritative when its versioned contract is intact.
// An explicit, real empty pack (mapped_kol_count=0) is also authoritative: falling
// back to the independently fetched Pool rows would turn a verified empty result
// into a different denominator.  Missing/malformed/non-real packs fall back to rows.
function isValidServerDistributionPack(value: any) {
  const pack = record(value);
  if (String(pack.resource || "") !== "dashboard.kol_distribution_pack") return false;
  const schemaVersion = int(pack.schema_version);
  if (schemaVersion == null || schemaVersion < 1 || pack.is_real !== true) return false;
  if (!Array.isArray(pack.countries)) return false;
  if (pack.countries.length > 0) return pack.countries.every(isUsableDistributionCountry);
  return (int(record(pack.stats).mapped_kol_count) ?? -1) === 0;
}

export function normalizeKolPins(rows: any, cityLat?: any, cityLng?: any) {
  return list(rows).map((raw: any, index: number) => {
    const item = record(raw);
    const seed = item.id || item.handle || item.display_name || index;
    const followers = int(item.followers);
    const views = int(item.avg_views);
    const fit = int(item.viltrox_fit_score);
    const source = String(item.location_source || "");
    const topic = String(item.primary_topic || item.content_style || item.platform || "真实 KOL Pool");
    return {
      id: item.id,
      handle: String(item.handle || item.display_name || `KOL ${index + 1}`),
      name: String(item.display_name || item.handle || `KOL ${index + 1}`),
      platform: String(item.platform || "kol-pool"),
      niche: source === "country_scatter" ? `${topic} · 国家级散点` : topic,
      engagement: fit != null ? `Fit ${fit}` : views != null ? `曝光 ${compact(views)}` : followers != null ? `粉丝 ${compact(followers)}` : "待评估",
      followers: followers != null ? compact(followers) : DASH,
      lat: cityLat + jitter(seed, 1),
      lng: cityLng + jitter(seed, 2),
      venues: [],
      raw: item,
    };
  });
}

export function normalizeMapHierarchy(distribution: any = {}, kolRows: any = []) {
  const useServerDistribution = isValidServerDistributionPack(distribution);
  const countries = useServerDistribution ? list(record(distribution).countries) : [];
  const hierarchy: Record<string, any> = {};
  for (const row of countries) {
    const item = record(row);
    const code = String(item.code || item.country_code || item.name || "");
    if (!code) continue;
    const centroid = countryCentroid(code) || { lat: 0, lng: 0 };
    hierarchy[code] = {
      lat: number(item.lat) || number(item.latitude) || centroid.lat,
      lng: number(item.lng) || number(item.longitude) || centroid.lng,
      count: int(item.count) || int(item.kol_count) || 0,
      revenue: item.exposure ? compact(item.exposure) : "",
      cities: {},
    };
    for (const [cityName, cityRaw] of asCityEntries(item.cities)) {
      const cityItem = record(cityRaw);
      const lat = number(cityItem.lat) || number(cityItem.latitude);
      const lng = number(cityItem.lng) || number(cityItem.longitude);
      if (lat == null || lng == null) continue;
      hierarchy[code].cities[cityName] = {
        lat,
        lng,
        count: int(cityItem.count) || int(cityItem.kol_count) || 0,
        revenue: cityItem.exposure ? compact(cityItem.exposure) : "",
        kols: normalizeKolPins(cityItem.sample_kols || cityItem.kols, lat, lng),
        raw: cityItem,
      };
    }
  }

  if (!useServerDistribution) {
    for (const row of list(kolRows)) {
      const item = record(row);
      const rawCode = String(item.country || list(item.geo_distribution)[0]?.country || "").trim().toUpperCase();
      const countryInfo = getCountryInfo(rawCode);
      const code = countryInfo?.code || rawCode;
      const cityName = String(item.city || item.location_city || item.location || "国家分布").trim();
      const centroid = countryCentroid(code);
      const lat = number(item.lat) || number(item.latitude) || centroid?.lat;
      const lng = number(item.lng) || number(item.longitude) || centroid?.lng;
      if (!code || !cityName || lat == null || lng == null) continue;
      if (!hierarchy[code]) {
        hierarchy[code] = { lat, lng, count: 0, revenue: "", cities: {} };
      }
      const bucket = hierarchy[code].cities[cityName] || {
        lat: lat + jitter(item.id || item.handle, 21, 0.8),
        lng: lng + jitter(item.id || item.handle, 22, 0.8),
        count: 0,
        revenue: "",
        kols: [],
        raw: {},
      };
      bucket.count += 1;
      bucket.kols.push(...normalizeKolPins([item], bucket.lat, bucket.lng));
      hierarchy[code].cities[cityName] = bucket;
      hierarchy[code].count = (Object.values(hierarchy[code].cities || {}) as any[])
        .reduce((sum: number, city: any) => sum + (int(city.count) || 0), 0);
    }
  }
  return Object.keys(hierarchy).length ? hierarchy : null;
}

// 真实活动 → 地图层(与 KOL 同形:{country:{lat,lng,count,cities}})。
// 点位层只接受显式 lat/lng。location_country 可用于其他国家聚合统计，
// 但不在这里用国家质心伪造精确活动点。
export function normalizeEventsHierarchy(eventRows: any = []) {
  const hierarchy: Record<string, any> = {};
  for (const raw of list(eventRows)) {
    const ev = record(raw);
    const code = String(ev.location_country || "").trim().toUpperCase();
    const lat = number(ev.location_lat);
    const lng = number(ev.location_lng);
    if (lat == null || lng == null) continue;
    const key = code || `${lat},${lng}`;
    if (!hierarchy[key]) hierarchy[key] = { lat, lng, count: 0, revenue: "", cities: {}, mapPrecision: "exact_coordinates" };
    hierarchy[key].count += 1;
    const city = String(ev.location_city || ev.location_name || "").trim();
    if (city) {
      const c = hierarchy[key].cities[city] || (hierarchy[key].cities[city] = { lat, lng, count: 0, revenue: "", kols: [], raw: ev });
      c.count += 1;
    }
  }
  return Object.keys(hierarchy).length ? hierarchy : null;
}

// 经销商地图层(主页地球):把 /dealers/locations 扁平 pin 归并成 country(US)→cities 层级,
// 形状对齐 normalizeEventsHierarchy 供同一地球消费。只上图有经纬度的(无则待 geocode 不显)。
export function normalizeDealersHierarchy(pins: any = []) {
  const hierarchy: Record<string, any> = {};
  const centroid = countryCentroid("US");
  for (const raw of list(pins)) {
    const p = record(raw);
    const lat = number(p.lat);
    const lng = number(p.lng);
    if (lat == null || lng == null) continue;
    const key = "US";
    if (!hierarchy[key]) hierarchy[key] = { lat: centroid?.lat ?? lat, lng: centroid?.lng ?? lng, count: 0, revenue: "", cities: {} };
    hierarchy[key].count += 1;
    const city = String(p.city || p.name || "").trim();
    if (city) {
      const c = hierarchy[key].cities[city] || (hierarchy[key].cities[city] = { lat, lng, count: 0, revenue: "", kols: [], raw: p });
      c.count += 1;
    }
  }
  return Object.keys(hierarchy).length ? hierarchy : null;
}

// 单个活动的地图落点只允许显式经纬度。国家级信息只能作聚合，
// 不能通过质心或 jitter 冒充场馆/门店精确位置。
export function eventCoords(_countryCode: any, lat: any, lng: any, _seed = "") {
  const latNum = number(lat);
  const lngNum = number(lng);
  if (latNum != null && lngNum != null) return { lat: latNum, lng: lngNum };
  return null;
}
