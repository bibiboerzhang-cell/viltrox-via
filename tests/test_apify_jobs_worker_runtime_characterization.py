from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from app.workers import apify_jobs_worker as worker
from app.workers.apify_jobs_worker_runtime import process_job_impl


SPECIAL_HANDLERS = {
    "session_advance": "_process_session_advance",
    "smart_search_profile_advance": "_process_smart_search_profile_advance",
    "kol_content_fit_analysis": "_process_kol_content_fit_analysis",
    "account_dossier_extract": "_process_account_dossier_extract",
    "project_contract_extract": "_process_project_contract_extract",
    "project_retrospective_aggregate": "_process_project_retrospective",
    "video_url_resolve": "_process_video_url_resolve",
    "kol_profile_deep_crawl": "_process_kol_profile_deep_crawl",
    "kol_pool_comments_collect": "_process_kol_pool_comments_collect",
    "kol_video_metric_refresh": "_process_kol_video_metric_refresh",
    "kol_audience_stats_refresh": "_process_kol_audience_stats_refresh",
    "official_channel_comments_collect": "_process_official_channel_comments_collect",
    "kol_outreach_draft": "_process_kol_outreach_draft",
    "contract_invoice_extract": "_process_contract_invoice_extract",
    "contract_polish": "_process_contract_polish",
    "logistics_track_sync": "_process_logistics_track_sync",
    "kol_auto_poll": "_process_kol_auto_poll",
}


def _namespace() -> dict[str, Any]:
    return dict(vars(worker))


def test_special_dispatch_matrix_and_eager_namespace_contract() -> None:
    conn = object()
    for job_type, expected_handler in SPECIAL_HANDLERS.items():
        events: list[tuple[Any, ...]] = []
        namespace = _namespace()
        for handler_name in SPECIAL_HANDLERS.values():
            namespace[handler_name] = (
                lambda _conn, _job, payload, handler_name=handler_name: events.append(
                    (handler_name, _conn, _job, payload)
                )
            )
        payload = {"sentinel": job_type}
        job = {"id": 101, "job_type": f"  {job_type.upper()}  ", "payload": payload}

        process_job_impl(conn, job, namespace)  # type: ignore[arg-type]

        assert events == [(expected_handler, conn, job, payload)]

    missing = _namespace()
    missing.pop("random")
    with pytest.raises(KeyError) as raised:
        process_job_impl(
            conn,  # type: ignore[arg-type]
            {"id": 102, "job_type": "session_advance", "payload": {}},
            missing,
        )
    assert raised.value.args == ("random",)


class _MockCursor:
    def __init__(self, events: list[tuple[Any, ...]]) -> None:
        self.events = events
        self.fetch_count = 0

    def __enter__(self):
        self.events.append(("cursor_enter",))
        return self

    def __exit__(self, exc_type, exc, tb):
        self.events.append(("cursor_exit", exc_type))

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.events.append(("execute", " ".join(sql.split()), params))

    def fetchone(self):
        self.fetch_count += 1
        self.events.append(("fetchone", self.fetch_count))
        return (321,)


class _MockConnection:
    def __init__(self, events: list[tuple[Any, ...]]) -> None:
        self.events = events
        self.cursor_value = _MockCursor(events)

    @contextmanager
    def transaction(self):
        self.events.append(("transaction_enter",))
        try:
            yield
        except Exception as exc:
            self.events.append(("transaction_exit", type(exc)))
            raise
        else:
            self.events.append(("transaction_exit", None))

    def cursor(self):
        return self.cursor_value


def test_mock_job_preserves_transaction_sql_and_sync_order() -> None:
    events: list[tuple[Any, ...]] = []
    conn = _MockConnection(events)
    namespace = _namespace()
    namespace.update(
        {
            "_target": lambda payload: events.append(("target", payload)) or ("video", "701"),
            "_derive_method": lambda payload: events.append(("derive", payload)) or "mock",
            "_mock_result": lambda job, payload: events.append(("mock_result", job["id"], payload)) or {"ok": True},
            "_json": lambda value: events.append(("json", value)) or '{"ok":true}',
            "_search_session_analysis_summary_from_result": lambda **kwargs: events.append(
                ("summary", kwargs)
            ) or {"summary": True},
            "_sync_search_session_job": lambda *args, **kwargs: events.append(
                ("sync", args, kwargs)
            ),
        }
    )
    payload = {
        "target_type": "video",
        "target_id": "701",
        "derive_method": "mock",
        "triggered_by_user_id": "44",
    }
    job = {"id": 103, "job_type": "video", "payload": payload}

    process_job_impl(conn, job, namespace)  # type: ignore[arg-type]

    names = [event[0] for event in events]
    assert names == [
        "target",
        "derive",
        "mock_result",
        "transaction_enter",
        "cursor_enter",
        "json",
        "execute",
        "fetchone",
        "execute",
        "cursor_exit",
        "transaction_exit",
        "summary",
        "sync",
    ]
    insert = events[6]
    assert "INSERT INTO vkpi_analysis_cache" in insert[1]
    assert insert[2] == ("video", "701", '{"ok":true}', 44)
    update = events[8]
    assert "SET status='done'" in update[1]
    assert update[2] == (103,)
    assert events[-1][2] == {
        "raw_status": "done",
        "analysis_summary": {"summary": True},
    }


def test_budget_block_releases_slot_before_target_without_provider_call() -> None:
    events: list[tuple[Any, ...]] = []
    namespace = _namespace()
    namespace.update(
        {
            "_analysis_cache_reuse_decision": lambda *_args: events.append(("cache",))
            or {"exists": False, "reusable": False, "reasons": []},
            "_advisory_lock": lambda *args: events.append(("lock", args)) or True,
            "_acquire_llm_slot": lambda _conn: events.append(("slot",)) or "slot-9",
            "_llm_budget_preflight": lambda *_args, **_kwargs: events.append(("preflight",)) or {},
            "_google_allowed": lambda _preflight: events.append(("google_allowed",))
            or (False, "cap_exhausted", 0.25),
            "_log_budget_preflight_record_only": lambda **kwargs: events.append(
                ("budget_log", kwargs)
            ),
            "_block_job": lambda *args: events.append(("block", args)),
            "_advisory_unlock": lambda *args: events.append(("unlock", args)),
            "_process_gemini_video": lambda *_args: pytest.fail("provider must stay at zero"),
            "verify_job_local_evaluation_capability": lambda *_args, **_kwargs: {
                "requested": False
            },
        }
    )

    process_job_impl(
        object(),  # type: ignore[arg-type]
        {
            "id": 104,
            "job_type": "video",
            "payload": {
                "target_type": "video",
                "target_id": "701",
                "derive_method": "video_analysis_final_v1",
            },
        },
        namespace,
    )

    assert [event[0] for event in events] == [
        "cache",
        "lock",
        "cache",
        "slot",
        "preflight",
        "google_allowed",
        "budget_log",
        "block",
        "unlock",
        "unlock",
    ]
    assert events[7][1][2] == "budget_guard_blocked"
    assert events[8][1][1:] == ("vkpi_analysis_worker_llm_slot", "slot-9")
    assert events[9][1][1:] == (
        "vkpi_analysis_worker_target",
        "video:701:video_analysis_final_v1",
    )
