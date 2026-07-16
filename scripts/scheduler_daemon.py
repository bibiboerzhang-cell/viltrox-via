"""本地常驻 scheduler daemon —— 让大脑自己跑(直接起 APScheduler 引擎,keep-alive)。

由 launchd(com.viltrox.vkpi-scheduler)拉起;崩溃 launchd 自动重启。
"""
from stdout_utils import out as stdout_out
import asyncio

from app.services.scheduler import jobs


async def main() -> None:
    await jobs.start_scheduler()
    st = jobs.get_scheduler_status()
    n = len(st.get("jobs", []) if isinstance(st, dict) else [])
    stdout_out(f"SCHEDULER_STARTED jobs={n}", flush=True)
    while True:
        await asyncio.sleep(1800)


if __name__ == "__main__":
    asyncio.run(main())
