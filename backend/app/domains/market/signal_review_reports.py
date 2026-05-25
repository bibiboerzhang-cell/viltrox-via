"""Report rendering for market-signal review packages."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_review_package_report(payload: dict[str, Any], *, out_dir: str | Path = "runtime/ops") -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out / f"{stamp}-market-signal-promotion-review-package-v0.json"
    md_path = out / f"{stamp}-market-signal-promotion-review-package-v0.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_review_package_markdown(payload), encoding="utf-8")
    return {"json_path": str(json_path.resolve()), "md_path": str(md_path.resolve())}


def write_external_signal_review_package_report(payload: dict[str, Any], *, out_dir: str | Path = "runtime/ops") -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out / f"{stamp}-market-external-signal-review-package-v0.json"
    md_path = out / f"{stamp}-market-external-signal-review-package-v0.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_external_signal_review_package_markdown(payload), encoding="utf-8")
    return {"json_path": str(json_path.resolve()), "md_path": str(md_path.resolve())}


def write_competitor_signal_write_report(payload: dict[str, Any], *, out_dir: str | Path = "runtime/ops") -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out / f"{stamp}-market-signal-reviewed-competitor-write-v0.json"
    md_path = out / f"{stamp}-market-signal-reviewed-competitor-write-v0.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_competitor_signal_write_markdown(payload), encoding="utf-8")
    return {"json_path": str(json_path.resolve()), "md_path": str(md_path.resolve())}


def render_review_package_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Market Signal Promotion Review Package v0",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- write_db: `{payload.get('write_db')}`",
        f"- llm_calls: `{payload.get('llm_calls')}`",
        f"- gemini_calls: `{payload.get('gemini_calls')}`",
        f"- passed: `{payload.get('passed')}`",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    for key in (
        "candidates_loaded",
        "ready_for_promotion",
        "pending_manual_review",
        "ignored",
        "rejected",
        "avg_review_confidence",
    ):
        lines.append(f"| {key} | {summary.get(key)} |")

    lines.extend(["", "## Suggested Actions", ""])
    for key, value in (summary.get("suggested_action_counts") or {}).items():
        lines.append(f"- `{key}`: {value}")

    lines.extend(["", "## Ready Candidates", ""])
    for item in (payload.get("ready_candidates") or [])[:20]:
        lines.append(
            f"- `{item.get('brand')}` · `{item.get('signal_type')}` · "
            f"score `{item.get('score')}` · confidence `{item.get('confidence')}` · "
            f"{item.get('detail')} · {item.get('source_url')}"
        )

    lines.extend(["", "## Needs Review / Ignore", ""])
    for item in (payload.get("pending_candidates") or [])[:15]:
        lines.append(
            f"- pending · `{item.get('brand')}` · `{item.get('signal_type')}` · "
            f"reasons `{', '.join(item.get('reasons') or [])}` · {item.get('detail')}"
        )
    for item in (payload.get("ignored_candidates") or [])[:15]:
        lines.append(
            f"- ignored · `{item.get('brand')}` · `{item.get('signal_type')}` · "
            f"reasons `{', '.join(item.get('reasons') or [])}` · {item.get('detail')}"
        )

    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- No DB write.",
            "- No promotion to `vkpi_competitor_signals` in this round.",
            "- No provider, LLM, Gemini, sync, or deep-scan call.",
            "- Next write round must take a database backup first.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_external_signal_review_package_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Market External Signal Review Package v0",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- write_db: `{payload.get('write_db')}`",
        f"- provider_calls: `{payload.get('provider_calls')}`",
        f"- llm_calls: `{payload.get('llm_calls')}`",
        f"- gemini_calls: `{payload.get('gemini_calls')}`",
        f"- passed: `{payload.get('passed')}`",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    for key in (
        "source_report_count",
        "items_loaded",
        "ready_for_market_mentions",
        "pending_manual_review",
        "ignored",
        "candidate_competitor_signal_after_market_mention",
    ):
        lines.append(f"| {key} | {summary.get(key)} |")

    lines.extend(["", "## Ready For Market Mentions", ""])
    for item in (payload.get("ready_candidates") or [])[:25]:
        lines.append(
            f"- `{item.get('source_key')}` · score `{item.get('score')}` · "
            f"groups `{', '.join(item.get('primary_groups') or [])}` · "
            f"target `{item.get('write_target')}` · secondary `{item.get('secondary_target') or '-'}` · "
            f"{item.get('title')} · {item.get('source_url')}"
        )

    lines.extend(["", "## Pending Manual Review", ""])
    for item in (payload.get("pending_candidates") or [])[:20]:
        lines.append(
            f"- pending · `{item.get('source_key')}` · score `{item.get('score')}` · "
            f"reasons `{', '.join(item.get('reasons') or [])}` · {item.get('title')}"
        )

    lines.extend(["", "## Ignored / Noise", ""])
    for item in (payload.get("ignored_candidates") or [])[:15]:
        lines.append(
            f"- ignored · `{item.get('source_key')}` · reasons `{', '.join(item.get('reasons') or [])}` · {item.get('title')}"
        )

    lines.extend(["", "## Checks", ""])
    for key, value in (payload.get("checks") or {}).items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- No DB write.",
            "- External Google/RSS rows must be persisted to market mention storage before any competitor-signal promotion.",
            "- No provider, LLM, Gemini, sync, or deep-scan call during review package build.",
            "- Next write round must take a database backup first.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_competitor_signal_write_markdown(payload: dict[str, Any]) -> str:
    before = payload.get("before_counts") or {}
    after = payload.get("after_counts") or {}
    lines = [
        "# Market Signal Reviewed Competitor Write v0",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- write_db: `{payload.get('write_db')}`",
        f"- backup_ref: `{payload.get('backup_ref')}`",
        f"- run_uid: `{payload.get('run_uid')}`",
        f"- run_id: `{payload.get('run_id')}`",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| ready_candidates | {payload.get('ready_candidates')} |",
        f"| inserted | {payload.get('inserted')} |",
        f"| skipped_existing | {payload.get('skipped_existing')} |",
        f"| competitor_signals_before | {before.get('vkpi_competitor_signals')} |",
        f"| competitor_signals_after | {after.get('vkpi_competitor_signals')} |",
        "",
        "## Boundary",
        "",
        "- Wrote only reviewed ready candidates from the R5 package.",
        "- Review status remains `pending_review` for operator confirmation.",
        "- No provider, LLM, Gemini, sync, or deep-scan call.",
    ]
    return "\n".join(lines) + "\n"
