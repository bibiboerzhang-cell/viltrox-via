"""分支覆盖冲刺·audience/authenticity.py — 四路信号阈值带/低样本闸/综合分聚合。

覆盖:评论者重复率三档、模板化率三档(emoji/短评/重复文三判)、
互动离群池分位两头带、既有假粉列三态、主入口空态与扣分求和。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.audience import authenticity as au  # noqa: E402


class FakeCursor:
    def __init__(self, rows: list | None = None, row: Any = None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, responders: list[tuple[str, Any]] | None = None):
        self.responders = responders or []
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()):  # noqa: ANN001
        self.executed.append((sql, params))
        for fragment, resp in self.responders:
            if fragment in sql:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return FakeCursor()


def _c(cid, text="hello there", author=None, handle=None):
    return {"id": cid, "comment_text": text, "author_id": author,
            "author_handle": handle, "likes_count": 0, "created_at": None, "post_id": 1}


class WordishTests(unittest.TestCase):
    def test_unicode_letters_count_as_wordish(self):
        self.assertTrue(au._has_wordish("مرحبا"))  # 阿拉伯语不是纯 emoji
        self.assertTrue(au._has_wordish("你好"))
        self.assertTrue(au._has_wordish("abc123"))
        self.assertFalse(au._has_wordish("🔥🔥 !!"))


class CommenterRepeatTests(unittest.TestCase):
    def test_no_identities_is_empty(self):
        out = au._commenter_repeat_signal([_c(1), _c(2)])
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["deduction"], 0)

    def test_author_id_preferred_over_handle(self):
        comments = [_c(1, author="A1", handle="ignored"), _c(2, handle="H2")]
        out = au._commenter_repeat_signal(comments)
        self.assertEqual(out["comments_with_author"], 2)
        self.assertEqual(out["unique_commenters"], 2)

    def test_low_sample_no_deduction(self):
        comments = [_c(i, author="same") for i in range(5)]
        out = au._commenter_repeat_signal(comments)
        self.assertEqual(out["level"], "none")
        self.assertEqual(out["deduction"], 0)
        self.assertIn("样本仅 5", out["note"])

    def test_warn_band_caps_deduction(self):
        comments = [_c(i, author="dup") for i in range(6)] + [_c(10 + i, author=f"u{i}") for i in range(4)]
        out = au._commenter_repeat_signal(comments)
        self.assertEqual(out["level"], "warn")
        self.assertEqual(out["deduction"], au.DEDUCT_CAP_REPEAT)
        self.assertEqual(out["repeat_comment_share"], 0.6)
        self.assertEqual(out["top_repeat_commenters"][0], {"author": "dup", "comments": 6})

    def test_info_bands(self):
        # share 0.4 → info/15
        comments = [_c(i, author="dup") for i in range(4)] + [_c(10 + i, author=f"u{i}") for i in range(6)]
        out = au._commenter_repeat_signal(comments)
        self.assertEqual((out["level"], out["deduction"]), ("info", 15))
        # share 0.25 → info/8
        comments = [_c(i, author="dup") for i in range(3)] + [_c(10 + i, author=f"u{i}") for i in range(9)]
        out = au._commenter_repeat_signal(comments)
        self.assertEqual((out["level"], out["deduction"]), ("info", 8))

    def test_all_unique_is_clean(self):
        comments = [_c(i, author=f"u{i}") for i in range(12)]
        out = au._commenter_repeat_signal(comments)
        self.assertEqual((out["level"], out["deduction"]), ("none", 0))


class TemplateSignalTests(unittest.TestCase):
    def test_no_texts_is_empty(self):
        out = au._template_signal([_c(1, text=""), _c(2, text=None)])
        self.assertEqual(out["status"], "empty")

    def test_low_sample_no_deduction(self):
        out = au._template_signal([_c(i, text="🔥") for i in range(5)])
        self.assertEqual(out["deduction"], 0)
        self.assertIn("样本仅 5", out["note"])

    def test_warn_band_with_breakdown(self):
        comments = (
            [_c(i, text="🔥🔥") for i in range(4)]              # 纯 emoji(且 ≥3 同文重复)
            + [_c(10 + i, text="nice") for i in range(4)]        # ≤5 字符短评 + 重复
            + [_c(20 + i, text=f"long unique comment number {i}") for i in range(2)]
        )
        out = au._template_signal(comments)
        self.assertEqual(out["level"], "warn")
        self.assertEqual(out["deduction"], au.DEDUCT_CAP_TEMPLATE)
        self.assertEqual(out["templated_share"], 0.8)
        self.assertEqual(out["breakdown"]["emoji_only"], 4)
        self.assertEqual(out["breakdown"]["very_short"], 4)
        self.assertEqual(out["breakdown"]["duplicated_text"], 8)
        self.assertTrue(any(e["count"] >= 3 for e in out["duplicated_examples"]))

    def test_info_bands_and_clean(self):
        # share 0.5 → info/15
        comments = [_c(i, text="ok") for i in range(5)] + [
            _c(10 + i, text=f"real thoughtful words {i}") for i in range(5)
        ]
        out = au._template_signal(comments)
        self.assertEqual((out["level"], out["deduction"]), ("info", 15))
        # share 0.4 → info/8
        comments = [_c(i, text="ok") for i in range(4)] + [
            _c(10 + i, text=f"real thoughtful words {i}") for i in range(6)
        ]
        out = au._template_signal(comments)
        self.assertEqual((out["level"], out["deduction"]), ("info", 8))
        # 全部长独文 → none
        comments = [_c(i, text=f"genuinely unique take number {i}") for i in range(12)]
        out = au._template_signal(comments)
        self.assertEqual((out["level"], out["deduction"]), ("none", 0))


class EngagementOutlierTests(unittest.TestCase):
    def _evidence_rows(self, per_kol_rates: dict[int, list[float]], views: int = 1000):
        rows = []
        for kid, rates in per_kol_rates.items():
            for rate in rates:
                rows.append({
                    "kol_pool_id": kid, "view_count": views,
                    "like_count": int(views * rate), "comment_count": 0, "is_active": 1,
                })
        return rows

    def _conn(self, rows):
        return FakeConn([("FROM vkpi_kol_video_evidence", FakeCursor(rows=rows))])

    def test_own_kol_without_enough_videos_is_empty(self):
        rows = self._evidence_rows({1: [0.01, 0.01]})  # 仅 2 条 < 3
        out = au._engagement_outlier_signal(self._conn(rows), 1)
        self.assertEqual(out["status"], "empty")
        self.assertIn("不足 3", out["reason"])

    def test_small_pool_is_empty(self):
        rows = self._evidence_rows({k: [0.01] * 3 for k in range(1, 6)})  # 5 KOL < 30
        out = au._engagement_outlier_signal(self._conn(rows), 1)
        self.assertEqual(out["status"], "empty")
        self.assertIn("<30", out["reason"])

    def test_extreme_high_percentile_warns(self):
        # 50 个 KOL:below=49/len=50 → 分位恰 0.98 触 warn
        pool = {k: [0.01] * 3 for k in range(1, 50)}
        pool[51] = [0.9] * 3  # 目标 KOL 遥遥领先
        out = au._engagement_outlier_signal(self._conn(self._evidence_rows(pool)), 51)
        self.assertEqual(out["level"], "warn")
        self.assertEqual(out["deduction"], au.DEDUCT_CAP_ENGAGEMENT)
        self.assertIn("极端偏高", out["direction"])

    def test_extreme_low_percentile_info(self):
        pool = {k: [0.05 + k * 0.001] * 3 for k in range(1, 31)}
        pool[31] = [0.0001] * 3
        out = au._engagement_outlier_signal(self._conn(self._evidence_rows(pool)), 31)
        self.assertEqual(out["level"], "info")
        self.assertEqual(out["deduction"], 10)
        self.assertIn("极端偏低", out["direction"])

    def test_middle_band_is_clean(self):
        pool = {k: [0.01 + k * 0.001] * 3 for k in range(1, 32)}
        out = au._engagement_outlier_signal(self._conn(self._evidence_rows(pool)), 15)
        self.assertEqual(out["level"], "none")
        self.assertEqual(out["deduction"], 0)
        self.assertIn("正常带", out["direction"])

    def test_inactive_and_zero_view_rows_excluded(self):
        rows = [
            {"kol_pool_id": 1, "view_count": 1000, "like_count": 10, "comment_count": 0, "is_active": 0},
            {"kol_pool_id": 1, "view_count": 0, "like_count": 10, "comment_count": 0, "is_active": 1},
            {"kol_pool_id": 1, "view_count": None, "like_count": 10, "comment_count": 0, "is_active": 1},
        ]
        out = au._engagement_outlier_signal(self._conn(rows), 1)
        self.assertEqual(out["status"], "empty")


class InflationSignalTests(unittest.TestCase):
    def test_never_checked_is_honest_gap(self):
        out = au._inflation_signal({"inflation_checked_at": None})
        self.assertEqual(out["status"], "empty")
        self.assertIn("不当 clean", out["reason"])

    def test_flagged_warns_with_cap(self):
        pool = {
            "inflation_checked_at": "2026-05-01T00:00:00Z",
            "suspect_inflation": 1,  # BOOLEAN 读回 int
            "inflation_reason": "follower spike",
            "inflation_signals_json": json.dumps({"spike": True}),
        }
        out = au._inflation_signal(pool)
        self.assertEqual(out["level"], "warn")
        self.assertEqual(out["deduction"], au.DEDUCT_CAP_INFLATION)
        self.assertTrue(out["suspect_inflation"])
        self.assertEqual(out["signals"], {"spike": True})

    def test_checked_clean_is_none_level(self):
        pool = {"inflation_checked_at": "2026-05-01T00:00:00Z", "suspect_inflation": 0}
        out = au._inflation_signal(pool)
        self.assertEqual(out["level"], "none")
        self.assertFalse(out["suspect_inflation"])
        self.assertIn("非背书", out["note"])


class LoadKolCommentsTests(unittest.TestCase):
    def test_dual_bridge_merges_and_dedupes(self):
        direct = [_c(1), _c(2)]
        bridged = [_c(2), _c(3)]  # id=2 与直连重复
        conn = FakeConn([
            ("WHERE account_id = ?", FakeCursor(rows=direct)),
            ("FROM vkpi_kol_video_evidence", FakeCursor(rows=[{"id": 77}])),
            ("post_table IN", FakeCursor(rows=bridged)),
        ])
        out = au.load_kol_comments(conn, 5)
        self.assertEqual(sorted(c["id"] for c in out), [1, 2, 3])

    def test_no_evidence_skips_bridge_query(self):
        conn = FakeConn([
            ("WHERE account_id = ?", FakeCursor(rows=[_c(1)])),
            ("FROM vkpi_kol_video_evidence", FakeCursor(rows=[])),
        ])
        out = au.load_kol_comments(conn, 5)
        self.assertEqual(len(out), 1)
        bridge_sqls = [s for s, _ in conn.executed if "post_table IN" in s]
        self.assertEqual(bridge_sqls, [])


class AuthenticitySignalTests(unittest.TestCase):
    _POOL = {
        "id": 3, "handle": "h", "display_name": "D", "platform": "youtube",
        "suspect_inflation": None, "inflation_reason": None,
        "inflation_signals_json": None, "inflation_checked_at": None,
    }

    def test_unknown_kol_raises(self):
        conn = FakeConn([("FROM vkpi_kol_pool WHERE id", FakeCursor(row=None))])
        with self.assertRaises(LookupError):
            au.authenticity_signal(3, conn=conn)

    def test_all_signals_empty_no_score(self):
        conn = FakeConn([
            ("FROM vkpi_kol_pool WHERE id", FakeCursor(row=dict(self._POOL))),
        ])
        out = au.authenticity_signal(3, conn=conn)
        self.assertEqual(out["status"], "empty")
        self.assertIsNone(out["authenticity_score"])
        self.assertEqual(out["signals_used"], [])
        self.assertEqual(out["confidence"]["label"], "low")

    def test_ready_path_sums_deductions(self):
        pool = dict(self._POOL, inflation_checked_at="2026-05-01T00:00:00Z", suspect_inflation=1,
                    inflation_reason="spike", inflation_signals_json="{}")
        comments = [_c(i, text=f"real long comment {i}", author=f"u{i}") for i in range(60)]
        conn = FakeConn([
            ("FROM vkpi_kol_pool WHERE id", FakeCursor(row=pool)),
            ("WHERE account_id = ?", FakeCursor(rows=comments)),
            ("FROM vkpi_kol_video_evidence WHERE kol_pool_id", FakeCursor(rows=[])),
        ])
        out = au.authenticity_signal(3, conn=conn)
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["authenticity_score"], 100 - au.DEDUCT_CAP_INFLATION)
        self.assertEqual(out["deductions"], [
            {"signal": "inflation_flag", "points": au.DEDUCT_CAP_INFLATION, "level": "warn"},
        ])
        self.assertEqual(out["confidence"]["label"], "high")
        self.assertIn("commenter_repeat", out["signals_used"])
        self.assertIn("inflation_flag", out["signals_used"])

    def test_comment_bridge_failure_degrades_not_crashes(self):
        pool = dict(self._POOL, inflation_checked_at="2026-05-01T00:00:00Z", suspect_inflation=0)
        conn = FakeConn([
            ("FROM vkpi_kol_pool WHERE id", FakeCursor(row=pool)),
            ("WHERE account_id = ?", RuntimeError("comments table gone")),
        ])
        out = au.authenticity_signal(3, conn=conn)
        self.assertEqual(out["status"], "ready")  # 假粉列仍可用
        self.assertEqual(out["authenticity_score"], 100)
        self.assertEqual(out["signals_used"], ["inflation_flag"])


if __name__ == "__main__":
    unittest.main()
