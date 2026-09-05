"""Typed read outcomes for one Apify run; never authorize a replacement run."""
from __future__ import annotations

from typing import Any


class ActorRunError(RuntimeError):
    """A failed/incomplete fetch, distinct from a successfully empty dataset."""

    def __init__(
        self, code: str, *, provider_outcome_unknown: bool = False,
        partial_items: list[dict[str, Any]] | None = None, run_id: str = "",
    ) -> None:
        self.code = code
        self.provider_outcome_unknown = bool(provider_outcome_unknown)
        self.partial_items = list(partial_items or [])
        self.run_id = str(run_id or "")
        # A known run can be reconciled/resumed; it is not permission to start
        # another actor.  Missing credentials require operator action too.
        self.retry_safe = False
        super().__init__(code)

    def as_result(self, platform: str, query: str = "", market: str = "") -> dict[str, Any]:
        status = "partial" if self.partial_items else (
            "not_configured" if self.code == "actor_not_configured" else "failed"
        )
        return {
            "status": status, "platform": platform, "query": query, "market": market,
            "items": list(self.partial_items),
            "metadata": {
                "provider_status": status, "error_code": self.code,
                "provider_outcome_unknown": self.provider_outcome_unknown,
                "retry_safe": False, "run_id": self.run_id,
                "limit_reached": self.code == "actor_dataset_limit_reached",
                "has_more": False, "pagination_supported": False,
                "exhaustion_proven": False,
            },
        }


def read_actor_dataset(client: Any, run: Any, *, max_items: int = 2000) -> list[dict[str, Any]]:
    """Read at most 2,000 rows plus one overflow probe from an existing run."""
    limit = max(1, min(2000, int(max_items)))
    payload = run if isinstance(run, dict) else {}
    run_id = str(payload.get("id") or "")
    state = str(payload.get("status") or "").upper()
    if state != "SUCCEEDED":
        raise ActorRunError(
            "actor_run_incomplete", run_id=run_id,
            provider_outcome_unknown=state not in {"FAILED", "TIMED-OUT", "ABORTED"},
        )
    dataset_id = str(payload.get("defaultDatasetId") or "")
    if not dataset_id:
        raise ActorRunError("actor_dataset_missing", run_id=run_id)
    items: list[dict[str, Any]] = []
    try:
        for item in client.dataset(dataset_id).iterate_items():
            if len(items) >= limit:
                raise ActorRunError("actor_dataset_limit_reached", partial_items=items, run_id=run_id)
            if not isinstance(item, dict):
                raise ValueError("invalid dataset row")
            items.append(item)
    except ActorRunError:
        raise
    except Exception as exc:
        raise ActorRunError("actor_dataset_read_failed", partial_items=items, run_id=run_id) from exc
    return items


def crawler_failure(error: ActorRunError, platform: str) -> dict[str, Any]:
    """Keep the legacy crawler status while adding the shared result contract."""
    result = error.as_result(platform)
    return {**result, "provider": platform, **result["metadata"],
            "provider_status": "error", "sync_status": "error", "error": error.code}


def bounded_actor_metadata(*, since: str = "", date_filter: str = "none") -> dict[str, Any]:
    """No invented cursor or proof that a bounded actor exhausted an account."""
    return {"pagination_supported": False, "has_more": False,
            "exhaustion_proven": False,
            "pagination_unsupported_reason": "actor_input_schema_has_no_cursor",
            "since_requested": str(since or ""), "date_filter": date_filter,
            "date_window_complete": False}
