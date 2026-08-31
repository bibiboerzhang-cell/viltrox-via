#!/usr/bin/env python3
"""把误判成 'website' 的聚合页外链改判为 'link_hub'(一次性纠偏,幂等可重跑)。

为什么要跑:
  页面腿 scripts/run_website_contact_batch.py 取每个 KOL 的外链时按
      ORDER BY CASE WHEN contact_type='link_hub' THEN 0 ELSE 1 END, id
  且只抓前 3 条。聚合页(Linktree 类)是邮箱产出率最高的一类页面,一旦被记成
  'website',它就排在随机个人站后面,极可能连抓都抓不到 —— 名单漏一个域名的代价
  不是"少一条线索",是"最好的线索被挤出窗口"。

  2026-08-31 本地库实测:contact_type='website' 里有 49 条 host 其实是聚合页
  (bio.site 一家 26 条,其余 bio.link/link.me/liinks.co/taplink.cc/hoo.be/dott.bio/
  superprofile.bio/linkfly.to/linkgenie.co/lnk.bio/msha.ke 等)。根因是
  business_contact_extract._LINK_HUBS 名单不全,本刀与名单补全同批。

判定口径:
  唯一真源 = business_contact_extract._LINK_HUBS + _url_host + _host_matches。
  本脚本禁止另抄一份域名表或另写一套 host 剥法(会与抽取腿漂移)。
  channel / normalized_value 走 contact_ingest.normalize_contact('link_hub', url),
  与新写入行完全同一条规范化路径。confidence 按同表既有口径 website 0.45 -> link_hub 0.5。

安全边界:
  只 UPDATE vkpi_kol_pool_contacts 的 contact_type / channel / normalized_value / confidence,
  行 id 不变(vkpi_kol_contact_evidence 的外键因此不受影响)。不新增行、不删行、
  不碰 vkpi_kol_pool、绝不触 viltrox_fit_score。两个唯一约束都先探后写:
    - vkpi_kol_pool_contacts_kol_pool_id_contact_type_contact_val_key (kol,type,value)
    - uq_vkpi_kol_contact_normalized (kol,channel,normalized_value)
  撞上任一约束的行报 SKIP 并原样保留,不做合并/删除(合并是另一把刀的活)。

用法:
  .venv/bin/python scripts/reclassify_link_hub_contacts.py            # 默认 --dry-run
  .venv/bin/python scripts/reclassify_link_hub_contacts.py --apply    # 写本地库
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:  # 直跑 / 被测试按路径 import 都要能拿到 stdout_utils
    sys.path.insert(0, str(SCRIPTS_DIR))

from stdout_utils import out  # noqa: E402

# prod 探针:脚本被拷到 /tmp 时用 VKPI_ROOT 指回线上仓库根(含 backend/ 与 .env)。
PROJECT_ROOT = Path(os.environ.get("VKPI_ROOT") or SCRIPTS_DIR.parent)
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

# 分类口径唯一真源:直接复用 L0 提取链的名单与 host 谓词,禁止在本脚本里另抄一份(会漂)。
from app.domains.kol.business_contact_extract import (  # noqa: E402
    _LINK_HUBS,
    _host_matches,
    _url_host,
)
from app.db.connection import get_conn  # noqa: E402
from app.domains.kol.contact_ingest import ContactValidationError, normalize_contact  # noqa: E402

TARGET_TYPE = "link_hub"
FROM_TYPE = "website"
# 与同表既有行一致(本地库实测:link_hub 0.5 / website 0.45)。
WEBSITE_CONFIDENCE = 0.45
LINK_HUB_CONFIDENCE = 0.5


def is_link_hub_url(url: str) -> bool:
    """外链是否落在聚合页名单上(host 级匹配,不是 substring)。

    substring 会把 example.com/linktr.ee 这种 path 误判成聚合页,
    所以这里只认 host == hub 或 host 以 '.hub' 结尾。
    """
    host = _url_host(url)
    if not host:
        return False
    return any(_host_matches(host, hub) for hub in _LINK_HUBS)


def _rows_to_fix(db: Any) -> list[dict[str, Any]]:
    """当前记成 'website' 但 host 其实是聚合页的行。"""
    rows = db.execute(
        "SELECT id, kol_pool_id, contact_value, contact_source, channel, "
        "normalized_value, confidence FROM vkpi_kol_pool_contacts "
        "WHERE contact_type=?",
        (FROM_TYPE,),
    ).fetchall()
    return [dict(r) for r in rows if is_link_hub_url(str(dict(r).get("contact_value") or ""))]


def _existing_link_hub_keys(db: Any) -> tuple[set[tuple[int, str]], set[tuple[int, str]]]:
    """已存在的 link_hub 行指纹,用来提前避开两个唯一约束。

    返回 (按 contact_value 的键集, 按 normalized_value 的键集)。
    """
    rows = db.execute(
        "SELECT kol_pool_id, contact_value, channel, normalized_value "
        "FROM vkpi_kol_pool_contacts WHERE contact_type=?",
        (TARGET_TYPE,),
    ).fetchall()
    by_value: set[tuple[int, str]] = set()
    by_normalized: set[tuple[int, str]] = set()
    for raw in rows:
        row = dict(raw)
        pool_id = int(row.get("kol_pool_id") or 0)
        by_value.add((pool_id, str(row.get("contact_value") or "")))
        if row.get("channel") == TARGET_TYPE and row.get("normalized_value"):
            by_normalized.add((pool_id, str(row["normalized_value"])))
    return by_value, by_normalized


def plan_row(
    row: dict[str, Any],
    by_value: set[tuple[int, str]],
    by_normalized: set[tuple[int, str]],
) -> dict[str, Any]:
    """单行 -> 改判计划。action 为 'fix' / 'skip',skip 必带 reason。"""
    pool_id = int(row.get("kol_pool_id") or 0)
    value = str(row.get("contact_value") or "")
    if (pool_id, value) in by_value:
        return {"action": "skip", "reason": "同 KOL 已有同 URL 的 link_hub 行(唯一约束 type+value)"}
    try:
        normalized = normalize_contact(TARGET_TYPE, value)
    except (ContactValidationError, TypeError, ValueError) as exc:
        return {"action": "skip", "reason": f"规范化拒绝({type(exc).__name__}: {exc})"}
    if (pool_id, normalized.normalized_value) in by_normalized:
        return {"action": "skip", "reason": "同 KOL 已有同 normalized 的 link_hub 行(唯一约束 kol+channel+normalized)"}
    current = row.get("confidence")
    # 只把 website 默认档(0.45 / 空)抬到 link_hub 档;人工调过的更高置信度不覆盖。
    bump = current is None or float(current) <= WEBSITE_CONFIDENCE
    return {
        "action": "fix",
        "channel": normalized.channel,
        "normalized_value": normalized.normalized_value,
        "confidence": LINK_HUB_CONFIDENCE if bump else float(current),
    }


def _apply_row(db: Any, row_id: int, plan: dict[str, Any]) -> None:
    db.execute(
        "UPDATE vkpi_kol_pool_contacts SET contact_type=?, channel=?, normalized_value=?, "
        "confidence=? WHERE id=?",
        (
            TARGET_TYPE,
            plan["channel"],
            plan["normalized_value"],
            plan["confidence"],
            int(row_id),
        ),
    )


def _run(apply: bool) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mode = "APPLY(写本地库)" if apply else "DRY-RUN(只报告,不写)"
    db = get_conn()
    out(f"== 聚合页外链改判 website -> link_hub == 模式:{mode} @ {now}")
    out(f"名单:_LINK_HUBS 共 {len(_LINK_HUBS)} 个域名(唯一真源 business_contact_extract)")

    rows = _rows_to_fix(db)
    by_value, by_normalized = _existing_link_hub_keys(db)
    out(f"命中:contact_type='{FROM_TYPE}' 中 host 属聚合页的行 {len(rows)} 条\n")

    fixed = skipped = 0
    by_host: dict[str, int] = {}
    for row in rows:
        plan = plan_row(row, by_value, by_normalized)
        value = str(row.get("contact_value") or "")
        host = _url_host(value)
        if plan["action"] == "skip":
            skipped += 1
            out(f"  SKIP id={row['id']} kol={row['kol_pool_id']} '{value}' -> {plan['reason']}")
            continue
        verb = "改判" if apply else "拟改判"
        out(
            f"  FIX  id={row['id']} kol={row['kol_pool_id']} '{value}'"
            f" | before: type={FROM_TYPE} channel={row.get('channel') or '-'}"
            f" conf={row.get('confidence')}"
            f" | after: type={TARGET_TYPE} channel={plan['channel']}"
            f" conf={plan['confidence']}({verb})"
        )
        if apply:
            _apply_row(db, int(row["id"]), plan)
        # 同批内也要占位,免得同 KOL 两条同 URL 的 website 行互撞唯一约束。
        by_value.add((int(row["kol_pool_id"]), value))
        by_normalized.add((int(row["kol_pool_id"]), plan["normalized_value"]))
        by_host[host] = by_host.get(host, 0) + 1
        fixed += 1

    if apply:
        db.commit()

    out("")
    for host, count in sorted(by_host.items(), key=lambda kv: (-kv[1], kv[0])):
        out(f"  {host:<20} {count:>3}")
    out(f"\n合计:{'已改判' if apply else '待改判'} {fixed} 条;跳过 {skipped} 条")
    if fixed and apply:
        out("提示:contact_system 的渠道权重 link_hub 12.0 > website 10.0,"
            "受影响 KOL 的 contactability 分需要下一轮 refresh_contactability 才会跟上。")
    if not apply:
        out("这是 DRY-RUN,库未改。确认无误后加 --apply 重跑。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真正写库(默认只 dry-run 报告)")
    args = parser.parse_args()
    return _run(apply=bool(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
