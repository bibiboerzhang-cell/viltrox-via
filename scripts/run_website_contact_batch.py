#!/usr/bin/env python3
"""页面抓取腿批跑器:对已有外链(website/link_hub)且 email 仍空的 KOL,
抓 contact 页 / Linktree 类聚合页,正则抽邮箱落 vkpi_kol_pool_contacts + 回填 email。

红线(写死,不接受参数放宽):
  - 仅 GET 公开页面;不绕任何登录/验证;Apify 零调用;LLM 零调用。
  - 单批上限 ≤100(HARD_CAP);--limit 超过即截到 100。
  - 速率 ≥2s/请求(set_fetch_throttle,参数只能调大不能调小)。
  - robots.txt 尊重:每 host 预检一次,Disallow 即跳过该链接;robots 拉不到视为允许。
  - 出网走代理 env:按 scripts/runtime_env.sh 口径,YTDLP_PROXY -> HTTPS_PROXY/HTTP_PROXY。
  - 失败(超时/连接错)每 KOL 最多重试 1 次;绝不触 viltrox_fit_score。

用法:
  .venv/bin/python scripts/run_website_contact_batch.py --dry-run            # 只列目标,零出网
  .venv/bin/python scripts/run_website_contact_batch.py --limit 15          # 真实出网跑 15 个 KOL
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import urllib.robotparser
from pathlib import Path
from typing import Any

from stdout_utils import out, out_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
BACKEND = PROJECT_ROOT / "backend"
HARD_CAP = 100          # 单批 KOL 上限,写死
MIN_INTERVAL_S = 2.0    # 每请求最小间隔,写死下限
LINKS_PER_KOL = 3       # 与 enrich_website_contacts_l1 的 links[:3] 对齐
ROBOTS_UA = "ViltroxContactEnrich"


def load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def apply_proxy_env() -> str:
    """按 runtime_env.sh 口径:YTDLP_PROXY 兜底进 HTTPS_PROXY/HTTP_PROXY,本地回环走 NO_PROXY。"""
    proxy = os.environ.get("YTDLP_PROXY", "")
    if proxy and not os.environ.get("VKPI_DISABLE_LLM_PROXY"):
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            os.environ.setdefault(key, proxy)
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,0.0.0.0,::1")
    return os.environ.get("HTTPS_PROXY", "")


def bootstrap() -> None:
    """main() 入口才做:载 .env + 挂 backend 进 sys.path(import 时不做,保测试 hermetic)。"""
    load_dotenv()
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def fetch_targets(db: Any, limit: int) -> list[dict[str, Any]]:
    """email 仍空且有外链的 KOL,每个带其外链列表(link_hub 优先,与 enrich 消费序一致)。"""
    rows = db.execute(
        """
        SELECT c.kol_pool_id AS kid, c.contact_type AS ctype, c.contact_value AS link
        FROM vkpi_kol_pool_contacts c
        JOIN vkpi_kol_pool p ON p.id = c.kol_pool_id
        WHERE c.contact_type IN ('website','link_hub') AND COALESCE(p.email,'') = ''
        ORDER BY c.kol_pool_id, CASE WHEN c.contact_type='link_hub' THEN 0 ELSE 1 END, c.id
        """
    ).fetchall()
    grouped: dict[int, list[str]] = {}
    for r in rows:
        d = dict(r)
        grouped.setdefault(int(d["kid"]), []).append(str(d["link"]))
    return [{"kol_pool_id": kid, "links": links} for kid, links in list(grouped.items())[:limit]]


class RobotsGate:
    """每 host 缓存一份 robots.txt 判定;拉取失败按业界惯例视为允许。"""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def allow(self, url: str) -> bool:
        host = url.lower().split("//", 1)[-1].split("/", 1)[0]
        if not host:
            return False
        rp = self._cache.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                rp.set_url(f"https://{host}/robots.txt")
                rp.read()
            except Exception as exc:  # robots 拉不到 -> 允许,但记台账不静默
                out(f"  robots fetch failed ({type(exc).__name__}) host={host} -> treat as allow")
                rp = True
            self._cache[host] = rp
        return True if rp is True else bool(rp.can_fetch(ROBOTS_UA, url))


def _classify(result: dict[str, Any], errors: list[str]) -> str:
    """把 enrich 返回 + 抓取错误台账折成单一 outcome 标签。"""
    timeout_hit = any(("timed out" in e.lower()) or ("timeout" in e.lower()) for e in errors)
    status = str(result.get("status") or "")
    if status == "ok" and result.get("email"):
        return "email_found"
    if status == "ok":
        return "contacts_no_email"
    if status == "no_links":
        return "no_links"
    return "timeout" if timeout_hit else "no_email"


def run_one(kid: int, gate: RobotsGate, blocked: list[str]) -> tuple[str, dict[str, Any]]:
    """跑单个 KOL:robots 预检 + 抓取;超时类失败重试 1 次(不多)。"""
    from app.domains.kol.contact_website_scrape import enrich_website_contacts_l1, pop_fetch_errors

    def allow_url(url: str) -> bool:
        ok = gate.allow(url)
        if not ok:
            blocked.append(url)
        return ok

    pop_fetch_errors()  # 清上一 KOL 残留
    result = enrich_website_contacts_l1(kid, allow_url=allow_url)
    outcome = _classify(result, pop_fetch_errors())
    if outcome == "timeout":  # 仅超时类失败重试一次
        result = enrich_website_contacts_l1(kid, allow_url=allow_url)
        retried = _classify(result, pop_fetch_errors())
        outcome = retried if retried != "timeout" else "timeout"
    return outcome, result


def main() -> int:
    parser = argparse.ArgumentParser(description="外链 KOL contact 页/Linktree 邮箱批跑器")
    parser.add_argument("--limit", type=int, default=25, help=f"本批 KOL 数(默认 25,硬上限 {HARD_CAP})")
    parser.add_argument("--dry-run", action="store_true", help="只列目标,零出网")
    parser.add_argument("--min-interval", type=float, default=MIN_INTERVAL_S,
                        help=f"每请求最小间隔秒(默认 {MIN_INTERVAL_S},只能调大)")
    args = parser.parse_args()
    limit = max(1, min(int(args.limit), HARD_CAP))

    bootstrap()
    from app.db.connection import get_conn
    from app.domains.kol.contact_website_scrape import set_fetch_throttle

    db = get_conn()
    targets = fetch_targets(db, limit)
    out(f"targets={len(targets)} (limit={limit}, hard_cap={HARD_CAP}, dry_run={args.dry_run})")
    if args.dry_run:
        for t in targets:
            out(f"  kol={t['kol_pool_id']} links={t['links'][:LINKS_PER_KOL]}")
        out_json({"mode": "dry_run", "targets": len(targets)})
        return 0

    proxy = apply_proxy_env()
    socket.setdefaulttimeout(10)  # robots.txt 拉取(robotparser 无 timeout 参数)也受此限,防挂死
    out(f"proxy={'on' if proxy else 'off'}; throttle={max(MIN_INTERVAL_S, args.min_interval)}s/request", flush=True)
    set_fetch_throttle(max(MIN_INTERVAL_S, float(args.min_interval)))
    gate = RobotsGate()
    counts: dict[str, int] = {}
    new_emails: list[dict[str, Any]] = []
    blocked: list[str] = []
    for i, t in enumerate(targets, 1):
        kid = int(t["kol_pool_id"])
        outcome, result = run_one(kid, gate, blocked)
        counts[outcome] = counts.get(outcome, 0) + 1
        email = str(result.get("email") or "")
        if outcome == "email_found":
            new_emails.append({"kol_pool_id": kid, "email": email})
        out(f"[{i}/{len(targets)}] kol={kid} outcome={outcome}"
            + (f" email={email}" if email else "") + f" found={result.get('found', 0)}", flush=True)
    summary = {
        "mode": "live", "attempted": len(targets), "counts": counts,
        "new_emails": len(new_emails), "emails": new_emails,
        "robots_blocked_urls": blocked[:20],
        "hit_rate": round(len(new_emails) / len(targets), 3) if targets else 0.0,
    }
    out_json(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
