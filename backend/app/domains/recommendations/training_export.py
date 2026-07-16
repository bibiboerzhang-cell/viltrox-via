"""Point-in-time training dataset export for future V-KPI ML scoring.

The export is a production code path, but it is not a model-training claim.  It
builds an immutable-by-write-contract JSONL artifact from the feature snapshot
that was frozen at the recommendation decision time.  A row without a
provable historical snapshot aborts the whole export instead of falling back
to current KOL data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from app.db.connection import get_conn
from app.domains.projects.workflow import staff_id as resolve_staff_id
from app.domains.recommendations import feature_store
from app.domains.recommendations import outcomes as outcome_collector
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema

PROJECT_ROOT = Path(__file__).resolve().parents[4]
EXPORT_DIR = PROJECT_ROOT / "runtime" / "vkpi_training_exports"

DATASET_SCHEMA_VERSION = "vkpi_training_dataset_v2_point_in_time"
MANIFEST_SCHEMA_VERSION = "vkpi_training_dataset_manifest_v1"
BUILDER_VERSION = "recommendations.training_export.pit.v1"
POINT_IN_TIME_SOURCE = "vkpi_kol_recommendations.feature_snapshot_json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
logger = logging.getLogger(__name__)


class PointInTimeDatasetBuildError(RuntimeError):
    """Raised when any export row lacks a complete point-in-time proof."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str, sort_keys=True)


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PointInTimeDatasetBuildError("dataset contains a non-canonical JSON value") from exc
    return rendered.encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _snapshot_sha256(value: Any) -> str:
    snapshot = _loads(value, None)
    if not isinstance(snapshot, dict) or not snapshot:
        raise PointInTimeDatasetBuildError("recommendation has no frozen feature snapshot")
    # Keep byte semantics identical to feature_store.get_features_at_time.
    rendered = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(rendered)


def _positive_int(value: Any, *, field: str, required: bool = True) -> int | None:
    if value in (None, "") and not required:
        return None
    if isinstance(value, bool):
        raise PointInTimeDatasetBuildError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PointInTimeDatasetBuildError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        if not required and parsed == 0:
            return None
        raise PointInTimeDatasetBuildError(f"{field} must be a positive integer")
    return parsed


def _as_utc(value: Any, *, field: str) -> tuple[datetime, str]:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise PointInTimeDatasetBuildError(f"{field} is required")
        candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise PointInTimeDatasetBuildError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PointInTimeDatasetBuildError(f"{field} must include a timezone")
    normalized = parsed.astimezone(timezone.utc)
    return normalized, normalized.isoformat().replace("+00:00", "Z")


def _point_in_time_features(
    row: dict[str, Any],
    *,
    feature_reader: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    recommendation_id = _positive_int(row.get("recommendation_id"), field="recommendation_id")
    kol_pool_id = _positive_int(row.get("kol_pool_id"), field="kol_pool_id")
    launch_id = _positive_int(row.get("launch_id"), field="launch_id", required=False)
    as_of_dt, as_of = _as_utc(row.get("recommendation_created_at"), field="recommendation_created_at")

    try:
        result = feature_reader(kol_pool_id, as_of, launch_id=launch_id)
    except Exception as exc:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} has no provable point-in-time feature row"
        ) from exc
    if not isinstance(result, dict):
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} returned an invalid feature payload"
        )
    metadata = result.get("_point_in_time")
    if not isinstance(metadata, dict):
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} is missing point-in-time metadata"
        )
    if metadata.get("point_in_time") is not True or metadata.get("status") != "historical_frozen_snapshot":
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} did not resolve to a historical frozen snapshot"
        )
    if str(metadata.get("source") or "") != POINT_IN_TIME_SOURCE:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} used an unapproved feature source"
        )

    _, requested_at = _as_utc(result.get("requested_at"), field="requested_at")
    if requested_at != as_of:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} point-in-time request does not match its decision time"
        )
    source_row_id = _positive_int(metadata.get("source_row_id"), field="source_row_id")
    if source_row_id != recommendation_id:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} resolved to a different recommendation snapshot"
        )
    expected_scope = "kol_launch" if launch_id else "kol_only"
    if metadata.get("entity_scope") != expected_scope:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} has the wrong entity scope"
        )
    source_launch_id = _positive_int(
        metadata.get("source_launch_id"),
        field="source_launch_id",
        required=False,
    )
    if source_launch_id != launch_id:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} has the wrong launch scope"
        )

    snapshot_dt, snapshot_at = _as_utc(metadata.get("snapshot_at"), field="snapshot_at")
    recorded_dt, recorded_at = _as_utc(metadata.get("recorded_at"), field="recorded_at")
    if snapshot_dt > as_of_dt or recorded_dt > as_of_dt or snapshot_dt > recorded_dt:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} failed the future-feature leakage check"
        )

    source_snapshot_sha256 = str(metadata.get("snapshot_sha256") or "").lower()
    if not _SHA256_RE.fullmatch(source_snapshot_sha256):
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} has no valid source snapshot digest"
        )
    embedded_snapshot_sha256 = _snapshot_sha256(row.get("recommendation_feature_snapshot_json"))
    if embedded_snapshot_sha256 != source_snapshot_sha256:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} source snapshot digest does not match the export row"
        )

    feature_schema_version = str(metadata.get("feature_schema_version") or "").strip()
    if not feature_schema_version:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} has no feature schema version"
        )
    features = {key: value for key, value in result.items() if key not in {"requested_at", "_point_in_time"}}
    if _positive_int(features.get("kol_pool_id"), field="feature kol_pool_id") != kol_pool_id:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} feature entity does not match the export row"
        )
    feature_launch_id = _positive_int(features.get("launch_id"), field="feature launch_id", required=False)
    if feature_launch_id != launch_id:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} feature launch does not match the export row"
        )
    embedded_schema_version = str(features.get("feature_schema_version") or "").strip()
    if embedded_schema_version and embedded_schema_version != feature_schema_version:
        raise PointInTimeDatasetBuildError(
            f"recommendation {recommendation_id} feature schema metadata is inconsistent"
        )

    lineage = {
        "recommendation_id": recommendation_id,
        "kol_pool_id": kol_pool_id,
        "launch_id": launch_id,
        "entity_scope": expected_scope,
        "as_of": as_of,
        "source": POINT_IN_TIME_SOURCE,
        "source_row_id": source_row_id,
        "source_snapshot_sha256": source_snapshot_sha256,
        "feature_schema_version": feature_schema_version,
        "snapshot_at": snapshot_at,
        "recorded_at": recorded_at,
        "storage_mutability": str(metadata.get("storage_mutability") or "unknown"),
        "future_leakage_check": {
            "snapshot_at_lte_as_of": True,
            "recorded_at_lte_as_of": True,
            "snapshot_at_lte_recorded_at": True,
        },
    }
    return features, lineage


_OUTCOME_KEYS = frozenset(
    {
        "project_created",
        "outreach_sent",
        "reply_received",
        "agreement_reached",
        "content_published",
        "attributed_clicks",
        "attributed_orders",
        "attributed_gmv_cents",
        "attributed_cost_cents",
        "computed_roi",
    }
)


def _dataset_record(row: dict[str, Any], features: dict[str, Any], lineage: dict[str, Any]) -> dict[str, Any]:
    launch = features.get("launch") if isinstance(features.get("launch"), dict) else {}
    outcome = {
        key: value
        for key, value in row.items()
        if key.startswith("was_") or key in _OUTCOME_KEYS
    }
    return {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "recommendation_id": lineage["recommendation_id"],
        "launch_id": lineage["launch_id"],
        "kol_pool_id": lineage["kol_pool_id"],
        "decision_at": lineage["as_of"],
        "platform": features.get("platform") or row.get("recommendation_platform"),
        "handle": features.get("handle") or row.get("recommendation_handle"),
        "product_sku": launch.get("product_sku"),
        "product_name": launch.get("product_name"),
        "score": row.get("score"),
        "rank": row.get("rank"),
        "status": row.get("recommendation_status"),
        "feature_snapshot": features,
        "feature_lineage": lineage,
        "scoring_breakdown": _loads(row.get("recommendation_scoring_breakdown_json"), {}),
        "outcome": outcome,
        "outcome_finalized_at": row.get("outcome_finalized_at"),
    }


def build_point_in_time_training_dataset(
    rows: Iterable[Any],
    *,
    date_from: str = "",
    date_to: str = "",
    generated_at: str | None = None,
    feature_reader: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build deterministic dataset bytes plus a self-verifying manifest.

    This function performs no writes and is suitable for offline evaluation
    builders.  The caller persists artifacts only after every row has passed
    the point-in-time contract.
    """

    reader = feature_reader or feature_store.get_features_at_time
    records: list[dict[str, Any]] = []
    lineages: list[dict[str, Any]] = []
    recommendation_ids: set[int] = set()
    for raw_row in rows:
        row = dict(raw_row)
        features, lineage = _point_in_time_features(row, feature_reader=reader)
        recommendation_id = int(lineage["recommendation_id"])
        if recommendation_id in recommendation_ids:
            raise PointInTimeDatasetBuildError(
                f"recommendation {recommendation_id} appears more than once in the dataset"
            )
        recommendation_ids.add(recommendation_id)
        lineages.append(lineage)
        records.append(_dataset_record(row, features, lineage))

    lines = [_canonical_json_bytes(record) for record in records]
    dataset_bytes = b"\n".join(lines) + (b"\n" if lines else b"")
    dataset_sha256 = _sha256(dataset_bytes)
    generated_dt, generated = _as_utc(generated_at or _utcnow(), field="generated_at")
    del generated_dt

    schema_versions = sorted({str(item["feature_schema_version"]) for item in lineages})
    entity_scope_counts = {
        scope: sum(1 for item in lineages if item["entity_scope"] == scope)
        for scope in ("kol_only", "kol_launch")
    }
    entity_ids = sorted({int(item["kol_pool_id"]) for item in lineages})
    launch_ids = sorted({int(item["launch_id"]) for item in lineages if item["launch_id"] is not None})
    as_of_values = sorted(str(item["as_of"]) for item in lineages)
    finalized_label_rows = sum(1 for record in records if record.get("outcome_finalized_at"))
    all_feature_times_proven = all(
        all(bool(value) for value in item["future_leakage_check"].values())
        for item in lineages
    )
    manifest: dict[str, Any] = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "generated_at": generated,
        "dataset": {
            "format": "jsonl",
            "row_count": len(records),
            "content_sha256": dataset_sha256,
            "empty": not records,
        },
        "selection": {
            "date_from": str(date_from or ""),
            "date_to": str(date_to or ""),
            "as_of_field": "vkpi_kol_recommendations.created_at",
        },
        "field_contract": {
            "model_feature_path": "feature_snapshot",
            "label_path": "outcome",
            "provenance_path": "feature_lineage",
            "excluded_from_model_features": [
                "recommendation_id",
                "decision_at",
                "platform",
                "handle",
                "product_sku",
                "product_name",
                "score",
                "rank",
                "status",
                "scoring_breakdown",
                "outcome",
                "outcome_finalized_at",
            ],
        },
        "source_versions": {
            "feature_reader": "app.domains.recommendations.feature_store.get_features_at_time",
            "feature_store_schema_version": feature_store.FEATURE_SNAPSHOT_SCHEMA_VERSION,
            "accepted_feature_schema_versions": schema_versions,
            "source": POINT_IN_TIME_SOURCE,
        },
        "scope": {
            "entity_type": "vkpi_kol_pool",
            "entity_ids": entity_ids,
            "entity_count": len(entity_ids),
            "entity_scope_counts": entity_scope_counts,
            "launch_ids": launch_ids,
            "launch_count": len(launch_ids),
            "as_of_min": as_of_values[0] if as_of_values else None,
            "as_of_max": as_of_values[-1] if as_of_values else None,
        },
        "future_leakage_check": {
            "scope": "features_only; outcomes are post-decision labels",
            "status": "passed" if records and all_feature_times_proven else "not_applicable_empty_dataset",
            "checked_rows": len(records),
            "violations": 0,
            "checks": [
                "source_row_id equals recommendation_id",
                "snapshot_at <= recorded_at <= recommendation created_at",
                "source snapshot SHA-256 matches the export row",
                "entity and launch scope match the export row",
            ],
        },
        "row_lineage": lineages,
        "artifact_write_contract": {
            "write_mode": "exclusive_create",
            "overwrite_allowed": False,
            "source_storage_mutability": sorted(
                {str(item["storage_mutability"]) for item in lineages}
            ),
        },
        "readiness": {
            "point_in_time_features_proven": bool(records) and all_feature_times_proven,
            "feature_dataset_nonempty": bool(records),
            "finalized_label_rows": finalized_label_rows,
            "unfinalized_label_rows": len(records) - finalized_label_rows,
            "automated_model_training_enabled": False,
            "model_effect_proven": False,
        },
    }
    manifest["manifest_payload_sha256"] = _sha256(_canonical_json_bytes(manifest))
    return {
        "records": records,
        "dataset_bytes": dataset_bytes,
        "manifest": manifest,
    }


def _write_exclusive(path: Path, payload: bytes) -> None:
    with path.open("xb") as fh:
        fh.write(payload)


def verify_point_in_time_training_artifacts(
    dataset_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Recompute the hashes and row-lineage binding of a persisted export."""

    dataset_file = Path(dataset_path)
    manifest_file = Path(manifest_path)
    dataset_bytes = dataset_file.read_bytes()
    manifest_bytes = manifest_file.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except (TypeError, ValueError) as exc:
        raise PointInTimeDatasetBuildError("training manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise PointInTimeDatasetBuildError("training manifest must be a JSON object")
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        raise PointInTimeDatasetBuildError("training manifest schema version is not supported")

    expected_manifest_payload_sha256 = str(manifest.get("manifest_payload_sha256") or "")
    manifest_payload = dict(manifest)
    manifest_payload.pop("manifest_payload_sha256", None)
    if not _SHA256_RE.fullmatch(expected_manifest_payload_sha256) or _sha256(
        _canonical_json_bytes(manifest_payload)
    ) != expected_manifest_payload_sha256:
        raise PointInTimeDatasetBuildError("training manifest payload SHA-256 does not match")

    dataset_metadata = manifest.get("dataset")
    if not isinstance(dataset_metadata, dict):
        raise PointInTimeDatasetBuildError("training manifest is missing dataset metadata")
    if _sha256(dataset_bytes) != str(dataset_metadata.get("content_sha256") or ""):
        raise PointInTimeDatasetBuildError("training dataset content SHA-256 does not match")

    records: list[dict[str, Any]] = []
    for line in dataset_bytes.splitlines():
        try:
            record = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise PointInTimeDatasetBuildError("training dataset contains invalid JSONL") from exc
        if not isinstance(record, dict):
            raise PointInTimeDatasetBuildError("training dataset rows must be JSON objects")
        if record.get("dataset_schema_version") != DATASET_SCHEMA_VERSION:
            raise PointInTimeDatasetBuildError("training dataset row schema version is not supported")
        records.append(record)
    if len(records) != int(dataset_metadata.get("row_count") or 0):
        raise PointInTimeDatasetBuildError("training dataset row count does not match the manifest")

    manifest_lineage = manifest.get("row_lineage")
    if not isinstance(manifest_lineage, list) or len(manifest_lineage) != len(records):
        raise PointInTimeDatasetBuildError("training dataset lineage count does not match")
    for index, record in enumerate(records):
        if record.get("feature_lineage") != manifest_lineage[index]:
            raise PointInTimeDatasetBuildError(
                f"training dataset row {index + 1} lineage does not match the manifest"
            )
    expected_status = "passed" if records else "not_applicable_empty_dataset"
    leakage = manifest.get("future_leakage_check")
    if not isinstance(leakage, dict) or leakage.get("status") != expected_status:
        raise PointInTimeDatasetBuildError("training dataset leakage status is inconsistent")
    if int(leakage.get("violations") or 0) != 0:
        raise PointInTimeDatasetBuildError("training dataset records future-feature violations")

    return {
        "status": "verified",
        "row_count": len(records),
        "dataset_sha256": _sha256(dataset_bytes),
        "manifest_payload_sha256": expected_manifest_payload_sha256,
        "manifest_file_sha256": _sha256(manifest_bytes),
        "future_leakage_check": leakage,
        "model_effect_proven": False,
    }


def export_training_dataset(
    date_from: str = "",
    date_to: str = "",
    *,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export the real recommendation dataset with mandatory PIT features.

    This remains an operator-triggered data artifact path.  No model is fit,
    activated, or evaluated by this function.
    """

    ensure_vkpi_product_industry_schema()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    uid = f"train-{secrets.token_hex(8)}"
    now = _utcnow()
    path = EXPORT_DIR / f"{uid}.jsonl"
    manifest_path = EXPORT_DIR / f"{uid}.manifest.json"
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_training_exports
            (export_uid, date_from, date_to, file_path, row_count, status, created_by_staff_id, created_at, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            date_from or "",
            date_to or "",
            str(path),
            0,
            "running",
            resolve_staff_id(staff) or None,
            now,
            _json(
                {
                    "builder_version": BUILDER_VERSION,
                    "dataset_schema_version": DATASET_SCHEMA_VERSION,
                    "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
                    "point_in_time_required": True,
                }
            ),
        ),
    )
    conn.commit()

    where = []
    params: list[Any] = []
    if date_from:
        where.append("r.created_at>=?")
        params.append(date_from)
    if date_to:
        where.append("r.created_at<=?")
        params.append(date_to)
    clause = "WHERE " + " AND ".join(where) if where else ""
    created_artifacts: list[Path] = []

    try:
        recommendation_ids = conn.execute(
            f"SELECT r.id FROM vkpi_kol_recommendations r {clause} ORDER BY r.created_at ASC, r.id ASC",
            tuple(params),
        ).fetchall()
        for recommendation in recommendation_ids:
            outcome_collector.refresh_business_outcome(int(recommendation["id"]))

        rows = conn.execute(
            f"""
            SELECT
                r.id AS recommendation_id,
                r.created_at AS recommendation_created_at,
                r.launch_id AS launch_id,
                r.kol_pool_id AS kol_pool_id,
                r.platform AS recommendation_platform,
                r.handle AS recommendation_handle,
                r.score AS score,
                r.rank AS rank,
                r.status AS recommendation_status,
                r.feature_snapshot_json AS recommendation_feature_snapshot_json,
                r.scoring_breakdown_json AS recommendation_scoring_breakdown_json,
                o.id AS outcome_id,
                o.was_shortlisted,
                o.shortlisted_at,
                o.was_rejected,
                o.rejected_at,
                o.reject_reason,
                o.was_claimed,
                o.claimed_at,
                o.project_created,
                o.project_created_at,
                o.outreach_sent,
                o.outreach_sent_at,
                o.reply_received,
                o.reply_at,
                o.reply_sentiment,
                o.agreement_reached,
                o.agreement_at,
                o.content_published,
                o.content_published_at,
                o.content_url,
                o.order_attributed,
                o.first_order_at,
                o.attributed_clicks,
                o.attributed_orders,
                o.attributed_gmv_cents,
                o.attributed_cost_cents,
                o.computed_roi,
                o.recommended_at,
                o.first_action_at,
                o.outcome_finalized_at,
                o.model_version,
                o.display_position,
                o.display_context_json
            FROM vkpi_kol_recommendations r
            LEFT JOIN vkpi_recommendation_outcomes o ON o.recommendation_id = r.id
            {clause}
            ORDER BY r.created_at ASC, r.id ASC
            """,
            tuple(params),
        ).fetchall()
        built = build_point_in_time_training_dataset(
            rows,
            date_from=date_from,
            date_to=date_to,
            generated_at=now,
        )
        manifest = dict(built["manifest"])
        dataset_bytes = bytes(built["dataset_bytes"])
        manifest_bytes = _canonical_json_bytes(manifest) + b"\n"
        _write_exclusive(path, dataset_bytes)
        created_artifacts.append(path)
        _write_exclusive(manifest_path, manifest_bytes)
        created_artifacts.append(manifest_path)
        metadata = {
            "builder_version": BUILDER_VERSION,
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "point_in_time_required": True,
            "dataset_sha256": manifest["dataset"]["content_sha256"],
            "manifest_payload_sha256": manifest["manifest_payload_sha256"],
            "manifest_file_sha256": _sha256(manifest_bytes),
            "manifest_path": str(manifest_path),
            "future_leakage_check": manifest["future_leakage_check"],
            "readiness": manifest["readiness"],
        }
        count = int(manifest["dataset"]["row_count"])
        conn.execute(
            """
            UPDATE vkpi_training_exports
            SET row_count=?, status='completed', completed_at=?, metadata_json=?
            WHERE export_uid=?
            """,
            (count, _utcnow(), _json(metadata), uid),
        )
        conn.commit()
    except Exception as exc:
        for artifact_path in created_artifacts:
            try:
                artifact_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                logger.warning(
                    "training export cleanup failed | artifact=%s category=%s",
                    artifact_path.name,
                    type(cleanup_exc).__name__,
                )
        failure_metadata = {
            "builder_version": BUILDER_VERSION,
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "point_in_time_required": True,
            "failure_type": type(exc).__name__,
            "failure_mode": "fail_closed_no_artifact",
            "automated_model_training_enabled": False,
        }
        try:
            conn.execute(
                """
                UPDATE vkpi_training_exports
                SET row_count=0, status='failed', completed_at=?, metadata_json=?
                WHERE export_uid=?
                """,
                (_utcnow(), _json(failure_metadata), uid),
            )
            conn.commit()
        except Exception as status_exc:
            logger.warning(
                "training export failure receipt update failed | category=%s",
                type(status_exc).__name__,
            )
        raise

    row = conn.execute(
        "SELECT * FROM vkpi_training_exports WHERE export_uid=?",
        (uid,),
    ).fetchone()
    export_row = dict(row) if row else {
        "export_uid": uid,
        "file_path": str(path),
        "row_count": count,
        "status": "completed",
        "metadata_json": _json(metadata),
    }
    return {
        "export": export_row,
        "manifest": {
            "path": str(manifest_path),
            "dataset_sha256": metadata["dataset_sha256"],
            "manifest_file_sha256": metadata["manifest_file_sha256"],
            "row_count": count,
            "future_leakage_check": manifest["future_leakage_check"],
            "readiness": manifest["readiness"],
        },
    }


def latest(limit: int = 20) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    rows = get_conn().execute(
        "SELECT * FROM vkpi_training_exports ORDER BY created_at DESC, id DESC LIMIT ?",
        (max(1, min(100, int(limit or 20))),),
    ).fetchall()
    return {"exports": [dict(row) for row in rows]}
