"""``pool.detail_bundle`` 的惰性数据源解析器。

``pool.py`` 是 KOL 域门面,被大量模块 import;它在 ``detail_bundle`` 里逐个
lazy import 分析/富化/媒体类模块。fan-out 口径统计的是**模块 import 的仓内不同
模块数**,函数体里的 lazy import 同样计边,所以那些 import 语句必须真正搬走才能
降低 pool 的 fan-out。本文件把它们收拢,pool 只保留一条到本模块的边。

**每个解析器都在函数体内重新 from-import,不在模块级绑定**,这一条是硬约束:

- ``detail_bundle`` 依赖源模块属性的后期绑定。既有测试打的是源模块属性
  (``eleven_dimensions.load_persisted_dimensions_11`` /
  ``cache_repo.get_analysis_cache_entries_for_targets`` /
  ``creator_gear.aggregate_creator_gear`` / ``analysis_readiness.
  load_readiness_video_evidence`` 等),不是 pool 上的名字;模块级绑定会把补丁
  之前的旧函数对象钉死,补丁将静默失效。
- ``creator_gear_helpers`` / ``audience_language_reader`` 必须保持"导入失败就抛
  出、由 pool 的 try 块记 warning 后降级"的语义;改成模块级 import 会让一次导入
  失败从"少几个设备字段"升级成整个详情抽屉 500。

同理,本文件**不得** import ``pool`` 或 ``pool_enrich``(两者互为一个 SCC),
也不得在模块级 import 上面这些源模块。
"""
from __future__ import annotations

from typing import Any


def analysis_cache_reader() -> Any:
    """返回 ``cache_repo.get_analysis_cache_entries_for_targets``,每次调用重新解析。"""
    from app.domains.analysis.cache_repo import get_analysis_cache_entries_for_targets

    return get_analysis_cache_entries_for_targets


def readiness_helpers() -> tuple[Any, Any, Any]:
    """返回 ``analysis_readiness`` 三件套,顺序与 pool 原 import 逐字一致。"""
    from app.domains.kol.analysis_readiness import (
        build_analysis_readiness,
        evidence_quality_projection,
        load_readiness_video_evidence,
    )

    return build_analysis_readiness, evidence_quality_projection, load_readiness_video_evidence


def dimensions_reader() -> Any:
    """返回 ``eleven_dimensions.load_persisted_dimensions_11``。"""
    from app.domains.kol.eleven_dimensions import load_persisted_dimensions_11

    return load_persisted_dimensions_11


def llm_deep_reader() -> Any:
    """返回 ``llm_deep_analysis.get_kol_llm_deep_analysis``。"""
    from app.domains.kol.llm_deep_analysis import get_kol_llm_deep_analysis

    return get_kol_llm_deep_analysis


def creator_gear_helpers() -> tuple[Any, Any]:
    """返回 ``creator_gear`` 的聚合器与文本兜底;导入失败交给 pool 的 try 块降级。"""
    from app.domains.kol.creator_gear import aggregate_creator_gear, gear_from_text

    return aggregate_creator_gear, gear_from_text


def audience_language_reader() -> Any:
    """返回 ``audience_language.audience_language_for_kol``;导入失败交给 pool 的 try 块降级。"""
    from app.domains.kol.audience_language import audience_language_for_kol

    return audience_language_for_kol


__all__ = [
    "analysis_cache_reader",
    "audience_language_reader",
    "creator_gear_helpers",
    "dimensions_reader",
    "llm_deep_reader",
    "readiness_helpers",
]
