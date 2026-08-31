#!/usr/bin/env python3
"""L0 邮箱重抽回填:用修复后的提取链重扫全池 raw,捞回当年被换行腐蚀吃掉的邮箱。

背景(业务活雷,根因已修):business_contact_extract 的 raw_full_scan 兜底扫描
曾对 json.dumps(raw) 文本跑邮箱正则,转义换行 \\n 的字面 n 被吞进本地部分,
产出 n 前缀假地址(\\nfoo@bar.com -> nfoo@bar.com)。正则输入已改扫原始字符串
叶子(_iter_raw_strings);历史脏数据由 fix_email_newline_corruption.py 清了
42 对 + 9 活雷,余 71 个 report-only 嫌疑(约半假邮箱半缺首字母)待裁。

本脚本做三件事(零 Apify / 零 LLM / 零出网,纯读本地已抓回 raw):
  ① 全池重抽:对每个有 raw 的 KOL 跑修复后的 extract_contacts_multi_source,
     只取 email 类候选;表里没有的「新邮箱」按既有置信度口径(_candidate_source
     + ingest_contact,与 contact_acquisition_queue L0 执行体同一条路)写
     vkpi_kol_pool_contacts。已存在的值一律跳过(ingest 本身也只 max-merge
     置信度,绝不覆盖更高置信来源)。
  ② 71 嫌疑自动裁决:嫌疑=raw_full_scan、n 开头、无干净孪生、未 invalid 的
     email 行。若该 KOL 重抽出干净值 clean 且满足
       A 型「嫌疑 = n + clean」(转义换行吞出的 n 前缀),或
       B 型「嫌疑 = clean 缺首字母」(clean 补全嫌疑缺失的首字母),
     则嫌疑行置 verification_status='invalid'+invalidated_at(不 DELETE,
     evidence 表有 FK),干净值入表(①里已插或本就在表)。裁决不了的保持
     report-only 原样不动。
  ③ 逐行 before/after 台账走 stdout_utils;末尾打印 prod 跑法(本脚本可直接
     在 prod 跑,遵守 cd /tmp + PYTHONDONTWRITEBYTECODE=1 探针纪律)。

红线:只写 vkpi_kol_pool_contacts 的 email 行(+其 evidence 观测),不碰
vkpi_kol_pool.email、不碰 outreach 发送、不触 viltrox_fit_score/rule_v0。

用法:
  .venv/bin/python scripts/backfill_email_reextract.py             # 默认 --dry-run
  .venv/bin/python scripts/backfill_email_reextract.py --apply     # 写本地库
  .venv/bin/python scripts/backfill_email_reextract.py --limit 50  # 只扫前 50 个 KOL
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.stdout_utils import out as stdout_out
except ModuleNotFoundError:  # direct execution: scripts/ is sys.path[0]
    try:
        from stdout_utils import out as stdout_out
    except ModuleNotFoundError:  # prod /tmp 单文件拷贝兜底:仍保持唯一 stdout 缝
        def stdout_out(*values: object, sep: str | None = " ", end: str | None = "\n",
                       file: Any | None = None, flush: bool = False) -> None:
            stream = sys.stdout if file is None else file
            stream.write((sep if sep is not None else " ").join(str(v) for v in values)
                         + (end if end is not None else "\n"))
            if flush:
                stream.flush()


def _out(*args: object) -> None:
    stdout_out(" ".join(str(a) for a in args) + "\n", end="")


# prod 探针:脚本被拷到 /tmp 时用 VKPI_ROOT 指回线上仓库根(含 backend/ 与 .env)。
PROJECT_ROOT = Path(os.environ.get("VKPI_ROOT") or Path(__file__).resolve().parents[1])
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
from app.domains.kol.business_contact_extract import extract_contacts_multi_source  # noqa: E402
from app.domains.kol.contact_acquisition_queue import (  # noqa: E402
    _candidate_source,
    _safe_profile_url,
)
from app.domains.kol.contact_ingest import ContactValidationError, ingest_contact  # noqa: E402

# 嫌疑定义 = fix_email_newline_corruption.py 模式C 的 report-only 集合:
# raw_full_scan、n 开头、无干净孪生、尚未 invalid 的 email 行。
_SUSPECT_SQL = """
SELECT c.id, c.kol_pool_id, c.contact_value, c.verification_status
FROM vkpi_kol_pool_contacts c
WHERE c.contact_type = 'email' AND c.contact_source = 'raw_full_scan'
  AND strpos(lower(c.contact_value), 'n') = 1
  AND c.verification_status <> 'invalid'
  AND NOT EXISTS (
        SELECT 1 FROM vkpi_kol_pool_contacts d
        WHERE d.kol_pool_id = c.kol_pool_id AND d.id <> c.id
          AND d.contact_type = 'email'
          AND lower(c.contact_value) = 'n' || lower(d.contact_value)
  )
ORDER BY c.kol_pool_id, c.id
"""

_EXISTING_SQL = """
SELECT id, kol_pool_id, contact_value, normalized_value, contact_source,
       confidence, verification_status
FROM vkpi_kol_pool_contacts
WHERE contact_type = 'email'
ORDER BY kol_pool_id, id
"""

_PROD_RUNBOOK = """
== prod 跑法(本脚本可直接在 prod 跑;正则重抽必须跑 Python,无纯 SQL 等价物)==
1) 拷贝脚本(不 rsync 仓库,不动线上代码):
     scp scripts/backfill_email_reextract.py <prod>:/tmp/
2) prod 探针纪律:cd /tmp && export PYTHONDONTWRITEBYTECODE=1(不在发布目录跑
   python、不落 .pyc;脚本经 VKPI_ROOT 只读线上 backend/ 代码与 .env)。
3) 先干跑并核对台账(不写任何数据):
     cd /tmp && PYTHONDONTWRITEBYTECODE=1 VKPI_ROOT=<APP_ROOT> \\
       <APP_ROOT>/.venv/bin/python /tmp/backfill_email_reextract.py --dry-run
   (<APP_ROOT> = 线上仓库根,含 backend/ 与 .env;venv 路径以线上实际为准)
4) 台账行数与本地 dry-run 数量级核对无异常后:
     cd /tmp && PYTHONDONTWRITEBYTECODE=1 VKPI_ROOT=<APP_ROOT> \\
       <APP_ROOT>/.venv/bin/python /tmp/backfill_email_reextract.py --apply
5) 收尾核对 SQL(prod psql 手跑):
     SELECT COUNT(*) FROM vkpi_kol_pool_contacts
      WHERE contact_type='email' AND verification_status='invalid';
     SELECT COUNT(*) FROM vkpi_kol_pool_contacts
      WHERE contact_type='email' AND contact_source='raw_full_scan'
        AND lower(contact_value) LIKE 'n%' AND verification_status<>'invalid';
注意:零 Apify / 零 LLM / 零出网;只写 vkpi_kol_pool_contacts(email 行)与其
vkpi_kol_contact_evidence 观测;不碰 vkpi_kol_pool.email 与 outreach。幂等:
重复跑只会 SKIP 已在表的值、跳过已 invalid 的嫌疑。
=============================================================================
"""


def _row_dict(row: Any) -> dict[str, Any]:
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {k: row[k] for k in row.keys()}


def extract_email_candidates(raw: dict[str, Any], platform: str) -> list[dict[str, Any]]:
    """修复后的提取链重抽,只留 email 类候选(纯函数、零网络)。"""
    contacts = extract_contacts_multi_source(raw or {}, platform=platform or "")
    return [c for c in contacts if c.get("contact_type") == "email"]


def plan_new_emails(
    candidates: list[dict[str, Any]], existing_values: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """分拣候选:(待插入的新邮箱, 已在表跳过的)。比较大小写不敏感;
    已存在的值(含 invalid 行)一律不再写 —— 既不覆盖更高置信来源,也不复活废行。"""
    to_insert: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in candidates:
        value = str(c.get("contact_value") or "").strip()
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        (skipped if key in existing_values else to_insert).append(c)
    return to_insert, skipped


def adjudicate_suspect(
    suspect_value: str, clean_values: list[str]
) -> tuple[str, str] | None:
    """嫌疑裁决:返回 (裁决型, 干净值) 或 None(裁决不了,保持 report-only)。

    A 型:嫌疑 = 'n' + 干净(转义换行吞出的 n 前缀假地址)。
    B 型:嫌疑 = 干净缺首字母(重抽出的干净值恰好补全首字母)。
    干净值必须来自重抽结果;clean == 嫌疑本身不构成裁决(真 n 开头邮箱不误伤)。
    """
    s = str(suspect_value or "").strip().lower()
    if not s:
        return None
    for clean in clean_values:
        c = str(clean or "").strip().lower()
        if c and c != s and s == "n" + c:
            return "A(n前缀)", clean
    for clean in clean_values:
        c = str(clean or "").strip().lower()
        if c and c != s and len(c) == len(s) + 1 and c[1:] == s:
            return "B(缺首字母)", clean
    return None


def evaluate_kol(
    raw: dict[str, Any],
    platform: str,
    existing_values: set[str],
    suspect_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """单 KOL 纯计算:重抽 -> 新邮箱计划 + 嫌疑裁决(不碰库,可单测)。"""
    candidates = extract_email_candidates(raw, platform)
    to_insert, skipped = plan_new_emails(candidates, existing_values)
    clean_values = [str(c.get("contact_value") or "") for c in candidates]
    decisions: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for srow in suspect_rows:
        verdict = adjudicate_suspect(str(srow.get("contact_value") or ""), clean_values)
        if verdict is None:
            unresolved.append(srow)
            continue
        kind, clean = verdict
        decisions.append({
            "suspect": srow,
            "kind": kind,
            "clean_value": clean,
            "clean_in_table": clean.strip().lower() in existing_values,
        })
    return {
        "to_insert": to_insert,
        "skipped": skipped,
        "decisions": decisions,
        "unresolved": unresolved,
    }


def _load_existing_by_kol(db: Any) -> dict[int, set[str]]:
    by_kol: dict[int, set[str]] = {}
    for r in db.execute(_EXISTING_SQL).fetchall():
        row = _row_dict(r)
        vals = by_kol.setdefault(int(row["kol_pool_id"]), set())
        for key in ("contact_value", "normalized_value"):
            v = str(row.get(key) or "").strip().lower()
            if v:
                vals.add(v)
    return by_kol


def _load_suspects_by_kol(db: Any) -> dict[int, list[dict[str, Any]]]:
    by_kol: dict[int, list[dict[str, Any]]] = {}
    for r in db.execute(_SUSPECT_SQL).fetchall():
        row = _row_dict(r)
        by_kol.setdefault(int(row["kol_pool_id"]), []).append(row)
    return by_kol


def _pool_ids(db: Any, limit: int | None) -> list[int]:
    sql = (
        "SELECT id FROM vkpi_kol_pool "
        "WHERE raw_platform_data IS NOT NULL AND raw_platform_data <> '' ORDER BY id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [int(_row_dict(r)["id"]) for r in db.execute(sql).fetchall()]


def _fetch_pool_row(db: Any, kol_id: int) -> dict[str, Any] | None:
    r = db.execute(
        "SELECT id, platform, profile_url, raw_platform_data FROM vkpi_kol_pool WHERE id=?",
        (kol_id,),
    ).fetchone()
    return _row_dict(r) if r is not None else None


def _parse_raw(text: Any) -> dict[str, Any]:
    try:
        raw = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _ingest_one(db: Any, pool: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """既有置信度口径写入:_candidate_source + ingest_contact(与 L0 队列执行体同路)。
    ingest 内部 max-merge 置信度、invalid/verified 状态不降级 —— 不覆盖更高置信来源。"""
    profile_url = _safe_profile_url(pool.get("profile_url"))
    source, public_declared, source_field = _candidate_source(
        candidate, platform=pool.get("platform"), source_url=profile_url
    )
    try:
        ingest_contact(
            kol_pool_id=int(pool["id"]),
            contact_type="email",
            contact_value=str(candidate.get("contact_value") or ""),
            source_type=source,
            source_url=profile_url,
            source_field=source_field,
            evidence_text=str(candidate.get("evidence_text") or ""),
            confidence=float(candidate.get("confidence") or 0.0),
            is_public_declared=public_declared,
            verification_status=(
                "verified_public_business" if public_declared else "observed"
            ),
            consent_basis=(
                "legitimate_interest_public_business"
                if public_declared
                else "source_observation"
            ),
            conn=db,
        )
        return True
    except (ContactValidationError, TypeError, ValueError) as exc:
        _out(f"  REJECT kol={pool['id']} '{candidate.get('contact_value')}':"
             f" ingest 校验拒绝({type(exc).__name__})")
        return False


def _report_inserts(pool_id: int, result: dict[str, Any], *, apply: bool) -> None:
    verb = "插入" if apply else "拟插入"
    for c in result["to_insert"]:
        _out(f"  NEW kol={pool_id} '{c['contact_value']}' src={c['source_type']}"
             f" conf={c['confidence']} | before: contacts 无此值 | after: {verb}(observed 口径)")
    for c in result["skipped"]:
        _out(f"  SKIP kol={pool_id} '{c['contact_value']}' 已在表(不覆盖既有/更高置信来源)")


def _report_decisions(pool_id: int, result: dict[str, Any], *, apply: bool) -> None:
    verb = "置 invalid" if apply else "拟置 invalid"
    for d in result["decisions"]:
        s = d["suspect"]
        where = "已在表" if d["clean_in_table"] else "本轮 NEW 入表"
        _out(f"  裁决{d['kind']} kol={pool_id} 嫌疑 id={s['id']} '{s['contact_value']}'"
             f" -> 干净 '{d['clean_value']}'({where})"
             f" | before: status={s['verification_status']} | after: status=invalid | {verb}")
    for s in result["unresolved"]:
        _out(f"  嫌疑未决 kol={pool_id} id={s['id']} '{s['contact_value']}':"
             f" 重抽无满足 A/B 关系的干净值,保持 report-only 不动")


def _apply_kol(db: Any, pool: dict[str, Any], result: dict[str, Any], now: str) -> dict[str, int]:
    """写库:新邮箱走 ingest;裁决嫌疑置 invalid(带 id+值双守卫,幂等)。"""
    ingested = rejected = invalidated = 0
    for c in result["to_insert"]:
        if _ingest_one(db, pool, c):
            ingested += 1
        else:
            rejected += 1
    for d in result["decisions"]:
        s = d["suspect"]
        db.execute(
            "UPDATE vkpi_kol_pool_contacts SET verification_status='invalid',"
            " invalidated_at=? WHERE id=? AND contact_value=?"
            " AND verification_status <> 'invalid'",
            (now, int(s["id"]), s["contact_value"]),
        )
        invalidated += 1
    return {"ingested": ingested, "rejected": rejected, "invalidated": invalidated}


def _run(apply: bool, limit: int | None) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mode = "APPLY(写本地库)" if apply else "DRY-RUN(只报告,不写)"
    db = get_conn()
    _out(f"== L0 邮箱重抽回填 == 模式:{mode} @ {now}")

    existing = _load_existing_by_kol(db)
    suspects = _load_suspects_by_kol(db)
    ids = _pool_ids(db, limit)
    _out(f"扫描范围:{len(ids)} 个有 raw 的 KOL;既有 email 行覆盖 {len(existing)} 个 KOL;"
         f"待裁嫌疑 {sum(len(v) for v in suspects.values())} 行(涉 {len(suspects)} 个 KOL)")

    totals = {"kols_hit": 0, "new": 0, "skip": 0, "ingested": 0, "rejected": 0,
              "adjA": 0, "adjB": 0, "unresolved": 0, "invalidated": 0}
    for kol_id in ids:
        pool = _fetch_pool_row(db, kol_id)
        if pool is None:
            continue
        result = evaluate_kol(
            _parse_raw(pool.get("raw_platform_data")),
            str(pool.get("platform") or ""),
            existing.get(kol_id, set()),
            suspects.get(kol_id, []),
        )
        if result["to_insert"] or result["decisions"] or result["unresolved"]:
            totals["kols_hit"] += 1
        totals["new"] += len(result["to_insert"])
        totals["skip"] += len(result["skipped"])
        totals["unresolved"] += len(result["unresolved"])
        for d in result["decisions"]:
            totals["adjA" if d["kind"].startswith("A") else "adjB"] += 1
        _report_inserts(kol_id, result, apply=apply)
        _report_decisions(kol_id, result, apply=apply)
        if apply:
            done = _apply_kol(db, pool, result, now)
            totals["ingested"] += done["ingested"]
            totals["rejected"] += done["rejected"]
            totals["invalidated"] += done["invalidated"]
    if apply:
        db.commit()

    _out(f"\n汇总:命中 KOL {totals['kols_hit']};新邮箱 {totals['new']}"
         f"(已在表跳过 {totals['skip']});嫌疑裁决 A 型 {totals['adjA']} / B 型 {totals['adjB']}"
         f" / 未决 report-only {totals['unresolved']}")
    if apply:
        _out(f"已写本地库:ingest 成功 {totals['ingested']} / 校验拒绝 {totals['rejected']};"
             f"嫌疑置 invalid {totals['invalidated']} 行。")
    else:
        _out("DRY-RUN:未写任何数据。")
        _out(_PROD_RUNBOOK)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="写本地库(默认只 dry-run 报告)")
    parser.add_argument("--dry-run", action="store_true", help="显式 dry-run(与默认等价)")
    parser.add_argument("--limit", type=int, default=None, help="只扫前 N 个 KOL(调试用)")
    args = parser.parse_args()
    apply = bool(args.apply) and not bool(args.dry_run)
    return _run(apply, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
