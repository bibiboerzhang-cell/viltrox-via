"""一句人话 → **全部筛选项**的提议(车道:模型提议筛选,2026-08-25)。

背景(实测,不是推测):``smart_query_planner`` 只产出检索词 / platforms /
min_followers_hint。``countries`` / ``languages`` / ``verticals`` 这三项在那个文件里
**零出现** —— 它们只能靠操作员自己去界面上勾。而线上真数(池 2036 人)显示,
恰恰是这三刀最致命:

    只勾美国 160 人 → +粉丝 1 万 75 → +粉丝 5 万 49 → +英语 **6** → +生活方式 **0**。

所以操作员打一句人话最后拿到 0 个结果(用户原话「别总是 00000」)。本模块补的就是
这个缺口:把一句人话翻成**五项筛选提议**(国家 / 语言 / 垂类 / 粉丝下限 / 平台),
并且把每一项标清楚是**操作员明确说的**还是**推断出来的**。

红线(逐条对应用户裁令):

1. **明确 vs 推断的判定权在规则手里,不在模型手里。**
   ``origin`` 只由 :func:`_explicit_*` 这几个纯规则函数按**操作员原话**判定;模型
   产出的一切一律只能是 ``model_inferred``。理由:若让模型自报「这是用户明确要求的」,
   它把五项全标成明确,自动松绑车道就被彻底冻住了,「00000」原样复发。
   下游自动松绑**只许**动 ``relaxable=True`` 的项(= 推断项),
   ``operator_explicit`` 的值是操作员的话,永远不许自动改。
2. **本模块只提议「操作员的筛选偏好」,一个字都不碰质量标准。**
   这里出现的字段只有国家 / 语言 / 垂类 / 粉丝下限 / 平台。新鲜度天数、器材证据要求、
   证据词数阈值、产品锚要求、账号安全判定 —— 本文件里零出现,也零 import。
3. **零成本**:纯字符串规则 + 复用已解析好的 plan,**不读库、不调 provider、不调模型**。
   模型那一份提议是**搭现有那次 planner 调用的顺风车**(见 :data:`FACET_PROMPT_RULE`),
   不额外发一次请求。
4. **不许静默替操作员做决定**:每一项都带 ``evidence``(为什么这么提)与 ``note``
   (门面直接能显示的人话),``locked_fields`` / ``relaxable_fields`` 明写哪些能动。
5. **三态口径复用既有的** ``profile_recall_filter_modes``,不另造一套模式名。
6. **降级要诚实**:``source`` / ``degraded`` / ``notice`` 如实标注这次是模型推断还是
   规则推荐,门面照此显示,不许假装。

red line:纯函数模块 —— 不读库、不写库、零触 ``viltrox_fit_score``、零触 rule_v0。
"""
from __future__ import annotations

import re
from typing import Any

from app.core.coerce import _text
from app.core.logging import get_logger
from app.domains.kol.pool_common import COUNTRY_CODE_ALIASES
from app.domains.kol.profile_recall_filter_modes import TRI_STATE_MODES, normalize_mode
from app.domains.kol.profile_recall_precision import explicit_platforms_from_query
from app.domains.kol.profile_vertical_lexicon import VERTICAL_KEYS, VERTICAL_LABELS_ZH
from app.domains.kol.smart_query_intent import AUDIENCE_SCALE_FLOOR, detect_audience_scale
from app.domains.kol.search_platform_policy import DEFAULT_DISCOVERY_PLATFORMS, STRICT_DISCOVERY_PLATFORMS

logger = get_logger(__name__)


#: 提议里出现的五项。顺序即门面展示顺序。
FACET_FIELDS: tuple[str, ...] = ("countries", "languages", "verticals", "min_followers", "platforms")

#: 操作员**原话里就有**的项。自动松绑车道**禁止**动它。
ORIGIN_EXPLICIT = "operator_explicit"
#: 操作员没说、由模型或规则推断出来的项。自动松绑只许动这一类。
ORIGIN_INFERRED = "model_inferred"

#: ``values`` 是谁给的(审计用,与 ``origin`` 正交:origin 说「谁的意思」,source 说「谁算的」)。
SOURCE_OPERATOR = "operator_text"
SOURCE_MODEL = "model"
SOURCE_RULE = "rule"

SUPPORTED_PLATFORMS: tuple[str, ...] = STRICT_DISCOVERY_PLATFORMS

#: **本模块自己推断出来的**语言筛选用的三态模式。故意不是 require。
#:
#: 线上真数:池 2036 人里 language 空 1450 人(71.2%)、country 空 715 人(35.1%)。
#: 硬筛对「未知」一律按「不符合」处理,于是「语言=英语」这一刀砍掉的人里绝大多数
#: 只是**没填**,不是不说英语。既然这一刀本来就不是操作员要的(是系统自己加的),
#: 那就加得轻一点:``include_unknown`` = 「已知语言对不上的排除,语言没填的保留」。
LANGUAGE_DEFAULT_MODE = "include_unknown"

#: **操作员亲口点名**的语言用的模式。2026-08-26 复核纠偏:此前这里也走
#: ``include_unknown``,等于操作员说「要说英语的」,系统静默把它改成「说英语的 + 不知道
#: 说什么语的」—— 这是替操作员改主意,且**没有任何地方告诉他**。红线:明确点名的项
#: 一律按原意执行(``require``)。确实建议放宽时,把建议挂在 ``relax_suggestion`` 上,
#: 由松绑车道**显式播报**给操作员,让他自己点头 —— 不在这里偷偷改。
LANGUAGE_EXPLICIT_MODE = "require"

#: 国家默认沿用 ``require``。用户已拍板:「勾『美国』就是要美国人」—— 国家是真语义,
#: 不许偷偷改成未知放行。国家的未知率(35%)也远没到语言那种程度。
COUNTRY_DEFAULT_MODE = "require"


# ── 供 planner prompt 拼接的规则片段(搭现有那次调用的顺风车,不新增请求)──────────
FACET_PROMPT_RULE = (
    "- ALSO PROPOSE THE OPERATOR'S FILTERS, not just search words. Add one JSON key "
    "filter_proposal, an object with: countries (ISO-2 codes, e.g. [\"US\"]), languages "
    "(ISO-639-1 codes, e.g. [\"en\"]), verticals (only from this fixed list: "
    f"{', '.join(VERTICAL_KEYS)}), min_followers (number, 0 when the operator implied no floor), "
    "platforms (subset of youtube / instagram / tiktok). "
    "Propose what a sensible operator would pick for THIS product and request. "
    "Do NOT claim the operator said something they did not say — whether a filter counts as "
    "operator-stated is decided outside this prompt, from their literal words. "
    "Keep every list short: a filter that names many things is a filter that returns nobody."
)


# ── 操作员原话解析(规则,判定「明确 vs 推断」的唯一权威)────────────────────────

def _ascii_boundary(alias: str) -> str:
    """给 ASCII 别名加词边界。``u.s.`` 这种带点的别名用不了 ``\\b``,故用前后向断言。"""
    return r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


#: 国家别名表直接复用 ``pool_common.COUNTRY_CODE_ALIASES`` —— 与硬筛端
#: ``_country_match_key`` 同一套口径,不另造第二张表(第二张表迟早漂移)。
_COUNTRY_MATCHERS: tuple[tuple[str, str, bool], ...] = tuple(
    sorted(
        ((alias, code, _has_cjk(alias)) for alias, code in COUNTRY_CODE_ALIASES.items()),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)
_CITY_COUNTRY_MATCHERS: tuple[tuple[str, str, bool], ...] = (
    ("london", "GB", False), ("伦敦", "GB", True),
    ("atlanta", "US", False), ("亚特兰大", "US", True),
)

#: 操作员会怎么**说**语言(与数据侧的取值别名刻意分开:这里是人话,那里是字段值)。
_LANGUAGE_PHRASES: tuple[tuple[str, str], ...] = (
    ("english", "en"), ("英语", "en"), ("英文", "en"),
    ("chinese", "zh"), ("mandarin", "zh"), ("中文", "zh"), ("汉语", "zh"),
    ("japanese", "ja"), ("日语", "ja"), ("日文", "ja"),
    ("korean", "ko"), ("韩语", "ko"), ("韩文", "ko"),
    ("german", "de"), ("德语", "de"),
    ("french", "fr"), ("法语", "fr"),
    ("spanish", "es"), ("西班牙语", "es"),
    ("italian", "it"), ("意大利语", "it"),
    ("portuguese", "pt"), ("葡萄牙语", "pt"),
)

#: 操作员会怎么**说**垂类。中文门面词直接从垂类词表的标签生成,再补同义说法。
_VERTICAL_PHRASES: tuple[tuple[str, str], ...] = tuple(
    [(label.lower(), key) for key, label in VERTICAL_LABELS_ZH.items()]
    + [
        ("lens review", "lens_review"), ("评测镜头", "lens_review"), ("镜头测评", "lens_review"),
        ("photography tutorial", "photography_tutorial"), ("摄影教学", "photography_tutorial"),
        ("摄影课", "photography_tutorial"), ("teaching", "photography_tutorial"),
        ("gear comparison", "gear_comparison"), ("器材对比", "gear_comparison"),
        ("对比评测", "gear_comparison"), ("横评", "gear_comparison"),
        ("portrait", "portrait"), ("人像摄影", "portrait"),
        ("video creation", "video_creation"), ("视频创作", "video_creation"),
        ("filmmaking", "video_creation"), ("影视创作", "video_creation"), ("拍视频", "video_creation"),
        ("camera system", "camera_system"), ("相机系统", "camera_system"), ("机身", "camera_system"),
        ("vlog", "vlog"), ("日常记录", "vlog"),
        ("lifestyle", "lifestyle"), ("生活方式", "lifestyle"),
        ("technology", "technology"), ("科技", "technology"), ("数码", "technology"),
    ]
)

#: 从 plan 的 ``product_focus``(英文创作者类型词)反推垂类 —— 这是**推断**,不是明确。
_FOCUS_VERTICAL_HINTS: tuple[tuple[str, str], ...] = (
    ("lens review", "lens_review"), ("lens reviewer", "lens_review"),
    ("tutorial", "photography_tutorial"), ("educator", "photography_tutorial"),
    ("teacher", "photography_tutorial"),
    ("comparison", "gear_comparison"), ("gear review", "gear_comparison"),
    ("portrait", "portrait"), ("wedding", "portrait"),
    ("filmmaker", "video_creation"), ("videographer", "video_creation"),
    ("cinematographer", "video_creation"), ("director of photography", "video_creation"),
    ("commercial video", "video_creation"), ("music video", "video_creation"),
    ("camera system", "camera_system"), ("mirrorless", "camera_system"),
    ("vlog", "vlog"), ("lifestyle", "lifestyle"), ("travel", "lifestyle"),
    ("tech", "technology"), ("technology", "technology"),
)

#: 「别按这个筛」的明确表态。命中即:该项**明确为空**且锁死 —— 模型和自动松绑都不许再往里塞。
_NO_COUNTRY_PHRASES: tuple[str, ...] = (
    "不限国家", "不限地区", "不分国家", "全球", "全世界", "worldwide", "global", "any country",
)
_NO_LANGUAGE_PHRASES: tuple[str, ...] = (
    "不限语言", "不分语言", "任何语言", "any language", "all languages",
)


def _explicit_countries(query: str) -> tuple[list[str], list[str]]:
    """操作员**原话**里点名的国家。返回 ``(ISO-2 列表, 命中的原话)``。"""
    lowered = _text(query).lower()
    if not lowered:
        return [], []
    codes: list[str] = []
    matched: list[str] = []
    for alias, code, is_cjk in (*_COUNTRY_MATCHERS, *_CITY_COUNTRY_MATCHERS):
        hit = alias in lowered if is_cjk else re.search(_ascii_boundary(alias), lowered) is not None
        if not hit:
            continue
        matched.append(alias)
        if code not in codes:
            codes.append(code)
    return codes, matched[:4]


def _explicit_languages(query: str) -> tuple[list[str], list[str]]:
    lowered = _text(query).lower()
    if not lowered:
        return [], []
    codes: list[str] = []
    matched: list[str] = []
    for phrase, code in _LANGUAGE_PHRASES:
        hit = phrase in lowered if _has_cjk(phrase) else re.search(_ascii_boundary(phrase), lowered) is not None
        if not hit:
            continue
        matched.append(phrase)
        if code not in codes:
            codes.append(code)
    return codes, matched[:4]


def _explicit_verticals(query: str) -> tuple[list[str], list[str]]:
    lowered = _text(query).lower()
    if not lowered:
        return [], []
    keys: list[str] = []
    matched: list[str] = []
    for phrase, key in _VERTICAL_PHRASES:
        if phrase not in lowered:
            continue
        matched.append(phrase)
        if key not in keys:
            keys.append(key)
    return keys, matched[:4]


def _said_no_filter(query: str, phrases: tuple[str, ...]) -> str:
    lowered = _text(query).lower()
    for phrase in phrases:
        if phrase in lowered:
            return phrase
    return ""


# ── 模型提议的清洗(模型只提议,取值由本模块校验后才落地)──────────────────────

def _clean_codes(value: Any, *, upper: bool = False, allowed: tuple[str, ...] = ()) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in raw:
        text = " ".join(str(item or "").split()).strip()
        if not text or text.lower() in {"all", "any", "*"}:
            continue
        text = text.upper() if upper else text.lower()
        if allowed and text not in allowed:
            continue
        if text not in out:
            out.append(text)
    return out


def _model_facet_payload(raw_plan: Any) -> dict[str, Any]:
    payload = (raw_plan or {}).get("filter_proposal") if isinstance(raw_plan, dict) else None
    return payload if isinstance(payload, dict) else {}


def _positive_int(value: Any) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


# ── 单项提议的构造 ─────────────────────────────────────────────────────────────

def _facet(
    field: str,
    values: list[Any],
    *,
    origin: str,
    source: str,
    evidence: str,
    mode: str = "require",
    note: str = "",
) -> dict[str, Any]:
    """一项筛选提议。``relaxable`` 是给自动松绑车道看的唯一开关:推断项 True,明确项 False。"""
    normalized_mode = normalize_mode(mode) if mode in TRI_STATE_MODES else "require"
    facet: dict[str, Any] = {
        "field": field,
        "values": list(values),
        "mode": normalized_mode,
        "origin": origin,
        "source": source,
        "relaxable": origin != ORIGIN_EXPLICIT,
        "evidence": evidence,
        "note": note,
    }
    if field == "min_followers":
        # 数值项额外给一个标量,免得下游对着单元素数组做算术。
        facet["value"] = int(values[0]) if values else None
    return facet


def _countries_facet(query: str, plan: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    said_no = _said_no_filter(query, _NO_COUNTRY_PHRASES)
    if said_no:
        return _facet(
            "countries", [], origin=ORIGIN_EXPLICIT, source=SOURCE_OPERATOR,
            evidence=f"你说了「{said_no}」", note="按你的要求不筛国家。",
        )
    explicit, matched = _explicit_countries(query)
    if explicit:
        return _facet(
            "countries", explicit, origin=ORIGIN_EXPLICIT, source=SOURCE_OPERATOR,
            mode=COUNTRY_DEFAULT_MODE,
            evidence="你原话里写了「" + "、".join(matched) + "」",
            note="按你点名的国家筛;国家没填的人会被排除。",
        )
    from_model = _clean_codes(model.get("countries"), upper=True)
    if from_model:
        return _facet(
            "countries", from_model, origin=ORIGIN_INFERRED, source=SOURCE_MODEL,
            mode=COUNTRY_DEFAULT_MODE,
            evidence="你没点名国家,这是按你的描述推断的",
            note="你没指定国家 —— 这一项是推断的,人不够时会先松它。",
        )
    # plan 的 ``market`` 是 planner prompt 里写死的「默认 US」,**不是操作员说的**。
    # 历史上它被当成硬约束一路带下去,是「只勾美国就剩 160 人」那一刀的隐形源头。
    market = _text(plan.get("market")).upper()
    if market and len(market) <= 3:
        return _facet(
            "countries", [market], origin=ORIGIN_INFERRED, source=SOURCE_RULE,
            mode=COUNTRY_DEFAULT_MODE,
            evidence="你没点名国家,这是默认的主力市场",
            note="你没指定国家 —— 这一项是默认值,人不够时会先松它。",
        )
    return _facet(
        "countries", [], origin=ORIGIN_INFERRED, source=SOURCE_RULE,
        evidence="没识别到国家要求", note="不按国家筛。",
    )


def _languages_facet(query: str, plan: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    said_no = _said_no_filter(query, _NO_LANGUAGE_PHRASES)
    if said_no:
        return _facet(
            "languages", [], origin=ORIGIN_EXPLICIT, source=SOURCE_OPERATOR,
            evidence=f"你说了「{said_no}」", note="按你的要求不筛语言。",
        )
    unknown_note = "语言没填的人也保留 —— 池里七成人没有语言记录,按「必须匹配」筛会只剩个位数。"
    explicit, matched = _explicit_languages(query)
    if explicit:
        return _facet(
            "languages", explicit, origin=ORIGIN_EXPLICIT, source=SOURCE_OPERATOR,
            mode=LANGUAGE_DEFAULT_MODE,
            evidence="你原话里写了「" + "、".join(matched) + "」",
            note="按你点名的语言筛," + unknown_note,
        )
    from_model = _clean_codes(model.get("languages"))
    if from_model:
        return _facet(
            "languages", from_model, origin=ORIGIN_INFERRED, source=SOURCE_MODEL,
            mode=LANGUAGE_DEFAULT_MODE,
            evidence="你没点名语言,这是按你的描述推断的",
            note="你没指定语言 —— 这一项是推断的," + unknown_note,
        )
    return _facet(
        "languages", [], origin=ORIGIN_INFERRED, source=SOURCE_RULE,
        mode=LANGUAGE_DEFAULT_MODE,
        evidence="没识别到语言要求", note="不按语言筛。",
    )


def _verticals_facet(query: str, plan: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    explicit, matched = _explicit_verticals(query)
    if explicit:
        return _facet(
            "verticals", explicit[:4], origin=ORIGIN_EXPLICIT, source=SOURCE_OPERATOR,
            evidence="你原话里写了「" + "、".join(matched) + "」",
            note="按你说的内容方向筛。",
        )
    from_model = _clean_codes(model.get("verticals"), allowed=VERTICAL_KEYS)
    if from_model:
        return _facet(
            "verticals", from_model[:4], origin=ORIGIN_INFERRED, source=SOURCE_MODEL,
            evidence="你没点名内容方向,这是按产品推断的",
            note="你没指定内容方向 —— 这一项是推断的,人不够时会先松它。",
        )
    focus_blob = " ".join(str(item or "") for item in (plan.get("product_focus") or [])).lower()
    keys: list[str] = []
    for phrase, key in _FOCUS_VERTICAL_HINTS:
        if phrase in focus_blob and key not in keys:
            keys.append(key)
    if keys:
        return _facet(
            "verticals", keys[:4], origin=ORIGIN_INFERRED, source=SOURCE_RULE,
            evidence="你没点名内容方向,这是按产品适配人群推断的",
            note="你没指定内容方向 —— 这一项是推断的,人不够时会先松它。",
        )
    return _facet(
        "verticals", [], origin=ORIGIN_INFERRED, source=SOURCE_RULE,
        evidence="没识别到内容方向要求", note="不按内容方向筛。",
    )


def _min_followers_facet(query: str, plan: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    # 明确与否由**操作员原话**说了算(``detect_audience_scale`` 只吃原话),
    # 不看 plan 的 ``audience_scale_source`` —— 那一位在模型给了档位时会标成模型来源,
    # 分不清「操作员说了大号」和「模型自己觉得该找大号」。
    detected = detect_audience_scale(query)
    if detected:
        floor = _positive_int(detected.get("min_followers_hint"))
        matched = [str(term) for term in (detected.get("matched_terms") or [])][:3]
        return _facet(
            "min_followers", [floor] if floor else [], origin=ORIGIN_EXPLICIT, source=SOURCE_OPERATOR,
            evidence=("你原话里写了「" + "、".join(matched) + "」") if matched else "你原话提到了体量",
            note="按你说的体量设的粉丝下限。",
        )
    from_model = _positive_int(model.get("min_followers"))
    if from_model:
        return _facet(
            "min_followers", [from_model], origin=ORIGIN_INFERRED, source=SOURCE_MODEL,
            evidence="你没说体量,这是按你的描述推断的",
            note="你没指定粉丝下限 —— 这一项是推断的,人不够时会先松它。",
        )
    hint = _positive_int(plan.get("min_followers_hint"))
    if hint:
        return _facet(
            "min_followers", [hint], origin=ORIGIN_INFERRED, source=SOURCE_RULE,
            evidence="你没说体量,这是默认的粉丝下限",
            note="你没指定粉丝下限 —— 这一项是默认值,人不够时会先松它。",
        )
    return _facet(
        "min_followers", [], origin=ORIGIN_INFERRED, source=SOURCE_RULE,
        evidence="没识别到体量要求", note="不设粉丝下限。",
    )


def _platforms_facet(query: str, plan: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    explicit = [item for item in explicit_platforms_from_query(query) if item in SUPPORTED_PLATFORMS]
    if explicit:
        return _facet(
            "platforms", explicit, origin=ORIGIN_EXPLICIT, source=SOURCE_OPERATOR,
            evidence="你原话里点了「" + "、".join(explicit) + "」",
            note="只在你点名的平台里找。",
        )
    # New providers are opt-in; a model/default plan cannot expand paid scope.
    from_model = _clean_codes(model.get("platforms"), allowed=DEFAULT_DISCOVERY_PLATFORMS)
    candidates = from_model or [item for item in (plan.get("platforms") or []) if item in DEFAULT_DISCOVERY_PLATFORMS]
    source = SOURCE_MODEL if from_model else SOURCE_RULE
    # 三个平台全在 = 根本没在筛平台。如实报成「不筛」,别摆一个假的三选三给操作员看。
    if not candidates or set(candidates) == set(DEFAULT_DISCOVERY_PLATFORMS):
        return _facet(
            "platforms", [], origin=ORIGIN_INFERRED, source=source,
            evidence="你没点名平台", note="三个平台一起找,不按平台筛。",
        )
    return _facet(
        "platforms", candidates, origin=ORIGIN_INFERRED, source=source,
        evidence="你没点名平台,这是推断的",
        note="你没指定平台 —— 这一项是推断的,人不够时会先松它。",
    )


# ── 汇总 ──────────────────────────────────────────────────────────────────────

def _recall_filters(facets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """把提议转成 ``recall_kol_profiles(filters=...)`` 认的形态(键名全在
    ``SUPPORTED_RECALL_FILTERS`` 内;三态项用既有的 ``{"values": [...], "mode": ...}``)。

    只出现操作员筛选偏好那五项。质量闸(新鲜度 / 器材证据 / 产品锚 / 账号安全)一个都不产出。
    """
    filters: dict[str, Any] = {}
    for key in ("countries", "languages"):
        facet = facets[key]
        if not facet["values"]:
            continue
        filters[key] = (
            {"values": list(facet["values"]), "mode": facet["mode"]}
            if facet["mode"] != "require"
            else list(facet["values"])
        )
    for key in ("verticals", "platforms"):
        if facets[key]["values"]:
            filters[key] = list(facets[key]["values"])
    floor = facets["min_followers"].get("value")
    if floor:
        filters["followers_min"] = int(floor)
    return filters


def _notice(source: str, degraded: bool) -> str:
    """门面直接显示的一句话。禁内部术语与厂商/模型名;降级必须说出来(红线 6)。"""
    if source == SOURCE_MODEL:
        return "筛选建议按你的描述自动推断得出,每一项都能改。"
    if degraded:
        return "筛选建议按内置规则给出(这次没能做自动推断),每一项都能改。"
    return "筛选建议按内置规则给出,每一项都能改。"


def propose_facets(
    query: Any,
    plan: dict[str, Any] | None = None,
    *,
    raw_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从一句人话 + 已归一的 plan,提议五项筛选。

    参数
    ----
    query
        操作员原话。**判定「明确 vs 推断」的唯一依据。**
    plan
        已归一的检索计划(``market`` / ``platforms`` / ``min_followers_hint`` /
        ``product_focus`` / ``fallback_used``)。规则兜底从这里取值。
    raw_plan
        模型这一轮吐出的原始 JSON(可能为空)。只读它的 ``filter_proposal``,
        且取值一律先过校验;模型**不能**把任何一项标成「操作员明确要求」。

    产出的 ``relaxable_fields`` 就是自动松绑车道的作业面:**只许**动这几项。
    """
    query_text = _text(query)
    plan = plan if isinstance(plan, dict) else {}
    model = _model_facet_payload(raw_plan)

    facets: dict[str, dict[str, Any]] = {
        "countries": _countries_facet(query_text, plan, model),
        "languages": _languages_facet(query_text, plan, model),
        "verticals": _verticals_facet(query_text, plan, model),
        "min_followers": _min_followers_facet(query_text, plan, model),
        "platforms": _platforms_facet(query_text, plan, model),
    }

    # 降级口径(红线 6):模型这一轮**真给了** filter_proposal 才算「模型推断」。
    # 没给就如实标规则推荐。两种情况额外算「降级」,门面要多说一句「没能做自动推断」:
    #   1) plan 自己标了 fallback_used(provider 不可用 / 超预算 / 解析失败);
    #   2) 确实发生过一次模型调用,却没拿到可用的筛选提议。
    # 首屏免调用路径(provider_free:两者都为 False)是设计好的,不是事故,不标降级。
    used_model = any(facet["source"] == SOURCE_MODEL for facet in facets.values())
    source = SOURCE_MODEL if used_model else SOURCE_RULE
    degraded = (not used_model) and bool(
        plan.get("fallback_used") or plan.get("provider_calls_performed")
    )

    explicit_fields = [key for key in FACET_FIELDS if facets[key]["origin"] == ORIGIN_EXPLICIT]
    inferred_fields = [key for key in FACET_FIELDS if facets[key]["origin"] == ORIGIN_INFERRED]
    return {
        "status": "ready",
        "original_query": query_text,
        "facets": facets,
        "order": list(FACET_FIELDS),
        "explicit_fields": explicit_fields,
        # 明确项 = 操作员的话,自动松绑**绝不许**动(红线 1)。
        "locked_fields": explicit_fields,
        "inferred_fields": inferred_fields,
        # 推断项 = 自动松绑车道唯一可动的集合。
        "relaxable_fields": [key for key in inferred_fields if facets[key]["values"]],
        "filters": _recall_filters(facets),
        "source": source,
        "degraded": degraded,
        "degraded_reason": "" if not degraded else _text(plan.get("reason")) or "planner_fallback",
        "notice": _notice(source, degraded),
    }


__all__ = [
    "COUNTRY_DEFAULT_MODE",
    "FACET_FIELDS",
    "FACET_PROMPT_RULE",
    "LANGUAGE_DEFAULT_MODE",
    "ORIGIN_EXPLICIT",
    "ORIGIN_INFERRED",
    "SOURCE_MODEL",
    "SOURCE_OPERATOR",
    "SOURCE_RULE",
    "propose_facets",
]
