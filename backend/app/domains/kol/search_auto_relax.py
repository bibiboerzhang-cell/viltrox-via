"""搜索「自动放宽」策略——模型提议、数据库定夺、放宽必上报(2026-08-26)。

用户原话:「我觉得我输入完之后最好是有个大模型计算,然后自动化选择输入,别总是 00000」。
今天的病灶实测在案(池 2036 人):只勾美国 160 → 加粉丝 5 万 49 → 再加英语 **6** →
再加生活方式 **0**。而 language 空 1450/2036(71.2%)、country 空 715(35.1%),
硬筛把「没填」当「不符合」一并驳回——被那一刀砍掉的人绝大多数只是**没填**。

本模块只做一件事:**当提议的筛选组合出不够人时,按「代价最小」的顺序把筛选放宽,
每放宽一格就用零成本 COUNT 复核一次,够了就停,并把整个过程如实交给操作员。**

五条不可越界的线(逐条对应用户裁令):

1. **只松「操作员的筛选偏好」,绝不松质量合格线。**
   可松:国家 / 语言 / 垂类 / 粉丝下限(仅限模型推断的那份)/ 平台。
   不可松:新鲜度天数、器材证据要求、证据词数阈值、产品锚、账号安全性判定——
   它们全在 :data:`PROTECTED_FILTER_KEYS` 里,本模块的任何代码路径都不写这些键,
   由 ``tests/test_kol_search_auto_relax.py`` 钉死。
2. **只松模型推断的项,操作员显式指定的一格都不动。**
   来源判定见 :func:`assemble_recall_filters` / :func:`merge_plan_filters`:
   凡是从请求体(界面勾选)进来的键一律 ``operator``;失败方向也是 ``operator``
   ——来源不明 = 当成操作员的,宁可不松。
3. **模型只提议,数据库定夺。** 产量一律来自注入的零成本 COUNT 估算器
   (:data:`YieldEstimator`),本模块不调 provider、不调大模型、不自己猜人数。
4. **绝不静默,且「加筛选」与「松筛选」同等可见。** payload 里 ``applied`` 记录松了什么,
   ``added`` 记录系统替操作员加了哪几项他没说过的硬筛(加了什么 / 为什么加 / 能不能去掉),
   ``added_dropped`` 记录他点掉之后真的去掉了哪几项。2026-08-26 复核纠偏:此前只有松绑
   进台账、加筛选完全静默 —— 系统能悄悄替操作员加上他从没说过的条件而他毫不知情,
   这比不松绑更严重。界面文案由前端按这些结构化事实自己拼(门面禁术语),本模块不产出
   面向操作员的句子(``reason`` 是提议车道 ``evidence`` 的原样透传,不是本模块写的话)。
5. **还原在任何状态下都能用。** ``operator_filters``(只剩操作员自己那部分的筛选)与
   ``restore_request``(前端照抄就能回去的请求体片段)与 ``status`` 无关地永远在 payload 里,
   ``disabled`` / ``unavailable`` 这些早退路径也在 —— 上一版正是因为早退把还原落点一起
   带走了,「改回我的条件」按下去回不到操作员的条件。

零 SQL、零 IO、零全局状态:除注入的估算器外全是纯函数。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.core.logging import get_logger
from app.domains.kol import smart_query_intent
from app.domains.kol.profile_recall_filter_modes import TRI_STATE_FILTER_FIELDS

logger = get_logger(__name__)

#: 前端按这个 schema 名认字段;改字段含义必须同时进版本号。
SCHEMA = "kol_search_auto_relax_v1"

#: 「凑够多少人」的目标。与前端 ``KOL_SEARCH_RESULT_LIMIT`` 同值。
DEFAULT_TARGET = 30

#: 一次搜索最多做几次 COUNT。纯 SQL COUNT 很便宜,但仍然要有上限防失控。
MAX_ESTIMATES = 12

#: **质量合格线**——不是操作员偏好,任何情况下都不许自动放宽。
#: 本模块所有写路径只碰 :data:`RELAXABLE_FILTER_KEYS`,这里列出来是为了能被测试正面断言,
#: 也为了让 payload 能如实告诉操作员「这些没动过」。
PROTECTED_FILTER_KEYS: frozenset[str] = frozenset(
    {
        "gear_content",
        "freshness_days",
        "recency_days",
        "max_age_days",
        "evidence_min_terms",
        "min_evidence_terms",
        "product_anchor",
        "require_product_anchor",
        "brand_safety",
        "account_safety",
        "quality_floor",
    }
)

#: 可放宽的筛选键。**顺序无关**,真正的顺序在 :func:`_candidate_steps`。
RELAXABLE_FILTER_KEYS: tuple[str, ...] = (
    "languages",
    "countries",
    "verticals",
    "followers_min",
    "platforms",
)

#: 界面可能以顶层字段(而非 ``filters``)送来的筛选键,与既有路由行为逐字节一致。
BODY_FILTER_KEYS: tuple[str, ...] = (
    "countries",
    "languages",
    "followers_min",
    "followers_max",
    "follower_min",
    "follower_max",
    "verticals",
    "gear_content",
)

#: 粉丝下限的档位阶梯(降序)。放宽只会沿着这个梯子往下走,**且永不低于**
#: :data:`smart_query_intent.AUDIENCE_SCALE_FLOOR`(3000,= 库内合格线 SMART_LOCAL_MIN_FOLLOWERS)。
#: 也就是说「放宽粉丝下限」最多把模型自己抬上去的那截还回来,碰不到质量地板。
FOLLOWERS_TIERS: tuple[int, ...] = (
    500_000,
    100_000,
    30_000,
    smart_query_intent.AUDIENCE_SCALE_FLOOR,
)

ORIGIN_OPERATOR = "operator"
ORIGIN_MODEL = "model"

STATUS_NOT_NEEDED = "not_needed"
STATUS_RELAXED = "relaxed"
STATUS_SHORT = "short"
STATUS_DISABLED = "disabled"
STATUS_UNAVAILABLE = "unavailable"

ACTION_INCLUDE_UNKNOWN = "include_unknown"
ACTION_LOWER = "lower"
ACTION_DROP = "drop"
#: 「系统替操作员加了一条他没说过的硬筛」。与三个放宽动作同级,一样进台账、一样能撤。
ACTION_ADD = "add"

#: 请求体里「只用我自己的条件」那一档:去掉系统推断的加项。缺省 = 采纳加项。
BODY_AUTO_FILTERS_KEY = "auto_filters"
#: 请求体里逐项去掉某几条系统加项(值 = 召回筛选键名列表)。
BODY_DROPPED_KEYS: tuple[str, ...] = ("dropped_auto_filters", "drop_auto_filters")

#: 前端照抄就能回到「只用我自己的条件」的请求体片段。**任何状态下都随 payload 一起给**。
RESTORE_REQUEST: dict[str, Any] = {"auto_relax": False, BODY_AUTO_FILTERS_KEY: False}

#: 估算器契约:入参 = 一份完整筛选字典,出参 = 三态计数。
#: **必须是零成本纯 SQL COUNT**——不许调 provider、不许调大模型(用户红线 2)。
#: 三态口径复用 ``profile_recall_filter_modes``:``qualified`` / ``unknown`` / ``mismatch``
#: 三档分开,不许把「未知」混进「不符合」。
YieldEstimator = Callable[[Mapping[str, Any]], Mapping[str, Any]]

#: 产量预估由另一条车道独占,这里只按名字懒加载,拿不到就如实报 ``unavailable``,
#: 绝不自己造一个估算糊弄过去。首选 ``search_yield_estimate.estimate_yield`` —— 那是
#: 产量车道给本车道开的取数口,把三态总账(``qualified`` / ``unknown`` / ``mismatch``)
#: 摊平在顶层;它不在时退到算法本体 ``pool_yield_estimate.estimate_pool_yield``
#: (人数在 ``estimated``,三态分档嵌在 ``totals`` 里,只影响明细不影响判断)。
_ESTIMATOR_CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("app.domains.kol.search_yield_estimate", ("estimate_yield",)),
    ("app.domains.kol.pool_yield_estimate", ("estimate_pool_yield",)),
)

#: 提议车道的 facet 名 → 召回筛选键名。``min_followers`` 是那边的标量项名。
FACET_TO_FILTER_KEY: dict[str, str] = {
    "countries": "countries",
    "languages": "languages",
    "verticals": "verticals",
    "platforms": "platforms",
    "min_followers": "followers_min",
}

#: 反向表:拿召回筛选键去提议车道 facet 里取「为什么加这一条」。
FILTER_KEY_TO_FACET: dict[str, str] = {value: key for key, value in FACET_TO_FILTER_KEY.items()}


@dataclass(frozen=True)
class RelaxStep:
    """一格放宽动作。``to`` 只对 :data:`ACTION_LOWER` 有意义(目标粉丝下限)。"""

    key: str
    action: str
    to: int | None = None


# ── 取值归一 ────────────────────────────────────────────────────────────────


def _as_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        return _as_list(value.get("values"))
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    text = str(value or "").strip().lower()
    return [text] if text else []


def _as_positive_int(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _count_of(estimate: Any) -> int:
    """从估算器返回值里取「合格人数」。看不懂就返回 0(失败方向 = 认为不够,继续如实上报)。"""
    if not isinstance(estimate, Mapping):
        return 0
    for key in ("estimated", "qualified", "count", "matched"):
        if key not in estimate:
            continue
        try:
            return max(0, int(float(estimate[key])))
        except (TypeError, ValueError):
            continue
    return 0


def _normalise_estimate(estimate: Any) -> dict[str, Any]:
    """基线读数归一。

    **只透传估算器真的给了的字段**——缺的就是缺的,绝不补 0 冒充「确认为零」,
    那正是「未知混进不符合」的老毛病。三态分档、口径说明、池子基数原样带出去,
    让界面能如实说「这是库内可选人数,联网还能补多少人不在此列」。
    """
    source = estimate if isinstance(estimate, Mapping) else {}
    out: dict[str, Any] = {"qualified": _count_of(source)}
    for key in ("unknown", "mismatch", "pool_total"):
        if key in source:
            out[key] = _as_positive_int(source.get(key)) or 0
    for key in ("scope", "scope_note", "tri_state", "not_estimated", "degraded"):
        if source.get(key) not in (None, "", [], {}):
            out[key] = source[key]
    return out


# ── 来源判定:哪些是操作员亲手勾的,哪些是模型推断的 ──────────────────────


def assemble_recall_filters(
    body: Mapping[str, Any],
    *,
    query_platforms: Any = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """把请求体拼成召回筛选字典,并给每个键盖上「操作员」的来源戳。

    行为与既有路由逐字节一致(``filters`` 为主 + 平台兜底链 + 顶层键补齐),这里只是
    额外产出 ``origins``。**从请求体进来的一律 operator**——界面上的
    ``toKolSearchApiFilters`` 只会输出操作员真正设过的键,所以「在 body 里」= 「他勾了」。
    """
    raw = body.get("filters")
    if raw not in (None, "") and not isinstance(raw, dict):
        raise ValueError("filters must be an object")
    filters: dict[str, Any] = dict(raw or {})

    if not filters.get("platforms"):
        explicit_body_platforms = (
            body.get("platforms")
            or body.get("platform")
            or body.get("new_discovery_platforms")
            or body.get("discovery_platforms")
        )
        if explicit_body_platforms:
            filters["platforms"] = explicit_body_platforms
        elif query_platforms:
            # 只有操作员原话里写死的平台才升级成硬筛;计划里的「全平台」默认值不算。
            filters["platforms"] = query_platforms

    for key in BODY_FILTER_KEYS:
        if body.get(key) not in (None, "") and key not in filters:
            filters[key] = body.get(key)

    origins = {key: ORIGIN_OPERATOR for key, value in filters.items() if value not in (None, "", [], {})}
    return filters, origins


def filter_proposal(plan: Any) -> dict[str, Any]:
    """取提议车道挂在计划上的 ``filter_proposal``。没有就返回空 dict。"""
    plan_dict = plan if isinstance(plan, dict) else {}
    proposal = plan_dict.get("filter_proposal")
    return proposal if isinstance(proposal, dict) else {}


def merge_plan_filters(
    filters: Mapping[str, Any],
    origins: Mapping[str, str],
    plan: Any,
) -> tuple[dict[str, Any], dict[str, str]]:
    """把提议车道的五项筛选并进来,并按**它自己的**明确 / 推断判定盖来源戳。

    消费的是 ``plan["filter_proposal"]``(``smart_query_facets.propose_facets`` 的产出):
    ``filters`` 是已经转好形的召回筛选,``relaxable_fields`` 是那边算好的「可动集合」。
    这里不重新判定「操作员是不是明确说了」——判定权在提议车道的原话规则手里,
    本模块只负责执行:``relaxable_fields`` 里的标 ``model``,其余一律 ``operator``。

    界面上勾过的键永远优先:操作员亲手设的值不会被提议覆盖,来源戳也不会被改写。
    没有 ``filter_proposal``(老计划 / 缓存命中前的旧盘)时退回既有的粉丝下限契约
    :func:`smart_query_intent.resolve_audience_scale`,不另立口径。
    """
    merged = dict(filters)
    marked = dict(origins)
    proposal = filter_proposal(plan)
    proposed = proposal.get("filters")
    relaxable = {
        FACET_TO_FILTER_KEY[field]
        for field in proposal.get("relaxable_fields") or []
        if field in FACET_TO_FILTER_KEY
    }

    if isinstance(proposed, dict):
        for key, value in proposed.items():
            if merged.get(key) not in (None, "", [], {}) or value in (None, "", [], {}):
                continue
            merged[key] = value
            marked[key] = ORIGIN_MODEL if key in relaxable else ORIGIN_OPERATOR
        return merged, marked

    scale = smart_query_intent.resolve_audience_scale(plan if isinstance(plan, dict) else {}, filters)
    if scale.get("hint_applied") and _as_positive_int(scale.get("applied_followers_min")):
        merged["followers_min"] = int(scale["applied_followers_min"])
        marked["followers_min"] = ORIGIN_MODEL
    return merged, marked


def advice_source(plan: Any) -> str:
    """这次的筛选提议是读懂描述算出来的,还是退回固定规则给的。降级必须如实说(红线 6)。"""
    proposal = filter_proposal(plan)
    if proposal:
        return "model" if str(proposal.get("source") or "") == "model" else "rules"
    plan_dict = plan if isinstance(plan, dict) else {}
    if plan_dict.get("fallback_used"):
        return "rules"
    provider = str(plan_dict.get("provider") or "").strip().lower()
    if provider in {"", "rule_v0", "provider_free", "product_catalog_guard"}:
        return "rules"
    return "model"


# ── 加筛选:系统替操作员加了什么(与放宽同等可见)──────────────────────────


def _facet_of(plan: Any, key: str) -> Mapping[str, Any]:
    """取提议车道对这一项的自述(``evidence`` / ``source``)。取不到就空,不自己编理由。"""
    facets = filter_proposal(plan).get("facets")
    field = FILTER_KEY_TO_FACET.get(key)
    facet = facets.get(field) if isinstance(facets, Mapping) and field else None
    return facet if isinstance(facet, Mapping) else {}


def _addition_record(key: str, value: Any, plan: Any) -> dict[str, Any]:
    """一条「系统加的筛选」的如实台账:加了什么、为什么加、能不能一键去掉。``reason`` 是
    提议车道 ``evidence`` 的**原样透传**;透传不到就是空串,由门面说通用话,不在这里编理由。
    """
    facet = _facet_of(plan, key)
    is_scalar = key == "followers_min"
    return {
        "key": key,
        "action": ACTION_ADD,
        "origin": ORIGIN_MODEL,
        "source": str(facet.get("source") or ""),
        "reason": str(facet.get("evidence") or ""),
        "mode": str(value.get("mode") or "require") if isinstance(value, dict) else "require",
        "values": None if is_scalar else _as_list(value),
        "value": _as_positive_int(value) if is_scalar else None,
        # 加项一律可撤:操作员没说过的条件,他随时能拿掉(红线:加与松同等可见、同等可撤)。
        "removable": True,
    }


def _model_added_keys(filters: Mapping[str, Any], origins: Mapping[str, str]) -> list[str]:
    """哪些键是系统加的:来源为模型 + 当前真的有值 + 不是合格线(合格线永远不是加项)。"""
    return [
        key
        for key, value in filters.items()
        if value not in (None, "", [], {})
        and key not in PROTECTED_FILTER_KEYS
        and str(origins.get(key) or ORIGIN_OPERATOR) == ORIGIN_MODEL
    ]


def operator_filters(filters: Mapping[str, Any], origins: Mapping[str, str]) -> dict[str, Any]:
    """只剩操作员自己那部分的筛选 —— 「改回我的条件」的落点。来源不是模型的一律留下
    (含来源不明的:失败方向 = 当成操作员的),合格线也一律留下(那是库的标准,不是谁加的)。
    """
    added = set(_model_added_keys(filters, origins))
    return {key: value for key, value in filters.items() if key not in added}


def split_additions(
    filters: Mapping[str, Any],
    origins: Mapping[str, str],
    plan: Any,
    *,
    keep: bool = True,
    dropped_keys: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    """把「系统加的筛选」摘出来:返回 ``(筛选, 来源, 采纳的加项, 去掉的加项)``。

    ``keep=False`` = 操作员按了「改回我的条件」,系统加的一项都不用;``dropped_keys`` =
    他逐条点掉的那几项。两种都**不是静默丢弃**:被去掉的项照样进 ``added_dropped``,
    界面据此说「已按你的要求去掉了这几条」,并且能一键恢复。
    """
    kept_filters, kept_origins = dict(filters), dict(origins)
    added: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for key in _model_added_keys(filters, origins):
        record = _addition_record(key, filters.get(key), plan)
        if keep and key not in dropped_keys:
            added.append(record)
            continue
        kept_filters.pop(key, None)
        kept_origins.pop(key, None)
        dropped.append({**record, "dropped": True})
    return kept_filters, kept_origins, added, dropped


def dropped_auto_filter_keys(body: Mapping[str, Any]) -> frozenset[str]:
    """请求体里逐条点掉的加项键名。认不出的形状一律当「没点掉」(失败方向 = 保持现状)。"""
    for field in BODY_DROPPED_KEYS:
        raw = body.get(field)
        if isinstance(raw, (list, tuple, set)):
            return frozenset(str(item).strip() for item in raw if str(item).strip())
    return frozenset()


# ── 放宽阶梯 ────────────────────────────────────────────────────────────────


def _is_relaxable(key: str, filters: Mapping[str, Any], origins: Mapping[str, str]) -> bool:
    """可放宽 = 在放宽白名单里 + 当前真的设了值 + 来源是模型。

    来源缺失一律当 ``operator``(失败方向:不明 = 不动),这是红线 1 的兜底。
    """
    if key in PROTECTED_FILTER_KEYS or key not in RELAXABLE_FILTER_KEYS:
        return False
    if filters.get(key) in (None, "", [], {}):
        return False
    return str(origins.get(key) or ORIGIN_OPERATOR) == ORIGIN_MODEL


def _lower_tiers(current: Any) -> list[int]:
    """比当前下限更低的档位(降序)。永不越过 3000 这块质量地板。"""
    value = _as_positive_int(current)
    if value is None:
        return []
    return [tier for tier in FOLLOWERS_TIERS if tier < value]


def _candidate_steps(filters: Mapping[str, Any], origins: Mapping[str, str]) -> list[RelaxStep]:
    """按「代价从小到大」排出可走的放宽阶梯。

    代价口径(用户裁令):**先松未知率高的维度**——语言空 71.2%、国家空 35.1%,
    把它们从「必须确认是」放到「确认是 + 未知」,放回来的几乎全是「我们不知道」的人,
    而不是「确认不符」的人。再往后才是丢掉模型自己猜的垂类 / 粉丝下限 / 平台。
    最后两格(整条丢掉语言 / 国家)才会真正放进「确认不符」的人,代价最高,排最后。
    """
    steps: list[RelaxStep] = []
    for key in ("languages", "countries"):
        if _is_relaxable(key, filters, origins) and key in TRI_STATE_FILTER_FIELDS:
            steps.append(RelaxStep(key, ACTION_INCLUDE_UNKNOWN))
    if _is_relaxable("verticals", filters, origins):
        steps.append(RelaxStep("verticals", ACTION_DROP))
    if _is_relaxable("followers_min", filters, origins):
        steps.extend(RelaxStep("followers_min", ACTION_LOWER, to=tier) for tier in _lower_tiers(filters.get("followers_min")))
    if _is_relaxable("platforms", filters, origins):
        steps.append(RelaxStep("platforms", ACTION_DROP))
    for key in ("languages", "countries"):
        if _is_relaxable(key, filters, origins):
            steps.append(RelaxStep(key, ACTION_DROP))
    return steps


def _apply_step(filters: Mapping[str, Any], step: RelaxStep) -> dict[str, Any]:
    """走一格。纯函数:返回新字典,原字典不动。"""
    nxt = dict(filters)
    if step.action == ACTION_INCLUDE_UNKNOWN:
        nxt[step.key] = {"values": _as_list(filters.get(step.key)), "mode": "include_unknown"}
    elif step.action == ACTION_LOWER:
        nxt[step.key] = int(step.to or smart_query_intent.AUDIENCE_SCALE_FLOOR)
    elif step.action == ACTION_DROP:
        nxt.pop(step.key, None)
    return nxt


def _step_record(step: RelaxStep, before: int, after: int, filters: Mapping[str, Any]) -> dict[str, Any]:
    """一格放宽的如实台账。**只有数字和键名,没有面向操作员的句子**——文案归门面。"""
    return {
        "key": step.key,
        "action": step.action,
        "count_before": before,
        "count_after": after,
        "gained": max(0, after - before),
        # include_unknown 这一格放回来的人,按三态口径**全部**是「字段没填」的人,
        # 不含任何「确认不符」的人。前端据此说「他们只是没填」才不算撒谎。
        "gained_are_unknown_only": step.action == ACTION_INCLUDE_UNKNOWN,
        "from_values": _as_list(filters.get(step.key)) if step.action != ACTION_LOWER else None,
        "from_value": _as_positive_int(filters.get(step.key)) if step.action == ACTION_LOWER else None,
        "to_value": step.to if step.action == ACTION_LOWER else None,
    }


# ── 主流程 ──────────────────────────────────────────────────────────────────


def default_estimator() -> YieldEstimator | None:
    """懒加载产量预估取数口(另一条车道独占)。拿不到就返回 None,由调用方如实报不可用。"""
    for module_name, function_names in _ESTIMATOR_CANDIDATES:
        try:
            module = __import__(module_name, fromlist=["*"])
        except ImportError:
            continue
        for name in function_names:
            candidate = getattr(module, name, None)
            if callable(candidate):
                return candidate
    logger.warning("auto_relax: no yield estimator resolved from %s", [name for name, _ in _ESTIMATOR_CANDIDATES])
    return None


def _envelope(
    filters: Mapping[str, Any],
    origins: Mapping[str, str],
    target: int,
    *,
    added: list[dict[str, Any]],
    added_dropped: list[dict[str, Any]],
) -> dict[str, Any]:
    """所有状态共用的信封。**加项台账与还原落点在这里,所以每一条早退路径都带着它们。**"""
    return {
        "schema": SCHEMA,
        "target": target,
        "applied": [],
        "skipped": [],
        # 加与松对称:applied = 系统松了什么,added = 系统加了什么(操作员没说过的)。
        "added": added,
        "added_dropped": added_dropped,
        "baseline": {},
        "estimates_performed": 0,
        "provider_calls": False,
        "estimate_cost": "sql_count_only",
        "advice_source": "rules",
        "origins": dict(origins),
        "protected_untouched": sorted(PROTECTED_FILTER_KEYS & set(filters)),
        "effective_filters": dict(filters),
        # 放宽前那份(= 本次实际执行的起点,含系统加项)。
        "original_filters": dict(filters),
        # 操作员自己那份(= 「改回我的条件」真正该回到的地方)。任何状态下都有。
        "operator_filters": operator_filters(filters, origins),
        "restore_request": dict(RESTORE_REQUEST),
    }


def plan_auto_relax(
    filters: Mapping[str, Any],
    origins: Mapping[str, str],
    *,
    estimator: YieldEstimator | None,
    target: int = DEFAULT_TARGET,
    enabled: bool = True,
    plan: Any = None,
    max_estimates: int = MAX_ESTIMATES,
    added: list[dict[str, Any]] | None = None,
    added_dropped: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """算产量 → 不够就按阶梯自动放宽 → 把整个过程如实交出来。

    返回 payload 见模块文档;``effective_filters`` 是调用方接下来真正该用的那份筛选。
    任何异常路径都**不会**把筛选放得比传进来的更松——失败方向永远是保持操作员原样。

    ``added`` / ``added_dropped`` 由 :func:`split_additions` 给;不给就按当前来源现算,
    保证**任何**调用方(包括直接调本函数的)都拿得到加项台账,加筛选没有静默的路径。
    """
    envelope = _envelope(
        filters,
        origins,
        target,
        added=added if added is not None else split_additions(filters, origins, plan)[2],
        added_dropped=list(added_dropped or []),
    )
    envelope["advice_source"] = advice_source(plan)
    envelope["baseline_count"] = None
    envelope["final_count"] = None
    if not enabled:
        return {**envelope, "status": STATUS_DISABLED}
    if estimator is None:
        return {**envelope, "status": STATUS_UNAVAILABLE, "unavailable_reason": "estimator_missing"}

    try:
        baseline_estimate = _normalise_estimate(estimator(dict(filters)))
    except Exception:
        logger.warning("auto_relax: baseline estimate failed", exc_info=True)
        return {**envelope, "status": STATUS_UNAVAILABLE, "unavailable_reason": "estimate_failed"}

    performed = 1
    current: dict[str, Any] = dict(filters)
    count = baseline_estimate["qualified"]
    envelope["baseline"] = baseline_estimate
    envelope["baseline_count"] = count
    # 口径说明原样透到界面:这是**库内**可选人数,联网还能补多少人不在此列。
    for key in ("scope_note", "pool_total"):
        if key in baseline_estimate:
            envelope[key] = baseline_estimate[key]

    steps = _candidate_steps(current, origins)
    envelope["skipped"] = [
        {"key": key, "reason": "operator_explicit"}
        for key in RELAXABLE_FILTER_KEYS
        if filters.get(key) not in (None, "", [], {})
        and str(origins.get(key) or ORIGIN_OPERATOR) == ORIGIN_OPERATOR
    ]

    if count >= target:
        return {**envelope, "status": STATUS_NOT_NEEDED, "final_count": count, "estimates_performed": performed}

    applied: list[dict[str, Any]] = []
    for step in steps:
        if count >= target or performed >= max_estimates:
            break
        candidate_filters = _apply_step(current, step)
        try:
            candidate_count = _count_of(estimator(candidate_filters))
        except Exception:
            logger.warning("auto_relax: estimate failed at step %s/%s", step.key, step.action, exc_info=True)
            break
        performed += 1
        applied.append(_step_record(step, count, candidate_count, current))
        current = candidate_filters
        count = candidate_count

    envelope["applied"] = applied
    envelope["effective_filters"] = current
    envelope["estimates_performed"] = performed
    envelope["final_count"] = count
    envelope["status"] = STATUS_RELAXED if count >= target else STATUS_SHORT
    return envelope


def run_auto_relax(
    body: Mapping[str, Any],
    plan: Any,
    *,
    query_platforms: Any = None,
    estimator: YieldEstimator | None = None,
    target: int = DEFAULT_TARGET,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """路由用的一步到位入口:拼筛选 → 并模型提议 → 摘加项 → 放宽 → 返回 ``(筛选, 台账)``。

    两个开关都在请求体里,**互相独立**,合起来才是完整的「改回我的条件」:

    * ``body["auto_relax"] is False`` —— 这一次一格都不松,台账标 ``disabled``;
    * ``body["auto_filters"] is False`` —— 系统推断出来的硬筛一条都不加,被去掉的项照样
      进 ``added_dropped``,界面据此说清「已按你的要求去掉了这几条」;
    * ``body["dropped_auto_filters"] = ["countries", ...]`` —— 逐条点掉某几项系统加项。

    上一版只有前一个开关:关掉自动放宽之后,系统推断出来的国家 / 语言 / 垂类照样被硬加上去,
    于是「改回我的条件」按下去**回不到操作员的条件**。现在 :data:`RESTORE_REQUEST` 两个开关
    一起送,才是真的回得去。
    """
    filters, origins = assemble_recall_filters(body, query_platforms=query_platforms)
    filters, origins = merge_plan_filters(filters, origins, plan)
    filters, origins, added, dropped = split_additions(
        filters,
        origins,
        plan,
        keep=body.get(BODY_AUTO_FILTERS_KEY) is not False,
        dropped_keys=dropped_auto_filter_keys(body),
    )
    enabled = body.get("auto_relax") is not False
    payload = plan_auto_relax(
        filters,
        origins,
        estimator=estimator if estimator is not None else default_estimator(),
        target=target,
        enabled=enabled,
        plan=plan,
        added=added,
        added_dropped=dropped,
    )
    effective = payload.get("effective_filters")
    return (dict(effective) if isinstance(effective, dict) else dict(filters)), payload


__all__ = [
    "ACTION_ADD",
    "ACTION_DROP",
    "ACTION_INCLUDE_UNKNOWN",
    "ACTION_LOWER",
    "BODY_AUTO_FILTERS_KEY",
    "BODY_DROPPED_KEYS",
    "BODY_FILTER_KEYS",
    "FILTER_KEY_TO_FACET",
    "RESTORE_REQUEST",
    "DEFAULT_TARGET",
    "FOLLOWERS_TIERS",
    "ORIGIN_MODEL",
    "ORIGIN_OPERATOR",
    "PROTECTED_FILTER_KEYS",
    "RELAXABLE_FILTER_KEYS",
    "SCHEMA",
    "STATUS_DISABLED",
    "STATUS_NOT_NEEDED",
    "STATUS_RELAXED",
    "STATUS_SHORT",
    "STATUS_UNAVAILABLE",
    "RelaxStep",
    "YieldEstimator",
    "advice_source",
    "assemble_recall_filters",
    "default_estimator",
    "dropped_auto_filter_keys",
    "merge_plan_filters",
    "operator_filters",
    "plan_auto_relax",
    "run_auto_relax",
    "split_additions",
]
