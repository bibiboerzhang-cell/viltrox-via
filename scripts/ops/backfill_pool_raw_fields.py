#!/usr/bin/env python3
"""回填 KOL 池 raw 字段提列(迁移 208 + 291;优化波 B · D 车道「零成本榨干已有 raw」)。

从 vkpi_kol_pool.raw_platform_data(已落库,零新采集、零 LLM)提列:
  * is_verified / is_tt_seller / is_commerce_user(TT authorMeta.verified / ttSeller /
    commerceUserInfo.commerceUser;IG verified / isBusinessAccount)
  * topic_details_json(YT topicDetails 若有 + brandingSettings.channel.keywords + 视频 categoryId;
    TT commerceUserInfo.category;IG businessCategoryName + productType)
  * tagged_brands_json(IG taggedUsers / mentions;TT detailedMentions / mentions)
  * bio / signature 里的联系方式:不在此直写 —— 发现候选即把该 KOL 入 contact_acquisition_queue
    (trigger_source=backfill),由既有 L0 reconcile 经 contact_ingest 的去重 / 抑制 / 合规闸落表;
    --reconcile --brand-scope X 可在 --apply 时就地跑 reconcile。

幂等:按 raw_fields_extracted_at + raw_fields_extractor_version 账本增量(raw 未变且版本未升即跳过;
--force 全量重提);解析器纯函数,重复跑结果一致。默认 dry-run 只打印统计;--apply 才写。
红线:只写派生列 + 队列行,绝不触 viltrox_fit_score / rule_v0 / KOL 归属。

用法(本地 / 隔离库验证):
  PYTHONPATH=.:scripts:backend APP_ROLE=admin-web ENABLE_SCHEDULER=0 \\
    .venv/bin/python scripts/ops/backfill_pool_raw_fields.py [--platform tiktok] [--limit 5000]
  ... backfill_pool_raw_fields.py --apply [--force] [--reconcile --brand-scope organization:1]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("viltrox.ops.backfill_pool_raw_fields")
LOG.setLevel(logging.INFO)
LOG.propagate = False
_STDOUT = logging.StreamHandler(stream=sys.stdout)
_STDOUT.setFormatter(logging.Formatter("%(message)s"))
LOG.addHandler(_STDOUT)

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FIELD_KEYS = ("is_verified", "is_tt_seller", "is_commerce_user", "topic_details_json", "tagged_brands_json")


def _emit(payload: Any) -> None:
    LOG.info(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="真正写列 / 入队(缺省 dry-run 只打印统计)")
    parser.add_argument("--dry-run", action="store_true", help="显式 dry-run(与缺省等价;与 --apply 互斥)")
    parser.add_argument("--limit", type=int, default=20000, help="本次最多扫描的池行数(默认 20000)")
    parser.add_argument("--platform", default="", help="只处理该平台(tiktok / instagram / youtube)")
    parser.add_argument("--id", type=int, action="append", default=[], help="只处理这些 kol_pool_id(可重复)")
    parser.add_argument("--force", action="store_true", help="忽略提列账本,全量重提(幂等)")
    parser.add_argument("--skip-contacts", action="store_true", help="不做联系方式入队")
    parser.add_argument("--requeue", action="store_true", help="已有队列行(非 suppressed)也重新武装")
    parser.add_argument("--reconcile", action="store_true", help="--apply 时就地跑 L0 reconcile(需 --brand-scope)")
    parser.add_argument("--brand-scope", default="", help="reconcile 的品牌作用域,如 organization:1")
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--apply 与 --dry-run 互斥")
    if args.reconcile and not args.brand_scope:
        parser.error("--reconcile 需要 --brand-scope")
    return args


def _candidate_rows(conn: Any, *, limit: int, platform: str, ids: list[int], force: bool) -> list[dict[str, Any]]:
    from app.domains.kol.pool_common import _table_columns

    columns = _table_columns(conn, "vkpi_kol_pool")
    has_ledger = "raw_fields_extracted_at" in columns and "raw_fields_extractor_version" in columns
    select_cols = "id, platform, raw_platform_data, updated_at"
    if has_ledger:
        select_cols += ", raw_fields_extracted_at, raw_fields_extractor_version"
    where = ["raw_platform_data IS NOT NULL", "raw_platform_data <> ''", "raw_platform_data <> '{}'"]
    params: list[Any] = []
    if platform:
        where.append("platform = ?")
        params.append(platform)
    if ids:
        where.append("id IN (" + ",".join("?" for _ in ids) + ")")
        params.extend(int(value) for value in ids)
    sql = f"SELECT {select_cols} FROM vkpi_kol_pool WHERE {' AND '.join(where)} ORDER BY id LIMIT ?"
    params.append(int(limit))
    rows = [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]
    for row in rows:
        row["_has_ledger"] = has_ledger
        row["_stale"] = force or not has_ledger or _ledger_stale(row)
    return rows


def _ledger_stale(row: dict[str, Any]) -> bool:
    from app.domains.kol.pool_enrich import RAW_FIELDS_EXTRACTOR_VERSION

    extracted_at = row.get("raw_fields_extracted_at")
    if not extracted_at:
        return True
    if str(row.get("raw_fields_extractor_version") or "") != RAW_FIELDS_EXTRACTOR_VERSION:
        return True
    updated_at = row.get("updated_at")
    if not updated_at:
        return False
    try:
        return _as_dt(updated_at) > _as_dt(extracted_at)
    except Exception:
        return True


def _as_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _queue_state(conn: Any, kol_pool_id: int) -> str | None:
    try:
        row = conn.execute(
            "SELECT status FROM vkpi_kol_contact_acquisition_queue WHERE kol_pool_id=?",
            (int(kol_pool_id),),
        ).fetchone()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return "table_missing"
    return str(dict(row).get("status") or "") if row else None


def run(conn: Any, args: argparse.Namespace) -> dict[str, Any]:
    import app.domains.kol.pool  # noqa: F401 — pool_enrich 单独先导会触发既有循环导入
    from app.domains.kol.business_contact_extract import extract_contacts_multi_source
    from app.domains.kol.pool_enrich import RAW_FIELDS_EXTRACTOR_VERSION, apply_raw_fields, extract_raw_fields

    rows = _candidate_rows(conn, limit=max(1, int(args.limit)), platform=str(args.platform or "").strip().lower(),
                           ids=list(args.id or []), force=bool(args.force))
    stats: dict[str, Any] = {
        "extractor_version": RAW_FIELDS_EXTRACTOR_VERSION,
        "candidates": len(rows),
        "skipped_fresh_ledger": 0,
        "processed": 0,
        "rows_with_any_field": 0,
        "field_fill": {key: 0 for key in FIELD_KEYS},
        "platform_fill": {},
        "written_rows": 0,
        "contacts": {
            "rows_with_candidates": 0,
            "candidate_total": 0,
            "by_type": {},
            "would_enqueue": 0,
            "enqueued": 0,
            "already_queued": 0,
            "suppressed": 0,
            "reconciled": 0,
            "queue_table_missing": 0,
        },
        "errors": 0,
    }
    per_platform: dict[str, Counter] = {}
    contact_types: Counter = Counter()
    for row in rows:
        if not row.get("_stale"):
            stats["skipped_fresh_ledger"] += 1
            continue
        kol_id = int(row["id"])
        platform = str(row.get("platform") or "").strip().lower()
        raw = row.get("raw_platform_data")
        try:
            fields = extract_raw_fields(raw, platform=platform)
        except Exception:
            stats["errors"] += 1
            LOG.warning("extract failed kol=%s", kol_id, exc_info=True)
            continue
        stats["processed"] += 1
        bucket = per_platform.setdefault(platform or "unknown", Counter())
        bucket["rows"] += 1
        any_field = False
        for key in FIELD_KEYS:
            if fields.get(key) is not None:
                stats["field_fill"][key] += 1
                bucket[key] += 1
                any_field = True
        if any_field:
            stats["rows_with_any_field"] += 1
        if args.apply:
            try:
                result = apply_raw_fields(conn, kol_id, raw, platform=platform)
                if result.get("written"):
                    stats["written_rows"] += 1
            except Exception:
                stats["errors"] += 1
                LOG.warning("apply failed kol=%s", kol_id, exc_info=True)
                try:
                    conn.rollback()
                except Exception:
                    pass
                continue
        if args.skip_contacts:
            continue
        try:
            raw_dict = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
            candidates = extract_contacts_multi_source(raw_dict, platform=platform) if isinstance(raw_dict, dict) else []
        except Exception:
            candidates = []
        if not candidates:
            continue
        contacts = stats["contacts"]
        contacts["rows_with_candidates"] += 1
        contacts["candidate_total"] += len(candidates)
        for candidate in candidates:
            contact_types[str(candidate.get("contact_type") or "")] += 1
        state = _queue_state(conn, kol_id)
        if state == "table_missing":
            contacts["queue_table_missing"] += 1
            continue
        if state == "suppressed":
            contacts["suppressed"] += 1
            continue
        if state is not None and not args.requeue:
            contacts["already_queued"] += 1
            continue
        contacts["would_enqueue"] += 1
        if not args.apply:
            continue
        from app.domains.kol.contact_acquisition_queue import enqueue_contact_acquisition

        try:
            enqueue_contact_acquisition(kol_id, trigger_source="backfill", conn=conn)
            contacts["enqueued"] += 1
        except Exception:
            stats["errors"] += 1
            LOG.warning("enqueue failed kol=%s", kol_id, exc_info=True)
            continue
        if args.reconcile:
            from app.domains.kol.contact_acquisition_queue import reconcile_contact_acquisition

            try:
                reconcile_contact_acquisition(kol_id, brand_scope=str(args.brand_scope), conn=conn)
                contacts["reconciled"] += 1
            except Exception:
                stats["errors"] += 1
                LOG.warning("reconcile failed kol=%s", kol_id, exc_info=True)
    stats["platform_fill"] = {platform: dict(counter) for platform, counter in sorted(per_platform.items())}
    stats["contacts"]["by_type"] = dict(contact_types.most_common())
    return stats


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    from app.db.connection import get_conn
    from app.domains.kol.pool_common import _table_columns

    conn = get_conn()
    columns = _table_columns(conn, "vkpi_kol_pool")
    if "topic_details_json" not in columns or "tagged_brands_json" not in columns:
        _emit({"status": "blocked", "reason": "migration_291_not_applied"})
        return 2
    stats = run(conn, args)
    stats["status"] = "applied" if args.apply else "dry_run"
    stats["write_db"] = bool(args.apply)
    _emit(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
