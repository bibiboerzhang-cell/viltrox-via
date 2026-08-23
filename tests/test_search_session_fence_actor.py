"""搜索派生深析的围栏身份与漂移口径(2026-08-23「部分完成」三连根因):
① worker 侧代表作深析用会话创建者 staff 铸围栏;② diagnostics 等运行时键不进围栏哈希;
③ 跨会话复用解析结果时有活人 staff 走会话+操作者路径而非 409。"""
from __future__ import annotations

from app.domains.kol import provider_job_access as pja
from app.domains.kol.session_actor import session_creator_staff


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, mapping):
        self.mapping = mapping

    def execute(self, sql, params=()):
        for key, rows in self.mapping.items():
            if key in sql:
                return _Cur(rows)
        return _Cur([])


def test_session_creator_staff_maps_user_id_to_active_staff():
    conn = _Conn({
        "vkpi_kol_search_sessions": [{"created_by": 1}],
        "FROM staff": [{"id": 40, "user_id": 1, "is_owner": 1, "active": 1}],
    })
    staff = session_creator_staff(conn, 1134)
    assert staff and staff["id"] == 40
    assert session_creator_staff(conn, 0) is None
    assert session_creator_staff(_Conn({"vkpi_kol_search_sessions": [{"created_by": 0}]}), 5) is None


def test_runtime_diagnostic_keys_are_outside_the_fence_hash():
    assert "diagnostics" in pja._MUTABLE_RUNTIME_KEYS
    assert "search_session_item_statuses" in pja._MUTABLE_RUNTIME_KEYS
    a = pja._execution_contract({"target_id": "9", "diagnostics": {"x": 1}}, action=pja.VIDEO_ANALYSIS)
    b = pja._execution_contract({"target_id": "9", "diagnostics": {"y": 2, "child_stderr_tail": "s"}}, action=pja.VIDEO_ANALYSIS)
    assert a == b


def test_profile_videos_enqueue_uses_session_creator_staff():
    from pathlib import Path

    src = Path(pja.__file__).resolve().parents[0].joinpath("url_deep_crawl_execute_profile_videos.py").read_text(encoding="utf-8")
    assert "_session_creator_staff(conn, body.get(\"search_session_id\"))" in src


def test_cross_session_resolve_reuse_falls_back_to_staff_path():
    from pathlib import Path

    src = Path(pja.__file__).resolve().parents[0].joinpath("video_analysis_job_access.py").read_text(encoding="utf-8")
    assert "if isinstance(staff, dict):" in src and "search_session_target_drifted" in src
