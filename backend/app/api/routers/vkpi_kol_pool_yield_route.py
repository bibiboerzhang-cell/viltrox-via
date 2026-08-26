"""产量预估只读端点:搜之前先问「这个组合能出几个人」(2026-08-26)。

纯 SELECT / COUNT。**不调 provider、不调 LLM、不入队、不写库**,因此登记进
``release_validation`` 的只读 GET 白名单 —— 发布验证期间照常可用。

门面契约(红线 4「必须如实告知」的落点,前端按名取):

* ``estimated`` —— 库内可选人数;``scope_note`` 必须原样显示,别让操作员以为
  这个数已经算上了联网还能找回来的人。
* ``ladder`` —— 逐条加刀的阶梯,每一级都带「这一刀砍掉的人里有多少只是**没填**」。
* ``drop_one`` —— 逐条松绑各能回来多少人,已按回来的人数从多到少排好。
  自动放宽只许采信这张表,**不许**让模型自己猜产量。
* ``tri_state`` —— 每一维的「合格 / 未知 / 明确不符」三档。
* ``not_estimated`` / ``degraded`` —— 这次没估的东西与降级情况,一个都不许藏。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains.kol.pool_yield_estimate import estimate_pool_yield


router = APIRouter(tags=["vkpi-kol-pool"])
logger = get_logger(__name__)

#: 单个多值参数最多认多少项,挡住超长 URL。
_MAX_VALUES = 40


def _values(raw: list[str] | None) -> list[str]:
    """既认重复参数(``?countries=US&countries=JP``)也认逗号串(``?countries=US,JP``)。"""
    out: list[str] = []
    for chunk in raw or []:
        for part in str(chunk or "").split(","):
            text = " ".join(part.split()).strip()
            if text and text not in out:
                out.append(text)
            if len(out) >= _MAX_VALUES:
                return out
    return out


def _tri_state(raw: list[str] | None, mode: str) -> Any:
    values = _values(raw)
    if not values:
        return None
    return {"values": values, "mode": mode}


@router.get("/kol-pool/yield-estimate")
def estimate_kol_pool_yield(
    countries: list[str] = Query(default=[]),
    languages: list[str] = Query(default=[]),
    platforms: list[str] = Query(default=[]),
    verticals: list[str] = Query(default=[]),
    countries_mode: str = Query(default="require", max_length=32),
    languages_mode: str = Query(default="require", max_length=32),
    followers_min: int | None = Query(default=None, ge=0),
    followers_max: int | None = Query(default=None, ge=0),
    gear_content: str = Query(default="any", max_length=16),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """库内产量预估。零成本只读:一条 GROUP BY COUNT 起步,勾了内容方向才多取两张表。"""
    del staff
    filters: dict[str, Any] = {}
    for key, raw, mode in (
        ("countries", countries, countries_mode),
        ("languages", languages, languages_mode),
    ):
        payload = _tri_state(raw, mode)
        if payload is not None:
            filters[key] = payload
    for key, raw in (("platforms", platforms), ("verticals", verticals)):
        values = _values(raw)
        if values:
            filters[key] = values
    if followers_min is not None:
        filters["followers_min"] = followers_min
    if followers_max is not None:
        filters["followers_max"] = followers_max
    if str(gear_content or "").strip().lower() not in ("", "any"):
        filters["gear_content"] = gear_content
    try:
        result = estimate_pool_yield(filters)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("产量预估失败 filters=%s", filters)
        raise HTTPException(
            status_code=503,
            detail="产量预估暂时算不出来;这不代表没有人,请稍后重试。",
        ) from exc
    result["provider_calls"] = False
    result["write_db"] = False
    result["execution_mode"] = "provider_free_estimate"
    return result


__all__ = ["estimate_kol_pool_yield", "router"]
