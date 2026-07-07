#!/usr/bin/env python3
"""Seed vkpi_kol_rates from historical contract fees (B3 学习闭环通电 · 刀3).

背景:vkpi_kol_rates(迁移211)0 行 → rate_card.estimate_rate 全员走
cpm_benchmark_v0 行业基准兜底 → 发射台预算段=拍脑袋常数。历史真实费用其实
已经落库,只是没人搬进报价台账。本脚本做的就是这一次性搬运。

真落点侦察(全只读扫描,再决定写什么):
  ① vkpi_project_contracts(迁移098):status='confirmed' 且 fee_amount>0 且
     kol_pool_id 非空的合同 → 人工确认过的合同成交价,最硬的真报价。
     未确认(extraction 草稿)/缺 kol_pool_id/非 USD 的行只统计不搬,诚实报缺口。
  ② vkpi_cost_ledger:cost_type='cash_fee' 且 status='actual' 的账本行
     (来源 source_ref='contract:{id}')→ 与①同源,凡 contract_id 已被①覆盖即跳过,
     只兜①漏掉的孤儿行(有账无合同行的历史脏数据)。

写入(走 rate_card.add_rate 唯一 sanctioned 写路径,只落 vkpi_kol_rates 一表):
  source='contract' / confidence='high' / platform=池表平台 / effective_date=确认日;
  note 带确定性标记 seed_from=contract:{id},重复跑先查同 note 行,幂等不双记。

规矩:
  - 默认 dry-run 只打印计划;加 --write 才落库。
  - 绝不触碰任何评分字段(viltrox_fit_score 等),绝不碰 rule_v0。
  - 一条真费用行都没有时诚实打印 0 并说明数据缺口,绝不造假数据。
  - SQL 兼容层:占位符用 ?,SQL 字符串里不出现字面百分号;金额判断拉回 Python。

用法:
  .venv/bin/python scripts/seed_kol_rates_from_contracts.py            # dry-run
  .venv/bin/python scripts/seed_kol_rates_from_contracts.py --write    # 落库
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# 绝对路径载 .env,防 cwd 陷阱(仿 scripts/backfill_pool_lang_geo.py)。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
BACKEND = PROJECT_ROOT / "backend"


def load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime_sync, get_conn  # noqa: E402
from app.domains.kol import rate_card  # noqa: E402


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _already_seeded(conn: Any, kol_pool_id: int, note: str) -> bool:
    """幂等锚:同 KOL + source=contract + 同确定性 note 已在台账即跳过(等值匹配,不用 LIKE)。"""
    row = conn.execute(
        "SELECT id FROM vkpi_kol_rates WHERE kol_pool_id = ? AND source = 'contract' AND note = ? LIMIT 1",
        (int(kol_pool_id), note),
    ).fetchone()
    return row is not None


def _scan_contracts(conn: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """①合同表侦察:可搬候选 + 缺口计数(未确认/缺 KOL/非 USD 只统计不搬)。"""
    rows = conn.execute(
        """
        SELECT id, project_id, assignment_id, kol_pool_id, status,
               fee_amount, fee_currency, confirmed_at, created_at
        FROM vkpi_project_contracts
        ORDER BY id ASC
        """,
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    gaps = {"no_fee": 0, "unconfirmed_fee": 0, "no_kol": 0, "non_usd": 0}
    for raw in rows:
        row = dict(raw)
        fee = _float(row.get("fee_amount"))
        if fee is None or fee <= 0:
            gaps["no_fee"] += 1
            continue
        if str(row.get("status") or "") != "confirmed":
            gaps["unconfirmed_fee"] += 1
            continue
        kol_id = _int(row.get("kol_pool_id"))
        if kol_id is None:
            gaps["no_kol"] += 1
            continue
        currency = str(row.get("fee_currency") or "USD").upper()
        if currency != "USD":
            gaps["non_usd"] += 1
            continue
        effective = row.get("confirmed_at") or row.get("created_at")
        candidates.append(
            {
                "contract_id": int(row["id"]),
                "kol_pool_id": kol_id,
                "amount_usd": round(fee, 2),
                "effective_date": str(effective)[:10] if effective else "",
                "note": f"seed_from=contract:{int(row['id'])} project={_int(row.get('project_id'))}",
            }
        )
    return candidates, gaps


def _scan_cost_ledger(conn: Any, covered_contract_ids: set[int]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """②账本兜底:cash_fee actual 行里未被①覆盖、又能定位 KOL 的孤儿行。"""
    rows = conn.execute(
        """
        SELECT id, project_id, kol_id, amount_cents, currency, status,
               source_ref, metadata_json, incurred_at
        FROM vkpi_cost_ledger
        WHERE cost_type = 'cash_fee'
        ORDER BY id ASC
        """,
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    gaps = {"not_actual": 0, "covered_by_contract": 0, "no_kol": 0, "non_usd": 0, "no_amount": 0}
    for raw in rows:
        row = dict(raw)
        if str(row.get("status") or "") != "actual":
            gaps["not_actual"] += 1
            continue
        cents = _int(row.get("amount_cents"))
        if cents is None or cents <= 0:
            gaps["no_amount"] += 1
            continue
        if str(row.get("currency") or "USD").upper() != "USD":
            gaps["non_usd"] += 1
            continue
        meta = _loads(row.get("metadata_json"))
        contract_id = _int(meta.get("contract_id"))
        if contract_id is not None and contract_id in covered_contract_ids:
            gaps["covered_by_contract"] += 1
            continue
        kol_id = _int(meta.get("kol_pool_id")) or _int(row.get("kol_id"))
        if kol_id is None:
            gaps["no_kol"] += 1
            continue
        effective = row.get("incurred_at")
        candidates.append(
            {
                "ledger_id": int(row["id"]),
                "kol_pool_id": kol_id,
                "amount_usd": round(cents / 100.0, 2),
                "effective_date": str(effective)[:10] if effective else "",
                "note": f"seed_from=cost_ledger:{int(row['id'])} project={_int(row.get('project_id'))}",
            }
        )
    return candidates, gaps


def _kol_platform(conn: Any, kol_pool_id: int) -> str:
    row = conn.execute(
        "SELECT platform FROM vkpi_kol_pool WHERE id = ?", (int(kol_pool_id),)
    ).fetchone()
    return str(dict(row).get("platform") or "").lower() if row else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed vkpi_kol_rates from historical contract fees")
    parser.add_argument("--write", action="store_true", help="真落库(缺省 dry-run 只打印计划)")
    args = parser.parse_args()

    conn = get_conn()
    contract_rows, contract_gaps = _scan_contracts(conn)
    covered = {c["contract_id"] for c in contract_rows}
    ledger_rows, ledger_gaps = _scan_cost_ledger(conn, covered)
    candidates = contract_rows + ledger_rows

    print("== 侦察结果 ==")
    print(f"合同表可搬候选: {len(contract_rows)}  缺口: {contract_gaps}")
    print(f"账本兜底候选:   {len(ledger_rows)}  缺口: {ledger_gaps}")

    if not candidates:
        print()
        print("真实费用行 0 条 — 诚实数据缺口,不造假:")
        print("  vkpi_project_contracts 里没有「已确认 + 有费用 + 挂 KOL」的合同,")
        print("  vkpi_cost_ledger 里也没有可定位 KOL 的 cash_fee 实付行。")
        print("  出路:上传合同并人工确认费用字段,或在报价卡手动录入报价(add_rate);")
        print("  在此之前 estimate_rate 只能走 cpm_benchmark_v0,发射台预算段会持续显示 estimate_only 警示。")
        close_db_runtime_sync()
        return 0

    seeded = 0
    skipped_existing = 0
    seeded_kols: set[int] = set()
    for cand in candidates:
        kol_id = cand["kol_pool_id"]
        note = cand["note"]
        if _already_seeded(conn, kol_id, note):
            skipped_existing += 1
            print(f"  = 已在台账,跳过: kol={kol_id} {note}")
            continue
        line = (f"kol={kol_id} amount_usd={cand['amount_usd']} source=contract "
                f"confidence=high effective={cand['effective_date'] or '-'} {note}")
        if not args.write:
            print(f"  + [dry-run] 计划写入: {line}")
            seeded += 1
            seeded_kols.add(kol_id)
            continue
        payload = {
            "amount_usd": cand["amount_usd"],
            "source": "contract",
            "confidence": "high",
            "platform": _kol_platform(conn, kol_id),
            "content_type": "",
            "note": note,
        }
        if cand["effective_date"]:
            payload["effective_date"] = cand["effective_date"]
        try:
            created = rate_card.add_rate(kol_id, payload, conn=conn)
            print(f"  + 已写入 rate id={created.get('id')}: {line}")
            seeded += 1
            seeded_kols.add(kol_id)
        except Exception as exc:  # noqa: BLE001 — 单行失败如实报,不掩盖不中断
            print(f"  ! 写入失败: {line} -> {str(exc)[:200]}")

    print()
    mode = "write" if args.write else "dry-run"
    print(f"== 统计({mode})== 候选 {len(candidates)} | 写入/计划 {seeded} | 幂等跳过 {skipped_existing}")

    if args.write and seeded_kols:
        print()
        print("== 灌种后 estimate_rate 验证(应从 cpm_benchmark_v0 切到 recorded_rates_median_v0)==")
        for kol_id in sorted(seeded_kols):
            est = rate_card.estimate_rate(kol_id, conn=conn)
            print(f"  kol={kol_id} method={est.get('method')} p50={est.get('estimated_usd_p50')} "
                  f"conf={est.get('confidence')} src_count={est.get('source_count')}")
    close_db_runtime_sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
