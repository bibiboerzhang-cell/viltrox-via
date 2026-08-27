"""库内召回语言硬筛的**取值来源**分层(2026-08-26)。

用户裁令:「语言根据 youtube 的视频和发的东西去估算吧,就和粉丝估算的一样,
不要直接砍掉太多东西了」。池里 ``language`` 只有 586/2036 填了值(28.8%),
历史硬筛拿这一个空字段就把 71% 的人判成「未知」一刀砍掉。

本模块只回答一个问题:**「这个人的语言,我们凭什么这么说?」**
它把答案拆成四档,并且四档在证据里**彼此可分**:

* ``self_reported`` —— 平台/资料上他自己填的。**要能证明**:逐行来源串
  (``language_source`` / facet 证据块的 ``source``)说它是平台资料声明,
  或者值取自 ``vkpi_kol_pool.language`` 这一列本身 —— 迁移 039/305 把这一列
  定义为「平台/创作者对自己的声明」,列契约就是那份证明。
* ``inferred``      —— 我们从他自己发的东西(简介 / 视频标题)推断出来的。
  两条推断车道都归这一档:落在**另一列**的投票推断
  (``vkpi_kol_pool.language_inferred``,迁移 305,带 high/medium/low 档位),
  以及在线腿 ``profile_online_facets.adapt_language`` 的当场检测
  (来源串形如 ``provider_public_content_language_v1``)。**永远不冒充自报值。**
* ``projected``     —— 有值,但**证不出是他自己说的**:来源串指向别处
  (例如 ``platform_content_metadata`` = 视频元数据里的音轨语言,那是平台对
  「这条片子」的标注,不是他对「自己」的声明),或者根本读不到来源。
  2026-08-26 复核前这一档被并进 ``self_reported`` —— 那是替他伪造声明。
* ``unknown``       —— 什么都没有。他回到「未知」档,由既有三态
  (``profile_recall_filter_modes.tri_state_outcome``,缺省 ``require``)决定拦不拦。
  与今晚上线的新鲜闸拆桶同口径:**推断不出来的人是「未知」,不是「不合格」。**

**分出 ``projected`` 不改变任何一个人的去留**:取值优先级(自报列 → 推断列 → 未知)
与拆分前逐字一致,变的只是这个值头上那句「谁说的」。取数腿(``language_sql_filter``)
下推的仍是「自报列 ∪ 推断列」并集,仍是闸的超集。

red line:

* 本模块**只读不写**,不做推断、不碰文本、不引第三方依赖 —— 推断由推断车道
  在写入侧完成并落进独立列,这里只按来源分层取值。
* 判定阈值一律不动:门槛(粉丝/新鲜度/器材证据/产品锚)与本模块无关,
  这里既不放宽也不收紧任何一条,只是把「他说什么语言」这个**事实**问得更全。
* 证据里只出现字段名(``bio`` / ``video_titles``)与语言码,**绝不回带原文**。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable


#: 取值来源四档。写进 ``qualification_evidence.language.origin``,界面按此如实标注。
ORIGIN_SELF_REPORTED = "self_reported"
ORIGIN_INFERRED = "inferred"
ORIGIN_PROJECTED = "projected"
ORIGIN_UNKNOWN = "unknown"

#: 自报值的历史默认来源串。**刻意保持原字面**(既有契约,由测试钉住)。
SELF_REPORTED_SOURCE = "vkpi_kol_profiles.language"
#: 推断值的来源串 —— 另一列,与自报值分开存,操作员一眼可分。
INFERRED_SOURCE = "vkpi_kol_pool.language_inferred"
#: ``projected`` 档读不到逐行来源串时的落点(读得到就如实回那一串,见
#: :func:`language_evidence_source`)。它只说一句「这个值没有出处」,
#: **不冒认任何一列** —— 回落自报列等于替他签一份他没签过的声明。
PROJECTED_SOURCE = "unattributed.language"
#: ``unknown`` 档的落点:没有取到值,就没有出处可写。空串是这里唯一诚实的答案 ——
#: 「不知道」不许写成任何一列的名字。
UNKNOWN_SOURCE = ""

#: 「谁说的」这句话去哪儿读。标量键先读,读不到再拆 facet 证据块。
#: ``language_source`` 是在线腿 ``profile_online_qualification._candidate_row`` 落的真键。
LANGUAGE_SOURCE_KEYS: tuple[str, ...] = ("language_source", "language_origin")
#: facet 证据块:``{"value","source","confidence","evidence_fields"}``,
#: 由 ``profile_online_facets.adapt_language`` 产出,挂在 ``facet_evidence.language``。
LANGUAGE_EVIDENCE_KEYS: tuple[str, ...] = ("facet_evidence", "language_evidence")

#: 来源串长这样才算得上「他自己在平台资料里填的」。与
#: ``profile_online_facets._LANGUAGE_SOURCES`` 的自报子集同口径 —— 那一侧把
#: ``provider_public_content_language_v1``(我们自己当场检测的)也收在同一个集合里,
#: 那是它判「可不可信」的口径,**不是**判「谁说的」的口径,这里不照抄。
SELF_DECLARED_SOURCES: frozenset[str] = frozenset({
    "platform_profile", "provider_declared", "creator_declared",
    "declared", "self_declared", "self_report", "self_reported",
    "platform_self_reported", "vkpi_kol_pool.language", "vkpi_kol_profiles.language",
})

#: 来源串里出现这些片段 = 这个值是从公开内容里倒推的,不是他自己填的。
#: 与门面 ``LanguageProvenance.ts`` 的 ``INFERRED_SOURCE_MARKERS`` 同一份词表,
#: 两侧对不上就会出现「服务端说推断、门面说自报」这类分歧。
INFERRED_SOURCE_MARKERS: tuple[str, ...] = (
    "infer", "detect", "estimat", "derive", "guess", "langdetect",
    "public_content", "content_text", "from_content", "content_inference",
)

#: **列契约**:没有逐行来源时,只有从这些容器里取到的 ``language`` 仍算得上「自报」——
#: 迁移 039 建的 ``vkpi_kol_pool.language`` 与迁移 305 的列注释把这一列定义成
#: 「平台/创作者对自己的声明」,``row`` 与投影出来的 ``item`` 装的都是这一列。
#: ``candidate_facets`` 是给前端重算分布用的展示投影(``candidate_facets()``
#: 只做 ``_normal_dimension`` 归一,不带任何来源),没有列契约可援引 ——
#: 从那里取到的值一律进 ``projected``,**不许默认成自报**。
COLUMN_CONTRACT_CONTAINERS: frozenset[str] = frozenset({"row", "item"})

#: 与推断车道的**读取契约**。列/键未迁移的旧布局取不到值 = 这一路没有信号,
#: 判定照跑,那个人只是留在「未知」档 —— 绝不因为读不到就误杀或误放。
#: 两种拼写都认(``language_inferred`` = 列名侧;``inferred_language`` = 门面侧已在读的键),
#: 免得两条车道各写各的拼法,在中间对不上。
INFERRED_VALUE_KEYS: tuple[str, ...] = ("language_inferred", "inferred_language")
INFERRED_METHOD_KEYS: tuple[str, ...] = ("language_inferred_method", "inferred_language_method")
#: ``language_inferred_source`` = 迁移 305 的真列名(哪段自述文本投出了这一票)。
INFERRED_BASIS_KEYS: tuple[str, ...] = (
    "language_inferred_source",
    "language_inferred_basis",
    "language_inferred_from",
    "inferred_language_basis",
)
INFERRED_CONFIDENCE_KEYS: tuple[str, ...] = (
    "language_inferred_confidence",
    "inferred_language_confidence",
)
#: 迁移 305 把置信度存成**档位文字**(high/medium/low),不是小数。两种都认,
#: 认不出的一律返回 None —— 宁可说「不知道多有把握」,也不编一个数字出来。
CONFIDENCE_TIERS = ("high", "medium", "low")

#: 迁移 305 落库的推断四列。**读路径按这份白名单 SELECT**:列没迁移的旧布局
#: 自动退成 ``NULL AS 列名``,而不是把整条搜索炸掉(与 topic_details_json 同机制)。
INFERRED_POOL_COLUMNS: tuple[str, ...] = (
    "language_inferred",
    "language_inferred_confidence",
    "language_inferred_source",
    "language_inferred_method",
)

#: 置信档从低到高。门槛按这个序比大小。
CONFIDENCE_ORDER: tuple[str, ...] = ("low", "medium", "high")

#: **推断值参与硬筛的最低置信档** = ``medium``(2026-08-26 复核后从 ``low`` 抬上来)。
#:
#: **为什么原来的依据不成立。** 旧口径拿 563 个「有平台自报语言」的人当地面真值反跑,
#: 得出 low 档判英语的准确率 96.7%(30 判 /29 真),据此判定「每一档边际准确率都够高,
#: 因此不设门槛」。这个外推在统计上站不住:``_confidence()`` 里 low 档的定义就是
#: ``top_votes < 2`` —— **只有一条文本给出了判定**,没有第二条来佐证;而「在平台上把
#: 语言字段填了」的人恰恰是资料完整、文本充足的那一类,两个群体不同分布,
#: 前者的准确率不能拿来替后者说话。
#:
#: **重新用同分布样本估。** 取本地库里 floor 真正管得着的那批人 —— 自报语言为空、
#: 推断值落在 low 档的 327 人 —— 随机抽 40 人(seed 20260826),把喂给检测器的原文
#: 逐条读过来人工判(判据与 langdetect 无关:读简介/标题内容 + 地理与频道线索):
#:
#: * 整体判对 37/40 = **92.5%**(Wilson 95% CI **[80.1%, 97.4%]**);
#: * 「判成英语」这一票 32/35 = **91.4%**(Wilson 95% CI **[77.6%, 97.0%]**)。
#:
#: 三个错全是同一种形状,也正是这一档结构上会犯的错:韩语粉丝号的简介用英文写着
#: "Korean … FAN Account";奥地利/斯洛伐克摄影师的简介只有相机品牌、邮箱和 "DM";
#: 瑞士法语区创作者的简介里唯一的实词短语是法语。**三个错全部指向同一个方向 ——
#: 把不说英语的人判成英语**,也就是全部会漏进英语搜索,没有一个是反方向。
#:
#: 结论:低档的真实「判英语」准确率点估 91.4%,置信区间下界 77.6%(五个里可能错一个)。
#: 旧决策所依赖的 96.7% 已被同分布样本否掉,因此不再无门槛放行。
#:
#: **代价是实测过的,不藏着。** 本地库「美国 + 5万粉 + 英语」这条真查询上,语言腿:
#:
#: ====================  ========  =================================
#: 门槛                   放行人数   其中靠推断进来的
#: ====================  ========  =================================
#: 不接推断(只看自报列)       82      0
#: ``low``(旧默认)          161     79
#: ``medium``(现默认)       109     27
#: ``high``                  93     11
#: ====================  ========  =================================
#:
#: 把这 52 个「只有 low 档撑着」的人全量(不是抽样)读了一遍:50 人确实是英语创作者,
#: 2 人证不出(一个提格里尼亚语频道配了英文简介,一个整份简介只有一个花体 handle)。
#: 也就是说这一刀在**这条**查询上砍掉的多半是真英语创作者 —— 但门槛是**全局常量**,
#: 而全局 low 档的准确率是上面那个 91.4%,不是这条美国查询里的 96%。
#: 按查询挑门槛做不到,只能按全局最保守的那个数定。
#:
#: 取 ``medium`` 而不是 ``high`` 的理由:``medium`` 是**投票机制真正起过作用**的
#: 最低一档(``top_votes >= 2``,至少两条他自己写的文本互相印证);``low`` 则是
#: 「一条文本、无人佐证」,不是一个置信度,是一个「没有第二意见」的标记。
#:
#: 方向性上也站得住:被门槛挡下的人回到「未知」档(**不是「不合格」**),操作员点
#: 「含未知」就能全部拿回来 —— 而「含未知」那一格的 SQL 腿在本波刚被修好,
#: 这条退路是真通的;反过来,一句错误的「他说英语」在证据里看不出错,拿不回来。
#:
#: 想改的把这个常量动一格,SQL 腿与闸会同时跟着走(两侧共用
#: :func:`meets_confidence_floor`),不存在一边松一边紧。门槛在 ``low`` 以上时,
#: **置信度读不出来的推断值不参与硬筛** —— 证不出达标就不放行。
MIN_INFERRED_CONFIDENCE = "medium"

#: 推断依据允许出现在证据里的字段名白名单。**只出字段名,不出原文。**
ALLOWED_BASIS = ("bio", "video_titles", "bio+video_titles")


def _facets(item: Any) -> dict[str, Any]:
    facets = item.get("candidate_facets") if isinstance(item, dict) else None
    return facets if isinstance(facets, dict) else {}


def _first_present(sources: Iterable[Any], keys: Iterable[str]) -> Any:
    """按 row -> item -> candidate_facets 的既有优先级取第一个非空值。"""
    return _first_present_labelled((("", source) for source in sources), keys)[0]


def _first_present_labelled(
    containers: Iterable[tuple[str, Any]], keys: Iterable[str],
) -> tuple[Any, str]:
    """同 :func:`_first_present`,外加**值是从哪个容器里取到的**。

    容器名是归属判定的一部分:没有逐行来源串时,只有列契约容器(``row`` / ``item``,
    装的是 ``vkpi_kol_pool.language``)取到的值仍算得上自报,展示投影
    (``candidate_facets``)取到的不算 —— 见 :data:`COLUMN_CONTRACT_CONTAINERS`。
    """
    key_list = list(keys)
    for name, source in containers:
        if not isinstance(source, dict):
            continue
        for key in key_list:
            value = source.get(key)
            if value not in (None, "", [], {}):
                return value, name
    return None, ""


def _clean_token(value: Any, allowed: tuple[str, ...] | None = None) -> str:
    token = " ".join(str(value or "").split()).strip().lower()
    if allowed is not None and token not in allowed:
        return ""
    return token[:40]


def _optional_confidence(value: Any) -> str | float | None:
    """档位文字(high/medium/low)原样留;小数收进 [0,1];其它一律 None。"""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        tier = value.strip().lower()
        if tier in CONFIDENCE_TIERS:
            return tier
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN:诚实丢弃,不装作有置信度
        return None
    return max(0.0, min(1.0, number))


def language_evidence_block(sources: Iterable[Any]) -> dict[str, Any]:
    """取 facet 证据块 ``facet_evidence.language``(或 ``language_evidence``)。

    形状 ``{"value","source","confidence","evidence_fields","version"}``,
    由 ``profile_online_facets.adapt_language`` 产出。取不到返回空 dict。
    """
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in LANGUAGE_EVIDENCE_KEYS:
            block = source.get(key)
            if not isinstance(block, dict):
                continue
            inner = block.get("language")
            inner = inner if isinstance(inner, dict) else block
            if isinstance(inner, dict) and _clean_token(inner.get("source")):
                return inner
    return {}


def language_source_token(sources: Iterable[Any]) -> str:
    """把「这个语言值是谁说的」从 row / item / facets 上读出来;读不到返回空串。

    两种落法都认:标量键 ``language_source``(在线腿 ``_candidate_row`` 落的),
    以及 facet 证据块 ``facet_evidence.language.source``
    (``profile_online_facets.adapt_language`` 的完整出参)。
    """
    source_list = list(sources)
    token = _clean_token(_first_present(source_list, LANGUAGE_SOURCE_KEYS))
    return token or _clean_token(language_evidence_block(source_list).get("source"))


def classify_language_origin(token: Any, *, container: str = "") -> str:
    """来源串 -> 归属档。**证不出「他自己填的」就不许写「自报」。**

    读不到来源串时不是「默认自报」,而是退到**列契约**:值取自
    ``vkpi_kol_pool.language`` 那一列(``row`` / ``item``)才算自报,
    展示投影(``candidate_facets``)取到的一律 ``projected``。
    """
    text = _clean_token(token)
    if not text:
        return ORIGIN_SELF_REPORTED if container in COLUMN_CONTRACT_CONTAINERS else ORIGIN_PROJECTED
    if text in SELF_DECLARED_SOURCES:
        return ORIGIN_SELF_REPORTED
    if any(marker in text for marker in INFERRED_SOURCE_MARKERS):
        return ORIGIN_INFERRED
    return ORIGIN_PROJECTED


def admitted_confidence_tiers(floor: Any = MIN_INFERRED_CONFIDENCE) -> tuple[str, ...]:
    """门槛之上(含)的档位。``low`` 门槛 = 全档放行,返回空 tuple 表示「不必过滤」。"""
    tier = str(floor or "").strip().lower()
    if tier not in CONFIDENCE_ORDER or tier == CONFIDENCE_ORDER[0]:
        return ()
    return CONFIDENCE_ORDER[CONFIDENCE_ORDER.index(tier):]


def meets_confidence_floor(confidence: Any, floor: Any = MIN_INFERRED_CONFIDENCE) -> bool:
    """推断值的置信档够不够格参与硬筛。

    门槛为 ``low``(默认)时恒真 —— 不设门槛,连读不出档位的也照收。
    门槛抬高后,读不出档位 = **证不出达标** = 不放行(保守方向)。
    """
    admitted = admitted_confidence_tiers(floor)
    if not admitted:
        return True
    return str(confidence or "").strip().lower() in admitted


def resolve_candidate_language(
    row: Any,
    item: Any,
    *,
    normalize: Callable[[Any], list[str]],
    min_confidence: Any = MIN_INFERRED_CONFIDENCE,
) -> dict[str, Any]:
    """自报优先、推断兜底、两样都没有就是未知 —— 并且**每一句归属都要能证明**。

    返回的 ``values`` 是**唯一**参与硬筛比对的那组值,``origin`` 说明它从哪来。
    取值优先级(自报列 → 推断列 → 未知)与来源分层拆分前**逐字一致**:
    ``origin`` 判成 ``projected`` 不会让这个值退出硬筛,只是不再声称是他自己填的。
    换句话说 —— **本函数的归属修正一个人的去留都不改,改的只是那句话的真假。**

    ``self_reported_values`` 从此**只装证得出是自报的那部分**:证不出的挪进
    ``projected_values``。前端拿它拼「他自己填的是 X」那半句(``LanguageProvenance.ts``
    的 ``divergenceLabel``),所以它必须是一句转述得起的话,不能是一个猜测。
    """
    containers = (("row", row), ("item", item), ("facets", _facets(item)))
    sources = tuple(value for _name, value in containers)
    self_raw, self_container = _first_present_labelled(containers, ("language",))
    self_values = list(normalize(self_raw)) if self_raw not in (None, "") else []
    source_token = language_source_token(sources)
    # 归属只在真有值时才谈得上;没有值就没有「谁说的」这个问题。
    self_origin = (
        classify_language_origin(source_token, container=self_container)
        if self_values else ORIGIN_UNKNOWN
    )
    inferred_raw = _first_present(sources, INFERRED_VALUE_KEYS)
    column_inferred = list(normalize(inferred_raw)) if inferred_raw not in (None, "") else []
    confidence = _optional_confidence(_first_present(sources, INFERRED_CONFIDENCE_KEYS))
    # 置信档不够门槛的推断值:不参与硬筛,但**照实透出**(旁挂的 inferred_values 不抹),
    # 免得操作员看见一个「未知」却不知道我们其实有一票没敢用的判断。
    admitted = bool(column_inferred) and meets_confidence_floor(confidence, min_confidence)

    if self_values:
        origin, values = self_origin, self_values
    elif admitted:
        origin, values = ORIGIN_INFERRED, column_inferred
    else:
        origin, values = ORIGIN_UNKNOWN, []

    # 自报列里那个值,按它自己的归属各归各位 —— 证不出自报的绝不留在 self 桶里。
    # 在线腿当场检测出来的语言(``provider_public_content_language_v1``)属于
    # 「我们推断的」,与迁移 305 的投票推断同档,一起并进 inferred_values。
    self_declared = self_values if self_origin == ORIGIN_SELF_REPORTED else []
    projected = self_values if self_origin == ORIGIN_PROJECTED else []
    content_inferred = self_values if self_origin == ORIGIN_INFERRED else []
    inferred_values = content_inferred + [
        value for value in column_inferred if value not in content_inferred
    ]

    resolution: dict[str, Any] = {
        "values": values,
        "origin": origin,
        "self_reported_values": self_declared,
        "inferred_values": inferred_values,
        "projected_values": projected,
        # 读到的那句原话。读不到就是空串 —— 空串本身也是诚实的信息:
        # 「这个值没有逐行来源,我们是靠列契约认下来的」。
        "origin_source": source_token,
    }
    if origin == ORIGIN_INFERRED:
        method = _clean_token(_first_present(sources, INFERRED_METHOD_KEYS))
        basis = _clean_token(_first_present(sources, INFERRED_BASIS_KEYS), ALLOWED_BASIS)
        if content_inferred:
            # 在线腿这一条不是迁移 305 那条车道:方法/把握度写在 facet 证据块里,
            # 305 的四列根本不存在。读它自己的那一份,读不到就诚实留空 —— 不借用别人的。
            block = language_evidence_block(sources)
            method = method or _clean_token(source_token)
            confidence = _optional_confidence(block.get("confidence"))
            # 证据里的 source 必须指向真正干活的那条腿。这一票不是迁移 305 那一列判的,
            # 就绝不许写 ``vkpi_kol_pool.language_inferred`` —— 那会是一句新的假话。
            resolution["inference_source"] = source_token
        resolution["inference_method"] = method or None
        resolution["inference_basis"] = basis or None
        resolution["inference_confidence"] = confidence
    elif column_inferred and not self_values:
        # 只有这一种情况会走到这里:有推断值,但档位没过门槛。
        resolution["inference_confidence"] = confidence
        resolution["inference_below_floor"] = str(min_confidence or "").strip().lower()
    return resolution


def resolve_language_match_key(
    row: Any,
    item: Any = None,
    *,
    match_key: Callable[[Any], str],
    min_confidence: Any = MIN_INFERRED_CONFIDENCE,
) -> tuple[str, str]:
    """硬筛用的单值口径:返回 ``(语言码, 来源档)``,判不出就是 ``("", "unknown")``。

    与 :func:`resolve_candidate_language` 同一套优先级(自报 → 推断 → 未知),
    只是把「一个人可能有多个语言」压成三态硬筛需要的那一个 key。
    """
    resolution = resolve_candidate_language(
        row, item, normalize=lambda value: [key for key in (match_key(value),) if key],
        min_confidence=min_confidence,
    )
    values = resolution.get("values") or []
    return (str(values[0]) if values else ""), str(resolution.get("origin") or ORIGIN_UNKNOWN)


def language_evidence_source(resolution: Any, *, self_source: str = SELF_REPORTED_SOURCE) -> str:
    """证据里的 ``source``:值从哪一列/哪条腿来,就写哪一条 —— 来源可追的落点。

    **四档各有各的答案,没有一档回落到自报列。**

    * ``self_reported`` —— 自报列(``self_source`` 由调用方指名是哪条腿的自报列);
    * ``inferred``      —— 两条推断腿各写各的:在线腿写它自己的来源串
      (``inference_source``),迁移 305 那条列车道写 :data:`INFERRED_SOURCE`。
      **不许互相冒名。**
    * ``projected``     —— 有值但证不出是他自己说的:读得到逐行来源串就**如实回那一串**
      (``platform_content_metadata`` 之类 —— 那才是这个值真正的出处),
      读不到才落 :data:`PROJECTED_SOURCE`,只说「没有出处」,不冒认任何一列;
    * ``unknown``       —— 没有取到值就没有出处,回 :data:`UNKNOWN_SOURCE`(空串)。

    2026-08-26 复核前只有 ``inferred`` 开了特例,``projected`` 与 ``unknown`` 一律回落
    ``self_source`` —— 那是把「证不出」和「不知道」两件事都说成「他自己说的」。
    认不出的档位(拼写漂移 / 将来的新档)一律走同一个保守出口:宁可空着,
    **绝不退化成自报** —— 与 ``self_reported`` 那个明牌布尔同一个方向。

    只改这个值头上「谁说的」那句话,不碰 ``values`` —— 一个人的去留都不改。
    """
    entry = resolution if isinstance(resolution, dict) else {}
    origin = str(entry.get("origin") or ORIGIN_UNKNOWN)
    if origin == ORIGIN_SELF_REPORTED:
        return self_source or SELF_REPORTED_SOURCE
    if origin == ORIGIN_INFERRED:
        return str(entry.get("inference_source") or "").strip()[:80] or INFERRED_SOURCE
    if origin == ORIGIN_PROJECTED:
        return str(entry.get("origin_source") or "").strip()[:80] or PROJECTED_SOURCE
    return UNKNOWN_SOURCE


def language_gate_evidence(
    resolution: dict[str, Any],
    *,
    targets: list[str],
    filter_requested: bool,
    invalid_targets: list[str],
    passed: bool,
    self_source: str = SELF_REPORTED_SOURCE,
) -> dict[str, Any]:
    """``qualification_evidence.language`` 的完整块。

    ``values`` / ``targets`` / ``filter_requested`` / ``invalid_targets`` / ``passed`` /
    ``source`` 六个既有键**一个不少**(下游契约),只在旁边加上来源分层的诚实字段。

    ``source`` 的**键名与类型不动**,只有 ``projected`` / ``unknown`` 两档的取值不再
    回落自报列(见 :func:`language_evidence_source`):自报档与推断档逐字不变,
    另两档改说真话。这只影响这一格显示什么,不影响 ``values`` / ``passed``。
    """
    origin = str(resolution.get("origin") or ORIGIN_UNKNOWN)
    evidence: dict[str, Any] = {
        "values": list(resolution.get("values") or []),
        "targets": list(targets),
        "filter_requested": bool(filter_requested),
        "invalid_targets": list(invalid_targets),
        "passed": bool(passed),
        "source": language_evidence_source(resolution, self_source=self_source),
        "origin": origin,
        "inferred": origin == ORIGIN_INFERRED,
        # 门面的**第二道防线**:``origin`` 这个字符串万一没被认出来(新档位、拼写漂移),
        # 也不许退化成「自报」。这两个布尔是明牌 —— 没证明就是 False,
        # 门面读到 ``self_reported is False`` 就不许说「他自己填的」。
        "self_reported": origin == ORIGIN_SELF_REPORTED,
        "self_reported_values": list(resolution.get("self_reported_values") or []),
        "inferred_values": list(resolution.get("inferred_values") or []),
        # 有值、但证不出是他自己说的那一组(来源指向别处,或根本没有来源)。
        "projected_values": list(resolution.get("projected_values") or []),
        # 读到的那句原话;空串 = 没有逐行来源,归属是靠列契约认下来的。
        "origin_source": str(resolution.get("origin_source") or ""),
    }
    if origin == ORIGIN_INFERRED:
        basis = resolution.get("inference_basis")
        evidence["inference_method"] = resolution.get("inference_method")
        evidence["inference_basis"] = basis
        evidence["inference_confidence"] = resolution.get("inference_confidence")
        # 门面读的是 ``basis`` / ``evidence_fields``(LanguageProvenance.ts),
        # 两个键一起给,推断依据才说得出「个人简介」还是「作品标题」。
        evidence["basis"] = basis
        evidence["evidence_fields"] = [part for part in str(basis or "").split("+") if part]
    return evidence


#: SQL 侧取值口径:去空白 + 转小写,和 ``_language_match_key`` 的前两步逐字对齐。
#: 只用 ``TRIM`` / ``LOWER`` / ``COALESCE`` / ``substr`` 四个两方言同名同义的函数,
#: 零字面百分号、零 ``LIKE`` —— 避开 compat 适配器的转义陷阱(记忆:迁移 ASCII ? 陷阱同源)。
def _sql_value(column: str) -> str:
    return f"LOWER(TRIM(COALESCE({column}, '')))"


def _sql_match(expr: str, values: list[str], expr_params: list[Any]) -> tuple[str, list[Any]]:
    """``值 = 目标`` 或 ``值以「目标-」开头``(en 命中 en-gb / en-us),逐条 OR。

    ``expr_params`` = 绑在 ``expr`` 自己里的参数(置信度档位),每次展开 ``expr``
    都要原样重发一遍,顺序才对得上。
    """
    clauses: list[str] = []
    params: list[Any] = []
    for value in values:
        clauses.append(f"{expr} = ? OR substr({expr}, 1, ?) = ?")
        params.extend(expr_params)
        params.append(value)
        params.extend(expr_params)
        params.extend((len(value) + 1, f"{value}-"))
    return "(" + " OR ".join(clauses) + ")", params


def language_sql_filter(
    values: list[str],
    *,
    mode: Any = "require",
    has_inferred_column: bool = False,
    min_confidence: Any = MIN_INFERRED_CONFIDENCE,
    self_column: str = "p.language",
    inferred_column: str = "p.language_inferred",
    confidence_column: str = "p.language_inferred_confidence",
) -> tuple[str, list[Any]]:
    """取数腿的语言下推。返回 ``(where 片段, 参数)``;空串 = 这一腿不下推语言。

    **为什么是「同时认两列」而不是「SQL 层干脆不筛语言」**:取数腿是带
    ``LIMIT`` + ``ORDER BY p.id DESC`` 的**有限**候选生成器。把语言条件整个删掉
    并不等于「交给闸判」,而是让固定的行预算被大量注定被闸驳回的人占满,
    真正合格的人反而被挤出窗口 —— 那是拿一种静默丢人换另一种。
    下推「自报 ∪ 推断」这个**并集**则可证明不比闸更严:闸的判定值必是两列之一
    (自报优先),所以闸会放行的人一定落在本条件内;本条件只可能多带回一些
    闸随后自己会驳掉的人。

    三态口径与 ``profile_recall_filter_modes.tri_state_outcome`` 对齐:

    * ``require``        —— 两列任一命中;
    * ``include_unknown`` —— 两列任一命中,**或**两列都空(此前 SQL 腿完全无视 mode,
      「含未知」那一格结构上取不到任何人,自动放宽因此形同虚设);
    * ``exclude``        —— **不下推**。这是负向筛选,下推正向匹配等于把操作员
      点名要排除的人原样留下(既有 bug);排除交给闸,取数腿不做多余动作。
    """
    targets = [text for text in (str(value or "").strip().lower() for value in values or []) if text]
    if not targets:
        return "", []
    normalized_mode = str(mode or "require").strip().lower()
    if normalized_mode == "exclude":
        return "", []
    self_expr = _sql_value(self_column)
    self_sql, params = _sql_match(self_expr, targets, [])
    parts = [self_sql]
    # 「可用的推断值」表达式:门槛抬高时,档位不达标的推断值在 SQL 里就等同于空,
    # 于是「命中」和「未知」两侧永远同一口径,与闸里的 meets_confidence_floor 对齐。
    admitted = list(admitted_confidence_tiers(min_confidence)) if has_inferred_column else []
    inferred_expr = _sql_value(inferred_column)
    if admitted:
        guard = f"{_sql_value(confidence_column)} IN (" + ",".join("?" for _ in admitted) + ")"
        inferred_expr = f"(CASE WHEN {guard} THEN {inferred_expr} ELSE '' END)"
    if has_inferred_column:
        inferred_sql, inferred_params = _sql_match(inferred_expr, targets, admitted)
        parts.append(inferred_sql)
        params.extend(inferred_params)
    if normalized_mode == "include_unknown":
        unknown_sql = f"{self_expr} = ''"
        if has_inferred_column:
            unknown_sql += f" AND {inferred_expr} = ''"
            params.extend(admitted)
        parts.append(f"({unknown_sql})")
    return "(" + " OR ".join(parts) + ")", params


def language_hard_filter(
    filters: Any,
    conn: Any,
    table_columns: Callable[[Any, str], set[str]],
    *,
    min_confidence: Any = MIN_INFERRED_CONFIDENCE,
) -> tuple[str, list[Any]]:
    """两条取数腿共用的入口:从筛选字典直接算出语言下推片段。

    ``_language_values`` 是 ``recall_kol_profiles`` 绑好的那组字面量(原值 ∪ 语言主码),
    取不到时退回原始 ``languages``。**没点语言就不去问列在不在** —— 探列本身也是一条
    语句,不该出现在与语言无关的搜索里(线上 PG 走 information_schema,是一条 SELECT)。
    """
    source = filters if isinstance(filters, dict) else {}
    targets = source.get("_language_values") or source.get("languages") or []
    if not targets:
        return "", []
    return language_sql_filter(
        targets,
        mode=source.get("languages_mode"),
        has_inferred_column="language_inferred" in table_columns(conn, "vkpi_kol_pool"),
        min_confidence=min_confidence,
    )

__all__ = [
    "ALLOWED_BASIS",
    "COLUMN_CONTRACT_CONTAINERS",
    "CONFIDENCE_ORDER",
    "CONFIDENCE_TIERS",
    "INFERRED_BASIS_KEYS",
    "INFERRED_CONFIDENCE_KEYS",
    "INFERRED_METHOD_KEYS",
    "INFERRED_POOL_COLUMNS",
    "INFERRED_SOURCE",
    "INFERRED_SOURCE_MARKERS",
    "INFERRED_VALUE_KEYS",
    "LANGUAGE_EVIDENCE_KEYS",
    "LANGUAGE_SOURCE_KEYS",
    "MIN_INFERRED_CONFIDENCE",
    "ORIGIN_INFERRED",
    "ORIGIN_PROJECTED",
    "ORIGIN_SELF_REPORTED",
    "ORIGIN_UNKNOWN",
    "PROJECTED_SOURCE",
    "SELF_DECLARED_SOURCES",
    "SELF_REPORTED_SOURCE",
    "UNKNOWN_SOURCE",
    "admitted_confidence_tiers",
    "classify_language_origin",
    "language_evidence_block",
    "language_evidence_source",
    "language_gate_evidence",
    "language_hard_filter",
    "language_source_token",
    "language_sql_filter",
    "meets_confidence_floor",
    "resolve_candidate_language",
    "resolve_language_match_key",
]
