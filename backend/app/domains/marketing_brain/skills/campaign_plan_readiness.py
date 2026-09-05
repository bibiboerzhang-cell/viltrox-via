"""Server-owned planning readiness, not execution permission or market proof.

An available row is material for human review only. It does not prove market
scope, creator suitability, sales, approved spend or permission to send anything.
This module has no persistence, model or provider dependencies.
"""
from __future__ import annotations

from typing import Any

_SOURCE_OK = {"ok", "ready"}
_SIGNAL_SECTIONS = ("competitor_moves", "opportunities", "today_actions")
_MATERIAL_STATES = {"", "ok", "ready", "awaiting_sales"}
_SIGNAL_FIELDS = {
    "competitor_moves": ("brand", "signal_type", "detail"),
    "opportunities": ("title", "basis", "window"),
    "today_actions": ("title", "why"),
}


def _projection_available(projection: Any) -> bool:
    """list_pool has no status: inspect its actual read-projection contract."""
    if not isinstance(projection, dict):
        return False
    revision = projection.get("source_revision")
    counts = (projection.get("physical_master_rows"), projection.get("visible_rows"))
    return (
        projection.get("method") == "canonical_pool_read_projection_v1"
        and projection.get("bridge_evidence_available") is True
        and isinstance(revision, str) and bool(revision.strip())
        and revision.strip().lower() != "unavailable"
        and all(type(count) is int and count >= 0 for count in counts)
        and counts[1] <= counts[0]
    )


def _candidate_identity_available(row: dict[str, Any]) -> bool:
    identity = row.get("id")
    if isinstance(identity, bool) or not isinstance(identity, (int, str)):
        return False
    try:
        valid_id = int(identity) > 0
    except ValueError:
        return False
    return valid_id and any(isinstance(row.get(key), str) and row[key].strip()
                            for key in ("handle", "username", "name", "display_name"))


def normalize_candidate_pool(value: Any) -> dict[str, Any]:
    """Do not turn a failed/unknown source with residual rows into ready."""
    if not isinstance(value, dict):
        return {"items": [], "status": "malformed"}
    if "status" in value:
        status = str(value.get("status") or "unknown").strip().lower()
    else:
        status = "ready" if _projection_available(value.get("projection")) else "unknown"
    if status not in _SOURCE_OK:
        return {"items": [], "status": status}
    if "projection" in value and not _projection_available(value["projection"]):
        return {"items": [], "status": "unknown"}
    items = value.get("items")
    if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
        return {"items": [], "status": "malformed"}
    valid = [row for row in items if _candidate_identity_available(row)]
    if len(valid) != len(items):
        return {"items": valid, "status": "malformed"}
    return {"items": valid, "status": "ready" if valid else "empty"}


def normalize_market_signals(value: Any) -> dict[str, Any]:
    """Keep metadata but discard unusable content before building rule angles."""
    if not isinstance(value, dict):
        return {"status": "malformed", "sections": {}, "coverage": "0/5"}
    result = dict(value)
    result["sections"] = {}
    if str(value.get("status") or "").strip().lower() not in _SOURCE_OK:
        return result
    sections = value.get("sections")
    if not isinstance(sections, dict):
        result["status"] = "malformed"
        return result
    for name in _SIGNAL_SECTIONS:
        section = sections.get(name)
        if not isinstance(section, dict):
            continue
        status = str(section.get("data_status") or "").strip().lower()
        items = section.get("items")
        if status not in _MATERIAL_STATES or not isinstance(items, list):
            continue
        valid = [row for row in items if isinstance(row, dict)
                 and any(isinstance(row.get(key), str) and row[key].strip() for key in _SIGNAL_FIELDS[name])
                 and str(row.get("data_status") or "").strip().lower() in _MATERIAL_STATES]
        result["sections"][name] = {**section, "items": valid}
    return result


def build_planning_readiness(ctx: dict[str, Any]) -> dict[str, Any]:
    """Compute before invoking a model; all three states stay unapproved."""
    gaps: list[dict[str, str]] = []
    next_steps: list[dict[str, str]] = []

    def add(code: str, message: str, title: str, reason: str) -> None:
        gaps.append({"code": code, "message": message})
        next_steps.append({"code": code, "title": title, "reason": reason})

    if ctx.get("budget_cents", 0) <= 0:
        add("budget_required", "规划预算未确认或不大于零。", "确认规划预算", "填写预算用于草案分配，不代表获批支出。")
    if ctx.get("goal") not in {"awareness", "conversion", "launch"}:
        add("goal_unsupported", "当前目标不属于支持的规划目标。", "确认营销目标", "请选择 awareness、conversion 或 launch。")
    needs_inputs = bool(gaps)
    if ctx.get("pool_status") != "ready":
        add("creator_evidence_missing", "候选创作者材料为空、未知、异常或格式不完整。", "补充候选创作者材料", "先在现有 KOL 检索/资料页确认候选与产品、市场的关联，再重新生成；不自动抓取。")
    signals = ctx.get("signals") or {}
    sections = signals.get("sections") or {}
    if not any((sections.get(name) or {}).get("items") for name in _SIGNAL_SECTIONS):
        add("market_evidence_missing", "尚无可供本草案引用的市场信号材料。", "补充市场信号材料", "在现有市场简报中核对来源、时间和目标范围，再重新生成；不自动刷新或付费调用。")
    status = "needs_inputs" if needs_inputs else "needs_evidence" if gaps else "draft_for_review"
    if not gaps:
        next_steps.append({
            "code": "human_review_required", "title": "人工核对草案与证据",
            "reason": "已有材料不等于匹配已验证。请核对范围、来源、候选档期和预算；草案不能直接执行。",
        })
    return {
        "status": status, "executable": False, "approval_status": "not_requested",
        "claim_status": "descriptive_only", "gaps": gaps, "next_steps": next_steps,
    }


def _complete_next_steps(value: Any) -> bool:
    return (
        isinstance(value, list) and bool(value)
        and all(isinstance(step, dict) and all(
            isinstance(step.get(key), str) and bool(step[key].strip())
            for key in ("code", "title", "reason")
        ) for step in value)
    )


def campaign_readiness_reviewable(output: dict[str, Any]) -> bool:
    """Respect the new contract without reclassifying legacy ledger records."""
    if "planning_readiness" not in output:
        return True
    readiness = output["planning_readiness"]
    return (
        isinstance(readiness, dict)
        and readiness.get("status") == "draft_for_review"
        and readiness.get("executable") is False
        and readiness.get("approval_status") == "not_requested"
        and readiness.get("claim_status") == "descriptive_only"
        and readiness.get("gaps") == []
        and _complete_next_steps(readiness.get("next_steps"))
    )
