from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from app.core.constants import MARKETING_DIMS, TECH_DIMS
from app.services.scoring import verticals


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Conn:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.writes: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        if "FROM submissions" in sql:
            return _Cursor(self.rows)
        self.writes.append((sql, params))
        return _Cursor([])

    def commit(self) -> None:
        self.commits += 1


class _FailingWriteConn(_Conn):
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        if "FROM submissions" in sql:
            return _Cursor(self.rows)
        raise RuntimeError("synthetic persistence failure")


def _row(
    index: int,
    *,
    created_at: datetime | None = None,
    score: float = 5.0,
    views: int = 1000,
    likes: int = 10,
    comments: int = 0,
    shares: int = 0,
) -> dict[str, Any]:
    quality_scores = {dimension: score for dimension in (*TECH_DIMS, *MARKETING_DIMS)}
    return {
        "id": index,
        "created_at": created_at or datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index),
        "video_analysis": json.dumps({"quality_scores": quality_scores}),
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "detection_status": "confirmed",
        "tech_score": score,
        "marketing_score": score,
    }


def _promoted_artifact() -> dict[str, Any]:
    tech = deepcopy(verticals._RULE_V0_VERTICAL_WEIGHTS["default"]["tech"])
    mkt = deepcopy(verticals._RULE_V0_VERTICAL_WEIGHTS["default"]["mkt"])
    tech[TECH_DIMS[0]] += 1
    tech[TECH_DIMS[1]] -= 1
    mkt[MARKETING_DIMS[0]] += 1
    mkt[MARKETING_DIMS[1]] -= 1
    return {
        "status": "trained",
        "trained": True,
        "persisted": True,
        "promoted": True,
        "vertical": "review",
        "samples": 40,
        "learned_at": "2026-07-13T12:00:00Z",
        "tech": tech,
        "mkt": mkt,
        "training_audit": {
            "version": verticals.VERTICAL_TRAINING_GATE_VERSION,
            "status": "ready",
            "claimable": True,
            "reasons": [],
            "policy": {"effective_min_valid_samples": 30},
            "facts": {
                "valid_samples": 40,
                "train_samples": 32,
                "holdout_samples": 8,
                "distinct_targets": 40,
                "positive_targets": 40,
                "train_distinct_targets": 32,
                "holdout_distinct_targets": 8,
                "train_tech_feature_variants": 32,
                "train_mkt_feature_variants": 32,
                "time_span_hours": 936,
                "strict_time_split": True,
            },
            "holdout": {
                "status": "passed",
                "reasons": [],
                "baseline_mae": 1.0,
                "tech_mae": 0.5,
                "mkt_mae": 0.5,
            },
        },
    }


def test_one_zero_outcome_cannot_bypass_hard_minimum(monkeypatch) -> None:
    conn = _Conn([_row(1, likes=0, comments=0, shares=0)])
    monkeypatch.setattr(verticals, "get_conn", lambda: conn)

    result = verticals.learn_vertical_weights("review", min_samples=1, return_audit=True)

    assert result is not None
    assert result["status"] == "skipped"
    assert result["trained"] is False
    assert "valid_samples<30" in result["training_audit"]["reasons"]
    assert "positive_targets<5" in result["training_audit"]["reasons"]
    assert result["training_audit"]["policy"]["effective_min_valid_samples"] == 30
    assert result["training_audit"]["facts"]["zero_targets"] == 1
    assert conn.writes == []
    assert conn.commits == 0


def test_default_skip_result_preserves_legacy_none_contract(monkeypatch) -> None:
    conn = _Conn([_row(1)])
    monkeypatch.setattr(verticals, "get_conn", lambda: conn)

    assert verticals.learn_vertical_weights("review", min_samples=1) is None


def test_constant_outcome_and_features_are_auditable_blockers(monkeypatch) -> None:
    conn = _Conn([_row(index, score=5, likes=10) for index in range(30)])
    monkeypatch.setattr(verticals, "get_conn", lambda: conn)

    result = verticals.learn_vertical_weights("review", return_audit=True)

    reasons = result["training_audit"]["reasons"]
    assert "distinct_targets<5" in reasons
    assert "train_tech_feature_variants<2" in reasons
    assert "train_mkt_feature_variants<2" in reasons
    assert conn.writes == []


def test_chronological_split_must_have_a_strict_boundary(monkeypatch) -> None:
    same_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        _row(index, created_at=same_time, score=1 + (index % 10), likes=index + 1)
        for index in range(30)
    ]
    conn = _Conn(rows)
    monkeypatch.setattr(verticals, "get_conn", lambda: conn)

    result = verticals.learn_vertical_weights("review", return_audit=True)

    reasons = result["training_audit"]["reasons"]
    assert "time_span_hours<24" in reasons
    assert "chronological_split_not_strict" in reasons
    assert result["training_audit"]["facts"]["strict_time_split"] is False
    assert conn.writes == []


def test_passing_holdout_is_trained_and_persisted_with_audit(monkeypatch) -> None:
    rows = []
    for index in range(40):
        score = 1.0 + index / 5.0
        # A monotonic engagement-rate label that both feature axes can predict.
        rows.append(_row(index, score=score, views=1000, likes=10 + index * 3))
    conn = _Conn(rows)
    monkeypatch.setattr(verticals, "get_conn", lambda: conn)

    result = verticals.learn_vertical_weights("review", return_audit=True)

    assert result is not None
    assert result["status"] == "trained"
    assert result["trained"] is True
    assert result["promoted"] is True
    assert result["persisted"] is True
    assert result["samples"] == 40
    assert result["training_audit"]["status"] == "ready"
    assert result["training_audit"]["facts"]["train_samples"] == 32
    assert result["training_audit"]["facts"]["holdout_samples"] == 8
    assert result["training_audit"]["holdout"]["status"] == "passed"
    assert result["training_audit"]["holdout"]["tech_mae"] <= result["training_audit"]["holdout"]["baseline_mae"]
    assert result["training_audit"]["holdout"]["mkt_mae"] <= result["training_audit"]["holdout"]["baseline_mae"]
    assert len(conn.writes) == 1
    assert "INSERT INTO insights_cache" in conn.writes[0][0]
    persisted = json.loads(conn.writes[0][1][1])
    assert persisted["training_audit"]["version"] == "vertical_ridge_training_gate_v1"
    assert persisted["persisted"] is True
    assert persisted["promoted"] is True
    assert verticals.validate_learned_weights_artifact(persisted)["accepted"] is True
    assert conn.commits == 1


def test_holdout_worse_than_baseline_never_writes_weights(monkeypatch) -> None:
    rows = []
    for index in range(40):
        score = 1.0 + index / 5.0
        likes = 10 + index * 3 if index < 32 else 90 - (index - 32) * 10
        rows.append(_row(index, score=score, views=1000, likes=likes))
    conn = _Conn(rows)
    monkeypatch.setattr(verticals, "get_conn", lambda: conn)

    result = verticals.learn_vertical_weights("review", return_audit=True)

    assert result is not None
    assert result["status"] == "skipped"
    assert result["reason_code"] == "holdout_validation_blocked"
    assert result["training_audit"]["holdout"]["status"] == "blocked"
    assert {
        "tech_holdout_mae_worse_than_baseline",
        "mkt_holdout_mae_worse_than_baseline",
    }.issubset(result["training_audit"]["holdout"]["reasons"])
    assert conn.writes == []
    assert conn.commits == 0


def test_training_is_not_reported_as_promoted_when_persistence_fails(monkeypatch) -> None:
    rows = [
        _row(index, score=1.0 + index / 5.0, views=1000, likes=10 + index * 3)
        for index in range(40)
    ]
    conn = _FailingWriteConn(rows)
    monkeypatch.setattr(verticals, "get_conn", lambda: conn)

    result = verticals.learn_vertical_weights("review", return_audit=True)

    assert result is not None
    assert result["status"] == "trained_not_persisted"
    assert result["trained"] is True
    assert result["promoted"] is False
    assert result["persisted"] is False
    assert conn.commits == 0


def test_legacy_weight_cache_is_rejected_and_rule_v0_is_restored(monkeypatch) -> None:
    vertical = "__legacy_cache__"
    stale = deepcopy(verticals._RULE_V0_VERTICAL_WEIGHTS["default"])
    stale["tech"][TECH_DIMS[0]] += 5
    monkeypatch.setitem(verticals.VERTICAL_WEIGHTS, vertical, stale)
    legacy = {
        "tech": deepcopy(stale["tech"]),
        "mkt": deepcopy(stale["mkt"]),
    }
    monkeypatch.setattr(verticals, "load_learned_weights", lambda _vertical: legacy)

    result = verticals.apply_learned_weights(vertical)

    assert result["status"] == "rule_v0_fallback"
    assert result["applied"] is False
    assert result["reason"] == "artifact_not_trained"
    assert verticals.VERTICAL_WEIGHTS[vertical] == verticals._RULE_V0_VERTICAL_WEIGHTS["default"]


def test_unpromoted_artifact_is_rejected_with_diagnostic_reason(monkeypatch) -> None:
    vertical = "__unpromoted_cache__"
    artifact = _promoted_artifact()
    artifact["promoted"] = False
    monkeypatch.setattr(verticals, "load_learned_weights", lambda _vertical: artifact)

    result = verticals.apply_learned_weights(vertical)

    assert result["status"] == "rule_v0_fallback"
    assert result["reason"] == "artifact_not_promoted"
    assert result["artifact_gate"]["accepted"] is False


def test_artifact_with_invalid_holdout_is_rejected(monkeypatch) -> None:
    vertical = "__invalid_holdout__"
    artifact = _promoted_artifact()
    artifact["training_audit"]["holdout"] = {
        "status": "blocked",
        "reasons": ["tech_holdout_mae_worse_than_baseline"],
        "baseline_mae": 1.0,
        "tech_mae": 2.0,
        "mkt_mae": 0.5,
    }
    monkeypatch.setattr(verticals, "load_learned_weights", lambda _vertical: artifact)

    result = verticals.apply_learned_weights(vertical)

    assert result["status"] == "rule_v0_fallback"
    assert result["reason"] == "holdout_not_passed"
    assert verticals.VERTICAL_WEIGHTS[vertical] == verticals._RULE_V0_VERTICAL_WEIGHTS["default"]


def test_valid_promoted_artifact_is_the_only_runtime_apply_path(monkeypatch) -> None:
    vertical = "__valid_promoted__"
    artifact = _promoted_artifact()
    monkeypatch.setattr(verticals, "load_learned_weights", lambda _vertical: artifact)

    result = verticals.apply_learned_weights(vertical)

    assert result["status"] == "applied"
    assert result["applied"] is True
    assert result["reason"] == "promoted_artifact_accepted"
    assert result["artifact_gate"]["accepted"] is True
    assert verticals.VERTICAL_WEIGHTS[vertical]["tech"] == artifact["tech"]
    assert verticals.VERTICAL_WEIGHTS[vertical]["mkt"] == artifact["mkt"]
