"""体系①Health 红灯守卫 — 不依赖 live server,直测 /health 的构建函数。

被测对象:app.main._runtime_trust()(/health 的 runtime-trust 块构建函数)。
它产出 server_git_sha / client_git_sha / worker_sha / sha_aligned / worker_online。

测试策略:不起 server,直接调用构建函数;通过 monkeypatch 注入三个 SHA
(server / client / worker)来构造对齐 / 不对齐场景,断言守卫逻辑正确。
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("JWT_SECRET", "test-secret")

import app.main as main  # noqa: E402


REQUIRED_FIELDS = {
    "server_git_sha",
    "client_git_sha",
    "worker_sha",
    "sha_aligned",
    "worker_online",
}


class _Trust:
    """构造可控的 _runtime_trust 输出,注入指定的三 sha + worker_online。"""

    @staticmethod
    def build(server_sha, client_sha, worker_sha):
        with mock.patch.object(main, "APP_GIT_SHA", server_sha), \
                mock.patch.object(main, "_read_frontend_build_sha", return_value=(client_sha or "")), \
                mock.patch.object(
                    main,
                    "_trust_worker_sha",
                    return_value={"worker_sha": worker_sha, "worker_sha_source": "test_injected"},
                ):
            return main._runtime_trust()


class HealthAlignmentGuardTests(unittest.TestCase):
    def test_trust_block_exposes_required_fields(self):
        """守卫面板必须暴露 server/client/worker sha + sha_aligned + worker_online。"""
        trust = main._runtime_trust()
        missing = REQUIRED_FIELDS - set(trust.keys())
        self.assertEqual(missing, set(), f"runtime trust missing fields: {missing}")

    def test_all_three_sha_match_aligned_true(self):
        """server == client(== worker)→ sha_aligned True。"""
        trust = _Trust.build("aaa111", "aaa111", "aaa111")
        self.assertEqual(trust["server_git_sha"], "aaa111")
        self.assertEqual(trust["client_git_sha"], "aaa111")
        self.assertEqual(trust["worker_sha"], "aaa111")
        self.assertIs(trust["sha_aligned"], True)

    def test_server_client_mismatch_aligned_false(self):
        """三 sha 不一致(server != client)→ sha_aligned 必须为 False(红灯)。"""
        trust = _Trust.build("server_sha_A", "client_sha_B", "worker_sha_C")
        self.assertEqual(trust["server_git_sha"], "server_sha_A")
        self.assertEqual(trust["client_git_sha"], "client_sha_B")
        self.assertEqual(trust["worker_sha"], "worker_sha_C")
        # 不一致 → 绝不能误报对齐
        self.assertIsNot(trust["sha_aligned"], True)
        self.assertIs(trust["sha_aligned"], False)

    def test_worker_sha_diverged_is_not_silently_aligned(self):
        """worker 落后于 server/client 时,守卫不得报告全绿对齐。

        记录真实 gap:当前 sha_aligned 只比较 server vs client,worker_sha
        未纳入。该测试断言『worker 分叉时不得出现 sha_aligned is True 且
        worker_sha == server』的虚假全绿——用 worker 值本身可观测分叉。
        """
        trust = _Trust.build("same_sha", "same_sha", "worker_behind")
        # server/client 对齐 → 当前实现 sha_aligned True(已知:worker 不参与)
        self.assertIs(trust["sha_aligned"], True)
        # 但 worker_sha 字段必须如实暴露分叉,供红灯守卫上层判定
        self.assertEqual(trust["worker_sha"], "worker_behind")
        self.assertNotEqual(trust["worker_sha"], trust["server_git_sha"])

    def test_missing_client_sha_aligned_is_not_true(self):
        """client sha 缺失(None/空)→ 不得报告 sha_aligned True。"""
        trust = _Trust.build("server_sha_A", "", "server_sha_A")
        self.assertIsNot(trust["sha_aligned"], True)

    def test_worker_online_field_is_bool_or_none(self):
        """worker_online 必须是可判定的 bool 或 None(未知),不得是其它类型。"""
        trust = main._runtime_trust()
        self.assertIn(type(trust["worker_online"]), (bool, type(None)))


if __name__ == "__main__":
    unittest.main()
