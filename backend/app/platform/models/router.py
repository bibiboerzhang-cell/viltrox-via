"""Model router — pick a model for a skill from thresholds, with a fallback chain.

Selection is a pure, deterministic function of the registry + a ``RouteRequest``
(quality/cost/speed thresholds + weights). The real LLM call is delegated to the
existing ``app.platform.llm_gateway.invoke`` via each model's ``gateway_provider``
— this layer NEVER re-implements provider transport.

Algorithm:
  1. Hard-filter the registry by quality / speed / context floors and cost ceiling.
  2. Score survivors = w_q*quality + w_c*cost + w_s*speed (weights normalised).
  3. Sort by score desc, then quality desc, then cheaper, then stable key —
     fully deterministic. The top is the primary; the rest form the fallback
     chain (best-effort alternatives in the same ranked order).
  4. If nothing passes the hard filters, fall back to the cheapest/most-local
     model so the caller still gets a usable decision (router never raises on
     "no candidate"; it degrades).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from . import cost_policy, quality_policy, registry
from .registry import ModelSpec


from app.core.logging import get_logger
logger = get_logger(__name__)

@dataclass(frozen=True)
class RouteRequest:
    """A skill's routing constraints + axis weights.

    Thresholds are hard floors/ceilings; weights bias the soft ranking among
    candidates that pass. ``allow_local`` / ``deny_models`` / ``prefer_models``
    give skills coarse control without touching the registry.
    """

    skill: str = ""
    min_quality: Optional[float] = None
    max_cost_cents_per_million: Optional[float] = None
    min_speed: Optional[float] = None
    min_context_tokens: Optional[int] = None
    quality_weight: float = 0.5
    cost_weight: float = 0.3
    speed_weight: float = 0.2
    allow_local: bool = True
    prefer_models: tuple[str, ...] = field(default_factory=tuple)
    deny_models: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RouteDecision:
    """The router's choice: primary model + ranked fallback chain + scores."""

    skill: str
    primary: ModelSpec
    fallback_chain: tuple[ModelSpec, ...]
    scores: dict[str, float]
    degraded: bool  # True when no candidate met the hard filters

    @property
    def provider_chain(self) -> list[str]:
        """gateway_provider names in order (primary first) for delegation."""
        chain = [self.primary, *self.fallback_chain]
        seen: set[str] = set()
        out: list[str] = []
        for m in chain:
            if m.gateway_provider not in seen:
                seen.add(m.gateway_provider)
                out.append(m.gateway_provider)
        return out


def _normalised_weights(req: RouteRequest) -> tuple[float, float, float]:
    q = max(0.0, float(req.quality_weight))
    c = max(0.0, float(req.cost_weight))
    s = max(0.0, float(req.speed_weight))
    total = q + c + s
    if total <= 0:
        return (1.0, 0.0, 0.0)
    return (q / total, c / total, s / total)


def _passes_hard_filters(model: ModelSpec, req: RouteRequest) -> bool:
    if model.key in req.deny_models:
        return False
    if model.is_local and not req.allow_local:
        return False
    if not quality_policy.meets_quality(model, req.min_quality):
        return False
    if not quality_policy.meets_speed(model, req.min_speed):
        return False
    if not quality_policy.meets_context(model, req.min_context_tokens):
        return False
    if not cost_policy.within_cost_ceiling(model, req.max_cost_cents_per_million):
        return False
    return True


def _score(model: ModelSpec, req: RouteRequest) -> float:
    wq, wc, ws = _normalised_weights(req)
    score = (
        wq * quality_policy.quality_score(model)
        + wc * cost_policy.cost_score(model, max_cents_per_million=req.max_cost_cents_per_million)
        + ws * quality_policy.speed_score(model)
    )
    # Soft preference bump (does not override hard filters, only ranking).
    if model.key in req.prefer_models:
        score += 1.0
    return score


def _rank(models: list[ModelSpec], req: RouteRequest) -> list[ModelSpec]:
    # Deterministic: score desc, quality desc, blended-cost asc, key asc.
    return sorted(
        models,
        key=lambda m: (
            -_score(m, req),
            -m.quality,
            cost_policy.blended_cost_cents(m),
            m.key,
        ),
    )


def route(req: RouteRequest) -> RouteDecision:
    """Pick a primary model + fallback chain for the request (pure, no I/O)."""
    all_models = registry.list_models()
    candidates = [m for m in all_models if _passes_hard_filters(m, req)]
    degraded = not candidates
    if degraded:
        # Nothing met the floors — degrade to the cheapest non-denied model so
        # the caller still gets a decision. Prefer local/free when allowed.
        pool = [m for m in all_models if m.key not in req.deny_models]
        if req.allow_local is False:
            pool = [m for m in pool if not m.is_local]
        if not pool:
            pool = list(all_models)
        candidates = sorted(
            pool,
            key=lambda m: (cost_policy.blended_cost_cents(m), -m.quality, m.key),
        )

    ranked = _rank(candidates, req) if not degraded else candidates
    primary = ranked[0]
    fallback = tuple(ranked[1:])
    scores = {m.key: round(_score(m, req), 6) for m in ranked}
    return RouteDecision(
        skill=req.skill,
        primary=primary,
        fallback_chain=fallback,
        scores=scores,
        degraded=degraded,
    )


def route_and_invoke(
    prompt: str,
    req: RouteRequest,
    *,
    purpose: str = "",
    max_output_tokens: int = 800,
    **gateway_kwargs: Any,
) -> dict[str, Any]:
    """Route, then delegate the real call to llm_gateway.invoke.

    The router chooses the model; transport / budget / ledger all stay in the
    existing gateway. We pass the chosen model's ``gateway_provider`` as
    ``preferred_provider`` so the gateway uses its own configured-provider and
    fallback machinery. We do NOT re-implement any provider here.

    Import of llm_gateway is lazy so importing this package never drags in the
    gateway's heavy deps (DB / schema) at module load.
    """
    from app.platform import llm_gateway  # lazy: keep router import-light

    decision = route(req)
    result = llm_gateway.invoke(
        prompt,
        purpose=purpose or req.skill,
        max_output_tokens=max_output_tokens,
        preferred_provider=decision.primary.gateway_provider,
        **gateway_kwargs,
    )
    # Annotate with the routing decision for observability (non-breaking add).
    if isinstance(result, dict):
        result.setdefault("router", {})
        result["router"] = {
            "skill": decision.skill,
            "model_key": decision.primary.key,
            "model_id": decision.primary.model_id,
            "gateway_provider": decision.primary.gateway_provider,
            "fallback_chain": [m.key for m in decision.fallback_chain],
            "degraded": decision.degraded,
        }
    return result


# ---------------------------------------------------------------------------
# Multi-key pool selection (N8): optional helper layered over api_key_pool.
#
# This ONLY chooses *which* pooled key to use for a provider; it never injects
# the key into any LLM call (that is a later cut). When the pool is empty or
# every candidate is exhausted/unhealthy/in-cooldown it returns None, so callers
# fall back to the existing single-key path with NO behaviour change.
#
# The key-pool stores provider names as ``gemini`` / ``openai`` / ``anthropic``
# / ``apify`` / ``youtube`` / ``resend`` (settings whitelist), while the model
# registry uses ``llm_gateway`` provider names (``google`` / ``openai`` /
# ``anthropic`` / ``rule_v0``). ``_KEY_POOL_PROVIDER_ALIASES`` bridges the gap so
# a registry ``gateway_provider`` can look up the right pool rows.
# ---------------------------------------------------------------------------

# gateway_provider name -> api_key_pool provider name.
_KEY_POOL_PROVIDER_ALIASES: dict[str, str] = {
    "google": "gemini",
    "gemini": "gemini",
    "openai": "openai",
    "anthropic": "anthropic",
    "apify": "apify",
    "youtube": "youtube",
    "resend": "resend",
}


def _pool_provider_name(provider: str) -> str:
    """Normalise a gateway/registry provider name to the key-pool provider name."""
    name = str(provider or "").strip().lower()
    return _KEY_POOL_PROVIDER_ALIASES.get(name, name)


def _truthy(value: Any) -> bool:
    """Tolerant truthiness (DB BOOLEAN reads back as int 1/0; treat that too)."""
    if value is True:
        return True
    if value in (1, "1", "true", "True", "t", "yes"):
        return True
    return bool(value) and value not in (0, "0", "false", "False", "f", "no", "")


def _key_meta(row: dict[str, Any]) -> dict[str, Any]:
    """Pull the per-key metadata dict out of a pool row (metadata_json or dict)."""
    meta = row.get("metadata_json")
    if isinstance(meta, dict):
        return meta
    if isinstance(meta, str) and meta.strip():
        import json

        try:
            parsed = json.loads(meta)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            logger.debug("suppressed exception (hardening): best-effort", exc_info=True)
            return {}
    # Some callers may pass an already-parsed ``metadata`` key.
    alt = row.get("metadata")
    return alt if isinstance(alt, dict) else {}


def _key_available(row: dict[str, Any], *, now: datetime) -> bool:
    """A pool key is usable when enabled, quota left, health ok, not cooling down.

    Health / quota-used / cooldown live in ``metadata_json`` (no dedicated
    columns), so this reads them defensively and treats anything missing as the
    permissive default (healthy, zero used, no cooldown).
    """
    if not _truthy(row.get("enabled", True)):
        return False

    meta = _key_meta(row)

    # Health: only block on an explicit non-ok status.
    health = str(meta.get("health", "ok") or "ok").strip().lower()
    if health not in ("ok", "healthy", "", "up"):
        return False

    # Quota: daily_quota == 0 means "unlimited / unset" (no ceiling).
    try:
        quota = int(row.get("daily_quota") or 0)
    except (TypeError, ValueError):
        quota = 0
    if quota > 0:
        try:
            used = int(meta.get("quota_used") or 0)
        except (TypeError, ValueError):
            used = 0
        if used >= quota:
            return False

    # Cooldown: ISO8601 timestamp in metadata; in the future -> skip.
    cooldown_until = meta.get("cooldown_until")
    if cooldown_until:
        parsed = _parse_iso(str(cooldown_until))
        if parsed is not None and parsed > now:
            return False

    return True


def _parse_iso(text: str) -> Optional[datetime]:
    raw = text.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _rotation_sort_key(row: dict[str, Any]) -> tuple[int, int, int]:
    """Round-robin order: lowest rotation_cursor, then least-recently used, then id.

    ``last_used_at`` is ISO text; NULL/empty sorts first (never used -> pick it).
    """
    try:
        cursor = int(row.get("rotation_cursor") or 0)
    except (TypeError, ValueError):
        cursor = 0
    last_used = row.get("last_used_at")
    parsed = _parse_iso(str(last_used)) if last_used else None
    # Never-used (None) sorts before any timestamp.
    last_bucket = 0 if parsed is None else 1
    last_ts = 0 if parsed is None else int(parsed.timestamp())
    try:
        row_id = int(row.get("id") or 0)
    except (TypeError, ValueError):
        row_id = 0
    return (cursor, last_bucket, last_ts, row_id)  # type: ignore[return-value]


def select_key_from_rows(
    rows: list[dict[str, Any]], *, now: Optional[datetime] = None
) -> Optional[dict[str, Any]]:
    """Pure selection over already-fetched pool rows (no DB / no decryption).

    Returns the chosen row (the original dict), or None if none are available.
    Kept separate from ``select_key_for`` so the rotation/availability logic is
    unit-testable without a database.
    """
    when = now or datetime.now(timezone.utc)
    available = [r for r in rows if _key_available(r, now=when)]
    if not available:
        return None
    available.sort(key=_rotation_sort_key)
    return available[0]


def select_key_for(provider: str) -> Optional[dict[str, Any]]:
    """Pick an available pooled key for ``provider`` (round-robin), or None.

    None means "no usable pooled key" -> the caller keeps its existing single-key
    behaviour. This NEVER injects the key into an LLM call. The returned dict is
    the pool's runtime descriptor (``account_name`` + decrypted ``key``) from
    :func:`api_key_pool.pick_active_key`, which already filters enabled + skips
    undecryptable entries; we apply the additional quota/health/cooldown gate
    here over the full enabled set first.

    Import of the settings module is lazy so this router stays import-light.
    """
    pool_provider = _pool_provider_name(provider)
    if not pool_provider:
        return None
    try:
        from app.domains.settings import api_key_pool  # lazy: keep router import-light
    except Exception:
        return None

    # Fetch enabled rows (with metadata) so we can apply the full gate + rotation.
    rows = _fetch_pool_rows(api_key_pool, pool_provider)
    if rows is None:
        # No raw-row accessor available -> defer to the module's own picker,
        # which still honours enabled + decryptability (degraded but safe).
        try:
            picked = api_key_pool.pick_active_key(pool_provider)
        except Exception:
            return None
        return picked

    chosen = select_key_from_rows(rows)
    if chosen is None:
        return None

    # Resolve the plaintext lazily via the module's own decrypt path; never log it.
    try:
        plain = api_key_pool.decrypt_key(chosen.get("key_encrypted") or "")
    except Exception:
        plain = None
    if not plain:
        # Chosen row could not be decrypted; fall back to the module picker which
        # auto-skips undecryptable entries.
        try:
            return api_key_pool.pick_active_key(pool_provider)
        except Exception:
            return None
    return {"account_name": chosen.get("account_name") or "", "key": plain}


def _fetch_pool_rows(api_key_pool: Any, pool_provider: str) -> Optional[list[dict[str, Any]]]:
    """Best-effort fetch of enabled pool rows (incl. metadata) for a provider.

    Returns a list of row dicts, or None when no raw accessor is reachable (the
    caller then degrades to ``pick_active_key``). Never raises.
    """
    try:
        from app.db.connection import get_conn, is_postgres_runtime
    except Exception:
        return None
    try:
        api_key_pool.ensure_vkpi_api_key_pool_schema()
    except Exception:
        logger.debug("suppressed exception (hardening): best-effort", exc_info=True)
        pass
    try:
        enabled_flag = True if is_postgres_runtime() else 1
        rows = get_conn().execute(
            """
            SELECT id, account_name, provider, key_encrypted, daily_quota,
                   enabled, rotation_cursor, last_used_at, metadata_json
            FROM vkpi_api_key_pool
            WHERE provider=? AND enabled=?
            """,
            (pool_provider, enabled_flag),
        ).fetchall()
    except Exception:
        return None
    return [dict(r) for r in rows]
