// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html

export const COUNTRY_INFO = {
  US: { flag: "🇺🇸", name: "United States",  tier: "A", weight: 1.20 },
  CA: { flag: "🇨🇦", name: "Canada",         tier: "A", weight: 1.05 },
  UK: { flag: "🇬🇧", name: "United Kingdom", tier: "A", weight: 1.10 },
  DE: { flag: "🇩🇪", name: "Germany",        tier: "A", weight: 1.15 },
  JP: { flag: "🇯🇵", name: "Japan",          tier: "A", weight: 1.10 },
  AU: { flag: "🇦🇺", name: "Australia",      tier: "A", weight: 1.00 },
  SE: { flag: "🇸🇪", name: "Sweden",         tier: "A", weight: 1.00 },
  NL: { flag: "🇳🇱", name: "Netherlands",    tier: "A", weight: 1.00 },
  KR: { flag: "🇰🇷", name: "South Korea",    tier: "A", weight: 1.00 },
  SG: { flag: "🇸🇬", name: "Singapore",      tier: "A", weight: 1.00 },
  FR: { flag: "🇫🇷", name: "France",         tier: "B", weight: 0.85 },
  IT: { flag: "🇮🇹", name: "Italy",          tier: "B", weight: 0.80 },
  ES: { flag: "🇪🇸", name: "Spain",          tier: "B", weight: 0.80 },
  AT: { flag: "🇦🇹", name: "Austria",        tier: "B", weight: 0.85 },
  CH: { flag: "🇨🇭", name: "Switzerland",    tier: "B", weight: 0.90 },
  BE: { flag: "🇧🇪", name: "Belgium",        tier: "B", weight: 0.85 },
  TW: { flag: "🇹🇼", name: "China TW",       tier: "B", weight: 0.85 },
  HK: { flag: "🇭🇰", name: "China HK",       tier: "B", weight: 0.85 },
  MY: { flag: "🇲🇾", name: "Malaysia",       tier: "B", weight: 0.70 },
  AE: { flag: "🇦🇪", name: "UAE",            tier: "B", weight: 0.75 },
  IE: { flag: "🇮🇪", name: "Ireland",        tier: "B", weight: 0.85 },
  RU: { flag: "🇷🇺", name: "Russia",         tier: "B", weight: 0.70 },
  ZA: { flag: "🇿🇦", name: "South Africa",   tier: "B", weight: 0.70 },
  NZ: { flag: "🇳🇿", name: "New Zealand",    tier: "B", weight: 0.75 },
  MX: { flag: "🇲🇽", name: "Mexico",         tier: "C", weight: 0.45 },
  BR: { flag: "🇧🇷", name: "Brazil",         tier: "C", weight: 0.40 },
  IN: { flag: "🇮🇳", name: "India",          tier: "C", weight: 0.35 },
  TH: { flag: "🇹🇭", name: "Thailand",       tier: "C", weight: 0.45 },
  ID: { flag: "🇮🇩", name: "Indonesia",      tier: "C", weight: 0.35 },
  VN: { flag: "🇻🇳", name: "Vietnam",        tier: "C", weight: 0.40 },
  PH: { flag: "🇵🇭", name: "Philippines",    tier: "C", weight: 0.40 },
  CN: { flag: "🇨🇳", name: "China (excl)",   tier: "X", weight: 0.00 },  // 不算海外
};

const COUNTRY_ALIASES = {
  "united states": "US", usa: "US", us: "US", "美国": "US",
  "united kingdom": "UK", "great britain": "UK", uk: "UK", "英国": "UK",
  canada: "CA", "加拿大": "CA",
  germany: "DE", "德国": "DE",
  france: "FR", "法国": "FR",
  italy: "IT", "意大利": "IT",
  spain: "ES", "西班牙": "ES",
  japan: "JP", "日本": "JP",
  korea: "KR", "south korea": "KR", "韩国": "KR",
  australia: "AU", "澳大利亚": "AU", "澳大": "AU",
  russia: "RU", "俄罗斯": "RU",
  "south africa": "ZA", "南非": "ZA",
  netherlands: "NL", "荷兰": "NL",
  belgium: "BE", "比利时": "BE",
  switzerland: "CH", "瑞士": "CH",
  austria: "AT", "奥地利": "AT",
  india: "IN", "印度": "IN",
  china: "CN", "中国": "CN",
  hongkong: "HK", "hong kong": "HK", "香港": "HK", "china hk": "HK", "中国香港": "HK",
  taiwan: "TW", "台湾": "TW", "china tw": "TW", "中国台湾": "TW",
  singapore: "SG", "新加坡": "SG",
  mexico: "MX", "墨西哥": "MX",
  brazil: "BR", "巴西": "BR",
  thailand: "TH", "泰国": "TH",
  vietnam: "VN", "越南": "VN",
  philippines: "PH", "菲律宾": "PH",
  indonesia: "ID", "印尼": "ID", "印度尼西亚": "ID",
  "new zealand": "NZ", "新西兰": "NZ",
};

export function normalizeCountryCode(value) {
  if (!value) return "";
  const raw = String(value).trim();
  if (!raw) return "";
  const upper = raw.toUpperCase();
  if (COUNTRY_INFO[upper]) return upper;
  return COUNTRY_ALIASES[raw.toLowerCase()] || COUNTRY_ALIASES[raw] || "";
}

export function getCountryInfo(value) {
  const code = normalizeCountryCode(value);
  return code ? { code, ...COUNTRY_INFO[code] } : null;
}
