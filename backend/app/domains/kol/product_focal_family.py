"""焦段家族解析:把操作员口语里的裸焦段(「135」「55」「85」)对到真实镜头家族。

背景(2026-08-25 线上回放):真实 query「我想要喜欢135的用户」在目录里解析不出任何产品
——`vkpi_product_aliases` 里只有 `135mm f18 LAB` 这类别名,没有光秃秃的「135」。
解析不出 → 既不报错也不提示 → 静默跑一趟没有产品锚的搜索 → 操作员等 30 秒拿到 0 条。

本模块只做两件事,都是只读:

1. **认焦段**:从目录里现算出「哪些数字真的是在售镜头的焦段」,只认这些数字。
   目录之外的数字(300W 的 300、粉丝数、年份、条数)一律不认。
2. **消歧**:同一焦段常有多款(135mm 有 8 行、56mm 有 20 行)。此时**不挑某一款**,
   而是返回焦段家族本身,让产品证据词落在焦段上("Viltrox 135mm");
   只有当卡口/系列线索把候选收窄到唯一一行时,才认定具体 SKU。

红线:绝不编造 SKU、绝不编造价格、绝不把非镜头(灯/电池/接圈/监视器)算进焦段家族。
本模块只读目录,不写任何字段。
"""
from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger
from app.domains.costs.product_catalog import list_product_catalog


logger = get_logger(__name__)


# 目录里合法的镜头焦段区间(Viltrox 最短 9mm、最长 135mm;留出余量但拒绝三位数功率/瓦数)。
_FOCAL_MIN = 8
_FOCAL_MAX = 800

# 判定一行是不是镜头。**先查否定词**——「Macro Extension Tube」里有 macro,
# 「300W ... LIGHT」里有数字,都必须先被踢出去,否则接圈/补光灯会混进焦段家族。
_NON_LENS_MARKERS = (
    "battery", "v-mount lithium", "light", "cob", "led", "flash", "strobe", "speedlite",
    "monitor", "extension tube", "tube", "tripod", "gimbal", "filter", "adapter",
    "charger", "power", "cable", "dock", "remote", "microphone", "speaker", "case", "bag",
)
_LENS_MARKERS = ("lens", "anamorphic", "cine", "prime")
_LENS_MODEL_RE = re.compile(r"(?:^|[^a-z])(?:af|mf|epic)(?:[^a-z]|$)", re.IGNORECASE)

# 焦段只从「数字 + mm」取。目录里的斜杠写法(AF 135/1.8 FE)另判:斜杠后必须是光圈
# (小于 32),否则 "50/99/150Wh" 会把电池的 50、99 当成焦段。
_MM_FOCAL_RE = re.compile(r"(?<![0-9])(\d{1,3})\s*mm(?![a-z])", re.IGNORECASE)
_SLASH_FOCAL_RE = re.compile(r"(?<![0-9.])(\d{2,3})\s*/\s*(\d{1,2}(?:\.\d)?)(?![0-9])")

# ``35mm`` is also a film format, and ``50mm equivalent`` is a field-of-view
# description.  Those phrases must not silently bind a Viltrox lens family.
# A nearby explicit product/lens anchor intentionally wins, so requests such
# as ``35mm F1.2 film look`` and ``35mm 镜头拍胶片`` keep their lens meaning.
_NON_LENS_MM_SUFFIX_RE = re.compile(
    r"^\s*(?:film\b|胶片|底片|negative\b|(?:full[- ]?frame\s+)?equivalent\b|全画幅\s*等效|等效)",
    re.IGNORECASE,
)
_NON_LENS_MM_PREFIX_RE = re.compile(
    r"(?:equivalent(?:\s+to)?|equiv\.?|等效(?:于)?)\s*$",
    re.IGNORECASE,
)
_EXPLICIT_LENS_PRODUCT_ANCHOR_RE = re.compile(
    r"\b(?:viltrox|af|mf|evo|lab|epic|air|raze|prime|anamorphic|cine|lens)\b"
    r"|唯卓仕|维卓仕?|镜头|定焦|变形宽银幕|卡口"
    r"|(?<![a-z0-9])[ft]\s*/?\s*\d{1,2}(?:\.\d+)?(?![a-z0-9])",
    re.IGNORECASE,
)

# ── query 侧:什么算「裸焦段」
# 整词锚定:两侧都不能贴字母/数字/点/斜杠/@/货币号,所以 "35mmc.com"、"550pro"、
# "1.8"、"p4-step23"、"@5treasuremom" 全部不进这条分支。
_BARE_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z._/@$#%¥£€,，-])(\d{2,3})(?![0-9A-Za-z._/%-])")
# 宽松形态:允许数字后面粘着字母(操作员常打 "55evo"、"90lab"),但把功率/尺寸单位
# 排掉——「300W」的 300、「50Wh」的 50 都不是焦段。product_resolver._query_focals
# 用这一档,好让两个焦段解析器共用同一套「这不是焦段」判据,而不是各写各的。
_LOOSE_NUMBER_RE = re.compile(
    r"(?<![0-9A-Za-z._/@$#%¥£€,，-])(\d{2,3})(?![0-9])"
    r"(?!\s*(?:ws|w(?![a-z])|nit|寸|inch|mah|fps))",
    re.IGNORECASE,
)
_URL_SPAN_RE = re.compile(r"https?://\S+|(?:www\.)?[a-z0-9-]+\.(?:com|net|org|io|co|de|cn|tv|me)\S*", re.IGNORECASE)

# 数字后面紧跟这些量词/单位 → 是在数数或说规格,不是焦段。
# 2026-08-26 复核坐实的误伤:「找24小时内活跃的youtube博主」→ 24 被当成 24mm,
# 而 24mm 目录里只有一行 → 整条搜索被锚到一支根本没人提过的镜头上,操作员看不出来。
# 病根是这张表只收了「个/位/万」这类计数词,漏掉了**时间单位**(小时/钟/刻)、
# 大量物量词(支/台/套/篇/张)与**区间连接词**(到/至/~)。误伤比漏认严重得多,
# 所以这张表宁可长一点:多挡一个数字只是少认一次焦段,认错一次是整条搜索跑偏。
# 英文分支带 (?![a-z]) 整词收尾——否则 "to" 会吃掉 "135 top creators" 里的 top。
_COUNTER_SUFFIX_RE = re.compile(
    r"^\s*(?:"
    r"万|千|亿|个|位|名|人|条|家|天|日|年|月|岁|后|元|块|次|场|集|部|届|分|秒|周|期|款|种|倍|"
    r"粉|美|欧|以|左|右|余|起|币|寸|多|来|"
    # 时间单位:24「小时」/48「时」/15「分钟」(分已在上一行)
    r"小时|时|钟|刻|"
    # 物量词 / 度量单位
    r"篇|张|支|台|套|件|份|组|批|轮|遍|档|级|度|帧|瓦|米|页|层|排|"
    # 区间左端:「10到50」「20至85」
    r"到|至|~|～|—|–|"
    r"%|％"
    r"|(?:"
    r"followers?|subs(?:cribers?)?|creators?|kols?|people|persons?|accounts?|videos?|views?|"
    r"posts?|results?|users?|days?|years?|hours?|minutes?|items?|channels?|"
    r"weeks?|months?|hrs?|secs?|mins?|times?|pcs?|units?|ratio|rate|score|"
    r"to|and|through|thru|"
    r"usd|dollars?|percent|watts?|nits?|fps|inches|inch|mah|wh|ws"
    r")(?![a-z])"
    r")",
    re.IGNORECASE,
)
# 数字前面是这些词 → 是比较/排名/计数/价格/区间右端,不是焦段。
# 英文只收 ≥4 字母的词:短词(to/and/at)做无边界前缀匹配会吃掉 "photo 135"、
# "shot at 135" 这类真焦段说法。区间右端的英文由 _RANGE_SPAN_RE 整体抹掉。
# 「vintage」是真系列词(结尾含 age),所以 age/aged 一律不进这张表。
_COUNTER_PREFIX_RE = re.compile(
    r"(?:"
    r"前|第|近|超过|至少|最少|最多|大于|小于|不少于|不超过|共|约|满|只要|要有|超|够|已有|"
    # 区间右端:「10到50」的 50、「20至85」的 85
    r"到|至|从|介于|区间|范围|"
    # 排名 / 均值 / 频次
    r"排名|排行|名次|平均|累计|连续|间隔|每|"
    # 价格 / 预算(「价格85」的尾字是「格」不是「价」,两个都要收)
    r"预算|花费|成本|折扣|格|"
    # 互动与账号指标
    r"粉丝|订阅|关注|播放|观看|点赞|评论|转发|收藏|互动|涨粉|完播|曝光|阅读|销量|库存|"
    r"时长|年龄|数量|条数|次数|人数|"
    # 单字收尾:完播「率」、客单「价」、运「费」、年「龄」、销「量」、峰「值」、总「额」
    r"率|额|价|费|龄|数|量|值|"
    r"top|over|under|than|least|most|min|max|about|around|approx|"
    r"between|from|within|past|recent|budget|price|rank|score|ratio|"
    r">|<|≥|≤|=|\+|\$|¥|￥|£|€"
    r")\s*$",
    re.IGNORECASE,
)
# 「数字 连接词 数字」是一个区间,两端都不是焦段。整段抹掉才对称——
# 只挡左端会把「粉丝10到50万」的 50 放过去,只挡右端会放过 10。
_RANGE_SPAN_RE = re.compile(
    r"(?<![0-9A-Za-z._/])"
    r"\d{1,4}\s*(?:万|千|亿|k|w|m)?\s*"
    r"(?:到|至|~|～|—|–|-|\bto\b|\band\b|\bthrough\b|\bthru\b)"
    r"\s*\d{1,4}",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _row_blob(product: dict[str, Any]) -> str:
    return " ".join(
        str(product.get(field) or "")
        for field in ("sku", "model_name", "marketing_name", "series", "category_main", "category_detail")
    ).lower()


def _is_lens_row(product: dict[str, Any]) -> bool:
    """一行是不是真镜头。否定词优先,防补光灯/电池/接圈混入焦段家族。"""
    blob = _row_blob(product)
    if any(marker in blob for marker in _NON_LENS_MARKERS):
        return False
    category = " ".join(
        str(product.get(field) or "") for field in ("category_main", "category_detail")
    ).lower()
    if "lens" in category:
        return True
    if any(marker in blob for marker in _LENS_MARKERS):
        return True
    return bool(_LENS_MODEL_RE.search(blob))


def _row_focals(product: dict[str, Any]) -> set[int]:
    blob = " ".join(
        str(product.get(field) or "")
        for field in ("sku", "model_name", "marketing_name")
    ).lower()
    focals = {int(match) for match in _MM_FOCAL_RE.findall(blob)}
    for focal, aperture in _SLASH_FOCAL_RE.findall(blob):
        try:
            if float(aperture) < 32:
                focals.add(int(focal))
        except ValueError:
            continue
    return {value for value in focals if _FOCAL_MIN <= value <= _FOCAL_MAX}


def focal_family_index(catalog_reader: Any = None) -> dict[int, list[dict[str, Any]]]:
    """焦段 → 该焦段的在售镜头行。只读目录;读失败返回空表(调用方按「没认出」处理)。

    ``catalog_reader`` 让调用方传入自己那份目录读取器,全链路只有一个目录接缝
    ——product_resolver 的既有测试打桩打在它那一侧,这里必须跟着走同一份桩。
    """
    reader = catalog_reader or list_product_catalog
    try:
        products = reader(limit=500).get("products") or []
    except Exception:
        # 目录读不到就当没有焦段表:解析不出产品 → 上游走「没认出」而不是硬猜。
        logger.warning("vkpi.product_focal_family.catalog_read_failed", exc_info=True)
        products = []
    index: dict[int, list[dict[str, Any]]] = {}
    for product in products:
        if not isinstance(product, dict):
            continue
        sku = str(product.get("sku") or "")
        # IMAGE-AWARDS-* 是活动/奖项页,历来被误当产品,绝不参与产品解析。
        if not sku or sku.upper().startswith("IMAGE-AWARDS"):
            continue
        if not _is_lens_row(product):
            continue
        focals = _row_focals(product)
        # 套装行(EPIC 25/35/50/65/75/100/135mm ANAMORPHIC CINE SET)列了七个焦段。
        # 它确实含 135mm,但把它算进「135mm 家族」只会虚增款数、拖歪产品锚——
        # 操作员说「135」时想的是一支 135,不是一整套。三个以上焦段即判为套装。
        if len(focals) >= 3:
            continue
        for focal in focals:
            index.setdefault(focal, []).append(product)
    return index


def _blank(match: "re.Match[str]") -> str:
    # 等长空格替换:后续判据要看命中位置的左右邻字,长度必须保持不变。
    return " " * len(match.group(0))


def _mask_urls(text: str) -> str:
    return _URL_SPAN_RE.sub(_blank, text)


def _mask_ranges(text: str) -> str:
    """把「10到50」「55 to 135」「between 24 and 85」这类区间整段抹掉。

    只用在裸数字这条路上。写了单位的「24mm 到 85mm」是操作员真的在点两个焦段,
    交给 multiple_focals 如实回问,不在这里抹。
    """
    return _RANGE_SPAN_RE.sub(_blank, text)


def explicit_focals(text: Any) -> list[int]:
    """Read explicit ``135mm`` focal requests, excluding format/FOV phrases.

    URL 里的数字先抹掉:链接里的 24mm 可能是别家产品的评测页,不是操作员在点名产品。
    ``35mm film/胶片`` 与 ``50mm equivalent/等效`` 默认属于格式或视角；只有同时
    存在光圈、系列、品牌、镜头等明确产品锚时才按镜头焦段保留。
    """
    low = _mask_urls(str(text or "").lower())
    has_product_anchor = _EXPLICIT_LENS_PRODUCT_ANCHOR_RE.search(low) is not None
    values: set[int] = set()
    for match in _MM_FOCAL_RE.finditer(low):
        before = low[max(0, match.start() - 40):match.start()]
        after = low[match.end():match.end() + 40]
        non_lens_context = bool(
            _NON_LENS_MM_SUFFIX_RE.search(after)
            or _NON_LENS_MM_PREFIX_RE.search(before)
        )
        if non_lens_context and not has_product_anchor:
            continue
        values.add(int(match.group(1)))
    return sorted(value for value in values if _FOCAL_MIN <= value <= _FOCAL_MAX)


def bare_focal_numbers(text: Any, *, strict_word: bool = True) -> list[int]:
    """裸数字里哪些**可能**是焦段。这里只做「不是在数数」的判据,还不查目录。

    判据(必须全部满足):
    - 两位或三位数,且是独立整词(不贴字母/小数点/斜杠/@/货币号);
    - 不在 URL 里;
    - 不在一个区间里(「10到50」「55 to 135」两端都不算);
    - 后面不紧跟量词或单位(个/位/万/小时/天/k/%/followers/W/nit…);
    - 前面不是排名/比较/价格/指标词(前/第/超过/排名/预算/完播率/top/between/>/$…)。

    是否真是焦段,由调用方拿目录焦段表判定——目录里没有的数字一律不认。

    ``strict_word=False`` 只放开「数字后面粘字母」这一条(给 "55evo" 这类打法用),
    URL / 区间 / 量词 / 比较词四道判据一条不减。
    """
    raw = str(text or "")
    if not raw.strip():
        return []
    masked = _mask_ranges(_mask_urls(raw))
    found: list[int] = []
    pattern = _BARE_NUMBER_RE if strict_word else _LOOSE_NUMBER_RE
    for match in pattern.finditer(masked):
        value = int(match.group(1))
        if not (_FOCAL_MIN <= value <= _FOCAL_MAX):
            continue
        if _COUNTER_SUFFIX_RE.match(masked[match.end():]):
            continue
        if _COUNTER_PREFIX_RE.search(masked[: match.start()]):
            continue
        if value not in found:
            found.append(value)
    return found


def _narrow(rows: list[dict[str, Any]], *, mount: str, series: str) -> list[dict[str, Any]]:
    narrowed = list(rows)
    if series:
        by_series = [
            row for row in narrowed
            if str(row.get("series") or "").strip().lower() == series.strip().lower()
        ]
        if by_series:
            narrowed = by_series
    if mount:
        # 卡口是硬约束:操作员点名了卡口,标了别的卡口的行必须出局。
        # 目录里 mount 为空的旧行(VL-LEN*)无从判断,同样出局——宁可少认,不可错认。
        narrowed = [row for row in narrowed if str(row.get("mount") or "").strip() == mount]
    return narrowed


def available_mounts(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(row.get("mount") or "").strip() for row in rows if str(row.get("mount") or "").strip()})


def focal_family_decision(
    text: Any,
    *,
    mount: str = "",
    series: str = "",
    catalog_reader: Any = None,
) -> dict[str, Any] | None:
    """解析一条 query 的焦段诉求。返回判定,或 None(压根没提焦段)。

    返回 dict 的 ``status``:
    - ``unique``    —— 卡口/系列把候选收窄到唯一一行(或操作员写了「135mm」而目录里
                       就这一支),``product`` 是那一行目录真值;裸数字绝不走这条;
    - ``family``    —— 同焦段多款,不挑具体型号,产品证据词用焦段本身;
    - ``no_catalog_match`` —— 明确提了焦段,但目录里没有这个焦段的镜头;
    - ``mount_unavailable`` —— 焦段有,但操作员点名的卡口没有这一支。

    ``ambiguous_focals`` 情形(一次提了多个焦段)返回 ``no_catalog_match`` 之外的
    ``multiple_focals``,同样交给调用方如实告知,绝不替操作员挑一个。
    """
    raw = _text(text)
    if not raw:
        return None
    explicit = explicit_focals(raw)
    source = "explicit_mm"
    focals = list(explicit)
    if not focals:
        focals = bare_focal_numbers(raw)
        source = "bare_number"
    if not focals:
        return None

    index = focal_family_index(catalog_reader)
    known = [value for value in focals if index.get(value)]

    if source == "bare_number":
        # 裸数字没有单位兜底,只认目录里真实存在的焦段;一个都对不上就当没提过焦段,
        # 走原本的搜索路径(绝不因为一个孤立数字就拦住一次正常搜索)。
        if not known:
            return None
    elif not known:
        return {
            "status": "no_catalog_match",
            "source": source,
            "focal": focals[0],
            "focals": focals,
            "requested_mount": mount,
            "requested_series": series,
            "rows": [],
            "available_focals": sorted(index),
        }

    if len(known) > 1:
        return {
            "status": "multiple_focals",
            "source": source,
            "focal": known[0],
            "focals": known,
            "requested_mount": mount,
            "requested_series": series,
            "rows": [],
            "available_focals": sorted(index),
        }

    focal = known[0]
    rows = index[focal]
    narrowed = _narrow(rows, mount=mount, series=series)
    if mount and not narrowed:
        return {
            "status": "mount_unavailable",
            "source": source,
            "focal": focal,
            "focals": [focal],
            "requested_mount": mount,
            "requested_series": series,
            "rows": rows,
            "available_mounts": available_mounts(rows),
        }
    narrowed_by = "mount" if mount else ("series" if series and len(narrowed) < len(rows) else "")
    # 认定具体 SKU 的唯一条件:操作员自己给了收窄线索(卡口/系列),或者至少把单位
    # 连着焦段一起写了(「135mm」)。**裸数字 + 恰好只有一行的焦段家族不算**——
    # 那是目录形状替操作员做了选择,而这正是「24 小时」一路锚到 AF-24MM-F18-Z、
    # 还带上 $379 定价的最后一环。此时退回焦段家族:证据词只落在「Viltrox 24mm」,
    # sku 留空、价格留 None,谁也没被替操作员挑中。
    if len(narrowed) == 1 and (narrowed_by or source == "explicit_mm"):
        return {
            "status": "unique",
            "source": source,
            "focal": focal,
            "focals": [focal],
            "requested_mount": mount,
            "requested_series": series,
            "narrowed_by": narrowed_by,
            "product": narrowed[0],
            "rows": rows,
            "available_mounts": available_mounts(rows),
        }
    return {
        "status": "family",
        "source": source,
        "focal": focal,
        "focals": [focal],
        "requested_mount": mount,
        "requested_series": series,
        "narrowed_by": narrowed_by,
        "rows": narrowed or rows,
        "available_mounts": available_mounts(narrowed or rows),
    }


# 可以写进产品锚的系列词。只有**整族每一行都带**这个词时才写——差一行都不写。
# 事故形状:23mm 家族里有一支 MF 23mm T1.5 Cine(M43)和两支 AF 23mm F1.4 平面定焦。
# 只要有一行是 Cine 就把整族叫成「23mm Cine」,街拍摄影师的诉求会被读成电影镜头。
_ANCHOR_FAMILY_WORDS = ("EPIC", "LAB", "EVO", "Air", "Vintage")


def _unanimous_family_word(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    blobs = [_row_blob(row) for row in rows]
    for word in _ANCHOR_FAMILY_WORDS:
        needle = word.lower()
        if all(re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", blob) for blob in blobs):
            return word
    series_values = {str(row.get("series") or "").strip() for row in rows}
    if len(series_values) == 1:
        return series_values.pop()
    return ""


def family_projection(decision: dict[str, Any]) -> dict[str, Any]:
    """把「同焦段多款」的判定摊成一个只带焦段锚的产品投影。

    刻意留空的字段:``sku``(没挑具体型号就绝不编一个)、``price_usd``
    (家族里价格不一,编一个就是假数)。``series`` 只在整族同系列时才填。
    """
    rows = list(decision.get("rows") or [])
    focal = int(decision.get("focal") or 0)
    mounts = available_mounts(rows)
    series_values = sorted({str(row.get("series") or "").strip() for row in rows if str(row.get("series") or "").strip()})
    detail_values = sorted({str(row.get("category_detail") or "").strip() for row in rows if str(row.get("category_detail") or "").strip()})
    mount_text = " / ".join(mounts) if mounts else "多卡口"
    description = (
        f"{focal}mm 焦段目录内共 {len(rows)} 款在售（{mount_text}）。"
        "操作员没有点名具体型号，按焦段找人。"
    )
    family_word = _unanimous_family_word(rows)
    anchor_name = f"Viltrox {focal}mm {family_word}".strip() if family_word else f"Viltrox {focal}mm"
    return {
        "sku": "",
        "model_name": anchor_name,
        "marketing_name": anchor_name,
        "category_main": "Lens",
        "category_detail": detail_values[0] if len(detail_values) == 1 else "",
        "series": series_values[0] if len(series_values) == 1 else "",
        "price_usd": None,
        "description": description,
        "resolution_kind": "focal_family",
        "focal_mm": focal,
        "focal_family_size": len(rows),
        "focal_family_mounts": mounts,
        "focal_family_skus": [str(row.get("sku") or "") for row in rows][:12],
    }


def family_options(decision: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    """给操作员看的可选项(真目录行,不编造)。"""
    options: list[dict[str, Any]] = []
    for row in (decision.get("rows") or [])[: max(1, int(limit))]:
        options.append(
            {
                "sku": row.get("sku"),
                "name": row.get("marketing_name") or row.get("model_name"),
                "mount": row.get("mount"),
                "series": row.get("series"),
            }
        )
    return options
