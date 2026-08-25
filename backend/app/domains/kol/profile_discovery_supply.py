"""
domains/kol/profile_discovery_supply.py — 在线发现「每条平台腿」的时间预算与供给上限。

车道 2(A3 腿级 deadline + B3 每平台上限)的落点。两件事都只管**供给侧**,
零触 viltrox_fit_score / rule_v0,也不碰任何质量判据。

■ 为什么要腿级 deadline —— prod 实测(a05e48dd3,vkpi_ai_cost_ledger.occurred_at
  与 vkpi_kol_search_sessions.online_qualification.stage_timing 对齐,9 个会话):
    YouTube 腿(Data API 快路径,不走 Apify)  < 2s
    TikTok  腿  8.4 / 8.5 / 8.9 / 10.7 / 10.7 / 10.9 / 11.1 / 13.7 / 41.3 s  中位 10.7s
    IG hashtag 阶段  7.9 / 9.7 / 15.4 / 42.9 / 45.1 / 48.3 / 52.7 / 52.7 / 76.5 s  中位 45.1s
    IG profile 阶段  15 / 15 / 18 / 20 / 23 / 23 / 29 / 30 / 32 s               中位 23s
  三条腿已经是 asyncio.gather 并发,所以在线段耗时 = 最慢那条腿 = IG(中位 ~68s,
  实测最大 108.5s = 会话 1144)。没有腿级上限时,一条慢腿把整个在线段拖死。

■ 诚实降级:超时的腿记「本轮该平台无供给」(status=deadline_exceeded)并进 errors,
  整体 status 因此落到 partial/failed —— 绝不冒充 ready、绝不把超时说成「搜到 0 条」。
  注意 Apify actor run 仍在云端跑完并照常计费(asyncio 取消不掉 to_thread 里的阻塞调用),
  这一点在 metadata 里说明白,不假装省了钱。

■ deadline 取值是**运营取舍**,不是技术定论。拿上面 9 个会话的实测腿耗时喂本模块真实
  代码路径量出来(脚本口径见 tests/test_discovery_leg_deadline_and_supply.py 同批):
      配置                         在线段 p50 / p90 / max      平台原始给量   IG 无供给
      现状(无腿级 deadline,YT 20)  63.3 / 108.5 / 108.5 s        60          0/9
      25s 全平台(本模块默认)         25.0 /  25.0 /  25.0 s        70          7/9
      YT·TT 25s + IG 45s          45.0 /  45.0 /  45.0 s        70          6/9
      YT·TT 25s + IG 90s          63.3 /  76.5 /  76.5 s        90          0/9
  默认取 25s = 把延迟压到目标线,代价是 IG 大概率整条腿没供给(但**看得见**:
  status=partial + counts.deadline_exceeded_platforms + platform_results 逐条标注)。
  要换成「保 IG 供给、只削掉最长尾」的取法,不必改代码也不必重新部署,只要:
      VKPI_DISCOVERY_LEG_DEADLINE_SECONDS_INSTAGRAM=90
  另注:IG 那条腿真正的成本/延迟根因是 hashtag actor 的 resultsLimit **按 tag 计**
  (单次 dataset 240~300 条 = 4~5 个 tag × 60,只为收敛出 ≤20 个号主)。改它会动召回
  广度(= 质量口径),本批刻意不碰。

■ 为什么 IG/TT 的每平台上限不跟着 YT 提到 50 —— prod 14 天 Apify 发现成本实测:
    apify/instagram-hashtag-scraper  29 runs  7947 items  $16.569   ← 占发现总花费 93.7%
    apify/instagram-profile-scraper  29 runs   580 items  $ 1.138
    clockworks/free-tiktok-scraper   26 runs   520 items  $ 0.829
  IG hashtag 的 resultsLimit 是**按 tag 计**的(实测单次 dataset 240~300 条
  = 4~5 个 tag × 60),上限翻 2.5 倍 ≈ 单次 $0.57 → $1.43。YouTube 那条腿走的是
  YouTube Data API search.list:同一次调用无论 maxResults 取 1 还是 50 都恒定 100
  quota units、仍是一次 HTTP 往返,所以 20→50 配额不变、延迟不变、零 Apify 花费。
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

from app.core.logging import get_logger
from app.domains.kol.discovery_filters import (
    _detect_excluded_region,
    _is_discovery_garbage,
    _is_hard_avoid,
)
from app.domains.kol.profile_discovery_candidates import _is_own_brand_account

logger = get_logger(__name__)

# 腿级 deadline(秒)。全局 env + 每平台 env 覆盖,运行时读(非 import 时快照),
# 线上改 env 重启即生效、不必重新部署。
LEG_DEADLINE_ENV = "VKPI_DISCOVERY_LEG_DEADLINE_SECONDS"
LEG_DEADLINE_PLATFORM_ENV_PREFIX = "VKPI_DISCOVERY_LEG_DEADLINE_SECONDS_"
LEG_DEADLINE_DEFAULT_SECONDS = 25.0

# 每平台供给上限硬顶(与 profile_discovery_queue/provider 既有 min(...,50) 口径一致)。
PLATFORM_SUPPLY_HARD_CAP = 50


def _positive_float(raw: Any) -> float | None:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def leg_deadline_seconds(platform: str) -> float:
    """本条腿允许跑多久。优先级:每平台 env > 全局 env > 默认 25s。

    非法/非正值一律忽略并退回下一级(失败方向安全:配错 env 不会变成 0 秒把所有腿掐死)。
    """
    key = str(platform or "").strip().lower()
    if key:
        override = _positive_float(os.environ.get(LEG_DEADLINE_PLATFORM_ENV_PREFIX + key.upper()))
        if override is not None:
            return override
    shared = _positive_float(os.environ.get(LEG_DEADLINE_ENV))
    if shared is not None:
        return shared
    return LEG_DEADLINE_DEFAULT_SECONDS


def resolve_platform_limit(
    platform: str,
    requested: int,
    overrides: Any = None,
) -> int:
    """本条腿要向 provider 要多少条。overrides = {平台: 上限} 的 operator 显式配置。

    缺 override / 非法值 → 用 requested(旧行为逐字不变)。永远夹在 [1, 50]。
    """
    value = requested
    if isinstance(overrides, dict):
        raw = overrides.get(str(platform or "").strip().lower())
        try:
            candidate = int(raw)
        except (TypeError, ValueError):
            candidate = 0
        if candidate > 0:
            value = candidate
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = requested
    return max(1, min(value, PLATFORM_SUPPLY_HARD_CAP))


def sanitize_platform_limits(value: Any) -> dict[str, int]:
    """把请求体里的每平台上限归一成 {小写平台: 1..50};非 dict / 垃圾值 → {}。"""
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key, raw in value.items():
        name = str(key or "").strip().lower()
        if not name:
            continue
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            continue
        if limit > 0:
            out[name] = min(limit, PLATFORM_SUPPLY_HARD_CAP)
    return out


def build_enrich_doom_gate(
    *,
    exclude_chinese: bool,
    neg_terms: list[str],
) -> Callable[[dict[str, Any]], bool]:
    """A2「富化后置」下发给 provider 层的**单调闸**工厂。

    返回的判据 True = 这条候选无论富化与否都会被发现主环丢弃 → 不值得为它烧一次
    IG profile-scraper 配额。里面只放**判据文本只增不减时结论不翻转**的闸
    (命中即丢的子串/正则族),口径与主环逐字同源(同一批 discovery_filters 函数)。

    刻意排除的两个:
    - ``_has_camera_signal`` 反单调 —— 相机信号常只写在 bio 里,而 bio 恰恰是富化才
      拿得到的;拿它前置会静默杀掉真摄影师。
    - ``_brand_official_verdict`` 的动态判据在缺 bio 时刻意「证据不足放行」,
      前置结论可能与富化后相反。

    本闸只决定花不花钱、从不删候选:判错的最坏后果是那条候选 followers 未知,
    照既有 reach_status=analyzing 通路诚实展示,绝不会少捞一个人。
    """

    def _doomed(probe: dict[str, Any]) -> bool:
        if _is_discovery_garbage(probe):
            return True
        if exclude_chinese and _detect_excluded_region(probe):
            return True
        if _is_own_brand_account(probe):
            return True
        return _is_hard_avoid(probe, neg_terms)

    return _doomed


def leg_no_supply(platform: str, deadline: float) -> dict[str, Any]:
    """A3 腿级 deadline 到点时的诚实结果:本轮该平台**无供给**(不是「搜到 0 条」)。

    形状与 provider 的 ``_search_one_platform`` 返回体兼容:``error=True`` 让它进 errors、
    整体 status 落到 partial/failed,绝不冒充 ready。
    """
    return {
        "platform": platform,
        "status": "deadline_exceeded",
        "message": f"platform_leg_deadline_exceeded_after_{deadline:.0f}s",
        "annotated": [],
        "error": True,
        "deadline_exceeded": True,
        "deadline_seconds": deadline,
        # Apify actor run 在云端照跑完并照常计费(取消不掉 to_thread 里的阻塞调用),
        # 如实标注,不假装超时=省钱。
        "provider_run_still_billed": True,
    }


# 被 deadline 甩掉的 task 必须留强引用,否则 asyncio 只持弱引用、任务可能被 GC 掉;
# 完成后自行从集合里摘除,不会无限增长。
_ORPHANED_LEGS: set[asyncio.Task[Any]] = set()


def _detach(task: asyncio.Task[Any]) -> None:
    """放弃等待一条超时的腿,但既不泄漏 task 也不留「exception was never retrieved」噪声。"""
    _ORPHANED_LEGS.add(task)

    def _reap(done: asyncio.Task[Any]) -> None:
        _ORPHANED_LEGS.discard(done)
        if done.cancelled():
            return
        exc = done.exception()
        if exc is not None:
            logger.debug("discovery_leg_orphan_finished_with_error error=%r", exc)

    task.add_done_callback(_reap)
    task.cancel()


def leg_accounting(outcome: Any) -> dict[str, Any]:
    """把一条腿的 deadline 记账透进 platform_results(没超时 → {},结构零变化)。

    诚实区分「超时所以本轮无供给」与「跑完了但搜到 0 条」——两者在旧结构里长得一模一样。
    """
    if not isinstance(outcome, dict) or not outcome.get("deadline_exceeded"):
        return {}
    return {
        "deadline_exceeded": True,
        "deadline_seconds": outcome.get("deadline_seconds"),
        "provider_run_still_billed": True,
    }


async def run_all_legs(
    platforms: list[str],
    factory: Callable[[str], Awaitable[Any]],
) -> list[Any]:
    """并发跑所有平台腿,每条腿各自受 deadline 约束;返回值与旧 ``asyncio.gather`` 逐位对齐。

    并发只保证「耗时不叠加」,不保证「不被最慢那条拖死」——prod 实测在线段耗时恒等于
    IG 那条腿(中位 ~68s、最大 108.5s),所以每条腿必须再有硬上限。
    ``return_exceptions=True`` 与旧行为一致:一条腿抛错不带塌其余腿。
    """
    return list(
        await asyncio.gather(
            *[
                run_leg_with_deadline(
                    platform,
                    lambda platform=platform: factory(platform),
                    on_timeout=leg_no_supply,
                )
                for platform in platforms
            ],
            return_exceptions=True,
        )
    )


async def run_leg_with_deadline(
    platform: str,
    factory: Callable[[], Awaitable[Any]],
    *,
    on_timeout: Callable[[str, float], Any],
) -> Any:
    """跑一条平台腿,超过 deadline 就交给 ``on_timeout`` 造「本轮该平台无供给」的诚实结果。

    实现要点:
    - 用 ``asyncio.wait``(不是 ``wait_for``)—— wait_for 超时后会 **await 被取消的 task**,
      万一某条腿吞了 CancelledError 或卡在不可取消的等待里,deadline 就形同虚设。
      wait 到点即返回,腿的死活与本协程解耦,上限是硬的。
    - 取消语义:被甩掉的 task 立刻 cancel + detach。腿里最终阻塞在
      ``asyncio.to_thread`` 上的 Apify 调用**取消不了**(线程照跑完、run 照计费),
      cancel 只是让协程侧立即松手;线程会在 actor 自己的 timeout_secs 内结束。
      这一点在返回体里如实标注,不假装省了钱。
    - 其它平台不受影响:每条腿各自一个 task,超时只影响本条。
    """
    deadline = leg_deadline_seconds(platform)
    task: asyncio.Task[Any] = asyncio.ensure_future(factory())
    done, _pending = await asyncio.wait({task}, timeout=deadline)
    if task not in done:
        _detach(task)
        logger.info(
            "discovery_leg_deadline_exceeded platform=%s deadline_s=%.1f "
            "(provider run keeps running and is still billed)",
            platform, deadline,
        )
        return on_timeout(platform, deadline)
    return task.result()
