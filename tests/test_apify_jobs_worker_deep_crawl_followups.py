from __future__ import annotations

from contextlib import contextmanager

from app.domains.kol import contact_acquisition_queue
from app.domains.kol import url_deep_crawl
from app.domains.sync import refresh_tier
from app.workers import apify_jobs_worker_deep_crawl as deep_crawl
from app.workers import apify_jobs_worker_handlers as handlers


@contextmanager
def _scope():
    yield


def test_suppressed_contact_followup_still_records_freshness(monkeypatch) -> None:
    marks: list[tuple[int, str]] = []
    monkeypatch.setattr(
        refresh_tier,
        "mark_kol_refreshed",
        lambda kol_pool_id, *, status: marks.append((kol_pool_id, status)),
    )
    monkeypatch.setattr(
        contact_acquisition_queue,
        "enqueue_contact_acquisition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("contact enqueue must stay suppressed")
        ),
    )
    monkeypatch.setattr(
        contact_acquisition_queue,
        "reconcile_contact_acquisition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("contact reconcile must stay suppressed")
        ),
    )

    deep_crawl.run_success_followups(
        42,
        {"suppress_contact_followup": True},
        {"organization_id": 7},
        db_connection_sync_scope=_scope,
        logger=handlers.logger,
    )

    assert marks == [(42, "ready")]


def test_deep_crawl_handler_invokes_followups_for_suppressed_contact(monkeypatch) -> None:
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(handlers, "db_connection_sync_scope", _scope)
    monkeypatch.setattr(handlers, "_resolve_job_staff", lambda *_args: {"organization_id": 7})
    monkeypatch.setattr(
        url_deep_crawl,
        "run_profile_deep_crawl_for_job",
        lambda *_args, **_kwargs: {"status": "ready", "matched_kol_pool_id": 42},
    )
    monkeypatch.setattr(deep_crawl, "crawl_outcome", lambda _result: (True, "ready"))
    monkeypatch.setattr(deep_crawl, "persist_crawl_outcome", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deep_crawl, "record_monitor_terminal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deep_crawl, "crawl_kol_pool_id", lambda *_args, **_kwargs: 42)
    monkeypatch.setattr(
        deep_crawl,
        "run_success_followups",
        lambda kol_pool_id, payload, *_args, **_kwargs: calls.append(
            (kol_pool_id, payload.get("suppress_contact_followup") is True)
        ),
    )

    handlers._process_kol_profile_deep_crawl(
        object(),
        {"id": 9},
        {"suppress_contact_followup": True},
    )

    assert calls == [(42, True)]
