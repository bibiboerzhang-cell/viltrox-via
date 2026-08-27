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
# 品类/自媒体后缀:品牌词后面跟这些词,整只名字就是「品牌 + 它卖的东西」——
# 2026-08-27 实测漏网案:Samyang 官方号 handle=samyanglens / 名字「Samyang Lens 엘케이삼양」,
# 余段 lens 不在官方/地区表里 → 主线闸、本闸、发现墙 8 道严格闸**全部放行**,直接混进
# 达人池;同批还有 TamronVids(Tamron 官方视频号,bio 是产品文案)。
# 词表刻意只收「几乎不可能出现在真人频道名里、且必须紧跟品牌词」的那几个:
# photo / photography / studio / films 这类真人常用词**故意不收**(宁可漏拦,绝不误吃)。
# 防误杀取证(本批 45 个合格新人全量回归):真人 Alt Buzz Lenses(altbuzzlenses)以
# lenses 结尾却不以品牌词开头 → 放行;店铺 KEH Camera(kehcamera)keh 不是品牌词 → 放行。
_PRODUCT_CATEGORY_SUFFIXES = frozenset({
    "lens",
    "lenses",
    "optic",
    "optics",
    "optical",
    "camera",
    "cameras",
    "vids",
    "video",
    "videos",
    "tv",
    "gear",
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


def official_suffix_tokens() -> frozenset[str]:
    """允许跟在品牌词后面的后缀词池 = 官方/地区词 ∪ 品类/自媒体词。

    供发现墙的 exact-handle 快路复用(``discovery_filters._competitor_brand_official``),
    两边共用一份词表 —— 官号形态的判据只能有一个定义。"""
    return _OFFICIAL_SUFFIXES | _PRODUCT_CATEGORY_SUFFIXES


def _suffix_ok(remainder: str) -> bool:
    """余段必须整只是后缀词,或两个后缀词相接(officialusa / globalstore / lensusa)。"""
    if not remainder:
        return False
    pool = official_suffix_tokens()
    if remainder in pool:
        return True
    for cut in range(2, len(remainder) - 1):
        if remainder[:cut] in pool and remainder[cut:] in pool:
            return True
    return False


def brand_plus_suffix_handle(handle_norm: str, brand_norm: str) -> bool:
    """归一 handle 是否 = 品牌词 或 品牌词 + 官方/地区/品类后缀(samyanglens / tamronvids)。

    发现墙的 exact-handle 快路用它把「等值品牌名」放宽到「品牌名 + 它卖的东西」。
    纯字符串判据、零 IO;调用方仍要另外拿 URL 佐证(exact_brand_handle_confirmed)。"""
    if not handle_norm or not brand_norm or not handle_norm.startswith(brand_norm):
        return False
    remainder = handle_norm[len(brand_norm):]
    return not remainder or _suffix_ok(remainder)


def _match_field(value: Any, tokens: frozenset[str]) -> str:
    """整只字段命中才返回品牌词:等于品牌词,或品牌词 + 官方/地区后缀。"""
    norm = _norm(value)
    if not norm:
        return ""
    # 长词优先(sonyalpha 先于 sony),命中即定;避免短词把长词的余段判成「非后缀」而漏拦。
    for token in sorted(tokens, key=len, reverse=True):
        if brand_plus_suffix_handle(norm, token):
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


def discovery_wall_verdict(item: Any) -> str:
    """发现墙的品牌官号判词:词表快路 / 动态判据 / **身份形态**,未命中 ""。

    第三路「身份形态」是 2026-08-27 Samyang 漏网案的落点:整只 handle / 整只名字 =
    品牌词 + 官方/地区/品类后缀。前两路对 ``samyanglens``(名字「Samyang Lens 엘케이삼양」、
    48.6k、证据标题「AF 135mm F1.8 FE | The Ultimate Portrait Prime.」)全落空 ——
    词表路要 bio 企业自述口吻(它的 bio 是 LK 集团愿景,强/弱口吻词一个都不含),
    动态路要 official/global/驼峰形态(它一个都没有),exact-handle 快路要 URL 里带
    公开 handle(发现 item 给的是 /channel/UC...,按设计 fail-open)。于是它带着产品级
    证据穿过全部 8 道严格闸进了达人池。同批漏网的还有 TamronVids(Tamron 官方视频号)。

    判据就是本模块那一份(整只命中才算、后缀表刻意不收 photo/photography/studio 这类
    真人常用词),不另发明第二套官号形态定义。刻意**只**装在发现墙上、不进
    ``discovery_filters.discovery_account_gate_verdict``:那条是建档硬闸、没有显式放行口,
    装上去会连 ``allow_brand_official=True`` 的显式建档也一并拒掉。

    防误吃取证:2026-08-27 实验的 209 个「旧闸放行的真候选」全量回归,第三路只新增判了
    TamronVids 一个;真达人 Alt Buzz Lenses(以 lenses 结尾但 altbuzz 不是品牌词)、
    二手商 KEH Camera 均照常放行。懒 import 防循环依赖。
    """
    from app.domains.kol.discovery_filters import _brand_official_verdict

    return _brand_official_verdict(item) or ("identity_form" if is_brand_official_row(item) else "")


__all__ = [
    "BRAND_OFFICIAL_SKIP_REASON",
    "brand_plus_suffix_handle",
    "discovery_wall_verdict",
    "official_suffix_tokens",
    "brand_official_gate_enabled",
    "brand_official_match",
    "configured_brand_tokens",
    "is_brand_official_row",
]
