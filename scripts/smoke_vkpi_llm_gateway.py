#!/usr/bin/env python3
"""Offline smoke for V-KPI LLM gateway.

This smoke must not call external LLM providers. It forces offline mode and
verifies fallback, ledger writes, stats fields, and score compatibility.
Monthly environment budget warnings are record-only in the current gateway;
the dedicated budget tests own hard-stop semantics.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import os
import sys
import time
import asyncio
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")
os.environ["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] = "1"
os.environ["LLM_MONTHLY_BUDGET_USD"] = "0"

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.core.model_registry import current_task_model_binding  # noqa: E402
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema  # noqa: E402
from app.platform import llm_gateway  # noqa: E402


# This smoke isolates budget/offline fallback semantics.  Readiness has its own
# fail-closed contract tests, so acknowledge only the exact current bindings in
# this disposable process; force-offline remains enabled and provider I/O is
# still impossible.  The acknowledgement does not alter signed readiness.
os.environ["VKPI_LLM_READINESS_OPERATOR_ACK"] = ",".join(
    sorted(set(current_task_model_binding().values()))
)


MARKER = f"vkpi-llm-gateway-smoke-{int(time.time())}"


def _count_marker() -> int:
    row = get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_llm_calls WHERE purpose LIKE ?", (f"{MARKER}%",)).fetchone()
    return int(row["n"] if row else 0)


def _cleanup() -> int:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_llm_calls WHERE purpose LIKE ?", (f"{MARKER}%",))
    conn.commit()
    return _count_marker()


def _assert(condition: bool, message: str, payload: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{message}: {payload!r}")


def main() -> None:
    ensure_vkpi_product_industry_schema()
    _cleanup()

    providers = llm_gateway.configured_providers()
    _assert(providers == ["rule_v0"], "offline mode should expose only rule_v0", providers)

    offline_with_zero_budget = llm_gateway.invoke(
        "Summarize this smoke test.",
        purpose=f"{MARKER}-budget",
        max_output_tokens=50,
        cost_tag="cron:p4_recommendation_reasons",
    )
    _assert(
        offline_with_zero_budget.get("provider") == "rule_v0",
        "offline zero-budget fallback provider",
        offline_with_zero_budget,
    )
    _assert(
        offline_with_zero_budget.get("reason") == "all_providers_failed",
        "offline zero-budget fallback reason",
        offline_with_zero_budget,
    )
    _assert(
        offline_with_zero_budget.get("fallback_used") is True,
        "offline zero-budget fallback flag",
        offline_with_zero_budget,
    )
    _assert(
        all((item or {}).get("status") == "not_configured" for item in offline_with_zero_budget.get("errors") or []),
        "force-offline candidates must stay unconfigured",
        offline_with_zero_budget,
    )

    forced_offline = llm_gateway.invoke(
        "Try all providers but stay offline.",
        purpose=f"{MARKER}-offline",
        max_output_tokens=50,
        skip_budget_check=True,
        preferred_provider="openai",
        cost_tag="cron:p4_recommendation_reasons",
    )
    _assert(forced_offline.get("provider") == "rule_v0", "offline fallback provider", forced_offline)
    _assert(forced_offline.get("reason") == "all_providers_failed", "offline fallback reason", forced_offline)
    errors = forced_offline.get("errors") or []
    _assert(any((item or {}).get("provider") == "openai" and (item or {}).get("status") == "not_configured" for item in errors), "openai offline attempt recorded", forced_offline)

    before = _count_marker()
    llm_gateway.record_call(provider="rule_v0", model="rule_v0", purpose=f"{MARKER}-manual", prompt="manual", status="manual_smoke", fallback_used=True, metadata={"marker": MARKER})
    after = _count_marker()
    _assert(after == before + 1, "record_call should increase marker count", {"before": before, "after": after})

    score = llm_gateway.score(
        {"followers": 10, "views": 100},
        purpose=f"{MARKER}-score",
        triggered_by="system:vkpi_llm_gateway_smoke",
        metadata={"marker": MARKER},
        staff=None,
    )
    _assert(score.get("fallback") == "rule_v0" and score.get("status") == "not_configured", "score compatibility", score)

    stats = llm_gateway.stats(limit=5)
    required = {"summary", "calls", "configured_providers", "monthly_budget_usd", "monthly_spent_usd", "monthly_remaining_usd", "full_prompt_readable"}
    _assert(required.issubset(stats.keys()), "stats missing fields", stats)
    _assert(stats.get("full_prompt_readable") is False, "stats must not expose full prompts", stats)

    residue = _cleanup()
    _assert(residue == 0, "smoke residue not cleaned", residue)
    stdout_out(json.dumps({"ok": True, "marker": MARKER, "providers": providers}, ensure_ascii=False))
    stdout_out("VKPI_LLM_GATEWAY_SMOKE_OK")


if __name__ == "__main__":
    try:
        main()
    finally:
        asyncio.run(close_db_runtime())
