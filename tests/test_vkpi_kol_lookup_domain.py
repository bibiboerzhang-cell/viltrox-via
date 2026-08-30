import json
from datetime import datetime, timezone

import pytest

from app.domains.access import scope
from app.domains.audit import service as audit_service
from app.domains.kol import lookup as lookup_domain
from app.domains.kol import lookup_tracking
from app.services.jobs import queue_common
from app.shared import vkpi_utils


@pytest.fixture(autouse=True)
def isolate_lookup_tracking(monkeypatch):
    """Keep orchestration unit tests from writing to the live runtime ledger."""

    class NoopLookupTracker:
        def __init__(self, *, body, staff):
            self.session_id = None
            self.task_id = "test_kol_lookup"

        def open(self):
            return None

        def set_query_text(self, result):
            return None

        def stage(self, stage):
            return None

        def finish(self, **kwargs):
            return None

    monkeypatch.setattr(lookup_domain, "LookupTracker", NoopLookupTracker)


def test_shared_utcnow_iso_matches_queue_timestamp_contract(monkeypatch):
    """The lower-layer helper preserves the queue timestamp's exact wire format."""

    class FrozenDateTime:
        @classmethod
        def now(cls, tz):
            assert tz is timezone.utc
            return datetime(2026, 8, 29, 12, 34, 56, 987654, tzinfo=timezone.utc)

    monkeypatch.setattr(queue_common, "datetime", FrozenDateTime)
    monkeypatch.setattr(vkpi_utils, "datetime", FrozenDateTime)

    expected = "2026-08-29T12:34:56Z"
    assert queue_common.utcnow() == expected
    assert vkpi_utils.utcnow_iso() == expected


def test_lookup_tracker_preserves_session_and_ledger_timestamp_outputs(monkeypatch):
    timestamps = iter(
        (
            "2026-08-29T12:00:01Z",
            "2026-08-29T12:00:02Z",
            "2026-08-29T12:00:03Z",
        )
    )
    executions: list[tuple[str, tuple]] = []
    session_creates: list[dict] = []
    session_updates: list[dict] = []

    class RecordingConnection:
        commits = 0

        def execute(self, sql, params):
            executions.append((" ".join(sql.split()), tuple(params)))
            return self

        def commit(self):
            self.commits += 1

    connection = RecordingConnection()
    staff = {"id": 7, "staff_id": 7, "user_id": 11}
    body = {
        "platform": "instagram",
        "handle": "creator",
        "scan_account": True,
        "product_sku": "AF35",
    }

    monkeypatch.setattr(lookup_tracking, "utcnow_iso", lambda: next(timestamps))
    monkeypatch.setattr(lookup_tracking, "get_conn", lambda: connection)
    monkeypatch.setattr(
        lookup_tracking.search_sessions,
        "create_session",
        lambda **kwargs: session_creates.append(kwargs) or {"id": 73},
    )
    monkeypatch.setattr(
        lookup_tracking.search_sessions,
        "update_session_result_summary",
        lambda session_id, **kwargs: session_updates.append({"session_id": session_id, **kwargs}),
    )

    tracker = lookup_tracking.LookupTracker(body=body, staff=staff)
    tracker.task_id = "kol_lookup_characterization"

    assert tracker.open() == 73
    tracker.stage(lookup_tracking.STAGE_THINKING)
    tracker.finish(
        status="ready",
        result={
            "kol": {"id": 19},
            "scan_result": {"content_count": 2},
            "analysis_result": {"status": "ready"},
        },
    )

    assert session_creates == [
        {
            "query_text": "instagram/creator",
            "query_type": "text_recall",
            "source": "kol_lookup",
            "input_payload": {
                "platform": "instagram",
                "handle": "creator",
                "scan_account": True,
                "product_sku": "AF35",
                "task_id": "kol_lookup_characterization",
            },
            "status": "running",
            "staff": staff,
        }
    ]
    assert session_updates == [
        {
            "session_id": 73,
            "status": "ready",
            "summary_patch": {
                "kind": "kol_lookup",
                "task_id": "kol_lookup_characterization",
            },
        }
    ]
    assert connection.commits == 3

    insert_params = executions[0][1]
    assert insert_params[:5] == (
        "kol_lookup_characterization",
        "kol_lookup",
        0,
        11,
        "processing",
    )
    assert json.loads(insert_params[5]) == {
        "source": "vkpi",
        "search_session_id": "73",
        "search_session_stage": "search",
        "query_type": "text_recall",
        "platform": "instagram",
        "handle": "creator",
        "user_id": 11,
        "staff_id": 7,
        "created_by_user_id": 11,
    }
    assert insert_params[6:] == (
        0,
        "search",
        "instagram/creator",
        "2026-08-29T12:00:01Z",
        "2026-08-29T12:00:01Z",
        "2026-08-29T12:00:01Z",
    )

    assert executions[1][1] == (
        "processing",
        "thinking",
        "2026-08-29T12:00:02Z",
        "kol_lookup_characterization",
    )
    close_params = executions[2][1]
    assert close_params[:4] == ("done", "summarizing", "kol_lookup", "")
    assert json.loads(close_params[4]) == {
        "session_id": 73,
        "scan_result": {"content_count": 2},
        "analysis_result": {"status": "ready"},
        "kol_id": 19,
    }
    assert close_params[5:] == (
        "2026-08-29T12:00:03Z",
        "2026-08-29T12:00:03Z",
        "kol_lookup_characterization",
    )


@pytest.mark.anyio
async def test_lookup_with_context_returns_plain_result_without_kol_id(monkeypatch):
    monkeypatch.setattr(lookup_domain.claims_domain, "lookup", lambda body, *, staff: {"status": "created"})

    result = await lookup_domain.lookup_with_context({"handle": "x"}, staff={"id": 1})
    # P4/P10:lookup 现在 additive 附加 search_session_id/task_id(任务可追踪 + 切页可恢复)。
    # 原契约保证不变:无 kol 的 plain claims 结果其 status 原样流过、不被加工出 kol。
    assert result["status"] == "created"
    assert "kol" not in result


@pytest.mark.anyio
async def test_lookup_with_context_marks_claimed_by_other(monkeypatch):
    monkeypatch.setattr(lookup_domain.claims_domain, "lookup", lambda body, *, staff: {"kol": {"id": 7}})

    def deny_access(*_args, **_kwargs):
        raise scope.ScopeDenied("kol scope denied")

    monkeypatch.setattr(lookup_domain.claims_domain, "assert_kol_access", deny_access)

    payload = await lookup_domain.lookup_with_context({"handle": "x"}, staff={"id": 1})

    assert payload["kol"] == {"id": 7}
    assert payload["dossier"] == {}
    assert payload["can_claim"] is False
    assert payload["access_status"] == "claimed_by_other"


@pytest.mark.anyio
async def test_lookup_with_context_can_scan_analyze_and_attach_dossier(monkeypatch):
    monkeypatch.setattr(lookup_domain.claims_domain, "lookup", lambda body, *, staff: {"kol": {"id": 9}})
    monkeypatch.setattr(lookup_domain.claims_domain, "assert_kol_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lookup_domain.account_domain, "get_dossier", lambda kol_id: {"kol_id": kol_id, "ready": True})

    async def fake_scan(kol_id, *, max_posts):
        return {"kol_id": kol_id, "max_posts": max_posts, "content_count": 2}

    async def fake_analyze(kol_id, *, product_sku, snapshot_id=None):
        return {"kol_id": kol_id, "product_sku": product_sku, "snapshot_id": snapshot_id}

    monkeypatch.setattr(lookup_domain.account_domain, "scan_account", fake_scan)
    monkeypatch.setattr(lookup_domain.account_domain, "analyze_account", fake_analyze)

    payload = await lookup_domain.lookup_with_context(
        {"scan_account": True, "max_posts": 500, "product_sku": "AF35"},
        staff={"id": 1},
    )

    assert payload["dossier"] == {"kol_id": 9, "ready": True}
    assert payload["scan_result"] == {"kol_id": 9, "max_posts": 80, "content_count": 2}
    assert payload["analysis_result"] == {"kol_id": 9, "product_sku": "AF35", "snapshot_id": None}


@pytest.mark.anyio
async def test_lookup_employee_never_returns_or_persists_plaintext_contacts(monkeypatch):
    secret = "private@example.com"
    finished: list[dict] = []
    audit_calls: list[dict] = []

    class RecordingTracker:
        session_id = 41
        task_id = "lookup-contact-boundary"

        def __init__(self, *, body, staff):
            del body, staff

        def open(self):
            return self.session_id

        def set_query_text(self, result):
            del result

        def stage(self, stage):
            del stage

        def finish(self, **kwargs):
            finished.append(kwargs)

    monkeypatch.setattr(lookup_domain, "LookupTracker", RecordingTracker)
    monkeypatch.setattr(
        lookup_domain.claims_domain,
        "lookup",
        lambda body, *, staff: {"kol": {"id": 9, "contact_email": secret}},
    )
    monkeypatch.setattr(lookup_domain.claims_domain, "assert_kol_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        lookup_domain.account_domain,
        "get_dossier",
        lambda kol_id: {"kol_id": kol_id, "contact_emails": [secret]},
    )
    monkeypatch.setattr(
        audit_service,
        "log_sensitive_access",
        lambda **kwargs: audit_calls.append(kwargs) or {"id": 1, "status": "logged"},
    )
    staff = {
        "id": 17,
        "active": 1,
        "role": "employee",
        "permissions": {"vkpi": "read"},
        "organization_id": 1,
        "organization_scope_status": "resolved",
    }

    payload = await lookup_domain.lookup_with_context({"handle": "creator"}, staff=staff)

    assert secret not in str(payload)
    assert finished and secret not in str(finished)
    assert audit_calls == []
