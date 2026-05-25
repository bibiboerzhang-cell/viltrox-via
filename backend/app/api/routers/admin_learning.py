"""Admin product-learning correction routes."""
from __future__ import annotations

from app.api.routers.admin_common import *

router = APIRouter(tags=["admin"])

@router.get("/api/admin/product_catalog")
def get_product_catalog(request: Request):
    """返回完整产品目录，前端纠正下拉框用"""
    require_admin(request)
    from app.core.constants import PRODUCT_RULES

    def _build():
        catalog = []
        seen = set()
        for item in PRODUCT_RULES:
            key = f"{item['series']}|{item['label']}"
            if key not in seen:
                seen.add(key)
                catalog.append({
                    "series": item["series"],
                    "label": item["label"],
                })
        try:
            from app.db.repositories.knowledge import list_product_knowledge_rules
            for item in list_product_knowledge_rules(limit=500):
                key = f"{item['series']}|{item['label']}"
                if key not in seen:
                    seen.add(key)
                    catalog.append({
                        "series": item["series"],
                        "label": item["label"],
                    })
        except Exception:
            logger.warning("admin.product_catalog_rule_load_failed", exc_info=True)
        return {"total": len(catalog), "items": catalog}

    return _admin_cache_get_or_build(
        "product_catalog",
        _build,
        ttl=60,
    )


@router.post("/api/admin/submissions/{submission_id}/correct")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def correct_submission_product(submission_id: int, request: Request):
    """
    管理员手动纠正一条投稿的产品识别。

    Body:
        {
            "correct_series": "DL",
            "correct_label": "AF 90mm F3.5 DL",
            "note": "DJI Inspire 3 + 90mm DL镜头"
        }
    """
    await require_admin_async(request)
    from app.services.audit.learning import record_correction

    body = await request.json()
    correct_series = body.get("correct_series", "").strip()
    correct_label = body.get("correct_label", "").strip()
    note = body.get("note", "").strip()

    if not correct_series or not correct_label:
        return {"status": "error", "message": "Both correct_series and correct_label required"}

    row = await db_read(partial(_load_submission_product_context, submission_id))
    if not row:
        return {"status": "error", "message": "Submission not found"}

    url = row["url"] or ""
    title = row["title"] or ""

    # Build learned text from video_analysis for keyword extraction
    learned_text = title
    try:
        va = json.loads(row["video_analysis"] or "{}")
        learned_text += " " + (va.get("notes") or "")
        learned_text += " " + (va.get("camera_body") or "")
        learned_text += " " + (va.get("viltrox_lens") or "")
        learned_text += " " + " ".join(va.get("brand_elements") or [])
        learned_text += " " + " ".join(va.get("products_detected") or [])
        learned_text += " " + " ".join(va.get("viltrox_products_all") or [])
    except Exception:
        logger.warning("admin.correct_submission_video_analysis_parse_failed", extra={"submission_id": submission_id}, exc_info=True)

    # Update the submission immediately
    await db_write(partial(_update_submission_product, submission_id, correct_series, correct_label, note))

    # Record the correction for future learning
    result = await asyncio.to_thread(
        record_correction,
        submission_id=submission_id,
        url=url,
        correct_series=correct_series,
        correct_label=correct_label,
        learned_text=learned_text,
        note=note,
    )
    _invalidate_admin_cache()

    return {
        "status": "success",
        "submission_id": submission_id,
        "correct_series": correct_series,
        "correct_label": correct_label,
        "learning": result,
    }


@router.get("/api/admin/learning/stats")
def learning_stats(request: Request):
    """学习系统统计"""
    require_admin(request)
    from app.services.audit.learning import get_correction_stats
    return get_correction_stats()


@router.get("/api/admin/learning/corrections")
def list_corrections(request: Request, limit: int = Query(default=100, le=500)):
    """列出所有学习记录"""
    require_admin(request)
    from app.services.audit.learning import list_all_corrections
    return {"items": list_all_corrections(limit)}


@router.delete("/api/admin/learning/corrections")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def delete_correction_endpoint(request: Request):
    """删除某个 URL 的学习记录"""
    await require_admin_async(request)
    from app.services.audit.learning import delete_correction
    body = await request.json()
    url = body.get("url", "")
    if not url:
        return {"status": "error", "message": "url required"}
    deleted = await asyncio.to_thread(delete_correction, url)
    if deleted:
        _invalidate_admin_cache()
    return {"status": "ok" if deleted else "not_found", "deleted": deleted}
