"""Stage legacy V-KPI Excel rows into P2B import staging tables."""
from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.domains.legacy_import.legacy_import_staging_records import _base_values, _prepare_records
from app.domains.legacy_import.legacy_import_staging_schema import (
    ACTIVE_BATCH_STATUSES,
    PIPELINE_DEFAULTS,
    PIPELINE_ORDER,
    PIPELINE_TABLES,
    STAGING_COLUMNS,
    StageRecord,
    file_sha256,
    json_dumps,
)


class FileAlreadyImported(RuntimeError):
    """Raised when an active staging batch already owns the same file hash."""


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_legacy_staging_schema() -> None:
    """Apply Postgres migrations without invoking unrelated runtime seeders."""
    from app.db.connection import _run_postgres_migrations, is_postgres_runtime

    if not is_postgres_runtime():
        raise RuntimeError("P2B staging CLI requires the Postgres runtime migrations")
    _run_postgres_migrations()


def _insert_row(conn: Any, table: str, columns: list[str], values: dict[str, Any]) -> int:
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    params = [values.get(column) for column in columns]
    row = conn.execute(f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) RETURNING id", params).fetchone()
    return int(row["id"])


def _insert_review_queue(conn: Any, import_batch_id: int, record: StageRecord, staging_id: int) -> None:
    if not record.review_reasons:
        return
    review_type = "unmatched_kol_review" if record.review_reasons == ["unmatched_kol_review"] else "validation_error"
    severity = "high" if any(reason.startswith("missing_") or reason.startswith("row_parse_error") for reason in record.review_reasons) else "medium"
    conn.execute(
        """
        INSERT INTO vkpi_legacy_import_review_queue (
          import_batch_id, pipeline, staging_table, staging_id, source_sheet,
          source_row, review_type, severity, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            import_batch_id,
            record.pipeline,
            record.table,
            staging_id,
            record.source_sheet,
            record.source_row,
            review_type,
            severity,
            json_dumps({"review_reasons": record.review_reasons, "raw": record.raw}),
        ),
    )


def _active_hash_lookup(conn: Any, sha256: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT batch_uid, status, parsed_at
        FROM vkpi_legacy_import_batches
        WHERE source_file_sha256 = ?
          AND status IN ('staging', 'staged', 'committing', 'committed')
        LIMIT 1
        """,
        (sha256,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def stage_legacy_file(
    path: str | Path,
    *,
    batch_label: str = "",
    sheet_name: str = "",
    max_rows: int = 0,
) -> dict[str, Any]:
    source = Path(path).expanduser()
    source_stat = source.stat()
    source_hash = file_sha256(source)
    batch_uid = f"vkpi_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:12]}"
    records, skipped_sheets = _prepare_records(source, sheet_name=sheet_name, max_rows=max_rows)
    pipeline_counts = Counter(record.pipeline for record in records)
    total_rows = sum(pipeline_counts.values()) + sum(int(item.get("rows") or 0) for item in skipped_sheets)

    conn = get_conn()
    import_batch_id = 0
    try:
        existing = _active_hash_lookup(conn, source_hash)
        if existing:
            raise FileAlreadyImported(
                f"File already imported as batch {existing['batch_uid']} (status: {existing['status']})"
            )
        metadata = {
            "batch_label": batch_label,
            "skipped_sheets": skipped_sheets,
            "pipeline_counts": {key: int(pipeline_counts.get(key, 0)) for key in PIPELINE_ORDER},
        }
        row = conn.execute(
            """
            INSERT INTO vkpi_legacy_import_batches (
              batch_uid, source_file_name, source_file_sha256,
              source_file_size_bytes, source_workbook_path, status,
              total_rows, contains_pii, metadata_json, rollback_until,
              rollback_policy
            ) VALUES (?, ?, ?, ?, ?, 'staging', ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                batch_uid,
                source.name,
                source_hash,
                int(source_stat.st_size),
                str(source),
                int(total_rows),
                True,
                json_dumps(metadata),
                (_utcnow_naive() + timedelta(minutes=30)).isoformat(timespec="seconds"),
                "manual_30m",
            ),
        ).fetchone()
        import_batch_id = int(row["id"])
        review_queue_rows = 0
        for record in records:
            base_values = _base_values(record, batch_uid)
            base_values["import_batch_id"] = import_batch_id
            columns = STAGING_COLUMNS[record.pipeline]
            staging_id = _insert_row(conn, record.table, columns, base_values)
            if record.review_reasons:
                _insert_review_queue(conn, import_batch_id, record, staging_id)
                review_queue_rows += 1

        unmatched_count = sum(1 for record in records if "unmatched_kol_review" in record.review_reasons)
        validation_error_count = review_queue_rows - unmatched_count
        metadata.update(
            {
                "review_queue_rows": review_queue_rows,
                "unmatched_count": unmatched_count,
                "validation_error_count": validation_error_count,
            }
        )
        conn.execute(
            """
            UPDATE vkpi_legacy_import_batches
            SET status='staged',
                staging_rows=?,
                review_rows=?,
                metadata_json=?,
                parsed_at=?,
                updated_at=?
            WHERE id=?
            """,
            (
                int(sum(pipeline_counts.values())),
                int(review_queue_rows),
                json_dumps(metadata),
                _utcnow_naive().isoformat(timespec="seconds"),
                _utcnow_naive().isoformat(timespec="seconds"),
                import_batch_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO vkpi_legacy_import_logs (
              import_batch_id, action, status, detail, row_count, metadata_json
            ) VALUES (?, 'stage_legacy_file', 'ok', ?, ?, ?)
            """,
            (
                import_batch_id,
                f"staged {sum(pipeline_counts.values())} rows from {source.name}",
                int(sum(pipeline_counts.values())),
                json_dumps(metadata),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return inspect_batch(batch_uid)


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row.items()) if hasattr(row, "items") else dict(row)


def inspect_batch(batch_uid: str) -> dict[str, Any]:
    conn = get_conn()
    batch_row = conn.execute("SELECT * FROM vkpi_legacy_import_batches WHERE batch_uid=?", (batch_uid,)).fetchone()
    if not batch_row:
        raise ValueError(f"batch not found: {batch_uid}")
    batch = _row_to_dict(batch_row)
    import_batch_id = int(batch["id"])
    pipeline_counts: dict[str, int] = {}
    for pipeline in PIPELINE_ORDER:
        table = PIPELINE_TABLES[pipeline]
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE import_batch_id=?", (import_batch_id,)).fetchone()
        pipeline_counts[pipeline] = int(row["n"] if row else 0)
    review_queue_rows = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_legacy_import_review_queue WHERE import_batch_id=?",
            (import_batch_id,),
        ).fetchone()["n"]
    )
    unmatched_count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_legacy_import_review_queue
            WHERE import_batch_id=? AND review_type='unmatched_kol_review'
            """,
            (import_batch_id,),
        ).fetchone()["n"]
    )
    validation_error_count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_legacy_import_review_queue
            WHERE import_batch_id=? AND review_type='validation_error'
            """,
            (import_batch_id,),
        ).fetchone()["n"]
    )
    committed_refs_count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_legacy_import_committed_refs
            WHERE import_batch_id=? AND rollback_status='not_rolled_back'
            """,
            (import_batch_id,),
        ).fetchone()["n"]
    )
    try:
        metadata = json.loads(batch.get("metadata_json") or "{}")
    except Exception:
        metadata = {}
    skipped_sheets = metadata.get("skipped_sheets") or []
    return {
        "batch_uid": batch_uid,
        "status": batch.get("status", ""),
        "pipeline_counts": pipeline_counts,
        "review_queue_rows": review_queue_rows,
        "skipped_sheets": skipped_sheets,
        "skipped_rows": sum(int(item.get("rows") or 0) for item in skipped_sheets if isinstance(item, dict)),
        "unmatched_count": unmatched_count,
        "validation_error_count": validation_error_count,
        "committed_refs_count": committed_refs_count,
        "source_file_sha256": batch.get("source_file_sha256", ""),
    }


def rollback_staging_batch(batch_uid: str) -> dict[str, Any]:
    conn = get_conn()
    batch_row = conn.execute(
        "SELECT id, status FROM vkpi_legacy_import_batches WHERE batch_uid=?",
        (batch_uid,),
    ).fetchone()
    if not batch_row:
        raise ValueError(f"batch not found: {batch_uid}")
    import_batch_id = int(batch_row["id"])
    committed_refs = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_legacy_import_committed_refs WHERE import_batch_id=?",
            (import_batch_id,),
        ).fetchone()["n"]
    )
    if committed_refs:
        raise RuntimeError("batch has committed refs; P2D main-table rollback is required")
    try:
        deleted_rows = 0
        for pipeline in PIPELINE_ORDER:
            table = PIPELINE_TABLES[pipeline]
            cursor = conn.execute(f"DELETE FROM {table} WHERE import_batch_id=?", (import_batch_id,))
            deleted_rows += int(getattr(cursor, "rowcount", 0) or 0)
        conn.execute("DELETE FROM vkpi_legacy_import_review_queue WHERE import_batch_id=?", (import_batch_id,))
        conn.execute(
            """
            UPDATE vkpi_legacy_import_batches
            SET status='rolled_back',
                rolled_back_rows=?,
                rolled_back_at=?,
                updated_at=?
            WHERE id=?
            """,
            (
                deleted_rows,
                _utcnow_naive().isoformat(timespec="seconds"),
                _utcnow_naive().isoformat(timespec="seconds"),
                import_batch_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO vkpi_legacy_import_logs (
              import_batch_id, action, status, detail, row_count, metadata_json
            ) VALUES (?, 'rollback_staging_batch', 'ok', ?, ?, ?)
            """,
            (
                import_batch_id,
                "cleared staging rows before main-table commit",
                deleted_rows,
                json_dumps({"batch_uid": batch_uid}),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"batch_uid": batch_uid, "status": "rolled_back", "rolled_back_rows": deleted_rows}


def format_batch_summary(result: dict[str, Any]) -> str:
    lines = [f"batch_uid={result.get('batch_uid', '')}"]
    pipeline_counts = result.get("pipeline_counts") or {}
    for pipeline in PIPELINE_ORDER:
        lines.append(f"pipeline.{pipeline}={int(pipeline_counts.get(pipeline, 0))}")
    lines.append(f"review_queue_rows={int(result.get('review_queue_rows', 0))}")
    lines.append(f"skipped={int(result.get('skipped_rows', 0))} rows / {len(result.get('skipped_sheets') or [])} sheets")
    lines.append(f"unmatched={int(result.get('unmatched_count', 0))}")
    lines.append(f"validation_error={int(result.get('validation_error_count', 0))}")
    lines.append(f"committed_refs={int(result.get('committed_refs_count', 0))}")
    return "\n".join(lines)
