from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import pytest

from app.workers.apify_jobs_worker_session import _search_session_job_state
from app.workers.apify_jobs_worker_skip import finish_skipped_impl


class _Logger:
    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        pytest.fail("legacy terminal path should not need warning fallback")


class _Cursor:
    def __init__(self, owner: "_Conn") -> None:
        self.owner = owner
        self.row: dict[str, Any] | None = None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        compact = " ".join(sql.split())
        self.owner.statements.append((compact, params))
        if compact.startswith("SELECT payload FROM apify_jobs"):
            self.row = {
                "payload": {
                    "target_type": "video",
                    "target_id": "701",
                    "derive_method": "video_analysis_final_v1",
                }
            }

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.row) if self.row is not None else None


class _Conn:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self, **_kwargs: Any) -> _Cursor:
        return _Cursor(self)

    def transaction(self):
        return nullcontext()


def test_legacy_cache_skip_is_terminal_partial_and_zero_followup() -> None:
    conn = _Conn()
    synced: list[dict[str, Any]] = []
    followups: list[str] = []

    def forbidden(name: str):
        def inner(*_args: Any, **_kwargs: Any) -> None:
            followups.append(name)
            pytest.fail(f"legacy cache must not run {name}")

        return inner

    finish_skipped_impl(
        conn,  # type: ignore[arg-type]
        9001,
        "skipped_legacy_cache_unverified:result_prompt_contract_mismatch",
        evaluation_only=False,
        namespace={
            "LOCAL_EVALUATION_CACHE_DERIVE_METHOD": "video_analysis_final_v1__local_eval",
            "_derive_method": lambda payload: payload.get("derive_method"),
            "_enqueue_account_dossier_extract_after_final_v1": forbidden("dossier"),
            "_enqueue_comments_collect_after_final_v1": forbidden("comments"),
            "_enqueue_content_fit_after_final_v1": forbidden("content_fit"),
            "_int_or_none": lambda value: int(value) if value else None,
            "_loads": lambda value, default: value if isinstance(value, dict) else default,
            "_search_session_analysis_summary_from_ready_cache": lambda *_args: {
                "cache_id": 501,
                "summary": "paid legacy raw remains displayable",
            },
            "_sync_deep_analysis_result_from_cache": forbidden("deep_projection"),
            "_sync_search_session_job": lambda *_args, **kwargs: synced.append(kwargs),
            "logger": _Logger(),
        },
    )

    assert followups == []
    assert synced[0]["raw_status"] == "done"
    assert synced[0]["reason"].startswith("skipped_legacy_cache_unverified")
    summary = synced[0]["analysis_summary"]
    assert summary["cache_id"] == 501
    assert summary["status"] == "legacy_unverified"
    assert summary["cache_reuse_status"] == "legacy_unverified"
    assert summary["revalidation_required"] is True
    assert summary["claim_status"] == "descriptive_only"
    update = next(sql for sql, _params in conn.statements if sql.startswith("UPDATE apify_jobs"))
    assert "status='done'" in update
    assert _search_session_job_state("done", synced[0]["reason"]) == ("partial", "summary")
