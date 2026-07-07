"""A4 问数页 · 预设问题库路由(加性新文件)。

- GET  /api/admin/vkpi/canned-queries
      → 12 个常用问题清单(key/标题/说明/列/是否吃区间/来源表)。
- POST /api/admin/vkpi/canned-queries/{key}/run  {range?}
      → 执行确定性 SQL 聚合,回传 {columns, rows, row_count, source_tables,
        summary, sql_explain, range_days, generated_at}。

零 LLM:问题→SQL 映射硬编码在 app.domains.analytics.canned_queries,摘要句
由 Python 拼装。权限与既有问数链路一致(require_tab("vkpi", "admin"))。
红线:纯读,零触 viltrox_fit_score、不碰 rule_v0。

【不改 main.py】注册行见任务回报 collect_anchors。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.analytics import canned_queries

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-canned-queries"])


@router.get("/canned-queries")
def list_canned_queries(staff=Depends(require_tab("vkpi", "admin"))) -> dict[str, Any]:
    """预设问题清单:12 个常用问题及其列/来源表说明。"""
    del staff
    return {"questions": canned_queries.list_questions()}


@router.post("/canned-queries/{key}/run")
def run_canned_query(
    key: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "admin")),
) -> dict[str, Any]:
    """执行一个预设问题(确定性 SQL 聚合,零 LLM),出数带来源。

    请求体:
      - range: 区间天数(可选,默认 30,夹取 1..365;仅 uses_range 的问题生效)
    """
    del staff
    range_days = payload.get("range", canned_queries.DEFAULT_RANGE_DAYS)
    try:
        return canned_queries.run(get_conn(), key, range_days=range_days)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # 安全断言失败 → 400(绝不把内部异常细节外泄成 500)。
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 单问失败不炸接口,诚实回原因
        logger.warning("canned query failed key=%s: %s", key, exc)
        return {
            "key": key,
            "title": "",
            "columns": [],
            "rows": [],
            "row_count": 0,
            "source_tables": [],
            "summary": "",
            "sql_explain": "",
            "range_days": None,
            "generated_at": "",
            "status": "error",
            "reason": str(exc)[:300],
        }
