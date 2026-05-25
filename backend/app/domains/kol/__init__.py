"""KOL domain facade."""

from app.domains.kol.account import (
    analyze_account_for_request,
    dossier_for_request,
    scan_account_for_request,
)
from app.domains.kol.claims import (
    claim,
    list_claims,
    list_kols,
    reassign,
    release,
    update_kol_manual,
)
from app.domains.kol.claim_payloads import claim_payload
from app.domains.kol.competitor_detector import (
    batch_evaluate_kol_pool,
    ensure_competitor_relation_schema,
    evaluate_kol_competitor_relation,
    evaluate_kol_competitors,
    get_persisted_kol_competitors,
    persist_competitor_relations,
    persisted_competitor_dashboard,
)
from app.domains.kol.contacts import add_contact, contact_rows_for_request
from app.domains.kol.decisions import (
    create_decision,
    create_followup,
    list_decisions,
    list_followups,
)
from app.domains.kol.identity import dedup_key, normalize_handle, normalize_platform
from app.domains.kol.lookup import lookup_with_context
from app.domains.kol.natural_search import _natural_search_payload as natural_search_payload
from app.domains.kol.profile import profile_with_dossier
from app.domains.kol.profile_payloads import assessment_for_request, product_fit_for_request

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
