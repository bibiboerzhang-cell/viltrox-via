import pytest

from app.domains.kol import account as account_domain


class _DossierPort:
    def get_dossier(self, kol_id):
        return {"kol_id": kol_id}

    async def scan_account(self, kol_id, *, max_posts=24):
        return {"kol_id": kol_id, "max_posts": max_posts}

    async def analyze_account(self, kol_id, *, product_sku="", snapshot_id=None):
        return {"kol_id": kol_id, "product_sku": product_sku, "snapshot_id": snapshot_id}


def test_kol_account_domain_wraps_dossier():
    assert account_domain.get_dossier(42, dossier_port=_DossierPort()) == {"kol_id": 42}


def test_kol_account_domain_dossier_request_checks_access(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        account_domain.claims_domain,
        "assert_kol_access",
        lambda kol_id, staff, *, allow_unclaimed=False: calls.setdefault("access", (kol_id, staff, allow_unclaimed)),
    )
    assert account_domain.dossier_for_request(
        42,
        staff={"id": 1},
        dossier_port=_DossierPort(),
    ) == {"kol_id": 42}
    assert calls["access"] == (42, {"id": 1}, True)


@pytest.mark.anyio
async def test_kol_account_domain_wraps_scan_and_analyze(monkeypatch):
    monkeypatch.setattr(account_domain.claims_domain, "assert_kol_access", lambda *_args, **_kwargs: None)

    port = _DossierPort()
    assert await account_domain.scan_account(5, max_posts=7, dossier_port=port) == {"kol_id": 5, "max_posts": 7}
    assert await account_domain.analyze_account(5, product_sku="AF35", snapshot_id=11, dossier_port=port) == {
        "kol_id": 5,
        "product_sku": "AF35",
        "snapshot_id": 11,
    }
    assert await account_domain.scan_account_for_request(
        5,
        max_posts=3,
        staff={"id": 1},
        dossier_port=port,
    ) == {"kol_id": 5, "max_posts": 3}
    assert await account_domain.analyze_account_for_request(
        5,
        product_sku="AF56",
        snapshot_id=12,
        staff={"id": 1},
        dossier_port=port,
    ) == {
        "kol_id": 5,
        "product_sku": "AF56",
        "snapshot_id": 12,
    }
