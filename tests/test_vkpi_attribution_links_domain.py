from app.domains import attribution as attribution_domain
from app.domains.attribution import links as attribution_links


def test_attribution_links_domain_wraps_read_paths(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        attribution_links.link_center,
        "list_links",
        lambda *, limit, status, staff, staff_id_filter: calls.setdefault("list", (limit, status, staff, staff_id_filter)) or {"links": []},
    )
    monkeypatch.setattr(
        attribution_links.link_center,
        "link_clicks",
        lambda link_id, *, staff, limit: {"link_id": link_id, "staff": staff, "limit": limit},
    )

    assert attribution_domain.list_links(limit=10, status="live", staff={"id": 1}, staff_id=2) == (10, "live", {"id": 1}, 2)
    assert attribution_domain.link_clicks(5, staff={"id": 1}, limit=20) == {"link_id": 5, "staff": {"id": 1}, "limit": 20}


def test_attribution_links_domain_wraps_write_paths(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        attribution_links.link_center,
        "create_link",
        lambda body, *, staff: {"body": body, "staff": staff},
    )
    monkeypatch.setattr(
        attribution_links.link_center,
        "update_link",
        lambda link_id, body, *, staff: {"link_id": link_id, "body": body, "staff": staff},
    )
    monkeypatch.setattr(
        attribution_links.link_center,
        "set_status",
        lambda link_id, status, *, staff: calls.setdefault(status, (link_id, staff)) or {"status": status},
    )

    assert attribution_domain.create_link({"slug": "abc"}, staff={"id": 1}) == {"body": {"slug": "abc"}, "staff": {"id": 1}}
    assert attribution_domain.update_link(7, {"status": "live"}, staff={"id": 2}) == {
        "link_id": 7,
        "body": {"status": "live"},
        "staff": {"id": 2},
    }
    assert attribution_domain.pause_link(8, staff={"id": 3}) == (8, {"id": 3})
    assert attribution_domain.archive_link(9, staff={"id": 4}) == (9, {"id": 4})


def test_attribution_links_health_check_preserves_access_gate(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(attribution_links.scope, "assert_link_access", lambda link_id, staff: calls.setdefault("access", (link_id, staff)))
    monkeypatch.setattr(
        attribution_links.link_center,
        "health_check",
        lambda link_id, *, staff: {"link_id": link_id, "staff": staff, "ok": True},
    )

    assert attribution_domain.health_check(11, staff={"id": 5}) == {"link_id": 11, "staff": {"id": 5}, "ok": True}
    assert calls["access"] == (11, {"id": 5})
