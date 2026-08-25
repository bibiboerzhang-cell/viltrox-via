#!/usr/bin/env python3
"""存量失效头像重抓驱动:先分类,再对「已失效」走既有档案深爬队列重取新头像。

与 ``prewarm_kol_pool_avatars.py`` 的分工(那份脚本本文件只调用、不修改):
prewarm 只能把**仍存活**的外链喂进本地媒体缓存,链接一旦真死它救不回来;
本脚本负责另一半——HEAD 探活把签名型 CDN 头像分成「仍存活」与「已失效」,
存活的原样交还 prewarm(打印现成命令,本脚本不碰缓存),已失效的按粉丝量
倒序、在预算上限内入既有 ``kol_profile_deep_crawl`` 队列重抓档案。

成本闸三重:``--limit`` 默认 50 上限 200(超出诚实记 ``over_limit``);默认
干跑、只有 ``--apply`` 才真入队且入队前先打印「将花多少次抓取」;``--apply``
前 release validation 围栏失败即闭,干跑期间连接本身设为只读。

探活只发 HEAD、不下载正文,并发与超时都有上限;**拿不准一律算存活**
(网络异常 → ``unknown``),宁可少抓一次也不浪费抓取预算。

入队复用既有通道与围栏:``enqueue_profile_deep_crawl_job(...,
enforce_target_write=True)``,由它自己做 owner/收藏行权限校验与身份绑定,
worker 侧还会在真正花钱前二次复验。本脚本不另起任何抓取实现,也不写业务表,
且刻意不接进任何自动流程:没有 scheduler 任务、没有 cron,只能人工跑。
"""
from __future__ import annotations

import argparse
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(1, str(SCRIPTS))

from stdout_utils import out, out_json  # noqa: E402

from app.core.permissions import check_tab_permission  # noqa: E402
from app.core.release_validation import release_validation_active  # noqa: E402
from app.core.security import user_status_allows_auth  # noqa: E402
from app.services.intelligence.account_scan_helpers import _avatar_url_policy  # noqa: E402


DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_SCAN_ROWS = 6000
DEFAULT_PROBE_CONCURRENCY = 8
MAX_PROBE_CONCURRENCY = 16
DEFAULT_PROBE_TIMEOUT = 6.0
MAX_PROBE_TIMEOUT = 15.0
FRESH_CRAWL_HOURS = 24
SUPPORTED_PLATFORMS = ("instagram", "tiktok", "youtube")
PROBE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# 每条计划 = 1 次账号档案抓取(provider 一次 actor run)。代表视频分析与
# 后续联系方式/档案跟进全部被 suppress_* 关掉,所以不会有隐藏的第二笔支出。
CRAWLS_PER_PLAN_ITEM = 1
# 只读探针可以触达的主机:与 account_scan_helpers._EPHEMERAL_AVATAR_HOST_SUFFIXES
# 同一份签名型 CDN 名单。库里存的是外部 URL,不做主机白名单就等于让一行脏数据
# 指挥本进程发任意请求。
PROBE_HOST_SUFFIXES = (
    "cdninstagram.com",
    "fbcdn.net",
    "tiktokcdn.com",
    "tiktokcdn-us.com",
    "tiktokcdn-eu.com",
    "byteoversea.com",
)
SELECT_AVATAR_ROWS_SQL = """
SELECT id, platform, handle, profile_url, avatar_url, followers, duplicate_of_id
FROM vkpi_kol_pool
WHERE avatar_url IS NOT NULL AND avatar_url <> ?{platform_clause}
ORDER BY COALESCE(followers, 0) DESC, id
LIMIT ?
"""
PLATFORM_CLAUSE_SQL = "\n  AND LOWER(COALESCE(platform, ?)) = ?"
SELECT_ACTOR_SQL = """
SELECT s.*, u.status AS user_status
FROM staff s
JOIN users u ON u.id = s.user_id
WHERE s.id = ?
LIMIT 1
"""


# ── 参数校验 ──────────────────────────────────────────────────────────


def _bounded(value: Any, *, name: str, low: float, high: float, cast: Callable[[Any], Any]) -> Any:
    """越界直接报错,不悄悄夹紧——预算上限被静默放大是最贵的一种 bug。"""

    try:
        parsed = cast(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if parsed < low or parsed > high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")
    return parsed


def normalize_limit(value: Any) -> int:
    return int(_bounded(value, name="limit", low=1, high=MAX_LIMIT, cast=int))


def normalize_concurrency(value: Any) -> int:
    return int(_bounded(
        value, name="probe concurrency", low=1, high=MAX_PROBE_CONCURRENCY, cast=int,
    ))


def normalize_timeout(value: Any) -> float:
    return float(_bounded(
        value, name="probe timeout", low=0.1, high=MAX_PROBE_TIMEOUT, cast=float,
    ))


def normalize_platform(value: Any) -> str:
    platform = str(value or "").strip().lower()
    if platform and platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"platform must be one of {', '.join(SUPPORTED_PLATFORMS)}")
    return platform


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def follower_band(value: Any) -> str:
    """粉丝分档:输出里只留档位,不回显单个账号的精确粉丝数。"""

    count = _int_or_zero(value)
    if count >= 1_000_000:
        return "1M+"
    if count >= 100_000:
        return "100K-1M"
    if count >= 10_000:
        return "10K-100K"
    if count >= 1_000:
        return "1K-10K"
    if count > 0:
        return "<1K"
    return "unknown"


# ── 分类:签名型 CDN 头像 → 存活 / 失效 ─────────────────────────────


def probe_host_allowed(url: str) -> bool:
    """只允许对已审阅的签名型 CDN 主机发探针。"""

    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in PROBE_HOST_SUFFIXES)


def avatar_state(avatar_url: Any) -> str:
    """复用线上唯一口径 ``_avatar_url_policy``,返回 durable/ephemeral/expired/…"""

    _usable, state = _avatar_url_policy(avatar_url)
    return str(state or "missing")


def probe_avatar(url: str, *, timeout: float = DEFAULT_PROBE_TIMEOUT) -> str:
    """只发 HEAD、不下载正文。返回 alive / dead / unknown。

    ``unknown`` 是保守出口:网络抖动、超时、DNS 失败都算「拿不准」,上层按
    存活处理,绝不因为一次探活异常就去花一次抓取预算。
    """

    if not probe_host_allowed(url):
        return "unknown"
    request = urllib.request.Request(  # noqa: S310 - 主机已按上面的白名单收口
        str(url),
        method="HEAD",
        headers={"User-Agent": PROBE_USER_AGENT},
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=float(timeout), context=context) as response:  # noqa: S310
            return "alive" if int(getattr(response, "status", 0)) == 200 else "dead"
    except urllib.error.HTTPError:
        # 服务端明确拒绝(403/404/410…)= 链接真死,可以放心重抓。
        return "dead"
    except Exception:
        # 异常正文可能带签名 URL,一律不回显,只留固定分类。
        return "unknown"


def _classified(row: dict[str, Any], verdict: str, reason: str) -> dict[str, Any]:
    return {
        "pool_id": int(row.get("id") or 0),
        "platform": str(row.get("platform") or "").strip().lower(),
        "followers": _int_or_zero(row.get("followers")),
        "follower_band": follower_band(row.get("followers")),
        "profile_url": str(row.get("profile_url") or "").strip(),
        "duplicate_of_id": _int_or_zero(row.get("duplicate_of_id")),
        "verdict": verdict,
        "reason": reason,
    }


def classify_rows(
    rows: Sequence[dict[str, Any]],
    *,
    probe_fn: Callable[[str], str] | None = None,
    concurrency: int = DEFAULT_PROBE_CONCURRENCY,
) -> list[dict[str, Any]]:
    """把 Pool 行分成 durable/missing/invalid/alive/dead/unknown。

    ``expired``(URL 自带的过期时间戳已过)不必探活,直接判死;只有
    ``ephemeral``(签名尚未标记过期)才值得花一次 HEAD。
    """

    probe = probe_fn or (lambda url: probe_avatar(url))
    workers = normalize_concurrency(concurrency)
    classified: list[dict[str, Any]] = []
    pending: list[tuple[int, str]] = []

    for row in rows:
        state = avatar_state(row.get("avatar_url"))
        if state == "durable":
            classified.append(_classified(row, "skipped", "stable_host"))
            continue
        if state in {"missing", "invalid"}:
            classified.append(_classified(row, "skipped", state))
            continue
        if state == "expired":
            classified.append(_classified(row, "dead", "policy_expired"))
            continue
        # ephemeral:签名还在有效期内,只有真发一次 HEAD 才知道死活。
        pending.append((len(classified), str(row.get("avatar_url") or "")))
        classified.append(_classified(row, "unknown", "probe_pending"))

    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            verdicts = list(pool.map(lambda item: probe(item[1]), pending))
        for (index, _url), verdict in zip(pending, verdicts):
            if verdict == "alive":
                classified[index]["verdict"] = "alive"
                classified[index]["reason"] = "head_200"
            elif verdict == "dead":
                classified[index]["verdict"] = "dead"
                classified[index]["reason"] = "head_not_200"
            else:
                # 保守:拿不准算存活,不进重抓计划。
                classified[index]["verdict"] = "unknown"
                classified[index]["reason"] = "probe_inconclusive"
    return classified


# ── 计划:失效行 → 重抓队列候选 ───────────────────────────────────


def _plan_ineligible_reason(item: dict[str, Any]) -> str:
    if item.get("duplicate_of_id"):
        return "duplicate_row"
    if item.get("platform") not in SUPPORTED_PLATFORMS:
        return "unsupported_platform"
    if not item.get("profile_url"):
        return "profile_url_missing"
    return ""


def build_plan(
    classified: Sequence[dict[str, Any]],
    *,
    limit: int,
    fresh_fn: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """按粉丝量倒序挑出重抓候选;先救大号,预算花在最有价值的人身上。"""

    budget = normalize_limit(limit)
    is_fresh = fresh_fn or (lambda _pool_id: False)
    dead = [item for item in classified if item.get("verdict") == "dead"]
    dead.sort(key=lambda item: (-_int_or_zero(item.get("followers")), int(item.get("pool_id") or 0)))

    plan: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in dead:
        reason = _plan_ineligible_reason(item)
        if reason:
            skipped.append({**item, "plan_status": "skipped", "plan_reason": reason})
            continue
        if is_fresh(int(item["pool_id"])):
            skipped.append({**item, "plan_status": "skipped", "plan_reason": "recently_crawled"})
            continue
        if len(plan) >= budget:
            skipped.append({**item, "plan_status": "skipped", "plan_reason": "over_limit"})
            continue
        plan.append({**item, "plan_status": "planned", "plan_reason": "dead_needs_refetch"})
    return {"plan": plan, "skipped": skipped}


def _counts(items: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _matrix(items: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(str(item.get(key) or "unknown"), []).append(item)
    return {name: _counts(group, "verdict") for name, group in sorted(groups.items())}


def prewarm_handoff(classified: Sequence[dict[str, Any]], *, cap: int = 50) -> dict[str, Any]:
    """仍存活的行交还既有 prewarm(本脚本不碰缓存,只给出现成命令)。"""

    alive_ids = sorted(
        int(item["pool_id"])
        for item in classified
        if item.get("verdict") == "alive" and int(item.get("pool_id") or 0) > 0
    )
    head = alive_ids[:cap]
    command = ""
    if head:
        args = " ".join(f"--pool-id {pool_id}" for pool_id in head)
        command = f"python scripts/ops/prewarm_kol_pool_avatars.py {args} --execute"
    return {
        "alive_pool_ids": len(alive_ids),
        "batch_cap": int(cap),
        "first_batch_pool_ids": head,
        "first_batch_command": command,
    }


# ── 执行:入既有档案深爬队列 ──────────────────────────────────────


def load_actor(conn: Any, staff_id: int) -> tuple[dict[str, Any] | None, str]:
    """按 worker 复验用的同一条查询取 actor,并做同样的 active/权限校验。

    这里只是提前失败,真正的授权判定仍由 ``enqueue_profile_deep_crawl_job``
    的围栏和 worker 侧的 ``_revalidate_target_write_fence`` 负责。
    """

    if int(staff_id or 0) <= 0:
        return None, "staff_id_required"
    row = conn.execute(SELECT_ACTOR_SQL, (int(staff_id),)).fetchone()
    if not row:
        return None, "actor_not_found"
    actor = dict(row)
    # BOOLEAN 经 compat 读回可能是 int 1/0,不能用 ``is True``。
    if actor.get("active") not in (True, 1, "1") or str(actor.get("suspended_at") or "").strip():
        return None, "actor_inactive"
    if not user_status_allows_auth(actor.get("user_status"), production=True):
        return None, "actor_inactive"
    if not check_tab_permission(actor, "vkpi", "write"):
        return None, "actor_permission_missing"
    return actor, ""


def _default_enqueue(
    *,
    url: str,
    kol_pool_id: int,
    staff: dict[str, Any],
) -> dict[str, Any]:
    """唯一的入队出口:既有账号深爬队列 + 既有 owner/目标写围栏。

    ``suppress_*`` 三连把代表视频分析、联系方式跟进、档案跟进全部关掉,
    ``mode="auto"`` 不materialize历史视频——一条计划就只对应一次档案抓取。
    """

    from app.domains.kol.url_deep_crawl import enqueue_profile_deep_crawl_job

    return enqueue_profile_deep_crawl_job(
        url,
        kol_pool_id=int(kol_pool_id),
        max_posts=1,
        mode="auto",
        representative_video_limit=1,
        staff=staff,
        source="ops_refresh_dead_avatars",
        queue_lane="batch",
        enforce_target_write=True,
        suppress_final_v1=True,
        suppress_contact_followup=True,
        suppress_profile_followups=True,
    )


def _terminal_error_code(exc: BaseException) -> str:
    """只留稳定错误码/异常类名——异常正文可能带签名 URL,绝不回显。"""

    return (str(getattr(exc, "code", "") or "").strip() or type(exc).__name__)[:80]


def execute_plan(
    plan: Sequence[dict[str, Any]],
    *,
    staff: dict[str, Any],
    enqueue_fn: Callable[..., dict[str, Any]] | None = None,
    fence_active_fn: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """逐条入队;围栏可能在计划建好之后才落下,每条都要重查一次。"""

    enqueue = enqueue_fn or _default_enqueue
    fence_fn = fence_active_fn or release_validation_active
    results: list[dict[str, Any]] = []

    def record(item: dict[str, Any], status: str, reason: str) -> None:
        results.append({
            "pool_id": int(item["pool_id"]),
            "follower_band": item.get("follower_band"),
            "status": status,
            "reason": reason,
        })

    for item in plan:
        if fence_fn():
            record(item, "blocked", "release_validation_fenced")
            continue
        try:
            payload = enqueue(
                url=str(item.get("profile_url") or ""),
                kol_pool_id=int(item["pool_id"]),
                staff=staff,
            )
        except Exception as exc:  # noqa: BLE001 - 逐条降级,不吞掉原因
            record(item, "failed", _terminal_error_code(exc))
            continue
        status = str((payload or {}).get("status") or "").strip().lower()
        if status in {"queued", "already_queued"}:
            record(item, status, "profile_deep_crawl")
        else:
            record(item, "failed", f"unexpected_enqueue_status:{status or 'empty'}"[:80])
    return results


def _make_read_only(conn: Any) -> None:
    """干跑期间把连接本身设成只读,让「零入队」不是靠自觉。"""

    from app.db.connection import is_postgres_runtime

    if is_postgres_runtime():
        raw = getattr(conn, "_raw", None)
        if raw is None:
            raise RuntimeError("postgres_read_only_handle_unavailable")
        raw.read_only = True
        if not bool(getattr(raw, "read_only", False)):
            raise RuntimeError("postgres_read_only_not_verified")
        return
    conn.execute("PRAGMA query_only=ON")
    row = conn.execute("PRAGMA query_only").fetchone()
    value = row[0] if isinstance(row, (tuple, list)) else row["query_only"]
    if int(value or 0) != 1:
        raise RuntimeError("sqlite_query_only_not_verified")


def fetch_avatar_rows(conn: Any, *, platform: str = "") -> tuple[list[dict[str, Any]], bool]:
    """读候选行;多读一行哨兵,证明扫描确实没被截断。"""

    clause = PLATFORM_CLAUSE_SQL if platform else ""
    params: tuple[Any, ...] = ("", "", platform, MAX_SCAN_ROWS + 1) if platform else ("", MAX_SCAN_ROWS + 1)
    sql = SELECT_AVATAR_ROWS_SQL.format(platform_clause=clause)
    items = [dict(row) for row in conn.execute(sql, params).fetchall()]
    if len(items) > MAX_SCAN_ROWS:
        return items[:MAX_SCAN_ROWS], True
    return items, False


def _summary(
    classified: Sequence[dict[str, Any]],
    plan_bundle: dict[str, Any],
    *,
    limit: int,
    apply_mode: bool,
    scanned: int,
    scan_capped: bool,
) -> dict[str, Any]:
    plan = plan_bundle["plan"]
    return {
        "mode": "apply" if apply_mode else "dry_run",
        "status": "ok",
        "dry_run": not apply_mode,
        "limit": int(limit),
        "rows_scanned": int(scanned),
        "scan_cap": MAX_SCAN_ROWS,
        "scan_cap_exceeded": bool(scan_capped),
        "classification": _counts(classified, "verdict"),
        "classification_reasons": _counts(classified, "reason"),
        "by_platform": _matrix(classified, "platform"),
        "by_follower_band": _matrix(classified, "follower_band"),
        "planned_refetch": len(plan),
        "planned_provider_crawls": len(plan) * CRAWLS_PER_PLAN_ITEM,
        "planned_by_follower_band": _counts(plan, "follower_band"),
        "plan_skipped_reasons": _counts(plan_bundle["skipped"], "plan_reason"),
        "prewarm_handoff": prewarm_handoff(classified),
        "enqueued": 0,
        "already_queued": 0,
        "failed": 0,
        "blocked": 0,
        "results": [],
    }


def run(
    conn: Any,
    *,
    staff_id: int,
    limit: int = DEFAULT_LIMIT,
    apply_mode: bool = False,
    platform: str = "",
    concurrency: int = DEFAULT_PROBE_CONCURRENCY,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    probe_fn: Callable[[str], str] | None = None,
    fresh_fn: Callable[[int], bool] | None = None,
    enqueue_fn: Callable[..., dict[str, Any]] | None = None,
    fence_active_fn: Callable[[], bool] | None = None,
    cost_reporter: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """分类 → 排序 → 预算内建计划 → 打印成本 → (仅 apply 时)入队。"""

    budget = normalize_limit(limit)
    workers = normalize_concurrency(concurrency)
    probe_timeout = normalize_timeout(timeout)
    platform_key = normalize_platform(platform)
    fence_fn = fence_active_fn or release_validation_active

    rows, scan_capped = fetch_avatar_rows(conn, platform=platform_key)
    probe = probe_fn or (lambda url: probe_avatar(url, timeout=probe_timeout))
    classified = classify_rows(rows, probe_fn=probe, concurrency=workers)

    freshness = fresh_fn
    if freshness is None and apply_mode:
        # 只在 apply 时才付这一串新鲜度查询;干跑不需要,也别去打库。
        from app.domains.kol.url_deep_crawl import profile_deep_crawl_is_fresh

        def freshness(pool_id: int) -> bool:
            return bool(profile_deep_crawl_is_fresh(int(pool_id), max_age_hours=FRESH_CRAWL_HOURS))

    plan_bundle = build_plan(classified, limit=budget, fresh_fn=freshness)
    summary = _summary(
        classified,
        plan_bundle,
        limit=budget,
        apply_mode=apply_mode,
        scanned=len(rows),
        scan_capped=scan_capped,
    )
    if scan_capped:
        # 诚实闸:扫描被上限截断时,分类不是全量,不许拿它当全池结论。
        summary["status"] = "partial"
    if cost_reporter is not None:
        cost_reporter(summary)
    if not apply_mode:
        return summary

    if fence_fn():
        summary.update({"status": "blocked", "reason": "release_validation_fenced"})
        summary["blocked"] = len(plan_bundle["plan"])
        return summary

    actor, actor_error = load_actor(conn, int(staff_id))
    if actor is None:
        summary.update({"status": "blocked", "reason": actor_error})
        summary["blocked"] = len(plan_bundle["plan"])
        return summary

    results = execute_plan(
        plan_bundle["plan"],
        staff=actor,
        enqueue_fn=enqueue_fn,
        fence_active_fn=fence_fn,
    )
    summary["results"] = results
    summary["enqueued"] = sum(1 for item in results if item["status"] == "queued")
    summary["already_queued"] = sum(1 for item in results if item["status"] == "already_queued")
    summary["failed"] = sum(1 for item in results if item["status"] == "failed")
    summary["blocked"] = sum(1 for item in results if item["status"] == "blocked")
    if summary["blocked"]:
        summary["status"] = "blocked"
    elif summary["failed"]:
        summary["status"] = "failed"
    return summary


def exit_code(summary: dict[str, Any]) -> int:
    status = str(summary.get("status") or "")
    if status == "blocked":
        return 2
    if status in {"failed", "partial"}:
        return 1
    return 0


def _print_cost(summary: dict[str, Any]) -> None:
    crawls = int(summary.get("planned_provider_crawls") or 0)
    planned = int(summary.get("planned_refetch") or 0)
    mode = "真入队" if summary.get("mode") == "apply" else "干跑(不入队)"
    out(
        f"[成本] 将花抓取次数={crawls}(重抓账号数={planned},每个账号 1 次档案抓取)"
        f" · 模式={mode} · 预算上限={summary.get('limit')}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="签名型 CDN 头像存活分类 + 失效行按粉丝量倒序重抓;默认干跑",
    )
    parser.add_argument(
        "--staff-id",
        type=int,
        required=True,
        help="入队 actor 的 staff.id;干跑时也必须给,便于提前发现权限问题",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"本次最多重抓多少个账号(默认 {DEFAULT_LIMIT},上限 {MAX_LIMIT})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真入既有档案深爬队列;省略时只分类 + 打印计划",
    )
    parser.add_argument(
        "--platform", default="",
        help=f"只处理某个平台({'/'.join(SUPPORTED_PLATFORMS)});省略则全池",
    )
    parser.add_argument(
        "--probe-concurrency", type=int, default=DEFAULT_PROBE_CONCURRENCY,
        help=f"HEAD 探活并发上限(默认 {DEFAULT_PROBE_CONCURRENCY},上限 {MAX_PROBE_CONCURRENCY})",
    )
    parser.add_argument(
        "--probe-timeout", type=float, default=DEFAULT_PROBE_TIMEOUT,
        help=f"单次 HEAD 超时秒数(默认 {DEFAULT_PROBE_TIMEOUT},上限 {MAX_PROBE_TIMEOUT})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        limit = normalize_limit(args.limit)
        concurrency = normalize_concurrency(args.probe_concurrency)
        timeout = normalize_timeout(args.probe_timeout)
        platform = normalize_platform(args.platform)
    except ValueError as exc:
        parser.error(str(exc))

    if args.apply and release_validation_active():
        out_json(
            {"mode": "apply", "status": "blocked", "reason": "release_validation_fenced", "enqueued": 0},
            ensure_ascii=False,
            sort_keys=True,
        )
        return 2

    from app.db.connection import get_conn

    conn = get_conn()
    try:
        if not args.apply:
            _make_read_only(conn)
        summary = run(
            conn,
            staff_id=int(args.staff_id),
            limit=limit,
            apply_mode=bool(args.apply),
            platform=platform,
            concurrency=concurrency,
            timeout=timeout,
            cost_reporter=_print_cost,
        )
    finally:
        if not args.apply:
            conn.rollback()
        conn.close()
    out_json(summary, ensure_ascii=False, sort_keys=True)
    return exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
