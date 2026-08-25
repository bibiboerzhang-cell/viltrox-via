"""KOL domain facade.

Public convenience exports are resolved lazily.  Importing an independent KOL
submodule must not initialize the account, provider, or model-client graph just
because Python first executes this package module.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
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


_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "add_contact": ("app.domains.kol.contacts", "add_contact"),
    "analyze_account_for_request": (
        "app.domains.kol.account",
        "analyze_account_for_request",
    ),
    "assessment_for_request": (
        "app.domains.kol.profile_payloads",
        "assessment_for_request",
    ),
    "batch_evaluate_kol_pool": (
        "app.domains.kol.competitor_detector",
        "batch_evaluate_kol_pool",
    ),
    "claim": ("app.domains.kol.claims", "claim"),
    "claim_payload": ("app.domains.kol.claim_payloads", "claim_payload"),
    "create_decision": ("app.domains.kol.decisions", "create_decision"),
    "create_followup": ("app.domains.kol.decisions", "create_followup"),
    "contact_rows_for_request": (
        "app.domains.kol.contacts",
        "contact_rows_for_request",
    ),
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
    "evaluate_kol_competitors": (
        "app.domains.kol.competitor_detector",
        "evaluate_kol_competitors",
    ),
    "get_persisted_kol_competitors": (
        "app.domains.kol.competitor_detector",
        "get_persisted_kol_competitors",
    ),
    "list_decisions": ("app.domains.kol.decisions", "list_decisions"),
    "list_followups": ("app.domains.kol.decisions", "list_followups"),
    "list_claims": ("app.domains.kol.claims", "list_claims"),
    "list_kols": ("app.domains.kol.claims", "list_kols"),
    "lookup_with_context": ("app.domains.kol.lookup", "lookup_with_context"),
    "natural_search_payload": (
        "app.domains.kol.natural_search",
        "_natural_search_payload",
    ),
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
    "product_fit_for_request": (
        "app.domains.kol.profile_payloads",
        "product_fit_for_request",
    ),
    "profile_with_dossier": ("app.domains.kol.profile", "profile_with_dossier"),
    "reassign": ("app.domains.kol.claims", "reassign"),
    "release": ("app.domains.kol.claims", "release"),
    "scan_account_for_request": (
        "app.domains.kol.account",
        "scan_account_for_request",
    ),
    "update_kol_manual": ("app.domains.kol.claims", "update_kol_manual"),
}

# The eager facade also left its imported child modules on the package object
# as an incidental compatibility surface (for example ``kol.account``).  Keep
# those attributes available without paying their import cost until somebody
# actually asks for one of them.
_LAZY_SUBMODULES: dict[str, str] = {
    name: f"app.domains.kol.{name}"
    for name in (
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
    )
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is not None:
        module_name, attribute = target
        value = getattr(import_module(module_name), attribute)
    elif module_name := _LAZY_SUBMODULES.get(name):
        value = import_module(module_name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_LAZY_SUBMODULES))
