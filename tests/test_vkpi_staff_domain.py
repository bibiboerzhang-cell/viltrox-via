from app.domains import staff as staff_domain
from app.domains.staff import profile as staff_profile_domain


def test_is_manager_staff_handles_owner_and_roles():
    assert staff_domain.is_manager_staff({"is_owner": 1}) is True
    assert staff_domain.is_manager_staff({"role": "marketing_manager"}) is True
    assert staff_domain.is_manager_staff({"role": "member"}) is False


def test_build_staff_profile_logs_audit(monkeypatch):
    events: list[tuple[str, dict]] = []

    monkeypatch.setattr(staff_profile_domain, "resolve_staff_id", lambda staff: 5)
    monkeypatch.setattr(
        staff_profile_domain.decision_engine,
        "staff_profile",
        lambda staff_id, *, staff, window, limit: {"staff_id": staff_id, "visibility": {"costs_visible": True}},
    )
    monkeypatch.setattr(
        staff_profile_domain.audit,
        "log_sensitive_access",
        lambda **kwargs: events.append(("sensitive", kwargs)),
    )
    monkeypatch.setattr(
        staff_profile_domain.audit,
        "log_business_event",
        lambda **kwargs: events.append(("business", kwargs)),
    )

    payload = staff_domain.build_staff_profile(42, staff={"id": 5}, window="week", limit=30)

    assert payload["staff_id"] == 42
    assert events[0][0] == "sensitive"
    assert events[0][1]["staff_id"] == 5
    assert events[0][1]["metadata"]["costs_visible"] is True
    assert events[1][0] == "business"
    assert events[1][1]["target_id"] == 42


def test_build_staff_kpi_and_workspace_apply_scope(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(staff_profile_domain.scope, "effective_staff_id", lambda staff, staff_id: staff_id or 17)
    monkeypatch.setattr(staff_profile_domain, "resolve_staff_id", lambda staff: 9)

    def staff_kpi(*, window, staff_id):
        calls["kpi_window"] = window
        calls["kpi_staff_id"] = staff_id
        return {"rows": []}

    def employee_workspace(staff_id):
        calls["workspace_staff_id"] = staff_id
        return {"staff_id": staff_id}

    monkeypatch.setattr(staff_profile_domain.decision_engine, "staff_kpi", staff_kpi)
    monkeypatch.setattr(staff_profile_domain.decision_engine, "employee_workspace", employee_workspace)

    assert staff_domain.build_staff_kpi(window="month", staff_id=None, staff={}) == {"rows": []}
    assert staff_domain.build_employee_workspace(staff_id=None, staff={}) == {"staff_id": 17}
    assert calls == {"kpi_window": "month", "kpi_staff_id": 17, "workspace_staff_id": 17}
