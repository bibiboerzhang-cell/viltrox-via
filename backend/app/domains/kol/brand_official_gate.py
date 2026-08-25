"""品牌官方账号闸(2026-08-25 发现墙缺陷③「品牌官号混进达人池」车道)。

本闸是**第二道**,不是唯一一道:建档入口 `profile_basics.write_kol_profile_batics` 先过主线
既有的 `discovery_filters.discovery_account_gate_verdict`(own_brand / lexicon / dynamic 三路),
本闸只补它漏的那一档「品牌词 + 地区/官方后缀」形态。隔离库 2020 行取证:主线那道对
tamron_europe(id 4791)、tamron_south_africa(5063)、twnz.official(5216)、sirui.cine(5240)
四行全判 ""(词表路径不含这些拼法,dynamic 路径要 bio 自述企业口吻),全池只命中 2 行
own_brand —— 这四个官号确实是从建档线走进池的(created_at 2026-07-26 ~ 2026-08-21)。

判据口径**刻意保守**——宁可漏拦(false negative),绝不误吃真达人:

- 只认**整只 handle / 整只 display_name** 等于「品牌词」或「品牌词 + 官方/地区后缀」;
- 品牌词只出现在名字**中间/结尾**(sonyalpharumors、canonrumors、sonya_official)一律放行;
- bio、视频标题、简介里提到品牌一概不看——那是达人在做评测,不是官号。

配置驱动(镜像 CN/HK/TW 地区排除的形态):默认词表在本模块,运行期可用
`VKPI_BRAND_OFFICIAL_GATE=0` 整闸关、`VKPI_BRAND_OFFICIAL_TOKENS` 加词、
`VKPI_BRAND_OFFICIAL_TOKENS_EXCLUDE` 减词。判据本身是纯函数(零库、零网络),
只有判据取不到时会打一条告警——失败绝不静默,也绝不朝「拦人」方向兜底。

红线:只拦「新建行」,既有行照常刷新、照常展示,绝不删行、绝不改任何评分字段。
读端一个字都不动(存量官号行的徽章/漏斗剔除是另一次决策,本刀不做)。
"""
from __future__ import annotations

import os
import re
from typing import Any

from app.core.coerce import _text
from app.core.logging import get_logger

logger = get_logger("viltrox.domains.kol.brand_official_gate")

# 落库/计数用的诚实原因码(门面文案另译中文「品牌官方账号」,不透判据)。
BRAND_OFFICIAL_SKIP_REASON = "brand_official_account"
# 懒 import 失败只告警一次(见 _handle_is_identity)。
_IDENTITY_RULE_WARNED: set[str] = set()

# 精选品牌词(相机/影像器材品牌 + 自家品牌)。只做「整只 handle 等值」与
# 「品牌词 + 官方/地区后缀」两种命中,所以短词(sony/dji)也不会误吃真人。
_DEFAULT_BRAND_TOKENS = frozenset({
    "sony",
    "sonyalpha",
    "canon",
    "nikon",
    "fujifilm",
    "fujifilmx",
    "panasonic",
    "lumix",
    "olympus",
    "omsystem",
    "leica",
    "hasselblad",
    "dji",
    "sigma",
    "sigmaphoto",
    "tamron",
    "samyang",
    "laowa",
    "venusoptics",
    "meike",
    "godox",
    "smallrig",
    "ulanzi",
    "insta360",
    "gopro",
    "zhiyun",
    "hohem",
    "feelworld",
    "sirui",
    "nanlite",
    "aputure",
    "gvm",
    "gvmled",
    "viltrox",
})
# 官方/地区后缀:品牌词后面**只**跟这些词才算官号形态。
# 词表刻意只放「官方口径词 + 地区词」——sonyalpharumors 的 rumors 不在表内 → 放行。
_OFFICIAL_SUFFIXES = frozenset({
    "official",
    "officiel",
    "officialpage",
    "officialchannel",
    "channel",
    "brand",
    "global",
    "worldwide",
    "international",
    "hq",
    "store",
    "shop",
    "imaging",
    "usa",
    "us",
    "uk",
    "eu",
    "europe",
    "asia",
    "africa",
    "southafrica",
    "mena",
    "latam",
    "nordic",
    "benelux",
    "cee",
    "japan",
    "jp",
    "korea",
    "kr",
    "china",
    "india",
    "malaysia",
    "singapore",
    "indonesia",
    "philippines",
    "thailand",
    "vietnam",
    "australia",
    "nz",
    "canada",
    "mexico",
    "brasil",
    "brazil",
    "france",
    "germany",
    "deutschland",
    "italia",
    "italy",
    "espana",
    "spain",
    "polska",
    "turkiye",
    "arabia",
})
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_OFF_FLAGS = {"0", "false", "no", "off"}


def _norm(value: Any) -> str:
    return _NON_ALNUM_RE.sub("", _text(value).lower())


def brand_official_gate_enabled() -> bool:
    """整闸开关(默认开)。关闸=完全不拦,读端徽章也不打——诚实回到旧行为。"""
    return _text(os.getenv("VKPI_BRAND_OFFICIAL_GATE", "1")).lower() not in _OFF_FLAGS


def configured_brand_tokens() -> frozenset[str]:
    """默认词表 + 运行期加词 − 运行期减词(全部归一成 [a-z0-9] 后比对)。"""
    tokens = {token for token in _DEFAULT_BRAND_TOKENS}
    for extra in _split_env(os.getenv("VKPI_BRAND_OFFICIAL_TOKENS", "")):
        tokens.add(extra)
    for removed in _split_env(os.getenv("VKPI_BRAND_OFFICIAL_TOKENS_EXCLUDE", "")):
        tokens.discard(removed)
    return frozenset(token for token in tokens if len(token) >= 3)


def _split_env(raw: Any) -> list[str]:
    return [_norm(part) for part in re.split(r"[,\s;|]+", _text(raw)) if _norm(part)]


def _suffix_ok(remainder: str) -> bool:
    """余段必须整只是官方/地区后缀,或两个后缀相接(officialusa / globalstore)。"""
    if not remainder:
        return False
    if remainder in _OFFICIAL_SUFFIXES:
        return True
    for cut in range(2, len(remainder) - 1):
        if remainder[:cut] in _OFFICIAL_SUFFIXES and remainder[cut:] in _OFFICIAL_SUFFIXES:
            return True
    return False


def _match_field(value: Any, tokens: frozenset[str]) -> str:
    """整只字段命中才返回品牌词:等于品牌词,或品牌词 + 官方/地区后缀。"""
    norm = _norm(value)
    if not norm:
        return ""
    # 长词优先(sonyalpha 先于 sony),命中即定;避免短词把长词的余段判成「非后缀」而漏拦。
    for token in sorted(tokens, key=len, reverse=True):
        if norm == token:
            return token
        if norm.startswith(token) and _suffix_ok(norm[len(token):]):
            return token
    return ""


def _handle_is_identity(handle: Any, platform: Any) -> bool:
    """该平台的 handle 能不能当身份用(与 pool_identity_key 同一份口径)。

    **判不了就当不能用(返回 False)**:本闸只剩 display_name 一路判据 → 宁可漏拦一个官号,
    绝不误吃一个真达人。2026-08-25 修(对抗审查缺陷⑨):旧码 `except: return True` 方向反了——
    判据一取不到就拿 handle 硬判,而本闸的两个下游都朝「拦人」方向放大后果
    (profile_basics 的建档闸直接不建行;读端徽章 + 漏斗把该行从 enrolled 里减掉),
    正是模块头「宁可漏拦,绝不误吃」的反面。失败必留痕,首次告警(逐行刷栈会淹日志)。
    """
    if not _text(handle):
        return False
    try:
        from app.domains.kol.pool_identity_key import handle_is_identity_signal

        return handle_is_identity_signal(handle, platform)
    except Exception:
        if "identity" not in _IDENTITY_RULE_WARNED:
            _IDENTITY_RULE_WARNED.add("identity")
            logger.warning(
                "handle-identity rule unavailable; brand gate ignores handle, reads display_name only",
                exc_info=True,
            )
        return False


def brand_official_match(
    *,
    handle: Any = "",
    display_name: Any = "",
    platform: Any = "",
) -> dict[str, Any]:
    """品牌官号判据。命中返回 {brand, field, reason};未命中返回 {}。

    只看身份字段(handle / display_name),且必须整只命中。闸关时恒返回 {}。
    `platform` 用来决定 handle 认不认:media/website 行的 handle 是从文章路径捏出来的
    (opticallimits 的行 handle 叫 nikon),那不是身份 → 只看 display_name。
    """
    if not brand_official_gate_enabled():
        return {}
    tokens = configured_brand_tokens()
    if not tokens:
        return {}
    if not _handle_is_identity(handle, platform):
        handle = ""
    for field, value in (("handle", handle), ("display_name", display_name)):
        brand = _match_field(value, tokens)
        if brand:
            return {"brand": brand, "field": field, "reason": BRAND_OFFICIAL_SKIP_REASON}
    return {}


def is_brand_official_row(row: Any) -> bool:
    """池行/候选项是否品牌官号(读端徽章与计数用)。非 dict → False。"""
    if not isinstance(row, dict):
        return False
    return bool(
        brand_official_match(
            handle=row.get("handle") or row.get("username"),
            display_name=row.get("display_name") or row.get("channel_name") or row.get("name"),
            platform=row.get("platform"),
        )
    )


__all__ = [
    "BRAND_OFFICIAL_SKIP_REASON",
    "brand_official_gate_enabled",
    "brand_official_match",
    "configured_brand_tokens",
    "is_brand_official_row",
]
