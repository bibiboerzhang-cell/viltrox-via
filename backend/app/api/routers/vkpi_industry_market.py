"""Market intelligence routes for V-KPI industry automation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains import market as market_domain
from app.services.vkpi import (
    market_intelligence_cards,
    market_intelligence_v0,
)


router = APIRouter()


def _is_manager_staff(staff: dict) -> bool:
    role = str(staff.get("role") or "").strip().lower()
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def _require_manager_staff(staff: dict) -> None:
    if not _is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


@router.get("/industry-data/market-intelligence/v0")
def industry_market_intelligence_v0(
    limit: int = Query(default=120, ge=1, le=500),
    run_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return market_intelligence_v0.build_market_intelligence_v0(limit=limit, run_id=run_id)


@router.get("/industry-data/market-intelligence/cards/v0")
def industry_market_intelligence_cards_v0(
    limit: int = Query(default=120, ge=1, le=500),
    run_id: int | None = Query(default=None, ge=1),
    brand_limit: int = Query(default=5, ge=1, le=10),
    include_latest_llm_artifact: bool = Query(default=True),
    include_latest_external_smoke: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    resolved_run_id = run_id if run_id is not None else market_intelligence_cards.latest_reviewed_market_run_id()
    market_report = market_intelligence_v0.build_market_intelligence_v0(limit=limit, run_id=resolved_run_id)
    llm_report = market_intelligence_cards.latest_usable_market_llm_report() if include_latest_llm_artifact else None
    external_report = market_intelligence_cards.latest_external_signal_smoke_report() if include_latest_external_smoke else None
    payload = market_intelligence_cards.build_market_intelligence_cards(
        market_report,
        llm_report=llm_report,
        external_smoke_report=external_report,
        brand_limit=brand_limit,
    )
    payload.setdefault("summary", {})["source_scope_auto_resolved"] = run_id is None and resolved_run_id is not None
    return payload


@router.get("/industry-data/market-external-signal-smoke/v0")
def industry_market_external_signal_smoke_v0(
    execute_http_fetch: bool = Query(default=False),
    source_key: str = Query(default=""),
    source_group: str = Query(default=""),
    limit_per_source: int = Query(default=5, ge=1, le=25),
    staff=Depends(require_tab("vkpi", "read")),
):
    if execute_http_fetch:
        _require_manager_staff(staff)
    return market_domain.build_external_signal_smoke(
        execute_http_fetch=execute_http_fetch,
        source_key=source_key,
        source_group=source_group,
        limit_per_source=limit_per_source,
    )


@router.get("/industry-data/market-external-source-matrix/v0")
def industry_market_external_source_matrix_v0(
    source_group: str = Query(default=""),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return market_domain.build_external_source_matrix(source_group=source_group)


@router.get("/industry-data/market-external-daily-plan/v0")
def industry_market_external_daily_plan_v0(
    source_group: str = Query(default=""),
    max_http_calls: int = Query(default=6, ge=1, le=20),
    limit_per_source: int = Query(default=5, ge=1, le=25),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return market_domain.build_external_daily_candidate_plan(
        source_group=source_group,
        max_http_calls=max_http_calls,
        limit_per_source=limit_per_source,
    )


@router.get("/industry-data/market-provider-preflight")
def industry_market_provider_preflight(
    preferred_provider: str = Query(default="google", pattern="^(google|openai|anthropic)$"),
    max_output_tokens: int = Query(default=160, ge=32, le=1000),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return market_domain.build_provider_preflight(
        preferred_llm_provider=preferred_provider,
        max_output_tokens=max_output_tokens,
    )
