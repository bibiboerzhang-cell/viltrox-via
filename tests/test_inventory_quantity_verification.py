from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_inventory
from app.domains import business_truth
from app.domains.access import scope
from app.domains.events import inventory_service


ROOT = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self, row: dict[str, Any] | None = None):
        self._row = row

    def fetchone(self):
        return self._row


class _InventoryConn:
    def __init__(self, *, update_succeeds: bool = True, verified: bool = False):
        self.update_succeeds = update_succeeds
        self.commits = 0
        self.rollbacks = 0
        self.sql: list[str] = []
        self.params: list[tuple[Any, ...]] = []
        self.current = {
            "id": "inv_1",
            "sku": "AF-35",
            "qty": 25,
            "quantity_status": "manual_confirmed" if verified else "unverified",
            "quantity_source": "wms_export" if verified else "manual_reference",
            "quantity_evidence_sha256": "a" * 64 if verified else None,
            "row_version": 3,
            "updated_at": "2026-07-15T12:00:00+00:00",
        }

    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        normalized = " ".join(sql.split())
        self.sql.append(normalized)
        self.params.append(tuple(params))
        if normalized.startswith("SELECT * FROM vkpi_inventory WHERE sku"):
            return _Cursor(dict(self.current))
        if normalized.startswith("UPDATE vkpi_inventory"):
            if not self.update_succeeds:
                return _Cursor(None)
            if "SET quantity_status=?" in normalized:
                self.current.update(
                    {
                        "quantity_status": params[0],
                        "quantity_source": params[1],
                        "quantity_source_ref": params[2],
                        "quantity_source_observed_at": params[3],
                        "quantity_evidence_sha256": params[4],
                        "quantity_verified_by_staff_id": params[5],
                        "quantity_verified_organization_id": params[6],
                        "row_version": 4,
                    }
                )
            else:
                self.current.update(
                    {
                        "quantity_status": "unverified",
                        "quantity_source": "verification_revoked",
                        "quantity_source_ref": None,
                        "quantity_evidence_sha256": None,
                        "row_version": 4,
                    }
                )
            return _Cursor(dict(self.current))
        if normalized.startswith("INSERT INTO vkpi_inventory_movements"):
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {normalized}; params={params}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _authorization() -> dict[str, Any]:
    return {
        "authorization_ref": "WAREHOUSE-2026-0715",
        "reason": "warehouse count reviewed",
        "confirmed_by_human": True,
        "actor_staff_id": 7,
    }


def _verify_body() -> dict[str, Any]:
    return {
        "source_type": "wms_export",
        "source_ref": "wms-export-2026-07-15.csv",
        "source_observed_at": "2026-07-15T11:55:00Z",
        "evidence_sha256": "a" * 64,
        "expected_id": "inv_1",
        "expected_qty": 25,
        "expected_row_version": 3,
        "expected_updated_at": "2026-07-15T12:00:00+00:00",
    }


def _configure_service(monkeypatch, conn: _InventoryConn):
    audit_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(inventory_service, "get_conn", lambda: conn)
    monkeypatch.setattr(inventory_service, "_ensure_inventory_verification_schema", lambda: None)
    monkeypatch.setattr(inventory_service, "ensure_vkpi_audit_schema", lambda: None)
    monkeypatch.setattr(
        inventory_service.audit,
        "log_business_event",
        lambda **kwargs: audit_calls.append(kwargs) or {"status": "logged"},
    )
    return audit_calls


def test_verify_quantity_binds_receipt_without_changing_quantity(monkeypatch) -> None:
    conn = _InventoryConn()
    audit_calls = _configure_service(monkeypatch, conn)

    result = inventory_service.verify_quantity(
        "AF-35",
        _verify_body(),
        authorization_evidence=_authorization(),
        staff={"id": 7, "role": "admin", "organization_id": 1},
    )

    assert result["verified"] is True
    assert result["quantity_changed"] is False
    assert result["item"]["qty"] == 25
    assert result["item"]["quantity_status"] == "source_confirmed"
    assert result["item"]["quantity_verified_organization_id"] == 1
    assert conn.commits == 1
    assert conn.rollbacks == 0
    update_sql = next(sql for sql in conn.sql if sql.startswith("UPDATE vkpi_inventory"))
    assert "SET qty" not in update_sql
    assert "qty=? AND row_version=? AND updated_at=?" in update_sql
    assert any("INSERT INTO vkpi_inventory_movements" in sql for sql in conn.sql)
    assert audit_calls[0]["action_type"] == "inventory_quantity_verify"
    assert audit_calls[0]["commit"] is False


def test_human_count_sources_are_manual_confirmed(monkeypatch) -> None:
    conn = _InventoryConn()
    _configure_service(monkeypatch, conn)
    body = {**_verify_body(), "source_type": "physical_count_sheet"}

    result = inventory_service.verify_quantity(
        "AF-35",
        body,
        authorization_evidence=_authorization(),
        staff={"id": 7, "role": "admin", "organization_id": 1},
    )

    assert result["item"]["quantity_status"] == "manual_confirmed"


def test_verify_quantity_stale_cas_rolls_back_without_audit(monkeypatch) -> None:
    conn = _InventoryConn(update_succeeds=False)
    audit_calls = _configure_service(monkeypatch, conn)

    with pytest.raises(inventory_service.InventoryVerificationConflict):
        inventory_service.verify_quantity(
            "AF-35",
            _verify_body(),
            authorization_evidence=_authorization(),
            staff={"id": 7, "role": "admin", "organization_id": 1},
        )

    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert audit_calls == []
    assert not any("INSERT INTO vkpi_inventory_movements" in sql for sql in conn.sql)


def test_verify_quantity_denies_non_default_organization(monkeypatch) -> None:
    conn = _InventoryConn()
    _configure_service(monkeypatch, conn)

    with pytest.raises(scope.ScopeDenied, match="organization scope unavailable"):
        inventory_service.verify_quantity(
            "AF-35",
            _verify_body(),
            authorization_evidence=_authorization(),
            staff={"id": 7, "role": "admin", "organization_id": 2},
        )

    assert not any(sql.startswith("UPDATE vkpi_inventory") for sql in conn.sql)


def test_revoke_quantity_verification_preserves_quantity_and_audits(monkeypatch) -> None:
    conn = _InventoryConn(verified=True)
    audit_calls = _configure_service(monkeypatch, conn)
    body = {
        "expected_id": "inv_1",
        "expected_qty": 25,
        "expected_row_version": 3,
        "expected_updated_at": "2026-07-15T12:00:00+00:00",
    }

    result = inventory_service.revoke_quantity_verification(
        "AF-35",
        body,
        authorization_evidence=_authorization(),
        staff={"id": 7, "role": "admin", "organization_id": 1},
    )

    assert result["verified"] is False
    assert result["quantity_changed"] is False
    assert result["item"]["qty"] == 25
    assert result["item"]["quantity_status"] == "unverified"
    assert audit_calls[0]["action_type"] == "inventory_quantity_verification_revoke"
    assert conn.commits == 1


def test_inventory_verify_route_rejects_quantity_mutation_field() -> None:
    with pytest.raises(HTTPException) as raised:
        vkpi_inventory.verify_quantity(
            "AF-35",
            {**_verify_body(), "qty": 999},
            staff={"id": 7, "role": "admin", "organization_id": 1},
        )
    assert raised.value.status_code == 400
    assert "unsupported request fields: qty" in str(raised.value.detail)


def test_inventory_verify_route_fails_closed_when_business_gate_disabled(monkeypatch) -> None:
    monkeypatch.setattr(business_truth, "manual_writes_enabled", lambda: False)
    with pytest.raises(HTTPException) as raised:
        vkpi_inventory.verify_quantity(
            "AF-35",
            {**_verify_body(), "authorization_evidence": _authorization()},
            staff={"id": 7, "role": "admin", "organization_id": 1},
        )
    assert raised.value.status_code == 409
    assert raised.value.detail["reason"] == "feature_disabled"


def test_inventory_verify_route_requires_owner_or_admin(monkeypatch) -> None:
    monkeypatch.setattr(business_truth, "manual_writes_enabled", lambda: True)
    with pytest.raises(HTTPException) as raised:
        vkpi_inventory.verify_quantity(
            "AF-35",
            {**_verify_body(), "authorization_evidence": _authorization()},
            staff={"id": 7, "role": "member", "organization_id": 1},
        )
    assert raised.value.status_code == 400
    assert raised.value.detail["reason"] == "owner_or_admin_required"


@pytest.mark.parametrize(
    "source_ref",
    [
        "https://user:password@example.com/count.csv",
        "https://example.com/count.csv?access_token=secret",
        "https://example.com/count.csv#signature=secret",
        "warehouse-sheet\nAuthorization: bearer secret",
    ],
)
def test_source_ref_rejects_credential_bearing_or_control_values(source_ref: str) -> None:
    with pytest.raises(ValueError):
        inventory_service._safe_source_ref(source_ref)


def test_source_ref_accepts_public_url_or_opaque_receipt_id() -> None:
    assert inventory_service._safe_source_ref(
        "https://warehouse.example/counts/2026-07-15.csv?version=3"
    ).endswith("?version=3")
    assert inventory_service._safe_source_ref("warehouse-count-2026-07-15") == "warehouse-count-2026-07-15"


def test_inventory_provenance_migration_never_confirms_existing_rows() -> None:
    up = (ROOT / "migrations/263_vkpi_inventory_quantity_provenance.sql").read_text()
    down = (ROOT / "migrations/263_vkpi_inventory_quantity_provenance_down.sql").read_text()

    assert "quantity_evidence_sha256" in up
    assert "quantity_verified_organization_id" in up
    assert "row_version" in up
    assert "SET quantity_status='manual_confirmed'" not in up
    assert "SET quantity_status='source_confirmed'" not in up
    assert "SET quantity_status='unverified'" in down


def test_sqlite_dev_schema_adds_receipt_columns_without_confirming_rows(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE vkpi_inventory (
          id TEXT PRIMARY KEY, sku TEXT UNIQUE, qty INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO vkpi_inventory (id,sku,qty,updated_at) VALUES ('inv_1','AF-35',25,'2026-07-15T12:00:00Z')"
    )
    conn.commit()
    monkeypatch.setattr(inventory_service, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(inventory_service, "get_conn", lambda: conn)

    inventory_service._ensure_inventory_verification_schema()

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(vkpi_inventory)").fetchall()}
    assert {
        "quantity_status",
        "quantity_source_ref",
        "quantity_evidence_sha256",
        "quantity_verified_organization_id",
        "row_version",
    } <= columns
    stored = dict(conn.execute("SELECT * FROM vkpi_inventory WHERE sku='AF-35'").fetchone())
    assert stored["qty"] == 25
    assert stored["quantity_status"] == "unverified"
    assert stored["quantity_source_ref"] is None
