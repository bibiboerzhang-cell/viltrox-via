#!/usr/bin/env python3
"""镜头出镜证据 -> 视频产品边(vkpi_kol_video_product_links relation_type=detected)。

把 vkpi_kol_lens_evidence(迁移 287,final_v1 深析散文抽出的 Viltrox 产品提及)里
resolution=sku 的行,按 (evidence_id, product_sku) 聚合后写成 detected 边:
  * confidence:该组任一提及 v_relevance=confirmed -> 0.9;否则 likely -> 0.6
    (口径复用 lens_evidence.v_relevance_for,与 MY KOL「镜头出镜」模块同源)
  * source 固定 lens_evidence_v2;已有 manual / confirmed 边的 (evidence, sku) 不写(人工优先);
    已有其它来源的 detected 边不覆盖;本脚本自己写的边重复跑只校正 confidence(幂等)
  * --revert 一键撤销:只删 relation_type=detected AND source=lens_evidence_v2 的边
默认 dry-run 只打印「可写多少条」;--apply 才写。零 LLM、零采集;红线不触 viltrox_fit_score / rule_v0。

用法(本地 / 隔离库):
  PYTHONPATH=.:scripts:backend APP_ROLE=admin-web ENABLE_SCHEDULER=0 \\
    .venv/bin/python scripts/ops/lens_detected_links.py [--limit 20000]
  ... lens_detected_links.py --apply
  ... lens_detected_links.py --revert [--apply]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

LOG = logging.getLogger("viltrox.ops.lens_detected_links")
LOG.setLevel(logging.INFO)
LOG.propagate = False
_STDOUT = logging.StreamHandler(stream=sys.stdout)
_STDOUT.setFormatter(logging.Formatter("%(message)s"))
LOG.addHandler(_STDOUT)

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

LINK_SOURCE = "lens_evidence_v2"
RELATION_TYPE = "detected"
CONFIDENCE_BY_RELEVANCE = {"confirmed": 0.9, "likely": 0.6}


def _emit(payload: Any) -> None:
    LOG.info(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="真正写表 / 删边(缺省 dry-run 只统计)")
    parser.add_argument("--dry-run", action="store_true", help="显式 dry-run(与缺省等价;与 --apply 互斥)")
    parser.add_argument("--revert", action="store_true", help="撤销本脚本写过的 detected 边(配 --apply 才真删)")
    parser.add_argument("--limit", type=int, default=20000, help="本次最多读取的证据行数(默认 20000)")
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--apply 与 --dry-run 互斥")
    return args


def plan_links(conn: Any, *, limit: int) -> dict[str, Any]:
    """只读规划:聚合证据 -> 目标边列表 + 与现有边的对照。"""
    from app.domains.kol import lens_evidence_store as store

    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT le.evidence_id, le.product_sku, le.resolution, le.lens_key, le.modalities,
                   le.source_fields, le.mention_count
            FROM vkpi_kol_lens_evidence le
            WHERE le.resolution = 'sku' AND le.evidence_id IS NOT NULL
              AND le.product_sku IS NOT NULL AND le.product_sku <> ''
            ORDER BY le.evidence_id, le.product_sku, le.id
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    ]
    groups: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["evidence_id"]), str(row["product_sku"]))
        relevance, reason = store._row_relevance(row)
        group = groups.setdefault(key, {"evidence_id": key[0], "product_sku": key[1], "relevance": "likely", "mentions": 0, "reasons": Counter()})
        group["mentions"] += int(row.get("mention_count") or 1)
        group["reasons"][reason] += 1
        if relevance == "confirmed":
            group["relevance"] = "confirmed"
    skus = sorted({key[1] for key in groups})
    known_skus: set[str] = set()
    if skus:
        placeholders = ",".join("?" for _ in skus)
        known_skus = {
            str(dict(r)["sku"])
            for r in conn.execute(f"SELECT sku FROM vkpi_products WHERE sku IN ({placeholders})", tuple(skus)).fetchall()
        }
    evidence_ids = sorted({key[0] for key in groups})
    existing: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for start in range(0, len(evidence_ids), 500):
        chunk = evidence_ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        for r in conn.execute(
            "SELECT id, evidence_id, product_sku, relation_type, source, confidence "
            f"FROM vkpi_kol_video_product_links WHERE evidence_id IN ({placeholders})",
            tuple(chunk),
        ).fetchall():
            rec = dict(r)
            existing.setdefault((int(rec["evidence_id"]), str(rec["product_sku"])), []).append(rec)
    plan: dict[str, Any] = {
        "evidence_rows": len(rows),
        "groups": len(groups),
        "insert": [],
        "update_confidence": [],
        "unchanged": 0,
        "skipped_manual_or_confirmed": 0,
        "skipped_other_detected_source": 0,
        "skipped_unknown_sku": 0,
        "relevance": {"confirmed": 0, "likely": 0},
        "reasons": Counter(),
    }
    for key, group in sorted(groups.items()):
        plan["reasons"].update(group["reasons"])
        if key[1] not in known_skus:
            plan["skipped_unknown_sku"] += 1
            continue
        plan["relevance"][group["relevance"]] += 1
        confidence = CONFIDENCE_BY_RELEVANCE[group["relevance"]]
        links = existing.get(key, [])
        if any(str(link.get("relation_type")) in ("manual", "confirmed") for link in links):
            plan["skipped_manual_or_confirmed"] += 1
            continue
        detected = [link for link in links if str(link.get("relation_type")) == RELATION_TYPE]
        if detected:
            link = detected[0]
            if str(link.get("source")) != LINK_SOURCE:
                plan["skipped_other_detected_source"] += 1
                continue
            if abs(float(link.get("confidence") or 0.0) - confidence) < 0.0005:
                plan["unchanged"] += 1
            else:
                plan["update_confidence"].append({"id": int(link["id"]), "confidence": confidence})
            continue
        plan["insert"].append({"evidence_id": key[0], "product_sku": key[1], "confidence": confidence})
    plan["reasons"] = dict(plan["reasons"])
    return plan


def apply_plan(conn: Any, plan: dict[str, Any]) -> dict[str, int]:
    inserted = updated = 0
    try:
        for item in plan["insert"]:
            row = conn.execute(
                """
                INSERT INTO vkpi_kol_video_product_links (
                    evidence_id, product_sku, relation_type, source, confidence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (evidence_id, product_sku, relation_type) DO NOTHING
                RETURNING id
                """,
                (int(item["evidence_id"]), str(item["product_sku"]), RELATION_TYPE, LINK_SOURCE, float(item["confidence"])),
            ).fetchone()
            inserted += 1 if row else 0
        for item in plan["update_confidence"]:
            conn.execute(
                "UPDATE vkpi_kol_video_product_links SET confidence=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND source=? AND relation_type=?",
                (float(item["confidence"]), int(item["id"]), LINK_SOURCE, RELATION_TYPE),
            )
            updated += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"inserted": inserted, "updated": updated}


def revert(conn: Any, *, apply: bool) -> dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_video_product_links WHERE relation_type=? AND source=?",
        (RELATION_TYPE, LINK_SOURCE),
    ).fetchone()
    count = int(dict(row or {}).get("n") or 0)
    deleted = 0
    if apply and count:
        try:
            conn.execute(
                "DELETE FROM vkpi_kol_video_product_links WHERE relation_type=? AND source=?",
                (RELATION_TYPE, LINK_SOURCE),
            )
            conn.commit()
            deleted = count
        except Exception:
            conn.rollback()
            raise
    return {"mode": "revert", "matched": count, "deleted": deleted}


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    from app.db.connection import get_conn, table_exists

    for table in ("vkpi_kol_lens_evidence", "vkpi_kol_video_product_links"):
        if not table_exists(table):
            _emit({"status": "blocked", "reason": f"{table}_missing"})
            return 2
    conn = get_conn()
    if args.revert:
        stats = revert(conn, apply=bool(args.apply))
        stats["status"] = "applied" if args.apply else "dry_run"
        _emit(stats)
        return 0
    plan = plan_links(conn, limit=max(1, int(args.limit)))
    stats: dict[str, Any] = {
        "status": "applied" if args.apply else "dry_run",
        "source": LINK_SOURCE,
        "evidence_rows": plan["evidence_rows"],
        "groups": plan["groups"],
        "would_insert": len(plan["insert"]),
        "would_update_confidence": len(plan["update_confidence"]),
        "unchanged": plan["unchanged"],
        "skipped_manual_or_confirmed": plan["skipped_manual_or_confirmed"],
        "skipped_other_detected_source": plan["skipped_other_detected_source"],
        "skipped_unknown_sku": plan["skipped_unknown_sku"],
        "relevance": plan["relevance"],
        "reasons": plan["reasons"],
        "confidence_map": CONFIDENCE_BY_RELEVANCE,
    }
    if args.apply:
        stats.update(apply_plan(conn, plan))
    _emit(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
