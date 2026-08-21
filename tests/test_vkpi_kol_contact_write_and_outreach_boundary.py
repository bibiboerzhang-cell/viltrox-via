from __future__ import annotations

from typing import Any

from app.api.routers import vkpi_kol_pool_intel
from app.domains.kol import business_contact_extract, contact_acquisition_queue
from app.domains.kol import contact_ingest, contact_system, outreach_pack


RAW_EMAIL = "Creator.Team@Example.COM"
NORMALIZED_EMAIL = "creator.team@example.com"
RAW_HANDLE = "@Creator.Team"
NORMALIZED_HANDLE = "@creator.team"
WRITER = {
    "id": 41,
    "staff_id": 41,
    "active": 1,
    "role": "employee",
    "permissions": {"vkpi": "write"},
    "organization_id": 1,
    "organization_scope_status": "resolved",
}


class _ExistingKolConn:
    def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> "_ExistingKolConn":
        return self

    def fetchone(self) -> dict[str, int]:
        return {"id": 7}


def _contains_contact_value(payload: Any) -> bool:
    rendered = str(payload).casefold()
    return any(
        marker.casefold() in rendered
        for marker in (RAW_EMAIL, NORMALIZED_EMAIL, RAW_HANDLE, NORMALIZED_HANDLE)
    )


def test_manual_contact_post_uses_canonical_observed_ingest_and_returns_no_values(
    monkeypatch,
) -> None:
    """The write endpoint may persist PII, but may never echo it to the client."""

    ingested: list[dict[str, Any]] = []
    refreshes: list[int] = []

    def canonical_ingest(**kwargs: Any) -> dict[str, Any]:
        normalized = contact_ingest.normalize_contact(
            kwargs["contact_type"], kwargs["contact_value"]
        )
        ingested.append(
            {
                **kwargs,
                "normalized_channel": normalized.channel,
                "normalized_value": normalized.normalized_value,
            }
        )
        return {
            "contact_id": len(ingested),
            "kol_pool_id": kwargs["kol_pool_id"],
            "channel": normalized.channel,
            "verification_status": kwargs["verification_status"],
            "inserted": True,
            "evidence_id": 100 + len(ingested),
            "promoted": False,
        }

    monkeypatch.setattr(business_contact_extract, "get_conn", lambda: _ExistingKolConn())
    monkeypatch.setattr(
        vkpi_kol_pool_intel,
        "_assert_private_kol_target",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(contact_ingest, "ingest_contact", canonical_ingest)
    monkeypatch.setattr(
        contact_system,
        "refresh_contactability",
        lambda kol_pool_id, **_kwargs: refreshes.append(int(kol_pool_id)) or {},
    )

    result = vkpi_kol_pool_intel.add_kol_manual_contact(
        7,
        {"email": RAW_EMAIL, "platform": "Instagram", "handle": RAW_HANDLE},
        staff=WRITER,
    )

    assert len(ingested) == 2
    assert {
        (row["normalized_channel"], row["normalized_value"])
        for row in ingested
    } == {("email", NORMALIZED_EMAIL), ("instagram_dm", NORMALIZED_HANDLE)}
    assert all(row["kol_pool_id"] == 7 for row in ingested)
    assert all(row["source_type"] == "manual" for row in ingested)
    assert all(row["source_field"].startswith("operator.") for row in ingested)
    assert all(row["verification_status"] == "observed" for row in ingested)
    assert all(row["confidence"] == 1.0 for row in ingested)
    assert all(row["is_public_declared"] is False for row in ingested)
    assert all(row["consent_basis"] == "manual_entry" for row in ingested)
    assert all(row["staff_id"] == 41 for row in ingested)
    assert all(row["conn"].fetchone() == {"id": 7} for row in ingested)
    assert refreshes == [7]

    assert result["status"] == "saved"
    assert result["saved"] == 2
    assert result["rejected"] == 0
    assert result["contacts"] == []
    assert result["contact_masked"] is True
    assert result["verification_status"] == "observed"
    assert result["provider_calls"] is False
    assert not _contains_contact_value(result)


def test_outreach_pack_missing_email_only_enqueues_provider_free_l0_when_flag_on(
    monkeypatch,
) -> None:
    """Generating copy must never turn a missing email into a provider scrape."""

    queue_calls: list[dict[str, Any]] = []
    forbidden_calls: list[str] = []

    def enqueue(kol_pool_id: int, **kwargs: Any) -> dict[str, Any]:
        call = {"kol_pool_id": kol_pool_id, **kwargs}
        queue_calls.append(call)
        assert not _contains_contact_value(call)
        return {"status": "pending_l0", "provider_calls": False}

    def forbidden(name: str):
        def _raise(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            forbidden_calls.append(name)
            raise AssertionError(f"provider/contact enrichment boundary crossed: {name}")

        return _raise

    monkeypatch.setattr(business_contact_extract, "_flag_enabled", lambda: True)
    assert business_contact_extract._flag_enabled() is True
    monkeypatch.setattr(
        business_contact_extract,
        "enrich_business_contacts",
        forbidden("enrich_business_contacts"),
    )
    monkeypatch.setattr(
        business_contact_extract,
        "_apify_scrape_about",
        forbidden("_apify_scrape_about"),
    )
    monkeypatch.setattr(contact_acquisition_queue, "enqueue_contact_acquisition", enqueue)

    monkeypatch.setattr(outreach_pack, "get_conn", lambda: object())
    monkeypatch.setattr(
        outreach_pack,
        "_kol_row",
        lambda *_args: {
            "id": 7,
            "email": "",
            "handle": "creator",
            "display_name": "Creator",
            "platform": "youtube",
            "bio": "cinematic street photography",
        },
    )
    monkeypatch.setattr(outreach_pack, "_read_pack_cache", lambda *_args: None)
    monkeypatch.setattr(outreach_pack, "_content_fit_snapshot", lambda *_args: {})
    monkeypatch.setattr(outreach_pack, "_build_brief", lambda *_args: {})
    monkeypatch.setattr(outreach_pack, "_personalization_context", lambda *_args: {})
    monkeypatch.setattr(outreach_pack, "_critic_context", lambda *_args: {})
    monkeypatch.setattr(
        outreach_pack,
        "_generate_email_draft",
        lambda *_args, **_kwargs: (
            {"subject": "draft", "email_en": "hello"},
            {"model": "rule_template", "cost_cents": 0},
        ),
    )
    monkeypatch.setattr(outreach_pack, "_write_pack_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        outreach_pack,
        "_email_status",
        lambda *_args, **_kwargs: {
            "state": "missing",
            "contact_masked": True,
            "contact_projection_reason": "summary_only",
        },
    )

    result = outreach_pack.generate_outreach_pack(7, force=True, staff=WRITER)

    assert forbidden_calls == []
    assert queue_calls == [{"kol_pool_id": 7, "trigger_source": "reconcile"}]
    assert result["state"] == "ready"
    assert result["email"]["state"] == "missing"
    assert result["email"]["enrich"] == {
        "attempted": True,
        "status": "queued_l0",
        "reason": "",
        "queue_status": "pending_l0",
        "provider_calls": False,
    }
    assert result["cached"] is False
