from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.domains.products import official_catalog_sync
from app.services.scheduler import jobs_tasks_products

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import vkpi_build_viltrox_product_catalog_seed  # noqa: E402
import vkpi_import_viltrox_product_catalog  # noqa: E402


def _product(
    sku: str | None = "AF-35-18-E",
    *,
    product_id: int = 100,
    variant_id: int = 1001,
    price: str = "379.00",
    title: str = "Viltrox AF 35mm F1.8",
    handle: str | None = None,
) -> dict:
    return {
        "id": product_id,
        "title": title,
        "handle": handle or f"viltrox-af-35mm-f1-8-{product_id}",
        "vendor": "Viltrox",
        "product_type": "Lens",
        "body_html": "<p>Compact <strong>full-frame</strong> lens.</p>",
        "tags": "Sony E, Autofocus",
        "published_at": "2026-07-12T08:00:00Z",
        "updated_at": "2026-07-12T09:00:00Z",
        "options": [{"name": "Mount"}],
        "variants": [
            {
                "id": variant_id,
                "sku": sku,
                "title": "Sony E",
                "price": price,
                "option1": "Sony E",
                "position": 1,
                "available": True,
                "updated_at": "2026-07-12T09:00:00Z",
            }
        ],
    }


def _feed(*products: dict) -> official_catalog_sync.CatalogFeed:
    return official_catalog_sync.validate_feed({"products": list(products)})


def test_fetch_uses_official_url_hard_timeout_and_explicit_user_agent(monkeypatch) -> None:
    captured: dict = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str) -> httpx.Response:
            captured["url"] = url
            request = httpx.Request("GET", url)
            return httpx.Response(200, request=request, json={"products": [_product()]})

    def client_factory(**kwargs):
        captured["client_kwargs"] = kwargs
        return FakeClient()

    monkeypatch.setattr(official_catalog_sync.httpx, "AsyncClient", client_factory)

    feed = asyncio.run(official_catalog_sync.fetch_official_catalog())

    assert captured["url"] == "https://viltrox.com/products.json?limit=250&page=1"
    timeout = captured["client_kwargs"]["timeout"]
    assert timeout.connect == timeout.read == timeout.write == timeout.pool == 30.0
    assert captured["client_kwargs"]["headers"]["User-Agent"] == official_catalog_sync.USER_AGENT
    assert len(feed.products) == 1 and feed.items[0]["sku"] == "AF-35MM-F18"


def test_fetch_retries_transient_http_errors_inside_deadline(monkeypatch) -> None:
    statuses = iter((503, 429, 200))
    calls: list[int] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str) -> httpx.Response:
            status = next(statuses)
            calls.append(status)
            request = httpx.Request("GET", url)
            return httpx.Response(
                status,
                request=request,
                json={"products": [_product()]} if status == 200 else {"error": "temporary"},
            )

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(official_catalog_sync.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(official_catalog_sync.asyncio, "sleep", no_sleep)

    feed = asyncio.run(official_catalog_sync.fetch_official_catalog())

    assert calls == [503, 429, 200]
    assert feed.items[0]["sku"] == "AF-35MM-F18"


def test_fetch_enforces_wall_clock_timeout(monkeypatch) -> None:
    class SlowClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url: str):
            await asyncio.sleep(0.05)
            raise AssertionError("request should have timed out")

    monkeypatch.setattr(official_catalog_sync, "HARD_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(official_catalog_sync.httpx, "AsyncClient", lambda **_kwargs: SlowClient())

    with pytest.raises(official_catalog_sync.OfficialCatalogSyncError) as exc_info:
        asyncio.run(official_catalog_sync.fetch_official_catalog())

    assert exc_info.value.error_type == "timeout"


@pytest.mark.parametrize(
    ("payload", "has_next_page", "error_type"),
    [
        ({"products": []}, False, "validation"),
        ({"products": [{} for _ in range(250)]}, False, "incomplete_feed"),
        ({"products": [_product()]}, True, "incomplete_feed"),
        (
            {"products": [{**_product(), "variants": [{"id": "", "sku": "AF-35", "price": "1.00"}]}]},
            False,
            "validation",
        ),
    ],
)
def test_incomplete_or_invalid_feed_is_rejected(payload, has_next_page, error_type) -> None:
    with pytest.raises(official_catalog_sync.OfficialCatalogSyncError) as exc_info:
        official_catalog_sync.validate_feed(payload, has_next_page=has_next_page)
    assert exc_info.value.error_type == error_type


def test_missing_official_sku_keeps_product_identity_and_variant_evidence() -> None:
    product = _product(sku=None, variant_id=43832872435778)
    product["variants"][0]["available"] = False
    product["variants"][0]["updated_at"] = "2026-07-13T09:00:50+08:00"

    feed = _feed(product)
    item = feed.variants[0]
    specs = json.loads(item["specs_json"])

    assert item["sku"] == "AF-35MM-F18"
    assert specs["shopify_variant_id"] == "43832872435778"
    assert specs["official_variants"][0]["official_sku"] == ""
    assert specs["public_store_purchase_available"] is False
    assert specs["warehouse_inventory_status"] == "not_provided_by_public_catalog"


def test_product_price_and_variant_counts_use_all_public_variants() -> None:
    product = _product(price="399.00")
    product["options"] = [{"name": "Mount"}, {"name": "Color"}]
    product["variants"].append(
        {
            "id": 1002,
            "sku": "AF-35-18-Z-WHITE",
            "title": "Nikon Z / White",
            "price": "379.00",
            "compare_at_price": "429.00",
            "option1": "Nikon Z",
            "option2": "White",
            "position": 2,
            "available": False,
            "updated_at": "2026-07-12T09:00:00Z",
        }
    )

    feed = _feed(product)
    item = feed.items[0]
    specs = json.loads(item["specs_json"])

    assert feed.variant_count == 2
    assert item["price_usd"] == Decimal("379.00")
    assert specs["price_min_usd"] == "379.00"
    assert specs["price_max_usd"] == "399.00"
    assert specs["available_variant_count"] == 1
    assert specs["availability_scope"] == "public_storefront_purchase_option_only"


def test_store_updated_at_churn_does_not_mark_product_changed(catalog_db) -> None:
    first_product = _product()
    first_feed = _feed(first_product)
    first = _run_persist("run-store-time-1", first_feed)
    stored_before = catalog_db.execute(
        "SELECT updated_at FROM vkpi_products WHERE sku='AF-35MM-F18'"
    ).fetchone()[0]

    second_product = _product()
    second_product["updated_at"] = "2026-07-12T10:00:00Z"
    second_product["variants"][0]["updated_at"] = "2026-07-12T10:00:00Z"
    second = _run_persist("run-store-time-2", _feed(second_product))
    stored_after = catalog_db.execute(
        "SELECT updated_at FROM vkpi_products WHERE sku='AF-35MM-F18'"
    ).fetchone()[0]

    assert first["inserted"] == 1
    assert second["updated"] == 0
    assert second["unchanged"] == 1
    assert stored_after == stored_before


def test_duplicate_variant_identity_is_rejected() -> None:
    duplicate = _product(
        product_id=200,
        variant_id=1001,
        title="Viltrox AF 50mm F1.8",
        handle="viltrox-af-50mm-f1-8",
    )
    with pytest.raises(official_catalog_sync.OfficialCatalogSyncError) as exc_info:
        _feed(_product(), duplicate)
    assert exc_info.value.error_type == "validation"


def test_invalid_feed_records_error_without_product_persistence(monkeypatch) -> None:
    events: list[tuple] = []

    async def invalid_fetch():
        raise official_catalog_sync.OfficialCatalogSyncError("truncated", error_type="incomplete_feed")

    def must_not_persist(*_args, **_kwargs):
        raise AssertionError("invalid feed must not reach product persistence")

    monkeypatch.setattr(official_catalog_sync, "_run_id", lambda: "run-invalid")
    monkeypatch.setattr(official_catalog_sync, "fetch_official_catalog", invalid_fetch)
    monkeypatch.setattr(official_catalog_sync, "persist_complete_feed", must_not_persist)
    monkeypatch.setattr(official_catalog_sync, "_record_run_start", lambda run_id: events.append(("start", run_id)))
    monkeypatch.setattr(
        official_catalog_sync,
        "_record_run_failure",
        lambda run_id, *, error_type, error_message, duration_ms=0: events.append(
            ("failed", run_id, error_type, error_message)
        ),
    )

    with pytest.raises(official_catalog_sync.OfficialCatalogSyncError):
        asyncio.run(official_catalog_sync.sync_official_catalog())

    assert events == [
        ("start", "run-invalid"),
        ("failed", "run-invalid", "incomplete_feed", "truncated"),
    ]


@pytest.fixture
def catalog_db(monkeypatch):
    sqlite3.register_adapter(Decimal, str)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.create_function("NOW", 0, lambda: "2026-07-12T00:00:00Z")
    conn.executescript(
        """
        CREATE TABLE vkpi_products (
            sku TEXT PRIMARY KEY,
            category_main TEXT NOT NULL,
            category_detail TEXT,
            model_name TEXT NOT NULL,
            marketing_name TEXT,
            price_usd NUMERIC,
            status TEXT NOT NULL DEFAULT 'priced',
            description TEXT,
            source_file TEXT,
            series TEXT DEFAULT '',
            mount TEXT DEFAULT '',
            product_url TEXT DEFAULT '',
            specs_json TEXT NOT NULL DEFAULT '{}',
            fit_tags_json TEXT NOT NULL DEFAULT '[]',
            source_url TEXT DEFAULT '',
            source_checked_at TEXT,
            source_confidence NUMERIC NOT NULL DEFAULT 0,
            official_catalog_product_id TEXT NOT NULL DEFAULT '',
            official_catalog_variant_id TEXT NOT NULL DEFAULT '',
            official_catalog_last_seen_at TEXT,
            official_catalog_missing_full_feeds INTEGER NOT NULL DEFAULT 0,
            official_catalog_previous_status TEXT NOT NULL DEFAULT '',
            imported_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE vkpi_official_catalog_sync_runs (
            run_id TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            products_fetched INTEGER NOT NULL DEFAULT 0,
            variants_fetched INTEGER NOT NULL DEFAULT 0,
            inserted_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            unchanged_count INTEGER NOT NULL DEFAULT 0,
            missing_count INTEGER NOT NULL DEFAULT 0,
            store_unlisted_count INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            error_type TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE vkpi_official_catalog_sync_items (
            run_id TEXT NOT NULL REFERENCES vkpi_official_catalog_sync_runs(run_id),
            sku TEXT NOT NULL,
            generated_sku TEXT NOT NULL,
            shopify_product_id TEXT NOT NULL,
            shopify_variant_id TEXT NOT NULL,
            PRIMARY KEY (run_id, sku)
        );
        """
    )
    monkeypatch.setattr(official_catalog_sync, "get_conn", lambda: conn)
    yield conn
    conn.close()


def _insert_tracked_product(
    conn: sqlite3.Connection,
    sku: str,
    variant_id: str,
    *,
    specs_json: str = "{}",
    fit_tags_json: str = "[]",
    product_id: str = "old-product",
) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_products
          (sku, category_main, model_name, price_usd, status, specs_json,
           fit_tags_json, official_catalog_product_id, official_catalog_variant_id,
           imported_at, updated_at)
        VALUES (?, 'Lens', ?, 100, 'priced', ?, ?, ?, ?, 'old', 'old')
        """,
        (sku, sku, specs_json, fit_tags_json, product_id, variant_id),
    )
    conn.commit()


def _run_persist(run_id: str, feed: official_catalog_sync.CatalogFeed) -> dict:
    official_catalog_sync._record_run_start(run_id)
    return official_catalog_sync.persist_complete_feed(run_id, feed)


def test_upsert_preserves_rich_fields_and_unlists_only_after_two_complete_feeds(catalog_db) -> None:
    rich_specs = json.dumps({"focal_length": "35mm", "aperture": "F1.8", "weight": "340g"})
    rich_tags = json.dumps(["portrait", "full-frame", "low-light"])
    _insert_tracked_product(
        catalog_db,
        "AF-35MM-F18",
        "1001",
        specs_json=rich_specs,
        fit_tags_json=rich_tags,
        product_id="100",
    )
    _insert_tracked_product(catalog_db, "MISSING-SKU", "2002", product_id="200")
    feed = _feed(_product(price="399.00"))

    first = _run_persist("run-1", feed)
    rich = catalog_db.execute(
        "SELECT * FROM vkpi_products WHERE sku='AF-35MM-F18'"
    ).fetchone()
    missing = catalog_db.execute(
        "SELECT * FROM vkpi_products WHERE sku='MISSING-SKU'"
    ).fetchone()

    assert first["run_id"] == "run-1"
    assert first["status"] == "completed"
    assert first["products_fetched"] == first["variants_fetched"] == 1
    assert first["inserted"] == 0
    assert first["updated"] == 1
    assert first["unchanged"] == 0
    assert first["missing"] == 1
    assert first["marked_unlisted"] == 0
    assert first["atomic"] is True
    assert first["warehouse_inventory_included"] is False
    merged_specs = json.loads(rich["specs_json"])
    assert {key: merged_specs[key] for key in ("focal_length", "aperture", "weight")} == json.loads(rich_specs)
    assert merged_specs["shopify_product_id"] == "100"
    assert merged_specs["variant_count"] == 1
    assert json.loads(rich["fit_tags_json"]) == ["portrait", "full-frame", "low-light", "Sony E", "Autofocus"]
    assert float(rich["price_usd"]) == 399.0
    assert missing["official_catalog_missing_full_feeds"] == 1
    assert missing["status"] == "priced"

    second = _run_persist("run-2", feed)
    missing = catalog_db.execute(
        "SELECT * FROM vkpi_products WHERE sku='MISSING-SKU'"
    ).fetchone()
    assert second["marked_unlisted"] == 1
    assert second["unchanged"] == 1
    assert missing["official_catalog_missing_full_feeds"] == 2
    assert missing["status"] == "store_unlisted"
    assert catalog_db.execute("SELECT COUNT(*) FROM vkpi_products").fetchone()[0] == 2

    restored_feed = _feed(
        _product(),
        _product(
            "MISSING-SKU",
            product_id=200,
            variant_id=2002,
            price="129.00",
            title="Viltrox Missing SKU",
            handle="missing-sku",
        ),
    )
    restored = _run_persist("run-3", restored_feed)
    missing = catalog_db.execute(
        "SELECT * FROM vkpi_products WHERE sku='MISSING-SKU'"
    ).fetchone()
    assert restored["updated"] == 2
    assert missing["official_catalog_missing_full_feeds"] == 0
    assert missing["status"] == "priced"
    assert missing["official_catalog_previous_status"] == ""


def test_sync_run_records_counts(catalog_db) -> None:
    result = _run_persist("run-counts", _feed(_product()))
    row = catalog_db.execute(
        "SELECT * FROM vkpi_official_catalog_sync_runs WHERE run_id='run-counts'"
    ).fetchone()

    assert result["inserted"] == 1
    assert row["status"] == "completed"
    assert row["products_fetched"] == 1
    assert row["variants_fetched"] == 1
    assert row["inserted_count"] == 1
    assert row["unchanged_count"] == 0
    assert row["duration_ms"] >= 0
    assert row["error_message"] == ""


def test_unchanged_daily_sighting_advances_last_seen_without_business_update(catalog_db, monkeypatch) -> None:
    checked_times = iter(("2026-07-12T01:00:00Z", "2026-07-13T01:00:00Z"))
    monkeypatch.setattr(official_catalog_sync, "_now", lambda: next(checked_times))
    feed = _feed(_product())

    _run_persist("run-day-1", feed)
    result = _run_persist("run-day-2", feed)
    row = catalog_db.execute(
        "SELECT updated_at, source_checked_at, official_catalog_last_seen_at FROM vkpi_products"
    ).fetchone()

    assert result["updated"] == 0
    assert result["unchanged"] == 1
    assert row["updated_at"] == "2026-07-12T01:00:00Z"
    assert row["source_checked_at"] == "2026-07-13T01:00:00Z"
    assert row["official_catalog_last_seen_at"] == "2026-07-13T01:00:00Z"


def test_stable_product_id_prevents_duplicate_when_generated_sku_changes(catalog_db) -> None:
    _insert_tracked_product(
        catalog_db,
        "LEGACY-35-SKU",
        "1001",
        product_id="100",
    )
    feed = _feed(
        _product(
            title="Viltrox AF 35mm F1.8 EVO Full-Frame Lens for Sony E-Mount",
            handle="af-35mm-f1-8-evo-fe",
        )
    )

    result = _run_persist("run-stable-id", feed)
    rows = catalog_db.execute(
        "SELECT sku, model_name, official_catalog_product_id FROM vkpi_products"
    ).fetchall()
    audit = catalog_db.execute(
        "SELECT sku, generated_sku FROM vkpi_official_catalog_sync_items WHERE run_id='run-stable-id'"
    ).fetchone()

    assert result["inserted"] == 0
    assert result["updated"] == 1
    assert len(rows) == 1
    assert rows[0]["sku"] == "LEGACY-35-SKU"
    assert rows[0]["official_catalog_product_id"] == "100"
    assert audit["sku"] == "LEGACY-35-SKU"
    assert audit["generated_sku"] != "LEGACY-35-SKU"


def test_identity_conflict_rolls_back_product_writes_and_records_failed_run(catalog_db, monkeypatch) -> None:
    _insert_tracked_product(
        catalog_db,
        "AF-35MM-F18",
        "9991",
        product_id="999",
    )

    async def fetch():
        return _feed(_product())

    monkeypatch.setattr(official_catalog_sync, "_run_id", lambda: "run-conflict")
    monkeypatch.setattr(official_catalog_sync, "fetch_official_catalog", fetch)

    with pytest.raises(official_catalog_sync.OfficialCatalogSyncError) as exc_info:
        asyncio.run(official_catalog_sync.sync_official_catalog())

    run = catalog_db.execute(
        "SELECT status, error_type FROM vkpi_official_catalog_sync_runs WHERE run_id='run-conflict'"
    ).fetchone()
    assert exc_info.value.error_type == "identity_conflict"
    assert run["status"] == "failed"
    assert run["error_type"] == "identity_conflict"
    assert catalog_db.execute("SELECT COUNT(*) FROM vkpi_official_catalog_sync_items").fetchone()[0] == 0
    assert catalog_db.execute("SELECT COUNT(*) FROM vkpi_products").fetchone()[0] == 1


def test_dry_run_reports_exact_impact_without_writes(catalog_db) -> None:
    _insert_tracked_product(
        catalog_db,
        "AF-35MM-F18",
        "1001",
        product_id="100",
    )
    _insert_tracked_product(
        catalog_db,
        "MISSING-SKU",
        "2002",
        product_id="200",
    )
    before = [tuple(row) for row in catalog_db.execute("SELECT * FROM vkpi_products ORDER BY sku")]

    result = official_catalog_sync.preview_complete_feed(_feed(_product(price="399.00")))
    after = [tuple(row) for row in catalog_db.execute("SELECT * FROM vkpi_products ORDER BY sku")]

    assert result["status"] == "dry_run"
    assert result["inserted"] == 0
    assert result["updated"] == 1
    assert result["missing"] == 1
    assert result["marked_unlisted"] == 0
    assert before == after
    assert catalog_db.execute("SELECT COUNT(*) FROM vkpi_official_catalog_sync_runs").fetchone()[0] == 0


def test_sync_run_records_failure_details(catalog_db) -> None:
    official_catalog_sync._record_run_start("run-failed")
    official_catalog_sync._record_run_failure(
        "run-failed",
        error_type="timeout",
        error_message="official catalog request exceeded 30 seconds",
    )
    row = catalog_db.execute(
        "SELECT * FROM vkpi_official_catalog_sync_runs WHERE run_id='run-failed'"
    ).fetchone()

    assert row["status"] == "failed"
    assert row["finished_at"] is not None
    assert row["error_type"] == "timeout"
    assert row["error_message"] == "official catalog request exceeded 30 seconds"


def test_scheduler_job_obeys_registry_gate_and_records_result(monkeypatch) -> None:
    calls: list[str] = []
    recorded: list[tuple[str, bool, str]] = []

    async def sync():
        calls.append("sync")
        return {"status": "completed", "inserted": 1}

    monkeypatch.setattr(official_catalog_sync, "sync_official_catalog", sync)
    monkeypatch.setattr(
        jobs_tasks_products,
        "_record_scheduler_run",
        lambda key, *, ok, error="": recorded.append((key, ok, error)),
    )
    monkeypatch.setattr(jobs_tasks_products, "_scheduler_task_enabled", lambda _key: False)
    asyncio.run(jobs_tasks_products.job_vkpi_official_catalog_sync())
    assert calls == [] and recorded == []

    monkeypatch.setattr(jobs_tasks_products, "_scheduler_task_enabled", lambda _key: True)
    asyncio.run(jobs_tasks_products.job_vkpi_official_catalog_sync())
    assert calls == ["sync"]
    assert recorded == [("vkpi_official_catalog_sync", True, "")]


def test_scheduler_failure_is_recorded_and_raised(monkeypatch) -> None:
    recorded: list[tuple[str, bool, str]] = []

    async def fail():
        raise official_catalog_sync.OfficialCatalogSyncError(
            "official catalog request exceeded 30 seconds",
            error_type="timeout",
        )

    monkeypatch.setattr(official_catalog_sync, "sync_official_catalog", fail)
    monkeypatch.setattr(jobs_tasks_products, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.setattr(
        jobs_tasks_products,
        "_record_scheduler_run",
        lambda key, *, ok, error="": recorded.append((key, ok, error)),
    )

    with pytest.raises(official_catalog_sync.OfficialCatalogSyncError):
        asyncio.run(jobs_tasks_products.job_vkpi_official_catalog_sync())
    assert recorded == [
        (
            "vkpi_official_catalog_sync",
            False,
            "official catalog request exceeded 30 seconds",
        )
    ]


def test_import_dry_run_never_initializes_or_writes_schema(tmp_path, monkeypatch) -> None:
    seed = tmp_path / "catalog.json"
    seed.write_text(
        json.dumps(
            [
                {
                    "sku": "AF-35MM-F18",
                    "model_name": "Viltrox AF 35mm F1.8",
                    "category_main": "Lens",
                    "source_url": "https://viltrox.com/products/af-35mm-f1-8",
                    "source_checked_at": "2026-07-12T00:00:00Z",
                    "specs": {"official_product_id": "100"},
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        vkpi_import_viltrox_product_catalog,
        "ensure_product_catalog_schema",
        lambda: (_ for _ in ()).throw(AssertionError("dry-run must not initialize schema")),
    )

    result = vkpi_import_viltrox_product_catalog.import_catalog(seed, apply=False)

    assert result["status"] == "dry_run_valid"
    assert result["would_upsert"] == 1


def test_seed_builder_reports_intentionally_incomplete_page_coverage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_build_viltrox_product_catalog_seed,
        "_fetch_text",
        lambda _url, timeout=15.0: json.dumps({"products": [_product()]}),
    )

    result = vkpi_build_viltrox_product_catalog_seed.build_seed(
        out=tmp_path / "generated.json",
        limit=1,
        fetch_pages=True,
        delay=0,
        all_categories=True,
        page_limit=0,
        request_timeout=1,
        deadline_seconds=1,
    )
    row = json.loads((tmp_path / "generated.json").read_text(encoding="utf-8"))[0]

    assert result["status"] == "completed_with_partial_page_coverage"
    assert result["page_fetch_attempted"] == 0
    assert result["page_fetch_skipped_limit"] == 1
    assert result["incomplete_page_coverage"] == 1
    assert row["specs"]["warehouse_inventory_status"] == "not_provided_by_public_catalog"
    assert row["specs"]["official_page_fetch"] == "skipped_page_limit"


def test_import_rejects_duplicate_seed_before_schema_writes(tmp_path, monkeypatch) -> None:
    row = {
        "sku": "AF-35MM-F18",
        "model_name": "Viltrox AF 35mm F1.8",
        "category_main": "Lens",
        "source_url": "https://viltrox.com/products/af-35mm-f1-8",
        "source_checked_at": "2026-07-12T00:00:00Z",
        "specs": {"official_product_id": "100"},
    }
    seed = tmp_path / "duplicate.json"
    seed.write_text(json.dumps([row, row]), encoding="utf-8")
    monkeypatch.setattr(
        vkpi_import_viltrox_product_catalog,
        "ensure_product_catalog_schema",
        lambda: (_ for _ in ()).throw(AssertionError("invalid apply must not initialize schema")),
    )

    result = vkpi_import_viltrox_product_catalog.import_catalog(seed, apply=True)

    assert result["status"] == "rejected_invalid_seed"
    assert result["upserted"] == 0
    assert result["errors"] == [{"sku": "AF-35MM-F18", "errors": ["duplicate_sku", "duplicate_official_product_id"]}]


def test_import_apply_merges_metadata_and_preserves_existing_status(tmp_path, monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_products (
            sku TEXT PRIMARY KEY,
            category_main TEXT NOT NULL,
            category_detail TEXT,
            model_name TEXT NOT NULL,
            marketing_name TEXT,
            price_usd REAL,
            status TEXT NOT NULL,
            description TEXT,
            source_file TEXT,
            series TEXT,
            mount TEXT,
            product_url TEXT,
            specs_json TEXT NOT NULL,
            fit_tags_json TEXT NOT NULL,
            source_url TEXT,
            source_checked_at TEXT,
            source_confidence REAL,
            imported_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO vkpi_products VALUES (
            'AF-35MM-F18', 'Lens', '', 'Old title', '', 379, 'store_unlisted', '',
            'old', '', '', '', '{"manual_note":"keep"}', '["manual-tag"]', '',
            '2026-07-11T00:00:00Z', 0.5, 'old', 'old'
        );
        """
    )
    seed = tmp_path / "newer.json"
    seed.write_text(
        json.dumps(
            [
                {
                    "sku": "AF-35MM-F18",
                    "model_name": "Viltrox AF 35mm F1.8",
                    "category_main": "Lens",
                    "price_usd": 399,
                    "status": "official",
                    "source_url": "https://viltrox.com/products/af-35mm-f1-8",
                    "source_checked_at": "2026-07-12T00:00:00Z",
                    "source_confidence": 1,
                    "fit_tags": ["Sony E"],
                    "specs": {"official_product_id": "100"},
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(vkpi_import_viltrox_product_catalog, "ensure_product_catalog_schema", lambda: None)
    monkeypatch.setattr(vkpi_import_viltrox_product_catalog, "get_conn", lambda: conn)

    result = vkpi_import_viltrox_product_catalog.import_catalog(seed, apply=True)
    row = conn.execute("SELECT * FROM vkpi_products WHERE sku='AF-35MM-F18'").fetchone()

    assert result["upserted"] == 1
    assert row["status"] == "store_unlisted"
    assert json.loads(row["specs_json"]) == {
        "manual_note": "keep",
        "official_product_id": "100",
    }
    assert json.loads(row["fit_tags_json"]) == ["manual-tag", "Sony E"]
    conn.close()


def test_migration_registers_one_enabled_low_risk_daily_task() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations/235_vkpi_official_catalog_sync.sql").read_text()

    assert "'vkpi_official_catalog_sync'" in migration
    assert "TRUE, 1, 0" in migration
    assert "'marketing_ops', 'low'" in migration
    assert "official_catalog_missing_full_feeds" in migration
    assert "idx_vkpi_products_official_product" in migration
    assert "unchanged_count" in migration
    assert "duration_ms" in migration
