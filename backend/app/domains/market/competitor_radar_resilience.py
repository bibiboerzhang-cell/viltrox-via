"""competitor_radar_resilience.py — 竞品雷达 in-job 重试壳 + 12:00 补漏判定(2026-08-24 审计 F4/F5)。

审计事实(prod 实证):雷达日任务 06:30 中国连败两天且模式各异——08-22 代理隧道 ~10.9s 掉线
(httpcore RemoteProtocolError,与 08-19 同款)、08-23 模型漏用 google_search → 零引文
fail-closed(ungrounded_not_persisted);长期 ~40-50% 天级断档,此前无 in-job 重试、无补漏、
run-now 端点关闭。本模块只做编排,不改 generate_competitor_radar 本体逻辑:

- ``generate_competitor_radar_with_retries``:provider 异常形状 / ungrounded 结果 → 最多再打
  2 发(5s/15s 退避,日任务线程里 sleep 无害),每发仍走原 fail-closed 全链
  (预算闸/接地闸/合同闸一寸不放宽)。预算注:最多 +2 发/日 ≈ $0.11(_EST_COST=0.05/发 +
  少量解析开销),在 cron:competitor_radar 硬上限内;预算被拦(budget_blocked)绝不重打。
- ``today_snapshot_exists``:12:00 中国补漏班车先查当日快照。口径 = 写端同一 SQL
  ``CURRENT_DATE``(写端 INSERT VALUES (CURRENT_DATE, ...),同一连接/时区必然一致);
  已有即如实跳过零成本。读失败按「不存在」处理并记日志——宁可多打一发幂等 upsert,
  也不因探针故障静默丢掉当天补漏。

红线:零触 viltrox_fit_score / rule_v0;只读/只经原写端写 vkpi_competitor_radar。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_ATTEMPTS = 3  # 1 正常发 + 最多 2 重试
_BACKOFF_SECONDS = (5.0, 15.0)
_NON_RETRYABLE_STATUSES = {"ok", "budget_blocked"}


def _retry_reason(result: Any) -> str:
    """判定失败是否属于「provider 异常 / ungrounded」可重打形状;不可重打返回空串。

    ai_today._generate 把 provider/传输层异常吞成 provenance.status=异常类型名
    (如 RemoteProtocolError,08-19/08-22 形状)或 contract_not_ready(含模型漏用
    google_search → 零引文,08-23 形状),这里据此识别;纯合同失败但 provider
    真实 success 的结果不重打(不在审计授权范围)。
    """
    if not isinstance(result, dict):
        return "non_dict_result"
    status = str(result.get("status") or "")
    if status in _NON_RETRYABLE_STATUSES:
        return ""
    if status == "ungrounded":
        return "ungrounded"
    if str(result.get("reason") or "") == "item_source_not_grounded":
        return "item_source_not_grounded"
    provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
    provider_status = str(provenance.get("status") or "")
    if provider_status and provider_status not in {"success", "budget_blocked"}:
        return f"provider_not_success:{provider_status[:60]}"
    return ""


def generate_competitor_radar_with_retries(
    *,
    max_attempts: int = _MAX_ATTEMPTS,
    backoff_seconds: tuple[float, ...] = _BACKOFF_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """跑雷达生成,provider 异常/ungrounded 封顶重试;逐发如实记日志。"""
    # 运行时经模块属性解析,保住既有测试对 competitor_radar.generate_competitor_radar 的 monkeypatch 路径。
    from app.domains.market import competitor_radar

    result: dict[str, Any] = {}
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        raw_result = competitor_radar.generate_competitor_radar()
        result = raw_result if isinstance(raw_result, dict) else {}
        reason = _retry_reason(raw_result)
        if not reason:
            if attempt > 1 and str(result.get("status") or "") == "ok":
                logger.info("competitor_radar.retry_recovered", extra={"attempt_total": attempt})
            return result
        if attempt >= max(1, int(max_attempts)):
            logger.warning(
                "competitor_radar.retries_exhausted",
                extra={"attempt_total": attempt, "final_reason": reason},
            )
            return result
        delay = (
            backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
            if backoff_seconds
            else 0.0
        )
        logger.warning(
            "competitor_radar.attempt_failed_retrying",
            extra={"attempt": attempt, "retry_reason": reason, "backoff_seconds": delay},
        )
        if delay > 0:
            sleeper(delay)
    return result


def today_snapshot_exists() -> bool:
    """当日雷达快照是否已存在(写端同一 CURRENT_DATE 口径);探针失败按不存在处理并记日志。"""
    from app.db.connection import get_conn
    from app.domains.market import competitor_radar

    try:
        competitor_radar._ensure_schema()
        row = get_conn().execute(
            "SELECT snapshot_date AS d FROM vkpi_competitor_radar "
            "WHERE snapshot_date = CURRENT_DATE LIMIT 1"
        ).fetchone()
        return row is not None
    except Exception:
        logger.warning("competitor_radar.catchup_snapshot_probe_failed", exc_info=True)
        return False
