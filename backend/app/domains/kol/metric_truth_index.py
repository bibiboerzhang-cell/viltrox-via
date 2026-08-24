"""Single-pass indexing helpers for Pool raw metric evidence."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any


def build_raw_metric_evidence_index(
    raw: Any,
    fields: Iterable[str],
    *,
    field_aliases: Mapping[str, set[str]],
    content_aliases: Mapping[str, set[str]],
    walk: Callable[..., Iterable[dict[str, Any]]],
    normalize_key: Callable[[Any], str],
    parse_number: Callable[[Any], int | float | None],
    record_failed: Callable[[Mapping[str, Any]], bool],
    content_record: Callable[[Mapping[str, Any]], bool],
) -> tuple[list[dict[str, Any]], dict[str, tuple[list[int | float], list[int | float]]]]:
    """Index explicit and content-sample metrics without five full raw walks.

    Content records intentionally keep the legacy nested-walk behavior: each
    qualifying content object owns one bounded traversal, so nested content
    samples and their historical duplicate/sample semantics remain unchanged.
    """
    active = tuple(dict.fromkeys(fields))
    evidence = {field: ([], []) for field in active}
    explicit_fields: dict[str, list[str]] = {}
    content_fields: dict[str, list[str]] = {}
    for field in active:
        for alias in field_aliases.get(field) or ():
            explicit_fields.setdefault(normalize_key(alias), []).append(field)
        for alias in content_aliases.get(field) or ():
            content_fields.setdefault(normalize_key(alias), []).append(field)

    records = list(walk(raw))
    for record in records:
        if record_failed(record):
            continue
        for raw_key, raw_value in record.items():
            targets = explicit_fields.get(normalize_key(raw_key)) or ()
            if not targets:
                continue
            parsed = parse_number(raw_value)
            if parsed is not None:
                for field in targets:
                    evidence[field][0].append(parsed)

    for record in records:
        if not content_record(record):
            continue
        for nested in walk(record):
            if record_failed(nested):
                continue
            for raw_key, raw_value in nested.items():
                targets = content_fields.get(normalize_key(raw_key)) or ()
                if not targets:
                    continue
                parsed = parse_number(raw_value)
                if parsed is not None:
                    for field in targets:
                        evidence[field][1].append(parsed)
    return records, evidence
