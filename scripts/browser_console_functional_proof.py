"""Hermetic validation for the browser gate's boolean/count function proof."""

from __future__ import annotations

from typing import Any, Mapping


FUNCTIONAL_ASK_BOOL_FIELDS = (
    "attempted",
    "trigger_present",
    "dialog_present",
    "suggestion_applied",
    "query_present",
    "ask_not_started_before_search",
    "ask_clicked",
    "completed",
    "failure_absent",
    "answer_present",
    "same_origin_api_idle",
)
FUNCTIONAL_ASK_COUNT_FIELDS = (
    "answer_char_count",
    "fact_count",
    "evidence_count",
    "intelligent_api_2xx_count",
    "ui_global_search_api_2xx_count",
)
FUNCTIONAL_SEARCH_BOOL_FIELDS = (
    "ui_search_completed",
    "ui_usable_state",
    "ui_results_rendered",
    "ui_trustworthy_empty",
    "ui_partial_or_forbidden_absent",
    "ui_error_absent",
    "request_completed",
    "same_origin",
    "http_2xx",
    "source_status_present",
    "required_sources_present",
    "source_status_values_valid",
    "all_sources_ready",
    "result_counts_valid",
    "result_counts_match_arrays",
)
FUNCTIONAL_SEARCH_COUNT_FIELDS = (
    "ui_result_count",
    "required_source_count",
    "ready_source_count",
    "result_count_total",
    "result_item_total",
)


def evaluate_deadline_proof(
    run: Mapping[str, Any],
    *,
    failures: list[str],
) -> dict[str, Any]:
    overall_timeout_ms = run.get("overall_timeout_ms")
    overall_elapsed_ms = run.get("overall_elapsed_ms")
    proof = {
        "overall_timeout_ms": (
            overall_timeout_ms
            if isinstance(overall_timeout_ms, int)
            and not isinstance(overall_timeout_ms, bool)
            and 60_000 <= overall_timeout_ms <= 1_080_000
            else 0
        ),
        "overall_elapsed_ms": (
            overall_elapsed_ms
            if isinstance(overall_elapsed_ms, int)
            and not isinstance(overall_elapsed_ms, bool)
            and overall_elapsed_ms >= 0
            else 0
        ),
        "deadline_not_exhausted": run.get("overall_deadline_exhausted") is False,
        "elapsed_within_timeout": (
            isinstance(overall_timeout_ms, int)
            and not isinstance(overall_timeout_ms, bool)
            and isinstance(overall_elapsed_ms, int)
            and not isinstance(overall_elapsed_ms, bool)
            and 0 <= overall_elapsed_ms <= overall_timeout_ms
        ),
    }
    if proof["overall_timeout_ms"] == 0:
        failures.append("browser overall timeout is missing or outside reviewed bounds")
    if (
        not isinstance(overall_elapsed_ms, int)
        or isinstance(overall_elapsed_ms, bool)
        or overall_elapsed_ms < 0
    ):
        failures.append("browser overall elapsed time is missing or invalid")
    if not proof["deadline_not_exhausted"]:
        failures.append("browser overall deadline was exhausted")
    if not proof["elapsed_within_timeout"]:
        failures.append("browser capture elapsed time exceeds its overall deadline")
    return proof


def evaluate_functional_proof(
    payload: Mapping[str, Any],
    *,
    failures: list[str],
) -> tuple[dict[str, Any], dict[str, bool]]:
    raw_proof = payload.get("functional_proof")
    if not isinstance(raw_proof, Mapping):
        failures.append("functional_proof must be an object")
        raw_proof = {}
    if set(raw_proof) != {"ask_find", "global_search"}:
        failures.append("functional_proof must contain only ask_find and global_search")

    def sanitize_section(
        name: str,
        bool_fields: tuple[str, ...],
        count_fields: tuple[str, ...],
    ) -> dict[str, Any]:
        raw = raw_proof.get(name)
        if not isinstance(raw, Mapping):
            failures.append(f"functional_proof.{name} must be an object")
            raw = {}
        expected = set(bool_fields) | set(count_fields)
        if set(raw) != expected:
            failures.append(
                f"functional_proof.{name} must contain only the reviewed boolean/count fields"
            )
        sanitized: dict[str, Any] = {}
        for field in bool_fields:
            value = raw.get(field)
            if not isinstance(value, bool):
                failures.append(f"functional_proof.{name}.{field} must be boolean")
                value = False
            sanitized[field] = value
        for field in count_fields:
            value = raw.get(field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or value > 1_000_000_000
            ):
                failures.append(
                    f"functional_proof.{name}.{field} must be a bounded non-negative integer"
                )
                value = 0
            sanitized[field] = value
        return sanitized

    ask = sanitize_section(
        "ask_find",
        FUNCTIONAL_ASK_BOOL_FIELDS,
        FUNCTIONAL_ASK_COUNT_FIELDS,
    )
    search = sanitize_section(
        "global_search",
        FUNCTIONAL_SEARCH_BOOL_FIELDS,
        FUNCTIONAL_SEARCH_COUNT_FIELDS,
    )

    ask_checks = {
        **{field: ask[field] is True for field in FUNCTIONAL_ASK_BOOL_FIELDS},
        "answer_char_count_positive": ask["answer_char_count"] > 0,
        "fact_count_positive": ask["fact_count"] > 0,
        "evidence_count_positive": ask["evidence_count"] > 0,
        "intelligent_api_2xx_observed": ask["intelligent_api_2xx_count"] >= 1,
        "ui_global_search_api_2xx_observed": ask["ui_global_search_api_2xx_count"] >= 1,
    }
    search_required_true_fields = set(FUNCTIONAL_SEARCH_BOOL_FIELDS) - {
        "ui_results_rendered",
        "ui_trustworthy_empty",
    }
    search_checks = {
        **{field: search[field] is True for field in search_required_true_fields},
        "ui_results_xor_trustworthy_empty": (
            search["ui_results_rendered"] is not search["ui_trustworthy_empty"]
        ),
        "ui_result_count_matches_state": (
            search["ui_result_count"] > 0
            if search["ui_results_rendered"]
            else search["ui_result_count"] == 0
        ),
        "required_source_count_exact": search["required_source_count"] == 3,
        "ready_source_count_exact": search["ready_source_count"] == 3,
        "result_totals_equal": search["result_count_total"] == search["result_item_total"],
    }
    ask_pass = all(ask_checks.values())
    search_pass = all(search_checks.values())
    if not ask_pass:
        failures.append(
            "functional Ask & Find proof failed: "
            + ", ".join(sorted(field for field, passed in ask_checks.items() if not passed))
        )
    if not search_pass:
        failures.append(
            "global search source-truth proof failed: "
            + ", ".join(sorted(field for field, passed in search_checks.items() if not passed))
        )
    return {
        "ask_find": ask,
        "global_search": search,
    }, {
        "ask_find_pass": ask_pass,
        "global_search_pass": search_pass,
        "pass": ask_pass and search_pass,
    }
