"""Tenant-safe internal context bridge for Marketing Advisor.

This bridge deliberately does *not* reuse the legacy discovery federation or
semantic-recall tables because those surfaces are still global and may call
paid providers.  It reads only the Advisor's composite owner-scoped tables:
the current thread, its sanitized context references, prior messages, and
explicitly confirmed normal-sensitivity personal memory.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from app.domains.advisor import repository
from app.domains.advisor.scope import AdvisorScope


_MODE = "advisor_owner_scope_v1"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}")


def _tokens(value: Any) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(str(value or ""))[:32]}


def _evidence_id(kind: str, uid: Any) -> str:
    digest = hashlib.sha256(f"{kind}:{str(uid or '')}".encode("utf-8")).hexdigest()
    return f"{kind}_{digest[:16]}"


def readiness() -> dict[str, Any]:
    schema_ready = repository.schema_ready()
    return {
        "ready": schema_ready,
        "reason": "" if schema_ready else "advisor_schema_unavailable",
        "mode": _MODE,
        "scope_dimensions": ["organization_id", "staff_id", "user_id"],
        "sources": ["confirmed_personal_memory", "current_thread_history", "sanitized_context_refs"],
        "legacy_global_search_used": False,
        "external_provider_allowed": False,
        "provider_called": False,
        "cost_incurred": False,
    }


def _owner_context(
    scope: AdvisorScope,
    *,
    question: str,
    thread_uid: str = "",
) -> dict[str, Any]:
    query_tokens = _tokens(question)
    snapshot = repository.get_memory(scope, limit=50)
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), dict) else {}
    memory_enabled = str(settings.get("state") or "active") == "active"
    memories: list[dict[str, Any]] = []
    if memory_enabled:
        for fact in snapshot.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            if fact.get("status") != "active" or fact.get("sensitivity") != "normal":
                continue
            summary = str(fact.get("summary") or "").strip()[:600]
            memory_tokens = _tokens(
                " ".join(
                    (
                        str(fact.get("memory_kind") or ""),
                        str(fact.get("memory_key") or ""),
                        summary,
                    )
                )
            )
            overlap = len(query_tokens & memory_tokens)
            memories.append(
                {
                    "evidence_id": _evidence_id("memory", fact.get("fact_uid")),
                    "kind": "confirmed_memory",
                    "memory_kind": str(fact.get("memory_kind") or "")[:40],
                    "memory_key": str(fact.get("memory_key") or "")[:160],
                    "summary": summary,
                    "match_terms": overlap,
                    "external_share_allowed": True,
                }
            )
    memories.sort(key=lambda item: (int(item.get("match_terms") or 0), item["evidence_id"]), reverse=True)

    refs: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    if thread_uid:
        thread = repository.get_thread(scope, thread_uid)
        for ref in thread.get("context_refs_json") or []:
            if not isinstance(ref, dict):
                continue
            snapshot_value = ref.get("snapshot") if isinstance(ref.get("snapshot"), dict) else {}
            refs.append(
                {
                    "evidence_id": _evidence_id(
                        "context",
                        f"{ref.get('entity_type')}:{ref.get('entity_id')}",
                    ),
                    "kind": "context_ref",
                    "entity_type": str(ref.get("entity_type") or "")[:40],
                    "entity_id": str(ref.get("entity_id") or "")[:160],
                    "label": str(snapshot_value.get("label") or "")[:240],
                    "platform": str(snapshot_value.get("platform") or "")[:40],
                    "observed_at": str(snapshot_value.get("observed_at") or "")[:64],
                    "external_share_allowed": True,
                }
            )
        for message in repository.list_messages(scope, thread_uid, limit=12)[-8:]:
            if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
                continue
            history.append(
                {
                    "evidence_id": _evidence_id("message", message.get("message_uid")),
                    "kind": "thread_message",
                    "role": str(message.get("role") or "")[:20],
                    "content": str(message.get("content_text") or "")[:1200],
                    # A user must explicitly opt in on the new request before
                    # history is included in an external-provider prompt.
                    "external_share_allowed": True,
                }
            )
    return {
        "mode": _MODE,
        "memory_enabled": memory_enabled,
        "memories": memories[:12],
        "context_refs": refs[:12],
        "history": history,
        "retention_policy": snapshot.get("retention_policy") or {},
    }


def answer(
    question: str,
    scope: AdvisorScope,
    *,
    thread_uid: str = "",
) -> dict[str, Any]:
    """Return a safe local context envelope without external calls or costs."""

    context = _owner_context(scope, question=question, thread_uid=thread_uid)
    evidence = [
        *context["memories"],
        *context["context_refs"],
        *context["history"],
    ]
    matched = sum(1 for item in context["memories"] if item.get("match_terms"))
    if evidence:
        local_answer = (
            f"已从当前员工的私有空间召回 {len(evidence)} 条内部上下文"
            f"（命中记忆 {matched} 条）。已阻断旧的全局搜索和所有外部 Provider；"
            "若要生成模型建议，仍需本次显式允许、精确模型就绪和独立预算闸通过。"
        )
    else:
        local_answer = (
            "当前员工的私有空间暂无可用上下文。本次未访问全局业务表，"
            "未调用外部搜索或模型。"
        )
    return {
        "answer": local_answer,
        "status": "ready",
        "mode": _MODE,
        "reason": "",
        "evidence": evidence,
        "provider_context": context,
        "navigation_actions": [],
        "scope_enforced": True,
        "provider_called": False,
        "cost_incurred": False,
    }
