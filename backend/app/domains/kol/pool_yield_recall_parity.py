"""预估与**召回腿**的逐字对齐层(2026-08-26)。

## 为什么要有这一层

产量预估原来只照着 ``_candidate_filter_verdict``(逐人三态判定)算数,而真搜一遍的时候,
候选**先**要被库内取数的那条 SQL 腿捞出来,捞不出来的人根本走不到三态判定跟前。两条腿
的口径不一样,于是预估报出的数字搜索兑现不了。实测三处不一致:

1. **「含未知」这一档取数腿够不着。** 取数腿的国家/语言条件是
   ``LOWER(COALESCE(p.country,'')) IN (...)``,资料没填的人恒等于空串,**任何模式下**
   都不在这个集合里。于是预估说「放宽语言能多回来 85 人」,真搜一个也回不来。
2. **国家同义词两套口径。** 预估把 USA / 美国 / America / U.S. 都归一成 ``US``;
   取数腿绑进 SQL 的却是「原值小写 ∪ 国家码小写」两种字面量,库里写 ``United States``
   的人在取数腿这里就被刷掉了,预估却把他算成了合格。
3. **「排除某国/某语言」必然是 0。** 取数腿不认模式,照样 ``IN (点名的值)`` 只捞这些人,
   随后三态判定把他们**全部**排掉 —— 结果恒为 0。预估过去按判定口径算,报出一个
   搜索永远给不出的正数。

## 2026-08-26 更新:三处不一致已由搜索车道**在取数腿那一侧**修掉

语言腿先修(自报列 ∪ 推断列 + 三态下推),国家腿同日跟上(归一化闭包 + 三态下推,
见 ``profile_recall_country_gate``)。于是上面 1/2/3 三条对**语言与国家**都不再成立:
「含未知」真能多捞回人、同义词写法全捞得到、「排除」不再下推正向匹配。本层因此不再
把这两维登记成「兑现不了」,而是照着取数腿的新口径**跟着算** —— 口径仍然只有一套,
只是那一套现在住在取数腿的共用构造器里,本层调它,不自己复刻。
**平台**仍是老形态(SQL 里没有模式这回事),照旧只有 ``require`` 兑现得了。

## 二选一:选「预估只估召回腿真能兑现的部分」

红线给了两条路:(甲)让召回腿真支持三态与同义词,让预估兑现;(乙)预估只估召回腿
真能兑现的部分,把兑现不了的如实登记进 ``not_estimated``。**本车道选(乙)**,理由与
代价都摆在这里:

* **理由一(权属)**:召回腿的 SQL 在 ``profile_recall_precision`` /
  ``profile_recall_storage`` / ``profile_recall.py`` 三处,不在本车道的独占范围内。
  改它属于跨车道动刀,并发施工下必然撞车。
* **理由二(风险面)**:让取数腿支持三态,等于把「资料没填的人」放进**所有**库内搜索
  的候选面,这是搜索行为的实质变更(会影响排序、配额、合格线的分母),该由搜索车道
  带着自己的验收去做,不该由预估车道顺手改掉。
* **理由三(红线优先级)**:红线最重的一条是「绝不允许承诺一个搜索兑现不了的数字」。
  (乙)立刻止住谎报;(甲)在别人改完之前,谎报还在继续。
* **代价(如实写明)**:操作员从此看到的「含未知」增益是 **0**,而不是 85。那 85 人
  确实存在(资料没填而已),但**今天的搜索取不回他们**。想把他们放回来,唯一真能兑现
  的动作是把这一维**整条去掉**(``drop_one`` 那张表里的数字是真的)。这一点必须
  写在界面上,不许让操作员以为「含未知」这一档还有用。
  (2026-08-26:这一段的代价对**语言与国家**已经不用付了 —— 取数腿真支持了三态,
  那两维的「含未知」增益从此是真数。平台照旧。)
  另:向量召回那条腿不带硬筛,理论上能捞到没填的人,但它取决于向量库在不在、预算够不够、
  这个人有没有被索引、以及他排不排得进 top-K —— 那是「碰运气」,不是能拿去驱动
  自动松绑的产量。本层一律不把它算进可兑现的数。

## 本模块的边界

纯函数,不读库、不写库、不调 provider、不调 LLM。只回答一个问题:
**「这个人,今天的搜索取得回来吗?」**
"""
from __future__ import annotations

from typing import Any, Iterable

from app.domains.kol.profile_recall_filter_modes import (
    OUTCOME_MISMATCH,
    OUTCOME_PASS,
    OUTCOME_UNKNOWN,
    normalize_mode,
    tri_state_outcome,
)
from app.domains.kol.profile_recall_country_gate import (
    country_sql_value,
    country_sql_values,
)
from app.domains.kol.profile_recall_projection import (
    _country_match_key,
    _language_match_key,
)


#: 第四种结果:三态判定放行了,但库内取数腿**取不回来**这个人。
#: 与 ``mismatch``(确认不符)刻意分开记 —— 前者是我们够不着,后者是他真不符合。
OUTCOME_UNRECALLABLE = "unrecallable"

#: 走取数腿硬筛的维度。``verticals`` 不在其列(它只在逐人判定里)。
SQL_FILTERED_DIMENSIONS: tuple[str, ...] = ("platforms", "countries", "languages")

#: 召回腿 SQL 的**字面**片段。由对齐测试逐条 pin 住:召回腿一改,预估当场失败,
#: 而不是继续报一个对不上的数。三处产生这些片段的地方:
#: ``profile_recall_precision.lexical_recall_candidates``、
#: ``profile_recall_storage._pool_text_fallback_hits``(广度兜底腿)、
#: ``profile_recall.recall_kol_profiles``(绑值的构造)。
#:
#: 2026-08-26 更新:搜索车道已经把语言这一维改成走共用构造器
#: ``profile_recall_language_gate.language_hard_filter``(自报列 ∪ 推断列,并且认三态),
#: 同日国家这一维也改成走 ``profile_recall_country_gate.country_hard_filter``
#: (归一化闭包,并且认三态),于是「兑现不了」这条前提对这两维都不再成立 ——
#: 见 :func:`mode_is_recallable`。平台仍是老形态,预估口径照旧。
RECALL_SQL_PINS: dict[str, str] = {
    "platforms": "LOWER(COALESCE(p.platform, '')) IN (",
    "countries": "country_hard_filter(",
    "countries_column": "LOWER(COALESCE(p.country, ''))",
    "languages": "language_hard_filter(",
    "languages_self_column": "LOWER(TRIM(COALESCE(p.language, '')))",
    "languages_inferred_column": "LOWER(TRIM(COALESCE(p.language_inferred, '')))",
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def recall_sql_values(dimension: str, requested: Iterable[Any]) -> frozenset[str]:
    """召回腿**真正绑进 SQL** 的那组字面量。

    与 ``profile_recall.recall_kol_profiles`` 构造 ``_country_values`` /
    ``_language_values`` 的表达式逐字同源:国家 = 「原值小写 ∪ 国家码小写」,
    语言 = 「原值小写 ∪ 语言主码」,平台 = 「原值小写」。
    """
    if dimension == "countries":
        # 2026-08-26 起国家腿下推的是**归一化闭包**(所有会归一到被点名国家码的写法),
        # 由 ``profile_recall_country_gate`` 独家产出 —— 预估这一侧照抄那一份,
        # 不再自己拼「原值小写 ∪ 国家码小写」那两个字面量。
        return frozenset(country_sql_values(requested or []))
    out: set[str] = set()
    for raw in requested or []:
        text = _clean(raw)
        if not text:
            continue
        out.add(text.lower())
        if dimension == "languages":
            code = _language_match_key(text)
            if code:
                out.add(code)
    return frozenset(out)


def sql_admits(
    dimension: str,
    raw_value: Any,
    values: frozenset[str],
    mode: Any = "require",
) -> bool:
    """这个人**能不能被取数腿捞出来**。

    平台:刻意**不** btrim —— 那一腿写的还是 ``LOWER(COALESCE(p.platform,''))``,
    库里带空白的值在那边就是捞不出来。这里跟着不 btrim,才不会估出一个搜索给不出的人。

    国家:2026-08-26 起走 ``country_sql_filter``,那一侧按 :func:`country_sql_value`
    抹空白 + 小写地比,并且认三态 —— 所以这里直接调那个函数,两边一个口径,
    「库里写 ``United States`` 而操作员点 ``US``」这类同义词落差不再算成够不着。

    语言:2026-08-26 起走 ``language_sql_filter``,那一侧 ``LOWER(TRIM(COALESCE(...)))``
    并且认三态 —— 所以这里跟着 strip、跟着认模式,两边一个口径。
    **推断列(``language_inferred``)刻意不算进预估**:预估的分组 SQL 只取了自报列,
    多算等于承诺一个自己没数过的数。因此有推断值的库上,语言这一维的预估会**偏保守**
    (少报,绝不多报),方向红线仍然成立。
    """
    if not values:
        return True
    if dimension == "countries":
        normalized = normalize_mode(mode)
        if normalized == "exclude":
            return True  # 负向筛选不下推,取数腿谁都不挡,排除全交给逐人判定
        current = country_sql_value(raw_value)
        if not current:
            return normalized == "include_unknown"
        return current in values
    if dimension == "languages":
        current = str(raw_value or "").strip().lower()
        normalized = normalize_mode(mode)
        if normalized == "exclude":
            return True  # 语言这一维不再正向下推,取数腿谁都不挡,排除全交给逐人判定
        if not current:
            return normalized == "include_unknown"
        # ``= ?`` 或 ``substr(...,1,?) = ?``:主码相同、带地区后缀也算。
        return any(current == value or current.startswith(f"{value}-") for value in values)
    current = str(raw_value or "").lower()
    return current in values


def verdict_outcome(dimension: str, raw_value: Any, requested: Iterable[Any], mode: Any) -> str:
    """逐人三态判定,与 ``_candidate_filter_verdict`` 同口径(平台走 btrim 再 lower)。"""
    if dimension == "platforms":
        wanted = {_clean(item).lower() for item in requested or []}
        wanted.discard("")
        if not wanted:
            return OUTCOME_PASS
        current = _clean(raw_value).lower()
        if not current:
            return OUTCOME_UNKNOWN
        return OUTCOME_PASS if current in wanted else OUTCOME_MISMATCH
    key_of = _country_match_key if dimension == "countries" else _language_match_key
    wanted = {key_of(item) for item in requested or []}
    wanted.discard("")
    return tri_state_outcome(key_of(raw_value), wanted, mode)


def recall_leg_outcome(
    dimension: str,
    raw_value: Any,
    *,
    requested: Iterable[Any],
    mode: Any = "require",
    sql_values: frozenset[str] | None = None,
    verdict: str | None = None,
) -> str:
    """**搜索真会怎么处置这个人**:取数腿与逐人判定两道一起算。

    * ``""`` —— 两道都过,搜索确实能给出这个人;
    * ``unknown`` —— 资料没填,两道都挡(补数据能救);
    * ``mismatch`` —— 确认不符(补数据救不了);
    * ``unrecallable`` —— 判定放行了,但取数腿够不着 —— 「含未知」档、
      「排除」档、以及库里写 ``United States`` 而操作员点的是 ``US`` 这类同义词落差,
      全部落在这一档。这个数**绝不能**当成放宽后能回来的人。
    """
    values = recall_sql_values(dimension, requested) if sql_values is None else sql_values
    if not values:
        return OUTCOME_PASS
    outcome = verdict_outcome(dimension, raw_value, requested, mode) if verdict is None else verdict
    if outcome:
        return outcome
    return OUTCOME_PASS if sql_admits(dimension, raw_value, values, mode) else OUTCOME_UNRECALLABLE


def mode_is_recallable(dimension: str, mode: Any) -> bool:
    """这个模式今天的搜索兑现得了吗。

    ``require`` 兑现得了(取数腿与判定同向);``include_unknown`` / ``exclude``
    兑现不了 —— 取数腿不认模式,前者取不到没填的人,后者只取点名的人再被判定全排掉。

    **语言(2026-08-26)与国家(同日,国家腿同病同修)都已不是这样**:两条腿都改走
    共用构造器(``language_sql_filter`` / ``country_sql_filter``),三态全部下推,
    ``exclude`` 直接不下推交给逐人判定。三档都兑现得了,不再登记为不可兑现。
    **平台**还是老形态(SQL 里就没有模式这回事),照旧只有 ``require`` 兑现得了。
    """
    if dimension not in SQL_FILTERED_DIMENSIONS:
        return True
    if dimension in {"countries", "languages"}:
        return True
    return normalize_mode(mode) == "require"


__all__ = [
    "OUTCOME_UNRECALLABLE",
    "RECALL_SQL_PINS",
    "SQL_FILTERED_DIMENSIONS",
    "mode_is_recallable",
    "recall_leg_outcome",
    "recall_sql_values",
    "sql_admits",
    "verdict_outcome",
]
