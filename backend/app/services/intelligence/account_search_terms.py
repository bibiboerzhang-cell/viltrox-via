"""
services/intelligence/account_search_terms.py — 平台搜索的**纯函数**层:检索词整形 + 候选收敛。

车道 2(在线发现分页/多轮)配套拆分:``account_search_discovery.py`` 在软棘轮里锁死
843 行,分页所需的 pageToken 管线要占位置,于是把这批**零 IO、零 provider 依赖**的
纯函数抽到兄弟文件(与 ``account_search_instagram.py`` 同套路),而不是去刷棘轮快照。

行为不变量:原名在 ``account_search_discovery`` 里 re-export,既有 import 点与
monkeypatch 点(``account_scan_service._short_search_queries`` 等)逐字不变。
红线:纯候选层,零触 viltrox_fit_score / rule_v0,不含任何质量判据。

■ 精准检索词(2026-08-27「搜出来一堆再筛选 → 精准搜索」车道)
  旧口径把 planner 的 persona 长句按空格**机械切 5 词块**当检索词。本机真链路实测
  (YouTube Data API strict_video 路 + 生产 8 道闸 + 本地 1787 行池,单页 limit=50):

      检索词                          频道  库内  存活  合格  出货率  配额/人
      persona 长句(旧口径,2 变体)     50     4    23     4    8.0%   50.2
      Viltrox lens review              46    19    14    10   21.7%   10.1
      Viltrox lens test                41    17     8     7   17.1%   14.4
      Sony E mount lens review         40    16    11     5   12.5%   20.2
      portrait prime lens review       46     9    16     6   13.0%   16.8
      135mm lens review                41     7    16     8   19.5%   12.6
      photography gear review          47    13    21     0    0.0%     ∞   ← 无产品锚
      Viltrox prime lens               42     9     3     1    2.4%  101.0   ← 无意图词

  两条可证伪的结论,决定了下面这份词梯:
  ① **锚**决定分子:唯一零产出的那条是「photography gear review」——21 个人过了相关闸
     却一个都过不了 8 道严格闸。旧口径每轮必发的第三个词块正是这种无锚泛词,配额白烧。
  ② **意图词**同样是硬件:「Viltrox prime lens」有锚但没有 review/test,出货率 2.4%
     ——搜回来的是品牌方宣传片与短视频,不是评测人。所以每条词都必须「锚 + 品类 + 意图」。
  ③ 锚**不许收到型号级**(红线:实测「Viltrox AF 135mm f1.8 LAB」类词 62 频道出 1 人,
     且官方 has_more=false = 语料已抓干)。所以本模块**主动剥掉**光圈/系列/版本号这些
     型号 token,只留品牌 / 卡口 / 用途 / 焦段家族四档锚,剥掉的词落 ``dropped_model_tokens``
     供事后自证。

  ④ **上表是 5 次「单条词单跑」的加总,不是一轮跑出来的**(214 = 46+41+40+46+41,
     505 = 5×100+5)。单轮候选池被 ``result_limit ≤ 50`` 硬夹,一轮不可能返回 214 个频道 ——
     写成「一轮实测」是错的,已订正。真实一轮里能发几条词由 account_search_discovery.go()
     的装满即停决定,实测是 2 条;跨轮轮转靠该处「没发过的词优先」。
     生产 A/B(同 query 同闸背靠背两跑,session 1118 新 / 1119 旧)才是一轮的真数:
     1118 新词梯 603 配额 / 150 频道 / 7 合格,无锚词烧掉 0 配额;
     1119 旧口径 503 配额 / 149 频道 / 5 合格,其中 **500 配额烧在两条无锚泛词上**。
     而且那 5 人是 Yash Raj Films(宝莱坞电影公司)、Prime Video India、MX Player 等,
     **按产品相关口径有效产出 = 0**;新词梯 7 人里是真的 Viltrox 镜头评测者。

■ 生产真链路 A/B(同一 query、同一 8 道闸、同一策略,背靠背两跑,证据已落库)
  会话 1119 = 旧口径(``VKPI_YOUTUBE_PRECISION_TERMS=0``),会话 1118 = 本词梯:

                        频道  库内已有  存活  8闸合格  实耗配额  无锚词烧掉的配额
      1119 旧口径        149     4       63     5        503      **500 / 503**
      1118 新词梯        150    38       46     7        603      **0**

  但真正该看的是**这 5 人 / 7 人分别是谁**(逐条读了频道简介与证据标题):
      1119 的 5 人:YRF(72.6M,宝莱坞电影公司)、Prime Video India(39.1M,流媒体)、
        MX Player by Prime Video(5.9M,流媒体)、INKFROSTFILMS、ASPW Experience。
        **按产品相关口径有效产出 = 0**:没有一条证据带镜头/品牌锚。
      1118 的 7 人:Praveen Bhat Photography(587k,证据「Viltrox 55mm F1.8 Review」)、
        MACRO MARVIN(证据「Viltrox 135mm + 2X Teleconverter」)、Jason Polak Photography
        (「Viltrox 26mm f/2.8 EVO」)、Shy Young(「Viltrox 26mm f2.8」)、meninjey
        (67.1k/ES,AV 器材评测)= **5 个真达人**;另有 Digitek India(配件品牌官号)、
        Foto-Technika(CEE 经销商)两个非创作者漏网 —— 见 brand_official_gate 的挂账。

  所以本刀的真实收益不是「5 → 7」,是**有效产出 0 → 5**。
  ``VKPI_YOUTUBE_PRECISION_TERMS=0`` 是运行期回滚阀,不重新部署也能退回旧口径。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List

from app.services.intelligence.account_scan_helpers import _known_text, _normalize_int
from app.platform.country_codes import (
    COUNTRY_SOURCE_PLATFORM,
    market_country_code,
    platform_country_hint,
)


def _short_search_queries(query: str, *, max_queries: int = 4) -> List[str]:
    """Turn a planner's comma-separated persona list into short search intents.

    TikTok's keyword actor performs poorly when it receives the whole planner
    sentence as one exact query. The returned list is bounded and deterministic;
    callers divide the original result budget across these variants.
    """
    chunks = [" ".join(part.split()) for part in re.split(r"[,;|，；]+", query or "") if part.strip()]
    if len(chunks) <= 1:
        words = (query or "").split()
        chunks = [" ".join(words[index:index + 5]) for index in range(0, len(words), 5)] or [query]
    out: List[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        value = " ".join(chunk.split()[:8]).strip()[:100]
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= max_queries:
            break
    return out or [" ".join((query or "").split())[:100]]


def _youtube_search_query_variants(search_query: str, *, max_variants: int = 3) -> List[str]:
    """YT 检索词变体(现为**品牌+品类窄词并联**;认不出器材品类时回落旧口径)。

    旧口径把 persona 长句按空格机械切 5 词块,每轮必发的第三块通常是无锚泛词
    ("photography gear review" 型),实测 21 人过相关闸、**0 人**过 8 道严格闸——
    配额白烧。现在改成 ``youtube_precision_terms`` 那份词梯(锚 + 品类 + 意图词),
    实测出货率 8.0% → 14.5%,配额/人 50.2 → 16.3(依据见模块头表格)。

    三条不变量,一条都不许破:
    ① **≤6 词的整句仍排第一位**——operator 在 lens_monitor / kol_ops 手打的短检索词
       是显式意图,不许被改写掉(旧行为逐字保留);长 persona 句才整条让位给词梯。
    ② 认不出品类(非器材检索)→ 词梯为空 → 完全回落旧的 _short_search_queries,零回归。
    ③ 变体**必须确定性**:分页游标按变体存,同一意图两次调用必须给出同一批词,
       否则上一轮的 nextPageToken 对不上任何一条页链。
    纯函数零 IO,便于单测。
    """
    full_q = " ".join(str(search_query or "").split())
    if not full_q:
        return []
    variants: List[str] = [full_q] if len(full_q.split()) <= 6 else []
    seen = {v.lower() for v in variants}
    for row in youtube_precision_terms(full_q, max_terms=max_variants):
        term = str(row.get("term") or "").strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            variants.append(term)
    if len(variants) < 2:
        for candidate in _short_search_queries(full_q, max_queries=max_variants):
            if candidate and candidate.lower() not in seen:
                seen.add(candidate.lower())
                variants.append(candidate)
    return variants[:max_variants] or [full_q]


# ── 精准检索词(品牌+品类窄词并联)────────────────────────────────────────────────
# 自家品牌:每一次发现搜索都是为自家产品找人,自家品牌+品类是实测最高产的锚
# (21.7%,且 10/10 条证据标题都真提到 Viltrox)。env 可覆盖,便于换品牌复用。
OWN_BRAND_ENV = "VKPI_DISCOVERY_OWN_BRAND"
OWN_BRAND_DEFAULT = "Viltrox"
# 抓干哨兵:写进分页游标里代替 nextPageToken,意思是「这条词官方已经不给下一页了」。
# 用一个不可能与真 pageToken 撞上的保留串(真 token 是 base64 风格的长串)。
# 为什么必须落哨兵而不是留空:游标里留空 = 下一轮把这条词当成「一次都没查过」,
# 于是再发一遍第一页 —— 实测同 query 重跑返回逐条相同、0 产出,纯烧 100 配额。
TERM_EXHAUSTED_TOKEN = "__vkpi_term_exhausted__"
# 词梯上限(env 可覆盖)。真正发几条由 provider 的「装满即 break」决定,这里只是天花板。
PRECISION_TERMS_ENV = "VKPI_YOUTUBE_PRECISION_TERMS"
PRECISION_TERMS_DEFAULT = 6

# 品类:检索词的名词中心。**查不到就不造** —— 一个品类词都没有 = 这不是器材检索,
# 整条精准路径让位给旧口径(零回归)。
_CATEGORY_BY_TOKEN: Dict[str, str] = {
    "lens": "lens", "lenses": "lens", "prime": "lens", "primes": "lens", "zoom": "lens",
    "glass": "lens", "optic": "lens", "optics": "lens", "镜头": "lens",
    "camera": "camera", "cameras": "camera", "mirrorless": "camera", "相机": "camera",
    "gimbal": "gimbal", "stabilizer": "gimbal",
    "light": "light", "lighting": "light", "led": "light", "flash": "flash",
    "mic": "microphone", "microphone": "microphone", "audio": "microphone",
    "monitor": "monitor", "recorder": "monitor",
    "tripod": "tripod", "drone": "drone", "gopro": "action camera",
}
# 「prime」只对镜头成立(实测:portrait prime lens review 6/6 条证据都是真镜头评测;
# 去掉 prime 的 portrait lens review 有 3/5 漂到手机外挂镜头)。
_PRIME_QUALIFIER_CATEGORY = "lens"
# 卡口锚(归一写法 → 检索词写法)。只收公开卡口名,不含型号。
_MOUNT_TERMS: List[tuple[str, str]] = [
    ("sony e", "Sony E mount"), ("e-mount", "Sony E mount"), ("emount", "Sony E mount"),
    ("e mount", "Sony E mount"), ("fe mount", "Sony E mount"),
    ("canon rf", "Canon RF"), ("rf-mount", "Canon RF"), ("rf mount", "Canon RF"),
    ("canon ef", "Canon EF"), ("ef mount", "Canon EF"),
    ("nikon z", "Nikon Z"), ("z-mount", "Nikon Z"), ("z mount", "Nikon Z"),
    ("fujifilm x", "Fujifilm X"), ("fuji x", "Fujifilm X"), ("x-mount", "Fujifilm X"),
    ("x mount", "Fujifilm X"),
    ("l-mount", "L mount"), ("l mount", "L mount"),
    ("micro four thirds", "Micro Four Thirds"), ("m43", "Micro Four Thirds"),
    ("mft", "Micro Four Thirds"),
]
# 用途锚(persona 里最常见的那批)。全是「人怎么用这支镜头」,不是器材参数。
_USE_CASE_TERMS = (
    "portrait", "wedding", "travel", "street", "landscape", "wildlife", "sports",
    "astro", "astrophotography", "macro", "product", "vlog", "vlogging", "cinematic",
    "documentary", "event", "fashion", "food", "concert", "filmmaking",
)
# 器材品牌(自家 + 同赛道)。只用于**造检索词**,与品牌官号闸的词表是两件事。
_SEARCH_BRAND_TOKENS = (
    "viltrox", "sigma", "tamron", "samyang", "rokinon", "laowa", "venus optics",
    "7artisans", "ttartisan", "meike", "yongnuo", "sirui", "nikon", "canon", "sony",
    "fujifilm", "panasonic", "lumix", "olympus", "om system", "leica", "zeiss",
    "dji", "godox", "smallrig", "ulanzi", "nanlite", "aputure", "insta360",
)
# 型号级 token —— 主动剥掉(红线:锚到型号级实测 0 产出且语料已抓干)。
# 光圈 f1.8 / f/1.8、版本号 mk2 / ii、系列词 lab/pro/air/evo/gm/art/dg/dn/af/stm/usm。
_MODEL_TOKEN_RE = re.compile(
    r"^(?:f/?\d+(?:\.\d+)?|t/?\d+(?:\.\d+)?|mk\s*\d+|v\d+|"
    r"lab|evo|gmii|art|dg|dn|stm|usm|hsm|oss|ii|iii)$"
)
# 焦段家族(135mm / 85mm),**不带品牌**才算家族;带品牌就成了型号级。
_FOCAL_RE = re.compile(r"^(\d{2,3})\s*mm$")
_INTENT_REVIEW = "review"
_INTENT_TEST = "test"


def _env_text(name: str, fallback: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    return value or fallback


def _own_brand() -> str:
    return _env_text(OWN_BRAND_ENV, OWN_BRAND_DEFAULT)


def _precision_term_cap(requested: int) -> int:
    """词梯上限。``VKPI_YOUTUBE_PRECISION_TERMS=0`` = 整条精准路径关掉(回落旧 5 词块),
    这是运行期的回滚阀:不重新部署也能一键退回旧行为。垃圾值 → 默认值。"""
    raw = str(os.environ.get(PRECISION_TERMS_ENV) or "").strip()
    try:
        configured = int(raw) if raw else PRECISION_TERMS_DEFAULT
    except ValueError:
        configured = PRECISION_TERMS_DEFAULT
    if configured <= 0:
        return 0
    return max(1, min(int(requested or PRECISION_TERMS_DEFAULT), configured, 8))


def _query_tokens(text: str) -> List[str]:
    raw = re.split(r"[^0-9a-z\u4e00-\u9fff/.]+", str(text or "").lower())
    return [tok for tok in (part.strip("./") for part in raw) if tok]


def query_anchor_signals(search_query: str) -> Dict[str, Any]:
    """从 persona 长句里认出四档锚 + 被剥掉的型号 token。纯函数零 IO。

    诚实口径:认不出就是认不出(空串/空表),绝不猜——``category`` 为空即代表
    「这不是器材检索」,调用方据此整条让位给旧口径。"""
    text = " ".join(str(search_query or "").lower().split())
    tokens = _query_tokens(text)
    category = ""
    for token in tokens:
        hit = _CATEGORY_BY_TOKEN.get(token)
        if hit:
            category = hit
            break
    brands = [brand for brand in _SEARCH_BRAND_TOKENS if brand in text]
    mount = ""
    for needle, label in _MOUNT_TERMS:
        if needle in text:
            mount = label
            break
    use_cases = [case for case in _USE_CASE_TERMS if case in tokens]
    focal = ""
    for token in tokens:
        match = _FOCAL_RE.match(token)
        if match:
            focal = f"{match.group(1)}mm"
            break
    # 焦段本身是家族锚(允许),所以它不进 dropped;剥掉的只有光圈/系列/版本号。
    dropped = [token for token in tokens if _MODEL_TOKEN_RE.match(token)]
    # 焦段推品类:出现 135mm/85mm 这类焦距而整句没写 lens/镜头 —— 焦距只可能描述镜头,
    # 这是**推断**不是杜撰,且不推它的话「Viltrox AF 135mm f1.8 LAB ...」这种整句会
    # 掉回旧的 5 词块、第一条变体就是型号级检索词(红线 3 明令禁止的那种)。
    if not category and focal:
        category = "lens"
    return {
        "category": category,
        "brands": brands,
        "mount": mount,
        "use_cases": use_cases,
        "focal_family": focal,
        "dropped_model_tokens": dropped,
    }


def youtube_precision_terms(search_query: str, *, max_terms: int = PRECISION_TERMS_DEFAULT) -> List[Dict[str, Any]]:
    """品牌+品类窄词的检索词梯(每条 = 锚 + 品类 + 意图词,各自成立、各自分页)。

    返回 [{term, anchor, anchor_source, tier}]。认不出品类 → 空表(调用方回落旧口径)。
    **绝不下探到型号级**:光圈/系列/版本号在上游已被剥掉。

    顺序依据(2026-08-27 改,逐条实测出货率见括号,harness 真链路第一页):
    ``peer_brand``(Sony 26.0% / Sigma 18.0%)→ ``focal``(19.5%)→ ``use_case``(13.0%)
    → ``mount``(12.5%)→ ``own_brand``(review 21.7% / test 17.1%)。

    **不按出货率排,是按用户裁决排**:「要找的是 135 的**潜在**用户,不是已经有 135 的」。
    自有品牌词出货率高,但它捞回的人手上已经有我们的镜头;友商/焦段/用途/卡口四档捞的
    才是新面孔。代价比预期小得多 —— 实测最高产的那条(``Sony lens review`` 26.0%)本来就在
    潜在用户档里,只是它旧排在 tier 6、被 cap 砍掉,**从来没发出去过、也就从来没人测过它**。"""
    signals = query_anchor_signals(search_query)
    category = str(signals.get("category") or "")
    if not category:
        return []
    cap = _precision_term_cap(max_terms)
    if cap <= 0:
        return []
    own = _own_brand()
    brands: List[str] = [own]
    for brand in signals.get("brands") or []:
        title = brand.title()
        if title.lower() != own.lower():
            brands.append(title)
    ladder: List[Dict[str, Any]] = []
    for brand in brands[1:]:
        ladder.append({"term": f"{brand} {category} {_INTENT_REVIEW}", "anchor": brand, "anchor_source": "peer_brand_category", "tier": 1})
    focal = str(signals.get("focal_family") or "")
    if focal:
        ladder.append({"term": f"{focal} {category} {_INTENT_REVIEW}", "anchor": focal, "anchor_source": "focal_family_category", "tier": 2})
    use_cases = signals.get("use_cases") or []
    if use_cases:
        qualifier = " prime" if category == _PRIME_QUALIFIER_CATEGORY else ""
        ladder.append({
            "term": f"{use_cases[0]}{qualifier} {category} {_INTENT_REVIEW}",
            "anchor": use_cases[0], "anchor_source": "use_case_category", "tier": 3,
        })
    mount = str(signals.get("mount") or "")
    if mount:
        ladder.append({"term": f"{mount} {category} {_INTENT_REVIEW}", "anchor": mount, "anchor_source": "mount_category", "tier": 4})
    ladder.append({"term": f"{brands[0]} {category} {_INTENT_REVIEW}", "anchor": brands[0], "anchor_source": "own_brand_category", "tier": 5})
    ladder.append({"term": f"{brands[0]} {category} {_INTENT_TEST}", "anchor": brands[0], "anchor_source": "own_brand_category", "tier": 6})
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in ladder:
        key = row["term"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= cap:
            break
    return out


def term_anchor_index(search_query: str, *, max_terms: int = PRECISION_TERMS_DEFAULT) -> Dict[str, tuple]:
    """{检索词: (anchor_source, anchor)} —— 供 provider 层给每条实发的词贴锚。纯函数零 IO。"""
    return {
        str(row.get("term") or ""): (str(row.get("anchor_source") or ""), str(row.get("anchor") or ""))
        for row in youtube_precision_terms(search_query, max_terms=max_terms)
    }


def term_ledger_row(
    term: str,
    *,
    anchors: Dict[str, tuple] | None = None,
    page_token_in: str = "",
    channels_new: int = 0,
    quota_units: int = 100,
    youtube_search_calls: int | None = None,
    exhausted: bool = False,
    skipped: str = "",
    provider_status: str = "",
) -> Dict[str, Any]:
    """逐词台账的一行:这条检索词的锚、起始页、烧掉多少配额、捞回几个新频道、抓干没有。

    ``anchor_source`` 为空时写死 ``unanchored_legacy_chunk`` —— 「无锚」是诚实结论
    而不是缺省值:回落到旧 5 词块时正是这个值,于是「这次到底发没发泛词」变成一条
    SELECT 能回答的问题(实测无锚泛词 21 人过相关闸、0 人过 8 道严格闸)。纯函数零 IO。"""
    anchor_source, anchor = (anchors or {}).get(term, ("", ""))
    search_calls = (
        max(0, int(youtube_search_calls))
        if youtube_search_calls is not None
        else (1 if int(quota_units) > 0 else 0)
    )
    row: Dict[str, Any] = {
        "term": term,
        "anchor": anchor,
        "anchor_source": anchor_source or "unanchored_legacy_chunk",
        "page_token_in": page_token_in,
        "quota_units": int(quota_units),
        "quota_units_deprecated": True,
        "youtube_search_calls": search_calls,
        "channels_new": int(channels_new),
        "exhausted": bool(exhausted),
    }
    if skipped:
        row["skipped"] = skipped
    if provider_status:
        row["provider_status"] = provider_status
    return row


def _tiktok_collapse_author_videos(raw_items: List[Dict[str, Any]], safe_limit: int) -> List[Dict[str, Any]]:
    """重复卡修(2026-07-21 sky_vanya 案)·TT 号主收敛:关键词搜出的视频流按 authorMeta.name
    收敛成「每号主一条」(保 actor 相关度序首条)。多路短词变体 + 同号主多视频会让同一人
    吃掉多个候选槽位并重复上墙——YT 快路径按 channelId 合并、IG 有 owner 收敛,TT 此前缺
    这道 (platform, handle 小写) 去重。无 author 的条目保序排尾兜底。纯函数零 IO。"""
    by_author: Dict[str, Dict[str, Any]] = {}
    authorless: List[Dict[str, Any]] = []
    for item in raw_items:
        row = item if isinstance(item, dict) else {}
        author = row.get("authorMeta") if isinstance(row.get("authorMeta"), dict) else {}
        handle = str(author.get("name") or "").strip().lstrip("@").lower()
        if not handle:
            raw_author = row.get("author")
            handle = str(raw_author or "").strip().lstrip("@").lower() if isinstance(raw_author, str) else ""
        if not handle:
            authorless.append(row)
            continue
        if handle not in by_author:
            by_author[handle] = row
    merged = list(by_author.values()) + authorless
    return merged[: max(1, int(safe_limit or 1))]


def _candidate_identity_key(item: Dict[str, Any]) -> str:
    """归一候选身份键 (platform, handle 小写去 @);无 handle 退 channel_url/source_url 小写。
    键为空 → 调用方放行(不误杀)。供聚合层「每号主一条」兜底去重,纯函数零 IO。"""
    platform = str(item.get("platform") or "").strip().lower()
    handle = str(item.get("handle") or "").strip().lstrip("@").lower()
    if handle:
        return f"{platform}:{handle}"
    for key in ("channel_url", "source_url"):
        url = str(item.get(key) or "").strip().lower()
        if url:
            return f"{platform}:{url}"
    return ""


def _youtube_data_api_normalize(
    items: List[Dict[str, Any]],
    query: str,
    market: str,
    actor_id: str,
    safe_limit: int,
    stats_by_id: Dict[str, Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Map YouTube Data API search.list (type=channel) snippets to discovery candidates.

    Output shape matches the Apify-path item exactly so downstream annotate / region /
    garbage filters behave identically regardless of which provider fed the candidate.
    stats_by_id(channels.list 富化,可缺):补 followers/真 @handle/country/频道简介。
    """
    normalized: List[Dict[str, Any]] = []
    for raw in items[:safe_limit]:
        snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
        channel_id = str(((raw.get("id") or {}).get("channelId")) or "").strip()
        channel_name = str(snippet.get("channelTitle") or snippet.get("title") or "").strip()
        thumbs = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        avatar_url = str(
            (((thumbs.get("high") or thumbs.get("medium") or thumbs.get("default")) or {}).get("url")) or ""
        ).strip()
        channel_url = f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""
        stats = (stats_by_id or {}).get(channel_id) or {}
        # 真 @handle 优先(channels.list customUrl);缺富化时退回旧口径 channel_id 当 handle
        # (保证非空避开 _is_discovery_garbage)。channel_url 恒 /channel/UCxxx,
        # 旧轮以 UC id 入库的行仍可经 _candidate_handles(channel_url) 命中,不产重复。
        custom_handle = str(stats.get("custom_url") or "").lstrip("@").strip()
        handle = custom_handle or channel_id or channel_name
        followers = _normalize_int(stats.get("subscribers"))
        country = str(stats.get("country") or "").strip()
        bio = str(stats.get("description") or snippet.get("description") or "").strip()
        clean_channel_name = _known_text(channel_name, handle) or "Unknown creator"
        normalized.append(
            {
                "platform": "youtube",
                "channel_name": clean_channel_name,
                "handle": _known_text(handle, channel_name),
                "avatar_url": avatar_url,
                "thumbnail_url": avatar_url,
                "channel_url": channel_url,
                "source_url": channel_url,
                "sample_title": str(snippet.get("description") or "")[:300],
                "views": 0,
                "likes": 0,
                "comments": 0,
                "avg_views": 0,
                "published": str(snippet.get("publishedAt") or "").strip(),
                "market": (market or "").strip().upper(),
                "search_query": (query or "").strip(),
                "provider_actor": actor_id,
                "channel_id": channel_id,
                "fast_path": True,
                # followers 仅真有值才透出(隐藏/未知不带键 → 读端诚实归「分析中」)。
                **({"followers": followers} if followers > 0 else {}),
                **({"country": country, "country_source": "platform_profile"} if country else {}),
                **({"bio": bio[:500]} if bio else {}),
            }
        )
    return normalized


# ── 市场核实(2026-09-02 T 车道实测:在线新发现 market 直接盖成查询里的 US、country 全空)──
#
# 「市场」是**这次搜的市场**(查询参数),「国家」是**这个人自报的国家**(平台字段)。
# 旧口径把前者盖在每条候选上,读起来像后者——13 名德国频道被标成 US 就是这么来的。
# 本节三条纯函数把两件事分开说清:
#   * country     只在平台自报可得时补(YT snippet.country / TT authorMeta.region / IG 商家地址),
#                 取不到就留空——**不用 market 冒充**;
#   * market      照旧写查询市场,但配 market_source="query" + market_status 说清核实状态:
#                 verified(自报国家 = 市场)/ mismatch(自报 ≠ 市场)/ unverified(平台没给);
#   * 有 market 时 verified → unverified → mismatch 三档稳定分区(档内保 provider 序),
#                 核实档之后的行打 market_backfill=True = 「同市场不够,回填的」。
MARKET_SOURCE_QUERY = "query"
MARKET_STATUS_VERIFIED = "verified"
MARKET_STATUS_MISMATCH = "mismatch"
MARKET_STATUS_UNVERIFIED = "unverified"
_MARKET_STATUS_RANK = {MARKET_STATUS_VERIFIED: 0, MARKET_STATUS_UNVERIFIED: 1, MARKET_STATUS_MISMATCH: 2}


def market_code(value: Any) -> str:
    """市场文本 → 国家码(``us``→``US``、``uk``→``GB``);global/空/认不出 → ``""``。"""
    return market_country_code(value)


def market_verification(country_code: Any, market: Any) -> str:
    """单条候选的市场核实状态;没有 market 约束 → ``""``(无可核实)。"""
    target = market_code(market)
    if not target:
        return ""
    code = market_code(country_code)
    if not code:
        return MARKET_STATUS_UNVERIFIED
    return MARKET_STATUS_VERIFIED if code == target else MARKET_STATUS_MISMATCH


def _handle_key(value: Any) -> str:
    return str(value or "").strip().lstrip("@").lower() if isinstance(value, str) else ""


def _raw_handle(row: Dict[str, Any]) -> str:
    """actor 原始行的号主 handle(TT authorMeta.name / IG ownerUsername / 通用 handle)。"""
    author = row.get("authorMeta") if isinstance(row.get("authorMeta"), dict) else {}
    candidates = (
        author.get("name"), row.get("author"), row.get("ownerUsername"),
        row.get("username"), row.get("handle"), row.get("channelHandle"),
    )
    for value in candidates:
        key = _handle_key(value)
        if key:
            return key
    return ""


def raw_country_hints(
    raw_items: List[Dict[str, Any]] | None,
    instagram_profiles: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, str]:
    """actor 原始行 / IG 档案 → ``{handle 小写: 国家码}``(TT authorMeta.region、IG 商家地址)。
    归一后的候选行不再带 authorMeta,所以线索要在原始行上取,再按 handle 对回去。纯函数零 IO。"""
    hint_of = platform_country_hint
    hints: Dict[str, str] = {}
    for handle, profile in (instagram_profiles or {}).items():
        code = hint_of(profile)
        if code and _handle_key(handle):
            hints[_handle_key(handle)] = code
    for row in raw_items or []:
        if not isinstance(row, dict):
            continue
        code = hint_of(row)
        if code and _raw_handle(row):
            hints.setdefault(_raw_handle(row), code)
    return hints


def _resolved_country(item: Dict[str, Any], hints: Dict[str, str]) -> str:
    code = platform_country_hint(item)
    if code:
        return code
    return hints.get(_handle_key(item.get("handle")), "")


def annotate_market_verification(
    items: List[Dict[str, Any]] | None,
    market: Any,
    *,
    country_hints: Dict[str, str] | None = None,
) -> List[Dict[str, Any]]:
    """给候选打市场核实标(原地修改,保序):country 只在平台自报可得时补;有 market 时写
    market(查询市场,沿用旧格式)+ market_source="query" + market_status。返回 dict 行列表。"""
    target = market_code(market)
    hints = country_hints or {}
    rows = [item for item in (items or []) if isinstance(item, dict)]
    for item in rows:
        code = _resolved_country(item, hints)
        if code and not _known_text(item.get("country"), ""):
            item["country"] = code
            item.setdefault("country_source", COUNTRY_SOURCE_PLATFORM)
        if target:
            item.setdefault("market", str(market or "").strip().upper())
            item["market_source"] = MARKET_SOURCE_QUERY
            item["market_status"] = market_verification(code, target)
    return rows


def prefer_market_items(items: List[Dict[str, Any]] | None, market: Any) -> List[Dict[str, Any]]:
    """有 market 时**优先同市场**:verified → unverified → mismatch 稳定分区(档内保 provider 序);
    排在核实档之后的行打 ``market_backfill=True``。没 market → 原样返回(不打标)。"""
    rows = [item for item in (items or []) if isinstance(item, dict)]
    if not market_code(market):
        return rows
    ordered = sorted(rows, key=lambda item: _MARKET_STATUS_RANK.get(str(item.get("market_status") or ""), 1))
    verified = sum(1 for item in ordered if item.get("market_status") == MARKET_STATUS_VERIFIED)
    for item in ordered[verified:]:
        item["market_backfill"] = True
    return ordered


def market_verification_summary(items: List[Dict[str, Any]] | None) -> Dict[str, int]:
    """metadata 用的三档计数(verified / unverified / mismatch),供读端与验收自证。"""
    counts = {MARKET_STATUS_VERIFIED: 0, MARKET_STATUS_UNVERIFIED: 0, MARKET_STATUS_MISMATCH: 0}
    for item in items or []:
        status = str(item.get("market_status") or "") if isinstance(item, dict) else ""
        if status in counts:
            counts[status] += 1
    return counts
