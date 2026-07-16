from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from app.domains.recommendations import feature_store


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _Conn:
    def __init__(self, row: dict[str, Any] | list[dict[str, Any]] | None) -> None:
        self.rows = row if isinstance(row, list) else ([row] if row else [])
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        self.calls.append((sql, params))
        return _Cursor(self.rows)


def _frozen_row(
    *,
    created_at: Any = "2026-07-10T12:00:00Z",
    snapshot_at: str | None = "2026-07-10T11:59:00Z",
    launch_id: int | None = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {"followers": 1200, "platform": "youtube"}
    snapshot.update(
        {
            "kol_pool_id": 7,
            "launch_id": launch_id,
            "posts_count": 20,
            "avg_views": 100,
            "avg_likes": 10,
            "avg_comments": 2,
            "engagement_rate": 0.12,
            "primary_topic": "camera",
            "sync_status": "ready",
        }
    )
    if snapshot_at is not None:
        snapshot["snapshot_at"] = snapshot_at
    return {
        "id": 41,
        "launch_id": launch_id,
        "created_at": created_at,
        "feature_snapshot_json": json.dumps(snapshot),
    }


def test_current_request_remains_live_but_is_not_labelled_point_in_time(monkeypatch) -> None:
    monkeypatch.setattr(
        feature_store,
        "snapshot_features",
        lambda **kwargs: {"followers": 9999, "kol_pool_id": kwargs["kol_pool_id"]},
    )
    monkeypatch.setattr(
        feature_store,
        "get_conn",
        lambda: pytest.fail("current snapshot must not query recommendation history"),
    )

    result = feature_store.get_features_at_time(7)

    assert result["followers"] == 9999
    assert result["requested_at"] == "current"
    assert result["_point_in_time"] == {
        "status": "current_not_historical",
        "point_in_time": False,
        "source": "live_kol_snapshot",
        "entity_scope": "kol_only",
    }


def test_historical_request_returns_latest_frozen_snapshot_with_provenance(monkeypatch) -> None:
    conn = _Conn(_frozen_row(created_at=datetime(2026, 7, 10, 12, tzinfo=timezone.utc)))
    monkeypatch.setattr(feature_store, "get_conn", lambda: conn)
    monkeypatch.setattr(
        feature_store,
        "snapshot_features",
        lambda **_kwargs: pytest.fail("historical request must never read current features"),
    )

    result = feature_store.get_features_at_time(7, "2026-07-10T08:30:00-04:00")

    assert result["followers"] == 1200
    assert result["requested_at"] == "2026-07-10T12:30:00Z"
    point_in_time = dict(result["_point_in_time"])
    snapshot_sha256 = point_in_time.pop("snapshot_sha256")
    assert len(snapshot_sha256) == 64
    assert point_in_time == {
        "status": "historical_frozen_snapshot",
        "point_in_time": True,
        "source": "vkpi_kol_recommendations.feature_snapshot_json",
        "source_row_id": 41,
        "source_launch_id": None,
        "entity_scope": "kol_only",
        "recorded_at": "2026-07-10T12:00:00Z",
        "snapshot_at": "2026-07-10T11:59:00Z",
        "storage_mutability": "application_convention_not_db_enforced",
        "feature_schema_version": "legacy_unversioned_standard",
        "candidate_rows_examined": 1,
        "candidate_rows_rejected": 0,
        "candidate_limit": 1000,
    }
    sql, params = conn.calls[0]
    assert "created_at<=?" not in sql
    assert "ORDER BY id DESC" in sql
    assert params == (7, 1001)


def test_missing_historical_snapshot_fails_closed_without_current_fallback(monkeypatch) -> None:
    conn = _Conn(None)
    monkeypatch.setattr(feature_store, "get_conn", lambda: conn)
    monkeypatch.setattr(
        feature_store,
        "snapshot_features",
        lambda **_kwargs: pytest.fail("missing history must not fall back to current features"),
    )

    with pytest.raises(feature_store.HistoricalFeatureSnapshotUnavailable):
        feature_store.get_features_at_time(7, "2026-07-10T12:30:00Z")


@pytest.mark.parametrize(
    "timestamp",
    ["2026-07-10T12:30:00", "not-a-time", ""],
)
def test_historical_timestamp_must_be_explicit_and_timezone_aware(monkeypatch, timestamp: str) -> None:
    if timestamp == "":
        monkeypatch.setattr(feature_store, "snapshot_features", lambda **_kwargs: {"ok": True})
        assert feature_store.get_features_at_time(7, timestamp)["requested_at"] == "current"
        return
    monkeypatch.setattr(
        feature_store,
        "get_conn",
        lambda: pytest.fail("invalid timestamp must fail before querying"),
    )
    with pytest.raises(ValueError):
        feature_store.get_features_at_time(7, timestamp)


def test_snapshot_with_future_internal_timestamp_is_rejected(monkeypatch) -> None:
    conn = _Conn(_frozen_row(snapshot_at="2026-07-11T00:00:00Z"))
    monkeypatch.setattr(feature_store, "get_conn", lambda: conn)

    with pytest.raises(
        feature_store.HistoricalFeatureSnapshotUnavailable,
        match="schema-compatible",
    ):
        feature_store.get_features_at_time(7, "2026-07-10T12:30:00Z")


def test_invalid_or_empty_frozen_snapshot_is_rejected(monkeypatch) -> None:
    conn = _Conn({"id": 41, "created_at": "2026-07-10T12:00:00Z", "feature_snapshot_json": "{}"})
    monkeypatch.setattr(feature_store, "get_conn", lambda: conn)

    with pytest.raises(feature_store.HistoricalFeatureSnapshotUnavailable, match="schema-compatible"):
        feature_store.get_features_at_time(7, "2026-07-10T12:30:00Z")


def test_heterogeneous_latest_row_is_skipped_for_earlier_standard_snapshot(monkeypatch) -> None:
    heterogeneous = {
        "id": 42,
        "created_at": "2026-07-10T12:10:00Z",
        "feature_snapshot_json": json.dumps(
            {"scenario": "new_launch_match", "product_query": "lens"}
        ),
    }
    conn = _Conn([heterogeneous, _frozen_row()])
    monkeypatch.setattr(feature_store, "get_conn", lambda: conn)

    result = feature_store.get_features_at_time(7, "2026-07-10T12:30:00Z")

    assert result["followers"] == 1200
    assert result["_point_in_time"]["source_row_id"] == 41
    assert result["_point_in_time"]["candidate_rows_examined"] == 2
    assert result["_point_in_time"]["candidate_rows_rejected"] == 1


def test_mixed_timezone_offsets_are_compared_as_instants_in_python(monkeypatch) -> None:
    older = _frozen_row(
        created_at="2026-07-10T14:00:00+02:00",
        snapshot_at="2026-07-10T13:59:00+02:00",
    )
    older["id"] = 40
    newer = _frozen_row(
        created_at="2026-07-10T08:15:00-04:00",
        snapshot_at="2026-07-10T08:14:00-04:00",
    )
    newer["id"] = 41
    conn = _Conn([older, newer])
    monkeypatch.setattr(feature_store, "get_conn", lambda: conn)

    result = feature_store.get_features_at_time(7, "2026-07-10T12:20:00Z")

    assert result["_point_in_time"]["source_row_id"] == 41
    assert result["_point_in_time"]["recorded_at"] == "2026-07-10T12:15:00Z"


def test_feature_freshness_wins_over_later_database_record_time(monkeypatch) -> None:
    fresher = _frozen_row(
        created_at="2026-07-10T12:00:00Z",
        snapshot_at="2026-07-10T11:59:00Z",
    )
    fresher["id"] = 40
    stale_backfill = _frozen_row(
        created_at="2026-07-10T12:30:00Z",
        snapshot_at="2026-07-10T10:00:00Z",
    )
    stale_backfill["id"] = 41
    conn = _Conn([stale_backfill, fresher])
    monkeypatch.setattr(feature_store, "get_conn", lambda: conn)

    result = feature_store.get_features_at_time(7, "2026-07-10T12:45:00Z")

    assert result["_point_in_time"]["source_row_id"] == 40
    assert result["_point_in_time"]["snapshot_at"] == "2026-07-10T11:59:00Z"


def test_corrupt_snapshot_entity_id_is_rejected_as_domain_miss(monkeypatch) -> None:
    row = _frozen_row()
    snapshot = json.loads(row["feature_snapshot_json"])
    snapshot["kol_pool_id"] = "not-an-id"
    row["feature_snapshot_json"] = json.dumps(snapshot)
    monkeypatch.setattr(feature_store, "get_conn", lambda: _Conn(row))

    with pytest.raises(feature_store.HistoricalFeatureSnapshotUnavailable):
        feature_store.get_features_at_time(7, "2026-07-10T12:30:00Z")


def test_non_finite_snapshot_entity_id_is_rejected_as_domain_miss(monkeypatch) -> None:
    row = _frozen_row()
    snapshot = json.loads(row["feature_snapshot_json"])
    snapshot["kol_pool_id"] = float("inf")
    row["feature_snapshot_json"] = snapshot
    monkeypatch.setattr(feature_store, "get_conn", lambda: _Conn(row))

    with pytest.raises(feature_store.HistoricalFeatureSnapshotUnavailable):
        feature_store.get_features_at_time(7, "2026-07-10T12:30:00Z")


def test_candidate_window_truncation_fails_closed(monkeypatch) -> None:
    rows = []
    for index in range(1001):
        row = _frozen_row()
        row["id"] = index + 1
        rows.append(row)
    monkeypatch.setattr(feature_store, "get_conn", lambda: _Conn(rows))

    with pytest.raises(
        feature_store.HistoricalFeatureSnapshotUnavailable,
        match="candidate window is incomplete",
    ):
        feature_store.get_features_at_time(7, "2026-07-10T12:30:00Z")


def test_launch_scoped_history_rejects_other_launch_and_preserves_launch_features(monkeypatch) -> None:
    launch_one = _frozen_row(
        created_at="2026-07-10T12:10:00Z",
        snapshot_at="2026-07-10T12:09:00Z",
        launch_id=1,
    )
    launch_one["id"] = 42
    launch_two = _frozen_row(launch_id=2)
    snapshot = json.loads(launch_two["feature_snapshot_json"])
    snapshot["launch"] = {"product_sku": "AF-TEST"}
    launch_two["feature_snapshot_json"] = json.dumps(snapshot)
    conn = _Conn([launch_one, launch_two])
    monkeypatch.setattr(feature_store, "get_conn", lambda: conn)

    result = feature_store.get_features_at_time(
        7,
        "2026-07-10T12:30:00Z",
        launch_id=2,
    )

    assert result["launch"] == {"product_sku": "AF-TEST"}
    assert result["launch_id"] == 2
    assert result["_point_in_time"]["entity_scope"] == "kol_launch"
    assert result["_point_in_time"]["source_launch_id"] == 2
    sql, params = conn.calls[0]
    assert "AND launch_id=?" in sql
    assert params == (7, 2, 1001)


def test_kol_only_history_does_not_leak_an_unrequested_launch_context(monkeypatch) -> None:
    row = _frozen_row(launch_id=2)
    snapshot = json.loads(row["feature_snapshot_json"])
    snapshot["launch"] = {"product_sku": "AF-TEST"}
    snapshot["matched_catalog_products"] = [{"sku": "AF-TEST"}]
    row["feature_snapshot_json"] = json.dumps(snapshot)
    monkeypatch.setattr(feature_store, "get_conn", lambda: _Conn(row))

    result = feature_store.get_features_at_time(7, "2026-07-10T12:30:00Z")

    assert result["_point_in_time"]["entity_scope"] == "kol_only"
    assert "launch" not in result
    assert "launch_id" not in result
    assert "matched_catalog_products" not in result


def test_launch_scope_must_be_positive() -> None:
    with pytest.raises(ValueError, match="launch_id"):
        feature_store.get_features_at_time(7, "2026-07-10T12:30:00Z", launch_id=0)


@pytest.mark.parametrize(
    "bad_row",
    [
        _frozen_row(created_at="not-a-time"),
        _frozen_row(snapshot_at="not-a-time"),
        _frozen_row(
            created_at="2026-07-10T12:00:00Z",
            snapshot_at="2026-07-10T12:01:00Z",
        ),
    ],
)
def test_bad_stored_time_is_a_domain_miss_not_a_user_timestamp_error(
    monkeypatch, bad_row: dict[str, Any]
) -> None:
    conn = _Conn(bad_row)
    monkeypatch.setattr(feature_store, "get_conn", lambda: conn)

    with pytest.raises(feature_store.HistoricalFeatureSnapshotUnavailable):
        feature_store.get_features_at_time(7, "2026-07-10T12:30:00Z")


def test_positive_kol_pool_id_is_required() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        feature_store.get_features_at_time(0, "2026-07-10T12:30:00Z")


def test_recommendation_public_exports_resolve_in_a_clean_process() -> None:
    root = Path(__file__).resolve().parents[1]
    names = [
        "BUDGET_SCOPE",
        "FORBIDDEN_WRITE_FLAGS",
        "build_new_launch_match_preview",
        "build_project_next_action_preview",
        "format_preview_summary",
        "format_project_next_action_summary",
        "render_markdown",
    ]
    code = (
        "import app.domains.recommendations as package; "
        f"names={names!r}; "
        "assert all(getattr(package, name) is not None for name in names)"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "backend")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
