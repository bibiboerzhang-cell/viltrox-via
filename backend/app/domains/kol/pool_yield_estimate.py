"""产量预估:**搜之前**就告诉操作员「这个组合能出几个人」(2026-08-26)。

用户原话:「我觉得我输入完之后最好是有个大模型计算,然后自动化选择输入,别总是 00000」。
「00000」= 漏斗每一级都是 0。线上实测的阶梯(池 2036 人)是:

    只勾美国 160 -> +粉丝 1 万 75 -> +粉丝 5 万 49 -> +英语 **6** -> +生活方式 **0**

最后那两刀之所以致命,是因为池里 ``language`` 空 1450/2036(71.2%)、``country`` 空 715
(35.1%),而硬筛缺省档把「没填」当「不符合」一并驳回。于是「语言」这一刀砍掉的 43 人里
绝大多数只是**没填**,不是不说英语。本模块把这件事在开搜前摆到台面上。

三件事,一件不多:

1. **分档**(不是一个数字):每一维分别报「合格 / 未知(没填)/ 取数腿够不着 /
   明确不符」四档。「未知」与「明确不符」分开记,是因为把两者混成一个「不符合」正是
   今天 58% 人群被误杀的根因;「取数腿够不着」单开一档,是因为那批人**判定放行了但
   搜索取不回来**,混进「合格」就成了虚报(见 ``pool_yield_recall_parity``)。
2. **产量阶梯**:逐条加(``ladder``)与逐条松(``drop_one``)两张表。知道最终是 6 还不够,
   要知道**松哪一项**能回来多少人 —— ``drop_one``(整条去掉某一维)是自动放宽车道
   唯一该采信的依据,因为只有它报的增益搜索兑现得了。
3. **零成本**:纯 SELECT / COUNT。没勾内容方向时只跑**一条** GROUP BY(本地 1787 人
   实测 3.6 ms);勾了才逐人取键,并且只为**阶梯真正用得上的那批 id** 判内容方向。

红线落点:

* **只报搜索真给得出的数。**(2026-08-26 对抗复核坐实的头号病:预估按逐人三态判定
  算数,可真搜一遍时候选**先**要被库内取数的那条 SQL 腿捞出来,捞不出来的人根本走
  不到判定跟前。于是「放宽语言能多回来 85 人」这种话搜索一个也兑现不了。)
  现在每一维都过 ``pool_yield_recall_parity.recall_leg_outcome`` 两道一起算:
  取数腿够不着的人一律落进 ``unrecallable`` 这一档,**不计入任何可回收的增益**。
  兑现不了的部分逐条登记进 ``not_estimated``,不许留在数字里。
  为什么选「缩小预估」而不是「让召回腿支持三态」,连同代价,写在
  ``pool_yield_recall_parity`` 的模块头。
* **这个数是硬筛后的可选面,不是最终结果。** 新鲜度、证据相关性、产品锚、账号安全、
  3000 粉硬底、跨平台去重都是**合格线**,本模块一概不估也一概不松,全部登记在
  ``not_estimated`` 里;实际拿到手的人只会更少,不会更多。
* **模型只提议,数据库定夺。** 本模块不调 LLM、不调 provider、不写库;能不能出人
  由 COUNT 说了算。上游若拿 LLM 猜产量,那是越权。
* **不碰合格线。** 本模块只算操作员的**筛选偏好**(国家 / 语言 / 内容方向 / 粉丝闸 /
  平台)。新鲜度、器材证据、证据词数、产品锚、账号安全性一概不在射程内,也不因为
  这里估出 0 就有任何理由去松它们。
* **只估库内。** 联网发现能再补多少人不在此列(那要花钱且不可预测),``scope_note``
  必须原样透到界面上。
* **诚实缺口。** 器材证据闸、已收藏全局排除、在线腿都进 ``not_estimated``,
  一个都不许藏。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from app.core.logging import get_logger
from app.domains.kol import pool_yield_estimate_sql as yield_sql
from app.domains.kol.profile_recall_filter_modes import (
    OUTCOME_MISMATCH,
    OUTCOME_PASS,
    OUTCOME_UNKNOWN,
    normalize_mode,
)
from app.domains.kol.pool_yield_recall_parity import (
    OUTCOME_UNRECALLABLE,
    SQL_FILTERED_DIMENSIONS,
    mode_is_recallable,
    recall_leg_outcome,
    recall_sql_values,
)
from app.domains.kol.profile_recall_projection import (
    _normalize_recall_filters,
)


logger = get_logger(__name__)

#: 阶梯的固定加刀顺序 —— 与线上实测阶梯(国家 -> 粉丝 -> 语言 -> 内容方向)一致,
#: 好让操作员看到的顺序与他自己勾选的直觉顺序对得上。顺序固定 = 两次估同一组合
#: 必然给出同一张阶梯表,不随字典序漂移。
DIMENSION_ORDER: tuple[str, ...] = (
    "platforms",
    "countries",
    "followers_min",
    "followers_max",
    "languages",
    "verticals",
)

#: 门面用词(禁术语、禁厂商名)。
DIMENSION_LABELS: dict[str, str] = {
    "platforms": "平台",
    "countries": "国家",
    "followers_min": "粉丝下限",
    "followers_max": "粉丝上限",
    "languages": "语言",
    "verticals": "内容方向",
}

#: 本模块**不估**的东西,一律如实登记。合格线那几条**每次都登记**——它们每次搜索都在
#: 生效,不因为这里估不出来就可以不说。
NOT_ESTIMATED_NOTES: dict[str, str] = {
    "gear_content": "器材证据要求是合格线不是偏好,本次不估;实际结果只会比这个数少。",
    "verticals": "内容方向这一维本次没能判出来,已从这张表里拿掉,不是判成了 0。",
    "favorites_exclusion": "已收藏的人在搜索里会被排掉,这个数没有减掉他们。",
    "online_discovery": "联网再找回来的人不在这个数里。",
    # ── 合格线(每次都登记)──────────────────────────────────────────────
    "qualification_freshness": (
        "作品新鲜度是合格线:近 45 天没有作品的人会被搜索挡掉(30 天内算新)。"
        "这个数没有减掉他们。"
    ),
    "qualification_evidence": (
        "作品证据的相关性门槛是合格线:搜不到与本次需求相关的作品就会被挡掉。"
        "这个数没有减掉他们。"
    ),
    "qualification_product_anchor": (
        "产品锚是合格线:必须搜到与本次产品对得上的作品。这个数没有减掉他们。"
    ),
    "qualification_account_safety": (
        "账号真实性与安全判定是合格线。这个数没有减掉被它挡掉的人。"
    ),
    "qualification_followers_floor": (
        "3000 粉的硬底是合格线,和你自己填的粉丝下限是两回事。这个数没有减掉他们。"
    ),
    "qualification_dedupe": (
        "同一个人在多个平台上会被合并成一条。这个数没有把重复的人合并掉,可能偏多。"
    ),
    # ── 兑现不了的档位(如实说清「这一档搜索给不出」)────────────────────
    "unknown_mode_not_recallable": (
        "「含未知」这一档目前搜索兑现不了:库内取数只按填了的值取人,资料没填的人"
        "取不回来。所以这里没有把他们算成「放宽就能回来的人」。"
        "真想把他们放回来,只能把这一维整条去掉——「逐条松」那张表里的数字才是真的。"
    ),
    "exclude_mode_not_recallable": (
        "「排除某国/某语言」这一档目前搜索兑现不了:库内取数腿只会按你点名的值取人,"
        "再由判定把这些人全部排掉,结果必然是 0。这里如实按 0 报,不是算错了。"
    ),
    "recall_key_gap": (
        "有些人库里写的国家/语言写法与你点的写法对不上(例如库里是 United States、"
        "你点的是 US),搜索的取数这一步就取不到他们。这些人已经从这个数里拿掉了。"
    ),
    "verticals_narrower_than_search": (
        "内容方向这里只看了资料和作品标题两路,搜索时还会看产品出镜和品牌露出;"
        "所以这一维可能少判,这个数偏保守——实际能挑的人可能比它多。"
    ),
}

#: 每次都要登记的合格线(顺序固定,便于界面稳定排列)。
ALWAYS_NOT_ESTIMATED: tuple[str, ...] = (
    "qualification_evidence",
    "qualification_freshness",
    "qualification_product_anchor",
    "qualification_account_safety",
    "qualification_followers_floor",
    "qualification_dedupe",
)

SCOPE = "local_pool"
SCOPE_NOTE = "这是库内可选人数;联网还能补多少人不在此列。"

#: 这个数到底是什么 —— 门面必须原样透出去,不许让操作员当成「最终能拿到几个人」。
ESTIMATE_BASIS = "hard_filter_only"
HEADLINE_NOTE = (
    "这是你这组筛选**硬筛之后还剩多少人可挑**,不是最后能拿到几个人。"
    "这些人还要过合格线(作品够新、证据对得上、产品锚、账号安全、3000 粉硬底、跨平台去重),"
    "所以真搜出来只会更少。"
)

_KEY_PLATFORM = 0
_KEY_COUNTRY = 1
_KEY_LANGUAGE = 2
_KEY_FOLLOWERS_MIN = 3
_KEY_FOLLOWERS_MAX = 4
_KEY_VERTICAL = 5

#: 走取数腿硬筛的三维在分组键里的下标(键的形状 == ``yield_sql.KEY_COLUMNS``,取的是原值)。
_SQL_KEY_INDEX: dict[str, int] = {
    "platforms": _KEY_PLATFORM,
    "countries": _KEY_COUNTRY,
    "languages": _KEY_LANGUAGE,
}

_STATE_TO_OUTCOME = {
    "pass": OUTCOME_PASS,
    "unknown": OUTCOME_UNKNOWN,
    "mismatch": OUTCOME_MISMATCH,
}


class _Spec:
    """一次预估的判定参数;从已归一的搜索侧 filters 直接派生,零二次解释。

    每一维都留着**两套**东西:操作员点的原值(取数腿绑进 SQL 的就是它的两种写法),
    以及三态模式。判定不在这里做,统一交给 ``pool_yield_recall_parity`` —— 取数腿与
    逐人判定两道一起算,一个口径。
    """

    def __init__(self, filters: dict[str, Any]) -> None:
        self.requested: dict[str, list[str]] = {
            name: [str(item).strip() for item in filters.get(name) or [] if str(item).strip()]
            for name in ("platforms", "countries", "languages")
        }
        self.modes: dict[str, str] = {
            name: normalize_mode(filters.get(f"{name}_mode"))
            for name in ("platforms", "countries", "languages")
        }
        self.sql_values = {
            name: recall_sql_values(name, values) for name, values in self.requested.items()
        }
        self.verticals = [str(item).strip() for item in filters.get("verticals") or [] if str(item).strip()]
        self.followers_min = filters.get("followers_min")
        self.followers_max = filters.get("followers_max")
        self._outcome_cache: dict[tuple[str, str], str] = {}

    @property
    def active(self) -> list[str]:
        """按固定顺序列出这次真正生效的维度。"""
        present = {
            "platforms": bool(self.requested["platforms"]),
            "countries": bool(self.requested["countries"]),
            "languages": bool(self.requested["languages"]),
            "verticals": bool(self.verticals),
            "followers_min": self.followers_min not in (None, ""),
            "followers_max": self.followers_max not in (None, ""),
        }
        return [name for name in DIMENSION_ORDER if present[name]]

    def unrecallable_modes(self) -> list[str]:
        """这次点了、但取数腿兑现不了的模式(``include_unknown`` / ``exclude``)。"""
        return [
            name
            for name in SQL_FILTERED_DIMENSIONS
            if self.requested[name] and not mode_is_recallable(name, self.modes[name])
        ]

    def outcome(self, dimension: str, key: tuple[str, ...]) -> str:
        """单维判定 —— **搜索真会怎么处置这个人**(取数腿 + 逐人判定,两道一起)。"""
        if dimension in SQL_FILTERED_DIMENSIONS:
            raw = key[_SQL_KEY_INDEX[dimension]]
            cached = self._outcome_cache.get((dimension, raw))
            if cached is None:
                cached = recall_leg_outcome(
                    dimension,
                    raw,
                    requested=self.requested[dimension],
                    mode=self.modes[dimension],
                    sql_values=self.sql_values[dimension],
                )
                self._outcome_cache[(dimension, raw)] = cached
            return cached
        if dimension == "followers_min":
            return _STATE_TO_OUTCOME.get(key[_KEY_FOLLOWERS_MIN], OUTCOME_UNKNOWN)
        if dimension == "followers_max":
            return _STATE_TO_OUTCOME.get(key[_KEY_FOLLOWERS_MAX], OUTCOME_UNKNOWN)
        if dimension == "verticals":
            return _STATE_TO_OUTCOME.get(key[_KEY_VERTICAL], OUTCOME_UNKNOWN)
        return OUTCOME_PASS


def _outcome_table(
    facts: list[tuple[tuple[str, ...], int]],
    spec: _Spec,
    dimensions: list[str],
) -> list[tuple[dict[str, str], int]]:
    """把每个分组一次性判完,后面的阶梯/松绑全部只是这张表上的加法。"""
    return [({name: spec.outcome(name, key) for name in dimensions}, count) for key, count in facts]


def _count_where_all_pass(table: list[tuple[dict[str, str], int]], dimensions: list[str]) -> int:
    return sum(
        count for outcomes, count in table if all(not outcomes[name] for name in dimensions)
    )


def _split_by(
    table: list[tuple[dict[str, str], int]],
    others: list[str],
    dimension: str,
) -> dict[str, int]:
    """在「其它维度全过」的人群里,按目标维度分成四档。

    * ``qualified`` —— 取数腿捞得到、判定也放行;
    * ``unknown`` —— 资料没填。**注意:今天的搜索也取不回他们**,把这一维切到
      「含未知」档并不能让他们回来(见 ``pool_yield_recall_parity`` 模块头);
    * ``unrecallable`` —— 判定放行了但取数腿够不着(同义词落差 / 已经点了兑现不了的档);
    * ``mismatch`` —— 确认不符,补数据也救不回来。

    只有把这一维**整条去掉**,后三档的人才会一起回来 —— ``drop_one`` 报的就是这个和。
    """
    buckets = {"qualified": 0, "unknown": 0, "unrecallable": 0, "mismatch": 0}
    for outcomes, count in table:
        if any(outcomes[name] for name in others):
            continue
        result = outcomes[dimension]
        if not result:
            buckets["qualified"] += count
        elif result == OUTCOME_UNKNOWN:
            buckets["unknown"] += count
        elif result == OUTCOME_UNRECALLABLE:
            buckets["unrecallable"] += count
        else:
            buckets["mismatch"] += count
    return buckets


def _combination_totals(
    table: list[tuple[dict[str, str], int]],
    dimensions: list[str],
    pool_total: int,
) -> dict[str, int]:
    """整组筛选的总账 —— 四档相加**必然**等于全池人数,不许有人没着落。

    * ``qualified`` —— 每一维都过,搜索真给得出;
    * ``unknown`` —— 挡住他的**全部**是「资料没填」。**这批人今天搜索取不回来**:
      库内取数只按填了的值取人。所以 ``unknown_recoverable_by_mode`` 恒为 False,
      这个数只当诊断(「该去补数据了」),绝不许当成放宽后的增益;
    * ``unrecallable`` —— 判定放行了但取数腿够不着(同义词落差、或点了兑现不了的档);
    * ``mismatch`` —— 至少有一维**确认**不符。补数据救不了他们。
    """
    qualified = 0
    unknown_only = 0
    unrecallable_only = 0
    for outcomes, count in table:
        results = {outcomes[name] for name in dimensions}
        if results <= {OUTCOME_PASS}:
            qualified += count
        elif OUTCOME_MISMATCH in results:
            continue
        elif OUTCOME_UNRECALLABLE in results:
            unrecallable_only += count
        else:
            unknown_only += count
    return {
        "qualified": qualified,
        "unknown": unknown_only,
        "unrecallable": unrecallable_only,
        # 「含未知」那一档取数腿够不着,别让下游把 unknown 当成能回收的人。
        "unknown_recoverable_by_mode": False,
        "mismatch": pool_total - qualified - unknown_only - unrecallable_only,
        "pool_total": pool_total,
    }


def _build_ladder(
    table: list[tuple[dict[str, str], int]],
    dimensions: list[str],
    pool_total: int,
) -> list[dict[str, Any]]:
    """逐条加刀:每一级报剩多少人,以及这一刀砍掉的人里有多少只是「没填」。"""
    ladder: list[dict[str, Any]] = [
        {"step": 0, "filter": None, "label": "全池(未加任何筛选)", "count": pool_total}
    ]
    applied: list[str] = []
    for index, name in enumerate(dimensions, start=1):
        buckets = _split_by(table, applied, name)
        applied = applied + [name]
        ladder.append(
            {
                "step": index,
                "filter": name,
                "label": DIMENSION_LABELS.get(name, name),
                "count": buckets["qualified"],
                "removed": buckets["unknown"] + buckets["unrecallable"] + buckets["mismatch"],
                "removed_unknown": buckets["unknown"],
                "removed_unrecallable": buckets["unrecallable"],
                "removed_mismatch": buckets["mismatch"],
            }
        )
    return ladder


def _build_drop_one(
    table: list[tuple[dict[str, str], int]],
    dimensions: list[str],
    estimated: int,
) -> list[dict[str, Any]]:
    """逐条松:**整条去掉**某一项各能回来多少人。按回来的人数从多到少排,最该松的排最前。

    这张表报的是「整条去掉」——去掉之后取数腿的那个条件也一起消失,所以四档的人会一起
    回来,这些数字搜索真兑现得了。**这是自动放宽唯一该采信的表**;「切到含未知档」
    那个动作今天兑现不了,别去那里找增益。
    """
    rows: list[dict[str, Any]] = []
    for name in dimensions:
        others = [item for item in dimensions if item != name]
        buckets = _split_by(table, others, name)
        recovered = (
            buckets["qualified"] + buckets["unknown"] + buckets["unrecallable"] + buckets["mismatch"]
        )
        rows.append(
            {
                "filter": name,
                "label": DIMENSION_LABELS.get(name, name),
                "count": recovered,
                "gain": recovered - estimated,
                "gain_unknown": buckets["unknown"],
                "gain_unrecallable": buckets["unrecallable"],
                "gain_mismatch": buckets["mismatch"],
            }
        )
    rows.sort(key=lambda item: (-item["gain"], item["filter"]))
    return rows


def _build_tri_state(
    table: list[tuple[dict[str, str], int]],
    dimensions: list[str],
) -> list[dict[str, Any]]:
    """每一维单独一行:在「其它条件都满足」的人群里,这一维把人分成了哪三档。"""
    rows: list[dict[str, Any]] = []
    for name in dimensions:
        others = [item for item in dimensions if item != name]
        buckets = _split_by(table, others, name)
        rows.append(
            {
                "filter": name,
                "label": DIMENSION_LABELS.get(name, name),
                "qualified": buckets["qualified"],
                "unknown": buckets["unknown"],
                "unrecallable": buckets["unrecallable"],
                "mismatch": buckets["mismatch"],
                "scope_count": (
                    buckets["qualified"]
                    + buckets["unknown"]
                    + buckets["unrecallable"]
                    + buckets["mismatch"]
                ),
            }
        )
    return rows


def _vertical_scan_ids(
    row_keys: list[tuple[int, tuple[str, ...]]],
    spec: _Spec,
    sql_dimensions: list[str],
) -> list[int]:
    """算出「阶梯真正用得上」的那批 id —— 只给他们判内容方向,别扫全池。

    阶梯与松绑要用到内容方向读数的人群,是「除某一维之外全过」这些集合的并集。
    没有任何非内容方向的筛选时,并集就是全池(那时如实报出扫了多少人)。
    """
    if not sql_dimensions:
        return [pool_id for pool_id, _ in row_keys]
    wanted: list[int] = []
    for pool_id, key in row_keys:
        failed = [name for name in sql_dimensions if spec.outcome(name, key)]
        if len(failed) <= 1:
            wanted.append(pool_id)
    return wanted


def _vertical_states(
    conn: Any,
    pool_ids: list[int],
    spec: _Spec,
) -> tuple[dict[int, str], list[str], int]:
    """判内容方向:纯函数复用搜索侧的多路取证,零取数以外的开销。"""
    if not pool_ids:
        return {}, [], 0
    try:
        from app.domains.kol.profile_vertical_signals import vertical_filter_outcome
    except Exception:
        logger.warning("产量预估:内容方向判定不可用,本次不估这一维", exc_info=True)
        return {}, ["verticals_unavailable"], 0
    inputs, queries = yield_sql.load_vertical_inputs(conn, pool_ids)
    states: dict[int, str] = {}
    for pool_id in pool_ids:
        row, evidence = inputs.get(pool_id, ({}, {}))
        try:
            outcome, _reading, _hits = vertical_filter_outcome(row, evidence, spec.verticals)
        except Exception:
            logger.debug("产量预估:单人内容方向判定失败,按未知计", exc_info=True)
            outcome = OUTCOME_UNKNOWN
        states[pool_id] = outcome or "pass"
    return states, [], queries


def _not_estimated(
    filters: dict[str, Any],
    degraded: list[str],
    spec: _Spec,
    dimensions: list[str],
    unrecallable_total: int,
) -> list[dict[str, str]]:
    """如实登记「这个数**没有**替你算的东西」。合格线那几条每次都在,一条都不许省。"""
    keys: list[str] = []
    if unrecallable_total > 0:
        keys.append("recall_key_gap")
    if any(name in ("countries", "languages") for name in dimensions):
        keys.append("unknown_mode_not_recallable")
    if any(spec.modes[name] == "exclude" for name in spec.unrecallable_modes()):
        keys.append("exclude_mode_not_recallable")
    if "verticals_unavailable" in degraded:
        keys.append("verticals")
    elif "verticals" in dimensions:
        keys.append("verticals_narrower_than_search")
    if filters.get("gear_content") == "yes":
        keys.append("gear_content")
    keys.extend(ALWAYS_NOT_ESTIMATED)
    keys.extend(("favorites_exclusion", "online_discovery"))
    seen: set[str] = set()
    return [
        {"item": key, "note": NOT_ESTIMATED_NOTES[key]}
        for key in keys
        if not (key in seen or seen.add(key))
    ]


def _applied_view(spec: _Spec, filters: dict[str, Any]) -> list[dict[str, Any]]:
    """操作员这次实际生效的每一维,附上「这个档搜索兑现得了吗」。"""
    out: list[dict[str, Any]] = []
    for name in spec.active:
        entry: dict[str, Any] = {"filter": name, "label": DIMENSION_LABELS.get(name, name)}
        if name in ("platforms", "countries", "languages", "verticals"):
            mode = normalize_mode(filters.get(f"{name}_mode"))
            entry["values"] = list(filters.get(name) or [])
            entry["mode"] = mode
            entry["mode_recallable"] = mode_is_recallable(name, mode)
        else:
            entry["value"] = int(filters.get(name) or 0)
        out.append(entry)
    return out


def estimate_pool_yield(
    filters: Any = None,
    *,
    get_connection: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """给一组筛选提议,用纯 SQL 算出库内能出几个人。**不调 provider、不调 LLM、不写库。**

    入参 ``filters`` 与搜索侧完全同形(``countries`` / ``languages`` / ``platforms`` /
    ``verticals`` / ``followers_min`` / ``followers_max``,支持 ``{"values": [...],
    "mode": "..."}`` 三态形态),归一化直接借搜索侧的 ``_normalize_recall_filters``,
    杜绝两套解释。
    """
    started = time.perf_counter()
    normalized, unsupported = _normalize_recall_filters(filters)
    spec = _Spec(normalized)
    dimensions = spec.active
    sql_dimensions = [name for name in dimensions if name != "verticals"]
    degraded: list[str] = []
    queries = 0
    conn = yield_sql.open_connection(get_connection)

    if "verticals" in dimensions:
        row_keys = yield_sql.load_row_keys(
            conn, followers_min=spec.followers_min, followers_max=spec.followers_max
        )
        queries += 1
        pool_total = len(row_keys)
        scan_ids = _vertical_scan_ids(row_keys, spec, sql_dimensions)
        states, vertical_degraded, vertical_queries = _vertical_states(conn, scan_ids, spec)
        queries += vertical_queries
        degraded.extend(vertical_degraded)
        if vertical_degraded:
            dimensions = sql_dimensions
            facts = [(key, 1) for _pool_id, key in row_keys]
        else:
            facts = [
                (key + (states.get(pool_id, OUTCOME_UNKNOWN),), 1) for pool_id, key in row_keys
            ]
        classified = 0 if vertical_degraded else len(scan_ids)
        rows_returned = len(row_keys)
    else:
        facts, pool_total = yield_sql.load_group_counts(
            conn, followers_min=spec.followers_min, followers_max=spec.followers_max
        )
        queries += 1
        classified = 0
        rows_returned = len(facts)

    table = _outcome_table(facts, spec, dimensions)
    estimated = _count_where_all_pass(table, dimensions)
    totals = _combination_totals(table, dimensions, pool_total)
    tri_state = _build_tri_state(table, dimensions)
    unrecallable_total = int(totals["unrecallable"]) + sum(
        int(row["unrecallable"]) for row in tri_state
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "scope": SCOPE,
        "scope_note": SCOPE_NOTE,
        "estimate_basis": ESTIMATE_BASIS,
        "headline_note": HEADLINE_NOTE,
        "pool_total": pool_total,
        "estimated": estimated,
        "totals": totals,
        "applied": _applied_view(spec, normalized),
        "unsupported": list(unsupported),
        "not_estimated": _not_estimated(
            normalized, degraded, spec, dimensions, unrecallable_total
        ),
        "tri_state": tri_state,
        "ladder": _build_ladder(table, dimensions, pool_total),
        "drop_one": _build_drop_one(table, dimensions, estimated),
        "cost": {
            "sql_queries": queries,
            "elapsed_ms": elapsed_ms,
            "rows_returned": rows_returned,
            "vertical_rows_classified": classified,
            "provider_calls": 0,
            "llm_calls": 0,
            "writes": 0,
        },
        "degraded": sorted(set(degraded)),
    }


__all__ = [
    "ALWAYS_NOT_ESTIMATED",
    "DIMENSION_LABELS",
    "DIMENSION_ORDER",
    "ESTIMATE_BASIS",
    "HEADLINE_NOTE",
    "NOT_ESTIMATED_NOTES",
    "SCOPE",
    "SCOPE_NOTE",
    "estimate_pool_yield",
]
