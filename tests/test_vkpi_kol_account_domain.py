import pytest

from app.domains.kol import account as account_domain


def test_kol_account_domain_wraps_dossier(monkeypatch):
    monkeypatch.setattr(account_domain.account_dossier, "get_kol_dossier", lambda kol_id: {"kol_id": kol_id})

    assert account_domain.get_dossier(42) == {"kol_id": 42}


@pytest.mark.anyio
async def test_kol_account_domain_wraps_scan_and_analyze(monkeypatch):
    async def fake_scan(kol_id, *, max_posts):
        return {"kol_id": kol_id, "max_posts": max_posts}

    async def fake_analyze(kol_id, *, product_sku, snapshot_id=None):
        return {"kol_id": kol_id, "product_sku": product_sku, "snapshot_id": snapshot_id}

    monkeypatch.setattr(account_domain.account_dossier, "scan_kol_account", fake_scan)
    monkeypatch.setattr(account_domain.account_dossier, "analyze_kol_account", fake_analyze)

    assert await account_domain.scan_account(5, max_posts=7) == {"kol_id": 5, "max_posts": 7}
    assert await account_domain.analyze_account(5, product_sku="AF35", snapshot_id=11) == {
        "kol_id": 5,
        "product_sku": "AF35",
        "snapshot_id": 11,
    }
