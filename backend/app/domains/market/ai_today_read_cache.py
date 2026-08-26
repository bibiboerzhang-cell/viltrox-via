"""AI Today 门面读路径的只读缓存。

为什么单独一层:
- `ai_today.get_ai_today_hot()` 每次读都要重跑推荐视频候选的大 LATERAL JOIN
  (`_recommended_video_rows`)+ 市场信号两段联表(`_market_sources`)+ 最多 90 行
  content_json 的解析与契约校验。2026-08-25 线上取证:12/12 次采样稳定 2.26-2.77s,
  是整个仪表盘 bundle 里最慢的一条腿。
- 但 AI Today 是**每早生成一次的日快照**(scheduler `vkpi_ai_today_hot`,一天一次),
  读端跟着门面 90s 轮询把同一份快照重算一遍毫无收益。

口径与红线:
- 全局键是安全的:该 payload 不含任何按人过滤的数据(`get_ai_today_hot()` 不接
  staff 入参,路由只要 vkpi:read),不存在把 A 的聚合喂给 B 的问题。
- 读失败不进缓存:`reason=read_error` 是一次真实抖动,把它钉住 TTL 就等于把
  「读不到」冒充成稳定结论。诚实空态(not_generated_yet / degraded / invalid)是
  真结论,可以缓存,门面照旧显示它自己的 freshness 字段。
- 返回深拷贝:调用方改了返回值也污染不到缓存里那份。
- 发布验证围栏期 memory_cache 自动只读不写(fenced_builder),这里无需另处理。
- 生成端(`generate_ai_today_hot`)完全不动。
"""
from __future__ import annotations

import copy
from typing import Any

from app.services.cache.memory_cache import cache_get_or_build


# 生成频率是「每天一次」,门面轮询是 90s。TTL 取 300s:相对生成频率完全无损,
# 相对轮询间隔足够大(不会像 30s 那样每拍必过期)。payload 自带 generated_at /
# freshness_status,新鲜度仍由数据自己说话,不受这个窗口影响。
AI_TODAY_READ_CACHE_TTL_SEC = 300
AI_TODAY_READ_CACHE_KEY = "ai_today_hot:read:v1"


def _cacheable(value: Any) -> bool:
    """只把真读到的结论放进缓存;读错误每次都要重试。"""

    return isinstance(value, dict) and str(value.get("reason") or "") != "read_error"


def get_ai_today_hot_cached(*, force_refresh: bool = False) -> dict[str, Any]:
    """读端缓存包装;`force_refresh` 只给用户手动刷新用,定时轮询不得带。"""

    from app.domains.market import ai_today

    value = cache_get_or_build(
        AI_TODAY_READ_CACHE_KEY,
        ai_today.get_ai_today_hot,
        ttl=AI_TODAY_READ_CACHE_TTL_SEC,
        cache_if=_cacheable,
        force_refresh=bool(force_refresh),
    )
    return copy.deepcopy(value) if isinstance(value, dict) else value


__all__ = [
    "AI_TODAY_READ_CACHE_KEY",
    "AI_TODAY_READ_CACHE_TTL_SEC",
    "get_ai_today_hot_cached",
]
