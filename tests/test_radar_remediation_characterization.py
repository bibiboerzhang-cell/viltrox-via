"""Characterization fence for deterministic Event/Dealer remediation queues."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

import pytest

from app.domains.commerce import dealer_scrape
from app.domains.events import radar, radar_remediation


AS_OF = datetime(2026, 7, 13, 20, 0, 0, tzinfo=timezone.utc)


def _digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _task_order_digest(queue: dict[str, Any]) -> str:
    ordered_ids = "\n".join(task["task_id"] for task in queue["tasks"])
    return hashlib.sha256(ordered_ids.encode("utf-8")).hexdigest()


def test_event_and_dealer_preview_contract_is_byte_stable_and_input_pure() -> None:
    catalog = radar.load_reviewed_catalog()
    candidates = dealer_scrape.reviewed_candidates()
    catalog_before = deepcopy(catalog)
    candidates_before = deepcopy(candidates)

    event = radar_remediation.build_event_remediation_queue(catalog, as_of=AS_OF)
    dealer = radar_remediation.build_dealer_remediation_queue(candidates, as_of=AS_OF)

    assert catalog == catalog_before
    assert candidates == candidates_before
    assert _digest(event) == "acb5e6f198d84748784055d39c4e7d724e0cb99263fe597bf9fd181aca3453cd"
    assert _digest(dealer) == "76decb2b60125d74ad2031cd69aa04c95aec719f6336002715c60e4da8bd080d"
    assert _task_order_digest(event) == "094a6544783f9155dabcdeec7b7ad48cf1ca57d7e1c5652afa98421f33f67f8b"
    assert _task_order_digest(dealer) == "38bfdadc5e43ebb5c5d65e01a3711fcdd5618b05ff265df6c775bcbb53f82b9f"

    for queue, scope in ((event, "event"), (dealer, "dealer")):
        assert queue["queue"] == {
            "id": "vkpi.event_dealer.remediation",
            "version": 1,
            "scope": scope,
            "generated_at": "2026-07-13T20:00:00+00:00",
            "read_only": True,
            "preview_only": True,
            "network_accessed": False,
            "database_accessed": False,
            "business_rows_written": 0,
        }
        assert queue["claim_status"] == "descriptive_only"
        assert queue["persistence_policy"]["unreviewed_catalog_import_allowed"] is False
        assert queue["persistence_policy"]["queue_preview_can_write"] is False
        assert queue["persistence_policy"]["accepted_task_auto_imports_catalog"] is False


@pytest.mark.parametrize(
    ("builder", "payload"),
    [
        (radar_remediation.build_event_remediation_queue, {}),
        (radar_remediation.build_dealer_remediation_queue, []),
    ],
)
@pytest.mark.parametrize("value", [True, 0, -1])
def test_nonpositive_or_boolean_staleness_keeps_value_error_contract(
    builder: Callable[..., dict[str, Any]],
    payload: Any,
    value: Any,
) -> None:
    with pytest.raises(ValueError, match="stale_after_days must be a positive integer"):
        builder(payload, as_of=AS_OF, stale_after_days=value)


@pytest.mark.parametrize(
    ("builder", "payload"),
    [
        (radar_remediation.build_event_remediation_queue, {}),
        (radar_remediation.build_dealer_remediation_queue, []),
    ],
)
def test_none_staleness_keeps_type_error_contract(
    builder: Callable[..., dict[str, Any]],
    payload: Any,
) -> None:
    with pytest.raises(TypeError):
        builder(payload, as_of=AS_OF, stale_after_days=None)
