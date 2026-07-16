"""车道2·杂项债①:主 worker 认领 SQL 的双认领毒化防护单测。

断言两道过滤真实生效:
- payload 带 local_lease_id(本地算力租约标记)的行,主 worker 认领 SELECT 不选中;
- job_type 属本地算力专属类型(registry.SAFE_TASK_TYPES)的行,主 worker 不抢;
- payload 为 NULL / 无标记的普通行仍可正常被选中(不误伤)。

配方:对真 Postgres(本机 54329/viltrox2)建 TEMP TABLE apify_jobs —— pg_temp
schema 隐式排在 search_path 最前,worker 的 CLAIM_SELECT_SQL 未加 schema 前缀,
会命中临时表而绝不触真表;会话结束临时表自动消失,零残留。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.workers import apify_jobs_worker as worker  # noqa: E402
from app.domains.local_workers.registry import SAFE_TASK_TYPES  # noqa: E402


_PG_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/viltrox2")

_TEMP_SCHEMA = """
CREATE TEMP TABLE apify_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_type TEXT NOT NULL,
    payload JSONB,
    attempts INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    next_retry_at TIMESTAMPTZ,
    last_error_category TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


class ClaimSqlGuardShapeTests(unittest.TestCase):
    """纯 SQL 构造断言(不依赖数据库)。"""

    def test_guard_fragments_present_in_claim_sql(self) -> None:
        self.assertIn("(payload->>'local_lease_id') IS NULL", worker.CLAIM_LOCAL_GUARD_SQL)
        self.assertIn("job_type NOT IN (", worker.CLAIM_LOCAL_GUARD_SQL)
        for task_type in SAFE_TASK_TYPES:
            self.assertIn(f"'{task_type}'", worker.CLAIM_LOCAL_GUARD_SQL)
        self.assertIn(worker.CLAIM_LOCAL_GUARD_SQL, worker.CLAIM_SELECT_SQL)

    def test_exclusive_types_track_registry_truth_source(self) -> None:
        self.assertEqual(tuple(worker.LOCAL_EXCLUSIVE_JOB_TYPES), tuple(SAFE_TASK_TYPES))


@pytest.mark.pg
class ClaimSqlGuardBehaviorTests(unittest.TestCase):
    """真 PG 行为断言:TEMP TABLE 遮蔽真表,跑 worker 同一份 CLAIM_SELECT_SQL。"""

    def setUp(self) -> None:
        try:
            self.conn = psycopg.connect(_PG_URL)
        except psycopg.OperationalError as exc:
            self.skipTest(f"local PG unavailable: {exc}")
        self.conn.execute(_TEMP_SCHEMA)

    def tearDown(self) -> None:
        # 只回滚不提交:临时表随会话销毁,真表零接触。
        self.conn.rollback()
        self.conn.close()

    def _seed(self, job_type: str, payload_json: str | None, created_offset_secs: int) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO apify_jobs (job_type, payload, status, created_at)
                VALUES (%s, %s::jsonb, 'queued', NOW() + make_interval(secs => %s))
                RETURNING id
                """,
                (job_type, payload_json, created_offset_secs),
            )
            row = cur.fetchone()
            assert row is not None
            return int(row[0])

    def _claim_selected_id(self) -> int | None:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(worker.CLAIM_SELECT_SQL)
            row = cur.fetchone()
            return int(row["id"]) if row else None

    def test_locally_leased_row_is_not_claimed(self) -> None:
        # 被本地算力租走的行排最早(无过滤时必被选中),普通行排最晚。
        leased_id = self._seed("account_deep", '{"local_lease_id": "lease-abc"}', 0)
        normal_id = self._seed("account_deep", '{}', 10)
        selected = self._claim_selected_id()
        self.assertEqual(selected, normal_id)
        self.assertNotEqual(selected, leased_id)

    def test_local_exclusive_job_type_is_not_claimed(self) -> None:
        exclusive_id = self._seed(SAFE_TASK_TYPES[0], '{}', 0)
        normal_id = self._seed("account_deep", '{}', 10)
        selected = self._claim_selected_id()
        self.assertEqual(selected, normal_id)
        self.assertNotEqual(selected, exclusive_id)

    def test_null_payload_row_still_claimable(self) -> None:
        # 防误伤:payload 为 NULL(->> 返 SQL NULL)的普通行必须仍可被认领。
        null_payload_id = self._seed("account_deep", None, 0)
        selected = self._claim_selected_id()
        self.assertEqual(selected, null_payload_id)


if __name__ == "__main__":
    unittest.main()
