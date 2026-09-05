"""Deadline accounting around Shopify credential preparation and one API call."""
import math


def call_with_deadline(timeout_seconds, credentials, request, query, variables, *, clock):
    remaining = float(timeout_seconds)
    if not math.isfinite(remaining) or remaining <= 0:
        return {"ok": False, "reason": "deadline_exceeded"}
    started = clock()
    prepared = credentials()
    remaining -= clock() - started
    if remaining <= 0:
        return {"ok": False, "reason": "deadline_exceeded"}
    return request(prepared, query, variables, timeout_seconds=remaining)
