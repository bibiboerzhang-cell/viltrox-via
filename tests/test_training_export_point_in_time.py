from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.domains.recommendations import training_export


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode("utf-8")


def _row(
    *,
    recommendation_id: int = 41,
    kol_pool_id: int = 7,
    launch_id: int | None = 2,
    created_at: str = "2026-07-10T12:00:00Z",
    snapshot_at: str = "2026-07-10T11:59:00Z",
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "feature_schema_version": training_export.feature_store.FEATURE_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_at": snapshot_at,
        "recommendation_id": recommendation_id,
        "kol_pool_id": kol_pool_id,
        "launch_id": launch_id,
        "platform": "youtube",
        "followers": 1200,
        "posts_count": 20,
        "avg_views": 100,
        "avg_likes": 10,
        "avg_comments": 2,
        "engagement_rate": 0.12,
        "primary_topic": "camera",
        "sync_status": "ready",
    }
    if launch_id is not None:
        snapshot["launch"] = {
            "product_sku": "AF-TEST",
            "product_name": "Frozen Test Lens",
        }
    return {
        "recommendation_id": recommendation_id,
        "recommendation_created_at": created_at,
        "kol_pool_id": kol_pool_id,
        "launch_id": launch_id,
        "recommendation_platform": "youtube",
        "recommendation_handle": "camera-test",
        "score": 88.5,
        "rank": 1,
        "recommendation_status": "recommended",
        "recommendation_feature_snapshot_json": json.dumps(snapshot),
        "recommendation_scoring_breakdown_json": json.dumps({"fit": 88.5}),
        "was_shortlisted": True,
        "outcome_finalized_at": "2026-07-12T00:00:00Z",
        "pool_handle": "camera-test",
        "pool_platform": "youtube",
        "product_sku": "AF-TEST",
        "product_name": "Test Lens",
    }


def _reader_for(row: dict[str, Any], **metadata_overrides: Any):
    snapshot = json.loads(row["recommendation_feature_snapshot_json"])
    digest = hashlib.sha256(
        json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    launch_id = row["launch_id"]
    metadata = {
        "status": "historical_frozen_snapshot",
        "point_in_time": True,
        "source": training_export.POINT_IN_TIME_SOURCE,
        "source_row_id": row["recommendation_id"],
        "source_launch_id": launch_id,
        "entity_scope": "kol_launch" if launch_id else "kol_only",
        "recorded_at": row["recommendation_created_at"],
        "snapshot_at": snapshot["snapshot_at"],
        "snapshot_sha256": digest,
        "storage_mutability": "application_convention_not_db_enforced",
        "feature_schema_version": snapshot["feature_schema_version"],
    }
    metadata.update(metadata_overrides)

    def reader(kol_pool_id: int, timestamp: str, *, launch_id: int | None = None) -> dict[str, Any]:
        assert kol_pool_id == row["kol_pool_id"]
        assert launch_id == row["launch_id"]
        if launch_id is None:
            features = {
                key: snapshot[key]
                for key in (
                    "feature_schema_version",
                    "snapshot_at",
                    "kol_pool_id",
                    "platform",
                    "followers",
                    "posts_count",
                    "avg_views",
                    "avg_likes",
                    "avg_comments",
                    "engagement_rate",
                    "primary_topic",
                    "sync_status",
                )
            }
        else:
            features = dict(snapshot)
        return features | {"requested_at": timestamp, "_point_in_time": metadata}

    return reader


def test_builder_produces_sha_bound_dataset_and_manifest() -> None:
    row = _row()
    built = training_export.build_point_in_time_training_dataset(
        [row],
        date_from="2026-07-01",
        date_to="2026-07-31",
        generated_at="2026-07-14T10:00:00-04:00",
        feature_reader=_reader_for(row),
    )

    dataset_bytes = built["dataset_bytes"]
    manifest = built["manifest"]
    assert manifest["dataset"] == {
        "format": "jsonl",
        "row_count": 1,
        "content_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "empty": False,
    }
    assert manifest["generated_at"] == "2026-07-14T14:00:00Z"
    assert manifest["scope"]["entity_ids"] == [7]
    assert manifest["scope"]["launch_ids"] == [2]
    assert manifest["scope"]["as_of_min"] == "2026-07-10T12:00:00Z"
    assert manifest["future_leakage_check"]["status"] == "passed"
    assert manifest["future_leakage_check"]["violations"] == 0
    assert manifest["field_contract"]["model_feature_path"] == "feature_snapshot"
    assert "scoring_breakdown" in manifest["field_contract"]["excluded_from_model_features"]
    assert "outcome" in manifest["field_contract"]["excluded_from_model_features"]
    assert manifest["readiness"] == {
        "point_in_time_features_proven": True,
        "feature_dataset_nonempty": True,
        "finalized_label_rows": 1,
        "unfinalized_label_rows": 0,
        "automated_model_training_enabled": False,
        "model_effect_proven": False,
    }
    payload_sha = manifest["manifest_payload_sha256"]
    payload_without_sha = dict(manifest)
    del payload_without_sha["manifest_payload_sha256"]
    assert payload_sha == hashlib.sha256(_canonical(payload_without_sha)).hexdigest()
    record = json.loads(dataset_bytes)
    assert record["decision_at"] == "2026-07-10T12:00:00Z"
    assert record["feature_lineage"]["source_row_id"] == 41
    assert record["feature_lineage"]["source_snapshot_sha256"] == manifest["row_lineage"][0]["source_snapshot_sha256"]
    assert record["outcome"]["was_shortlisted"] is True


def test_kol_only_builder_strips_unrequested_launch_context() -> None:
    row = _row(launch_id=None)
    built = training_export.build_point_in_time_training_dataset(
        [row],
        generated_at="2026-07-14T14:00:00Z",
        feature_reader=_reader_for(row),
    )

    record = built["records"][0]
    assert record["launch_id"] is None
    assert record["feature_lineage"]["entity_scope"] == "kol_only"
    assert "launch" not in record["feature_snapshot"]
    assert built["manifest"]["scope"]["launch_ids"] == []


def test_builder_never_uses_live_pool_or_launch_join_values() -> None:
    row = _row()
    row["pool_handle"] = "future-live-handle"
    row["pool_platform"] = "future-live-platform"
    row["product_sku"] = "FUTURE-SKU"
    row["product_name"] = "Future Mutable Product Name"
    built = training_export.build_point_in_time_training_dataset(
        [row],
        generated_at="2026-07-14T14:00:00Z",
        feature_reader=_reader_for(row),
    )

    record = built["records"][0]
    assert record["platform"] == "youtube"
    assert record["handle"] == "camera-test"
    assert record["product_sku"] == "AF-TEST"
    assert record["product_name"] == "Frozen Test Lens"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"point_in_time": False}, "historical frozen snapshot"),
        ({"source_row_id": 99}, "different recommendation snapshot"),
        ({"source_launch_id": 99}, "wrong launch scope"),
        ({"snapshot_at": "2026-07-10T12:01:00Z"}, "future-feature leakage"),
        ({"snapshot_sha256": "0" * 64}, "digest does not match"),
    ],
)
def test_builder_fails_closed_on_incomplete_or_inconsistent_proof(
    overrides: dict[str, Any],
    message: str,
) -> None:
    row = _row()

    with pytest.raises(training_export.PointInTimeDatasetBuildError, match=message):
        training_export.build_point_in_time_training_dataset(
            [row],
            generated_at="2026-07-14T14:00:00Z",
            feature_reader=_reader_for(row, **overrides),
        )


def test_builder_wraps_historical_reader_miss_without_current_fallback() -> None:
    row = _row()

    def unavailable(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise training_export.feature_store.HistoricalFeatureSnapshotUnavailable("missing")

    with pytest.raises(training_export.PointInTimeDatasetBuildError, match="no provable"):
        training_export.build_point_in_time_training_dataset(
            [row],
            generated_at="2026-07-14T14:00:00Z",
            feature_reader=unavailable,
        )


def test_empty_dataset_is_explicitly_not_training_eligible() -> None:
    built = training_export.build_point_in_time_training_dataset(
        [],
        generated_at="2026-07-14T14:00:00Z",
        feature_reader=lambda *_args, **_kwargs: pytest.fail("empty export must not read features"),
    )

    assert built["dataset_bytes"] == b""
    assert built["manifest"]["dataset"]["row_count"] == 0
    assert built["manifest"]["future_leakage_check"]["status"] == "not_applicable_empty_dataset"
    assert built["manifest"]["readiness"]["feature_dataset_nonempty"] is False
    assert built["manifest"]["readiness"]["finalized_label_rows"] == 0


def test_persisted_artifacts_are_self_verifying_and_tamper_evident(tmp_path: Path) -> None:
    row = _row()
    built = training_export.build_point_in_time_training_dataset(
        [row],
        generated_at="2026-07-14T14:00:00Z",
        feature_reader=_reader_for(row),
    )
    dataset_path = tmp_path / "dataset.jsonl"
    manifest_path = tmp_path / "dataset.manifest.json"
    dataset_path.write_bytes(built["dataset_bytes"])
    manifest_path.write_bytes(_canonical(built["manifest"]) + b"\n")

    verified = training_export.verify_point_in_time_training_artifacts(
        dataset_path,
        manifest_path,
    )
    assert verified["status"] == "verified"
    assert verified["row_count"] == 1
    assert verified["model_effect_proven"] is False

    dataset_path.write_bytes(dataset_path.read_bytes().replace(b"88.5", b"89.5", 1))
    with pytest.raises(training_export.PointInTimeDatasetBuildError, match="content SHA-256"):
        training_export.verify_point_in_time_training_artifacts(dataset_path, manifest_path)


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None, row: dict[str, Any] | None = None) -> None:
        self._rows = rows or []
        self._row = row

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _ExportConn:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        self.calls.append((" ".join(sql.split()), tuple(params)))
        normalized = " ".join(sql.split())
        if "SELECT r.id FROM vkpi_kol_recommendations" in normalized:
            return _Cursor(rows=[{"id": self.row["recommendation_id"]}])
        if "r.id AS recommendation_id" in normalized:
            return _Cursor(rows=[self.row])
        if normalized.startswith("SELECT * FROM vkpi_training_exports"):
            return _Cursor(row=None)
        return _Cursor()

    def commit(self) -> None:
        self.commits += 1


def test_real_export_path_writes_exclusive_dataset_and_manifest(monkeypatch, tmp_path: Path) -> None:
    row = _row()
    conn = _ExportConn(row)
    refreshed: list[int] = []
    monkeypatch.setattr(training_export, "EXPORT_DIR", tmp_path)
    monkeypatch.setattr(training_export, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(training_export, "get_conn", lambda: conn)
    monkeypatch.setattr(training_export, "resolve_staff_id", lambda _staff: 9)
    monkeypatch.setattr(training_export.secrets, "token_hex", lambda _length: "abc123")
    monkeypatch.setattr(
        training_export.outcome_collector,
        "refresh_business_outcome",
        lambda recommendation_id: refreshed.append(recommendation_id),
    )
    monkeypatch.setattr(training_export.feature_store, "get_features_at_time", _reader_for(row))

    result = training_export.export_training_dataset(staff={"id": 9})

    dataset_path = tmp_path / "train-abc123.jsonl"
    manifest_path = tmp_path / "train-abc123.manifest.json"
    assert refreshed == [41]
    assert dataset_path.is_file()
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert hashlib.sha256(dataset_path.read_bytes()).hexdigest() == manifest["dataset"]["content_sha256"]
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == result["manifest"]["manifest_file_sha256"]
    assert result["export"]["status"] == "completed"
    assert result["manifest"]["row_count"] == 1
    assert any("status='completed'" in sql for sql, _params in conn.calls)
    assert conn.commits >= 2


def test_real_export_path_removes_artifacts_and_marks_failed(monkeypatch, tmp_path: Path) -> None:
    row = _row()
    conn = _ExportConn(row)
    monkeypatch.setattr(training_export, "EXPORT_DIR", tmp_path)
    monkeypatch.setattr(training_export, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(training_export, "get_conn", lambda: conn)
    monkeypatch.setattr(training_export, "resolve_staff_id", lambda _staff: 9)
    monkeypatch.setattr(training_export.secrets, "token_hex", lambda _length: "failed")
    monkeypatch.setattr(training_export.outcome_collector, "refresh_business_outcome", lambda _id: None)
    monkeypatch.setattr(
        training_export.feature_store,
        "get_features_at_time",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            training_export.feature_store.HistoricalFeatureSnapshotUnavailable("missing")
        ),
    )

    with pytest.raises(training_export.PointInTimeDatasetBuildError):
        training_export.export_training_dataset(staff={"id": 9})

    assert list(tmp_path.iterdir()) == []
    assert any("status='failed'" in sql for sql, _params in conn.calls)


def test_real_export_path_never_deletes_a_preexisting_artifact(monkeypatch, tmp_path: Path) -> None:
    row = _row()
    conn = _ExportConn(row)
    preexisting = tmp_path / "train-collision.jsonl"
    preexisting.write_bytes(b"existing-immutable-artifact\n")
    monkeypatch.setattr(training_export, "EXPORT_DIR", tmp_path)
    monkeypatch.setattr(training_export, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(training_export, "get_conn", lambda: conn)
    monkeypatch.setattr(training_export, "resolve_staff_id", lambda _staff: 9)
    monkeypatch.setattr(training_export.secrets, "token_hex", lambda _length: "collision")
    monkeypatch.setattr(training_export.outcome_collector, "refresh_business_outcome", lambda _id: None)
    monkeypatch.setattr(training_export.feature_store, "get_features_at_time", _reader_for(row))

    with pytest.raises(FileExistsError):
        training_export.export_training_dataset(staff={"id": 9})

    assert preexisting.read_bytes() == b"existing-immutable-artifact\n"
    assert any("status='failed'" in sql for sql, _params in conn.calls)


def test_admin_export_route_reports_point_in_time_precondition_as_conflict(monkeypatch) -> None:
    from fastapi import HTTPException

    from app.api.routers import vkpi_industry_automation

    monkeypatch.setattr(
        vkpi_industry_automation.training_data_export,
        "export_training_dataset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            training_export.PointInTimeDatasetBuildError("historical proof missing")
        ),
    )

    with pytest.raises(HTTPException) as captured:
        vkpi_industry_automation.automation_training_export({}, staff={"id": 9})

    assert captured.value.status_code == 409
    assert captured.value.detail == "historical proof missing"
