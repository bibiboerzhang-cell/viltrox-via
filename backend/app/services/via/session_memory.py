"""Via session memory, persona patch, and local learning helpers."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.repositories.via_control import (
    get_latest_via_outcome_record,
    get_via_reward_trace_by_idempotency,
    insert_via_decision_record,
    insert_via_outcome_record,
    insert_via_retrieval_evidence,
    insert_via_reward_trace,
    list_via_decision_records,
    list_via_memory_retention_stats,
    list_via_reward_traces,
    upsert_via_memory_retention_stat,
    upsert_via_routing_provider_stat,
    update_via_outcome_record,
)
from app.core.config import VIA_BASE_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.db.repositories.via import (
    add_via_memory_ref,
    create_via_session,
    find_via_session,
    get_or_create_via_persona,
    get_via_session_bundle,
    touch_via_session,
    update_via_persona,
)
from app.services.memory.l3_store import record_creator_memory_fact, record_feedback_signal
from app.services.via.activity_pack import resolve_via_activity_state
from app.services.via.business_brain import build_business_context, get_via_business_reply
from app.services.via.decision_ledger import (
    build_context_refs,
    build_decision_candidates,
    estimate_model_cost,
    summarize_control_loop,
)
from app.services.via.external_viltrox_assets import (
    get_external_system_prompt_injection,
    sanitize_external_via_output,
)
from app.services.via.memory_promoter import persist_via_memory_promotions, propose_via_memory_promotions
from app.services.via.model_router import generate_json_with_collab, generate_json_with_route, get_via_model_plan, preview_via_routes
from app.services.via.policy_registry import get_via_policy, get_via_shadow_policy
from app.services.via.product_brain import build_product_context, get_via_product_reply
from app.services.via.reward_aggregator import (
    aggregate_via_reply_outcome,
    merge_via_reward_trace_summary,
    summarize_via_reward_traces,
)
from app.services.via.shadow_learning import (
    evaluate_shadow_memory_promotion,
    evaluate_shadow_model_choice,
    evaluate_shadow_retrieval_plan,
)
from app.services.via.trigger_engine import build_via_trigger_snapshot
from app.services.via.learning_signals import (
    compact_via_profile_context,
    extract_via_learning_signals,
    merge_via_persona_profile,
)
from app.services.via.knowledge_seed import (
    build_via_seed_documents,
    extract_workspace_docx_product_line_catalog,
    extract_workspace_docx_software_catalog,
)
from app.services.via.vector_memory import (
    recall_via_vector_memory,
    store_via_seed_documents,
    store_via_vector_exchange,
    sync_bundle_memory_refs_to_vector,
)

logger = get_logger(__name__)


def _hash_ip(raw_ip: str) -> str:
    value = str(raw_ip or "").strip()
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _anonymous_persona_key(signed_device_id: str = "", client_fingerprint: str = "", request_ip: str = "") -> str:
    seed_parts = [
        str(signed_device_id or "").strip(),
        str(client_fingerprint or "").strip(),
        _hash_ip(request_ip),
    ]
    seed = "|".join(part for part in seed_parts if part)
    if not seed:
        seed = secrets.token_hex(8)
    return f"anon:{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}"


def _sanitize_persona_patch(body: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(body or {})
    patch: dict[str, Any] = {}
    for key in ("display_name", "archetype", "temperament", "talk_style", "outfit_code", "accessory_code"):
        value = str(raw.get(key) or "").strip()
        if value:
            patch[key] = value[:120]
    for key, default in (("talkativeness", 0.55), ("curiosity", 0.7)):
        if key not in raw:
            continue
        try:
            patch[key] = max(0.0, min(1.0, float(raw.get(key))))
        except Exception:
            patch[key] = default
    if isinstance(raw.get("profile"), dict):
        patch["profile_json"] = raw["profile"]
    if isinstance(raw.get("memory_policy"), dict):
        patch["memory_policy_json"] = raw["memory_policy"]
    return patch


def _load_memory_candidates(user_id: int, session_key: str, limit: int = 12) -> list[dict[str, Any]]:
    conn = get_conn()
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    handles = [
        str(row["handle"] or "").strip()
        for row in conn.execute(
            "SELECT handle FROM user_social_accounts WHERE user_id=? ORDER BY verified DESC, id DESC LIMIT 8",
            (int(user_id or 0),),
        ).fetchall()
    ] if int(user_id or 0) else []

    if int(user_id or 0):
        rows = conn.execute(
            """
            SELECT memory_kind, fact_key, source_ref, fact_value_json, confidence
            FROM creator_memory_entries
            WHERE user_id=?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (int(user_id), int(limit)),
        ).fetchall()
        for row in rows:
            key = (str(row["memory_kind"] or ""), str(row["source_ref"] or row["fact_key"] or ""))
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "memory_kind": row["memory_kind"] or "creator_memory",
                    "source_ref": row["source_ref"] or f"creator_memory:{row['fact_key']}",
                    "memory_key": row["fact_key"] or "",
                    "weight": float(row["confidence"] or 0.6),
                    "payload": {"session_key": session_key, "fact_value_json": row["fact_value_json"] or "{}"},
                }
            )

    for handle in handles[:4]:
        rows = conn.execute(
            """
            SELECT memory_kind, fact_key, source_ref, fact_value_json, confidence
            FROM creator_memory_entries
            WHERE creator_handle=?
            ORDER BY updated_at DESC
            LIMIT 4
            """,
            (handle,),
        ).fetchall()
        for row in rows:
            key = (str(row["memory_kind"] or ""), str(row["source_ref"] or row["fact_key"] or ""))
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "memory_kind": row["memory_kind"] or "creator_memory",
                    "source_ref": row["source_ref"] or f"creator_memory:{row['fact_key']}",
                    "memory_key": row["fact_key"] or "",
                    "weight": float(row["confidence"] or 0.6),
                    "payload": {"handle": handle, "fact_value_json": row["fact_value_json"] or "{}"},
                }
            )

    observation_rows = conn.execute(
        """
        SELECT observation_key, source_platform, subject_type, subject_key, summary, metrics_json
        FROM market_observations
        ORDER BY created_at DESC
        LIMIT 6
        """
    ).fetchall()
    for row in observation_rows:
        key = ("market_observation", str(row["observation_key"] or ""))
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "memory_kind": "market_observation",
                "source_ref": row["observation_key"] or f"market:{row['subject_key']}",
                "memory_key": row["subject_key"] or "",
                "weight": 0.42,
                "payload": {
                    "source_platform": row["source_platform"] or "",
                    "subject_type": row["subject_type"] or "",
                    "summary": row["summary"] or "",
                    "metrics_json": row["metrics_json"] or "{}",
                },
            }
        )

    return refs[: max(1, int(limit))]


def _memory_teaser(bundle: dict[str, Any]) -> str:
    for item in bundle.get("memory_refs", []):
        payload = item.get("payload") or {}
        summary = str(payload.get("summary") or "").strip()
        if summary:
            return summary[:180]
        fact_value = str(payload.get("fact_value_json") or "").strip()
        if fact_value:
            return fact_value[:180]
    return ""


def _memory_prompt_lines(bundle: dict[str, Any], limit: int = 6) -> list[str]:
    lines: list[str] = []
    for item in bundle.get("memory_refs", [])[:limit]:
        payload = item.get("payload") or {}
        summary = str(payload.get("summary") or "").strip()
        snippet = str(payload.get("text_snippet") or "").strip()
        lowered_summary = summary.lower()
        if lowered_summary.startswith("keywords:") or lowered_summary.startswith("关键词："):
            continue
        if lowered_summary in {
            "user is asking via about products and creator progress.",
            "用户正在和 via 聊产品与创作。",
        }:
            continue
        if summary:
            if snippet and snippet[:220] not in summary:
                lines.append(f"{summary[:180]} | {snippet[:220]}")
            else:
                lines.append(summary[:220])
            continue
        fact_value = str(payload.get("fact_value_json") or "").strip()
        if fact_value:
            lines.append(fact_value[:180])
    return lines


def _fire_and_forget(coro: Any, *, label: str) -> None:
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:
        return

    def _log_failure(done: asyncio.Task) -> None:
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("via.background_task_failed", extra={"label": label}, exc_info=True)

    task.add_done_callback(_log_failure)


async def _prime_via_memory_assets(bundle: dict[str, Any], *, include_remote: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    vector_stats: dict[str, Any] = {"backend": "none", "upserted": 0, "deferred": True}
    seed_stats: dict[str, Any] = {"backend": "none", "stored": 0, "deferred": True}
    try:
        vector_stats = await asyncio.wait_for(sync_bundle_memory_refs_to_vector(bundle), timeout=1.5)
    except Exception:
        logger.warning("via.vector_memory_prime_failed", exc_info=True)
    try:
        seed_docs = await asyncio.wait_for(build_via_seed_documents(bundle, include_remote=include_remote), timeout=2.0)
        seed_stats = await asyncio.wait_for(store_via_seed_documents(bundle, seed_docs), timeout=2.0)
    except Exception:
        logger.warning("via.seed_memory_prime_failed", extra={"include_remote": include_remote}, exc_info=True)
    return vector_stats, seed_stats


def _persist_via_learning(bundle: dict[str, Any], user_text: str, reply_text: str, *, current_surface: str = "upload") -> dict[str, Any]:
    session = bundle.get("session") or {}
    persona = bundle.get("persona") or {}
    if not session or not persona:
        return {}
    signals = extract_via_learning_signals(user_text, reply_text=reply_text)
    if not signals.get("keywords") and not signals.get("traits"):
        return {"persona": persona, "signals": signals}
    updated_profile = merge_via_persona_profile(persona.get("profile") or {}, signals)
    updated_persona = update_via_persona(int(persona["id"]), {"profile_json": updated_profile})
    source_ref = f"via:{session.get('session_key')}:{hashlib.sha256((user_text + '|' + reply_text).encode('utf-8')).hexdigest()[:12]}"
    add_via_memory_ref(
        session_id=int(session["id"]),
        memory_kind="conversation_signal",
        source_ref=source_ref,
        memory_key=f"signal:{signals.get('captured_at', '')}",
        weight=float(signals.get("confidence") or 0.5),
        payload={
            "summary": signals.get("summary") or "",
            "keywords": signals.get("keywords") or [],
            "traits": signals.get("traits") or {},
            "language": signals.get("language") or "",
            "surface": current_surface or session.get("current_surface") or "upload",
            "reply_excerpt": signals.get("reply_excerpt") or "",
        },
    )
    user_id = int(session.get("user_id") or 0)
    if user_id:
        record_creator_memory_fact(
            user_id=user_id,
            memory_kind="via_traits",
            fact_key="user_traits",
            fact_value=updated_profile.get("user_traits") or {},
            confidence=float(signals.get("confidence") or 0.5),
            source_ref=source_ref,
        )
        record_creator_memory_fact(
            user_id=user_id,
            memory_kind="via_keywords",
            fact_key="core_keywords",
            fact_value=updated_profile.get("core_keywords") or [],
            confidence=float(signals.get("confidence") or 0.5),
            source_ref=source_ref,
        )
        record_creator_memory_fact(
            user_id=user_id,
            memory_kind="via_summary",
            fact_key="conversation_summary",
            fact_value={
                "summary": signals.get("summary") or "",
                "keywords": signals.get("keywords") or [],
                "surface": current_surface or session.get("current_surface") or "upload",
            },
            confidence=float(signals.get("confidence") or 0.5),
            source_ref=source_ref,
        )
    record_feedback_signal(
        source_type="via_chat",
        source_id=str(session.get("session_key") or ""),
        event_type="conversation_signals_extracted",
        actor_role="user",
        user_id=user_id,
        payload={
            "summary": signals.get("summary") or "",
            "keywords": signals.get("keywords") or [],
            "traits": signals.get("traits") or {},
            "language": signals.get("language") or "",
        },
    )
    return {"persona": updated_persona, "signals": signals}


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
