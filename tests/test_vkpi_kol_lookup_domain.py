import pytest

from app.domains.kol import lookup as lookup_domain
from app.domains.access import scope


@pytest.mark.anyio
async def test_lookup_with_context_returns_plain_result_without_kol_id(monkeypatch):
    monkeypatch.setattr(lookup_domain.claims_domain, "lookup", lambda body, *, staff: {"status": "created"})

    result = await lookup_domain.lookup_with_context({"handle": "x"}, staff={"id": 1})
    # P4/P10:lookup 现在 additive 附加 search_session_id/task_id(任务可追踪 + 切页可恢复)。
    # 原契约保证不变:无 kol 的 plain claims 结果其 status 原样流过、不被加工出 kol。
    assert result["status"] == "created"
    assert "kol" not in result


@pytest.mark.anyio
async def test_lookup_with_context_marks_claimed_by_other(monkeypatch):
    monkeypatch.setattr(lookup_domain.claims_domain, "lookup", lambda body, *, staff: {"kol": {"id": 7}})

    def deny_access(*_args, **_kwargs):
        raise scope.ScopeDenied("kol scope denied")

    monkeypatch.setattr(lookup_domain.claims_domain, "assert_kol_access", deny_access)

    payload = await lookup_domain.lookup_with_context({"handle": "x"}, staff={"id": 1})

    assert payload["kol"] == {"id": 7}
    assert payload["dossier"] == {}
    assert payload["can_claim"] is False
    assert payload["access_status"] == "claimed_by_other"


@pytest.mark.anyio
async def test_lookup_with_context_can_scan_analyze_and_attach_dossier(monkeypatch):
    monkeypatch.setattr(lookup_domain.claims_domain, "lookup", lambda body, *, staff: {"kol": {"id": 9}})
    monkeypatch.setattr(lookup_domain.claims_domain, "assert_kol_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lookup_domain.account_domain, "get_dossier", lambda kol_id: {"kol_id": kol_id, "ready": True})

    async def fake_scan(kol_id, *, max_posts):
        return {"kol_id": kol_id, "max_posts": max_posts, "content_count": 2}

    async def fake_analyze(kol_id, *, product_sku, snapshot_id=None):
        return {"kol_id": kol_id, "product_sku": product_sku, "snapshot_id": snapshot_id}

    monkeypatch.setattr(lookup_domain.account_domain, "scan_account", fake_scan)
    monkeypatch.setattr(lookup_domain.account_domain, "analyze_account", fake_analyze)

    payload = await lookup_domain.lookup_with_context(
        {"scan_account": True, "max_posts": 500, "product_sku": "AF35"},
        staff={"id": 1},
    )

    assert payload["dossier"] == {"kol_id": 9, "ready": True}
    assert payload["scan_result"] == {"kol_id": 9, "max_posts": 80, "content_count": 2}
    assert payload["analysis_result"] == {"kol_id": 9, "product_sku": "AF35", "snapshot_id": None}
