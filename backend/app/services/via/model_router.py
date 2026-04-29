"""
services/via/model_router.py — Via model routing and multi-provider JSON generation
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from app.core.config import (
    CLAUDE_HAIKU_MODEL,
    GEMINI_MODEL,
    OPENAI_MODEL,
    VIA_DIALOGUE_COLLAB_ENABLED,
    VIA_DIALOGUE_COLLAB_MAX_PROVIDERS,
    VIA_DIALOGUE_COLLAB_PROVIDERS,
    VIA_DIALOGUE_MODEL,
    VIA_DIALOGUE_PROVIDER,
    VIA_SUMMARY_MODEL,
    VIA_SUMMARY_PROVIDER,
    VIA_VISION_MODEL,
    VIA_VISION_PROVIDER,
)
from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE, get_claude_client
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE, gemini_client
from app.services.ai.clients.openai_client import OPENAI_AVAILABLE, openai_client
from app.services.ai.retry import call_ai_with_retry
from app.services.ai.runtime_guards import guarded_provider_call


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        parts = text.split("```")
        text = next((chunk for chunk in parts if "{" in chunk and "}" in chunk), text)
        text = text.replace("json", "", 1).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _coerce_dialogue_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    title = "Via reply"
    body = text
    parts = [line.strip(" -*•") for line in str(raw or "").splitlines() if line.strip()]
    if len(parts) >= 2 and 1 <= len(parts[0].split()) <= 6 and len(parts[0]) <= 40:
        title = parts[0][:40]
        body = " ".join(parts[1:]).strip() or text
    elif len(text) <= 120:
        title = "Via reply"
    return {
        "title": title[:40] or "Via reply",
        "text": body[:500],
        "quick_actions": [],
    }


def _provider_available(provider: str) -> bool:
    provider = str(provider or "").strip().lower()
    if provider == "claude":
        return bool(ANTHROPIC_AVAILABLE and get_claude_client())
    if provider == "gemini":
        return bool(GEMINI_AVAILABLE and gemini_client)
    if provider == "openai":
        return bool(OPENAI_AVAILABLE and openai_client)
    return False


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        value = str(item or "").strip().lower()
        if value and value not in out:
            out.append(value)
    return out


def _providers_and_models_for_purpose(purpose: str) -> tuple[list[str], dict[str, str]]:
    if purpose == "summary":
        preferred = _unique([VIA_SUMMARY_PROVIDER, "openai", "claude", "gemini"])
        models = {
            "openai": VIA_SUMMARY_MODEL if VIA_SUMMARY_PROVIDER == "openai" else OPENAI_MODEL,
            "claude": VIA_SUMMARY_MODEL if VIA_SUMMARY_PROVIDER == "claude" else CLAUDE_HAIKU_MODEL,
            "gemini": VIA_SUMMARY_MODEL if VIA_SUMMARY_PROVIDER == "gemini" else GEMINI_MODEL,
        }
    elif purpose == "vision":
        preferred = _unique([VIA_VISION_PROVIDER, "gemini", "openai", "claude"])
        models = {
            "gemini": VIA_VISION_MODEL if VIA_VISION_PROVIDER == "gemini" else GEMINI_MODEL,
            "openai": VIA_VISION_MODEL if VIA_VISION_PROVIDER == "openai" else OPENAI_MODEL,
            "claude": VIA_VISION_MODEL if VIA_VISION_PROVIDER == "claude" else CLAUDE_HAIKU_MODEL,
        }
    else:
        preferred = _unique([VIA_DIALOGUE_PROVIDER, "claude", "openai", "gemini"])
        models = {
            "claude": VIA_DIALOGUE_MODEL if VIA_DIALOGUE_PROVIDER == "claude" else CLAUDE_HAIKU_MODEL,
            "openai": VIA_DIALOGUE_MODEL if VIA_DIALOGUE_PROVIDER == "openai" else OPENAI_MODEL,
            "gemini": VIA_DIALOGUE_MODEL if VIA_DIALOGUE_PROVIDER == "gemini" else GEMINI_MODEL,
        }
    return preferred, models


def _routes_for_purpose(
    purpose: str,
    *,
    preferred_override: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, str]]:
    preferred, models = _providers_and_models_for_purpose(purpose)
    ordered = _unique(preferred_override or preferred)
    routes: list[dict[str, str]] = []
    for provider in ordered:
        if _provider_available(provider):
            routes.append({"purpose": purpose, "provider": provider, "model": models.get(provider) or ""})
            if limit and len(routes) >= limit:
                break
    return routes


def _route_for_purpose(purpose: str) -> dict[str, str]:
    routes = _routes_for_purpose(purpose, limit=1)
    if routes:
        return routes[0]
    return {"purpose": purpose, "provider": "none", "model": ""}


def _dialogue_collab_routes() -> list[dict[str, str]]:
    if not VIA_DIALOGUE_COLLAB_ENABLED:
        return _routes_for_purpose("dialogue", limit=1)
    preferred = _unique(list(VIA_DIALOGUE_COLLAB_PROVIDERS) + [VIA_DIALOGUE_PROVIDER, "claude", "openai", "gemini"])
    return _routes_for_purpose(
        "dialogue",
        preferred_override=preferred,
        limit=max(1, VIA_DIALOGUE_COLLAB_MAX_PROVIDERS),
    )


def _rollout_identity(route_info: dict[str, Any] | None = None) -> str:
    route_info = dict(route_info or {})
    for key in ("session_key", "rollout_key", "client_fingerprint"):
        value = str(route_info.get(key) or "").strip()
        if value:
            return value
    user_id = int(route_info.get("user_id") or 0)
    if user_id:
        return f"user:{user_id}"
    return "default"


def _exploration_order(preferred: list[str], *, route_info: dict[str, Any] | None = None, exploration_ratio: float = 0.0) -> list[str]:
    ordered = _unique(preferred)
    if len(ordered) <= 1 or exploration_ratio <= 0:
        return ordered
    identity = _rollout_identity(route_info)
    digest = hashlib.sha256("|".join([identity] + ordered).encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    branch_count = min(len(ordered) - 1, int(1.0 / max(exploration_ratio, 0.01)))
    for branch_index in range(branch_count):
        lower = branch_index * exploration_ratio
        upper = (branch_index + 1) * exploration_ratio
        if lower <= bucket < upper and branch_index + 1 < len(ordered):
            return ordered[branch_index + 1 :] + ordered[: branch_index + 1]
    return ordered


def _routing_bucket_key(route_info: dict[str, Any] | None = None) -> str:
    route_info = dict(route_info or {})
    intent = str(route_info.get("intent") or "quick_chat").strip().lower()
    surface = str(route_info.get("current_surface") or route_info.get("surface") or "upload").strip().lower()
    return f"{intent}:{surface}"


def _learner_order(preferred: list[str], *, route_info: dict[str, Any] | None = None, exploration_ratio: float = 0.0) -> list[str]:
    ordered = _unique(preferred)
    if len(ordered) <= 1:
        return ordered
    try:
        from app.db.repositories.via_control import list_via_routing_provider_stats
    except Exception:
        return _exploration_order(ordered, route_info=route_info, exploration_ratio=exploration_ratio)
    stats_rows = list_via_routing_provider_stats(limit=24, bucket_key=_routing_bucket_key(route_info), target="dialogue_generation")
    if not stats_rows:
        return _exploration_order(ordered, route_info=route_info, exploration_ratio=exploration_ratio)
    stats_by_provider = {str(item.get("provider") or "").strip().lower(): item for item in stats_rows}

    def _score(provider: str) -> float:
        item = stats_by_provider.get(provider, {})
        exposure = int(item.get("exposure_count") or 0)
        success_rate = float(item.get("success_rate") or 0.0)
        avg_reward = float(item.get("avg_reward") or 0.0)
        guard_penalty = float(item.get("guard_fail_count") or 0.0) / max(1.0, float(exposure or 1))
        explore_bonus = 1.0 / max(1.0, float(exposure + 1) ** 0.5)
        return (success_rate * 0.55) + (avg_reward * 0.55) + (explore_bonus * max(0.05, float(exploration_ratio or 0.0))) - (guard_penalty * 0.6)

    ranked = sorted(ordered, key=_score, reverse=True)
    return _exploration_order(ranked, route_info=route_info, exploration_ratio=exploration_ratio)


def get_via_model_plan(
    *,
    policy: dict[str, Any] | None = None,
    route_info: dict[str, Any] | None = None,
) -> dict[str, dict[str, str] | dict[str, Any]]:
    policy = dict(policy or {})
    route_info = dict(route_info or {})
    execution_mode = str(policy.get("execution_mode") or "").strip().lower()
    preferred = _unique([str(item).strip().lower() for item in list(policy.get("providers") or []) if str(item).strip()])
    if execution_mode == "bandit_explore":
        preferred = _learner_order(
            preferred or _unique([VIA_DIALOGUE_PROVIDER, "claude", "openai", "gemini"]),
            route_info=route_info,
            exploration_ratio=float(policy.get("exploration_ratio") or 0.0),
        )
        dialogue_routes = _routes_for_purpose("dialogue", preferred_override=preferred, limit=1)
    elif execution_mode == "single_preferred":
        dialogue_routes = _routes_for_purpose("dialogue", preferred_override=preferred or None, limit=1)
    elif execution_mode == "collab_preferred":
        collab_limit = max(1, int(policy.get("collab_limit") or VIA_DIALOGUE_COLLAB_MAX_PROVIDERS or 2))
        dialogue_routes = _routes_for_purpose("dialogue", preferred_override=preferred or None, limit=collab_limit)
    else:
        dialogue_routes = _dialogue_collab_routes()
    dialogue = dict(dialogue_routes[0] or _route_for_purpose("dialogue")) if dialogue_routes else dict(_route_for_purpose("dialogue"))
    dialogue["mode"] = "collab" if len(dialogue_routes) > 1 else "single"
    dialogue["collaboration_enabled"] = "true" if len(dialogue_routes) > 1 else ("true" if VIA_DIALOGUE_COLLAB_ENABLED else "false")
    dialogue["consulted_providers"] = ",".join(route["provider"] for route in dialogue_routes)
    dialogue["consulted_models"] = ",".join(route["model"] for route in dialogue_routes if route.get("model"))
    dialogue["routes"] = dialogue_routes
    dialogue["execution_mode"] = execution_mode or ("collab_preferred" if len(dialogue_routes) > 1 else "single_preferred")
    dialogue["policy_version"] = str(policy.get("policy_version") or "")
    dialogue["rollout_state"] = str(policy.get("rollout_state") or "")
    rollout_raw = policy.get("rollout_percentage")
    dialogue["rollout_percentage"] = 1.0 if rollout_raw in (None, "") else float(rollout_raw)
    if dialogue_routes:
        dialogue["primary_provider"] = dialogue_routes[0]["provider"]
        dialogue["primary_model"] = dialogue_routes[0]["model"]
    return {
        "dialogue": dialogue,
        "summary": _route_for_purpose("summary"),
        "vision": _route_for_purpose("vision"),
    }


def preview_via_routes(
    purpose: str,
    *,
    preferred_override: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, str]]:
    return _routes_for_purpose(purpose, preferred_override=preferred_override, limit=limit)


def _extract_gemini_text(resp: Any) -> str:
    text = str(getattr(resp, "text", "") or "").strip()
    if text:
        return text
    candidates = getattr(resp, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = str(getattr(part, "text", "") or "").strip()
            if part_text:
                return part_text
    return ""


async def generate_json_with_route(
    *,
    purpose: str,
    system_prompt: str,
    payload: dict[str, Any],
    temperature: float = 0.55,
    max_tokens: int = 260,
    route_override: dict[str, str] | None = None,
    allow_text_fallback: bool = False,
) -> dict[str, Any] | None:
    route = dict(route_override or _route_for_purpose(purpose))
    provider = route["provider"]
    model = route["model"]
    if provider == "none":
        return None
    prompt = json.dumps(payload, ensure_ascii=False)
    return await _generate_json_with_provider(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        allow_text_fallback=allow_text_fallback,
    )


async def _generate_json_with_provider(
    *,
    provider: str,
    model: str,
    system_prompt: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    allow_text_fallback: bool = False,
) -> dict[str, Any] | None:
    try:
        if provider == "openai":
            def _call_openai() -> Any:
                return openai_client.chat.completions.create(
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                )

            resp = await guarded_provider_call("openai", lambda: asyncio.to_thread(_call_openai))
            content = resp.choices[0].message.content if resp and resp.choices else ""
        elif provider == "claude":
            client = get_claude_client()
            if not client:
                return None

            def _call_claude() -> Any:
                return call_ai_with_retry(
                    "via.model_router.claude",
                    lambda: client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        system=system_prompt,
                        messages=[{"role": "user", "content": prompt}],
                    ),
                )

            resp = await guarded_provider_call("claude", lambda: asyncio.to_thread(_call_claude))
            parts = getattr(resp, "content", None) or []
            content = "".join(str(getattr(part, "text", "") or "") for part in parts).strip()
        else:
            def _call_gemini() -> Any:
                combined = f"{system_prompt}\n\nPayload:\n{prompt}"
                return gemini_client.models.generate_content(model=model, contents=combined)

            resp = await guarded_provider_call("gemini", lambda: asyncio.to_thread(_call_gemini))
            content = _extract_gemini_text(resp)
        data = _parse_json_object(content)
        if not data:
            data = _coerce_dialogue_object(content) if allow_text_fallback else None
        if not data:
            return None
        return {"provider": provider, "model": model, "data": data}
    except Exception:
        return None


def _merge_dialogue_results(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not results:
        return None
    primary = results[0]
    primary_data = primary.get("data") or {}
    primary_text = str(primary_data.get("text") or "").strip()
    fallback = next(
        (
            item
            for item in results
            if str((item.get("data") or {}).get("text") or "").strip()
        ),
        primary,
    )
    fallback_data = fallback.get("data") or {}
    title = str(primary_data.get("title") or fallback_data.get("title") or "Via reply").strip()[:40] or "Via reply"
    text = str(primary_text or fallback_data.get("text") or "").strip()
    if not text:
        return None
    quick_actions: list[str] = []
    for item in results:
        data = item.get("data") or {}
        for action in data.get("quick_actions") or []:
            value = str(action or "").strip()[:40]
            if value and value not in quick_actions:
                quick_actions.append(value)
    return {
        "provider": "collab",
        "model": primary.get("model") or "",
        "data": {
            "title": title,
            "text": text,
            "quick_actions": quick_actions[:3],
        },
        "primary_provider": primary.get("provider") or "",
        "primary_model": primary.get("model") or "",
        "providers": [str(item.get("provider") or "") for item in results if str(item.get("provider") or "")],
        "models": [str(item.get("model") or "") for item in results if str(item.get("model") or "")],
        "strategy": "primary_consensus",
    }


async def generate_json_with_collab(
    *,
    purpose: str,
    system_prompt: str,
    payload: dict[str, Any],
    temperature: float = 0.55,
    max_tokens: int = 260,
    routes_override: list[dict[str, str]] | None = None,
    allow_text_fallback: bool = False,
) -> dict[str, Any] | None:
    routes = list(routes_override or (_dialogue_collab_routes() if purpose == "dialogue" else _routes_for_purpose(purpose, limit=1)))
    if not routes:
        return None
    prompt = json.dumps(payload, ensure_ascii=False)
    if len(routes) == 1:
        route = routes[0]
        return await _generate_json_with_provider(
            provider=route["provider"],
            model=route["model"],
                system_prompt=system_prompt,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                allow_text_fallback=allow_text_fallback,
            )
    results = await asyncio.gather(
        *[
            _generate_json_with_provider(
                provider=route["provider"],
                model=route["model"],
                    system_prompt=system_prompt,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    allow_text_fallback=allow_text_fallback,
                )
                for route in routes
            ]
    )
    successful = [result for result in results if result]
    return _merge_dialogue_results(successful)


async def summarize_via_exchange(
    *,
    user_text: str,
    reply_text: str,
    signals: dict[str, Any] | None = None,
    current_surface: str = "upload",
) -> dict[str, Any]:
    base_signals = dict(signals or {})
    fallback_summary = str(base_signals.get("summary") or "").strip() or str(user_text or "").strip()[:180]
    fallback_keywords = [str(item).strip() for item in (base_signals.get("keywords") or []) if str(item).strip()][:8]
    system_prompt = (
        "You are Via's memory summarizer. "
        "Turn a user message and Via reply into compact reusable memory. "
        "Return JSON only with keys summary and keywords. "
        "summary must be under 180 characters. keywords must be an array of up to 8 concise tokens."
    )
    payload = {
        "surface": current_surface,
        "user_text": str(user_text or "").strip()[:500],
        "reply_text": str(reply_text or "").strip()[:500],
        "signals": {
            "summary": fallback_summary,
            "keywords": fallback_keywords,
            "traits": base_signals.get("traits") or {},
        },
    }
    result = await generate_json_with_route(
        purpose="summary",
        system_prompt=system_prompt,
        payload=payload,
        temperature=0.25,
        max_tokens=180,
    )
    if not result:
        return {
            "summary": fallback_summary,
            "keywords": fallback_keywords,
            "provider": "fallback",
            "model": "",
        }
    data = result["data"]
    keywords = [str(item).strip()[:40] for item in (data.get("keywords") or []) if str(item).strip()][:8]
    summary = str(data.get("summary") or "").strip()[:180] or fallback_summary
    return {
        "summary": summary,
        "keywords": keywords or fallback_keywords,
        "provider": result["provider"],
        "model": result["model"],
    }
