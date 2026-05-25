"""Build UI-safe IntelligenceCard payloads from reviewed market signals."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domains.market.llm_quality import evaluate_market_llm_report


def _now_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any, fallback: str = "") -> str:
    parsed = str(value or "").strip()
    return parsed or fallback


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
        return parsed if parsed == parsed else 0.0
    except Exception:
        return 0.0


def _hash(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _evidence(label: str, source: str, value: Any) -> dict[str, str]:
    return {"label": label, "source": source, "value": _text(value, "-")}


def _priority_from_score(score: float) -> str:
    if score >= 100:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _market_summary_card(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    distributions = report.get("distributions") if isinstance(report.get("distributions"), dict) else {}
    run_id = summary.get("run_id")
    signals = int(_number(summary.get("signals_loaded")))
    launch = int(_number(summary.get("launch_candidates")))
    comments = int(_number(summary.get("comment_opportunities")))
    high = int(_number(summary.get("high_priority")))
    return {
        "id": f"market-summary-run-{run_id or 'all'}",
        "type": "market",
        "priority": "high" if high >= 8 else "medium" if high else "low",
        "status": "open",
        "title": "Reddit 市场信号已进入审核队列",
        "summary": f"本范围加载 {signals} 条信号，其中新品/竞品动向 {launch} 条、评论机会 {comments} 条、高优先级 {high} 条。",
        "entityType": "market_signal_run",
        "entityId": str(run_id or ""),
        "confidence": 0.78 if signals else 0.2,
        "freshnessLabel": _text(report.get("generated_at")),
        "sourceLabel": "vkpi_competitor_signals",
        "evidence": [
            _evidence("范围", "Market Intelligence v0", f"run_id={run_id}" if run_id else "all"),
            _evidence("信号数", "summary.signals_loaded", signals),
            _evidence("高优先级", "summary.high_priority", high),
            _evidence("review_status", "distributions.review_status", distributions.get("review_status")),
        ],
        "actions": [
            {"id": "review", "label": "查看证据", "kind": "secondary"},
            {"id": "task", "label": "生成任务", "kind": "primary"},
        ],
    }


def _brand_cards(report: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    run_id = (report.get("summary") or {}).get("run_id") if isinstance(report.get("summary"), dict) else None
    cards: list[dict[str, Any]] = []
    for item in (report.get("hot_brands") or [])[: max(0, int(limit or 0))]:
        if not isinstance(item, dict):
            continue
        brand = _text(item.get("brand"), "unknown")
        count = int(_number(item.get("count")))
        score = _number(item.get("score"))
        cards.append(
            {
                "id": f"market-brand-{brand}-{_hash(run_id, brand, count, score)}",
                "type": "competitor",
                "priority": _priority_from_score(score),
                "status": "open",
                "title": f"{brand.upper()} 本轮信号较突出",
                "summary": f"在当前范围中出现 {count} 条，累计 score {score:g}。该结论只代表样本内热度，不代表销量或全市场份额。",
                "entityType": "competitor_brand",
                "entityId": brand,
                "confidence": 0.72 if count >= 2 else 0.58,
                "freshnessLabel": _text(report.get("generated_at")),
                "sourceLabel": "Market Intelligence v0",
                "evidence": [
                    _evidence("run_id", "summary.run_id", run_id),
                    _evidence("count", "hot_brands.count", count),
                    _evidence("score", "hot_brands.score", score),
                ],
                "actions": [
                    {"id": "review", "label": "查看证据", "kind": "secondary"},
                    {"id": "snooze", "label": "稍后看", "kind": "secondary"},
                ],
            }
        )
    return cards


def _llm_card(llm_report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not llm_report:
        return None
    quality = evaluate_market_llm_report(llm_report)
    gate = quality.get("quality_gate") if isinstance(quality.get("quality_gate"), dict) else {}
    result = llm_report.get("llm_single_result") if isinstance(llm_report.get("llm_single_result"), dict) else {}
    if not quality.get("usable_for_ui"):
        return None
    text = _text(result.get("text"))
    provider = _text(result.get("provider"))
    model = _text(result.get("model"))
    return {
        "id": f"market-llm-summary-{_hash(provider, model, text)}",
        "type": "brief",
        "priority": "medium",
        "status": "open",
        "title": "LLM 量化摘要已通过质量闸门",
        "summary": text[:520],
        "entityType": "market_llm_summary",
        "entityId": provider,
        "confidence": 0.74,
        "freshnessLabel": _text(llm_report.get("generated_at")),
        "sourceLabel": f"{provider}/{model}",
        "evidence": [
            _evidence("quality", "market_llm_quality_gate_v0", gate.get("status")),
            _evidence("tokens", "llm_single_result", f"in={result.get('input_tokens')} out={result.get('output_tokens')}"),
            _evidence("cost_cents", "llm_single_result.cost_cents", result.get("cost_cents")),
        ],
        "actions": [
            {"id": "review", "label": "查看证据", "kind": "secondary"},
            {"id": "task", "label": "生成任务", "kind": "primary"},
        ],
        "quality_gate": gate,
    }


def _priority_from_unit_score(score: float) -> str:
    if score >= 0.72:
        return "high"
    if score >= 0.42:
        return "medium"
    return "low"


def _external_signal_cards(report: dict[str, Any] | None, *, limit: int = 4) -> list[dict[str, Any]]:
    if not report:
        return []
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    candidates = report.get("top_candidates") if isinstance(report.get("top_candidates"), list) else []
    items_loaded = int(_number(summary.get("items_loaded")))
    business_items = int(_number(summary.get("business_signal_items")))
    if not items_loaded and not candidates:
        return []
    cards: list[dict[str, Any]] = [
        {
            "id": f"external-smoke-summary-{_hash(report.get('generated_at'), items_loaded, business_items)}",
            "type": "market",
            "priority": "medium" if business_items else "low",
            "status": "open",
            "title": "Google/RSS 外部信号 smoke 已生成",
            "summary": f"本次只读 smoke 读取 {items_loaded} 条外部条目，命中业务信号 {business_items} 条；未写库、未触发同步、未调用 LLM。",
            "entityType": "external_signal_smoke",
            "entityId": _text(report.get("generated_at")),
            "confidence": 0.62 if business_items else 0.35,
            "freshnessLabel": _text(report.get("generated_at")),
            "sourceLabel": "market_external_signal_smoke_v0",
            "evidence": [
                _evidence("items_loaded", "summary.items_loaded", items_loaded),
                _evidence("business_signal_items", "summary.business_signal_items", business_items),
                _evidence("tier1_mentions", "summary.tier1_mentions", summary.get("tier1_mentions")),
                _evidence("viltrox_product_mentions", "summary.viltrox_product_mentions", summary.get("viltrox_product_mentions")),
            ],
            "actions": [
                {"id": "review", "label": "查看证据", "kind": "secondary"},
                {"id": "task", "label": "生成任务", "kind": "primary"},
            ],
        }
    ]
    for item in candidates[: max(0, int(limit or 0))]:
        if not isinstance(item, dict):
            continue
        score = _number(item.get("score"))
        title = _text(item.get("title"), "外部市场信号")
        url = _text(item.get("source_url"))
        cards.append(
            {
                "id": f"external-signal-{_hash(item.get('source_uid'), item.get('source_url'), title)}",
                "type": "market" if item.get("provider") != "google_news" else "competitor",
                "priority": _priority_from_unit_score(score),
                "status": "open",
                "title": title[:120],
                "summary": _text(item.get("summary"), "外部信号只有标题，待人工复核后才进入业务结论。")[:260],
                "entityType": "external_signal_item",
                "entityId": _text(item.get("source_uid")),
                "confidence": max(0.25, min(0.82, score)),
                "freshnessLabel": _text(item.get("published_at"), item.get("provider")),
                "sourceLabel": _text(item.get("provider"), "external"),
                "evidence": [
                    _evidence("provider", "external_signal.provider", item.get("provider")),
                    _evidence("source_key", "external_signal.source_key", item.get("source_key")),
                    _evidence("score", "external_signal.score", score),
                    {"label": "url", "source": "external_signal.source_url", "value": url or "-", "url": url or None},
                    _evidence("keyword_hits", "taxonomy.keyword_hits", ", ".join(item.get("keyword_hits") or [])),
                ],
                "actions": [
                    {"id": "review", "label": "查看证据", "kind": "secondary"},
                    {"id": "snooze", "label": "稍后看", "kind": "secondary"},
                ],
            }
        )
    return cards


def build_market_intelligence_cards(
    market_report: dict[str, Any],
    *,
    llm_report: dict[str, Any] | None = None,
    external_smoke_report: dict[str, Any] | None = None,
    brand_limit: int = 5,
) -> dict[str, Any]:
    cards = [_market_summary_card(market_report), *_brand_cards(market_report, limit=brand_limit)]
    llm_card = _llm_card(llm_report)
    if llm_card:
        cards.insert(1, llm_card)
    external_cards = _external_signal_cards(external_smoke_report)
    if external_cards:
        insert_at = 2 if llm_card else 1
        cards[insert_at:insert_at] = external_cards
    checks = {
        "market_report_ready": bool(market_report.get("passed")) and bool((market_report.get("summary") or {}).get("signals_loaded")),
        "cards_have_evidence": all(bool(card.get("evidence")) for card in cards),
        "llm_card_requires_quality_gate": llm_card is None or bool(llm_card.get("quality_gate", {}).get("usable_for_ui")),
        "external_smoke_is_read_only": external_smoke_report is None or (
            external_smoke_report.get("write_db") is False
            and external_smoke_report.get("llm_calls") is False
            and external_smoke_report.get("sync_triggered") is False
        ),
        "no_db_write": True,
        "no_provider_call": True,
    }
    return {
        "mode": "market_intelligence_cards_v0",
        "generated_at": _now_z(),
        "write_db": False,
        "provider_calls": False,
        "llm_calls": False,
        "sync_triggered": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "card_count": len(cards),
            "llm_card_included": bool(llm_card),
            "external_smoke_card_count": len(external_cards),
            "source_run_id": (market_report.get("summary") or {}).get("run_id") if isinstance(market_report.get("summary"), dict) else None,
        },
        "cards": cards,
        "policy": {
            "source_scope_required": True,
            "hide_unusable_llm_output": True,
            "evidence_required_for_every_card": True,
        },
    }


def latest_usable_market_llm_report(
    *,
    ops_dir: str | Path = "runtime/ops",
    pattern: str = "*market-llm-single-smoke-*.json",
) -> dict[str, Any] | None:
    root = Path(ops_dir)
    if not root.exists() or not root.is_dir():
        return None
    candidates = sorted(
        [path for path in root.glob(pattern) if path.is_file()],
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    for path in candidates:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(report, dict):
            continue
        quality = evaluate_market_llm_report(report)
        if quality.get("usable_for_ui"):
            report["_artifact_path"] = str(path)
            report["_quality_gate"] = quality.get("quality_gate")
            return report
    return None


def latest_external_signal_smoke_report(
    *,
    ops_dir: str | Path = "runtime/ops",
    pattern: str = "*market-external-signal-smoke-v0.json",
) -> dict[str, Any] | None:
    root = Path(ops_dir)
    if not root.exists() or not root.is_dir():
        return None
    candidates = sorted(
        [path for path in root.glob(pattern) if path.is_file()],
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    for path in candidates:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(report, dict):
            continue
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        if report.get("passed") and int(_number(summary.get("items_loaded"))) > 0:
            report["_artifact_path"] = str(path)
            return report
    return None


def build_market_intelligence_cards_from_files(
    market_report_path: str | Path,
    *,
    llm_report_path: str | Path | None = None,
    external_smoke_report_path: str | Path | None = None,
    brand_limit: int = 5,
) -> dict[str, Any]:
    market_report = json.loads(Path(market_report_path).read_text(encoding="utf-8"))
    if not isinstance(market_report, dict):
        raise ValueError("market report must be a JSON object")
    llm_report = None
    if llm_report_path:
        loaded_llm = json.loads(Path(llm_report_path).read_text(encoding="utf-8"))
        if not isinstance(loaded_llm, dict):
            raise ValueError("LLM report must be a JSON object")
        llm_report = loaded_llm
    external_report = None
    if external_smoke_report_path:
        loaded_external = json.loads(Path(external_smoke_report_path).read_text(encoding="utf-8"))
        if not isinstance(loaded_external, dict):
            raise ValueError("external smoke report must be a JSON object")
        external_report = loaded_external
    return build_market_intelligence_cards(
        market_report,
        llm_report=llm_report,
        external_smoke_report=external_report,
        brand_limit=brand_limit,
    )


def write_market_intelligence_cards(payload: dict[str, Any], *, out_dir: str | Path = "runtime/ops") -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out / f"{stamp}-market-intelligence-cards-v0.json"
    md_path = out / f"{stamp}-market-intelligence-cards-v0.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_market_intelligence_cards_markdown(payload), encoding="utf-8")
    return {"json_path": str(json_path.resolve()), "md_path": str(md_path.resolve())}


def render_market_intelligence_cards_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# Market Intelligence Cards v0",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- passed: `{payload.get('passed')}`",
        f"- card_count: `{summary.get('card_count')}`",
        f"- source_run_id: `{summary.get('source_run_id')}`",
        f"- llm_card_included: `{summary.get('llm_card_included')}`",
        f"- external_smoke_card_count: `{summary.get('external_smoke_card_count')}`",
        "",
        "## Cards",
        "",
    ]
    for card in payload.get("cards") or []:
        lines.append(
            f"- `{card.get('type')}` · `{card.get('priority')}` · {card.get('title')} · {card.get('summary')}"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Card build writes no DB rows.",
            "- Card build does not call providers; external smoke artifacts record their own `provider_calls` flag.",
            "- Unusable LLM outputs are hidden.",
        ]
    )
    return "\n".join(lines) + "\n"
