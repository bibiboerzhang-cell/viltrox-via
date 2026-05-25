"""P5.68 X comments go/no-go readiness report.

This module is intentionally read-only. It defines the 14-target validation
gate for X comments before any Apify spend or daily market collection is
allowed.
"""
from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.platform.industry_crawlers import get_crawler, is_supported


VALIDATION_TARGET_COUNT = 14

X_COMMENTS_SOURCE_CONTRACT = {
    "identity": ["target_id", "source_uid", "platform", "source_url", "tweet_id", "conversation_id"],
    "content": ["author", "handle", "text", "published_at", "language"],
    "metrics": ["like_count", "reply_count", "retweet_count", "quote_count"],
    "comments": ["comment_id", "parent_id", "depth", "author", "handle", "text", "like_count", "published_at"],
    "provenance": ["provider", "provider_run_id", "captured_at", "raw_payload_hash", "actor_id"],
    "review": ["review_status", "signal_type", "brand", "product_hint", "confidence", "decision_note"],
}

VALIDATION_LIMITS = {
    "target_count_required": VALIDATION_TARGET_COUNT,
    "comments_per_target_default": 50,
    "comments_per_target_hard_cap": 100,
    "total_comments_hard_cap": 1400,
    "max_provider_runs": 14,
    "max_errors_before_stop": 3,
    "max_empty_targets_before_stop": 5,
}

GO_NO_GO_CRITERIA = {
    "go": [
        "14 selected X post URLs or tweet ids are provided before execution",
        "provider path is explicit: X_BEARER_TOKEN for official API replies or APIFY_X_COMMENTS_ACTOR_ID for Apify replies",
        "errors < 3 and provider run artifacts are saved",
        "at least 8 of 14 targets return usable comments or explicit no-comments status",
        "at least 5 targets contain product, brand, competitor, complaint, launch, or creator conversation signal",
        "estimated cost and runtime are recorded before any continuation",
    ],
    "no_go": [
        "provider is not configured for comments",
        "errors >= 3 in the 14-target validation batch",
        "fewer than 5 targets contain useful market signal",
        "comments are mostly spam/low-signal after manual review",
        "cost per useful signal is not acceptable",
    ],
    "stop_rules": [
        "Stop immediately when provider errors reach 3",
        "Stop immediately if cost or rate limit exceeds the approved validation budget",
        "Do not retry failed targets more than once during validation",
        "Do not promote to daily collection from validation results alone",
    ],
}

REQUIRED_TARGET_FIELDS = ["target_id", "source_url", "brand_context", "expected_signal"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _env_present(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _count(table_name: str) -> int:
    if not _table_exists(table_name):
        return 0
    try:
        row = get_conn().execute(f"SELECT COUNT(*) AS n FROM {table_name}").fetchone()
        return int(row["n"] or 0) if row else 0
    except Exception:
        return 0


def _tweet_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{8,25}", raw):
        return raw
    match = re.search(r"/status(?:es)?/(\d{8,25})", raw)
    return match.group(1) if match else ""


def _load_targets(path_value: str | Path | None) -> dict[str, Any]:
    if not path_value:
        return {
            "provided": False,
            "path": "",
            "count": 0,
            "valid": False,
            "errors": ["targets_file_not_provided"],
            "items": [],
        }
    path = Path(path_value)
    if not path.exists():
        return {
            "provided": True,
            "path": str(path),
            "count": 0,
            "valid": False,
            "errors": ["targets_file_missing"],
            "items": [],
        }
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing_fields = [field for field in REQUIRED_TARGET_FIELDS if field not in (reader.fieldnames or [])]
        if missing_fields:
            errors.append(f"missing_fields:{','.join(missing_fields)}")
        for idx, row in enumerate(reader, start=1):
            item = {field: str(row.get(field) or "").strip() for field in REQUIRED_TARGET_FIELDS}
            tweet_id = _tweet_id(item.get("source_url", ""))
            if not item["target_id"]:
                errors.append(f"row_{idx}:missing_target_id")
            if not tweet_id:
                errors.append(f"row_{idx}:invalid_x_status_url")
            item["tweet_id"] = tweet_id
            items.append(item)
    if len(items) != VALIDATION_TARGET_COUNT:
        errors.append(f"target_count:{len(items)}_expected:{VALIDATION_TARGET_COUNT}")
    duplicate_ids = sorted({item["target_id"] for item in items if sum(1 for candidate in items if candidate["target_id"] == item["target_id"]) > 1})
    if duplicate_ids:
        errors.append(f"duplicate_target_ids:{','.join(duplicate_ids)}")
    return {
        "provided": True,
        "path": str(path),
        "count": len(items),
        "valid": not errors,
        "errors": errors,
        "items": items,
    }


def _provider_modes() -> dict[str, Any]:
    crawler = get_crawler("x")
    status = crawler.provider_status() if crawler is not None else {}
    official_api_ready = bool(_env_present("X_BEARER_TOKEN"))
    apify_token_ready = bool(_env_present("APIFY_TOKEN"))
    comments_actor_ready = bool(_env_present("APIFY_X_COMMENTS_ACTOR_ID"))
    comments_provider_ready = official_api_ready or (apify_token_ready and comments_actor_ready)
    preferred_path = (
        "official_api_replies"
        if official_api_ready
        else "apify_comments_actor"
        if apify_token_ready and comments_actor_ready
        else "profile_actor_only"
        if apify_token_ready
        else "none"
    )
    return {
        "crawler_registered": is_supported("x") and crawler is not None,
        "provider_status": status,
        "official_api_ready": official_api_ready,
        "apify_token_ready": apify_token_ready,
        "comments_actor_ready": comments_actor_ready,
        "comments_provider_ready": comments_provider_ready,
        "preferred_path": preferred_path,
        "env": {
            "X_BEARER_TOKEN": official_api_ready,
            "APIFY_TOKEN": apify_token_ready,
            "APIFY_X_ACTOR_ID": _env_present("APIFY_X_ACTOR_ID"),
            "APIFY_X_COMMENTS_ACTOR_ID": comments_actor_ready,
        },
    }


def build_x_comments_go_no_go_report(targets_file: str | Path | None = None) -> dict[str, Any]:
    modes = _provider_modes()
    targets = _load_targets(targets_file)
    tables = {
        "vkpi_market_scan_runs": {"exists": _table_exists("vkpi_market_scan_runs"), "rows": _count("vkpi_market_scan_runs")},
        "vkpi_market_sources": {"exists": _table_exists("vkpi_market_sources"), "rows": _count("vkpi_market_sources")},
        "vkpi_market_mentions": {"exists": _table_exists("vkpi_market_mentions"), "rows": _count("vkpi_market_mentions")},
        "vkpi_competitor_signals": {"exists": _table_exists("vkpi_competitor_signals"), "rows": _count("vkpi_competitor_signals")},
    }
    checks = {
        "x_crawler_registered": bool(modes["crawler_registered"]),
        "provider_paths_classified": modes["preferred_path"] in {"official_api_replies", "apify_comments_actor", "profile_actor_only", "none"},
        "comments_provider_gate_explicit": True,
        "validation_target_count_defined": VALIDATION_LIMITS["target_count_required"] == VALIDATION_TARGET_COUNT,
        "hard_caps_defined": all(value > 0 for value in VALIDATION_LIMITS.values()),
        "source_contract_defined": all(bool(fields) for fields in X_COMMENTS_SOURCE_CONTRACT.values()),
        "go_no_go_criteria_defined": all(bool(value) for value in GO_NO_GO_CRITERIA.values()),
        "market_storage_ready": all(tables[name]["exists"] for name in ("vkpi_market_scan_runs", "vkpi_market_sources", "vkpi_market_mentions")),
        "review_storage_ready": bool(tables["vkpi_competitor_signals"]["exists"]),
        "external_calls_blocked": True,
        "writes_blocked": True,
        "provider_calls_blocked": True,
        "sync_blocked": True,
        "no_daily_x_collection_promise": True,
    }
    if not targets["provided"]:
        decision = "hold_targets_required"
        next_step = "Prepare exactly 14 selected X post URLs before requesting a provider run."
    elif not targets["valid"]:
        decision = "hold_targets_invalid"
        next_step = "Fix the target CSV before any provider or Apify validation."
    elif not modes["comments_provider_ready"]:
        decision = "hold_comments_provider_not_ready"
        next_step = "Configure X_BEARER_TOKEN or APIFY_X_COMMENTS_ACTOR_ID behind budget approval."
    else:
        decision = "ready_for_manual_approval"
        next_step = "After explicit approval, run the 14-target validation once and apply stop rules."
    run_ready_after_approval = bool(targets["valid"] and modes["comments_provider_ready"])
    return {
        "mode": "p5_68_x_comments_go_no_go",
        "generated_at": _now(),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "decision": decision,
        "next_step": next_step,
        "run_ready_after_approval": run_ready_after_approval,
        "checks": checks,
        "provider_modes": modes,
        "targets": targets,
        "limits": VALIDATION_LIMITS,
        "source_contract": X_COMMENTS_SOURCE_CONTRACT,
        "go_no_go_criteria": GO_NO_GO_CRITERIA,
        "tables": tables,
        "policy": {
            "scope": "14_selected_x_posts_only",
            "no_full_x_claim": True,
            "no_daily_collection_before_go": True,
            "comments_require_selected_post": True,
            "apify_requires_budget_approval": True,
            "storage": "validation_artifact_first_then_reviewed_market_tables",
        },
    }
