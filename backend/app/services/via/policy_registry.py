"""
services/via/policy_registry.py — Deterministic core + learnable shell policy registry
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.core.logging import get_logger
from app.db.repositories.via_control import (
    get_live_via_policy_version,
    get_staged_via_policy_version,
    list_live_via_policy_versions,
)

logger = get_logger(__name__)


_POLICY_REGISTRY: dict[str, dict[str, Any]] = {
    "intent_route": {
        "policy_key": "via.intent.hybrid",
        "policy_version": "2026.04.14",
        "core_mode": "deterministic_core",
        "learnable_shell": True,
        "description": "Route user input across business, product, memory, creative, and deep lanes.",
    },
    "retrieval_plan": {
        "policy_key": "via.retrieval.selective",
        "policy_version": "2026.04.14",
        "core_mode": "learnable_shell",
        "learnable_shell": True,
        "description": "Decide whether to stay on bundle memory, vector recall, or seed-only context.",
    },
    "reply_mode": {
        "policy_key": "via.reply.mode",
        "policy_version": "2026.04.14",
        "core_mode": "learnable_shell",
        "learnable_shell": True,
        "description": "Choose between policy guard, fast brains, and AI dialogue.",
    },
    "model_choice": {
        "policy_key": "via.model.route",
        "policy_version": "2026.04.14",
        "core_mode": "learnable_shell",
        "learnable_shell": True,
        "description": "Choose the provider/model plan for dialogue and deep reasoning.",
    },
    "risk_gate": {
        "policy_key": "via.guard.policy",
        "policy_version": "2026.04.14",
        "core_mode": "deterministic_core",
        "learnable_shell": False,
        "description": "Apply hard safety and privacy boundaries before generation.",
    },
    "memory_promotion": {
        "policy_key": "via.memory.promotion",
        "policy_version": "2026.04.14",
        "core_mode": "learnable_shell",
        "learnable_shell": True,
        "description": "Promote working exchange into episodic, semantic, or procedural memory.",
    },
}


def _load_live_overlay(policy_key: str) -> dict[str, Any]:
    try:
        row = get_live_via_policy_version(policy_key)
    except Exception:
        logger.warning(
            "via.policy_registry.load_live_overlay_failed",
            extra={"policy_key": policy_key},
            exc_info=True,
        )
        return {}
    if not row:
        return {}
    config = dict(row.get("config") or {})
    return {
        **config,
        "policy_version": str(row.get("version_label") or config.get("policy_version") or ""),
        "policy_source": "db_live",
        "live_version_key": str(row.get("version_key") or ""),
        "source_proposal_key": str(row.get("source_proposal_key") or ""),
    }


def _load_staged_overlay(policy_key: str) -> dict[str, Any]:
    try:
        row = get_staged_via_policy_version(policy_key)
    except Exception:
        logger.warning(
            "via.policy_registry.load_staged_overlay_failed",
            extra={"policy_key": policy_key},
            exc_info=True,
        )
        return {}
    if not row:
        return {}
    config = dict(row.get("config") or {})
    return {
        **config,
        "policy_version": str(row.get("version_label") or config.get("policy_version") or ""),
        "policy_source": "db_staged",
        "staged_version_key": str(row.get("version_key") or ""),
        "source_proposal_key": str(row.get("source_proposal_key") or ""),
    }


def _route_rollout_identity(route_info: dict[str, Any] | None = None) -> str:
    route_info = dict(route_info or {})
    for key in ("rollout_key", "session_key", "client_fingerprint"):
        value = str(route_info.get(key) or "").strip()
        if value:
            return value
    user_id = int(route_info.get("user_id") or 0)
    if user_id:
        return f"user:{user_id}"
    session_id = int(route_info.get("session_id") or 0)
    if session_id:
        return f"session:{session_id}"
    return ""


def _rollout_allows_session(policy_key: str, overlay: dict[str, Any], route_info: dict[str, Any] | None = None) -> bool:
    rollout_mode = str(overlay.get("rollout_mode") or "").strip().lower()
    try:
        rollout_raw = overlay.get("rollout_percentage")
        rollout_percentage = 1.0 if rollout_raw in (None, "") else float(rollout_raw)
    except Exception:
        rollout_percentage = 1.0
    if rollout_mode != "limited":
        return True
    if rollout_percentage <= 0:
        return False
    if rollout_percentage >= 1:
        return True
    identity = _route_rollout_identity(route_info)
    if not identity:
        return False
    digest = hashlib.sha256(f"{policy_key}|{identity}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return bucket < rollout_percentage


def get_via_policy(decision_type: str, *, route_info: dict[str, Any] | None = None) -> dict[str, Any]:
    decision_key = str(decision_type or "").strip()
    policy = dict(_POLICY_REGISTRY.get(decision_key) or {})
    route_info = dict(route_info or {})
    if not policy:
        return {
            "policy_key": "via.unknown",
            "policy_version": "2026.04.14",
            "core_mode": "deterministic_core",
            "learnable_shell": False,
            "description": "",
        }
    live_overlay = _load_live_overlay(str(policy.get("policy_key") or ""))
    if live_overlay:
        rollout_raw = live_overlay.get("rollout_percentage")
        policy["rollout_state"] = "live_holdout"
        policy["rollout_mode"] = str(live_overlay.get("rollout_mode") or "")
        policy["rollout_percentage"] = 1.0 if rollout_raw in (None, "") else float(rollout_raw)
        policy["live_version_key"] = str(live_overlay.get("live_version_key") or "")
        if _rollout_allows_session(str(policy.get("policy_key") or ""), live_overlay, route_info):
            policy.update(live_overlay)
            policy["rollout_state"] = "live_applied"
    if decision_key == "model_choice":
        if not policy.get("execution_mode"):
            if route_info.get("use_deep_reasoning"):
                policy["execution_mode"] = "collab_preferred"
            else:
                policy["execution_mode"] = "single_preferred"
    elif decision_key == "reply_mode":
        if not policy.get("execution_mode"):
            policy["execution_mode"] = "policy_guard" if route_info.get("guarded") else str(route_info.get("brain") or "fast_path")
    return policy


def get_via_shadow_policy(decision_type: str, *, route_info: dict[str, Any] | None = None) -> dict[str, Any]:
    decision_key = str(decision_type or "").strip()
    policy = dict(_POLICY_REGISTRY.get(decision_key) or {})
    route_info = dict(route_info or {})
    if not policy or not bool(policy.get("learnable_shell")):
        return {}
    policy.update(_load_staged_overlay(str(policy.get("policy_key") or "")))
    if policy.get("policy_source") != "db_staged":
        return {}
    if decision_key == "model_choice":
        if not policy.get("execution_mode"):
            if route_info.get("use_deep_reasoning"):
                policy["execution_mode"] = "collab_preferred"
            else:
                policy["execution_mode"] = "single_preferred"
    elif decision_key == "reply_mode":
        if not policy.get("execution_mode"):
            policy["execution_mode"] = "policy_guard" if route_info.get("guarded") else str(route_info.get("brain") or "fast_path")
    policy["shadow_only"] = True
    return policy


def list_via_policies() -> list[dict[str, Any]]:
    live_versions = {}
    try:
        live_versions = {
            str(item.get("policy_key") or ""): item
            for item in list_live_via_policy_versions()
        }
    except Exception:
        live_versions = {}
    items: list[dict[str, Any]] = []
    for key, value in _POLICY_REGISTRY.items():
        merged = dict(value)
        live = live_versions.get(str(value.get("policy_key") or ""))
        if live:
            merged.update(dict(live.get("config") or {}))
            merged["policy_version"] = str(live.get("version_label") or merged.get("policy_version") or "")
            merged["policy_source"] = "db_live"
            merged["live_version_key"] = str(live.get("version_key") or "")
            merged["source_proposal_key"] = str(live.get("source_proposal_key") or "")
        items.append(dict({"decision_type": key}, **merged))
    return items
