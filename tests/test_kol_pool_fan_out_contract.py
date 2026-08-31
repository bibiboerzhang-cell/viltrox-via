"""``app.domains.kol.pool`` 的 fan-out 契约 + 两个下沉兄弟文件的行为等价守卫。

背景:工程健康合同 ``internal_fan_out_max`` 的 target=20 / ceiling=40。口径是
**模块 import 的仓内不同模块数**(collector 的 ``graph``,不是 ``import_time_graph``):
祖先包边算,函数体里的 lazy import 也算——这一点和「环棘轮」相反,环棘轮只看
import 期子图。所以想降 pool 的 fan-out,把 import 挪进函数体是**无效**的,必须把
import 语句真正搬到别的模块里去。

pool.py 是 KOL 域门面,被大量模块 import。本文件把它的两条下沉红线钉住:

1. fan-out 不许再爬回 ceiling —— 见 ``test_pool_fan_out_stays_under_ceiling``;
2. 下沉不许改语义 —— 门面上的名字要保住,兄弟文件不许反向 import 把 pool 拽进环,
   ``detail_bundle`` 依赖的源模块属性后期绑定要原样保留(既有测试打的是
   ``eleven_dimensions.load_persisted_dimensions_11`` 这类源模块属性)。
"""
from __future__ import annotations

import ast
import sys
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import vkpi_engineering_health_collect as collector  # noqa: E402
from scripts import vkpi_engineering_health_graph as graph_tools  # noqa: E402
from scripts import vkpi_engineering_health_snapshot as snapshot  # noqa: E402

POOL = "app.domains.kol.pool"
SIBLINGS = (
    "app.domains.kol.pool_detail_sources",
    "app.domains.kol.pool_industry_adapters",
)
# 合同 ceiling 是 40;留 2 的余量,免得下一次加依赖刚好卡在 39 又把全局顶回去。
FAN_OUT_CEILING = 38

# 下沉时从 pool 摘走 import、但门面必须逐字保名的对外符号。
REEXPORTED_NAMES = (
    "ScoringRegistry",
    "calculate_kpis",
    "ensure_vkpi_product_industry_schema",
    "get_crawler",
)


@lru_cache(maxsize=1)
def _import_graph() -> dict[str, set[str]]:
    """collector 同口径的全量 import 图(含函数体内 lazy import 边)。"""
    captured = snapshot.snapshot_sources(
        ROOT,
        collector.PYTHON_ROOTS,
        {".py"},
        skip_parts=collector.SKIP_PARTS,
        test_directory_names=collector.TEST_DIRECTORY_NAMES,
        test_filename_markers=collector.TEST_FILENAME_MARKERS,
    )
    assert captured.complete, (
        f"源快照不完整,fan-out 口径失真:symlinks={list(captured.symlink_sources)} "
        f"errors={list(captured.read_errors)}"
    )
    trees, failures = collector.parse_python_sources(list(captured.files))
    assert not failures, f"生产 Python 解析失败,fan-out 口径失真:{failures}"
    build = graph_tools.build_backend_import_graph(trees)
    assert not build.collisions, f"模块名冲突,import 图不可信:{build.collisions}"
    return build.graph


def test_pool_fan_out_stays_under_ceiling() -> None:
    graph = _import_graph()
    targets = sorted(graph.get(POOL, set()))
    assert len(targets) <= FAN_OUT_CEILING, (
        f"{POOL} 的模块级 fan-out={len(targets)} 超过 {FAN_OUT_CEILING}"
        f"(合同 ceiling=40,超了该项直接 0 分)。"
        "修法:把重依赖的 import 语句下沉到兄弟文件并在 pool 保名 re-export;"
        "注意把 import 挪进函数体对本指标无效——lazy import 同样计边。"
        f"\n当前依赖:{targets}"
    )


@pytest.mark.parametrize("sibling", SIBLINGS)
def test_sibling_does_not_import_back_into_pool_scc(sibling: str) -> None:
    """兄弟文件反向 import 会把自己拽进 pool↔pool_enrich 那个 SCC,撞环棘轮。"""
    graph = _import_graph()
    assert sibling in graph, f"{sibling} 不在 import 图里(文件被删/改名?)"
    forbidden = {POOL, "app.domains.kol.pool_enrich"} & graph[sibling]
    assert not forbidden, f"{sibling} 反向 import 了 {sorted(forbidden)},会新增成环模块"


@pytest.mark.parametrize("name", REEXPORTED_NAMES)
def test_pool_facade_keeps_reexported_names(name: str) -> None:
    """下沉后 ``pool.<name>`` 必须还在:外部 import 与 monkeypatch 路径逐字不变。"""
    from app.domains.kol import pool as kol_pool

    assert hasattr(kol_pool, name), f"pool 门面丢了对外符号 {name}"


def test_industry_adapters_source_is_a_pure_reexport() -> None:
    """适配器文件只做模块级 re-export;混进逻辑就该重新评估它的 fan-out 归属。"""
    path = ROOT / "backend" / "app" / "domains" / "kol" / "pool_industry_adapters.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    unexpected = [
        type(node).__name__
        for node in tree.body
        if not isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.Expr))
    ]
    assert not unexpected, f"pool_industry_adapters 出现非 re-export 语句:{unexpected}"


def test_detail_sources_resolve_lazily_so_source_patches_still_win(monkeypatch) -> None:
    """reader 必须每次调用重新 from-import,否则源模块 monkeypatch 会静默失效。

    ``tests/test_kol_pool_detail_bundle_limits.py`` 打的是
    ``eleven_dimensions.load_persisted_dimensions_11`` / ``cache_repo.
    get_analysis_cache_entries_for_targets`` 这类**源模块属性**。如果兄弟文件在模块级
    绑定了这些函数对象,补丁就只会改到源模块、改不到 detail_bundle 实际调用的那个。
    """
    from app.domains.analysis import cache_repo
    from app.domains.kol import audience_language, creator_gear, eleven_dimensions
    from app.domains.kol import pool_detail_sources as sources

    sentinel = object()

    monkeypatch.setattr(eleven_dimensions, "load_persisted_dimensions_11", sentinel)
    assert sources.dimensions_reader() is sentinel

    monkeypatch.setattr(cache_repo, "get_analysis_cache_entries_for_targets", sentinel)
    assert sources.analysis_cache_reader() is sentinel

    monkeypatch.setattr(creator_gear, "aggregate_creator_gear", sentinel)
    assert sources.creator_gear_helpers()[0] is sentinel

    monkeypatch.setattr(audience_language, "audience_language_for_kol", sentinel)
    assert sources.audience_language_reader() is sentinel


def test_detail_sources_readers_return_the_documented_callables() -> None:
    """reader 返回值要和 pool 原来的 import 名字一一对上(顺序也算契约)。"""
    from app.domains.kol import pool_detail_sources as sources

    assert sources.llm_deep_reader().__name__ == "get_kol_llm_deep_analysis"
    build_readiness, evidence_quality, load_sample = sources.readiness_helpers()
    assert build_readiness.__name__ == "build_analysis_readiness"
    assert evidence_quality.__name__ == "evidence_quality_projection"
    assert load_sample.__name__ == "load_readiness_video_evidence"
    aggregate_gear, gear_from_text = sources.creator_gear_helpers()
    assert aggregate_gear.__name__ == "aggregate_creator_gear"
    assert gear_from_text.__name__ == "gear_from_text"
