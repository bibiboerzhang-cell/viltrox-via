"""普通员工分组可见性:staff dict 只带 id(无 staff_id)时也要能看到自己是成员/创建者的组。"""
from __future__ import annotations

from app.domains.staff_groups import service as sg


def test_member_visible_with_id_key_only(monkeypatch):
    rows = [
        {"id": "g1", "name": "A", "member_ids": ["7"], "created_by": "9", "permissions": {}},
        {"id": "g2", "name": "B", "member_ids": ["8"], "created_by": "7", "permissions": {}},
        {"id": "g3", "name": "C", "member_ids": ["8"], "created_by": "9", "permissions": {}},
    ]
    monkeypatch.setattr(sg, "_group_row", lambda r: dict(r))

    class _Cur:
        def fetchall(self):
            return rows

    class _Conn:
        def execute(self, *a, **k):
            return _Cur()

    monkeypatch.setattr(sg, "get_conn", lambda: _Conn())
    out = sg.list_groups({"id": 7, "role": "member"})
    assert sorted(g["id"] for g in out["items"]) == ["g1", "g2"]
    out_mgr = sg.list_groups({"id": 1, "role": "admin"})
    assert len(out_mgr["items"]) == 3
