#!/usr/bin/env python3
"""全池邮箱 MX 批验证 —— 把「有邮箱」升级成「有能收信的邮箱」。

复用 backend/app/domains/kol/contact_email_quality.py 的既有层,不另造轮子:
  - 语法层 validate_email_syntax(占位域名 / 平台域名 / \\n@提及腐蚀全在里面)
  - MX 层 check_mx(dnspython 优先,dig 兜底;NXDOMAIN 或「无 MX 且无 A」才算 fail)
  - 结果缓存 cache_quality_result → vkpi_kol_contact_evidence
    (source_type='email_quality_check',按 (contact_id, 邮箱指纹) upsert → 天然幂等)

为什么不直接用模块自带 run_batch(另起一支的唯一理由):
  run_batch 只挑「还没有缓存行」的行,且单轮 DNS 预算被模块硬夹在 200。
  本脚本要的是**全池一次过**:全池 491 行摊到 203 个唯一域名,MX 查不到还要
  走 implicit-A 兜底(最坏 2 次查询/域名 = 406 次),200 的单轮预算不够。
  做法:域名切成 ≤100 个一轮,每轮前 set_dns_budget(本轮域名数 × 2),
  保证**轮内绝不会中途耗尽**——耗尽会把 dns_budget_exhausted 写进模块的
  进程内域名缓存,毒化后续所有轮次。总花费再按脚本自己的 HARD_QUOTA 硬夹。

三层缓存(顺序:库 → 进程 → 网络),决定了「重跑几乎不花配额」:
  ① 库缓存:已有 evidence 且判定确定(mx_ok/mx_bad)的域名直接沿用,零查询。
  ② 进程缓存:check_mx 内部按域名去重,gmail.com 218 行只查 1 次。
  ③ 只有以上都没有的域名才真的出网。mx_unknown 不进缓存种子——它是「没查出来」
     不是「查出来是坏的」,下一轮必须重试。

红线(写死,不接受参数放宽):
  - **只做域名级 MX/A 查询,绝不做 SMTP RCPT 探测**(灰色行为)。
  - 并发 ≤4;单查询超时 2s,超时/SERVFAIL/工具缺席一律记 unknown,**不记 fail**。
  - 单次运行 DNS 查询总量 ≤600(HARD_QUOTA);--quota 只能往小调。
  - 零 Apify、零 LLM、零 DDL;不改 contact_value、不改 verification_status、
    不触 viltrox_fit_score / rule_v0;报告只出域名与计数,不铺邮箱明文。

用法:
  .venv/bin/python scripts/run_email_mx_verification.py --dry-run   # 只列计划,零 DNS 零写库
  .venv/bin/python scripts/run_email_mx_verification.py             # 全池实跑
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from stdout_utils import out, out_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
BACKEND = PROJECT_ROOT / "backend"

if str(BACKEND) not in sys.path:  # import app.* 之前必须挂上;只动 sys.path,不读 .env
    sys.path.insert(0, str(BACKEND))

from app.domains.kol import contact_email_quality as ceq  # noqa: E402

HARD_QUOTA = 600            # 单次运行 DNS 查询总上限,写死
QUERIES_PER_DOMAIN = 2      # 最坏情况:MX 一次 + implicit-A 兜底一次
CHUNK_DOMAINS = 100         # 每轮域名数;×2 = 200 = 模块 MAX_DNS_BUDGET 上限
EMAIL_TYPES = ("email", "business_email")
TERMINAL_STATUSES = ("revoked", "invalid")
CACHE_LOAD_CHUNK = 200      # load_quality_cache 的 IN() 参数分片

MX_OK = "mx_ok"
MX_FAIL = "mx_fail"         # 报告口径;落库仍是模块的 ceq.MX_BAD
UNKNOWN = "unknown"
SYNTAX_BAD = "syntax_bad"
REPORT_BUCKETS = (MX_OK, MX_FAIL, UNKNOWN, SYNTAX_BAD)


def load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def clamp_quota(value: int) -> int:
    """脚本级 DNS 总配额硬夹在 [0, HARD_QUOTA];只能往小调。"""
    return max(0, min(int(value), HARD_QUOTA))


# ---------------------------------------------------------------- 取数与计划
def fetch_email_rows(db: Any, limit: int | None = None) -> list[dict[str, Any]]:
    """全池非终态邮箱行(email + business_email);revoked/invalid 不进批。"""
    type_marks = ",".join("?" for _ in EMAIL_TYPES)
    status_marks = ",".join("?" for _ in TERMINAL_STATUSES)
    sql = f"""
        SELECT id, kol_pool_id, contact_value, contact_source, contact_type
        FROM vkpi_kol_pool_contacts
        WHERE contact_type IN ({type_marks})
          AND COALESCE(contact_value,'') <> ''
          AND verification_status NOT IN ({status_marks})
        ORDER BY id
    """
    params: list[Any] = [*EMAIL_TYPES, *TERMINAL_STATUSES]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [dict(r) for r in db.execute(sql, tuple(params)).fetchall()]


def build_plans(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每行折成一个计划项:语法结果 + 域名 + 来源,后续全程只看计划项。"""
    plans: list[dict[str, Any]] = []
    for row in rows:
        syntax = ceq.validate_email_syntax(str(row.get("contact_value") or ""))
        plans.append(
            {
                "contact_id": int(row["id"]),
                "kol_pool_id": int(row["kol_pool_id"]),
                "email": str(row.get("contact_value") or ""),
                "contact_source": str(row.get("contact_source") or ""),
                "domain": str(syntax["domain"]),
                "syntax_ok": bool(syntax["ok"]),
                "syntax_reason": str(syntax["reason"]),
                "role": bool(syntax["role"]),
            }
        )
    return plans


def unique_domains(plans: list[dict[str, Any]]) -> list[str]:
    """语法通过的行的唯一域名,保持首次出现序(可复现)。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for plan in plans:
        domain = plan["domain"]
        if plan["syntax_ok"] and domain and domain not in seen:
            seen.add(domain)
            ordered.append(domain)
    return ordered


# ---------------------------------------------------------------- ① 库缓存种子
def seed_domains_from_db(db: Any, plans: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """从既有 evidence 取「已经有确定判定」的域名,直接沿用,零 DNS。

    只认 mx_ok / mx_bad;mx_unknown 是「没查出来」不是结论,必须重查。
    同一域名在库里出现互相矛盾的判定时整体作废(宁可重查,不采信脏缓存)。
    """
    ids = [plan["contact_id"] for plan in plans]
    cached: dict[int, dict[str, Any]] = {}
    for start in range(0, len(ids), CACHE_LOAD_CHUNK):
        cached.update(ceq.load_quality_cache(db, ids[start:start + CACHE_LOAD_CHUNK]))
    votes: dict[str, set[tuple[str, str]]] = {}
    for plan in plans:
        verdict = cached.get(plan["contact_id"])
        if verdict is None or not plan["syntax_ok"]:
            continue
        status = str(verdict["mx_status"])
        if status not in (ceq.MX_OK, ceq.MX_BAD):
            continue
        votes.setdefault(plan["domain"], set()).add((status, str(verdict["detail"])))
    seeded: dict[str, tuple[str, str]] = {}
    for domain, entries in votes.items():
        statuses = {status for status, _ in entries}
        if len(statuses) == 1:
            seeded[domain] = sorted(entries)[0]
        else:
            out(f"  cache conflict on {domain}: {sorted(statuses)} -> re-query")
    return seeded


# ---------------------------------------------------------------- ② MX 解析
def _resolve_chunk(chunk: list[str], timeout: float) -> dict[str, tuple[str, str]]:
    """一轮并发解析;并发数硬夹在 ceq.MAX_DNS_CONCURRENCY(=4)。"""
    workers = max(1, min(ceq.MAX_DNS_CONCURRENCY, len(chunk)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        answers = list(pool.map(lambda d: ceq.check_mx(d, timeout=timeout), chunk))
    return dict(zip(chunk, answers))


def resolve_domains(domains: list[str], *, timeout: float, quota: int) -> dict[str, Any]:
    """分轮解析唯一域名,总查询量硬夹在 quota;配额用尽的域名诚实记 unknown。"""
    results: dict[str, tuple[str, str]] = {}
    remaining = clamp_quota(quota)
    index = 0
    spent = 0
    rounds = 0
    while index < len(domains):
        chunk_size = min(CHUNK_DOMAINS, remaining // QUERIES_PER_DOMAIN)
        if chunk_size <= 0:
            break  # 剩余配额不够安全跑完一个域名(MX+A),停手而不是半途耗尽毒化缓存
        chunk = domains[index:index + chunk_size]
        budget = ceq.set_dns_budget(len(chunk) * QUERIES_PER_DOMAIN)
        results.update(_resolve_chunk(chunk, timeout))
        used = budget - ceq.dns_budget_remaining()
        spent += used
        remaining -= used
        index += len(chunk)
        rounds += 1
        out(f"  round {rounds}: domains={len(chunk)} dns_queries={used} quota_left={remaining}", flush=True)
    starved = domains[index:]
    for domain in starved:
        results[domain] = (ceq.MX_UNKNOWN, "script_quota_exhausted")
    return {
        "results": results,
        "dns_queries_spent": spent,
        "domains_resolved": index,
        "domains_starved": len(starved),
        "rounds": rounds,
        "quota_left": remaining,
    }


# ---------------------------------------------------------------- ③ 判定与落库
def verdict_for(plan: dict[str, Any], domain_results: dict[str, tuple[str, str]], *, do_mx: bool) -> tuple[str, str]:
    """单行最终 (mx_status, detail);语法坏的行不出网、不记 fail。"""
    if not plan["syntax_ok"]:
        return ceq.MX_UNKNOWN, "syntax_rejected"
    if not do_mx:
        return ceq.MX_UNKNOWN, "mx_disabled"
    return domain_results.get(plan["domain"], (ceq.MX_UNKNOWN, "not_resolved"))


def report_bucket(plan: dict[str, Any], mx_status: str) -> str:
    if not plan["syntax_ok"]:
        return SYNTAX_BAD
    if mx_status == ceq.MX_OK:
        return MX_OK
    if mx_status == ceq.MX_BAD:
        return MX_FAIL
    return UNKNOWN


def write_verdicts(db: Any, plans: list[dict[str, Any]], domain_results: dict[str, tuple[str, str]],
                   *, do_mx: bool) -> list[tuple[dict[str, Any], str, str]]:
    """按 (contact_id, 邮箱指纹) upsert 进 evidence;不碰 contact_value/verification_status。"""
    written: list[tuple[dict[str, Any], str, str]] = []
    for plan in plans:
        status, detail = verdict_for(plan, domain_results, do_mx=do_mx)
        ceq.cache_quality_result(
            db,
            contact_id=plan["contact_id"],
            kol_pool_id=plan["kol_pool_id"],
            email=plan["email"],
            syntax_ok=plan["syntax_ok"],
            syntax_reason=plan["syntax_reason"],
            mx_status=status,
            role=plan["role"],
            detail=detail,
        )
        written.append((plan, status, detail))
    db.commit()
    return written


# ---------------------------------------------------------------- ④ 报告
def _empty_bucket_counts() -> dict[str, int]:
    return {bucket: 0 for bucket in REPORT_BUCKETS}


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 3) if whole else 0.0


def build_report(written: list[tuple[dict[str, Any], str, str]]) -> dict[str, Any]:
    """总分布 + 按 contact_source 分组(回答「哪条腿的邮箱质量最好」)。"""
    overall = _empty_bucket_counts()
    by_source: dict[str, dict[str, int]] = {}
    fail_domains: dict[str, int] = {}
    unknown_reasons: dict[str, int] = {}
    role_flagged = 0
    for plan, status, detail in written:
        bucket = report_bucket(plan, status)
        overall[bucket] += 1
        source = plan["contact_source"] or "(unset)"
        by_source.setdefault(source, _empty_bucket_counts())[bucket] += 1
        if plan["role"]:
            role_flagged += 1
        if bucket == MX_FAIL:
            fail_domains[plan["domain"]] = fail_domains.get(plan["domain"], 0) + 1
        elif bucket == UNKNOWN:
            unknown_reasons[detail] = unknown_reasons.get(detail, 0) + 1
    total = len(written)
    source_table = {}
    for source, counts in sorted(by_source.items(), key=lambda kv: -sum(kv[1].values())):
        rows = sum(counts.values())
        source_table[source] = {**counts, "rows": rows, "mx_ok_rate": _rate(counts[MX_OK], rows)}
    return {
        "rows_checked": total,
        "distribution": overall,
        "mx_ok_rate": _rate(overall[MX_OK], total),
        "role_flagged": role_flagged,
        "by_source": source_table,
        "mx_fail_domains": sorted(fail_domains.items(), key=lambda kv: -kv[1])[:20],
        "unknown_reasons": unknown_reasons,
    }


def dry_run_report(plans: list[dict[str, Any]], domains: list[str], seeded: dict[str, tuple[str, str]]) -> dict[str, Any]:
    to_query = [d for d in domains if d not in seeded]
    by_source: dict[str, int] = {}
    for plan in plans:
        source = plan["contact_source"] or "(unset)"
        by_source[source] = by_source.get(source, 0) + 1
    return {
        "mode": "dry_run",
        "rows_total": len(plans),
        "syntax_bad_rows": sum(1 for p in plans if not p["syntax_ok"]),
        "unique_domains": len(domains),
        "domains_from_db_cache": len(seeded),
        "domains_needing_dns": len(to_query),
        "worst_case_dns_queries": len(to_query) * QUERIES_PER_DOMAIN,
        "rows_by_source": by_source,
    }


# ---------------------------------------------------------------- CLI
def run(db: Any, *, limit: int | None, quota: int, timeout: float, do_mx: bool) -> dict[str, Any]:
    """实跑一次全池校验并落库;返回报告。"""
    plans = build_plans(fetch_email_rows(db, limit))
    domains = unique_domains(plans)
    seeded = seed_domains_from_db(db, plans) if do_mx else {}
    pending = [d for d in domains if d not in seeded]
    out(f"rows={len(plans)} unique_domains={len(domains)} "
        f"from_db_cache={len(seeded)} needing_dns={len(pending)} quota={quota}", flush=True)
    resolution = resolve_domains(pending, timeout=timeout, quota=quota) if do_mx else {
        "results": {}, "dns_queries_spent": 0, "domains_resolved": 0,
        "domains_starved": 0, "rounds": 0, "quota_left": quota,
    }
    domain_results: dict[str, tuple[str, str]] = {**seeded, **resolution["results"]}
    written = write_verdicts(db, plans, domain_results, do_mx=do_mx)
    report = build_report(written)
    report.update({
        "mode": "live" if do_mx else "syntax_only",
        "unique_domains": len(domains),
        "domains_from_db_cache": len(seeded),
        "domains_resolved_now": resolution["domains_resolved"],
        "domains_starved_by_quota": resolution["domains_starved"],
        "dns_queries_spent": resolution["dns_queries_spent"],
        "dns_rounds": resolution["rounds"],
        "quota": quota,
        "quota_left": resolution["quota_left"],
        "smtp_probe": "never (MX/A only)",
        "note": "verification_status 与 contact_value 均未改动;判定只写 evidence 缓存行",
    })
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="全池邮箱 MX 批验证(域名级,绝不做 SMTP 探测)")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 行(默认全池)")
    parser.add_argument("--quota", type=int, default=HARD_QUOTA,
                        help=f"本次 DNS 查询总上限(默认 {HARD_QUOTA},硬夹,只能调小)")
    parser.add_argument("--timeout", type=float, default=ceq.DNS_TIMEOUT_SECONDS,
                        help=f"单次 DNS 超时秒(默认 {ceq.DNS_TIMEOUT_SECONDS};超时记 unknown 不记 fail)")
    parser.add_argument("--dry-run", action="store_true", help="只列计划,零 DNS、零写库")
    parser.add_argument("--no-mx", action="store_true", help="只跑语法层并落库,零 DNS")
    args = parser.parse_args(argv)

    load_dotenv()
    from app.db.connection import get_conn

    db = get_conn()
    quota = clamp_quota(args.quota)
    if args.dry_run:
        plans = build_plans(fetch_email_rows(db, args.limit))
        domains = unique_domains(plans)
        out_json(dry_run_report(plans, domains, seed_domains_from_db(db, plans)),
                 ensure_ascii=False, indent=2)
        return 0
    report = run(db, limit=args.limit, quota=quota, timeout=float(args.timeout), do_mx=not args.no_mx)
    out_json(report, ensure_ascii=False, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
