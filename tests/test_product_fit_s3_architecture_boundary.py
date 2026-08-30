from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from app.domains.kol import product_fit, product_fit_persistence
from app.domains.kol.product_fit_repository import SqlProductFitRepository
from app.domains.recommendations import new_launch_match_helpers as legacy_queries


ROOT = Path(__file__).resolve().parents[1]
S3_PRODUCTION_FILES = (
    "backend/app/domains/kol/product_fit.py",
    "backend/app/domains/kol/product_fit_helpers.py",
    "backend/app/domains/kol/product_fit_persistence.py",
    "backend/app/domains/kol/product_fit_repository.py",
    "backend/app/domains/kol/product_fit_reason_adapter.py",
)
S3_SHARED_FILES = (
    "backend/app/shared/product_fit_contracts.py",
    "backend/app/shared/product_fit_policy.py",
    "backend/app/shared/product_fit_rendering.py",
)


class _Rows:
    def __init__(self, rows: Any) -> None:
        self.rows = rows

    def fetchall(self) -> list[Any]:
        return list(self.rows or [])

    def fetchone(self) -> Any:
        if isinstance(self.rows, list):
            return self.rows[0] if self.rows else None
        return self.rows


class _Connection:
    def __init__(self, rows: Any) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        self.calls.append((sql, tuple(params)))
        return _Rows(self.rows)


def _imports(path: Path) -> tuple[list[ast.ImportFrom], list[ast.Import]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return (
        [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)],
        [node for node in ast.walk(tree) if isinstance(node, ast.Import)],
    )


def test_s3_has_no_wildcard_private_recommendation_or_dynamic_import_edges() -> None:
    for relative in (*S3_PRODUCTION_FILES, *S3_SHARED_FILES):
        path = ROOT / relative
        from_imports, direct_imports = _imports(path)
        assert not any(alias.name == "*" for node in from_imports for alias in node.names), relative
        imported_modules = {
            module
            for module in (
                *(node.module or "" for node in from_imports),
                *(alias.name for node in direct_imports for alias in node.names),
            )
            if module
        }
        assert not any(
            module.startswith("app.domains.recommendations")
            for module in imported_modules
        ), relative
        source = path.read_text(encoding="utf-8")
        assert "importlib" not in source
        assert "__import__(" not in source

    for relative in S3_SHARED_FILES:
        from_imports, direct_imports = _imports(ROOT / relative)
        imported_modules = {
            module
            for module in (
                *(node.module or "" for node in from_imports),
                *(alias.name for node in direct_imports for alias in node.names),
            )
            if module
        }
        assert not any(module.startswith("app.domains") for module in imported_modules), relative
        assert not any(module.startswith("app.db") for module in imported_modules), relative
        assert not any(module.startswith("app.platform") for module in imported_modules), relative


def _run_legacy_query(monkeypatch, fn, rows: list[dict[str, Any]], *, postgres: bool, args=()):
    connection = _Connection(rows)
    monkeypatch.setattr(legacy_queries, "get_conn", lambda: connection)
    monkeypatch.setattr(legacy_queries, "is_postgres_runtime", lambda: postgres)
    return fn(*args), connection.calls


@pytest.mark.parametrize("postgres", [False, True])
def test_repository_preserves_legacy_query_bytes_results_and_params(monkeypatch, postgres: bool) -> None:
    pool_row = {
        "id": 1,
        "platform": "youtube",
        "handle": "creator",
        "display_name": "Creator",
        "country": "US",
        "source_ref": "source:1",
        "sync_status": "ready",
        "followers": 100,
        "avg_views": 50,
        "avg_comments": 4,
        "engagement_rate": 0.04,
    }
    pool_row.update(
        {
            "low_reach_flagged": False,
            "contact_has_email": True,
            "contact_has_phone": False,
        }
        if postgres
        else {"raw_platform_data": "{}"}
    )
    cases = (
        (
            legacy_queries._kol_entities,
            "list_kol_entities",
            [{"id": 1, "entity_uid": "kol:1", "display_name": "Creator", "status": "active", "identity_json": "{}", "metadata_json": "{}"}],
            (),
        ),
        (legacy_queries._pool_by_source_ref, "pools_by_source_ref", [pool_row], ()),
        (
            legacy_queries._legacy_entities_by_uid,
            "legacy_entities_by_uid",
            [{"id": 2, "entity_uid": "legacy:1", "weak_label": "", "resolution_decision": "keep"}],
            (),
        ),
        (
            legacy_queries._kol_facts,
            "facts_by_kol",
            [{"id": 3, "entity_id": 1, "fact_type": "country", "fact_value_text": "US", "confidence_score": 1.0, "source_ref": "s", "source_table": "t", "source_id": "3", "fact_json": "{}", "observed_at": "2026-08-29T00:00:00Z"}],
            (),
        ),
        (
            legacy_queries._worked_links,
            "worked_links_by_kol",
            [{"id": 4, "link_uid": "link:1", "source_entity_id": 1, "target_entity_id": 9, "link_type": "worked_on_product", "confidence_score": 1.0, "source_ref": "s", "source_json": "{}", "product_uid": "p", "product_name": "P", "product_key": "p"}],
            (),
        ),
        (
            legacy_queries._product_family_maps,
            "product_family_maps",
            [{"product_id": 9, "product_uid": "p", "product_name": "P", "product_key": "p", "product_metadata_json": "{}", "family_id": 10, "family_uid": "f", "family_name": "F", "family_key": "f", "family_metadata_json": "{}"}],
            (),
        ),
        (
            legacy_queries._target_market_signals,
            "target_market_signals",
            [{"id": 5, "entity_id": 10, "fact_type": "market_signal"}],
            (10,),
        ),
    )

    for legacy_fn, repository_method, rows, args in cases:
        legacy_result, legacy_calls = _run_legacy_query(
            monkeypatch,
            legacy_fn,
            rows,
            postgres=postgres,
            args=args,
        )
        connection = _Connection(rows)
        repository = SqlProductFitRepository(
            lambda: connection,
            lambda: postgres,
        )
        repository_result = getattr(repository, repository_method)(*args)
        assert repository_result == legacy_result
        assert connection.calls == legacy_calls


def test_repository_preserves_product_fit_specific_sql_and_projection() -> None:
    families = [{"id": 1, "entity_uid": "family:1", "display_name": "Family"}]
    connection = _Connection(families)
    repository = SqlProductFitRepository(lambda: connection, lambda: False)
    assert repository.candidate_families() == families
    assert connection.calls == [
        (
            """
        SELECT *
        FROM vkpi_memory_entities
        WHERE entity_type='product_family'
          AND status IN ('active', 'imported')
        ORDER BY display_name, id
        """,
            (),
        )
    ]

    official = [
        {
            "id": 7,
            "target_entity_id": 11,
            "link_type": "official_account_published_product",
        }
    ]
    connection = _Connection(official)
    repository = SqlProductFitRepository(lambda: connection, lambda: False)
    assert repository.official_family_links() == {11: official}
    assert connection.calls == [
        (
            """
        SELECT *
        FROM vkpi_memory_links
        WHERE link_type='official_account_published_product'
        ORDER BY observed_at DESC, id DESC
        """,
            (),
        )
    ]

    deep_profile = {
        "id": 19,
        "dimensions_11_json": json.dumps(
            {
                "method": "fixture",
                "computed_at": "2026-08-29T00:00:00Z",
                "block4_specialty": {
                    "product_fit": {"AF-35": 91, "zero": 0},
                    "product_fit_confidence": {"AF-35": 0.8, "zero": 1.0},
                },
            }
        ),
    }
    connection = _Connection(deep_profile)
    repository = SqlProductFitRepository(lambda: connection, lambda: False)
    assert repository.dimensions11_fit(42) == {
        "AF-35": {
            "sku": "AF-35",
            "normalized": "af35",
            "score": 91.0,
            "confidence": 0.8,
            "profile_deep_id": 19,
            "method": "fixture",
            "computed_at": "2026-08-29T00:00:00Z",
        }
    }
    assert connection.calls == [
        (
            """
        SELECT id, dimensions_11_json
        FROM vkpi_kol_profile_deep
        WHERE kol_pool_id=?
        LIMIT 1
        """,
            (42,),
        )
    ]


def test_injected_repository_and_reason_port_are_bounded_and_persist_false_is_zero_write(monkeypatch) -> None:
    family = {
        "id": 10,
        "entity_uid": "family:10",
        "display_name": "AF 35mm F1.8 FE",
        "identity_key": "af-35-f18-fe",
        "metadata_json": "{}",
    }
    product_map = {
        90: {
            "product_id": 90,
            "family_id": 10,
            "family_uid": "family:10",
            "family_name": family["display_name"],
            "family_key": family["identity_key"],
            "family_metadata_json": "{}",
        }
    }

    class Repository:
        def list_kol_entities(self):
            return [{"id": 1, "entity_uid": "kol:1", "display_name": "Creator", "status": "active", "identity_json": json.dumps({"source_ref": "source:1", "country": "US"}), "metadata_json": "{}"}]

        def pools_by_source_ref(self):
            return {"source:1": {"id": 2, "source_ref": "source:1", "platform": "youtube", "handle": "creator", "display_name": "Creator", "country": "US", "sync_status": "active"}}

        def legacy_entities_by_uid(self):
            return {}

        def facts_by_kol(self):
            return {1: []}

        def worked_links_by_kol(self):
            return {1: []}

        def product_family_maps(self):
            return product_map, {10: family}

        def target_market_signals(self, _family_id):
            return []

        def candidate_families(self):
            return [family]

        def official_family_links(self):
            return {}

        def dimensions11_fit(self, _kol_pool_id):
            return {}

    class ReasonPort:
        def __init__(self) -> None:
            self.calls: list[tuple[dict[str, Any], dict[str, Any], int, str]] = []

        def generate_reason(self, candidate, *, binding, token_limit, budget_scope):
            self.calls.append((candidate, binding, token_limit, budget_scope))
            return {"status": "success", "short_reason": "bounded reason"}

    monkeypatch.setattr(product_fit.memory, "readiness", lambda: {"status": "ready_for_p4_dry_run", "provider_calls_allowed": False})
    monkeypatch.setattr(product_fit, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(product_fit, "get_budget_status", lambda *_args, **_kwargs: {"configured": True})
    monkeypatch.setattr(product_fit, "_catalog_products_for_match", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(product_fit, "persist_product_fit_preview_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("persist=false wrote business data")))
    reason_port = ReasonPort()

    payload = product_fit.build_kol_product_fit_preview(
        kol_entity_uid="kol:1",
        include_low_evidence=True,
        with_llm_reasons=True,
        reason_limit=9,
        persist_run=False,
        repository=Repository(),
        reason_port=reason_port,
    )

    assert payload["persistence"] == {"enabled": False}
    assert payload["summary"]["returned"] == 1
    assert payload["summary"]["reasons_attached"] == 1
    assert payload["items"][0]["recommendation_reason"] == {
        "status": "success",
        "short_reason": "bounded reason",
    }
    assert len(reason_port.calls) == 1
    candidate, binding, token_limit, budget_scope = reason_port.calls[0]
    assert candidate is payload["items"][0]
    assert binding is payload
    assert token_limit == 220
    assert budget_scope == product_fit.REASON_BUDGET_SCOPE


class _PersistenceConnection:
    def __init__(self, *, fail_on: str = "") -> None:
        self.fail_on = fail_on
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        normalized = " ".join(sql.split())
        self.calls.append((normalized, tuple(params)))
        if self.fail_on and self.fail_on in normalized:
            raise RuntimeError("fixture persistence failure")
        if normalized.startswith("SELECT * FROM vkpi_kol_recommendation_runs"):
            return _Rows({"id": 701})
        if normalized.startswith("SELECT * FROM vkpi_kol_recommendations"):
            return _Rows({"id": 801})
        return _Rows(None)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _persistence_payload() -> dict[str, Any]:
    return {
        "summary": {"total_families_evaluated": 3, "llm_reasons_requested": True, "reasons_attached": 1},
        "kol": {"kol_pool_id": 2, "platform": "youtube", "handle": "creator"},
        "items": [
            {
                "product_family_uid": "family:10",
                "product_family_name": "AF 35mm F1.8 FE",
                "product_member_count": 2,
                "matched_catalog_product": {"sku": "AF35"},
                "matched_catalog_products": [{"sku": "AF35"}],
                "links": {"open_in_vkpi": "/products/family:10"},
                "score": 84.5,
                "rank": 1,
                "score_breakdown": {"final": 84.5},
                "evidence_pro": [{"type": "fit"}],
                "evidence_con": [],
                "recommendation_reason": {"short_reason": "Strong fit", "model": "fixture"},
            }
        ],
    }


def test_persist_true_keeps_single_transaction_and_exact_projection(monkeypatch) -> None:
    connection = _PersistenceConnection()
    tokens = iter(("run-token", "rec-token"))
    monkeypatch.setattr(product_fit_persistence, "get_conn", lambda: connection)
    monkeypatch.setattr(product_fit_persistence.secrets, "token_hex", lambda _size: next(tokens))

    result = product_fit_persistence.persist_product_fit_preview_run(
        _persistence_payload(),
        scenario="kol_product_fit",
        generated_at="2026-08-29T13:00:00Z",
    )

    assert result == {
        "enabled": True,
        "run_uid": "p4kpf-run-token",
        "run_id": 701,
        "recommendation_count": 1,
        "recommendation_ids": [801],
    }
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert [sql.split()[2] for sql, _params in connection.calls if sql.startswith("INSERT INTO")] == [
        "vkpi_kol_recommendation_runs",
        "vkpi_kol_recommendations",
        "vkpi_recommendation_explanations",
    ]
    run_params = connection.calls[0][1]
    assert run_params[:6] == (
        "p4kpf-run-token",
        None,
        "kol_product_fit_v1",
        "previewed",
        3,
        1,
    )
    assert json.loads(run_params[6]) == {
        "scenario": "kol_product_fit",
        "source_mode": "kol_product_fit_preview",
        "dry_run": True,
        "kol": {"kol_pool_id": 2, "platform": "youtube", "handle": "creator"},
        "llm_reasons_requested": True,
        "reason_count": 1,
    }


def test_persist_true_rolls_back_and_preserves_exception(monkeypatch) -> None:
    connection = _PersistenceConnection(fail_on="INSERT INTO vkpi_kol_recommendations")
    tokens = iter(("run-token", "rec-token"))
    monkeypatch.setattr(product_fit_persistence, "get_conn", lambda: connection)
    monkeypatch.setattr(product_fit_persistence.secrets, "token_hex", lambda _size: next(tokens))

    with pytest.raises(RuntimeError, match="fixture persistence failure"):
        product_fit_persistence.persist_product_fit_preview_run(
            _persistence_payload(),
            scenario="kol_product_fit",
            generated_at="2026-08-29T13:00:00Z",
        )

    assert connection.commits == 0
    assert connection.rollbacks == 1
