"""_fail_job 早退缺口修复单测:可重试类(retry)一旦重试预算耗尽,应走 status='triage'
而非掉进 else 落 status='failed' —— 否则永远绕过 triage 引擎死在死信池(审计:192 download +
152 media_resolve 全卡这条路)。

用一个最小 FakeConn 模拟 worker 用到的 psycopg 接口(transaction() 上下文 + dict_row 游标),
拦截 UPDATE 语句,断言「写进 apify_jobs 的 status 列」是 'triage' 而非 'failed'。
不依赖活 DB / 不碰红线 fit_score。
"""
from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.workers import apify_jobs_worker as worker  # noqa: E402


class _FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=None):
        s = " ".join(sql.split())
        self._conn.executed.append((s, params))
        # _fail_job 先 SELECT attempts FOR UPDATE → 回当前 attempts。
        if s.upper().startswith("SELECT ATTEMPTS"):
            self._conn._last_select = {"attempts": self._conn.attempts}
        return self

    def fetchone(self):
        # 兜住 SELECT attempts 与 UPDATE ... RETURNING next_retry_at。
        if self._conn._last_select is not None:
            row = self._conn._last_select
            self._conn._last_select = None
            return row
        return {"next_retry_at": None}


class FakeConn:
    """最小 psycopg 兼容:transaction() 上下文 + cursor(row_factory=...) 上下文。"""

    def __init__(self, attempts: int) -> None:
        self.attempts = attempts
        self.executed: list[tuple[str, object]] = []
        self._last_select: dict | None = None

    @contextmanager
    def transaction(self):
        yield self

    def cursor(self, row_factory=None):
        return _FakeCursor(self)

    def status_writes(self) -> list[str]:
        """从拦截到的 UPDATE 里提取写入的 status 字面量(SET status='xxx')。"""
        out = []
        for sql, _ in self.executed:
            up = sql.upper()
            if up.startswith("UPDATE APIFY_JOBS") and "SET STATUS=" in up:
                # 取 SET status='...' 的字面量
                frag = up.split("SET STATUS=", 1)[1]
                literal = frag.split(",", 1)[0].strip().strip("'")
                out.append(literal.lower())
        return out


class FailJobExhaustedRoutingTests(unittest.TestCase):
    def _run(self, *, exc_message: str, attempts: int):
        conn = FakeConn(attempts=attempts)
        with patch.object(worker, "_sync_search_session_job", lambda *a, **k: None), \
                patch.object(worker, "MAX_JOB_ATTEMPTS", 2), \
                patch.object(worker, "PROVIDER_RETRY_MAX_ATTEMPTS", 5):
            worker._fail_job(conn, 999, RuntimeError(exc_message))
        return conn

    def test_retryable_with_budget_requeues(self) -> None:
        # media_resolve(retry 类),attempts=0 → next_attempt=1 < 2 → 重新 queued。
        conn = self._run(exc_message="media_resolve_failed: media_resolve_failed", attempts=0)
        self.assertEqual(conn.status_writes(), ["queued"])

    def test_retryable_exhausted_goes_to_triage_not_failed(self) -> None:
        # 关键回归:download(retry 类),attempts=1 → next_attempt=2 >= 2(耗尽)。
        # 修复前:掉进 else 落 'failed';修复后:走 'triage'。
        conn = self._run(exc_message="direct_video_download_failed: yt-dlp exited 1", attempts=1)
        writes = conn.status_writes()
        self.assertIn("triage", writes)
        self.assertNotIn("failed", writes)

    def test_provider_pressure_exhausted_goes_to_triage(self) -> None:
        # provider_pressure 用更宽预算(5),attempts=5 → next_attempt=6 >= 5 耗尽 → triage(非 failed)。
        conn = self._run(exc_message="429 RESOURCE_EXHAUSTED quota exceeded", attempts=5)
        writes = conn.status_writes()
        self.assertIn("triage", writes)
        self.assertNotIn("failed", writes)

    def test_triage_class_still_goes_to_triage(self) -> None:
        # content_unavailable(triage 类)无论预算 → triage(行为不变)。
        conn = self._run(exc_message="precheck: youtube oEmbed 404 deleted", attempts=0)
        self.assertEqual(conn.status_writes(), ["triage"])

    def test_unknown_requeues_with_backoff_when_budget_left(self) -> None:
        # 90106365a 起 unknown 归入 retry 类(实测大半是瞬时环境问题,重跑即过):
        # attempts=0 → next_attempt=1 < 2(有预算)→ 重新 queued(带退避),不再直落 failed。
        conn = self._run(exc_message="something weird with no recognizable signal", attempts=0)
        self.assertEqual(conn.status_writes(), ["queued"])

    def test_unknown_exhausted_goes_to_triage(self) -> None:
        # unknown 走 retry 类既有归宿:预算耗尽(attempts=1 → next_attempt=2 >= 2)
        # 汇入 triage 可见池待人工,不再死在无人排水的 failed 池。
        conn = self._run(exc_message="something weird with no recognizable signal", attempts=1)
        writes = conn.status_writes()
        self.assertIn("triage", writes)
        self.assertNotIn("failed", writes)


if __name__ == "__main__":
    unittest.main()
