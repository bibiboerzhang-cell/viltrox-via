"""apify_jobs_worker 编排层依赖面棘轮(2026-08-31 fan-out 刀 50→27)。

合同 internal_fan_out_max:target=20 / ceiling=40 / weight=0.10。app.workers.apify_jobs_worker
曾是全库非豁免最大(50),单它一个就把该项按 0 分计。刀法是「按内聚度把叶子依赖转给本就在用
它的那个兄弟」,worker 本体只留编排所需的 core/db/家族兄弟。

本文件把三件事钉死,防止它悄悄长回去:
1. 编排层白名单——worker 只许直接 import app.core.* / app.db.* / app.workers.*;
2. fan-out 棘轮——按 collector 口径(含祖先包边)实测,只减不增;
3. namespace 契约 parity——re-export 出去的名字必须还在,且仍绑定原叶子的同一个对象。

第 3 条是钱袋子红线:worker 用 ``namespace=globals()`` 把这些名字交给
apify_jobs_worker_execution / apify_jobs_worker_runtime,少一个名字就是运行期 KeyError,
绑错对象就是预算围栏/provider 认领失效。它必须由「对象身份」而不是「名字存在」来证明。
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE = "app.workers.apify_jobs_worker"
RELATIVE_PATH = "backend/app/workers/apify_jobs_worker.py"

# 编排层只许直接依赖这三支。domains/platform/services 的叶子一律经兄弟转出。
ORCHESTRATION_PREFIXES = ("app.core", "app.db", "app.workers")

# 实测值 27(2026-08-31)。留 3 格给编排本身的正常演进;够不上就该再拆一刀,而不是抬棘轮。
FAN_OUT_RATCHET = 30

# re-export 名 -> (真源模块, 真源属性名)。worker 侧属性必须 is 同一个对象。
REEXPORT_PARITY: tuple[tuple[str, str, str], ...] = (
    ("budget_guard", "app.domains.costs", "budget_guard"),
    ("llm_gateway", "app.platform", "llm_gateway"),
    ("ApifyBudgetBlocked", "app.platform.apify_budget", "ApifyBudgetBlocked"),
    ("ApifyExecutionClaimBlocked", "app.platform.apify_budget", "ApifyExecutionClaimBlocked"),
    ("ApifyProviderReplayBlocked", "app.platform.apify_budget", "ApifyProviderReplayBlocked"),
    ("acquire_provider_execution_claim", "app.platform.apify_budget", "acquire_provider_execution_claim"),
    ("apify_execution_context", "app.platform.apify_budget", "apify_execution_context"),
    ("finalize_provider_execution_claim", "app.platform.apify_budget", "finalize_provider_execution_claim"),
    ("LOCAL_EVALUATION_CACHE_DERIVE_METHOD", "app.platform.llm_local_evaluation", "LOCAL_EVALUATION_CACHE_DERIVE_METHOD"),
    ("LOCAL_EVALUATION_DERIVE_METHOD", "app.platform.llm_local_evaluation", "LOCAL_EVALUATION_DERIVE_METHOD"),
    ("LOCAL_EVALUATION_EXECUTION_CLASS", "app.platform.llm_local_evaluation", "LOCAL_EVALUATION_EXECUTION_CLASS"),
    ("verify_job_local_evaluation_capability", "app.platform.llm_local_evaluation", "verify_job_local_evaluation_capability"),
    ("download_direct_video_url", "app.services.media.video_download", "download_direct_video_url"),
    ("cache_local_video_file", "app.domains.media.cache", "cache_local_video_file"),
    ("_content_url_video_id", "app.domains.kol.url_deep_crawl_helpers", "_video_id"),
    ("upsert_deep_analysis_from_final_v1_cache", "app.domains.kol.final_v1_extract", "upsert_deep_analysis_from_final_v1_cache"),
    ("upsert_account_dossier_extract", "app.domains.kol.account_dossier_extract", "upsert_account_dossier_extract"),
    ("project_contracts", "app.domains.projects", "contracts"),
    ("project_retrospective", "app.domains.projects", "retrospective_aggregate"),
    ("kol_profile_discovery", "app.domains.kol", "profile_discovery"),
    ("kol_search_sessions", "app.domains.kol", "search_sessions"),
    ("LOCAL_EXCLUSIVE_JOB_TYPES", "app.domains.local_workers.registry", "SAFE_TASK_TYPES"),
)

# namespace=globals() 的下游取用键(apify_jobs_worker_execution / _runtime),少一个即运行期炸。
NAMESPACE_CONTRACT_KEYS = (
    "ApifyBudgetBlocked",
    "ApifyExecutionClaimBlocked",
    "ApifyProviderReplayBlocked",
    "acquire_provider_execution_claim",
    "apify_execution_context",
    "finalize_provider_execution_claim",
    "llm_gateway",
    "verify_job_local_evaluation_capability",
    "LOCAL_EVALUATION_CACHE_DERIVE_METHOD",
    "LOCAL_EVALUATION_DERIVE_METHOD",
    "LOCAL_EVALUATION_EXECUTION_CLASS",
)


def _imported_app_modules() -> set[str]:
    """worker 源码里出现的所有 app.* import 目标(含函数体内的 lazy import)。"""
    tree = ast.parse((ROOT / RELATIVE_PATH).read_text(encoding="utf-8"), filename=RELATIVE_PATH)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith("app."))
        elif isinstance(node, ast.ImportFrom):
            # level>0 的相对 import 落在 app.workers 包内,天然合规。
            if node.level == 0 and (node.module or "").startswith("app."):
                found.add(node.module or "")
    return found


def test_worker_only_imports_orchestration_layers() -> None:
    offenders = sorted(
        module
        for module in _imported_app_modules()
        if not module.startswith(ORCHESTRATION_PREFIXES)
    )
    assert not offenders, (
        "apify_jobs_worker 是编排层,只许直接 import app.core/app.db/app.workers。"
        " 新叶子请交给「本就在用它的那个兄弟」转出(prep/media/gemini/session/handlers/config),"
        f" worker 侧保留同名 re-export:{offenders}"
    )


def test_worker_fan_out_stays_under_ratchet() -> None:
    """按 collector 口径实测(含祖先包边),只减不增。"""
    from scripts import vkpi_engineering_health_graph as graph_tools

    trees: dict[str, ast.Module] = {}
    for path in sorted((ROOT / "backend" / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(ROOT))
        trees[relative] = ast.parse(path.read_text(encoding="utf-8"), filename=relative)

    graph = graph_tools.build_backend_import_graph(trees).graph
    fan_out = len(graph[MODULE])
    assert fan_out <= FAN_OUT_RATCHET, (
        f"{MODULE} fan-out 涨到 {fan_out}(棘轮 {FAN_OUT_RATCHET})。"
        f" 合同 ceiling=40,这个模块曾经 50 分把整项按 0 计——请再拆一刀,别抬棘轮:"
        f" {sorted(graph[MODULE])}"
    )


@pytest.mark.parametrize(("attribute", "source_module", "source_attribute"), REEXPORT_PARITY)
def test_reexport_binds_the_same_object_as_its_true_source(
    attribute: str, source_module: str, source_attribute: str
) -> None:
    """转一跳搬运工不许换对象:worker.<NAME> 必须 is 原叶子的那一个。"""
    worker = importlib.import_module(MODULE)
    origin = importlib.import_module(source_module)

    assert hasattr(worker, attribute), (
        f"{MODULE}.{attribute} 不见了。它是对外符号/namespace 契约的一部分,"
        " 拆分只许换 import 来源,不许删名字。"
    )
    assert getattr(worker, attribute) is getattr(origin, source_attribute), (
        f"{MODULE}.{attribute} 绑错了对象,应当 is {source_module}.{source_attribute}。"
        " 单一真源仍是原叶子,兄弟模块只是搬运工。"
    )


def test_namespace_contract_keys_are_all_present_in_worker_globals() -> None:
    """worker 用 namespace=globals() 交付这些键;少一个就是下游运行期 KeyError。"""
    worker = importlib.import_module(MODULE)
    namespace = vars(worker)
    missing = sorted(key for key in NAMESPACE_CONTRACT_KEYS if key not in namespace)
    assert not missing, (
        "namespace=globals() 契约缺键,apify_jobs_worker_execution / _runtime 会在运行期炸:"
        f"{missing}"
    )


def test_pinned_delegation_shims_stay_on_the_worker_module() -> None:
    """三个被源码守卫钉死的名必须留在 worker 本体(call-time 委派壳)。"""
    worker = importlib.import_module(MODULE)
    for name in ("_block_job", "_finish_skipped", "_provider_retry_delay_seconds"):
        assert callable(getattr(worker, name, None)), f"{MODULE}.{name} 必须留在 worker 本体"
