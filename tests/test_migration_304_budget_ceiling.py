"""Migration 304: 1000 美元每月综合上限 + 12 个无 caps 行 scope 的补种契约。

为什么这个文件存在
------------------
用户裁决把综合上限从 3000 压到 1000。一旦总闸降到 1000,任何**高于总闸的子闸**就是
死配置 —— 它永远不会先于总闸触发,却会让运维误以为那一路还有额度。现网采样里恰好有
三行处在这个状态(monthly_total 自己、provider:gemini、以及带 cron: 前缀因而是**日窗**
的 cron:vkpi_analysis_worker)。下面的断言把「压下来」和「压到哪」都钉死。

第二段钉的是补种的**边界**,而不只是补种本身:

* 补种会把一个 scope 从「不设防」变成「受闸管」。额度给低了会**制造**出原本不存在的
  拦截,所以每一行的额度都必须显著高于实测量级 —— 断言逐条核对播种额度。
* 三个 scope 刻意不补(``cron:dealer_web_verify`` / ``vkpi_product_persona`` /
  ``vkpi_kol_memory_summary``),每一个都有反向证据:前者被迁移 296 的评审显式排除并
  由 tests/test_migration_296_budget_scope_registry.py 钉成断言,后两者分别是离线批跑
  脚本和代码里写明「record-only,绝不硬拦」的契约。这里把「不补」也钉住,免得后人
  顺手补齐反而推翻上一轮结论。
* 本批 12 个 scope 全部**不走** ``require_configured=True`` 路径,所以缺 caps 行不会像
  303 那批一样造成 100 percent 降级。这条与 tests/test_budget_scope_seed_coverage.py
  的严格调用方集合互不重叠,下面也断言了这一点。
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "migrations"
UP_PATH = MIGRATIONS_DIR / "304_vkpi_budget_ceiling_1000.sql"
DOWN_PATH = MIGRATIONS_DIR / "304_vkpi_budget_ceiling_1000_down.sql"
UP = UP_PATH.read_text(encoding="utf-8")
DOWN = DOWN_PATH.read_text(encoding="utf-8")

#: 用户裁决的新综合上限。
MONTHLY_TOTAL_CAP = "1000.00"

#: (scope, 新 cap, 旧 cap) —— 现网采样中 cap_usd 大于等于 1000 的全部三行。
TIGHTENED = (
    ("monthly_total", "1000.00", "3000.00"),
    ("provider:gemini", "400.00", "1500.00"),
    ("cron:vkpi_analysis_worker", "50.00", "1500.00"),
)

#: 迁移 304 补种的 12 行:scope -> (cap, fallback_action)。
SEEDED = {
    "cron:kol_account_dossier": ("2.00", "skip_llm_keep_last"),
    "cron:vkpi_discovery_localize": ("2.00", "keep_original_text"),
    "cron:audience_avatar": ("2.00", "fallback_to_rule_v0"),
    "cron:audit_deep_score": ("5.00", "fallback_to_rule_v0"),
    "cron:audit_vision_fallback": ("2.00", "fallback_to_rule_v0"),
    "cron:intelligence_market": ("2.00", "fallback_to_evidence_only"),
    "cron:intelligence_brand": ("2.00", "fallback_to_evidence_only"),
    "cron:lens_compare": ("2.00", "skip_llm_keep_last"),
    "cron:lens_monitor": ("2.00", "skip_llm_keep_last"),
    "cron:brand_analysis": ("2.00", "fallback_to_evidence_only"),
    "projects:invoice_extract": ("10.00", "block_invoice_extract"),
    "marketing_brain_skill": ("10.00", "rule_mode_dry_run"),
}

#: 有反向证据、刻意不补的三个 scope。
DELIBERATELY_UNSEEDED = (
    "cron:dealer_web_verify",
    "vkpi_product_persona",
    "vkpi_kol_memory_summary",
)

#: 每个补种 scope 的真实调用点。scope 字面量漂了(比如调用方只传 purpose、
#: 却按 cost_tag 的拼法去种),种下的行就永远不会被命中 —— 那正是迁移 303 修过的
#: 一类病。这里逐条回钉源码,让漂移在测试期就暴露。
CALL_SITES = {
    "cron:kol_account_dossier": (
        "backend/app/services/kol/account_dossier.py",
        'purpose="kol_account_dossier"',
    ),
    "cron:vkpi_discovery_localize": (
        "backend/app/domains/kol/profile_discovery_localize.py",
        'purpose="vkpi_discovery_localize"',
    ),
    "cron:audience_avatar": (
        "backend/app/domains/kol/audience_avatar_llm.py",
        'purpose="audience_avatar"',
    ),
    "cron:audit_deep_score": (
        "backend/app/services/ai/analyzers/claude_text.py",
        'cost_tag="cron:audit_deep_score"',
    ),
    "cron:audit_vision_fallback": (
        "backend/app/services/ai/analyzers/claude_vision_images.py",
        'cost_tag="cron:audit_vision_fallback"',
    ),
    "cron:intelligence_market": (
        "backend/app/services/intelligence/market.py",
        'purpose="intelligence_market"',
    ),
    "cron:intelligence_brand": (
        "backend/app/services/intelligence/brand.py",
        'purpose="intelligence_brand"',
    ),
    "cron:lens_compare": (
        "backend/app/services/intelligence/lens_compare.py",
        'purpose="lens_compare"',
    ),
    "cron:lens_monitor": (
        "backend/app/services/intelligence/lens_monitor.py",
        'purpose="lens_monitor"',
    ),
    "cron:brand_analysis": (
        "backend/app/api/routers/brand_analysis.py",
        'purpose="brand_analysis"',
    ),
    "projects:invoice_extract": (
        "backend/app/domains/projects/contract_assist.py",
        'cost_tag="projects:invoice_extract"',
    ),
    "marketing_brain_skill": (
        "backend/app/domains/marketing_brain/skill_orchestrator.py",
        '_BUDGET_SCOPE = "marketing_brain_skill"',
    ),
}


def test_every_cap_above_the_new_total_is_tightened_and_idempotent() -> None:
    """三条高于新总闸的 cap 必须被压下来,且守卫是「只收紧」而不是「等于旧值」。"""

    for scope, new_cap, _old_cap in TIGHTENED:
        pattern = (
            rf"UPDATE vkpi_provider_budget_caps\s+SET cap_usd = {re.escape(new_cap)}\s+"
            rf"WHERE scope = '{re.escape(scope)}'\s+AND cap_usd > {re.escape(new_cap)};"
        )
        assert re.search(pattern, UP), f"missing monotone tightening for {scope}"

    # 守卫必须是 `cap_usd > 目标值`:既保证重跑幂等(第二次无行命中),又保证只会调低、
    # 永不调高 —— 哪怕现网额度已经漂移过,用户的裁令也一定落地。
    assert "AND cap_usd = " not in UP, "UP must not guard on an exact legacy value"


def test_no_seeded_cap_may_exceed_the_new_monthly_total() -> None:
    """子闸高于总闸就是死配置。播种的每一行额度都必须严格低于 1000。"""

    ceiling = float(MONTHLY_TOTAL_CAP)
    for scope, (cap, _fallback) in SEEDED.items():
        assert float(cap) < ceiling, f"{scope} cap {cap} is not below the total ceiling"
    for scope, new_cap, _old in TIGHTENED:
        if scope == "monthly_total":
            continue
        assert float(new_cap) < ceiling, f"{scope} cap {new_cap} is not below the total"


def test_seeds_are_conflict_safe_and_start_from_zero_spend() -> None:
    normalized = " ".join(UP.lower().split())
    assert "on conflict (scope) do nothing" in normalized
    # 绝不 DO UPDATE:运维手建 / 手调过额度的同名行必须一行不动。
    assert "do update" not in normalized
    for scope, (cap, fallback) in SEEDED.items():
        pattern = (
            rf"\('{re.escape(scope)}', {re.escape(cap)}, 0, 0\.80, 1\.00, NULL, "
            rf"'{re.escape(fallback)}',"
        )
        assert re.search(pattern, UP), f"missing seed row for {scope}"
        assert f'"seeded_by":"migration_304"' in UP
    # current_spend 一律 0:台账 vkpi_ai_cost_ledger 是真账本,迁移绝不回填历史花费。
    assert UP.count("migration_304") >= len(SEEDED)


def test_seeded_scopes_match_their_real_call_sites() -> None:
    """scope 字面量必须与调用方实际产生的 cost scope 一致。

    口径见 backend/app/platform/llm_gateway.py ``_cost_scope_for_purpose``:
    传了 cost_tag 就原样使用;只传 purpose 则拼成 ``cron:`` 前缀。
    """

    for scope, (relative_path, needle) in CALL_SITES.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert needle in source, f"stale call site for {scope} in {relative_path}"
        if needle.startswith("purpose="):
            purpose = needle.split('"')[1]
            assert scope == f"cron:{purpose}", (
                f"{scope} is seeded bare but its caller only passes purpose "
                f"-> the gateway will look up cron:{purpose}"
            )
    assert set(CALL_SITES) == set(SEEDED)


def test_deliberately_unseeded_scopes_stay_unseeded() -> None:
    """三个有反向证据的 scope 不得被顺手补齐。"""

    for scope in DELIBERATELY_UNSEEDED:
        assert f"('{scope}'," not in UP, f"{scope} was excluded on purpose, do not seed it"
        # 排除理由必须留在迁移注释里,否则后人只会看到「少了一个」。
        assert scope in UP, f"{scope} exclusion must stay documented in the migration"


def test_unseeded_scopes_are_not_strict_callers() -> None:
    """刻意不补的三个 scope 必须确实不走 require_configured=True,否则「不补」= 拦死。

    与 tests/test_budget_scope_seed_coverage.py 的严格集合互不重叠:那个文件管
    ``llm_production.generate_json`` 的严格调用方(缺行即 100 percent 降级),
    本批与它一个都不重合。
    """

    from tests import test_budget_scope_seed_coverage as strict_guard

    strict_map, _unresolved = strict_guard._scan_generate_json_call_sites()
    strict_scopes = set(strict_map)
    for scope in (*DELIBERATELY_UNSEEDED, *SEEDED):
        assert scope not in strict_scopes, (
            f"{scope} IS a strict caller -- a missing caps row hard-blocks it, "
            "so it must be seeded rather than reasoned about as record-only"
        )


def test_down_restores_old_caps_and_removes_only_its_own_rows() -> None:
    for scope, new_cap, old_cap in TIGHTENED:
        pattern = (
            rf"UPDATE vkpi_provider_budget_caps\s+SET cap_usd = {re.escape(old_cap)}\s+"
            rf"WHERE scope = '{re.escape(scope)}'\s+AND cap_usd = {re.escape(new_cap)};"
        )
        assert re.search(pattern, DOWN), f"missing restore for {scope}"

    normalized = " ".join(DOWN.lower().split())
    # 出身守卫用 strpos,不用 LIKE(百分号字面量会撞上 compat 的转义陷阱)。
    assert "strpos(metadata_json, 'migration_304') > 0" in normalized
    assert "like '" not in normalized, "provenance guard must use strpos, not LIKE"
    for scope in SEEDED:
        assert f"'{scope}'" in DOWN
    assert "delete from schema_migrations" in normalized
    assert "304_vkpi_budget_ceiling_1000.sql" in normalized


def test_migration_sql_avoids_the_compat_placeholder_traps() -> None:
    """ASCII 问号会被 compat 适配器当占位符,百分号会撞上 LIKE 转义。两者一个都不许有。"""

    for path, source in ((UP_PATH, UP), (DOWN_PATH, DOWN)):
        assert "?" not in source, f"{path.name} contains an ASCII question mark"
        assert "%" not in source, f"{path.name} contains a percent literal"
        assert "BEGIN;" not in source and "COMMIT;" not in source, (
            f"{path.name} must not own its transaction"
        )
