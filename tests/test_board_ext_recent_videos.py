"""board-ext recent_videos U2:final_v1 证据 modality 投影契约测试。

覆盖:CTE 只投影 modality 字符串(detail 不出库)、行级 viltrox_modalities 三种组合 +
旧结果诚实空、compat 红线(零 percent / 零 LIKE / 零注释)。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.kol import my_kol_board_ext as ext  # noqa: E402
from app.domains.kol import video_evidence_projection as projection  # noqa: E402


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, routes=None):
        self.routes = routes or {}

    def execute(self, sql, params=()):
        for known_sql, rows in self.routes.items():
            if sql == known_sql:
                return _Result(rows)
        return _Result([])


def test_recent_videos_sql_projects_modality_strings_only_and_keeps_compat_redlines():
    sql = ext.RECENT_VIDEOS_SQL
    assert sql.startswith(ext.V_CONTENT_CLASSIFIED_CTE)
    assert "vc.final_v1_viltrox_modalities AS llm_viltrox_modalities" in sql
    assert "jsonb_path_query_array" in ext.V_CONTENT_CLASSIFIED_CTE
    assert "[*].modality" in ext.V_CONTENT_CLASSIFIED_CTE
    assert "__FINAL_V1_MODALITIES_EXPR__" not in ext.V_CONTENT_CLASSIFIED_CTE
    assert "detail" not in ext.V_CONTENT_CLASSIFIED_CTE
    assert projection.FINAL_V1_MODALITIES_PG_EXPR in ext.V_CONTENT_CLASSIFIED_CTE
    assert "%" not in sql
    assert " LIKE " not in f" {sql.upper()} ".replace("\n", " ")
    assert "--" not in sql


def _row(evidence_id: int, modalities):
    return {
        "evidence_id": evidence_id, "kol_pool_id": 101, "project_id": None, "content_url": "",
        "platform": "tiktok", "title": "t", "video_title": "", "thumbnail_url": "", "view_count": 1,
        "like_count": 0, "publish_date": "2026-07-01", "posted_at": None, "created_at": None,
        "evidence_type": "video", "kol_name": "n", "kol_handle": "h", "has_final_v1_cache": 1,
        "llm_viltrox_status": "present", "llm_viltrox_detected_text": "true",
        "llm_viltrox_products": "[]", "llm_competitor_mentions": None,
        "llm_viltrox_modalities": modalities, "v_tier": "analysis_confirmed",
    }


def test_recent_videos_rows_carry_modality_subset_in_fixed_order(monkeypatch):
    monkeypatch.setattr(ext, "_now_utc", lambda: datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc))
    conn = _FakeConn(routes={
        ext.RECENT_VIDEOS_SQL: [
            _row(1, json.dumps(["visual"])),                                   # compat 把 jsonb 数组读成 JSON 文本
            _row(2, ["audio", "subtitle", "audio"]),                            # 两种 + 重复 + 乱序
            _row(3, json.dumps(["metadata", "audio", "visual", "subtitle"])),   # 三种,metadata 剔除
            _row(4, None),                                                      # 旧结果无证据块
            _row(5, "not-json"),                                                # 解析失败 fail-closed
        ],
    })
    items = {item["evidence_id"]: item["viltrox_modalities"] for item in ext.build_board_ext(conn)["recent_videos"]["items"]}
    assert items == {
        1: ["visual"],
        2: ["subtitle", "audio"],
        3: ["visual", "subtitle", "audio"],
        4: [],
        5: [],
    }
