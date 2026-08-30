"""Planner intent contract helpers: audience scale, product anchor, short queries.

2026-08-25 车道 A。三件事,全是纯函数(零 IO、零 LLM、零写库):

1. **受众规模不再变成题材词**。「消费群体多 / 粉丝多 / 大号」是**受众规模**约束,
   过去被 planner 译成 lifestyle/street/portrait 这类题材词,把用户的话理解错了。
   :func:`detect_audience_scale` 把这类表述落成档位 + 粉丝数下限**建议值**;
   :func:`resolve_audience_scale` 规定操作员自己填的 followers_min 永远优先。
2. **产品锚只服务 existing_evidence**。默认 prospective_growth 按产品能力与使用场景
   找潜在用户,不要求品牌/型号;显式查「已经提到/使用 Viltrox」时仍可用
   :func:`product_anchor` / :func:`build_search_queries` 生成带锚查询。
3. **多条短查询取代一条长句**。一条长 query 同时服务向量召回 / YouTube 搜索 / IG 标签,
   对向量是稀释、对平台搜索过长。这里产出 2-4 条 ≤6 词、角度互不重复的短 query。

红线:本模块不放宽任何质量口径——不改 required_terms、不改新鲜度、不改器材证据要求;
粉丝下限只会被**抬高**,绝不会低于 :data:`AUDIENCE_SCALE_FLOOR`。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# 与 profile_recall_qualification.SMART_LOCAL_MIN_FOLLOWERS 同值(3000)。
# 此处不 import 那个模块:它是本波的禁区文件,且会带来重量级依赖链。
# tests/test_smart_query_intent_contract.py 钉死两者相等,漂移即红。
AUDIENCE_SCALE_FLOOR = 3_000

# 受众规模档位 → 粉丝数下限建议值。只作建议,消费端仍按操作员优先。
AUDIENCE_SCALE_TIERS: dict[str, int] = {
    "micro": AUDIENCE_SCALE_FLOOR,
    "mid": 30_000,
    "large": 100_000,
    "mega": 500_000,
}

# 表述 → 档位。长词优先匹配(先 sort by length),避免「大号」吃掉「超大号」。
_AUDIENCE_SCALE_TERMS: dict[str, str] = {
    # 大盘/头部
    "超大号": "mega", "头部": "mega", "顶流": "mega", "百万粉": "mega", "千万粉": "mega",
    "大网红": "mega", "mega influencer": "mega", "millions of followers": "mega",
    "huge audience": "mega", "top tier creator": "mega",
    # 受众规模大(用户原话「消费群体多」落在这里)
    "消费群体多": "large", "消费人群多": "large", "受众多": "large", "受众大": "large",
    "受众广": "large", "粉丝多": "large", "粉丝量大": "large", "粉丝基数大": "large",
    "粉丝基数": "large", "大号": "large", "影响力大": "large", "影响力强": "large",
    "播放量高": "large", "播放高": "large", "观众多": "large", "曝光大": "large",
    "曝光多": "large", "人气高": "large", "流量大": "large", "覆盖人群多": "large",
    "large audience": "large", "big audience": "large", "big following": "large",
    "large following": "large", "high reach": "large", "wide audience": "large",
    "lots of followers": "large", "many followers": "large", "high traffic": "large",
    # 腰部
    "腰部": "mid", "中腰部": "mid", "中型": "mid", "mid-tier": "mid", "mid tier": "mid",
    "medium audience": "mid",
    # 小号/素人(仍受 AUDIENCE_SCALE_FLOOR 兜底,不会把闸放到 3000 以下)
    "小号": "micro", "素人": "micro", "微网红": "micro", "尾部": "micro",
    "micro influencer": "micro", "nano influencer": "micro", "small creators": "micro",
    "small audience": "micro",
}

# 受众规模的英文表述绝不能当成题材检索词。planner 若照抄进 product_focus,这里剔掉。
_AUDIENCE_SCALE_NOISE_TERMS = frozenset(
    {
        "large", "big", "huge", "popular", "audience", "following", "followers",
        "reach", "traffic", "mega", "macro-influencer", "micro", "nano", "viral",
        "large audience", "big following", "high reach", "wide audience",
    }
)

# planner prompt 里直接内嵌的硬约束文本(与本模块口径同源,改一处即可)。
AUDIENCE_SCALE_PROMPT_RULE = (
    "- AUDIENCE SIZE IS NOT A TOPIC. Phrases about how many people a creator reaches "
    "(消费群体多 / 粉丝多 / 受众广 / 大号 / 影响力大 / large audience / big following / "
    "lots of followers) describe REACH, not subject matter. NEVER translate them into "
    "content-genre words such as lifestyle, street, portrait, vlog or travel. "
    "Put them in audience_scale (one of: micro, mid, large, mega) and leave the genre "
    "words to what the operator actually said about content."
)

_MOUNT_RULES: tuple[tuple[str, str], ...] = (
    (r"\bpl[-\s]?mount\b|pl\s*卡口", "PL-mount"),
    (r"\brf[-\s]?mount\b|rf\s*卡口|\bcanon\s+rf\b", "Canon RF-mount"),
    (r"\bef[-\s]?mount\b|ef\s*卡口", "Canon EF-mount"),
    (r"\bx[-\s]?mount\b|\bxf\b|x\s*卡口|富士|fujifilm|\bfuji\b", "Fujifilm X-mount"),
    (r"\bz[-\s]?mount\b|z\s*卡口|尼康|nikon", "Nikon Z-mount"),
    (r"\bl[-\s]?mount\b|l\s*卡口", "L-mount"),
    (r"\bm43\b|micro\s+four\s+thirds|\bmft\b", "MFT"),
    (r"\bfe[-\s]?mount\b|\be[-\s]?mount\b|e\s*卡口|索尼|\bsony\b", "Sony E-mount"),
)

_DEFAULT_BRAND = "Viltrox"
_MODEL_FAMILY_TERMS = ("epic", "lab", "evo", "air", "vintage", "pro")
_FOCAL_RE = re.compile(r"^\d{1,3}(?:-\d{1,3})?mm$", re.IGNORECASE)
_MODEL_CODE_RE = re.compile(r"^(?=.*[a-z])(?=.*\d)[a-z0-9]+(?:-[a-z0-9]+)*$", re.IGNORECASE)
_CATEGORY_ANGLE = (
    ("monitor", ("monitor", "review")),
    ("flash", ("flash", "lighting")),
    ("light", ("lighting", "review")),
    ("lens", ("lens", "review")),
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def detect_audience_scale(*values: Any) -> dict[str, Any] | None:
    """Map操作员原话里的受众规模表述到档位 + 粉丝下限建议值(命中不到返回 None)。"""
    blob = " ".join(_text(value).lower() for value in values if _text(value))
    if not blob:
        return None
    matched: list[tuple[str, str]] = []
    for phrase in sorted(_AUDIENCE_SCALE_TERMS, key=len, reverse=True):
        if phrase in blob:
            matched.append((phrase, _AUDIENCE_SCALE_TERMS[phrase]))
    if not matched:
        return None
    order = list(AUDIENCE_SCALE_TIERS)
    tier = max((tier for _, tier in matched), key=order.index)
    return {
        "audience_scale": tier,
        "min_followers_hint": AUDIENCE_SCALE_TIERS[tier],
        "matched_terms": [phrase for phrase, _ in matched][:4],
        "source": "operator_text",
    }


def normalise_audience_scale(
    raw_scale: Any,
    raw_hint: Any,
    *,
    detected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 LLM 返回的 audience_scale/min_followers_hint 归一;缺失时用文本探测兜底。

    永不低于 :data:`AUDIENCE_SCALE_FLOOR`——本波只改「怎么理解用户的话」,不放宽粉丝下限。
    """
    scale = str(raw_scale or "").strip().lower()
    if scale not in AUDIENCE_SCALE_TIERS:
        scale = str((detected or {}).get("audience_scale") or "").strip().lower()
    if scale not in AUDIENCE_SCALE_TIERS:
        return {"audience_scale": "", "min_followers_hint": None, "audience_scale_source": "unspecified"}
    try:
        hint = int(float(raw_hint)) if raw_hint not in (None, "") else 0
    except (TypeError, ValueError):
        hint = 0
    if hint <= 0:
        hint = int((detected or {}).get("min_followers_hint") or AUDIENCE_SCALE_TIERS[scale])
    hint = max(AUDIENCE_SCALE_FLOOR, min(hint, 50_000_000))
    return {
        "audience_scale": scale,
        "min_followers_hint": hint,
        "audience_scale_source": "llm" if str(raw_scale or "").strip().lower() in AUDIENCE_SCALE_TIERS
        else ("operator_text" if detected else "unspecified"),
    }


def _operator_followers_min(operator_filters: Any) -> int | None:
    filters = operator_filters if isinstance(operator_filters, dict) else {}
    for key in ("followers_min", "follower_min", "followersMin", "minFollowers"):
        value = filters.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def resolve_audience_scale(plan: Any, operator_filters: Any = None) -> dict[str, Any]:
    """决定最终 followers 下限:**操作员填了就用操作员的**,planner hint 只在留空时生效。

    这是 A1 的消费端契约。返回 ``applied_followers_min=None`` 表示「不额外加闸,
    交给既有 smart-local policy 的 min_followers」。
    """
    plan_dict = plan if isinstance(plan, dict) else {}
    hint_raw = plan_dict.get("min_followers_hint")
    try:
        hint = int(float(hint_raw)) if hint_raw not in (None, "") else 0
    except (TypeError, ValueError):
        hint = 0
    hint = max(AUDIENCE_SCALE_FLOOR, hint) if hint > 0 else 0
    operator_value = _operator_followers_min(operator_filters)
    if operator_value is not None:
        return {
            "applied_followers_min": operator_value,
            "source": "operator",
            "planner_hint": hint or None,
            "hint_applied": False,
        }
    if hint > 0:
        return {
            "applied_followers_min": hint,
            "source": "planner_hint",
            "planner_hint": hint,
            "hint_applied": True,
        }
    return {
        "applied_followers_min": None,
        "source": "policy_default",
        "planner_hint": None,
        "hint_applied": False,
    }


def mount_label(*values: Any) -> str:
    """从产品名/描述/操作员原话里识别卡口,返回可直接检索的英文标签。"""
    blob = " ".join(_text(value).lower() for value in values if _text(value))
    if not blob:
        return ""
    for pattern, label in _MOUNT_RULES:
        if re.search(pattern, blob, flags=re.IGNORECASE):
            return label
    return ""


def _model_tokens(name: str, *, limit: int = 2) -> list[str]:
    """从目录名里挑出可检索的型号词:焦段 > 型号码 > 系列族名(保留原始大小写)。"""
    words = [word.strip("()[],·") for word in _text(name).split() if word.strip("()[],·")]
    focal = [word for word in words if _FOCAL_RE.fullmatch(word)]
    codes = [
        word for word in words
        if _MODEL_CODE_RE.fullmatch(word) and word not in focal and len(word) >= 2
    ]
    family = [word for word in words if word.lower() in _MODEL_FAMILY_TERMS]
    ordered: list[str] = []
    seen: set[str] = set()
    for word in [*focal, *codes, *family]:
        if word.lower() not in seen:
            seen.add(word.lower())
            ordered.append(word)
    return ordered[:limit]


def product_anchor(product: Any, *, query_text: Any = "") -> dict[str, Any]:
    """产出每条检索词都必须携带的产品锚 ``{brand} {model}`` 与卡口标签。

    未解析到产品时返回空锚(``core=[]``),调用方据此跳过锚约束——绝不编造品牌词。
    """
    item = product if isinstance(product, dict) else {}
    name = _text(item.get("marketing_name") or item.get("model_name"))
    if not name and not _text(item.get("sku")):
        return {"brand": "", "model_tokens": [], "mount": "", "core": [], "prefix": "", "category_terms": []}
    # 目录是 Viltrox 单品牌;名称里已带品牌词时沿用其原始大小写,否则补默认品牌词。
    brand = next(
        (word for word in name.split() if word.lower().strip(",·") == _DEFAULT_BRAND.lower()),
        _DEFAULT_BRAND,
    )
    models = [token for token in _model_tokens(name) if token.lower() != brand.lower()]
    if not models:
        models = _model_tokens(_text(item.get("sku")).replace("-", " "))
    mount = mount_label(name, item.get("description"), item.get("specs_line"), query_text)
    core = [brand, *models]
    blob = " ".join(
        _text(item.get(key)).lower()
        for key in ("category_main", "category_detail", "series", "marketing_name", "model_name")
    )
    category_terms: list[str] = []
    for needle, terms in _CATEGORY_ANGLE:
        if needle in blob:
            category_terms = list(terms)
            break
    return {
        "brand": brand,
        "model_tokens": models,
        "mount": mount,
        "core": core,
        "prefix": " ".join([*core, *mount.split()]) if mount else " ".join(core),
        "category_terms": category_terms,
    }


def anchor_required_terms(anchor: Any) -> list[str]:
    """契约测试用:每条 search_query 必须包含的小写锚词(品牌 + 型号)。"""
    item = anchor if isinstance(anchor, dict) else {}
    return [str(term).lower() for term in (item.get("core") or []) if str(term).strip()]


def query_has_product_anchor(query: Any, anchor: Any) -> bool:
    """大小写不敏感地判断一条 query 是否携带完整产品锚。"""
    required = anchor_required_terms(anchor)
    if not required:
        return True
    lowered = _text(query).lower()
    return all(term in lowered for term in required)


def strip_audience_scale_terms(terms: Iterable[Any]) -> list[str]:
    """把受众规模词从题材词表里剔掉(它们是过滤条件,不是检索题材)。"""
    output: list[str] = []
    for raw in terms or []:
        value = _text(raw).lower()
        if not value or value in _AUDIENCE_SCALE_NOISE_TERMS:
            continue
        output.append(value)
    return output


def angle_terms(sources: Iterable[Any], anchor: Any = None) -> list[str]:
    """把 LLM/规则给的检索词打散成「去掉产品锚之后」的题材短语,保序去重。

    LLM 若已按契约产出自带锚的 search_queries,这里剥掉锚词只留角度部分,避免
    重新装桶时把锚词当内容、挤掉真正的题材词。
    """
    item = anchor if isinstance(anchor, dict) else {}
    drop = {str(term).lower() for term in (item.get("core") or [])}
    drop.update(part.lower() for part in _text(item.get("mount")).split())
    output: list[str] = []
    seen: set[str] = set()
    for source in sources or []:
        words = [word for word in _text(source).split() if word.lower() not in drop]
        phrase = " ".join(strip_audience_scale_terms(words))
        key = phrase.lower()
        if phrase and key not in seen:
            seen.add(key)
            output.append(phrase)
    return output


def _compose(core: list[str], extra: Iterable[str], *, max_words: int) -> str:
    words: list[str] = list(core)
    for token in extra:
        for part in _text(token).split():
            if len(words) >= max_words:
                break
            if part.lower() not in {word.lower() for word in words}:
                words.append(part)
    return " ".join(words)


def build_search_queries(
    anchor: Any,
    focus_terms: Iterable[Any],
    *,
    max_queries: int = 4,
    max_words: int = 6,
) -> list[str]:
    """产出 2-4 条 ≤``max_words`` 词、角度互不重复、每条自带产品锚的检索词。

    角度依次是「产品+卡口」「产品+题材」「产品+使用场景」「产品+品类词」;
    没有产品锚(未解析到产品)时退化为纯题材短句,仍然分条、仍然限长。
    """
    item = anchor if isinstance(anchor, dict) else {}
    core = [str(term) for term in (item.get("core") or []) if str(term).strip()]
    focus = strip_audience_scale_terms(focus_terms)
    slot = max(1, int(max_words) - len(core))
    queries: list[str] = []

    def push(candidate: str) -> None:
        value = _text(candidate)
        if not value:
            return
        key = value.lower()
        if key in {existing.lower() for existing in queries} or len(queries) >= max(1, int(max_queries)):
            return
        queries.append(value)

    if item.get("mount"):
        push(_compose(core, [str(item["mount"])], max_words=max_words))
    # 按**短语**装桶(不切断 "natural light" 这类词组),每桶 ≤ slot 个词。
    buckets: list[list[str]] = []
    current: list[str] = []
    for phrase in focus:
        parts = _text(phrase).split()[:slot]
        if not parts:
            continue
        if current and len(current) + len(parts) > slot:
            buckets.append(current)
            current = []
        current.extend(parts)
    if current:
        buckets.append(current)
    for bucket in buckets:
        if len(queries) >= max_queries:
            break
        push(_compose(core, bucket, max_words=max_words))
    if len(queries) < 2 and item.get("category_terms"):
        push(_compose(core, list(item["category_terms"]), max_words=max_words))
    if not queries and core:
        push(" ".join(core))
    return queries


def compat_search_query(
    queries: Iterable[Any],
    anchor: Any = None,
    *,
    extra_terms: Iterable[Any] = (),
    max_words: int = 18,
) -> str:
    """向后兼容的单条 ``search_query``:锚在前 + 各角度词去重合并。

    下游(向量召回 / YouTube 搜索 / IG 标签)本波不改造,继续消费这一条;
    与改造前相比它只是**多带了产品锚**,广度不减(``extra_terms`` 收纳没能挤进
    ≤6 词短 query 的剩余题材词)。
    """
    item = anchor if isinstance(anchor, dict) else {}
    words: list[str] = []
    seen: set[str] = set()

    def extend(source: Any) -> None:
        for part in _text(source).split():
            key = part.lower()
            if key not in seen and len(words) < max(1, int(max_words)):
                seen.add(key)
                words.append(part)

    extend(item.get("prefix"))
    for query in queries or []:
        extend(query)
    for term in strip_audience_scale_terms(extra_terms):
        extend(term)
    return " ".join(words)
