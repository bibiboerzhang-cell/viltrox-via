#!/usr/bin/env python3
"""清洗 email 换行腐蚀行(n 前缀假地址)—— 只本地库;prod 版输出 SQL 交用户手跑。

背景(业务活雷):business_contact_extract 的 raw_full_scan 兜底扫描曾对
json.dumps(raw) 文本跑邮箱正则,转义换行 \\n 的字面 n 被吞进本地部分,产出
n 前缀假地址(\\nfoo@bar.com -> nfoo@bar.com),外联往错地址发。正则输入已修
(改扫原始字符串叶子);本脚本清历史脏数据。

清洗范围(只动 email 相关行/列,不碰 outreach 发送逻辑):
  模式A vkpi_kol_pool_contacts 内同 KOL 双写对:腐蚀行(contact_source=
        'raw_full_scan' 且 lower(contact_value)='n'||lower(干净行)),干净行
        来源置信不低于腐蚀行(youtube_about_declared > ig_business_profile >
        bio_explicit_contact > raw_bio_scan > raw_full_scan)。
        处置:腐蚀行标 verification_status='invalid' + invalidated_at(不 DELETE:
        vkpi_kol_contact_evidence 有 FK 指向 contacts,且留审计痕)。
  模式B vkpi_kol_pool.email 本身是腐蚀值、contacts 里有干净孪生:
        UPDATE pool.email -> 干净值(带 email=腐蚀值 守卫,幂等)。
  模式C 只报告不动:raw_full_scan 的 n 开头邮箱但无干净孪生(无仲裁源,交人工)。

用法:
  .venv/bin/python scripts/fix_email_newline_corruption.py            # 默认 --dry-run:只报告+打 prod SQL
  .venv/bin/python scripts/fix_email_newline_corruption.py --apply    # 写本地库(逐行 before/after 台账)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
os.environ.setdefault("APP_ROLE", "admin-web")
os.environ.setdefault("ENABLE_SCHEDULER", "0")
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import get_conn  # noqa: E402

# 来源置信排序(高 -> 低);干净行必须 >= 腐蚀行才允许仲裁。
_SOURCE_TRUST = {
    "youtube_about_declared": 5,
    "ig_business_profile": 4,
    "bio_explicit_contact": 3,
    "raw_bio_scan": 2,
    "video_caption": 2,
    "raw_full_scan": 1,
}

_PAIR_SQL = """
SELECT a.id            AS bad_id,
       a.kol_pool_id   AS kol_pool_id,
       a.contact_value AS bad_value,
       a.contact_source AS bad_source,
       a.verification_status AS bad_status,
       b.id            AS good_id,
       b.contact_value AS good_value,
       b.contact_source AS good_source
FROM vkpi_kol_pool_contacts a
JOIN vkpi_kol_pool_contacts b
  ON a.kol_pool_id = b.kol_pool_id AND a.id <> b.id
 AND a.contact_type = 'email' AND b.contact_type = 'email'
 AND lower(a.contact_value) = 'n' || lower(b.contact_value)
WHERE a.contact_source = 'raw_full_scan'
ORDER BY a.kol_pool_id, a.id
"""

_POOL_SQL = """
SELECT p.id            AS pool_id,
       p.email         AS bad_email,
       c.contact_value AS good_value,
       c.contact_source AS good_source
FROM vkpi_kol_pool p
JOIN vkpi_kol_pool_contacts c
  ON c.kol_pool_id = p.id AND c.contact_type = 'email'
WHERE lower(p.email) = 'n' || lower(c.contact_value)
ORDER BY p.id
"""

_ORPHAN_SQL = """
SELECT c.id, c.kol_pool_id, c.contact_value
FROM vkpi_kol_pool_contacts c
WHERE c.contact_type = 'email' AND c.contact_source = 'raw_full_scan'
  AND strpos(lower(c.contact_value), 'n') = 1
  AND NOT EXISTS (
        SELECT 1 FROM vkpi_kol_pool_contacts d
        WHERE d.kol_pool_id = c.kol_pool_id AND d.id <> c.id
          AND d.contact_type = 'email'
          AND lower(c.contact_value) = 'n' || lower(d.contact_value)
  )
ORDER BY c.kol_pool_id, c.id
"""

# prod 版 SQL(PostgreSQL;集合式、与本脚本同守卫,交用户在 prod 手跑)。
_PROD_SQL = """
-- ============ prod 版 SQL(先跑审计 SELECT 核对行数,再在事务里执行) ============
BEGIN;

-- 模式A 审计:同 KOL 双写对里的 n 前缀腐蚀行
SELECT a.id, a.kol_pool_id, a.contact_value AS bad, b.contact_value AS good,
       a.contact_source, b.contact_source
FROM vkpi_kol_pool_contacts a
JOIN vkpi_kol_pool_contacts b
  ON a.kol_pool_id=b.kol_pool_id AND a.id<>b.id
 AND a.contact_type='email' AND b.contact_type='email'
 AND lower(a.contact_value)='n'||lower(b.contact_value)
WHERE a.contact_source='raw_full_scan';

-- 模式A 处置:腐蚀行标 invalid(不 DELETE,evidence 表有 FK)
UPDATE vkpi_kol_pool_contacts a
SET verification_status='invalid', invalidated_at=NOW()
FROM vkpi_kol_pool_contacts b
WHERE a.kol_pool_id=b.kol_pool_id AND a.id<>b.id
  AND a.contact_type='email' AND b.contact_type='email'
  AND lower(a.contact_value)='n'||lower(b.contact_value)
  AND a.contact_source='raw_full_scan'
  AND a.verification_status <> 'invalid';

-- 模式B 审计:pool.email 本身是腐蚀值且 contacts 有干净孪生
SELECT p.id, p.email AS bad, c.contact_value AS good, c.contact_source
FROM vkpi_kol_pool p
JOIN vkpi_kol_pool_contacts c ON c.kol_pool_id=p.id AND c.contact_type='email'
WHERE lower(p.email)='n'||lower(c.contact_value);

-- 模式B 处置:pool.email 换成干净值
UPDATE vkpi_kol_pool p
SET email=c.contact_value
FROM vkpi_kol_pool_contacts c
WHERE c.kol_pool_id=p.id AND c.contact_type='email'
  AND lower(p.email)='n'||lower(c.contact_value);

COMMIT;

-- 模式C(只查不动,交人工):raw_full_scan 的 n 开头邮箱、无干净孪生
SELECT c.id, c.kol_pool_id, c.contact_value
FROM vkpi_kol_pool_contacts c
WHERE c.contact_type='email' AND c.contact_source='raw_full_scan'
  AND lower(c.contact_value) LIKE 'n%'
  AND NOT EXISTS (
        SELECT 1 FROM vkpi_kol_pool_contacts d
        WHERE d.kol_pool_id=c.kol_pool_id AND d.id<>c.id AND d.contact_type='email'
          AND lower(c.contact_value)='n'||lower(d.contact_value));
-- =============================================================================
"""


def _row_dict(row: Any) -> dict[str, Any]:
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {k: row[k] for k in row.keys()}


def _trust(source: str) -> int:
    return _SOURCE_TRUST.get(str(source or "").strip(), 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="写本地库(默认只 dry-run 报告)")
    parser.add_argument("--dry-run", action="store_true", help="显式 dry-run(与默认等价)")
    args = parser.parse_args()
    apply = bool(args.apply) and not bool(args.dry_run)
    mode = "APPLY(写本地库)" if apply else "DRY-RUN(只报告,不写)"
    now = datetime.now(timezone.utc).isoformat()
    db = get_conn()

    print(f"== email 换行腐蚀清洗 == 模式:{mode} @ {now}")

    # ---- 模式A:contacts 表内双写对 ----
    pairs = [_row_dict(r) for r in db.execute(_PAIR_SQL).fetchall()]
    fixable, skipped = [], []
    for p in pairs:
        if _trust(p["good_source"]) >= _trust(p["bad_source"]):
            fixable.append(p)
        else:
            skipped.append(p)
    print(f"\n[模式A] contacts 双写对(腐蚀=n+干净):命中 {len(pairs)},可仲裁 {len(fixable)},"
          f"仲裁源不足跳过 {len(skipped)}")
    a_applied = 0
    for p in fixable:
        already = str(p["bad_status"] or "") == "invalid"
        action = "已是 invalid,跳过" if already else ("置 invalid" if apply else "拟置 invalid")
        print(f"  A kol={p['kol_pool_id']} 腐蚀行 id={p['bad_id']} '{p['bad_value']}'"
              f" ({p['bad_source']}) -> 保留 id={p['good_id']} '{p['good_value']}'"
              f" ({p['good_source']}) | before: status={p['bad_status']}"
              f" | after: status=invalid | {action}")
        if apply and not already:
            db.execute(
                "UPDATE vkpi_kol_pool_contacts SET verification_status='invalid',"
                " invalidated_at=? WHERE id=? AND contact_value=?"
                " AND verification_status <> 'invalid'",
                (now, int(p["bad_id"]), p["bad_value"]),
            )
            a_applied += 1
    for p in skipped:
        print(f"  A-skip kol={p['kol_pool_id']} id={p['bad_id']} '{p['bad_value']}':"
              f" 干净行来源 {p['good_source']} 置信低于 {p['bad_source']},不仲裁")

    # ---- 模式B:pool.email 是腐蚀值 ----
    pool_rows = [_row_dict(r) for r in db.execute(_POOL_SQL).fetchall()]
    # 同一 pool 行可能 join 出多孪生;按最高置信源取一条
    best: dict[int, dict[str, Any]] = {}
    for r in pool_rows:
        pid = int(r["pool_id"])
        if pid not in best or _trust(r["good_source"]) > _trust(best[pid]["good_source"]):
            best[pid] = r
    print(f"\n[模式B] pool.email 腐蚀且有干净孪生:命中 {len(best)} 行")
    b_applied = 0
    for r in best.values():
        action = "改写" if apply else "拟改写"
        print(f"  B pool_id={r['pool_id']} | before: email='{r['bad_email']}'"
              f" | after: email='{r['good_value']}'(源 {r['good_source']})| {action}")
        if apply:
            db.execute(
                "UPDATE vkpi_kol_pool SET email=? WHERE id=? AND email=?",
                (r["good_value"], int(r["pool_id"]), r["bad_email"]),
            )
            b_applied += 1

    # ---- 模式C:无孪生嫌疑行(只报告) ----
    orphans = [_row_dict(r) for r in db.execute(_ORPHAN_SQL).fetchall()]
    print(f"\n[模式C] raw_full_scan n 开头、无干净孪生(无仲裁源,只报告不动):{len(orphans)} 行")
    for r in orphans:
        print(f"  C id={r['id']} kol={r['kol_pool_id']} '{r['contact_value']}'")

    if apply:
        db.commit()
        print(f"\n已写本地库:模式A 置 invalid {a_applied} 行,模式B 改写 pool.email {b_applied} 行。")
    else:
        print(f"\nDRY-RUN 汇总:模式A 可仲裁 {len(fixable)} 行 / 模式B {len(best)} 行 /"
              f" 模式C 嫌疑 {len(orphans)} 行;未写任何数据。")
        print(_PROD_SQL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
