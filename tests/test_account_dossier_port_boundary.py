from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domains.kol import account
from app.api.routers import vkpi_kol_links
from app.services.kol import account_dossier
from app.services.kol.account_dossier_adapter import (
    DEFAULT_ACCOUNT_DOSSIER_ADAPTER,
    ServiceAccountDossierAdapter,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.anyio
async def test_default_adapter_preserves_keywords_defaults_and_call_order(monkeypatch) -> None:
    calls: list[tuple] = []

    monkeypatch.setattr(
        account_dossier,
        "get_kol_dossier",
        lambda kol_id: calls.append(("get", kol_id)) or {"kol_id": kol_id},
    )

    async def scan(kol_id, *, max_posts):
        calls.append(("scan", kol_id, max_posts))
        return {"max_posts": max_posts}

    async def analyze(kol_id, *, product_sku, snapshot_id=None):
        calls.append(("analyze", kol_id, product_sku, snapshot_id))
        return {"snapshot_id": snapshot_id}

    monkeypatch.setattr(account_dossier, "scan_kol_account", scan)
    monkeypatch.setattr(account_dossier, "analyze_kol_account", analyze)
    adapter = ServiceAccountDossierAdapter()

    assert adapter.get_dossier(7) == {"kol_id": 7}
    assert await adapter.scan_account(7) == {"max_posts": 24}
    assert await adapter.analyze_account(7, product_sku="AF35", snapshot_id="9") == {"snapshot_id": "9"}
    assert calls == [
        ("get", 7),
        ("scan", 7, 24),
        ("analyze", 7, "AF35", "9"),
    ]


@pytest.mark.anyio
async def test_adapter_matches_direct_legacy_calls_for_1000_keyword_scenarios(monkeypatch) -> None:
    async def scan(kol_id, *, max_posts):
        return (kol_id, max_posts)

    async def analyze(kol_id, *, product_sku, snapshot_id=None):
        return (kol_id, product_sku, snapshot_id)

    monkeypatch.setattr(account_dossier, "scan_kol_account", scan)
    monkeypatch.setattr(account_dossier, "analyze_kol_account", analyze)
    adapter = ServiceAccountDossierAdapter()
    scenarios = 0

    for kol_id in range(1, 11):
        for max_posts in (1, 7, 24, 50, 80, 120, 250, 500, 999, 1000):
            for index in range(10):
                sku = f"SKU-{index}"
                snapshot_id = None if index == 0 else str(index)
                assert await adapter.scan_account(kol_id, max_posts=max_posts) == await scan(
                    kol_id,
                    max_posts=max_posts,
                )
                assert await adapter.analyze_account(
                    kol_id,
                    product_sku=sku,
                    snapshot_id=snapshot_id,
                ) == await analyze(
                    kol_id,
                    product_sku=sku,
                    snapshot_id=snapshot_id,
                )
                scenarios += 1

    assert scenarios == 1000


@pytest.mark.anyio
async def test_request_wrapper_checks_access_before_adapter_and_masks_after_success(monkeypatch) -> None:
    events: list[str] = []

    class Port:
        def get_dossier(self, _kol_id):
            events.append("adapter")
            return {"contact_email": "secret@example.com"}

        async def scan_account(self, _kol_id, *, max_posts=24):
            raise AssertionError(max_posts)

        async def analyze_account(self, _kol_id, *, product_sku="", snapshot_id=None):
            raise AssertionError((product_sku, snapshot_id))

    monkeypatch.setattr(
        account.claims_domain,
        "assert_kol_access",
        lambda *_args, **_kwargs: events.append("access"),
    )
    result = account.dossier_for_request(7, staff={"id": 1}, dossier_port=Port())
    events.append("returned")

    assert events == ["access", "adapter", "returned"]
    assert "secret@example.com" not in str(result)


def test_adapter_exception_identity_and_text_are_not_rewritten(monkeypatch) -> None:
    error = LookupError("KOL not found")

    def fail(_kol_id):
        raise error

    monkeypatch.setattr(account_dossier, "get_kol_dossier", fail)
    with pytest.raises(LookupError, match="KOL not found") as caught:
        ServiceAccountDossierAdapter().get_dossier(99)
    assert caught.value is error


def test_kol_account_has_no_legacy_service_import() -> None:
    source = ROOT / "backend/app/domains/kol/account.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(name.startswith("app.services.kol") for name in imports)
    assert "app.shared.account_dossier_port" in imports


@pytest.mark.anyio
async def test_api_composition_injects_the_reviewed_default_adapter(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    async def lookup(_body, *, staff, dossier_port):
        del staff
        calls.append(("lookup", dossier_port))
        return {"kol": {"id": 7}}

    async def analyze(_kol_id, **kwargs):
        calls.append(("analyze", kwargs["dossier_port"]))
        return {"status": "ready"}

    monkeypatch.setattr(vkpi_kol_links.kol_domain, "lookup_with_context", lookup)
    monkeypatch.setattr(vkpi_kol_links.kol_domain, "analyze_account_for_request", analyze)
    monkeypatch.setattr(
        vkpi_kol_links.kol_domain,
        "dossier_for_request",
        lambda _kol_id, **kwargs: calls.append(("dossier", kwargs["dossier_port"])) or {},
    )
    monkeypatch.setattr(
        vkpi_kol_links.kol_domain,
        "profile_with_dossier",
        lambda _kol_id, **kwargs: calls.append(("profile", kwargs["dossier_port"])) or {},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_queue=None)))

    await vkpi_kol_links.lookup_kol(request, {}, staff={"id": 1})
    vkpi_kol_links.kol_dossier(7, staff={"id": 1})
    vkpi_kol_links.kol_profile(7, staff={"id": 1})
    await vkpi_kol_links.analyze_kol(7, {"product_sku": "AF35"}, staff={"id": 1})

    assert [name for name, _adapter in calls] == ["lookup", "dossier", "profile", "analyze"]
    assert all(adapter is DEFAULT_ACCOUNT_DOSSIER_ADAPTER for _name, adapter in calls)
