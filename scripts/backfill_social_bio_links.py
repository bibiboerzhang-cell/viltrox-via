#!/usr/bin/env python3
"""IG/TikTok/YouTube 结构化外链回填:把 raw 里躺着的 bio 外链落进 vkpi_kol_pool_contacts。

背景(已实测坐实):L0 提取链 extract_contacts_multi_source 的外链解析只吃「文本
blob」(bio/描述),对平台的**结构化外链字段**完全瞎:
  - instagram:profile.items[*].externalUrl —— 346 条,几乎全部不在 contacts;
  - tiktok:  profile.items[*].authorMeta.bioLink —— 只在 authorMeta 里,
             且常写成无 scheme 的裸域名(www.foo.com),文本扫描也够不到;
  - youtube: profile.items[*].snippet.description(+ brandingSettings.channel
             .description)—— 文本路径既有链已基本开采,本脚本只捡漏。
而页面抓取腿 scripts/run_website_contact_batch.py 的取数是
  WHERE c.contact_type IN ('website','link_hub') AND COALESCE(p.email,'')=''
—— 只认 contacts 表。外链不落表,页面腿就永远看不见它们。本脚本把这些外链
落进 contacts,页面腿下一批自动接上。

做三件事(零 Apify / 零 LLM / 零出网,纯读本地已抓回 raw + 写 contacts):
  ① 按平台键路径取结构化外链,补齐 scheme,去 CDN/缩略图垃圾域;
  ② 分类:聚合页(_LINK_HUBS 口径,linktr.ee/beacons.ai/stan.store/...)→
     contact_type='link_hub';其余自有域名 → 'website';**纯社交跳转链接**
     (instagram/tiktok/youtube/twitter/x/facebook... 见 _SOCIAL_HOSTS)不写 ——
     它们不是联系页,写了只会让页面腿白跑;
  ③ 走既有 ingest_contact 落库(与 L0 队列执行体同一条路):contact_source=
     'raw_bio_link',置信度按同表既有口径(link_hub 0.5 / website 0.45),
     verification_status='observed',consent_basis 与既有 raw_bio_scan 外链行一致。

幂等:同一 KOL 下、同一「host+path」(忽略 scheme/www/query)的 website 或
link_hub 行已存在即 SKIP;ingest_contact 内部还有一层 normalized 去重兜底。
重复跑不会产生第二行。

每 KOL 写入条数上限 --max-links(默认 3)= 页面腿 LINKS_PER_KOL 的消费上限,
hub 优先。写超过页面腿会读的条数没有收益,只会把目标池灌成噪音。

红线:不碰 vkpi_kol_pool.email、不碰 outreach、不触 viltrox_fit_score/rule_v0;
只写 vkpi_kol_pool_contacts 的 website/link_hub 行与其来源观测。

用法:
  .venv/bin/python scripts/backfill_social_bio_links.py                # 默认 --dry-run
  .venv/bin/python scripts/backfill_social_bio_links.py --apply        # 写本地库
  .venv/bin/python scripts/backfill_social_bio_links.py --platform tiktok --limit 50
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

# 分类口径唯一真源:直接复用 L0 提取链的三张清单,禁止在本脚本里另抄一份(会漂)。
from app.domains.kol.business_contact_extract import (  # noqa: E402
    _CDN_JUNK,
    _LINK_HUBS,
    _SOCIAL_HOSTS,
)
from app.db.connection import get_conn  # noqa: E402
from app.domains.kol.contact_acquisition_queue import _safe_profile_url  # noqa: E402
from app.domains.kol.contact_ingest import ContactValidationError, ingest_contact  # noqa: E402

SOURCE_RAW_BIO_LINK = "raw_bio_link"
# 与同表既有 raw_bio_scan 外链行完全一致的口径(本地库实测:link_hub 0.5 / website 0.45,
# is_public_declared=false / verification_status=observed)。
CONFIDENCE = {"link_hub": 0.5, "website": 0.45}
CONSENT_BASIS = "legitimate_interest_public_business"
DEFAULT_MAX_LINKS = 3  # 对齐 run_website_contact_batch.LINKS_PER_KOL
PLATFORMS = ("instagram", "tiktok", "youtube")

_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_TRAILING_JUNK = ".,;:!?)]}'\"​‌‍、。"


# ---------------------------------------------------------------- 纯函数区 ----

def normalize_link(value: Any) -> str:
    """裸值 -> 可用 http(s) URL;补 scheme(TikTok bioLink 常是 www.foo.com),
    削尾部标点。非 http(s)(mailto:/tel:/intent://)与不像域名的串一律返回 ''。"""
    text = str(value or "").strip().strip(_TRAILING_JUNK)
    if not text or " " in text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    elif not _SCHEME_RE.match(text):
        head = text.split("/", 1)[0]
        if "." not in head or head.startswith(".") or head.endswith("."):
            return ""
        text = "https://" + text
    low = text.lower()
    if not low.startswith(("http://", "https://")):
        return ""
    return text


def link_host(url: str) -> str:
    """URL -> 小写 host(去 www. 前缀、去端口)。取不到返回 ''。"""
    rest = str(url or "").split("//", 1)[-1]
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.rsplit("@", 1)[-1].split(":", 1)[0].strip().strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else ""


def _host_matches(host: str, needle: str) -> bool:
    """精确 host 匹配(== 或子域后缀)。不能用 substring:jurjax.com 含 'x.com'。"""
    return host == needle or host.endswith("." + needle)


def classify_link(url: str) -> tuple[str, str]:
    """外链 -> (contact_type, 排除原因)。contact_type 为 '' 表示不写。

    'link_hub'  聚合页(Linktree 类),页面腿邮箱产出率最高;
    'website'   自有域名 / 其他联系页;
    ''          排除:bad_url / cdn_junk / social_redirect。
    """
    host = link_host(url)
    if not host:
        return "", "bad_url"
    if any(junk in host for junk in _CDN_JUNK):
        return "", "cdn_junk"
    if any(_host_matches(host, social) for social in _SOCIAL_HOSTS):
        return "", "social_redirect"
    if any(_host_matches(host, hub) for hub in _LINK_HUBS):
        return "link_hub", ""
    return "website", ""


def dedupe_key(url: str) -> str:
    """幂等键:host + path,忽略 scheme / www / query / 尾斜杠。
    既有行大多是 http:// 或带 ?ref= 的历史值,只比 contact_value 会重复插。"""
    host = link_host(url)
    if not host:
        return ""
    rest = str(url or "").split("//", 1)[-1]
    path = rest.split("/", 1)[1] if "/" in rest else ""
    path = path.split("?", 1)[0].split("#", 1)[0].rstrip("/").lower()
    return f"{host}/{path}" if path else host


def _profile_container(raw: Any) -> tuple[dict[str, Any], str]:
    """与 extract_contacts_multi_source 同一口径:raw['profile'] 优先,否则 raw 本身。"""
    if not isinstance(raw, dict):
        return {}, "raw_platform_data"
    nested = raw.get("profile")
    if isinstance(nested, dict) and nested:
        return nested, "profile"
    return raw, "raw_platform_data"


def _items(profile: dict[str, Any]) -> list[dict[str, Any]]:
    items = profile.get("items")
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict)]


def _tiktok_bio_link(item: dict[str, Any]) -> str:
    """authorMeta.bioLink:实测是裸字符串,但历史 actor 版本给过 {'link': ...},两种都吃。"""
    meta = item.get("authorMeta")
    if not isinstance(meta, dict):
        return ""
    value = meta.get("bioLink")
    if isinstance(value, dict):
        for key in ("link", "url", "bioLink"):
            if isinstance(value.get(key), str):
                return value[key]
        return ""
    return value if isinstance(value, str) else ""


def _youtube_descriptions(item: dict[str, Any]) -> list[tuple[str, str]]:
    """YouTube 无结构化外链字段,链接只在频道简介里;两处简介同源,取到即止。"""
    snippet = item.get("snippet")
    if isinstance(snippet, dict) and isinstance(snippet.get("description"), str):
        return [("snippet.description", snippet["description"])]
    branding = item.get("brandingSettings")
    channel = branding.get("channel") if isinstance(branding, dict) else None
    if isinstance(channel, dict) and isinstance(channel.get("description"), str):
        return [("brandingSettings.channel.description", channel["description"])]
    return []


def extract_bio_links(raw: Any, platform: str) -> list[dict[str, str]]:
    """按平台键路径取结构化外链(纯函数、零网络)。返回 [{contact_value, source_field}],
    同 KOL 内按 dedupe_key 去重、保留首次出现顺序。raw 结构异常一律返回 [],不炸。"""
    key = str(platform or "").strip().lower()
    profile, prefix = _profile_container(raw)
    if not profile:
        return []
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def _take(value: Any, field: str) -> None:
        url = normalize_link(value)
        marker = dedupe_key(url)
        if not url or not marker or marker in seen:
            return
        seen.add(marker)
        found.append({"contact_value": url, "source_field": field})

    for idx, item in enumerate(_items(profile)):
        base = f"{prefix}.items.{idx}"
        if key in {"instagram", "ig"}:
            _take(item.get("externalUrl"), f"{base}.externalUrl")
        elif key == "tiktok":
            _take(_tiktok_bio_link(item), f"{base}.authorMeta.bioLink")
        elif key == "youtube":
            for field, text in _youtube_descriptions(item):
                for match in _URL_IN_TEXT_RE.findall(text):
                    _take(match, f"{base}.{field}")
    return found


def plan_kol(
    raw: Any, platform: str, existing_keys: set[str], *, max_links: int = DEFAULT_MAX_LINKS
) -> dict[str, list[dict[str, str]]]:
    """单 KOL 纯计算:抽外链 -> 分类 -> 分拣。不碰库,可单测。

    返回 to_insert(hub 优先、截到 max_links)/ skipped(已在表)/ excluded(带原因)/
    overflow(被 max_links 截掉的)。
    """
    candidates: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    for link in extract_bio_links(raw, platform):
        contact_type, reason = classify_link(link["contact_value"])
        if not contact_type:
            excluded.append({**link, "reason": reason})
            continue
        row = {**link, "contact_type": contact_type}
        if dedupe_key(link["contact_value"]) in existing_keys:
            skipped.append(row)
        else:
            candidates.append(row)
    # hub 优先(页面腿邮箱产出率最高),同类保持发现顺序。
    ordered = sorted(candidates, key=lambda c: 0 if c["contact_type"] == "link_hub" else 1)
    cap = max(0, int(max_links))
    return {
        "to_insert": ordered[:cap],
        "overflow": ordered[cap:],
        "skipped": skipped,
        "excluded": excluded,
    }


# ------------------------------------------------------------------ 库交互 ----

def _row_dict(row: Any) -> dict[str, Any]:
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {k: row[k] for k in row.keys()}


def _load_existing_links(db: Any) -> dict[int, set[str]]:
    """每 KOL 已有的 website/link_hub 外链指纹(两类合一个集合:同一个 URL 不该
    因为分类口径变化被写第二遍)。"""
    by_kol: dict[int, set[str]] = {}
    rows = db.execute(
        "SELECT kol_pool_id, contact_value, normalized_value FROM vkpi_kol_pool_contacts "
        "WHERE contact_type IN ('website','link_hub')"
    ).fetchall()
    for raw_row in rows:
        row = _row_dict(raw_row)
        bucket = by_kol.setdefault(int(row["kol_pool_id"]), set())
        for column in ("contact_value", "normalized_value"):
            marker = dedupe_key(normalize_link(row.get(column)))
            if marker:
                bucket.add(marker)
    return by_kol


def _pool_rows(db: Any, platforms: tuple[str, ...], limit: int | None) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in platforms)
    sql = (
        "SELECT id, platform, profile_url, raw_platform_data FROM vkpi_kol_pool "
        f"WHERE lower(COALESCE(platform,'')) IN ({placeholders}) "
        "AND raw_platform_data IS NOT NULL AND raw_platform_data <> '' ORDER BY id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [_row_dict(r) for r in db.execute(sql, tuple(platforms)).fetchall()]


def _parse_raw(text: Any) -> dict[str, Any]:
    try:
        raw = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _target_pool(db: Any) -> tuple[int, int]:
    """页面腿(run_website_contact_batch.fetch_targets)的目标池口径,一字不改。"""
    row = _row_dict(db.execute(
        "SELECT COUNT(DISTINCT c.kol_pool_id) AS kols, COUNT(*) AS links "
        "FROM vkpi_kol_pool_contacts c JOIN vkpi_kol_pool p ON p.id = c.kol_pool_id "
        "WHERE c.contact_type IN ('website','link_hub') AND COALESCE(p.email,'') = ''"
    ).fetchone())
    return int(row["kols"]), int(row["links"])


def _ingest_one(db: Any, pool: dict[str, Any], row: dict[str, str]) -> bool:
    """既有落库口径:ingest_contact(与 L0 队列执行体同一条路,自带 normalized 去重
    + 来源观测 + 置信度 max-merge,绝不覆盖更高置信来源)。"""
    contact_type = row["contact_type"]
    try:
        ingest_contact(
            kol_pool_id=int(pool["id"]),
            contact_type=contact_type,
            contact_value=row["contact_value"],
            source_type=SOURCE_RAW_BIO_LINK,
            source_url=_safe_profile_url(pool.get("profile_url")),
            source_field=row["source_field"],
            evidence_text=row["contact_value"],
            confidence=CONFIDENCE[contact_type],
            is_public_declared=False,
            verification_status="observed",
            consent_basis=CONSENT_BASIS,
            conn=db,
        )
        return True
    except (ContactValidationError, TypeError, ValueError) as exc:
        out(f"  REJECT kol={pool['id']} {contact_type} '{row['contact_value']}':"
            f" ingest 校验拒绝({type(exc).__name__}: {exc})")
        return False


# ------------------------------------------------------------------- 台账 ----

def _bump(counts: dict[str, dict[str, int]], platform: str, key: str, n: int = 1) -> None:
    counts.setdefault(platform, {})[key] = counts.setdefault(platform, {}).get(key, 0) + n


def _report(pool_id: int, platform: str, plan: dict[str, list[dict[str, str]]], *, apply: bool) -> None:
    verb = "写入" if apply else "拟写入"
    for row in plan["to_insert"]:
        out(f"  NEW  kol={pool_id} [{platform}] {row['contact_type']} '{row['contact_value']}'"
            f" src={SOURCE_RAW_BIO_LINK} conf={CONFIDENCE[row['contact_type']]}"
            f" field={row['source_field']} | before: contacts 无此外链 | after: {verb}(observed)")
    for row in plan["skipped"]:
        out(f"  SKIP kol={pool_id} [{platform}] '{row['contact_value']}' 已在 contacts(幂等跳过)")
    for row in plan["excluded"]:
        out(f"  DROP kol={pool_id} [{platform}] '{row['contact_value']}' 排除:{row['reason']}")
    for row in plan["overflow"]:
        out(f"  CAP  kol={pool_id} [{platform}] '{row['contact_value']}' 超每 KOL 上限,不写")


def _summary_line(platform: str, c: dict[str, int]) -> str:
    return (f"  {platform:<10} 待写 {c.get('pending', 0):>4}"
            f"(hub {c.get('pending_link_hub', 0):>3} / site {c.get('pending_website', 0):>3})"
            f" | 跳过已在表 {c.get('skipped', 0):>4}"
            f" | 纯社交排除 {c.get('social_redirect', 0):>4}"
            f" | 其他排除 {c.get('bad_url', 0) + c.get('cdn_junk', 0):>3}"
            f" | 超限不写 {c.get('overflow', 0):>3}"
            f" | 命中 KOL {c.get('kols_hit', 0):>4}")


def _run(apply: bool, platforms: tuple[str, ...], limit: int | None, max_links: int) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mode = "APPLY(写本地库)" if apply else "DRY-RUN(只报告,不写)"
    db = get_conn()
    out(f"== IG/TikTok/YouTube 外链回填 == 模式:{mode} @ {now}")
    out(f"平台:{','.join(platforms)};每 KOL 上限 {max_links} 条(hub 优先)")

    before_kols, before_links = _target_pool(db)
    out(f"页面腿目标池 before:{before_kols} 个 KOL / {before_links} 条外链")

    existing = _load_existing_links(db)
    rows = _pool_rows(db, platforms, limit)
    out(f"扫描范围:{len(rows)} 个有 raw 的 KOL;contacts 已有外链覆盖 {len(existing)} 个 KOL\n")

    counts: dict[str, dict[str, int]] = {}
    ingested = rejected = 0
    for pool in rows:
        platform = str(pool.get("platform") or "").strip().lower()
        plan = plan_kol(
            _parse_raw(pool.get("raw_platform_data")), platform,
            existing.get(int(pool["id"]), set()), max_links=max_links,
        )
        if not (plan["to_insert"] or plan["skipped"] or plan["excluded"] or plan["overflow"]):
            continue
        if plan["to_insert"]:
            _bump(counts, platform, "kols_hit")
        _bump(counts, platform, "pending", len(plan["to_insert"]))
        for row in plan["to_insert"]:
            _bump(counts, platform, f"pending_{row['contact_type']}")
        _bump(counts, platform, "skipped", len(plan["skipped"]))
        _bump(counts, platform, "overflow", len(plan["overflow"]))
        for row in plan["excluded"]:
            _bump(counts, platform, row["reason"])
        _report(int(pool["id"]), platform, plan, apply=apply)
        if not apply:
            continue
        for row in plan["to_insert"]:
            if _ingest_one(db, pool, row):
                ingested += 1
            else:
                rejected += 1
    if apply:
        db.commit()

    out("\n汇总(按平台):")
    for platform in sorted(counts):
        out(_summary_line(platform, counts[platform]))
    total_pending = sum(c.get("pending", 0) for c in counts.values())
    total_kols = sum(c.get("kols_hit", 0) for c in counts.values())
    out(f"  {'合计':<10} 待写 {total_pending} 条,涉 {total_kols} 个 KOL")

    if apply:
        after_kols, after_links = _target_pool(db)
        out(f"\n已写本地库:ingest 成功 {ingested} 条 / 校验拒绝 {rejected} 条。")
        out(f"页面腿目标池 after:{after_kols} 个 KOL / {after_links} 条外链"
            f"(新增 +{after_kols - before_kols} 个 KOL / +{after_links - before_links} 条外链)")
    else:
        out("\nDRY-RUN:未写任何数据。加 --apply 落库。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="写本地库(默认只 dry-run 报告)")
    parser.add_argument("--dry-run", action="store_true", help="显式 dry-run(与默认等价)")
    parser.add_argument("--limit", type=int, default=None, help="只扫前 N 个 KOL(调试用)")
    parser.add_argument("--platform", default="", help=f"只跑单平台({'/'.join(PLATFORMS)})")
    parser.add_argument("--max-links", type=int, default=DEFAULT_MAX_LINKS,
                        help=f"每 KOL 最多写几条外链(默认 {DEFAULT_MAX_LINKS},对齐页面腿消费上限)")
    args = parser.parse_args()
    chosen = str(args.platform or "").strip().lower()
    if chosen and chosen not in PLATFORMS:
        out(f"未知平台 '{chosen}';可选:{'/'.join(PLATFORMS)}")
        return 2
    platforms = (chosen,) if chosen else PLATFORMS
    apply = bool(args.apply) and not bool(args.dry_run)
    return _run(apply, platforms, args.limit, int(args.max_links))


if __name__ == "__main__":
    raise SystemExit(main())
