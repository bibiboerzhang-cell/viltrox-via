"""内容墙「去查最新内容」的派活编排(唯一花钱的一步)。

**不另造入队路径**:逐个账号调既有的 ``url_deep_crawl.enqueue_profile_deep_crawl_job``,
既有的目标写围栏 / 幂等 / 预算闸一个不绕。本模块只做三件事:

1. **重算报价并与客户端回传的指纹比对**——不一致就 409 让操作员重看。
   没有这一步,「确认框写 3 个、实际派 30 个」是完全可能发生的,而且最难被发现。
2. **把三个 follow-up 全部关掉**(代表作深析 / 联系方式补抓 / 账号档案抽取)。
   不关的话一次「取内容」会扇出成四个付费动作,报价里的「1 个账号 = 1 次取数」就是假话。
   这三个开关在本模块里**硬编码**,不做参数——忘了传一次就翻四倍。
3. **逐条如实回执**:派出去几个、几个是并入已有的、几个没权限、几个失败,一条不编。

诚实契约(红线 4):派活本身只返回「已派/未派」的事实,它不知道也不假装知道结果何时回来。
**但派完之后必须能读回结局**——``read_dispatch_outcomes`` 就是那条回读通道
(2026-08-24 线上 P0 的同型病根:派出去的活被拦死,界面却一直停在「已安排,还没结果
回来」,操作员对着假进度等了 17 分钟)。回读是纯 SELECT,只认本车道自己派的活,
终态按人话说清「取回来了 / 没能取到 + 为什么」,机器码一个字都不上门面。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.kol import my_kol_wall_fetch_plan as wall_plan

logger = get_logger("viltrox.domains.kol.my_kol_wall_fetch")


class WallFetchError(RuntimeError):
    """稳定错误码 + HTTP 状态,供路由直接转 HTTPException。"""

    def __init__(self, code: str, status_code: int = 409, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = int(status_code)
        self.detail = detail or {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ── 派单结局回读(纯 SELECT;HIGH 3:派完必须能读回结局,而且必须能停) ────────

# 一次最多回读多少条:单次上限硬顶 30,再留一点并入的余量。
OUTCOME_LOOKUP_LIMIT = 40

# apify_jobs 状态 → 三档结局。口径与 tasks.queue_view 的状态集合同源。
_WAITING_STATUSES = {"queued", "retrying", "processing", "running", "in_progress", "started"}
_LANDED_STATUSES = {"done", "success", "partial_done"}

# 终态但没取到内容时的人话。**按失败类别说,不按机器码说**;未映射的一律归入
# 统一兜底句,绝不把 last_error 原文截几十个字符打到门面上。
_STOPPED_BY_STATUS = {
    "cancelled": "已被取消,没有去取",
    "prefilter_rejected": "预检没有放行,没有去取",
    "timeout": "取的时候超时了,没能取回来",
}
_STOPPED_BY_CATEGORY = {
    "authorization": "没有取这个账号内容的权限,或授权已变更:请从收藏里重新发起",
    "budget": "本月取数额度已用完,这一条没能开始",
    "provider": "平台侧暂时取不到(繁忙或网络不稳)",
    "download": "这个账号的内容暂时取不到:主页可能已私密、改名或被平台限制",
    "model": "取回来的内容格式异常,没能存下来",
}
UNKNOWN_STOP_REASON = "没能取回来,原因还没查清"


def _stop_reason(status: str, last_error: Any, last_error_category: Any) -> str:
    """终态未取到 → 一句人话。分类逻辑复用既有 ``failure_category``,措辞按本车道重写。"""

    mapped = _STOPPED_BY_STATUS.get(status)
    if mapped:
        return mapped
    try:
        from app.domains.kol.video_analysis_progress_reasons import failure_category

        category = failure_category(last_error_category=last_error_category, last_error=last_error)
    except Exception:  # noqa: BLE001 — 读不出类别也不许让回读整个失败
        category = "unknown"
    return _STOPPED_BY_CATEGORY.get(category, UNKNOWN_STOP_REASON)


def read_dispatch_outcomes(
    conn: Any,
    *,
    job_ids: list[int],
    staff_scope_id: int | None,
    scoped: bool = True,
) -> dict[str, Any]:
    """回读本车道派出的活现在到哪一步了(纯 SELECT,零写库、零 provider)。

    只认 ``job_type=kol_profile_deep_crawl`` 且 ``payload.source`` 是本车道的记录;
    非管理层再叠一层「只看自己派的」。读不到的 id 如实归入 ``unknown``——
    宁可说「读不到」,也不许把读不到当成「已完成」。
    """

    ids = []
    for value in job_ids or []:
        job_id = _int(value)
        if job_id > 0 and job_id not in ids:
            ids.append(job_id)
        if len(ids) >= OUTCOME_LOOKUP_LIMIT:
            break
    if not ids:
        return {"status": "ok", "items": [], "counts": _outcome_counts([]), "unknown_job_ids": []}

    marks = ",".join("?" for _ in ids)
    sid = max(0, _int(staff_scope_id)) if scoped else 0
    rows = conn.execute(
        f"""
        SELECT id AS job_id,
               COALESCE(status, '') AS status,
               COALESCE(last_error, '') AS last_error,
               COALESCE(last_error_category, '') AS last_error_category,
               payload ->> 'kol_pool_id' AS kol_pool_id
        FROM apify_jobs
        WHERE id IN ({marks})
          AND job_type = ?
          AND payload ->> 'source' = ?
          AND (? = 0 OR payload ->> 'staff_id' = ?)
        """,
        (
            *ids,
            _deep_crawl_job_type(),
            wall_plan.WALL_FETCH_SOURCE,
            sid,
            str(sid),
        ),
    ).fetchall()

    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        data = dict(row)
        job_id = _int(data.get("job_id"))
        seen.add(job_id)
        status = str(data.get("status") or "").strip().lower()
        if status in _WAITING_STATUSES:
            state, reason = "waiting", ""
        elif status in _LANDED_STATUSES:
            state, reason = "landed", ""
        else:
            state = "stopped"
            reason = _stop_reason(status, data.get("last_error"), data.get("last_error_category"))
        items.append(
            {
                "job_id": job_id,
                "kol_pool_id": _int(data.get("kol_pool_id")) or None,
                "state": state,
                "reason_human": reason or None,
            }
        )
    unknown = [job_id for job_id in ids if job_id not in seen]
    return {
        "status": "ok",
        "items": items,
        "counts": _outcome_counts(items, unknown=len(unknown)),
        "unknown_job_ids": unknown,
    }


def _deep_crawl_job_type() -> str:
    from app.domains.kol.url_deep_crawl_queue import DEEP_CRAWL_JOB_TYPE

    return str(DEEP_CRAWL_JOB_TYPE)


def _outcome_counts(items: list[dict[str, Any]], *, unknown: int = 0) -> dict[str, int]:
    counts = {"waiting": 0, "landed": 0, "stopped": 0, "unknown": int(unknown)}
    for item in items:
        key = str(item.get("state") or "")
        if key in counts:
            counts[key] += 1
    return counts


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", "")
    if code:
        return str(code)
    return type(exc).__name__


def run_wall_fetch(
    conn: Any,
    *,
    staff: dict[str, Any] | None,
    staff_scope_id: int | None,
    kol_pool_id: int = 0,
    days: int = 0,
    plan_hash: str = "",
    expected_count: int | None = None,
) -> dict[str, Any]:
    """按报价派活。报价对不上一条都不派。"""

    plan = wall_plan.plan_wall_fetch(
        conn,
        staff=staff,
        staff_scope_id=staff_scope_id,
        kol_pool_id=kol_pool_id,
        days=days,
    )
    submitted_hash = str(plan_hash or "").strip()
    if not submitted_hash:
        raise WallFetchError("wall_fetch_plan_required", 400, {"plan": plan})
    if submitted_hash != plan["plan_hash"]:
        # 从看报价到点确认之间,名单/冷却/额度都可能变。宁可让人重看一眼,
        # 也不能拿旧数字去派新活。
        raise WallFetchError("wall_fetch_plan_drifted", 409, {"plan": plan})
    if expected_count is not None and _int(expected_count) != _int(plan["planned_count"]):
        raise WallFetchError("wall_fetch_plan_drifted", 409, {"plan": plan})
    if plan["planned_count"] <= 0:
        return {"status": "nothing_to_fetch", "plan": plan, "queued": [], "already_queued": [], "failed": []}

    spec = wall_plan.window_spec(plan["days"])
    # 单个账号走交互泳道(操作员在等);批量走 batch 泳道,绝不占交互道。
    queue_lane = "interactive" if plan["planned_count"] == 1 else "batch"

    queued: list[dict[str, Any]] = []
    already_queued: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    from app.domains.kol import url_deep_crawl

    for item in plan["planned"]:
        pool_id = _int(item.get("kol_pool_id"))
        try:
            row = conn.execute(
                "SELECT COALESCE(profile_url, '') AS profile_url FROM vkpi_kol_pool WHERE id=?",
                (pool_id,),
            ).fetchone()
            profile_url = str(dict(row).get("profile_url") or "") if row else ""
            if not profile_url:
                failed.append({**item, "reason": "profile_url_missing"})
                continue
            result = url_deep_crawl.enqueue_profile_deep_crawl_job(
                profile_url,
                kol_pool_id=pool_id,
                max_posts=wall_plan.WINDOW_POSTS,
                mode="account_deep",
                staff=staff,
                source=wall_plan.WALL_FETCH_SOURCE,
                queue_lane=queue_lane,
                enforce_target_write=True,
                since_iso=spec["since"],
                # 三个 follow-up 全关:一次「取内容」就是一次取数,不扇出成四个付费动作。
                suppress_final_v1=True,
                suppress_contact_followup=True,
                suppress_profile_followups=True,
            )
        except Exception as exc:  # noqa: BLE001 — 单个账号失败不阻断整批,但必须如实计入
            logger.warning(
                "wall_fetch.enqueue_failed kol_pool_id=%s code=%s",
                pool_id,
                _error_code(exc),
            )
            failed.append({**item, "reason": _error_code(exc)})
            continue
        record = {**item, "job_id": _int(result.get("job_id")) or None}
        if str(result.get("status") or "") == "already_queued":
            already_queued.append(record)
        else:
            queued.append(record)

    return {
        "status": "dispatched",
        "plan": plan,
        "queued": queued,
        "already_queued": already_queued,
        "failed": failed,
        "counts": {
            "planned": plan["planned_count"],
            "queued": len(queued),
            "already_queued": len(already_queued),
            "failed": len(failed),
        },
    }
