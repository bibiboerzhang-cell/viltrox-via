"""config-gate + 延迟 import + to_thread + 运行记账 的日任务包装(D2 车道三任务用;与哨兵占位同口径)。"""
from __future__ import annotations

from typing import Any


def gated_daily_job(task_key: str, module: str, entrypoint: str, **entry_kwargs: Any) -> Any:
    """D2 车道三个零 LLM 日任务的统一包装:config-gate(scheduler_tasks.<task_key>)+ 延迟 import +
    to_thread 跑同步入口 + 运行记账;模块缺失诚实占位(与哨兵同口径)。"""
    import asyncio
    import importlib
    import inspect

    from app.services.scheduler.jobs_tasks import _record_scheduler_run, _scheduler_task_enabled

    async def job() -> Any:
        if not _scheduler_task_enabled(task_key):
            return None
        try:
            entry = getattr(importlib.import_module(module), entrypoint)
        except Exception as exc:  # noqa: BLE001 — 模块未落地时诚实占位
            from app.services.scheduler.jobs import logger as _jobs_logger

            _jobs_logger.warning("scheduler.%s_module_missing", task_key, extra={"reason": f"{type(exc).__name__}: {str(exc)[:120]}"})
            return {"status": "module_missing", "module": module}
        try:
            if inspect.iscoroutinefunction(entry):
                result = await entry(**entry_kwargs)
            else:
                result = await asyncio.to_thread(entry, **entry_kwargs)
            _record_scheduler_run(task_key, ok=True)
            return result
        except Exception as exc:  # noqa: BLE001 — 只记账不拖垮调度器
            _record_scheduler_run(task_key, ok=False, error=str(exc)[:240])
            raise

    job.__name__ = f"job_{task_key}"
    return job


