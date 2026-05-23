"""V-KPI industry data, audience graph, and automation routes."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import (
    ab_experiments,
    audience_graph,
    brand_signal_detector,
    competitor_brain,
    content_brain,
    industry_data,
    llm_gateway,
    market_intelligence_v0,
    outcome_collector,
    training_data_export,
)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-industry-automation"])


def _is_manager_staff(staff: dict) -> bool:
    role = str(staff.get("role") or "").strip().lower()
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def _require_manager_staff(staff: dict) -> None:
    if not _is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


@router.get("/industry-data/projects")
def industry_projects(
    limit: int = Query(default=100, ge=1, le=300),
    active_only: bool = True,
    staff=Depends(require_tab("vkpi", "read")),
):
    return industry_data.list_projects(limit=limit, active_only=active_only)


@router.post("/industry-data/projects")
def industry_create_project(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return industry_data.create_project(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/industry-data/projects/{project_id}")
def industry_get_project(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return industry_data.get_project(project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/industry-data/projects/{project_id}")
def industry_delete_project(project_id: int, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    return industry_data.delete_project(project_id, staff=staff)


@router.get("/industry-data/projects/{project_id}/accounts")
def industry_accounts(project_id: int, limit: int = Query(default=300, ge=1, le=1000), staff=Depends(require_tab("vkpi", "read"))):
    return industry_data.list_accounts(project_id=project_id, limit=limit)


@router.post("/industry-data/projects/{project_id}/accounts")
def industry_add_account(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return industry_data.add_account(project_id, body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/industry-data/projects/{project_id}/accounts/import")
def industry_import_accounts(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    items = body.get("items") if isinstance(body.get("items"), list) else []
    return industry_data.import_accounts(project_id, items, staff=staff)


@router.post("/industry-data/projects/{project_id}/apify/import")
def industry_import_apify_history(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    items = body.get("items") if isinstance(body.get("items"), list) else []
    return industry_data.import_historical_dataset(
        project_id,
        items,
        source_type=str(body.get("source_type") or "apify_json"),
        source_ref=str(body.get("source_ref") or ""),
        staff=staff,
    )


@router.get("/industry-data/accounts/{account_id}")
def industry_get_account(
    account_id: int,
    limit: int = Query(default=500, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return industry_data.get_account(account_id, post_limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/industry-data/accounts/{account_id}")
def industry_update_account(account_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return industry_data.update_account(account_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/industry-data/accounts/{account_id}/refresh")
def industry_refresh_account(account_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return industry_data.refresh_account(account_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/industry-data/projects/{project_id}/cross-platform")
def industry_cross_platform(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    return industry_data.cross_platform(project_id)


@router.get("/industry-data/projects/{project_id}/posts")
def industry_posts(project_id: int, limit: int = Query(default=100, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    return industry_data.posts(project_id, limit=limit)


@router.get("/industry-data/content-brain/status")
def industry_content_brain_status(staff=Depends(require_tab("vkpi", "read"))):
    del staff
    return content_brain.get_content_brain_status()


@router.get("/industry-data/content-brain/posts")
def industry_content_brain_posts(
    status: str = "",
    platform: str = "",
    query: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return content_brain.list_content_brain_posts(status=status, platform=platform, query=query, limit=limit)


@router.get("/industry-data/competitor-brain/status")
def industry_competitor_brain_status(staff=Depends(require_tab("vkpi", "read"))):
    del staff
    return competitor_brain.get_competitor_brain_status()


@router.get("/industry-data/competitor-brain/signals")
def industry_competitor_brain_signals(
    review_status: str = "",
    brand: str = "",
    signal_type: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return competitor_brain.list_competitor_signals(
        review_status=review_status,
        brand=brand,
        signal_type=signal_type,
        limit=limit,
    )


@router.get("/industry-data/competitor-brain/review-suggestions")
def industry_competitor_brain_review_suggestions(
    review_status: str = "pending_review",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return competitor_brain.build_competitor_signal_review_suggestions(
        review_status=review_status,
        limit=limit,
    )


@router.post("/industry-data/competitor-brain/review-suggestions/apply")
def industry_competitor_brain_apply_review_suggestions(body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    payload = body or {}
    try:
        return competitor_brain.apply_competitor_signal_review_suggestions(
            review_status=str(payload.get("review_status") or "pending_review"),
            suggested_action=str(payload.get("suggested_action") or "ready"),
            limit=int(payload.get("limit") or 100),
            staff=staff,
            dry_run=not bool(payload.get("confirm")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/industry-data/competitor-brain/signals/{signal_id}/review")
def industry_competitor_brain_review_signal(signal_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return competitor_brain.review_competitor_signal(
            signal_id,
            action=str(body.get("action") or ""),
            note=str(body.get("note") or body.get("reason") or ""),
            staff=staff,
            dry_run=False,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/industry-data/market-intelligence/v0")
def industry_market_intelligence_v0(
    limit: int = Query(default=120, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return market_intelligence_v0.build_market_intelligence_v0(limit=limit)


@router.get("/brand-signals/preview")
def brand_signals_preview(
    source: str = Query(default="all"),
    since: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return brand_signal_detector.scan_cached_brand_signals(
        source=source,
        since=since,
        limit=limit,
        write_db=False,
    )


@router.get("/brand-signals")
def brand_signals_list(
    status: str = Query(default="new"),
    signal_type: str = Query(default=""),
    brand_role: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return brand_signal_detector.list_brand_signals(
        status=status,
        signal_type=signal_type,
        brand_role=brand_role,
        limit=limit,
    )


@router.post("/brand-signals/scan")
def brand_signals_scan(body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    return brand_signal_detector.scan_cached_brand_signals(
        source=str(body.get("source") or "all"),
        since=str(body.get("since") or ""),
        limit=int(body.get("limit") or 200),
        write_db=bool(body.get("write_db")),
    )


@router.post("/brand-signals/{signal_id}/action")
def brand_signal_action(signal_id: int, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    try:
        return brand_signal_detector.review_brand_signal(
            signal_id,
            action=str(body.get("action") or ""),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/audience-graph/status")
def audience_graph_status(staff=Depends(require_tab("vkpi", "read"))):
    return audience_graph.status()


@router.post("/audience-graph/estimate")
def audience_graph_estimate(body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    return audience_graph.estimate(body or {})


@router.get("/automation/outcomes/{recommendation_id}")
def automation_outcome(recommendation_id: int, staff=Depends(require_tab("vkpi", "read"))):
    return outcome_collector.get_outcome(recommendation_id)


@router.get("/automation/experiments")
def automation_experiments(limit: int = Query(default=100, ge=1, le=300), staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return ab_experiments.list_experiments(limit=limit)


@router.post("/automation/experiments")
def automation_create_experiment(body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return ab_experiments.create_experiment(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/automation/experiments/{experiment_id}/status")
def automation_experiment_status(experiment_id: int, body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return ab_experiments.update_status(experiment_id, str(body.get("status") or ""), staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/automation/models")
def automation_models(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return ab_experiments.models()


@router.post("/automation/models/{model_version}/activate")
def automation_activate_model(model_version: str, staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return ab_experiments.activate_model(model_version, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/automation/llm-stats")
def automation_llm_stats(limit: int = Query(default=100, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return llm_gateway.stats(limit=limit)


@router.post("/automation/ml/score")
def automation_ml_score(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    return llm_gateway.score(body.get("features") or {}, str(body.get("model_version") or "latest"), staff=staff)


@router.post("/automation/training-data/export")
def automation_training_export(body: dict | None = None, staff=Depends(require_tab("vkpi", "admin"))):
    payload = body or {}
    return training_data_export.export_training_dataset(str(payload.get("date_from") or ""), str(payload.get("date_to") or ""), staff=staff)


@router.get("/automation/training-data/latest")
def automation_training_latest(limit: int = Query(default=20, ge=1, le=100), staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return training_data_export.latest(limit=limit)
