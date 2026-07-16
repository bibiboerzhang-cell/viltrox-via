from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool, vkpi_kol_pool_intel
from app.domains.kol import business_contact_extract


def test_contact_save_does_not_expose_internal_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        business_contact_extract,
        "add_manual_contact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("postgres secret")),
    )

    with pytest.raises(HTTPException) as exc_info:
        vkpi_kol_pool_intel.add_kol_manual_contact(
            12,
            {"email": "creator@example.com"},
            staff={"id": 7},
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["code"] == "kol_contact_save_failed"
    assert exc_info.value.detail["correlation_id"]
    assert "secret" not in str(exc_info.value.detail)


def test_needs_analysis_does_not_expose_internal_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vkpi_kol_pool.kol_video_analysis_enqueue,
        "list_kols_needing_video_analysis",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("queue secret")),
    )

    with pytest.raises(HTTPException) as exc_info:
        vkpi_kol_pool.list_kol_pool_needs_analysis(limit=50, staff={"id": 7})

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "needs_analysis_queue_unavailable"
    assert exc_info.value.detail["retryable"] is True
    assert "secret" not in str(exc_info.value.detail)
