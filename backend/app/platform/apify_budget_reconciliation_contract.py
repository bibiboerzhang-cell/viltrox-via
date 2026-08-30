"""Injected seams and value objects for legacy Apify budget repair."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable


CallableAny = Callable[..., Any]


@dataclass(frozen=True)
class ReconciliationDependencies:
    ensure_schema: CallableAny
    get_conn: CallableAny
    is_postgres_runtime: Callable[[], bool]
    utcnow: CallableAny
    parse_time: CallableAny
    iso: CallableAny
    money: CallableAny
    json_object: CallableAny
    positive_int: CallableAny
    json_dumps: CallableAny
    budget_scope: str
    terminal_run_states: frozenset[str]
    reconciliation_audit_key: str
    cap_repair_audit_key: str


@dataclass(frozen=True)
class LegacyExpectation:
    key: str
    run_id: str
    status: str
    actual: Decimal
    ledger_id: int


@dataclass(frozen=True)
class CapRepairExpectation:
    base: LegacyExpectation
    settled_at: Any
    spend: dict[str, Decimal]


class ReconciliationRejected(Exception):
    """Expected fail-closed outcome inside a transaction."""

    def __init__(self, reason: str, **extra: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.extra = extra


def reject(reason: str, **extra: Any) -> None:
    raise ReconciliationRejected(reason, **extra)
