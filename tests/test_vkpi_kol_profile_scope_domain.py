from app.domains.kol import profile_scope


def test_profile_scope_project_staff_filter_for_manager(monkeypatch):
    monkeypatch.setattr(profile_scope.scope, "can_view_all", lambda staff: True)

    assert profile_scope.project_staff_filter({"role": "manager"}) == ("", [])


def test_profile_scope_project_staff_filter_for_actor(monkeypatch):
    monkeypatch.setattr(profile_scope.scope, "can_view_all", lambda staff: False)
    monkeypatch.setattr(profile_scope.scope, "actor_staff_id", lambda staff: 7)

    sql, params = profile_scope.project_staff_filter({"id": 7})

    assert "p.assigned_staff_id=?" in sql
    assert params == [7, 7]


def test_profile_scope_project_and_link_clauses():
    assert profile_scope.project_scope_clause(is_manager=True, project_ids=[], column="sa.project_id") == ("", [])
    assert profile_scope.project_scope_clause(is_manager=False, project_ids=[], column="sa.project_id") == (" AND 1=0", [])
    assert profile_scope.project_scope_clause(is_manager=False, project_ids=[1, 2], column="sa.project_id") == (
        " AND sa.project_id IN (?,?)",
        [1, 2],
    )
    assert profile_scope.link_scope_clause(is_manager=True, actor=7, project_ids=[1]) == ("", [])
    assert profile_scope.link_scope_clause(is_manager=False, actor=7, project_ids=[1, 2]) == (
        " AND (l.staff_id=? OR l.created_by_staff_id=? OR l.project_id IN (?,?))",
        [7, 7, 1, 2],
    )


def test_profile_scope_audit_where_parts():
    where, params = profile_scope.audit_where_parts(
        kol_id=9,
        project_ids=[1],
        link_ids=[2],
        sales=[{"id": "3"}, {"id": 0}],
        costs=[{"id": "4"}],
    )

    assert "(target_type='kol' AND target_id=?)" in where
    assert "(target_type='project' AND target_id IN (?))" in where
    assert "(target_type='link' AND target_id IN (?))" in where
    assert "(target_type='attribution' AND target_id IN (?))" in where
    assert "(target_type='cost' AND target_id IN (?))" in where
    assert params == ["9", "1", "2", "3", "4"]
