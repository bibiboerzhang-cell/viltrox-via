"""Read-only dashboard helpers for runtime/ops agent artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn


DASHBOARD_AGENT_SPECS = [
    {"id": "recommendation", "name": "推荐 Agent", "pattern": "*p7-82-recommendation-agent-v0.json"},
    {"id": "brief", "name": "简报 Agent", "pattern": "*p7-83-brief-agent-v0.json"},
    {"id": "evidence", "name": "证据 Agent", "pattern": "*p7-81-evidence-agent-v0.json"},
    {"id": "sync_sentinel", "name": "同步守护", "pattern": "*p7-80-sync-sentinel-agent-v0.json"},
    {"id": "brain", "name": "脑层验收", "pattern": "*p6-79-brain-layer-acceptance-v0.json"},
    {"id": "market_intel", "name": "市场情报", "pattern": "*p5-69-market-intelligence-v0*.json"},
]


def _latest_dashboard_agent_artifact(ops_dir: str, pattern: str) -> Path | None:
    root = Path(ops_dir)
    if not root.exists() or not root.is_dir():
        return None
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)[0]


def _load_dashboard_agent_json(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _artifact_mtime_iso(path: Path | None) -> str | None:
    if not path:
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _agent_summary(agent_id: str, payload: dict[str, Any], kol_pool_total: int) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if agent_id == "recommendation":
        return f"{int(summary.get('candidate_count') or 0)} 个候选 · {summary.get('agent_status') or 'ready'}"
    if agent_id == "brief":
        return str(summary.get("headline") or f"{int(summary.get('brief_item_count') or 0)} 条简报项")
    if agent_id == "evidence":
        return f"{int(summary.get('chain_count') or 0)} 条证据链 · {int(summary.get('evidence_ref_count') or 0)} 条 evidence"
    if agent_id == "sync_sentinel":
        return f"{int(summary.get('signal_count') or 0)} 条同步信号 · {int(summary.get('critical_count') or 0)} 个 critical"
    if agent_id == "brain":
        return "技术验收通过" if summary.get("technical_acceptance_passed") else "等待验收"
    if agent_id == "market_intel":
        return f"{int(summary.get('signals_loaded') or 0)} 条市场信号 · {int(summary.get('high_priority') or 0)} 条高优先级"
    if agent_id == "kol_intel":
        return f"{kol_pool_total} 个 KOL 已入池"
    return "状态已读取"


def _agent_inbox_type(agent_id: str) -> str:
    return {
        "recommendation": "recommendation",
        "brief": "brief",
        "evidence": "evidence",
        "sync_sentinel": "sync",
        "brain": "brain",
        "market_intel": "market",
    }.get(agent_id, "agent")


def _agent_inbox_title(agent_id: str, agent_name: str, payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if agent_id == "brief":
        return str(summary.get("headline") or "Brief Agent 输出")
    if agent_id == "recommendation":
        return f"推荐 Agent · {int(summary.get('candidate_count') or 0)} 个候选"
    if agent_id == "evidence":
        return f"证据 Agent · {int(summary.get('chain_count') or 0)} 条证据链"
    if agent_id == "sync_sentinel":
        return f"同步守护 · {summary.get('sentinel_status') or 'ready'}"
    if agent_id == "brain":
        return "脑层验收 · 技术通过" if summary.get("technical_acceptance_passed") else "脑层验收 · 待确认"
    if agent_id == "market_intel":
        return f"市场情报 · {int(summary.get('signals_loaded') or 0)} 条信号"
    return f"{agent_name} 输出"


def _dashboard_agent_inbox_item(spec: dict[str, str], path: Path) -> dict[str, Any] | None:
    payload = _load_dashboard_agent_json(path)
    if not payload:
        return None
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    passed = payload.get("passed")
    status = "active" if passed is not False else "warning"
    next_steps = payload.get("next_steps") if isinstance(payload.get("next_steps"), list) else []
    return {
        "id": path.stem,
        "agent_id": spec["id"],
        "agent_name": spec["name"],
        "type": _agent_inbox_type(spec["id"]),
        "title": _agent_inbox_title(spec["id"], spec["name"], payload),
        "summary": _agent_summary(spec["id"], payload, kol_pool_total=0),
        "status": status,
        "passed": bool(passed) if passed is not None else False,
        "mode": payload.get("mode") or "",
        "generated_at": payload.get("generated_at") or None,
        "last_run_at": _artifact_mtime_iso(path),
        "artifact_name": path.name,
        "source": "runtime/ops",
        "details": {
            "summary": summary,
            "next_steps": [str(step) for step in next_steps[:5]],
        },
    }


def _build_dashboard_agents_inbox(
    ops_dir: str = "runtime/ops",
    limit: int = 50,
    agent_id: str | None = None,
) -> dict[str, Any]:
    root = Path(ops_dir)
    items: list[dict[str, Any]] = []
    normalized_agent_id = str(agent_id or "").strip() or None
    if root.exists() and root.is_dir():
        for spec in DASHBOARD_AGENT_SPECS:
            if normalized_agent_id and spec["id"] != normalized_agent_id:
                continue
            for path in root.glob(spec["pattern"]):
                if not path.is_file():
                    continue
                item = _dashboard_agent_inbox_item(spec, path)
                if item:
                    item["_mtime"] = path.stat().st_mtime
                    items.append(item)
    items.sort(key=lambda item: (float(item.get("_mtime") or 0), str(item.get("artifact_name") or "")), reverse=True)
    for item in items:
        item.pop("_mtime", None)
    bounded_limit = max(1, min(100, int(limit or 50)))
    return {
        "items": items[:bounded_limit],
        "total": len(items),
        "limit": bounded_limit,
        "agent_id": normalized_agent_id,
        "ops_dir": ops_dir,
        "is_real": True,
        "source": "runtime/ops",
    }


def _build_dashboard_agents_status(ops_dir: str = "runtime/ops", kol_pool_total: int = 0) -> dict[str, Any]:
    agents: list[dict[str, Any]] = []
    for spec in DASHBOARD_AGENT_SPECS:
        path = _latest_dashboard_agent_artifact(ops_dir, spec["pattern"])
        payload = _load_dashboard_agent_json(path)
        loaded = bool(path and payload)
        passed = payload.get("passed")
        status = "active" if loaded and passed is not False else "warning" if loaded else "idle"
        agents.append({
            "id": spec["id"],
            "name": spec["name"],
            "status": status,
            "last_output": path.name if path else "",
            "last_run_at": _artifact_mtime_iso(path),
            "summary": _agent_summary(spec["id"], payload, kol_pool_total) if loaded else "暂无 runtime/ops 输出",
            "mode": payload.get("mode") or "",
            "passed": bool(passed) if passed is not None else False,
        })
    agents.append({
        "id": "kol_intel",
        "name": "KOL 智能",
        "status": "active" if kol_pool_total else "idle",
        "last_output": "vkpi_kol_pool",
        "last_run_at": None,
        "summary": _agent_summary("kol_intel", {}, kol_pool_total),
        "mode": "read_only_kol_intelligence_card_v0",
        "passed": bool(kol_pool_total),
    })
    active_count = sum(1 for agent in agents if agent["status"] == "active")
    return {
        "agents": agents,
        "active_count": active_count,
        "total": len(agents),
        "ops_dir": ops_dir,
        "is_real": True,
        "source": "runtime/ops + vkpi_kol_pool",
    }


def build_dashboard_agents_status(ops_dir: str = "runtime/ops") -> dict[str, Any]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()
        kol_pool_total = int((row or {})["n"] or 0)
    except Exception:
        kol_pool_total = 0
    return _build_dashboard_agents_status(ops_dir=ops_dir, kol_pool_total=kol_pool_total)


def _build_dashboard_copilot_brief(ops_dir: str = "runtime/ops") -> dict[str, Any]:
    path = _latest_dashboard_agent_artifact(ops_dir, "*p7-83-brief-agent-v0.json")
    payload = _load_dashboard_agent_json(path)
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    items = payload.get("brief_items") if isinstance(payload.get("brief_items"), list) else []
    actions = payload.get("next_actions") if isinstance(payload.get("next_actions"), list) else []
    headline = str(summary.get("headline") or "暂无 Copilot Brief")
    return {
        "headline": headline,
        "summary": summary,
        "items": items[:5],
        "actions": actions[:5],
        "last_output": path.name if path else "",
        "last_run_at": _artifact_mtime_iso(path),
        "mode": payload.get("mode") or "",
        "is_real": bool(path and payload),
        "source": "runtime/ops p7-83 brief agent",
    }


def _build_dashboard_tasks(ops_dir: str = "runtime/ops", limit: int = 6) -> dict[str, Any]:
    path = _latest_dashboard_agent_artifact(ops_dir, "*p7-82-recommendation-agent-v0.json")
    payload = _load_dashboard_agent_json(path)
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    next_steps = payload.get("next_steps") if isinstance(payload.get("next_steps"), list) else []
    tasks: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates[: max(1, min(20, int(limit or 6)))]):
        item = candidate if isinstance(candidate, dict) else {}
        confidence = float(item.get("confidence") or item.get("score") or 0)
        priority = "high" if confidence >= 0.75 else "mid" if confidence >= 0.45 else "low"
        title = str(item.get("title") or item.get("decision") or item.get("kol_handle") or f"推荐候选 {index + 1}")
        body = str(item.get("reason") or item.get("recommendation_reason") or item.get("summary") or "等待人工复核")
        tasks.append({
            "id": str(item.get("candidate_id") or item.get("id") or f"rec-{index + 1}"),
            "title": title,
            "body": body,
            "priority": priority,
            "confidence": confidence,
            "source": "recommendation_agent_v0",
            "is_real": True,
        })
    return {
        "tasks": tasks,
        "next_steps": [str(step) for step in next_steps[:5]],
        "candidate_count": len(candidates),
        "last_output": path.name if path else "",
        "last_run_at": _artifact_mtime_iso(path),
        "mode": payload.get("mode") or "",
        "is_real": bool(path and payload),
        "source": "runtime/ops p7-82 recommendation agent",
    }
