"""国家码归一:别名表 + 纯函数(platform 层,零 IO、零 app 依赖)。

为什么在 platform:``services.intelligence``(联网发现)与 ``domains.kol``(池富化)都要用
同一张别名表判国家;放在任一方都会形成 services↔domains 的跨核心环(健康采集器按静态
import 图计,懒 import 也算)。下沉到 platform 后两边都只向下依赖。

导出:
* :data:`COUNTRY_CODE_ALIASES` / :data:`COUNTRY_NAMES` —— 别名表与规范名(原 pool_common 同表)。
* :func:`country_code` —— 文本 → 国家码(宽松:认不出且 ≤3 字符原样大写返回;>3 字符原文返回)。
* :func:`market_country_code` —— 市场/国家文本 → 国家码;全球词 / 空 / 认不出 → ``""``。
* :func:`platform_country_hint` —— 一行平台数据里平台**自报**的国家码(零推断)。
"""

from __future__ import annotations

import json
import re
from typing import Any

#: 平台自报国家的来源标(与 profile_recall_qualification._APPROVED_DECLARED_MARKET_SOURCES 对齐)。
COUNTRY_SOURCE_PLATFORM = "platform_profile"
#: 「全球」类市场词:不构成任何国家约束。
GLOBAL_MARKET_WORDS = frozenset({"global", "all", "worldwide", "*"})
#: 一行平台数据里可能直接带国家的字段(按可信顺序)。
_DIRECT_COUNTRY_KEYS = ("country", "countryCode", "country_code", "region")
#: IG 商家地址(dict 或 JSON 串)里的国家字段。
_ADDRESS_COUNTRY_KEYS = ("country_code", "countryCode", "country")

COUNTRY_CODE_ALIASES = {
    "us": "US",
    "usa": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "united states": "US",
    "united states of america": "US",
    "america": "US",
    "美国": "US",
    "uk": "GB",
    "gb": "GB",
    "great britain": "GB",
    "united kingdom": "GB",
    "england": "GB",
    "英国": "GB",
    "canada": "CA",
    "加拿大": "CA",
    "germany": "DE",
    "deutschland": "DE",
    "德国": "DE",
    "france": "FR",
    "法国": "FR",
    "italy": "IT",
    "意大利": "IT",
    "spain": "ES",
    "西班牙": "ES",
    "netherlands": "NL",
    "holland": "NL",
    "荷兰": "NL",
    "belgium": "BE",
    "比利时": "BE",
    "japan": "JP",
    "日本": "JP",
    "south korea": "KR",
    "korea": "KR",
    "韩国": "KR",
    "china": "CN",
    "中国": "CN",
    "hong kong": "HK",
    "hongkong": "HK",
    "hk": "HK",
    "香港": "HK",
    "china hk": "HK",
    "中国香港": "HK",
    "taiwan": "TW",
    "tw": "TW",
    "台湾": "TW",
    "china tw": "TW",
    "中国台湾": "TW",
    "australia": "AU",
    "澳大利亚": "AU",
    "brazil": "BR",
    "巴西": "BR",
    "mexico": "MX",
    "墨西哥": "MX",
    "india": "IN",
    "印度": "IN",
    "thailand": "TH",
    "泰国": "TH",
    "vietnam": "VN",
    "越南": "VN",
    "philippines": "PH",
    "菲律宾": "PH",
    "indonesia": "ID",
    "印度尼西亚": "ID",
}

COUNTRY_NAMES = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "DE": "Germany",
    "FR": "France",
    "IT": "Italy",
    "ES": "Spain",
    "NL": "Netherlands",
    "BE": "Belgium",
    "JP": "Japan",
    "KR": "South Korea",
    "CN": "China",
    "HK": "China HK",
    "TW": "China TW",
    "AU": "Australia",
    "BR": "Brazil",
    "MX": "Mexico",
    "IN": "India",
    "TH": "Thailand",
    "VN": "Vietnam",
    "PH": "Philippines",
    "ID": "Indonesia",
}


def country_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = re.sub(r"\s+", " ", text.lower())
    if lowered in COUNTRY_CODE_ALIASES:
        return COUNTRY_CODE_ALIASES[lowered]
    upper = text.upper()
    if upper in COUNTRY_NAMES:
        return upper
    return upper if len(upper) <= 3 else text


def market_country_code(value: Any) -> str:
    """市场/国家文本 → 国家码;全球词 / 空 / 认不出 → ``""``(诚实留空,不编码)。"""
    text = str(value or "").strip()
    if not text or text.lower() in GLOBAL_MARKET_WORDS:
        return ""
    code = str(country_code(text) or "").strip().upper()
    return code if 0 < len(code) <= 3 else ""


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def json_dict(value: Any) -> dict[str, Any]:
    """raw_platform_data 可能是 dict(PG jsonb)或 JSON 串(sqlite);坏串 → {}。"""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip().startswith("{"):
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _address_country(value: Any) -> str:
    """IG 商家地址里的国家线索(profile-scraper 的 businessAddress 多为 JSON 串);拿不到 → ""。"""
    address = json_dict(value)
    for key in _ADDRESS_COUNTRY_KEYS:
        code = market_country_code(address.get(key))
        if code:
            return code
    return ""


def platform_country_hint(row: Any) -> str:
    """一行平台数据里平台**自报**的国家码(只认平台字段,零推断):
    直接字段 country/countryCode/region → TT ``authorMeta.region`` → YT ``snippet.country``
    → IG 商家地址国家。全部缺席 → ``""``。"""
    data = as_dict(row)
    author = as_dict(data.get("authorMeta"))
    snippet = as_dict(data.get("snippet"))
    values = [data.get(key) for key in _DIRECT_COUNTRY_KEYS]
    values.extend((author.get("region"), snippet.get("country")))
    for value in values:
        code = market_country_code(value)
        if code:
            return code
    return _address_country(data.get("businessAddress") or data.get("business_address"))


__all__ = [
    "COUNTRY_CODE_ALIASES",
    "COUNTRY_NAMES",
    "COUNTRY_SOURCE_PLATFORM",
    "GLOBAL_MARKET_WORDS",
    "as_dict",
    "country_code",
    "json_dict",
    "market_country_code",
    "platform_country_hint",
]
