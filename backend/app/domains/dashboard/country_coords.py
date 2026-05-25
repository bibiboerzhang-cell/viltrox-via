"""Country centroid helpers for dashboard KOL distribution maps."""
from __future__ import annotations

import re
from typing import Any


COUNTRY_COORDS: dict[str, dict[str, float | str]] = {
    "AE": {"name": "United Arab Emirates", "lat": 24.0, "lng": 54.0},
    "AR": {"name": "Argentina", "lat": -38.4, "lng": -63.6},
    "AT": {"name": "Austria", "lat": 47.6, "lng": 14.1},
    "AU": {"name": "Australia", "lat": -25.3, "lng": 133.8},
    "BD": {"name": "Bangladesh", "lat": 23.7, "lng": 90.4},
    "BE": {"name": "Belgium", "lat": 50.5, "lng": 4.5},
    "BR": {"name": "Brazil", "lat": -14.2, "lng": -51.9},
    "CA": {"name": "Canada", "lat": 56.1, "lng": -106.3},
    "CH": {"name": "Switzerland", "lat": 46.8, "lng": 8.2},
    "CL": {"name": "Chile", "lat": -35.7, "lng": -71.5},
    "CN": {"name": "China", "lat": 35.9, "lng": 104.2},
    "CO": {"name": "Colombia", "lat": 4.6, "lng": -74.1},
    "CZ": {"name": "Czechia", "lat": 49.8, "lng": 15.5},
    "DE": {"name": "Germany", "lat": 51.2, "lng": 10.5},
    "DK": {"name": "Denmark", "lat": 56.3, "lng": 9.5},
    "EG": {"name": "Egypt", "lat": 26.8, "lng": 30.8},
    "ES": {"name": "Spain", "lat": 40.5, "lng": -3.7},
    "FI": {"name": "Finland", "lat": 61.9, "lng": 25.7},
    "FR": {"name": "France", "lat": 46.2, "lng": 2.2},
    "GB": {"name": "United Kingdom", "lat": 55.4, "lng": -3.4},
    "GE": {"name": "Georgia", "lat": 42.3, "lng": 43.4},
    "HK": {"name": "Hong Kong", "lat": 22.3, "lng": 114.2},
    "HR": {"name": "Croatia", "lat": 45.1, "lng": 15.2},
    "HU": {"name": "Hungary", "lat": 47.2, "lng": 19.5},
    "ID": {"name": "Indonesia", "lat": -0.8, "lng": 113.9},
    "IE": {"name": "Ireland", "lat": 53.4, "lng": -8.2},
    "IN": {"name": "India", "lat": 20.6, "lng": 78.9},
    "IR": {"name": "Iran", "lat": 32.4, "lng": 53.7},
    "IS": {"name": "Iceland", "lat": 64.9, "lng": -19.0},
    "IT": {"name": "Italy", "lat": 41.9, "lng": 12.6},
    "JP": {"name": "Japan", "lat": 36.2, "lng": 138.3},
    "KR": {"name": "South Korea", "lat": 36.5, "lng": 127.8},
    "KZ": {"name": "Kazakhstan", "lat": 48.0, "lng": 66.9},
    "LU": {"name": "Luxembourg", "lat": 49.8, "lng": 6.1},
    "LV": {"name": "Latvia", "lat": 56.9, "lng": 24.6},
    "MA": {"name": "Morocco", "lat": 31.8, "lng": -7.1},
    "MM": {"name": "Myanmar", "lat": 21.9, "lng": 95.9},
    "MX": {"name": "Mexico", "lat": 23.6, "lng": -102.5},
    "MY": {"name": "Malaysia", "lat": 4.2, "lng": 101.9},
    "NL": {"name": "Netherlands", "lat": 52.1, "lng": 5.3},
    "NO": {"name": "Norway", "lat": 60.5, "lng": 8.5},
    "NZ": {"name": "New Zealand", "lat": -40.9, "lng": 174.9},
    "PE": {"name": "Peru", "lat": -9.2, "lng": -75.0},
    "PH": {"name": "Philippines", "lat": 12.9, "lng": 121.8},
    "PL": {"name": "Poland", "lat": 51.9, "lng": 19.1},
    "PT": {"name": "Portugal", "lat": 39.4, "lng": -8.2},
    "RO": {"name": "Romania", "lat": 45.9, "lng": 24.9},
    "RS": {"name": "Serbia", "lat": 44.0, "lng": 20.8},
    "RU": {"name": "Russia", "lat": 61.5, "lng": 105.3},
    "SE": {"name": "Sweden", "lat": 60.1, "lng": 18.6},
    "SG": {"name": "Singapore", "lat": 1.4, "lng": 103.8},
    "SI": {"name": "Slovenia", "lat": 46.1, "lng": 14.8},
    "SK": {"name": "Slovakia", "lat": 48.7, "lng": 19.7},
    "TH": {"name": "Thailand", "lat": 15.9, "lng": 101.0},
    "TW": {"name": "Taiwan", "lat": 23.7, "lng": 121.0},
    "UA": {"name": "Ukraine", "lat": 48.4, "lng": 31.2},
    "US": {"name": "United States", "lat": 39.8, "lng": -98.6},
    "VN": {"name": "Vietnam", "lat": 14.1, "lng": 108.3},
    "ZA": {"name": "South Africa", "lat": -30.6, "lng": 22.9},
}


COUNTRY_ALIASES: dict[str, str] = {
    "america": "US",
    "argentina": "AR",
    "australia": "AU",
    "austria": "AT",
    "bangladesh": "BD",
    "belgium": "BE",
    "brazil": "BR",
    "canada": "CA",
    "chile": "CL",
    "china": "CN",
    "colombia": "CO",
    "croatia": "HR",
    "czech": "CZ",
    "czechia": "CZ",
    "denmark": "DK",
    "dubai": "AE",
    "egypt": "EG",
    "england": "GB",
    "finland": "FI",
    "france": "FR",
    "georgia": "GE",
    "germany": "DE",
    "hong kong": "HK",
    "hungary": "HU",
    "iceland": "IS",
    "india": "IN",
    "indonesia": "ID",
    "iran": "IR",
    "ireland": "IE",
    "italy": "IT",
    "japan": "JP",
    "kazakhstan": "KZ",
    "korea": "KR",
    "latvia": "LV",
    "luxembourg": "LU",
    "malaysia": "MY",
    "mexico": "MX",
    "morocco": "MA",
    "myanmar": "MM",
    "netherlands": "NL",
    "new zealand": "NZ",
    "norway": "NO",
    "peru": "PE",
    "philippines": "PH",
    "poland": "PL",
    "portugal": "PT",
    "romania": "RO",
    "russia": "RU",
    "serbia": "RS",
    "singapore": "SG",
    "slovakia": "SK",
    "slovenia": "SI",
    "south africa": "ZA",
    "south korea": "KR",
    "spain": "ES",
    "sweden": "SE",
    "switzerland": "CH",
    "taiwan": "TW",
    "thailand": "TH",
    "toronto": "CA",
    "uk": "GB",
    "united arab emirates": "AE",
    "united kingdom": "GB",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "vienna": "AT",
    "vietnam": "VN",
    "阿根廷": "AR",
    "阿联酋": "AE",
    "爱尔兰": "IE",
    "奥地利": "AT",
    "澳大利亚": "AU",
    "巴西": "BR",
    "比利时": "BE",
    "冰岛": "IS",
    "波兰": "PL",
    "迪拜": "AE",
    "丹麦": "DK",
    "德国": "DE",
    "俄罗斯": "RU",
    "埃及": "EG",
    "法国": "FR",
    "菲律宾": "PH",
    "芬兰": "FI",
    "哥伦比亚": "CO",
    "格鲁吉亚": "GE",
    "韩国": "KR",
    "荷兰": "NL",
    "加拿大": "CA",
    "捷克": "CZ",
    "哈萨克斯坦": "KZ",
    "克罗地亚": "HR",
    "拉脱维亚": "LV",
    "卢森堡": "LU",
    "罗马尼亚": "RO",
    "马来西亚": "MY",
    "美国": "US",
    "孟加拉国": "BD",
    "秘鲁": "PE",
    "缅甸": "MM",
    "摩洛哥": "MA",
    "墨西哥": "MX",
    "南非": "ZA",
    "挪威": "NO",
    "葡萄牙": "PT",
    "日本": "JP",
    "瑞典": "SE",
    "瑞士": "CH",
    "塞尔维亚": "RS",
    "斯洛伐克": "SK",
    "斯洛文尼亚": "SI",
    "台湾": "TW",
    "泰国": "TH",
    "乌克兰": "UA",
    "西班牙": "ES",
    "香港": "HK",
    "新加坡": "SG",
    "新西兰": "NZ",
    "匈牙利": "HU",
    "伊朗": "IR",
    "意大利": "IT",
    "印度": "IN",
    "印度尼西亚": "ID",
    "印尼": "ID",
    "英国": "GB",
    "越南": "VN",
    "智利": "CL",
    "中国": "CN",
    "中国台湾": "TW",
}


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def resolve_country_code(*values: Any) -> str:
    """Resolve noisy country fields to an ISO-3166 alpha-2 code when possible."""
    candidates: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        candidates.append(text)
        candidates.extend(part.strip() for part in re.split(r"[,，/、;；|\n]+", text) if part.strip())

    for candidate in candidates:
        upper = candidate.upper()
        if upper in COUNTRY_COORDS:
            return upper
        normalized = _normalize_text(candidate)
        if normalized in COUNTRY_ALIASES:
            return COUNTRY_ALIASES[normalized]

    joined = " ".join(candidates)
    for alias, code in COUNTRY_ALIASES.items():
        if alias and alias in joined:
            return code
    return ""


def country_geo(code: str) -> dict[str, Any] | None:
    normalized = str(code or "").upper()
    row = COUNTRY_COORDS.get(normalized)
    if not row:
        return None
    return {
        "code": normalized,
        "name": str(row["name"]),
        "lat": float(row["lat"]),
        "lng": float(row["lng"]),
    }
