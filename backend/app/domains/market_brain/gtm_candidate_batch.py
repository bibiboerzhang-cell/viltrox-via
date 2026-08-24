"""Batched read adapter for GTM preview candidate enrichment.

The scoring and forecast algorithms stay in ``strategy_sim``, ``rate_card``
and ``performance_forecast``. This adapter only loads each candidate set once
and presents the existing single-item interfaces back to ``strategy_sim``.
"""
from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


def _candidate_ids(pool_items: list[dict[str, Any]]) -> list[int]:
    ids: list[int] = []
    for item in pool_items:
        try:
            kol_pool_id = int(item.get("kol_pool_id"))
        except (TypeError, ValueError):
            continue
        if kol_pool_id > 0 and kol_pool_id not in ids:
            ids.append(kol_pool_id)
    return ids


def build_candidates(
    sku: str,
    pool_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Any, str]:
    """Return the original strategy candidates with batched source reads."""
    from app.db.connection import get_conn
    from app.domains.market import strategy_sim

    rate_card, forecast_mod, roster_mod, engines_missing = strategy_sim._load_engines()
    db = get_conn()
    ids = _candidate_ids(pool_items)
    rate_estimates: dict[int, dict[str, Any]] = {}
    forecasts: dict[int, dict[str, Any]] = {}
    if rate_card is not None and hasattr(rate_card, "estimate_rates"):
        try:
            rate_estimates = rate_card.estimate_rates(ids, conn=db)
        except Exception as exc:  # noqa: BLE001 - adapter falls back per candidate
            logger.warning("gtm candidate rate batch unavailable: %s", exc)
    if forecast_mod is not None and hasattr(forecast_mod, "forecast_for_kols"):
        try:
            forecasts = forecast_mod.forecast_for_kols(
                ids,
                sku=sku,
                conn=db,
                context="sim",
                dry_run=True,
            )
        except Exception as exc:  # noqa: BLE001 - adapter falls back per candidate
            logger.warning("gtm candidate forecast batch unavailable: %s", exc)

    class _RateAdapter:
        @staticmethod
        def _tier_for_followers(followers: int) -> str:
            return rate_card._tier_for_followers(followers)

        @staticmethod
        def estimate_rate(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
            cached = rate_estimates.get(int(kol_pool_id))
            return cached if cached is not None else rate_card.estimate_rate(kol_pool_id, conn=conn)

    class _ForecastAdapter:
        @staticmethod
        def forecast_for_kol(
            kol_pool_id: int,
            sku: str | None = None,
            *,
            conn: Any = None,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            cached = forecasts.get(int(kol_pool_id))
            if cached is not None:
                return cached
            return forecast_mod.forecast_for_kol(
                int(kol_pool_id), sku=sku, conn=conn, context="sim", dry_run=True,
            )

    candidates = strategy_sim._build_candidates(
        sku,
        pool_items,
        _RateAdapter if rate_card is not None else None,
        _ForecastAdapter if forecast_mod is not None else None,
        db,
    )
    return candidates, roster_mod, engines_missing


__all__ = ["build_candidates"]
