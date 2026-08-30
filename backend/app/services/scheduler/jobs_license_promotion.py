"""驾照晋升每日评估 job + 注册(2026-08-30 点火令「打开自动驾照」;env 闸默认 OFF)。

独立兄弟件(jobs_registry 触 800 行软棘轮,job 体与注册下沉到此,registry 只留一行调用):
- env 开关 VKPI_LICENSE_PROMOTION_SCAN_ENABLED,默认 off → 连注册都不发生;上线开闸交用户
  (.env 置 1 后重启 scheduler 服务生效),与 BH_SNAPSHOT_ENABLED 同款注册期闸口径。
- job 恒 dry_run:app.domains.agents.autonomy_license.run_license_promotion_scan 只评估 +
  把 promote/demote 建议幂等写 vkpi_action_inbox(category=license_promotion),
  人审通过后由既有端点 POST /api/admin/vkpi/autonomy/licenses/{action_type}/override 执行。
红线:晋升永远走人审(inbox 建议),绝无自我提权;L4 永不自动授予;零 LLM;
不动 MIN_LEVEL_FOR_LLM、不碰 skill_license_gate;本模块绝不落库改级。
"""
from __future__ import annotations

import os
from typing import Any

LICENSE_PROMOTION_ENV = "VKPI_LICENSE_PROMOTION_SCAN_ENABLED"
TASK_ID = "vkpi_license_promotion_scan"


def license_promotion_scan_enabled() -> bool:
    """env 开关读数(默认 off);注册期判定,与 BH_SNAPSHOT_ENABLED 同词表口径。"""
    return os.environ.get(LICENSE_PROMOTION_ENV, "0").strip().lower() not in {"0", "false", "no", ""}


async def job_vkpi_license_promotion_scan() -> Any:
    """恒 dry_run 的每日评估:同步入口丢 to_thread,不占调度器事件循环。"""
    import asyncio

    from app.domains.agents.autonomy_license import run_license_promotion_scan

    return await asyncio.to_thread(run_license_promotion_scan)


def register_license_promotion_job(scheduler: Any) -> bool:
    """env 闸通过才 add_job(默认 off 时零注册零痕迹);返回是否注册。

    07:40 中国:排在 07:30 daily_action_inbox_generate 之后,同一收件箱当天可见;
    避开 :35 偶数小时族与 08:00 morning_sync。调用方传入的是 jobs_registry 的
    _RunRecordingRegistration 包装,运行记账照旧生效。
    """
    if not license_promotion_scan_enabled():
        return False
    from apscheduler.triggers.cron import CronTrigger

    from app.services.scheduler.jobs import CHINA_TZ

    scheduler.add_job(
        job_vkpi_license_promotion_scan,
        trigger=CronTrigger(hour=7, minute=40, timezone=CHINA_TZ),
        id=TASK_ID,
        name="Autonomy license daily promotion evaluation (dry-run only, suggestions to action inbox, human-approved)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    return True
