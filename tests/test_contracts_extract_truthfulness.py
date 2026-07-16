from __future__ import annotations

import json
from typing import Any

import pytest

from app.domains.projects import contracts_extract


class _Result:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _Conn:
    def __init__(
        self,
        *,
        status: str = "processing",
        raw: dict[str, Any] | str | None = None,
        manual: dict[str, Any] | str | None = None,
        confirmed: dict[str, Any] | str | None = None,
        confirm_after_select: bool = False,
    ) -> None:
        self.row = {
            "status": status,
            "extraction_status": "processing",
            "raw_extracted_json": raw or {},
            "manual_overrides_json": manual or {},
            "field_confirmed_json": confirmed or {},
        }
        self.contract_updates: list[tuple[str, tuple[Any, ...]]] = []
        self.cache_writes: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0
        self.confirm_after_select = confirm_after_select

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT raw_extracted_json FROM vkpi_project_contracts"):
            return _Result({"raw_extracted_json": self.row["raw_extracted_json"]})
        if normalized.startswith("SELECT status, manual_overrides_json, field_confirmed_json"):
            result = _Result(dict(self.row))
            if self.confirm_after_select:
                self.row["status"] = "confirmed"
            return result
        if normalized.startswith("UPDATE vkpi_project_contracts"):
            self.contract_updates.append((normalized, params))
            if "extraction_status='failed'" in normalized:
                if self.row["status"] != "confirmed":
                    self.row["status"] = "needs_review"
                self.row["extraction_status"] = "failed"
                self.row["raw_extracted_json"] = json.loads(str(params[0]))
            elif "SET extraction_status='ready'" in normalized:
                self.row["extraction_status"] = "ready"
                self.row["raw_extracted_json"] = json.loads(str(params[0]))
            elif "ELSE 'extracted' END" in normalized:
                if self.row["status"] != "confirmed":
                    self.row["status"] = "extracted"
                self.row["extraction_status"] = "ready"
            return _Result()
        if normalized.startswith("INSERT INTO vkpi_analysis_cache"):
            self.cache_writes.append((normalized, params))
            return _Result()
        raise AssertionError(f"unexpected SQL: {normalized}")

    def commit(self) -> None:
        self.commits += 1


@pytest.fixture(autouse=True)
def _mock_external_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        contracts_extract,
        "_record_contract_cost",
        lambda *_args, **_kwargs: {"id": "mock-cost"},
    )
    monkeypatch.setattr(contracts_extract.audit, "log_business_event", lambda **_kwargs: None)


def _valid_extraction() -> dict[str, Any]:
    return {
        "ok": True,
        "model": "fixture-model",
        "usage_metadata": {"input_tokens": 10, "output_tokens": 20},
        "cost_usd": 0.01,
        "extracted": {
            "summary": "One paid Instagram deliverable.",
            "fee_amount": 1250,
            "fee_currency": "EUR",
            "contract_duration": "",
            "start_date": None,
            "end_date": None,
            "platforms": ["Instagram"],
            "deliverable_count": 1,
            "deliverables": [
                {
                    "platform": "Instagram",
                    "content_type": "Reel",
                    "quantity": 1,
                    "deadline": "2026-08-15",
                    "notes": "",
                }
            ],
            "must_include": [],
            "usage_rights": "Organic social for 90 days",
            "exclusivity": "",
            "buyout_rights": "",
            "breach_terms": "",
            "payment_terms": "Net 30",
            "cancellation_terms": "",
            "revision_terms": "",
            "promised_publish_deadline": None,
            "risk_flags": [],
            "missing_or_unclear_fields": [],
            "field_confidence": {
                "fee_amount": 0.98,
                "fee_currency": 0.98,
                "contract_duration": 0,
                "start_date": 0,
                "end_date": 0,
                "platforms": 0.95,
                "deliverable_count": 0.95,
                "deliverables": 0.95,
                "must_include": 0,
                "usage_rights": 0.9,
                "exclusivity": 0,
                "buyout_rights": 0,
                "breach_terms": 0,
                "payment_terms": 0.9,
                "cancellation_terms": 0,
                "revision_terms": 0,
            },
            "evidence": {
                "fee_amount": "Page 2: Total fee EUR 1,250.",
                "deliverables": "Page 3: one Instagram Reel due August 15.",
                "usage_rights": "Page 4: organic social use for 90 days.",
                "payment_terms": "Page 2: payable net 30.",
            },
        },
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True, "extracted": {}},
        {
            "ok": True,
            "extracted": {"summary": "No contract facts", "field_confidence": {}, "evidence": {}},
        },
        {
            "ok": True,
            "extracted": {
                "fee_amount": "1250",
                "field_confidence": {"fee_amount": 0.9},
                "evidence": {"fee_amount": "Page 2"},
            },
        },
        {
            "ok": True,
            "extracted": {
                "deliverables": {"quantity": 1},
                "field_confidence": {"deliverables": 0.9},
                "evidence": {"deliverables": "Page 3"},
            },
        },
        {
            "ok": True,
            "extracted": {
                "deliverables": [{"platform": "Instagram", "quantity": "1"}],
                "field_confidence": {"deliverables": 0.9},
                "evidence": {"deliverables": "Page 3"},
            },
        },
        {
            "ok": True,
            "extracted": {
                "fee_amount": 1250,
                "field_confidence": {"fee_amount": "0.9"},
                "evidence": {"fee_amount": "Page 2"},
            },
        },
        {
            "ok": True,
            "extracted": {
                "fee_amount": 1250,
                "field_confidence": {"fee_amount": 0.9},
                "evidence": {},
            },
        },
    ],
    ids=[
        "empty-object",
        "metadata-only",
        "amount-wrong-type",
        "deliverables-wrong-type",
        "deliverable-child-wrong-type",
        "confidence-wrong-type",
        "missing-evidence",
    ],
)
def test_invalid_extractions_fail_and_preserve_link_keys(payload: dict[str, Any]) -> None:
    conn = _Conn(raw={"signed_version_of": 44, "superseded_by": 45})

    with pytest.raises(contracts_extract.ContractExtractionValidationError):
        contracts_extract._apply_extraction(conn, 7, 3, None, payload)

    assert conn.row["status"] == "needs_review"
    assert conn.row["extraction_status"] == "failed"
    assert conn.row["raw_extracted_json"]["signed_version_of"] == 44
    assert conn.row["raw_extracted_json"]["superseded_by"] == 45
    assert conn.row["raw_extracted_json"]["error"].startswith("contract_extraction_")
    assert not conn.cache_writes
    assert all("extraction_status='ready'" not in sql for sql, _params in conn.contract_updates)


def test_valid_full_fixture_remains_compatible_and_preserves_links() -> None:
    conn = _Conn(raw={"signed_version_of": 44})

    result = contracts_extract._apply_extraction(conn, 7, 3, None, _valid_extraction())

    assert result["cost_ledger"] == {"id": "mock-cost"}
    assert conn.row["status"] == "extracted"
    assert conn.row["extraction_status"] == "ready"
    update_sql, update_params = next(
        item for item in conn.contract_updates if "ELSE 'extracted' END" in item[0]
    )
    assert "fee_amount=CASE WHEN status='confirmed'" in update_sql
    assert "fee_currency=CASE WHEN status='confirmed'" in update_sql
    assert "platforms_json=CASE WHEN status='confirmed'" in update_sql
    assert "deliverables_json=CASE WHEN status='confirmed'" in update_sql
    assert "usage_rights=CASE WHEN status='confirmed'" in update_sql
    raw_payloads = [
        json.loads(value)
        for value in update_params
        if isinstance(value, str) and value.startswith("{")
    ]
    assert any(payload.get("signed_version_of") == 44 for payload in raw_payloads)
    assert conn.cache_writes


def test_missing_currency_is_not_defaulted_to_usd() -> None:
    conn = _Conn()
    payload = {
        "ok": True,
        "extracted": {
            "fee_amount": 500,
            "field_confidence": {"fee_amount": 0.95},
            "evidence": {"fee_amount": "Page 1: creator fee is 500."},
        },
    }

    contracts_extract._apply_extraction(conn, 7, 3, None, payload)

    update_sql, update_params = next(
        item for item in conn.contract_updates if "ELSE 'extracted' END" in item[0]
    )
    assert "fee_amount=CASE WHEN status='confirmed'" in update_sql
    assert "fee_currency=CASE WHEN status='confirmed'" not in update_sql
    assert "USD" not in update_params


def test_manual_and_field_confirmed_values_are_not_overwritten() -> None:
    conn = _Conn(
        manual={"fee_amount": 999},
        confirmed=json.dumps({"payment_terms": True}),
    )

    contracts_extract._apply_extraction(conn, 7, 3, None, _valid_extraction())

    update_sql, _params = next(
        item for item in conn.contract_updates if "ELSE 'extracted' END" in item[0]
    )
    assert "fee_amount=CASE WHEN status='confirmed'" not in update_sql
    assert "payment_terms=CASE WHEN status='confirmed'" not in update_sql
    assert "deliverables_json=CASE WHEN status='confirmed'" in update_sql


def test_manual_all_marker_protects_every_business_field() -> None:
    conn = _Conn(manual={"all": True})

    contracts_extract._apply_extraction(conn, 7, 3, None, _valid_extraction())

    update_sql, _params = next(
        item for item in conn.contract_updates if "ELSE 'extracted' END" in item[0]
    )
    assert "raw_extracted_json=?::jsonb" in update_sql
    assert "fee_amount=CASE WHEN status='confirmed'" not in update_sql
    assert "deliverables_json=CASE WHEN status='confirmed'" not in update_sql
    assert "payment_terms=CASE WHEN status='confirmed'" not in update_sql


def test_confirmation_race_is_protected_inside_update() -> None:
    conn = _Conn(confirm_after_select=True)

    contracts_extract._apply_extraction(conn, 7, 3, None, _valid_extraction())

    update_sql, _params = next(
        item for item in conn.contract_updates if "ELSE 'extracted' END" in item[0]
    )
    assert conn.row["status"] == "confirmed"
    assert conn.row["extraction_status"] == "ready"
    assert "status=CASE WHEN status='confirmed' THEN status ELSE 'extracted' END" in update_sql
    assert "fee_amount=CASE WHEN status='confirmed' THEN fee_amount ELSE ? END" in update_sql


def test_confirmed_contract_never_updates_business_fields() -> None:
    conn = _Conn(status="confirmed", raw={"signed_version_of": 44})

    contracts_extract._apply_extraction(conn, 7, 3, None, _valid_extraction())

    assert conn.row["status"] == "confirmed"
    assert conn.row["extraction_status"] == "ready"
    update_sql, _params = conn.contract_updates[-1]
    assert "SET extraction_status='ready'" in update_sql
    assert "fee_amount" not in update_sql
    assert conn.row["raw_extracted_json"]["signed_version_of"] == 44


def test_invalid_result_keeps_confirmed_status_but_marks_extraction_failed() -> None:
    conn = _Conn(status="confirmed", raw={"signed_version_of": 44})

    with pytest.raises(contracts_extract.ContractExtractionValidationError):
        contracts_extract._apply_extraction(conn, 7, 3, None, {"ok": True, "extracted": {}})

    assert conn.row["status"] == "confirmed"
    assert conn.row["extraction_status"] == "failed"
    assert conn.row["raw_extracted_json"]["signed_version_of"] == 44


def test_core_uses_mocked_model_and_rejects_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn(raw={"superseded_by": 88})
    monkeypatch.setattr(
        contracts_extract,
        "extract_contract_pdf_with_timeout",
        lambda *_args, **_kwargs: {"ok": True, "extracted": {}},
    )

    with pytest.raises(contracts_extract.ContractExtractionValidationError):
        contracts_extract._run_contract_extraction_core(
            conn,
            3,
            7,
            contract={"file_name": "contract.pdf", "r2_key": "unused"},
            context={"project_id": 3},
            staff=None,
            local_path="mock-contract.pdf",
        )

    assert conn.row["status"] == "needs_review"
    assert conn.row["extraction_status"] == "failed"
    assert conn.row["raw_extracted_json"]["superseded_by"] == 88
