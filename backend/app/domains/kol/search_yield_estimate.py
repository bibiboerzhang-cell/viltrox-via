"""自动放宽车道的取数口:一次筛选组合 -> 三态人数(2026-08-26)。

``search_auto_relax`` 按名字懒加载本模块的 :func:`estimate_yield`,拿它决定「够不够
30 个人」以及「松哪一刀」。真正的算法全在 :mod:`app.domains.kol.pool_yield_estimate`,
这里只是把它的完整回执收窄成放宽车道要的四个数,**不重算、不另立口径**。

红线复述(两条都由被代理的模块保证,不在这里放水):

* **零成本**:纯 SELECT / COUNT。不调 provider、不调 LLM、不写库。
* **模型只提议,数据库定夺**:``qualified`` 是 COUNT 数出来的,不是谁猜的。

``unknown`` 与 ``mismatch`` 必须分开看:``unknown`` 是「挡住他的全是资料没填」,
``mismatch`` 是「至少有一维确认不符」。把两者混成一个数正是今天误杀一大片人的根因。

**但 ``unknown`` 不是「切到含未知档就能回来的人」**(2026-08-26 更正):库内取数腿
只按填了的值取人,没填的人**任何模式下都取不回来**。所以本口子透出的
``unknown_recoverable_by_mode`` 恒为 ``False``,放宽车道走 ``include_unknown``
那一格时拿到的 ``after`` 会与 ``before`` 一样 —— 这不是估错了,是那一格今天真的
放不出人来,车道应当照实记账然后走下一格。真能兑现的增益只在「整条去掉某一维」
(``drop_one``)那张表里。缘由与代价见 ``pool_yield_recall_parity`` 模块头。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from app.domains.kol.pool_yield_estimate import estimate_pool_yield


def estimate_yield(
    filters: Mapping[str, Any] | None = None,
    *,
    get_connection: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """返回 ``{qualified, unknown, unrecallable, mismatch, pool_total, ...}``,并原样带上明细。

    ``ladder`` / ``drop_one`` / ``tri_state`` / ``cost`` 一并透出,好让放宽车道把
    「松了哪一项、松之前多少人、松之后多少人」如实写进给操作员看的台账。
    ``estimate_basis`` / ``headline_note`` / ``not_estimated`` 也一并透出 —— 这个数是
    **硬筛后的可选面**,不是最终能拿到几个人,车道的文案不许说成后者。
    """
    result = estimate_pool_yield(dict(filters or {}), get_connection=get_connection)
    totals = dict(result.get("totals") or {})
    totals.update(
        {
            "count": result.get("estimated", 0),
            "scope": result.get("scope"),
            "scope_note": result.get("scope_note"),
            "estimate_basis": result.get("estimate_basis"),
            "headline_note": result.get("headline_note"),
            "applied": result.get("applied", []),
            "ladder": result.get("ladder", []),
            "drop_one": result.get("drop_one", []),
            "tri_state": result.get("tri_state", []),
            "not_estimated": result.get("not_estimated", []),
            "degraded": result.get("degraded", []),
            "cost": result.get("cost", {}),
        }
    )
    return totals


#: 放宽车道也认这个名字(两条车道各自独立命名,这里两个都给,免得对不上号)。
estimate_pool_yield_totals = estimate_yield

__all__ = ["estimate_pool_yield_totals", "estimate_yield"]
