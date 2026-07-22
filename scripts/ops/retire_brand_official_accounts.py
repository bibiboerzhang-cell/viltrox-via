#!/usr/bin/env python3
"""Retire competitor brand official accounts from vkpi_kol_pool(按 platform+handle 名称匹配).

背景(2026-07-21 品牌官号混入案):发现闸上线前已入池的竞品品牌官号
(FEELWORLD/NEEWER/Godox/Tamron/7Artisans/FUJIFILM/Panavision/DZOFILM/Thypoch)
仍留在池里污染推荐面。此脚本按 (platform, handle) 名称匹配清退(本地/prod 行 id 不同,
绝不按 id 硬编码),结构照官号清退前例:备份 → 松外键置 NULL → 删子表 → 删池行 → commit → 报总数。

Truth boundaries / red lines:

* 只清退池行与其子表行;绝不触碰 viltrox_fit_score / rule_v0 / 任何评分公式。
* 幂等:按名称匹配,无匹配即报零结束;重跑无副作用。
* 默认 dry-run(只打印将删的池行与子表行数);--apply 才真正删除。
* --apply 先全量备份到 /tmp/brand-official-retirement-<date>.json 再动手;任何写阶段
  异常 → 整体 rollback + 非零退出,绝不留半删状态。

用法(本地验证;prod 执行留给主会话):
  APP_ROLE=admin-web ENABLE_SCHEDULER=0 .venv/bin/python scripts/ops/retire_brand_official_accounts.py
  APP_ROLE=admin-web ENABLE_SCHEDULER=0 .venv/bin/python scripts/ops/retire_brand_official_accounts.py --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
LOG = logging.getLogger("viltrox.ops.retire_brand_official_accounts")

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 清退目标:竞品品牌官号,按 (platform, handle) 名称匹配(小写等值;YouTube 无 @handle 的
# 行以 UC 频道 ID 为 handle)。本地与 prod 通用——id 不同、名称一致。
TARGETS: list[tuple[str, str]] = [
    ("tiktok", "feelworldofficial"),
    ("youtube", "UC51_cDBWvxF86jmmQrYuy4A"),
    ("tiktok", "feelworld.uk"),
    ("tiktok", "neewerofficial"),
    ("tiktok", "godox_global"),
    ("tiktok", "tamron_europe"),
    ("tiktok", "7artisansglobal"),
    ("tiktok", "fujifilmx_us"),
    ("youtube", "UCU7ExGoX7S-g74PWxQMnz9A"),
    ("instagram", "panavisionofficial"),
    ("tiktok", "dzofilm_official"),
    ("tiktok", "thypoch_official"),
]

# 硬外键子表(kol_pool_id 列,先删子行再删池行)。清单照官号清退前例,一字不改。
CHILD_TABLES: list[str] = [
    "vkpi_kol_video_evidence",
    "vkpi_kol_url_deep_crawl_runs",
    "vkpi_kol_llm_deep_analysis_results",
    "vkpi_kol_profile_recall_status",
    "vkpi_kol_pool_contacts",
    "vkpi_kol_pool_aliases",
    "vkpi_kol_pool_brand_links",
    "vkpi_kol_embeddings",
    "vkpi_kol_recommendations",
    "vkpi_recommendation_outcomes",
    "vkpi_competitor_relation",
    "vkpi_kol_refresh_tier",
    "vkpi_kol_profile_index_entries",
    "vkpi_kol_pool_favorites",
    "vkpi_kol_pool_touches",
    "vkpi_kol_memory_snapshots",
    "vkpi_kol_lifecycle_events",
    "vkpi_kol_portal_tokens",
    "vkpi_kol_pool_members",
    "vkpi_goaffpro_kol_links",
    "vkpi_kol_cooperation_events",
]

# 松外键表(行保留,引用列置 NULL)。表清单与列映射照官号清退前例,一字不改。
LOOSE_FK_TABLES: list[str] = [
    "vkpi_ai_cost_ledger",
    "vkpi_kol_search_session_items",
    "vkpi_industry_accounts",
    "vkpi_legacy_kol_profiles_staging",
    "vkpi_legacy_cooperations_staging",
    "vkpi_legacy_risk_watchlist_staging",
    "vkpi_project_kol_assignments",
    "vkpi_project_contracts",
    "vkpi_pii_export_ledger",
]
LOOSE_FK_COLUMNS: dict[str, str] = {
    "vkpi_industry_accounts": "linked_kol_pool_id",
    "vkpi_legacy_kol_profiles_staging": "matched_kol_pool_id",
    "vkpi_legacy_cooperations_staging": "matched_kol_pool_id",
    "vkpi_legacy_risk_watchlist_staging": "matched_kol_pool_id",
    "vkpi_pii_export_ledger": "subject_kol_pool_id",
}


def _resolve_target_ids(conn: Any) -> list[int]:
    """按 (platform, handle) 名称匹配池行 id(小写等值;幂等,无匹配返回空表)。"""
    ids: set[int] = set()
    for platform, handle in TARGETS:
        rows = conn.execute(
            "SELECT id, platform, handle, display_name FROM vkpi_kol_pool "
            "WHERE LOWER(platform)=? AND LOWER(handle)=?",
            (platform.lower(), handle.lower()),
        ).fetchall()
        for row in rows:
            ids.add(int(row["id"]))
            LOG.info(
                "匹配 %s/%s -> id=%s display_name=%r",
                platform, handle, row["id"], row["display_name"],
            )
        if not rows:
            LOG.info("无匹配(已清退或本库不存在):%s/%s", platform, handle)
    return sorted(ids)


def _fetch_rows(conn: Any, table: str, column: str, ids: list[int]) -> list[dict[str, Any]] | None:
    """读一张表里引用目标池行的全部行;表缺/列缺 → rollback 并返回 None(该表跳过)。"""
    ph = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {column} IN ({ph})", tuple(ids)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        conn.rollback()
        LOG.debug("表 %s 读取失败(本库可能无此表/列),跳过:%s", table, exc)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="按名称清退竞品品牌官号池行(默认 dry-run)")
    parser.add_argument("--apply", action="store_true", help="真正执行删除(默认只打印将删行)")
    args = parser.parse_args()

    from app.db.connection import get_conn

    conn = get_conn()
    ids = _resolve_target_ids(conn)
    if not ids:
        LOG.info("零匹配,无事可做(幂等结束)。")
        return 0
    ph = ",".join("?" for _ in ids)
    id_params = tuple(ids)

    pool_rows = [
        dict(r) for r in conn.execute(f"SELECT * FROM vkpi_kol_pool WHERE id IN ({ph})", id_params).fetchall()
    ]

    # 读阶段:硬外键子表 + 松外键表全量读出(备份原料 + dry-run 报数);失败表记 None 跳过。
    child_rows: dict[str, list[dict[str, Any]]] = {}
    for table in CHILD_TABLES:
        rows = _fetch_rows(conn, table, "kol_pool_id", ids)
        if rows:
            child_rows[table] = rows
    loose_rows: dict[str, list[dict[str, Any]]] = {}
    for table in LOOSE_FK_TABLES:
        column = LOOSE_FK_COLUMNS.get(table, "kol_pool_id")
        rows = _fetch_rows(conn, table, column, ids)
        if rows:
            loose_rows[table] = rows

    LOG.info("目标池行 %d 条:ids=%s", len(pool_rows), ids)
    for row in pool_rows:
        LOG.info(
            "  将删池行 id=%s %s/%s display_name=%r followers=%s",
            row.get("id"), row.get("platform"), row.get("handle"),
            row.get("display_name"), row.get("followers"),
        )
    for table, rows in child_rows.items():
        LOG.info("  子表 %s:将删 %d 行", table, len(rows))
    for table, rows in loose_rows.items():
        column = LOOSE_FK_COLUMNS.get(table, "kol_pool_id")
        LOG.info("  松外键 %s.%s:将置 NULL %d 行", table, column, len(rows))

    if not args.apply:
        LOG.info(
            "dry-run 结束(未动库)。加 --apply 执行:池行 %d + 子表行 %d + 松外键置NULL %d。",
            len(pool_rows),
            sum(len(v) for v in child_rows.values()),
            sum(len(v) for v in loose_rows.values()),
        )
        return 0

    # 备份先行(--apply 才写):池行 + 子表行 + 松外键行全量落盘,可回滚可追溯。
    backup_path = Path(f"/tmp/brand-official-retirement-{dt.date.today().isoformat()}.json")
    backup = {
        "exported_at": str(dt.datetime.now(dt.UTC)),
        "target_ids": ids,
        "pool": pool_rows,
        "children": child_rows,
        "loose_fk": loose_rows,
    }
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, default=str))
    LOG.info("备份已写 %s(池 %d 行,子表 %d 张)", backup_path, len(pool_rows), len(child_rows))

    # 写阶段:只碰读阶段验证过存在的表,单事务;任何异常整体 rollback,绝不留半删状态。
    try:
        for table, rows in loose_rows.items():
            column = LOOSE_FK_COLUMNS.get(table, "kol_pool_id")
            conn.execute(f"UPDATE {table} SET {column}=NULL WHERE {column} IN ({ph})", id_params)
            LOG.info("松外键置 NULL:%s.%s(%d 行)", table, column, len(rows))
        for table, rows in child_rows.items():
            conn.execute(f"DELETE FROM {table} WHERE kol_pool_id IN ({ph})", id_params)
            LOG.info("子表删除:%s(%d 行)", table, len(rows))
        # 池内自引用(重复合并指针)先解,防 FK 卡删。
        conn.execute(f"UPDATE vkpi_kol_pool SET duplicate_of_id=NULL WHERE duplicate_of_id IN ({ph})", id_params)
        conn.execute(f"DELETE FROM vkpi_kol_pool WHERE id IN ({ph})", id_params)
        conn.commit()
    except Exception:
        conn.rollback()
        LOG.exception("写阶段异常,已整体 rollback(库未变;备份仍在 %s)", backup_path)
        return 1

    total = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()["n"]
    LOG.info(
        "清退完成:删池行 %d + 子表行 %d,松外键置NULL %d;池总数=%s;备份=%s",
        len(pool_rows),
        sum(len(v) for v in child_rows.values()),
        sum(len(v) for v in loose_rows.values()),
        total,
        backup_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
