#!/usr/bin/env python3
"""Export and evaluate a human-labeled KOL search relevance Gold Set.

Examples:

  # Read-only local export; no Provider/LLM calls.
  python scripts/eval_kol_search_relevance.py export \
      --output /tmp/kol-search-candidates.json \
      --labels-template /tmp/kol-search-labels.jsonl

  # Metrics remain blocked until all 6 x 30 candidates have two independent
  # human reviews and every disagreement has a third-human adjudication.
  python scripts/eval_kol_search_relevance.py evaluate \
      /tmp/kol-search-labels.jsonl \
      --manifest /tmp/kol-search-candidates.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
MODULE_PATH = BACKEND / "app" / "domains" / "kol" / "search_relevance_eval.py"
DEFAULT_LOCAL_DATABASE_URL = "postgresql://postgres@127.0.0.1:54329/viltrox2"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SOURCE_VERSION_FILES = (
    "backend/app/domains/kol/profile_recall.py",
    "backend/app/domains/kol/profile_recall_contract.py",
    "backend/app/domains/kol/profile_recall_precision.py",
    "backend/app/domains/kol/profile_recall_projection.py",
    "backend/app/domains/kol/profile_recall_relevance.py",
    "backend/app/domains/kol/profile_recall_storage.py",
    "backend/app/domains/kol/search_relevance_eval.py",
    "backend/app/domains/kol/search_relevance_metrics.py",
)

# This CLI is intentionally runnable as ``python scripts/...`` without a
# caller-supplied PYTHONPATH.  The evaluator's statistical implementation uses
# normal ``app.*`` imports, so establish the project backend before loading it.
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _load_evaluator():
    spec = importlib.util.spec_from_file_location("vkpi_kol_search_relevance_eval", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("search_relevance_evaluator_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EVALUATOR = _load_evaluator()


def _validate_loopback_database_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("loopback_postgresql_url_required")
    return str(value).strip()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, dict):
        payload = payload.get("labels") or payload.get("items") or payload.get("records") or []
    if not isinstance(payload, list):
        raise ValueError("labels_input_must_be_json_array_or_jsonl")
    return payload


def _dump(payload: Any, *, pretty: bool) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ) + "\n"


def _write_or_print(payload: Any, *, output: Path | None, pretty: bool) -> None:
    rendered = _dump(payload, pretty=pretty)
    if output is None:
        sys.stdout.write(rendered)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def _configure_read_only_runtime(database_url: str) -> None:
    # Set every potentially billable provider credential to explicit blank
    # before importing the application.  Provider-free recall never reads
    # them, but this is a fail-closed defence against future wiring drift.
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "APIFY_TOKEN",
        "APIFY_API_TOKEN",
    ):
        os.environ[key] = ""
    os.environ.update(
        {
            "DATABASE_URL": database_url,
            "DATABASE_POOL_URL": "",
            "DB_USE_PGBOUNCER": "0",
            "DB_RUNTIME_BACKEND": "postgres",
            "DB_TARGET_BACKEND": "postgres",
            "ENVIRONMENT": "local",
            "V2_PRODUCTION_MODE": "0",
            "VKPI_SKIP_DOTENV": "1",
            "ENABLE_SCHEDULER": "0",
            "RECALL_LLM_RERANK_ENABLED": "0",
            "POSTGRES_POOL_MIN_SIZE": "1",
            "POSTGRES_POOL_MAX_SIZE": "1",
            "POSTGRES_POOL_TIMEOUT_SEC": "20",
            # libpq applies this to every pool connection.
            "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=120000",
        }
    )


def _source_code_version() -> str:
    digest = hashlib.sha256()
    for relative in SOURCE_VERSION_FILES:
        path = ROOT / relative
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    return f"source-sha256:{digest.hexdigest()}"


def _dataset_snapshot_id(conn: Any) -> str:
    snapshot: dict[str, Any] = {}
    for table in (
        "vkpi_kol_pool",
        "vkpi_kol_video_evidence",
        "vkpi_analysis_cache",
    ):
        row = conn.execute(
            f"SELECT COUNT(*) AS row_count, COALESCE(MAX(CAST(id AS TEXT)), '') AS max_id FROM {table}"
        ).fetchone()
        snapshot[table] = {
            "row_count": int(row[0] or 0),
            "max_id": str(row[1] or ""),
        }
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"local-db-sha256:{hashlib.sha256(payload).hexdigest()}"


def _export_manifest(database_url: str) -> dict[str, Any]:
    _configure_read_only_runtime(database_url)
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.db import connection
    from app.domains.kol import profile_recall

    conn = connection.get_conn()
    transaction_read_only = conn.execute("SHOW transaction_read_only").fetchone()
    if not transaction_read_only or str(transaction_read_only[0]).strip().lower() != "on":
        raise RuntimeError("database_read_only_guard_failed")
    try:
        return EVALUATOR.build_candidate_manifest(
            profile_recall.recall_kol_profiles,
            code_version=_source_code_version(),
            dataset_snapshot_id=_dataset_snapshot_id(conn),
        )
    finally:
        connection.close_standalone_conn(conn)


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": manifest.get("schema_version"),
        "query_suite_version": manifest.get("query_suite_version"),
        "query_suite_fingerprint": manifest.get("query_suite_fingerprint"),
        "algorithm_version": manifest.get("algorithm_version"),
        "filter_policy_version": manifest.get("filter_policy_version"),
        "code_version": manifest.get("code_version"),
        "dataset_snapshot_id": manifest.get("dataset_snapshot_id"),
        "query_source": manifest.get("query_source"),
        "truth_status": manifest.get("truth_status"),
        "claim_status": manifest.get("claim_status"),
        "query_count": manifest.get("query_count"),
        "candidates_per_query": manifest.get("candidates_per_query"),
        "candidate_count": manifest.get("candidate_count"),
        "candidate_export_complete": manifest.get("candidate_export_complete"),
        "manifest_fingerprint": manifest.get("manifest_fingerprint"),
        "queries": manifest.get("queries"),
        "diagnostics": manifest.get("diagnostics"),
    }


def _cmd_export(args: argparse.Namespace) -> int:
    database_url = _validate_loopback_database_url(args.database_url)
    manifest = _export_manifest(database_url)
    if args.labels_template:
        labels = EVALUATOR.build_label_template(manifest)
        args.labels_template.parent.mkdir(parents=True, exist_ok=True)
        args.labels_template.write_text(
            "".join(_dump(row, pretty=False) for row in labels),
            encoding="utf-8",
        )
    payload = _manifest_summary(manifest) if args.summary_only else manifest
    _write_or_print(payload, output=args.output, pretty=args.pretty)
    return 0 if manifest.get("candidate_export_complete") else 3


def _cmd_evaluate(args: argparse.Namespace) -> int:
    manifest = _load_json(args.manifest)
    report = EVALUATOR.evaluate_search_relevance(
        _load_records(args.labels),
        manifest=manifest,
    )
    _write_or_print(report, output=args.output, pretty=args.pretty)
    return 0 if report.get("evaluation_status") == "evaluated" else 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser(
        "export",
        help="Export the fixed 6 x 30 manifest plus 360 independent review slots",
    )
    export.add_argument(
        "--database-url",
        default=os.getenv("KOL_SEARCH_EVAL_DATABASE_URL", DEFAULT_LOCAL_DATABASE_URL),
        help="Loopback PostgreSQL snapshot only.",
    )
    export.add_argument("--output", type=Path)
    export.add_argument("--labels-template", type=Path)
    export.add_argument("--summary-only", action="store_true")
    export.add_argument("--pretty", action="store_true")
    export.set_defaults(handler=_cmd_export)

    evaluate = subparsers.add_parser("evaluate", help="Evaluate completed human labels")
    evaluate.add_argument("labels", type=Path)
    evaluate.add_argument("--manifest", type=Path, required=True)
    evaluate.add_argument("--output", type=Path)
    evaluate.add_argument("--pretty", action="store_true")
    evaluate.set_defaults(handler=_cmd_evaluate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except ValueError as exc:
        _write_or_print(
            {
                "evaluation_status": "not_evaluated",
                "gate_status": "blocked",
                "error": str(exc),
                "diagnostics": {
                    "provider_calls": False,
                    "llm_calls": False,
                    "database_write": False,
                },
            },
            output=getattr(args, "output", None),
            pretty=getattr(args, "pretty", False),
        )
        return 2
    except Exception:
        # Never expose database URLs, credentials, SQL text, or candidate data
        # from an unexpected failure path.
        _write_or_print(
            {
                "evaluation_status": "not_evaluated",
                "gate_status": "blocked",
                "error": "search_relevance_evaluation_failed",
                "diagnostics": {
                    "provider_calls": False,
                    "llm_calls": False,
                    "database_write": False,
                },
            },
            output=getattr(args, "output", None),
            pretty=getattr(args, "pretty", False),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
