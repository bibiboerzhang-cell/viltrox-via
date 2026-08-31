"""产品-行业侧适配器:KOL pool 门面对外保名的 re-export 集中处。

``pool.py`` 历史上在模块级直接 import 这四个符号,连同 ``app.platform`` /
``app.platform.db`` / ``app.domains.industry`` 三条祖先包边,一共七条边全部计入
pool 的模块级 fan-out(合同 v1.1 的 ``internal_fan_out_max`` 口径按仓内不同模块数
计,祖先包也算)。把 import 语句下沉到本文件后,pool 只保留一条到本模块的边,
而 ``pool.<name>`` 这四个名字逐字不变——``monkeypatch.setattr(kol_pool,
"ensure_vkpi_product_industry_schema", ...)`` 这类既有测试路径不受影响。

pool 真正调用的只有 ``ensure_vkpi_product_industry_schema``;``get_crawler`` /
``calculate_kpis`` / ``ScoringRegistry`` 是历史对外符号,只在此保名 re-export,
不要据此推断 pool 仍在使用它们。

本文件只做模块级 re-export,不含任何逻辑,也**不得** import ``pool`` 或
``pool_enrich``(那两个模块互为一个 SCC,反向 import 会把本文件拽进环里)。
"""
from __future__ import annotations

from app.platform.industry_crawlers import get_crawler
from app.domains.industry.snapshot_kpis import calculate_kpis
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.scoring import ScoringRegistry

__all__ = [
    "ScoringRegistry",
    "calculate_kpis",
    "ensure_vkpi_product_industry_schema",
    "get_crawler",
]
