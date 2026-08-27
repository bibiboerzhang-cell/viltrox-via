"""库内召回国家硬筛的**取数腿口径**(2026-08-26)。

## 病根:闸归一化、取数腿只认原值

国家这一维上,闸(``profile_recall_projection._candidate_filter_verdict``)判的是
**归一化后的国家码** —— ``USA`` / ``U.S.`` / ``America`` / ``United States`` / ``美国``
一律先经 :func:`country_match_key` 收成 ``US`` 再比。而取数腿(线上唯一真正把人捞
出来的那条 SQL)写的是::

    LOWER(COALESCE(p.country, '')) IN (原值小写, 国家码小写)

两套口径。于是操作员点「美国」(门面按规则给出的筛选值是国家码 ``US``),
取数腿绑进 SQL 的字面量就只有 ``'us'`` —— 库里 324 个 ``country='美国'`` 的人
**在闸之前**就被剔掉了,闸再正确也见不到他们。这与语言腿刚治好的是**同一种病**:
判定层认得的写法,取数层认不得。

## 治法:照抄语言腿 —— 下推「归一化后可能匹配」的并集

对每个被点名的国家码,把**所有会归一到这个码的写法**(别名表反查 + 规范名 + 码本身)
一次性下推成一个 ``IN`` 集合。这可以证明**不比闸更严**:闸放行一个人,当且仅当
``country_match_key(p.country)`` 落在被点名的码集里;而任何满足这一条的写法,按
:func:`country_match_key` 的四条分支(别名命中 / 是已知码 / 短码原样 / 长文本原样)
逐条对照,都一定出现在 :func:`country_sql_values` 产出的集合里。见
``tests/test_kol_recall_country_wiring.py::test_recall_leg_is_a_superset_of_the_gate``,
那条测试拿真 sqlite 把「闸 ⊆ 取数腿」逐行跑出来,不是字符串比对。

顺带把国家腿上与语言腿一模一样的两个既有 bug 一并治了(两者都是**放宽方向**,
不动任何质量标准):

* ``include_unknown`` —— 取数腿此前完全无视模式,``country`` 没填的人恒等于空串,
  **任何模式下**都不在 ``IN`` 集合里,于是「含未知」那一格的增益结构性恒为 0;
* ``exclude`` —— 取数腿照样只捞被点名的国家,随后闸把他们**全部**排掉,结果恒为 0。
  正解与语言腿一致:负向筛选**不下推**,交给闸逐人判。

## red line

* 本模块**纯函数**:不读库、不写库、不推断、不碰 ``viltrox_fit_score``。
* **一条质量标准都不动**:新鲜度天数、器材证据、证据词数、粉丝下限、产品锚、
  检测器阈值全部与本模块无关,这里只把「他是哪国人」这个**比对口径**对齐。
* 三态语义不变:``require`` 缺省,未知照旧被 ``require`` 拦下 —— 取数腿变宽
  **不等于**闸放行,谁该被拦还是被拦。
* SQL 兼容(与语言腿同源):占位符 ``?``、**零字面百分号、零 ``LIKE``**,
  只用 ``LOWER`` / ``COALESCE`` / ``REPLACE`` 三个两方言同名同义的函数。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from app.core.logging import get_logger
from app.domains.kol.pool_common import COUNTRY_CODE_ALIASES, COUNTRY_NAMES

logger = get_logger("viltrox.kol.recall.country")

#: 取数腿默认落在哪一列上。两条腿(词法腿 / 广度兜底腿)都写 ``p.country``。
COUNTRY_COLUMN = "p.country"

#: SQL 侧要抹掉的空白字符。闸那边用 ``" ".join(text.split())`` **折叠**空白;
#: SQL 这边**全抹**(``REPLACE`` 逐字符去掉)。全抹是比折叠更粗的归一化 ——
#: 折叠后相等的两个串,全抹后必然也相等,反之不必然。所以这个方向只会让取数腿
#: **更宽**,超集关系不受影响。(``TRIM`` 只管首尾,内部的双空格治不了,故不用它。)
_SQUEEZED_WHITESPACE: tuple[str, ...] = (" ", "\t", "\n", "\r")


def country_match_key(value: Any) -> str:
    """国家硬筛的**唯一**归一化口径:``USA`` / ``美国`` / ``America`` → ``US``。

    实现自 ``profile_recall_projection._country_match_key`` **原样搬来**(逐字节,
    含 fallback 与静默降级路径),那边现在转调本函数 —— 闸与取数腿共用同一把尺子,
    第二把尺子迟早会漂,不许再有第二把。
    """
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    try:
        from app.domains.kol.pool_common import _country_code

        code = str(_country_code(text) or "").strip().upper()
        if code:
            return code
    except Exception:
        logger.debug("country filter normalization fallback", exc_info=True)
    return re.sub(r"[^a-z0-9一-鿿]", "", text.lower())


def country_sql_value(value: Any) -> str:
    """一个库内取值在 SQL 侧会被算成什么(抹空白 + 小写)。

    与 :func:`country_sql_expression` 生成的表达式**逐字同义** —— 预估层
    (``pool_yield_recall_parity``)要判断「这个人取数腿捞不捞得到」时按这个算,
    才不会两边各写一套。
    """
    text = str(value or "").lower()
    for char in _SQUEEZED_WHITESPACE:
        text = text.replace(char, "")
    return text


def country_match_keys(values: Iterable[Any]) -> list[str]:
    """操作员点名的那组国家 → 去重后的国家码。与闸里的 ``requested`` 集合同源。"""
    keys = {country_match_key(value) for value in values or []}
    keys.discard("")
    return sorted(keys)


def country_sql_values(values: Iterable[Any]) -> list[str]:
    """下推给 SQL 的字面量:**所有会归一到被点名国家码的写法**(去空白 + 小写)。

    对一个国家码 ``K``,闭包 = ``{别名表里 code == K 的全部别名}`` ∪
    ``{COUNTRY_NAMES[K] 规范名}`` ∪ ``{K 自己}``。这三样穷尽了
    :func:`country_match_key` 会判出 ``K`` 的全部输入形态:

    * 输入命中别名表 —— 落在第一项;
    * 输入是已知国家码(``_country_code`` 走 ``COUNTRY_NAMES`` 分支)—— 落在第三项;
    * 输入是三字符以内的未知码 —— ``K`` 就是它的大写,落在第三项;
    * 输入是四字符以上的未知国名 —— ``K`` 就是它的大写,同样落在第三项。

    因此「闸放行 ⇒ 取数腿捞得到」在**任意**库内取值上成立,不依赖数据长什么样。
    """
    out: set[str] = set()
    for key in country_match_keys(values):
        out.add(country_sql_value(key))
        out.add(country_sql_value(COUNTRY_NAMES.get(key, "")))
        for alias, code in COUNTRY_CODE_ALIASES.items():
            if code == key:
                out.add(country_sql_value(alias))
    out.discard("")
    return sorted(out)


def country_sql_expression(column: str = COUNTRY_COLUMN) -> str:
    """SQL 侧的取值口径:抹空白 + 转小写。零字面百分号、零 ``LIKE``。"""
    expr = f"LOWER(COALESCE({column}, ''))"
    for char in _SQUEEZED_WHITESPACE:
        expr = f"REPLACE({expr}, '{char}', '')"
    return expr


def country_sql_filter(
    values: Iterable[Any],
    *,
    mode: Any = "require",
    column: str = COUNTRY_COLUMN,
) -> tuple[str, list[Any]]:
    """取数腿的国家下推。返回 ``(where 片段, 参数)``;空串 = 这一腿不下推国家。

    三态口径与 ``profile_recall_filter_modes.tri_state_outcome`` 逐条对齐:

    * ``require``        —— 归一化后命中被点名的任一国家码;
    * ``include_unknown`` —— 命中,**或** ``country`` 整个是空/纯空白(= 闸眼里的「未知」);
    * ``exclude``        —— **不下推**。负向筛选下推正向匹配,等于把操作员点名要排除的人
      原样捞回来再被闸全排掉,结果恒为 0(与语言腿此前同一个 bug)。

    **为什么不干脆「SQL 层不筛国家」**:取数腿是带 ``LIMIT`` 的有限候选生成器,
    删掉条件不等于交给闸判,而是让固定的行预算被注定被闸驳回的人占满,合格的人
    反而被挤出窗口 —— 那是拿一种静默丢人换另一种(理由与语言腿逐字同源)。
    """
    literals = country_sql_values(values)
    if not literals:
        return "", []
    if str(mode or "require").strip().lower() == "exclude":
        return "", []
    expr = country_sql_expression(column)
    sql = f"{expr} IN (" + ",".join("?" for _ in literals) + ")"
    params: list[Any] = list(literals)
    if str(mode or "require").strip().lower() == "include_unknown":
        sql = f"({sql} OR {expr} = '')"
    else:
        sql = f"({sql})"
    return sql, params


def country_hard_filter(filters: Any, column: str = COUNTRY_COLUMN) -> tuple[str, list[Any]]:
    """两条取数腿共用的入口:从筛选字典直接算出国家下推片段。

    取值优先 ``countries``(操作员点名的原值);``_country_values``
    (``recall_kol_profiles`` 预先绑好的「原值小写 ∪ 国家码小写」)作兜底 ——
    两者归一化后得到的国家码集合相同,谁在都算得对。
    """
    source = filters if isinstance(filters, dict) else {}
    values = source.get("countries") or source.get("_country_values") or []
    if not values:
        return "", []
    return country_sql_filter(values, mode=source.get("countries_mode"), column=column)


__all__ = [
    "COUNTRY_COLUMN",
    "country_hard_filter",
    "country_match_key",
    "country_match_keys",
    "country_sql_expression",
    "country_sql_filter",
    "country_sql_value",
    "country_sql_values",
]
