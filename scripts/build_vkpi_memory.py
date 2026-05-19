#!/usr/bin/env python3
"""Build or inspect V-KPI Memory v0 from committed legacy batches."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime  # noqa: E402
from app.services.vkpi import memory  # noqa: E402


DEFAULT_BATCH_UID = "vkpi_20260519033921_b36c6f28ec8d"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build V-KPI Memory v0 from P2D committed data.")
    parser.add_argument("--batch-uid", default=DEFAULT_BATCH_UID)
    parser.add_argument("--build-legacy", action="store_true", help="Build memory facts from the legacy batch")
    parser.add_argument("--build-product-families", action="store_true", help="Normalize product memory into product_family entities")
    parser.add_argument("--build-market-memory", action="store_true", help="Build Market Memory v0 from legacy launch/VOC/official content")
    parser.add_argument("--summary", action="store_true", help="Print memory summary")
    parser.add_argument("--source-ref", default="", help="Optional source_ref prefix for summary")
    parser.add_argument("--product-families", nargs="?", const="", default=None, help="List normalized product families")
    parser.add_argument("--market-signals", nargs="?", const="", default=None, help="List Market Memory signals")
    parser.add_argument("--signal-type", default="", help="Optional signal type filter for --market-signals")
    parser.add_argument("--product-kol-candidates", default="", help="List KOL memory evidence for a product query")
    parser.add_argument("--kol-product-memory", default="", help="Show product memory for a KOL memory entity_uid")
    parser.add_argument("--fit-features", default="", help="Extract deterministic fit features for a KOL memory entity_uid")
    parser.add_argument("--product-query", default="", help="Optional product query for --fit-features")
    parser.add_argument("--limit", type=int, default=50, help="Limit query outputs")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser.parse_args()


def _print_summary(result: dict) -> None:
    print(f"source_ref={result.get('source_ref', '')}")
    for key, value in sorted((result.get("entities") or {}).items()):
        print(f"entities.{key}={int(value)}")
    for key, value in sorted((result.get("facts") or {}).items()):
        print(f"facts.{key}={int(value)}")
    print(f"links={int(result.get('links', 0))}")
    print(f"snapshots={int(result.get('snapshots', 0))}")
    if result.get("batch_uid"):
        print(f"batch_uid={result['batch_uid']}")
    if result.get("snapshot_uid"):
        print(f"snapshot_uid={result['snapshot_uid']}")
    for key, value in sorted((result.get("build_counts") or {}).items()):
        print(f"build.{key}={int(value)}")


def _print_candidate_result(result: dict) -> None:
    print(f"product_query={result.get('product_query', '')}")
    print(f"matched_products={len(result.get('matched_products') or [])}")
    print(f"matched_families={len(result.get('matched_families') or [])}")
    print(f"total_candidates={int(result.get('total') or 0)}")
    for idx, item in enumerate(result.get("items") or [], start=1):
        entity = item.get("entity") or {}
        features = item.get("features") or {}
        print(
            f"{idx}. entity_uid={entity.get('entity_uid', '')} "
            f"platform={features.get('platform', '')} "
            f"handle={features.get('handle', '')} "
            f"score={int(item.get('memory_score') or 0)} "
            f"matched_products={int(item.get('matched_product_count') or 0)} "
            f"matched_cooperations={int(item.get('matched_cooperation_count') or 0)} "
            f"risk_flags={int(features.get('risk_flag_count') or 0)} "
            f"sync_status={features.get('sync_status', '')}"
        )


def _print_product_families(result: dict) -> None:
    print(f"query={result.get('query', '')}")
    print(f"total_families={int(result.get('total_families') or 0)}")
    print(f"matched_families={int(result.get('matched_families') or 0)}")
    for idx, item in enumerate(result.get("items") or [], start=1):
        print(
            f"{idx}. family_uid={item.get('entity_uid', '')} "
            f"name={item.get('display_name', '')} "
            f"members={int(item.get('member_count') or 0)} "
            f"cooperations={int(item.get('cooperation_count') or 0)}"
        )
        for member in (item.get("members") or [])[:3]:
            print(f"   - product={member.get('display_name', '')} links={int(member.get('link_count') or 0)}")


def _print_market_signals(result: dict) -> None:
    print(f"query={result.get('query', '')}")
    print(f"signal_type={result.get('signal_type', '')}")
    if "total_signals" in result:
        print(f"total_signals={int(result.get('total_signals') or 0)}")
    if result.get("signals"):
        for key, value in sorted((result.get("signals") or {}).items()):
            print(f"signals.{key}={int(value)}")
    if result.get("target_entity_types"):
        for key, value in sorted((result.get("target_entity_types") or {}).items()):
            print(f"targets.{key}={int(value)}")
    if result.get("attachment"):
        for key, value in sorted((result.get("attachment") or {}).items()):
            print(f"attachment.{key}={int(value)}")
    if "total_returned" in result:
        print(f"total_returned={int(result.get('total_returned') or 0)}")
    for idx, item in enumerate(result.get("items") or [], start=1):
        entity = item.get("entity") or {}
        fact = item.get("fact") or {}
        print(
            f"{idx}. type={item.get('signal_type', '')} "
            f"date={item.get('signal_date', '')} "
            f"target={entity.get('entity_type', '')}:{entity.get('display_name', '')} "
            f"value={item.get('value', '')} "
            f"source={fact.get('source_sheet', '')}:{fact.get('source_row', '')}"
        )


def _print_kol_memory(result: dict) -> None:
    entity = result.get("entity") or {}
    features = result.get("features") or {}
    print(f"entity_uid={entity.get('entity_uid', '')}")
    print(f"display_name={entity.get('display_name', '')}")
    print(f"platform={features.get('platform', '')}")
    print(f"handle={features.get('handle', '')}")
    print(f"product_count={int(features.get('product_count') or 0)}")
    print(f"cooperation_count={int(features.get('cooperation_count') or 0)}")
    print(f"risk_flag_count={int(features.get('risk_flag_count') or 0)}")
    print(f"sync_status={features.get('sync_status', '')}")
    print(f"product_links={len(result.get('product_links') or [])}")
    for idx, link in enumerate((result.get("product_links") or [])[:10], start=1):
        product = link.get("product") or {}
        print(f"{idx}. product={product.get('display_name', '')} source_ref={link.get('source_ref', '')}")


def _print_fit_features(result: dict) -> None:
    entity = result.get("entity") or {}
    features = result.get("features") or {}
    print(f"entity_uid={entity.get('entity_uid', '')}")
    print(f"product_query={result.get('product_query', '')}")
    print(f"memory_score={int(result.get('memory_score') or 0)}")
    for key in (
        "platform",
        "handle",
        "country",
        "sync_status",
        "weak_label",
        "review_state",
        "contact_status",
        "cooperation_count",
        "product_count",
        "matched_product_count",
        "matched_product_cooperation_count",
        "risk_flag_count",
        "evidence_count",
    ):
        print(f"{key}={features.get(key, '')}")
    print(f"warnings={','.join(result.get('warnings') or [])}")


def main() -> int:
    args = parse_args()
    try:
        if args.build_product_families:
            result = memory.build_product_family_memory()
        elif args.build_market_memory:
            result = memory.build_market_memory_from_legacy_batch(args.batch_uid)
        elif args.product_families is not None:
            result = memory.product_family_summary(query=args.product_families, limit=args.limit)
        elif args.market_signals is not None:
            result = memory.market_signals(query=args.market_signals, signal_type=args.signal_type, limit=args.limit)
        elif args.product_kol_candidates:
            result = memory.product_kol_candidates(product_query=args.product_kol_candidates, limit=args.limit)
        elif args.kol_product_memory:
            result = memory.kol_product_memory(args.kol_product_memory, limit=args.limit)
        elif args.fit_features:
            result = memory.fit_features(args.fit_features, product_query=args.product_query)
        elif args.build_legacy:
            result = memory.build_memory_from_legacy_batch(args.batch_uid)
        else:
            source_ref = args.source_ref
            if not source_ref and not args.summary:
                source_ref = f"legacy_batch:{args.batch_uid}"
            result = memory.summary(source_ref=source_ref)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        elif args.build_product_families or args.product_families is not None:
            _print_product_families(result)
            if result.get("build_counts"):
                for key, value in sorted((result.get("build_counts") or {}).items()):
                    print(f"build.{key}={int(value)}")
        elif args.build_market_memory:
            _print_market_signals(result)
            if result.get("build_counts"):
                for key, value in sorted((result.get("build_counts") or {}).items()):
                    print(f"build.{key}={int(value)}")
        elif args.market_signals is not None:
            _print_market_signals(result)
        elif args.product_kol_candidates:
            _print_candidate_result(result)
        elif args.kol_product_memory:
            _print_kol_memory(result)
        elif args.fit_features:
            _print_fit_features(result)
        else:
            _print_summary(result)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
