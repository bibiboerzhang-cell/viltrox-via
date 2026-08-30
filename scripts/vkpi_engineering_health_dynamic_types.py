"""Value objects shared by the engineering-health dynamic-import analyzer."""
from __future__ import annotations

from dataclasses import dataclass


MAX_DOMAIN_VALUES = 4096
Domain = tuple[object, ...]


@dataclass(frozen=True)
class FrozenMap:
    """Hashable mapping value used by the static evaluator."""

    items: tuple[tuple[object, object], ...]


@dataclass(frozen=True)
class DynamicImportFinding:
    path: str
    line: int
    column: int
    callee: str
    targets: tuple[str, ...]
    missing_targets: tuple[str, ...]
    resolution_kind: str | None
    reason: str | None
    literal: bool


@dataclass(frozen=True)
class CallSite:
    symbol: str
    positional: tuple[Domain | None, ...]
    keywords: tuple[tuple[str, Domain | None], ...]
