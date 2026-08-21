from __future__ import annotations

from app.domains.kol import content_fit_analysis


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return _Cursor(self.row)


def _job(
    state: str,
    *,
    job_id: int = 91,
    created_at: str = "2026-08-21T11:00:00+00:00",
) -> dict:
    return {
        "id": job_id,
        "status": state,
        "state": state,
        "terminal": state in {"blocked", "failed"},
        "created_at": created_at,
        "stage": "content_fit",
        "reason": "bounded reason" if state in {"blocked", "failed"} else None,
    }


def test_newer_active_job_supersedes_previous_ready_cache(monkeypatch) -> None:
    monkeypatch.setattr(content_fit_analysis, "get_conn", lambda: object())
    monkeypatch.setattr(
        content_fit_analysis,
        "_read_cache",
        lambda *_args, **_kwargs: {
            "state": "ready",
            "result": {"fit_verdict": "fit"},
            "updated_at": "2026-08-21T10:00:00+00:00",
        },
    )
    captured: dict[str, object] = {}

    def read_job(*_args, **kwargs):
        captured.update(kwargs)
        return _job("queued")

    monkeypatch.setattr(content_fit_analysis, "_content_fit_job_snapshot", read_job)

    result = content_fit_analysis.get_content_fit(42, "AF-35-PRO", job_id=91)

    assert captured == {"job_id": 91}
    assert result["state"] == "queued"
    assert result["job_id"] == 91
    assert result["analysis_job"]["stage"] == "content_fit"
    assert result["previous_cache_updated_at"] == "2026-08-21T10:00:00+00:00"


def test_ready_cache_newer_than_old_terminal_job_remains_authoritative(monkeypatch) -> None:
    monkeypatch.setattr(content_fit_analysis, "get_conn", lambda: object())
    monkeypatch.setattr(
        content_fit_analysis,
        "_read_cache",
        lambda *_args, **_kwargs: {
            "state": "ready",
            "result": {"fit_verdict": "fit"},
            "updated_at": "2026-08-21T12:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        content_fit_analysis,
        "_content_fit_job_snapshot",
        lambda *_args, **_kwargs: _job("failed", created_at="2026-08-21T11:00:00+00:00"),
    )

    result = content_fit_analysis.get_content_fit(42)

    assert result["state"] == "ready"
    assert result["result"]["fit_verdict"] == "fit"
    assert result["analysis_job"]["state"] == "failed"


def test_terminal_without_cache_is_projected_with_bounded_reason(monkeypatch) -> None:
    monkeypatch.setattr(content_fit_analysis, "get_conn", lambda: object())
    monkeypatch.setattr(content_fit_analysis, "_read_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        content_fit_analysis,
        "_content_fit_job_snapshot",
        lambda *_args, **_kwargs: _job("blocked"),
    )

    result = content_fit_analysis.get_content_fit(42)

    assert result["state"] == "blocked"
    assert result["terminal"] is True
    assert result["analysis_job"]["reason"] == "bounded reason"
    assert "payload" not in result["analysis_job"]
    assert "last_error" not in result["analysis_job"]


def test_no_cache_or_job_is_honestly_not_requested(monkeypatch) -> None:
    monkeypatch.setattr(content_fit_analysis, "get_conn", lambda: object())
    monkeypatch.setattr(content_fit_analysis, "_read_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(content_fit_analysis, "_content_fit_job_snapshot", lambda *_args, **_kwargs: None)

    result = content_fit_analysis.get_content_fit(42)

    assert result == {
        "status": "not_requested",
        "state": "missing",
        "kol_pool_id": 42,
        "product_sku": None,
        "derive_method": "content_fit_v1",
    }


def test_stale_cache_is_not_misreported_ready() -> None:
    conn = _Conn(
        {
            "result": '{"fit_verdict":"fit"}',
            "model": "model-a",
            "cost": 0.01,
            "status": "stale",
            "updated_at": "2026-08-21T12:00:00+00:00",
        }
    )

    result = content_fit_analysis._read_cache(conn, 42, include_stale=True)

    assert result is not None
    assert result["state"] == "stale"
    assert result["status"] == "stale"
    assert "status IN ('ready', 'stale')" in conn.calls[0][0]


def test_job_projection_redacts_provider_email_url_and_free_text() -> None:
    conn = _Conn(
        {
            "id": 91,
            "status": "failed",
            "last_error": {
                "reason": "provider_5xx",
                "reason_detail": "contact secret.person@example.com at https://secret.invalid/path?token=x",
                "provider": "https://secret.invalid/provider",
                "stage": "https://secret.invalid/stage",
            },
            "last_error_category": "provider",
            "next_retry_at": None,
            "created_at": "2026-08-21T11:00:00+00:00",
            "started_at": "2026-08-21T11:00:01+00:00",
            "updated_at": "2026-08-21T11:00:02+00:00",
        }
    )

    result = content_fit_analysis._content_fit_job_snapshot(conn, 42, job_id=91)

    assert result is not None
    serialized = str(result)
    assert "secret.person@example.com" not in serialized
    assert "https://secret.invalid" not in serialized
    assert result["reason"] == "provider_5xx"
    assert result["reason_detail"] == "provider_5xx"
    assert result["stage"] is None
    assert "last_error" not in result and "provider" not in result
