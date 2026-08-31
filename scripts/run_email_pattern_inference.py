#!/usr/bin/env python3
"""邮箱模式推断批跑器:对「有个人域名外链、但无任何实抓邮箱」的 KOL,
按行业惯例组合候选地址,用 MX 闸收敛,出分布报告。

**产出是线索不是事实。** 只做 MX(域名收得了信),不做 SMTP RCPT 探测
(灰色行为,禁),所以没有任何「该地址存在」的证据 —— 候选恒带
contact_source='pattern_inferred' / confidence=0.35 / usable=False。

红线(写死,不接受参数放宽):
  - 零 Apify、零 LLM。唯一出网是 DNS MX 查询。
  - DNS 单轮上限 ≤500(HARD_DNS_CAP);实际再被 contact_email_quality 的
    MAX_DNS_BUDGET(200/轮)夹一次,报告里两个数都列。
  - --dry-run 默认开;--apply 目前被**主动挡住**(schema 没有能容纳推断值的
    落点,落进去会污染 contactability_score,详见 contact_email_inference 头部)。
  - 不触 viltrox_fit_score / rule_v0;报告不铺无关明文。

用法:
  .venv/bin/python scripts/run_email_pattern_inference.py --limit 60
  .venv/bin/python scripts/run_email_pattern_inference.py --limit 60 --out /tmp/x.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from stdout_utils import out, out_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
BACKEND = PROJECT_ROOT / "backend"
HARD_DOMAIN_CAP = 300   # 单批域名上限,写死
HARD_DNS_CAP = 500      # 单批 DNS 查询上限,写死
SAMPLE_ROWS = 12        # 报告里逐域抽样展示的条数


def load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def bootstrap() -> None:
    """main() 入口才做:载 .env + 挂 backend 进 sys.path(保测试 hermetic)。"""
    load_dotenv()
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def _print_head(summary: dict[str, Any]) -> None:
    mx = summary["mx_distribution"]
    out(f"domains_examined={summary['domains_examined']}")
    out(f"  mx_ok={mx.get('mx_ok', 0)} mx_unknown={mx.get('mx_unknown', 0)} mx_bad={mx.get('mx_bad', 0)}")
    out(f"  domains_with_candidates={summary['domains_with_candidates']}"
        f" candidates_total={summary['candidates_total']}")
    out(f"  dns_budget requested={summary['dns_budget_requested']}"
        f" effective={summary['dns_budget_effective']}"
        f" remaining={summary['dns_budget_remaining']}")


def _print_patterns(summary: dict[str, Any]) -> None:
    dist = summary["pattern_distribution"]
    if not dist:
        out("  pattern_distribution: (空)")
        return
    out("  pattern_distribution (按 local 模式):")
    for pattern, n in sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])):
        out(f"    {pattern:<22} {n}")


def _print_samples(summary: dict[str, Any]) -> None:
    hits = [r for r in summary["results"] if r["candidates"]][:SAMPLE_ROWS]
    if not hits:
        return
    out(f"  样例(前 {len(hits)} 个出候选的域,只列首选):")
    for row in hits:
        top = row["candidates"][0]
        out(f"    kol={row['kol_pool_id']:<6} {row['domain']:<32}"
            f" n={len(row['candidates']):<3} top={top['email']} ({top['pattern']})")


def _print_unknown(summary: dict[str, Any]) -> None:
    unknown = [r["domain"] for r in summary["results"] if r["mx_status"] == "mx_unknown"]
    bad = [r["domain"] for r in summary["results"] if r["mx_status"] == "mx_bad"]
    if unknown:
        out(f"  mx_unknown(不出候选,留人工):{unknown[:10]}")
    if bad:
        out(f"  mx_bad(域名收不了信,判死):{bad[:10]}")


def _write_out(path: str, summary: dict[str, Any], plan: list[dict[str, Any]]) -> None:
    payload = {
        "summary": {k: v for k, v in summary.items() if k != "results"},
        "results": summary["results"],
        "persist_plan": plan,
        "honesty_note": "pattern_inferred = 线索不是事实;未做 SMTP 探测;usable 恒 False",
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out(f"  已写出 {path}({len(plan)} 条落库预案,本轮未执行任何写入)")


def _refuse_apply() -> int:
    out("--apply 被挡住:vkpi_kol_pool_contacts 现有 schema 没有能容纳推断值的落点。")
    out("  含 'email' 的 contact_type 会被 contact_system._normalize_channel 归并进")
    out("  email 渠道(权重 55),refresh_contact_system_columns 据此重算并写回")
    out("  contactability_score —— 猜的地址会冒充「能邮件触达」。换个 contact_type")
    out("  也仍吃 DEFAULT_CHANNEL_WEIGHT=3.0。verification_status 被 CHECK 锁死在")
    out("  5 个枚举值,没有一个表示 inferred;扩枚举 = DDL,本轮禁 DDL。")
    out("  先加落点(迁移),再谈落库。当前用 --out 拿 persist_plan 交给下一刀。")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KOL 邮箱模式推断(MX 收敛,默认 dry-run)")
    parser.add_argument("--limit", type=int, default=60,
                        help=f"本批**域名**数(默认 60,硬上限 {HARD_DOMAIN_CAP})")
    parser.add_argument("--dns-budget", type=int, default=HARD_DNS_CAP,
                        help=f"本轮 DNS 查询上限(硬夹 ≤{HARD_DNS_CAP},再被 ceq 夹到 200)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="只推断不落库(默认,且当前是唯一可用模式)")
    parser.add_argument("--apply", action="store_true", help="落库(当前被主动挡住,见输出说明)")
    parser.add_argument("--out", type=str, default="", help="把完整结果 + 落库预案写成 JSON")
    args = parser.parse_args(argv)

    if args.apply:
        return _refuse_apply()

    limit = max(1, min(int(args.limit), HARD_DOMAIN_CAP))
    budget = max(1, min(int(args.dns_budget), HARD_DNS_CAP))
    bootstrap()
    from app.db.connection import get_conn
    from app.domains.kol import contact_email_inference as cei

    db = get_conn()
    targets = cei.select_inference_targets(db, limit)
    out(f"targets={len(targets)} 个域名 (limit={limit}, dns_budget={budget}, mode=dry_run)")
    if not targets:
        out_json({"mode": "dry_run", "targets": 0})
        return 0

    summary = cei.infer_for_targets(targets, dns_budget=budget)
    _print_head(summary)
    _print_patterns(summary)
    _print_samples(summary)
    _print_unknown(summary)
    plan = cei.build_persist_plan(summary)
    if args.out:
        _write_out(args.out, summary, plan)
    out_json({
        "mode": "dry_run",
        "domains_examined": summary["domains_examined"],
        "mx_distribution": summary["mx_distribution"],
        "domains_with_candidates": summary["domains_with_candidates"],
        "candidates_total": summary["candidates_total"],
        "pattern_distribution": summary["pattern_distribution"],
        "persist_plan_rows": len(plan),
        "wrote_to_db": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
