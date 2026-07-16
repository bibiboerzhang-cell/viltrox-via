"""Failure cleanup helpers for structured report generation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.domains.reports import pdf_renderer


logger = get_logger(__name__)
_REPORT_FILE_SUFFIXES = {"markdown": ".md", "pdf": ".pdf"}


def _rollback_report_transaction(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception as exc:  # pragma: no cover - a dead connection cannot recover
        logger.warning("vkpi report rollback failed: %s", type(exc).__name__)


def cleanup_generated_report_files(
    report_uid: str,
    stored_files: list[tuple[str, dict[str, Any]]],
) -> None:
    """Remove only regular files owned by this generation attempt."""
    expected_names = {
        f"{report_uid}{suffix}" for suffix in _REPORT_FILE_SUFFIXES.values()
    }
    for _stored_format, stored in stored_files:
        raw_path = stored.get("file_path") if isinstance(stored, dict) else None
        if not raw_path:
            continue
        candidate = Path(str(raw_path))
        if candidate.name not in expected_names:
            logger.warning(
                "vkpi report cleanup rejected filename | uid=%s name=%s",
                report_uid,
                candidate.name,
            )
            continue
        try:
            pdf_renderer.remove_stored_file(
                candidate,
                expected_size=stored.get("file_size_bytes"),
                expected_sha256=str(stored.get("sha256_hex") or ""),
            )
        except FileNotFoundError:
            continue
        except Exception as exc:  # pragma: no cover - environment-specific filesystem
            logger.warning(
                "vkpi report cleanup failed | uid=%s name=%s error=%s",
                report_uid,
                candidate.name,
                type(exc).__name__,
            )

    try:
        root = pdf_renderer.report_storage_dir()
        removed_stage = False
        for name in expected_names:
            for staged_path in root.glob(f".{name}.*.tmp"):
                try:
                    staged_path.unlink(missing_ok=True)
                    removed_stage = True
                except Exception as exc:  # pragma: no cover - environment-specific filesystem
                    logger.warning(
                        "vkpi report staging cleanup failed | uid=%s name=%s error=%s",
                        report_uid,
                        staged_path.name,
                        type(exc).__name__,
                    )
        if removed_stage:
            pdf_renderer._fsync_directory(root)
    except Exception as exc:  # pragma: no cover - storage root failures are environment-specific
        logger.warning(
            "vkpi report staging scan failed | uid=%s error=%s",
            report_uid,
            type(exc).__name__,
        )


def delete_partial_report_file_rows(conn: Any, report_run_id: int) -> None:
    """Delete only this failed run's file rows and commit that cleanup."""
    for attempt in range(2):
        try:
            conn.execute(
                "DELETE FROM vkpi_report_files WHERE report_run_id=?",
                (int(report_run_id),),
            )
            conn.commit()
            return
        except Exception as exc:
            _rollback_report_transaction(conn)
            if attempt:
                logger.error(
                    "vkpi report file-row cleanup failed | run_id=%s error=%s",
                    report_run_id,
                    type(exc).__name__,
                )


def persist_failed_report(
    conn: Any,
    report_run_id: int,
    exc: Exception,
    *,
    expected_metadata_json: str,
) -> None:
    """Fail only the rendering attempt that still owns the metadata token."""
    error_type = type(exc).__name__[:80]
    for attempt in range(2):
        try:
            result = conn.execute(
                """
                UPDATE vkpi_report_runs
                SET status='failed', error_message=?
                WHERE id=? AND status='rendering' AND metadata_json=?
                """,
                (error_type, int(report_run_id), expected_metadata_json),
            )
            conn.commit()
            if getattr(result, "rowcount", None) == 0:
                logger.warning(
                    "vkpi report failed-state CAS not applied | run_id=%s",
                    report_run_id,
                )
            return
        except Exception as recovery_exc:
            _rollback_report_transaction(conn)
            if attempt:
                logger.error(
                    "vkpi report failed-state persistence failed | run_id=%s error=%s",
                    report_run_id,
                    type(recovery_exc).__name__,
                )
