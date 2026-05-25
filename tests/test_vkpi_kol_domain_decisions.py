from app.domains import kol as kol_domain
from app.domains.kol import decisions as kol_domain_decisions


def test_kol_decision_domain_wraps_create_and_list(monkeypatch):
    calls: dict[str, object] = {}

    def create_decision(body, *, staff):
        calls["create_body"] = body
        calls["create_staff"] = staff
        return {"decision_uid": "d1"}

    def list_decisions(*, kol_pool_id, decision_key, limit):
        calls["list"] = (kol_pool_id, decision_key, limit)
        return {"decisions": []}

    monkeypatch.setattr(kol_domain_decisions.decision_audit, "create_decision", create_decision)
    monkeypatch.setattr(kol_domain_decisions.decision_audit, "list_decisions", list_decisions)

    assert kol_domain.create_decision({"kol_pool_id": 1}, staff={"id": 2}) == {"decision_uid": "d1"}
    assert kol_domain.list_decisions(kol_pool_id=1, decision_key="contact", limit=10) == {"decisions": []}
    assert calls == {
        "create_body": {"kol_pool_id": 1},
        "create_staff": {"id": 2},
        "list": (1, "contact", 10),
    }


def test_kol_decision_domain_wraps_followups(monkeypatch):
    calls: dict[str, object] = {}

    def list_followup_queue(*, status, days_after, decision_key, limit):
        calls["queue"] = (status, days_after, decision_key, limit)
        return {"items": []}

    def create_followup(body, *, staff):
        calls["followup_body"] = body
        calls["followup_staff"] = staff
        return {"followup_uid": "f1"}

    monkeypatch.setattr(kol_domain_decisions.decision_audit, "list_followup_queue", list_followup_queue)
    monkeypatch.setattr(kol_domain_decisions.decision_audit, "create_followup", create_followup)

    assert kol_domain.list_followups(status="due", days_after=30, decision_key="watch", limit=20) == {"items": []}
    assert kol_domain.create_followup({"decision_uid": "d1"}, staff={"id": 3}) == {"followup_uid": "f1"}
    assert calls == {
        "queue": ("due", 30, "watch", 20),
        "followup_body": {"decision_uid": "d1"},
        "followup_staff": {"id": 3},
    }
