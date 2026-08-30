"""Behavior locks for final-v1 video enqueue orchestration."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.domains.kol import video_analysis_enqueue as enqueue
from app.domains.kol import video_analysis_job_access
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/app/domains/kol/video_analysis_enqueue.py"


class _Conn:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def execute(self, _sql: str, _params: Any = None) -> None:
        self.events.append("capability_update")

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")


def _allowed_budget() -> dict[str, Any]:
    return {
        "allowed": True,
        "reason": "provider_calls_allowed",
        "ready_models": ["gemini-test"],
        "model_readiness_status": "production_ready",
        "preflight": {"raw": True},
    }


def _patch_common(monkeypatch: pytest.MonkeyPatch, events: list[str], fit_values: list[int]) -> None:
    evidence = {
        "evidence_type": "video", "media_kind": "video",
        "content_url": "https://www.youtube.com/watch?v=abcdefghijk", "title": "Review",
        "view_count": 10, "duration_seconds": 20, "kol_handle": "creator",
    }
    monkeypatch.setattr(enqueue, "_load_owned_evidence", lambda *_a, **_k: events.append("evidence") or evidence)
    monkeypatch.setattr(enqueue, "_ready_cache", lambda *_a, **_k: events.append("cache") or None)
    monkeypatch.setattr(enqueue.llm_gateway, "budget_preflight", lambda *_a, **_k: events.append("preflight") or {})
    monkeypatch.setattr(enqueue, "_google_budget", lambda *_a, **_k: events.append("budget") or _allowed_budget())
    monkeypatch.setattr(enqueue, "_fit_snapshot", lambda *_a, **_k: events.append("fit") or fit_values.pop(0))

    def lineage(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        events.append("lineage:empty" if not payload else "lineage:payload")
        return dict(payload)

    monkeypatch.setattr(enqueue, "with_search_session_lineage", lineage)
    monkeypatch.setattr(video_analysis_job_access, "video_analysis_authorization_scope", lambda _payload: events.append("scope") or "scope")
    monkeypatch.setattr(enqueue, "active_job_idempotency_key", lambda *_a: events.append("idempotency") or "job-key")
    monkeypatch.setattr(enqueue, "_active_job", lambda *_a, **_k: events.append("active") or None)
    monkeypatch.setattr(
        enqueue, "enqueue_active_apify_job",
        lambda *_a, **_k: events.append("enqueue") or ({"id": 51, "status": "queued"}, True),
    )
    monkeypatch.setattr(enqueue, "redact_local_evaluation_capability", lambda row: events.append("redact") or row)


def test_enqueue_preserves_budget_authorization_fit_commit_and_redaction_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    _patch_common(monkeypatch, events, [88, 88])
    result = enqueue._enqueue_final_v1_video_analysis(_Conn(events), kol_pool_id=7, evidence_id=9)

    assert events == [
        "evidence", "lineage:empty", "cache", "preflight", "budget", "fit",
        "lineage:payload", "scope", "idempotency", "active", "enqueue", "fit", "commit", "redact",
    ]
    assert result["status"] == "queued"
    assert result["write_db"] is True
    assert result["writes"] == ["apify_jobs"]
    assert result["viltrox_fit_score_changed_ids"] == []


def test_enqueue_rolls_back_before_raising_when_fit_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    _patch_common(monkeypatch, events, [88, 89])
    with pytest.raises(RuntimeError, match=r"viltrox_fit_score_changed_ids=\[7\]; rolled back"):
        enqueue._enqueue_final_v1_video_analysis(_Conn(events), kol_pool_id=7, evidence_id=9)
    assert events[-2:] == ["fit", "rollback"]
    assert "commit" not in events


def test_local_capability_failure_rolls_back_and_preserves_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    _patch_common(monkeypatch, events, [88])
    error = ValueError("signing failed")

    def fail(**_kwargs: Any) -> dict[str, Any]:
        events.append("sign")
        raise error

    monkeypatch.setattr(enqueue, "issue_local_evaluation_capability", fail)
    with pytest.raises(ValueError, match="signing failed") as caught:
        enqueue._enqueue_final_v1_video_analysis(
            _Conn(events), kol_pool_id=7, evidence_id=9, local_evaluation=True,
        )
    assert caught.value is error
    assert events[-2:] == ["sign", "rollback"]
    assert "capability_update" not in events
    assert "commit" not in events


def test_video_enqueue_complexity_and_module_size_are_bounded() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert len(source.splitlines()) < 800
    rows = collect_complexity({str(SOURCE): ast.parse(source)})
    focal = next(row for row in rows if row.qualified_name == "_enqueue_final_v1_video_analysis")
    assert focal.cc <= 30
