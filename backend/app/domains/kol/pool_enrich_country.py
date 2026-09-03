"""KOL Pool 富化 · 平台自报国家(country provenance)—— 纯函数层。

T 车道实测(2026-09-02,会话 1124):在线新发现入池 ``country=''``(ids 15548-15557 全空),
而会话项的 ``market`` 直接盖成查询里的 ``US``——没核实过。根因有两层:

1. 发现侧 item 只在 YouTube channels.list 回了 ``snippet.country`` 时才带 ``country``,
   TikTok ``authorMeta.region`` / IG 商家地址从来没被读过;
2. 池写端(富化 ``_write_enriched_item``)压根不写 ``country`` 列——抓到了也落不下。

本模块只做「平台字段 → 国家码」这一件事,**零推断、零 LLM**:

* :func:`country_code` —— 文本 → 国家码(``uk``→``GB`` 等别名与 ``pool_common`` 同一张表;
  ``global``/空/认不出的长文本 → ``""``,绝不编码);
* :func:`platform_country_hint` —— 一行平台数据(发现候选 / 抓取 profile / actor 原始行)里
  平台**自报**的国家:YT ``snippet.country`` / TT ``authorMeta.region`` / IG 商家地址国家;
* :func:`derive_profile_country` —— 富化 raw_data 的 profile 段 → 国家码;
* :func:`country_write_decision` —— 只在「库里没有」或「库里那条本来就是平台自报」时才写,
  人工 / 历史导入的国家绝不被抓取覆盖;
* :func:`stamp_country_provenance` —— 把来源写进 raw_platform_data(``country_source`` =
  ``platform_profile``,是 ``profile_recall_qualification._market_resolution`` 认可的显式来源)。

红线:零触 ``viltrox_fit_score`` / rule_v0;取不到就留空,不用 market 冒充 country。
"""
from __future__ import annotations

from typing import Any

from app.domains.kol.pool_common import _profile_item
from app.platform.country_codes import (
    COUNTRY_SOURCE_PLATFORM,
    GLOBAL_MARKET_WORDS,
    as_dict as _dict,
    json_dict as _json_dict,
    market_country_code as country_code,
    platform_country_hint,
)

def derive_profile_country(raw_data: Any, platform: str = "") -> str:
    """富化 raw_data(``{"profile": provider payload, ...}``)→ 平台自报国家码;取不到 → ``""``。

    ``platform`` 只用于文档意图;三平台字段互不冲突,统一走 :func:`platform_country_hint`。"""
    del platform  # 三平台字段名互不重叠,无需分支;保留参数是给调用方表达意图
    raw = _json_dict(raw_data)
    if not raw:
        return ""
    profile = _profile_item(raw)
    code = platform_country_hint(profile)
    if code:
        return code
    # TT 的 profile payload 是「视频行」列表,authorMeta 挂在每条视频上;首条已在 _profile_item
    # 取到;这里再看 profile 段自身(apify 单行 profile 形状)兜底。
    return platform_country_hint(_dict(raw.get("profile")))


def _raw_country_source(raw_platform_data: Any) -> str:
    return str(_json_dict(raw_platform_data).get("country_source") or "").strip().lower()


def country_write_decision(*, existing_country: Any, existing_raw: Any, derived: Any) -> str:
    """返回要写的国家码或 ``""``(= 不写,保留原值)。

    * 平台没给 → 不写(留空是诚实结论,绝不用查询 market 冒充);
    * 库里为空 → 写;
    * 库里已有且来源是平台自报 → 刷新;
    * 库里已有但来源是人工 / 历史导入 / 未记录 → **不覆盖**。"""
    code = country_code(derived)
    if not code:
        return ""
    if not str(existing_country or "").strip():
        return code
    if _raw_country_source(existing_raw) == COUNTRY_SOURCE_PLATFORM:
        return code
    return ""


def stamp_country_provenance(raw_data: dict[str, Any], code: str) -> None:
    """把国家来源写进即将落库的 raw_platform_data(供 _market_resolution 认作显式来源)。"""
    raw_data["country_source"] = COUNTRY_SOURCE_PLATFORM
    raw_data["declared_country"] = {
        "value": code,
        "source": COUNTRY_SOURCE_PLATFORM,
        "method": "platform_profile_field",
        "confidence": 1.0,
    }


def resolve_enrich_country(item: dict[str, Any], platform: str, raw_data: dict[str, Any]) -> str:
    """富化写端一步到位:决定要写的国家码(或 ``""``),并在要写时把来源盖进 raw_data。"""
    code = country_write_decision(
        existing_country=item.get("country"),
        existing_raw=item.get("raw_platform_data"),
        derived=derive_profile_country(raw_data, platform),
    )
    if code:
        stamp_country_provenance(raw_data, code)
    return code


__all__ = [
    "COUNTRY_SOURCE_PLATFORM",
    "GLOBAL_MARKET_WORDS",
    "country_code",
    "country_write_decision",
    "derive_profile_country",
    "platform_country_hint",
    "resolve_enrich_country",
    "stamp_country_provenance",
]
