from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_EXPORTS = {
    "add_contact": ("app.domains.kol.contacts", "add_contact"),
    "analyze_account_for_request": ("app.domains.kol.account", "analyze_account_for_request"),
    "assessment_for_request": ("app.domains.kol.profile_payloads", "assessment_for_request"),
    "batch_evaluate_kol_pool": ("app.domains.kol.competitor_detector", "batch_evaluate_kol_pool"),
    "claim": ("app.domains.kol.claims", "claim"),
    "claim_payload": ("app.domains.kol.claim_payloads", "claim_payload"),
    "create_decision": ("app.domains.kol.decisions", "create_decision"),
    "create_followup": ("app.domains.kol.decisions", "create_followup"),
    "contact_rows_for_request": ("app.domains.kol.contacts", "contact_rows_for_request"),
    "dossier_for_request": ("app.domains.kol.account", "dossier_for_request"),
    "dedup_key": ("app.domains.kol.identity", "dedup_key"),
    "ensure_competitor_relation_schema": (
        "app.domains.kol.competitor_detector",
        "ensure_competitor_relation_schema",
    ),
    "evaluate_kol_competitor_relation": (
        "app.domains.kol.competitor_detector",
        "evaluate_kol_competitor_relation",
    ),
    "evaluate_kol_competitors": ("app.domains.kol.competitor_detector", "evaluate_kol_competitors"),
    "get_persisted_kol_competitors": (
        "app.domains.kol.competitor_detector",
        "get_persisted_kol_competitors",
    ),
    "list_decisions": ("app.domains.kol.decisions", "list_decisions"),
    "list_followups": ("app.domains.kol.decisions", "list_followups"),
    "list_claims": ("app.domains.kol.claims", "list_claims"),
    "list_kols": ("app.domains.kol.claims", "list_kols"),
    "lookup_with_context": ("app.domains.kol.lookup", "lookup_with_context"),
    "natural_search_payload": ("app.domains.kol.natural_search", "_natural_search_payload"),
    "normalize_handle": ("app.domains.kol.identity", "normalize_handle"),
    "normalize_platform": ("app.domains.kol.identity", "normalize_platform"),
    "persist_competitor_relations": (
        "app.domains.kol.competitor_detector",
        "persist_competitor_relations",
    ),
    "persisted_competitor_dashboard": (
        "app.domains.kol.competitor_detector",
        "persisted_competitor_dashboard",
    ),
    "product_fit_for_request": ("app.domains.kol.profile_payloads", "product_fit_for_request"),
    "profile_with_dossier": ("app.domains.kol.profile", "profile_with_dossier"),
    "reassign": ("app.domains.kol.claims", "reassign"),
    "release": ("app.domains.kol.claims", "release"),
    "scan_account_for_request": ("app.domains.kol.account", "scan_account_for_request"),
    "update_kol_manual": ("app.domains.kol.claims", "update_kol_manual"),
}

EXPECTED_ALL = [
    "add_contact",
    "analyze_account_for_request",
    "assessment_for_request",
    "batch_evaluate_kol_pool",
    "claim",
    "claim_payload",
    "create_decision",
    "create_followup",
    "contact_rows_for_request",
    "dossier_for_request",
    "dedup_key",
    "ensure_competitor_relation_schema",
    "evaluate_kol_competitor_relation",
    "evaluate_kol_competitors",
    "get_persisted_kol_competitors",
    "list_decisions",
    "list_followups",
    "list_claims",
    "list_kols",
    "lookup_with_context",
    "natural_search_payload",
    "normalize_handle",
    "normalize_platform",
    "persist_competitor_relations",
    "persisted_competitor_dashboard",
    "product_fit_for_request",
    "profile_with_dossier",
    "reassign",
    "release",
    "scan_account_for_request",
    "update_kol_manual",
]

EXPECTED_IMPLICIT_SUBMODULES = [
    "account",
    "claim_payloads",
    "claims",
    "competitor_detector",
    "contacts",
    "decisions",
    "identity",
    "lookup",
    "natural_search",
    "profile",
    "profile_payloads",
]


def _run_probe(code: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(ROOT / "backend"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "VKPI_SKIP_DOTENV": "1",
            "ENVIRONMENT": "test",
            "V2_PRODUCTION_MODE": "0",
            "ENABLE_BROWSER": "0",
            "ENABLE_SCHEDULER": "0",
            "REDIS_URL": "",
        }
    )
    return subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_facade_declares_all_31_public_exports_without_loading_them() -> None:
    expected_exports_json = json.dumps(EXPECTED_EXPORTS)
    expected_all_json = json.dumps(EXPECTED_ALL)
    completed = _run_probe(
        f"""
import json
import sys
import app.domains.kol as kol

expected_exports = json.loads({expected_exports_json!r})
expected_all = json.loads({expected_all_json!r})
assert kol.__all__ == expected_all
assert len(kol.__all__) == len(set(kol.__all__)) == 31
assert set(kol._LAZY_EXPORTS) == set(expected_exports)
assert set(kol.__all__).issubset(dir(kol))
assert not any(module_name in sys.modules for module_name, _attribute in expected_exports.values())

try:
    getattr(kol, "definitely_not_a_kol_export")
except AttributeError:
    pass
else:
    raise AssertionError("unknown facade attribute did not fail closed")

print(json.dumps({{"status": "ok"}}, sort_keys=True))
"""
    )

    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {"status": "ok"}


@pytest.mark.parametrize(
    ("public_name", "target"),
    EXPECTED_EXPORTS.items(),
    ids=EXPECTED_EXPORTS,
)
def test_each_facade_export_resolves_in_a_cold_process(
    public_name: str,
    target: tuple[str, str],
) -> None:
    module_name, attribute_name = target
    completed = _run_probe(
        f"""
import importlib
import app.domains.kol as kol

actual = getattr(kol, {public_name!r})
expected = getattr(importlib.import_module({module_name!r}), {attribute_name!r})
assert actual is expected
assert vars(kol)[{public_name!r}] is expected
print("ok")
"""
    )

    assert completed.stdout.strip().splitlines()[-1] == "ok"


def test_import_star_resolves_all_exports_from_a_cold_process() -> None:
    expected_exports_json = json.dumps(EXPECTED_EXPORTS)
    completed = _run_probe(
        f"""
import importlib
import json

expected_exports = json.loads({expected_exports_json!r})
namespace = {{}}
exec("from app.domains.kol import *", namespace)
star_exports = {{key: value for key, value in namespace.items() if not key.startswith("__")}}
assert set(star_exports) == set(expected_exports)
for public_name, (module_name, attribute_name) in expected_exports.items():
    assert star_exports[public_name] is getattr(importlib.import_module(module_name), attribute_name)
print(json.dumps({{"exports": len(star_exports), "status": "ok"}}, sort_keys=True))
"""
    )

    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "exports": 31,
        "status": "ok",
    }


@pytest.mark.parametrize("submodule_name", EXPECTED_IMPLICIT_SUBMODULES)
def test_eager_facade_implicit_submodule_attributes_remain_lazy_compatible(
    submodule_name: str,
) -> None:
    completed = _run_probe(
        f"""
import importlib
import sys
import app.domains.kol as kol

module_name = f"app.domains.kol.{{{submodule_name!r}}}"
assert module_name not in sys.modules
assert getattr(kol, {submodule_name!r}) is importlib.import_module(module_name)
assert vars(kol)[{submodule_name!r}] is sys.modules[module_name]
print("ok")
"""
    )

    assert completed.stdout.strip().splitlines()[-1] == "ok"


def test_leaf_import_keeps_unrelated_account_and_provider_graph_unloaded() -> None:
    completed = _run_probe(
        """
import json
import sys
import app.domains.kol as kol
from app.domains.kol import signature_profile
from app.domains.kol.discovery_filters import _reach_display_state

heavy_modules = [
    "app.domains.kol.account",
    "app.domains.kol.claims",
    "app.domains.kol.profile_payloads",
    "app.services.kol.account_dossier",
    "app.services.ai.clients.gemini_client",
    "app.services.ai.clients.openai_client",
    "app.services.scraping.apify",
]
print(json.dumps({
    "leaf_available": callable(signature_profile.shooting_style_summary),
    "submodule_import_compatible": kol.signature_profile is signature_profile,
    "reach_filter_available": callable(_reach_display_state),
    "unexpected_modules": [name for name in heavy_modules if name in sys.modules],
}, sort_keys=True))
"""
    )

    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "leaf_available": True,
        "reach_filter_available": True,
        "submodule_import_compatible": True,
        "unexpected_modules": [],
    }


@pytest.mark.parametrize("facade_first", [False, True])
def test_recommendation_product_fit_cycle_is_import_order_independent(facade_first: bool) -> None:
    imports = (
        "from app.domains.kol import product_fit_for_request\n"
        "import app.domains.recommendations.new_launch_match as match"
        if facade_first
        else
        "import app.domains.recommendations.new_launch_match as match\n"
        "from app.domains.kol import product_fit_for_request"
    )
    completed = _run_probe(
        f"""
{imports}
assert callable(product_fit_for_request)
assert callable(match.build_new_launch_match_preview)
print("ok")
"""
    )

    assert completed.stdout.strip().splitlines()[-1] == "ok"
