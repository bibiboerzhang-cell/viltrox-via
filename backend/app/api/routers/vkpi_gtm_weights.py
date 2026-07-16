"""V-KPI GTM 权重回流路由(闭环波 L4)。

- GET /api/admin/vkpi/gtm/weight-changes/preview
  → 纯读预览:带 next_weight_change 的 vkpi_gtm_outcomes 逐条跑
    weight_feedback.apply_weight_change(dry_run=True),展示「会回流什么/为何 hold」。
- GET /api/admin/vkpi/gtm/verdicts/{verdict_id}/context?id_type=inbox|outcome
  → 裁决一屏读数:当时预期(expected_result)vs 三窗实际(window_7d/14d/28d)+
    actual_result + 权重回流预览。前端 VerdictPanel 内嵌用。
    id_type 口径与 L2 decide 端点对齐(缺省 inbox=bet 的 action_inbox id,经
    vkpi_gtm_outcomes.action_inbox_id 桥接列解析;outcome=结果行 id 直查)。

诚实态:vkpi_gtm_outcomes(L2 件,迁移 217)未落地 → {available: False, reason};
行不存在 404;聚合内部异常不 500,回 {status:"error", reason}。
红线:本路由全只读零写库;裁决只能人工 POST(L2 的 decide 端点),此处绝无
自动裁决路径;权重真回流(dry_run=False)带样本闸(样本<5 强制 hold),
且只走既有 recommendation_feedback 生效链;绝不写 viltrox_fit_score、不碰 rule_v0。
compat:SQL 占位符 ?、全文零 percent 字符;jsonb 读回 dict/str 双态容错。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.gtm_scope import legacy_gtm_scope_guard
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-gtm-weights"])

_OUTCOME_TABLE = "vkpi_gtm_outcomes"


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _outcome_out(row: dict[str, Any]) -> dict[str, Any]:
    """gtm_outcomes 行 → 可序列化裁决读数(列漂移容错:全部 .get)。"""
    return {
        "id": row.get("id"),
        "gtm_plan_id": row.get("gtm_plan_id"),
        "product_sku": row.get("product_sku"),
        "market": row.get("market"),
        "segment": row.get("segment"),
        "channel": row.get("channel"),
        "action_type": row.get("action_type"),
        "content_angle": row.get("content_angle"),
        "expected_result": _loads(row.get("expected_result"), {}),
        "actual_result": _loads(row.get("actual_result"), {}),
        "window_7d": _loads(row.get("window_7d"), {}),
        "window_14d": _loads(row.get("window_14d"), {}),
        "window_28d": _loads(row.get("window_28d"), {}),
        "decision": row.get("decision"),
        "lesson": row.get("lesson"),
        "next_weight_change": _loads(row.get("next_weight_change"), None),
        "action_inbox_id": row.get("action_inbox_id"),
        "kol_pool_id": row.get("kol_pool_id"),
        "review_at": _iso(row.get("review_at")),
        "created_at": _iso(row.get("created_at")),
        "decided_at": _iso(row.get("decided_at")),
        "decided_by": row.get("decided_by"),
    }


@router.get("/gtm/weight-changes/preview")
def preview_weight_changes(
    limit: int = Query(default=20, ge=1, le=100, description="最多预览多少条带 next_weight_change 的 outcome"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """权重回流预览:全 dry_run,零写库;逐条展示会回流什么、为何 hold(样本闸/未裁决)。"""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="GTM weight preview")
    if scope_unavailable is not None:
        return {**scope_unavailable, "items": [], "count": 0}
    from app.db.connection import get_conn, table_exists
    from app.domains.market_brain import weight_feedback

    try:
        if not table_exists(_OUTCOME_TABLE):
            return {
                "available": False,
                "reason": "GTM 结果账本(vkpi_gtm_outcomes,迁移 217)尚未落地。",
                "items": [],
                "min_sample": weight_feedback.MIN_SAMPLE,
            }
        rows = get_conn().execute(
            """
            SELECT * FROM vkpi_gtm_outcomes
            WHERE next_weight_change IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            preview = weight_feedback.apply_weight_change(row, dry_run=True)
            if not preview.get("entries"):
                continue  # jsonb 为空对象/空数组:无条目,不占预览位
            items.append({
                "gtm_outcome_id": row.get("id"),
                "gtm_plan_id": row.get("gtm_plan_id"),
                "product_sku": row.get("product_sku"),
                "market": row.get("market"),
                "channel": row.get("channel"),
                "action_type": row.get("action_type"),
                "decision": row.get("decision"),
                "decided_at": _iso(row.get("decided_at")),
                "preview": preview,
            })
        return {
            "available": True,
            "count": len(items),
            "items": items,
            "min_sample": weight_feedback.MIN_SAMPLE,
            "note": "纯预览零写库;真回流只走 recommendation_feedback 既有生效链且带样本闸。",
        }
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("gtm weight-changes preview failed: %s", exc)
        return {"available": False, "status": "error", "reason": str(exc)[:300], "items": []}


@router.get("/gtm/verdicts/{verdict_id}/context")
def get_verdict_context(
    verdict_id: int,
    id_type: str = Query(default="inbox", description="inbox=bet 的 action_inbox id(缺省,与 decide 端点对齐)/ outcome=vkpi_gtm_outcomes.id"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """裁决一屏读数:当时预期 vs 三窗实际(自动回填部分)+ 权重回流预览。全只读。"""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="GTM verdict context")
    if scope_unavailable is not None:
        return scope_unavailable
    from app.db.connection import get_conn, table_exists
    from app.domains.market_brain import weight_feedback

    idt = str(id_type or "inbox").strip().lower()
    if idt not in ("inbox", "outcome"):
        raise HTTPException(status_code=422, detail="id_type must be 'inbox' or 'outcome'")
    if not table_exists(_OUTCOME_TABLE):
        return {
            "available": False,
            "reason": "GTM 结果账本(vkpi_gtm_outcomes,迁移 217)尚未落地。",
        }
    if idt == "outcome":
        row = get_conn().execute(
            "SELECT * FROM vkpi_gtm_outcomes WHERE id = ?",
            (int(verdict_id),),
        ).fetchone()
    else:
        # inbox 口径:经桥接列 action_inbox_id 解析(同一 bet 多行时取最新)。
        row = get_conn().execute(
            """
            SELECT * FROM vkpi_gtm_outcomes
            WHERE action_inbox_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(verdict_id),),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="gtm outcome not found")
    data = dict(row)
    try:
        weight_preview = weight_feedback.apply_weight_change(data, dry_run=True)
    except Exception as exc:  # noqa: BLE001 — 预览失败不阻塞裁决读数
        logger.warning("gtm verdict context weight preview failed id=%s: %s", verdict_id, exc)
        weight_preview = {"ok": False, "reason": str(exc)[:200]}
    return {
        "available": True,
        "outcome": _outcome_out(data),
        "weight_preview": weight_preview,
        "note": "裁决只能人工 POST decide 端点;本读数纯聚合,不参与任何评分。",
    }
