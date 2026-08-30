"""分支覆盖冲刺·kol/leadtime_competing.py — 制作周期配对/竞业监控的边界与降级分支。

覆盖:时间解析多形态、窗口签收锚回退链、贴配对三级优先与出界计数、
主/副链聚合与诚实空态、竞品露出去重/undated 桶/副源防御读。
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.db.connection as db_connection  # noqa: E402
from app.domains.kol import leadtime_competing as lc  # noqa: E402


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
                return resp
        return FakeCursor()

    def commit(self):
        pass


class PatchDbMixin:
    def _use_conn(self, conn: FakeConn, tables_exist: bool = True):
        self._orig_get_conn = db_connection.get_conn
        self._orig_table_exists = db_connection.table_exists
        db_connection.get_conn = lambda: conn  # type: ignore[assignment]
        db_connection.table_exists = lambda name: tables_exist  # type: ignore[assignment]
        self.addCleanup(self._restore_db)

    def _restore_db(self):
        db_connection.get_conn = self._orig_get_conn  # type: ignore[assignment]
        db_connection.table_exists = self._orig_table_exists  # type: ignore[assignment]


class ParseHelpersTests(unittest.TestCase):
    def test_loads_dual_mode(self):
        self.assertEqual(lc._loads({"a": 1}), {"a": 1})
        self.assertEqual(lc._loads("[1]"), [1])
        self.assertIsNone(lc._loads("{bad"))
        self.assertIsNone(lc._loads("  "))
        self.assertIsNone(lc._loads(None))

    def test_parse_ts_forms(self):
        self.assertIsNone(lc._parse_ts(None))
        self.assertIsNone(lc._parse_ts(""))
        self.assertIsNone(lc._parse_ts("not-a-date"))
        aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(lc._parse_ts(aware), aware)
        naive = datetime(2026, 1, 1, 12, 0, 0)
        self.assertEqual(lc._parse_ts(naive).tzinfo, timezone.utc)
        from datetime import date

        d = lc._parse_ts(date(2026, 6, 28))
        self.assertEqual((d.year, d.month, d.day), (2026, 6, 28))
        # str(datetime) 空格写法
        self.assertEqual(
            lc._parse_ts("2026-06-17 18:07:56+00:00"),
            datetime(2026, 6, 17, 18, 7, 56, tzinfo=timezone.utc),
        )
        # compat Z 写法
        self.assertEqual(
            lc._parse_ts("2026-07-05T02:37:20Z"),
            datetime(2026, 7, 5, 2, 37, 20, tzinfo=timezone.utc),
        )
        # DATE 列
        self.assertEqual(
            lc._parse_ts("2026-06-28"), datetime(2026, 6, 28, tzinfo=timezone.utc)
        )

    def test_iso_and_median(self):
        self.assertIsNone(lc._iso(None))
        self.assertEqual(
            lc._iso(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)),
            "2026-01-02T03:04:05Z",
        )
        self.assertIsNone(lc._median([]))
        self.assertEqual(lc._median([3.0, 1.0, 2.0]), 2.0)
        self.assertEqual(lc._median([1.0, 2.0, 3.0, 4.0]), 2.5)


class WindowDeliveredAtTests(unittest.TestCase):
    def test_metadata_delivered_at_wins(self):
        win = {"metadata_json": json.dumps({"delivered_at": "2026-05-01T00:00:00Z"}),
               "starts_at": "2026-05-20"}
        self.assertEqual(
            lc._window_delivered_at(win), datetime(2026, 5, 1, tzinfo=timezone.utc)
        )

    def test_fallback_starts_minus_default_offset(self):
        win = {"metadata_json": "{}", "starts_at": "2026-05-10"}
        self.assertEqual(
            lc._window_delivered_at(win), datetime(2026, 5, 3, tzinfo=timezone.utc)
        )

    def test_custom_offset_used(self):
        win = {"metadata_json": json.dumps({"window_offset_days": [3, 30]}),
               "starts_at": "2026-05-10"}
        self.assertEqual(
            lc._window_delivered_at(win), datetime(2026, 5, 7, tzinfo=timezone.utc)
        )

    def test_garbage_offset_falls_back_to_seven(self):
        win = {"metadata_json": json.dumps({"window_offset_days": ["x"]}),
               "starts_at": "2026-05-10"}
        self.assertEqual(
            lc._window_delivered_at(win), datetime(2026, 5, 3, tzinfo=timezone.utc)
        )

    def test_no_anchor_returns_none(self):
        self.assertIsNone(lc._window_delivered_at({"metadata_json": None, "starts_at": None}))


class PickPostTests(unittest.TestCase):
    _DELIVERED = datetime(2026, 5, 1, tzinfo=timezone.utc)

    def _post(self, pid, published, assignment_id=None, project_id=None, kol=None):
        return {
            "id": pid, "assignment_id": assignment_id, "project_id": project_id,
            "kol_pool_id": kol, "published_at": published,
        }

    def test_matched_id_beats_assignment_and_project(self):
        win = {"matched_content_post_id": 1, "assignment_id": 5, "project_id": 9, "kol_pool_id": 3}
        posts = [
            self._post(2, "2026-05-02T00:00:00Z", assignment_id=5),
            self._post(3, "2026-05-02T00:00:00Z", project_id=9, kol=3),
            self._post(1, "2026-05-09T00:00:00Z"),
        ]
        best, invalid = lc._pick_post_for_window(win, self._DELIVERED, posts)
        self.assertEqual(best["id"], 1)
        self.assertEqual(invalid, 0)

    def test_same_rank_earliest_publish_wins(self):
        win = {"matched_content_post_id": None, "assignment_id": 5, "project_id": 9, "kol_pool_id": 3}
        posts = [
            self._post(2, "2026-05-08T00:00:00Z", assignment_id=5),
            self._post(4, "2026-05-03T00:00:00Z", assignment_id=5),
        ]
        best, _ = lc._pick_post_for_window(win, self._DELIVERED, posts)
        self.assertEqual(best["id"], 4)

    def test_out_of_range_pairs_counted_not_sampled(self):
        win = {"matched_content_post_id": None, "assignment_id": 5, "project_id": 9, "kol_pool_id": 3}
        posts = [
            self._post(2, "2026-04-25T00:00:00Z", assignment_id=5),   # published < delivered
            self._post(3, "2027-05-02T00:00:00Z", assignment_id=5),   # > MAX_LEADTIME_DAYS
        ]
        best, invalid = lc._pick_post_for_window(win, self._DELIVERED, posts)
        self.assertIsNone(best)
        self.assertEqual(invalid, 2)

    def test_post_without_published_not_counted_invalid(self):
        win = {"matched_content_post_id": None, "assignment_id": 5, "project_id": 9, "kol_pool_id": 3}
        posts = [self._post(2, None, assignment_id=5)]
        best, invalid = lc._pick_post_for_window(win, self._DELIVERED, posts)
        self.assertIsNone(best)
        self.assertEqual(invalid, 0)

    def test_unrelated_posts_ignored(self):
        win = {"matched_content_post_id": None, "assignment_id": 5, "project_id": 9, "kol_pool_id": 3}
        posts = [self._post(2, "2026-05-02T00:00:00Z", assignment_id=6, project_id=8, kol=4)]
        best, invalid = lc._pick_post_for_window(win, self._DELIVERED, posts)
        self.assertIsNone(best)
        self.assertEqual(invalid, 0)


class ProductionLeadtimeTests(PatchDbMixin, unittest.TestCase):
    def test_unknown_pool_raises(self):
        conn = FakeConn([("FROM vkpi_kol_pool", FakeCursor(row=None))])
        self._use_conn(conn)
        with self.assertRaises(LookupError):
            lc.production_leadtime(3)

    def test_empty_chains_report_honest_empty(self):
        conn = FakeConn([("FROM vkpi_kol_pool", FakeCursor(row={"id": 3}))])
        self._use_conn(conn)
        out = lc.production_leadtime(3)
        self.assertEqual(out["status"], "empty")
        self.assertIsNone(out["median_days"])
        self.assertEqual(out["sample_count"], 0)
        self.assertEqual(out["diagnostics"]["windows_seen"], 0)
        self.assertFalse(out["llm_calls"])

    def test_window_chain_and_stage_chain_combined(self):
        windows = [{
            "id": 1, "project_id": 100, "assignment_id": 50, "kol_pool_id": 3,
            "starts_at": "2026-05-10", "status": "open", "matched_content_post_id": None,
            "metadata_json": json.dumps({"delivered_at": "2026-05-01T00:00:00Z"}),
        }]
        posts = [{
            "id": 9, "project_id": 100, "assignment_id": 50, "kol_pool_id": 3,
            "published_at": "2026-05-05T00:00:00Z", "status": "published", "content_url": "u",
        }]
        stage_rows = [
            # project 100 也有阶段事件,但主链已取样必须跳过
            {"project_id": 100, "to_stage": "received", "effective_at": "2026-05-01T00:00:00Z"},
            {"project_id": 100, "to_stage": "published", "effective_at": "2026-05-21T00:00:00Z"},
            # project 200 贡献副链样本(10 天)
            {"project_id": 200, "to_stage": "received", "effective_at": "2026-05-01T00:00:00Z"},
            {"project_id": 200, "to_stage": "published", "effective_at": "2026-05-11T00:00:00Z"},
            # project 300 间隔越界:计入 pairs_out_of_range
            {"project_id": 300, "to_stage": "received", "effective_at": "2025-01-01T00:00:00Z"},
            {"project_id": 300, "to_stage": "published", "effective_at": "2025-12-01T00:00:00Z"},
        ]
        conn = FakeConn([
            ("FROM vkpi_kol_pool", FakeCursor(row={"id": 3})),
            ("FROM vkpi_project_content_observation_windows", FakeCursor(rows=windows)),
            ("FROM vkpi_project_content_posts", FakeCursor(rows=posts)),
            ("FROM vkpi_project_stage_events", FakeCursor(rows=stage_rows)),
        ])
        self._use_conn(conn)
        out = lc.production_leadtime(3)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["sample_count"], 2)
        sources = sorted(s["source"] for s in out["samples"])
        self.assertEqual(sources, ["observation_window", "stage_events"])
        days_by_source = {s["source"]: s["days"] for s in out["samples"]}
        self.assertEqual(days_by_source["observation_window"], 4.0)
        self.assertEqual(days_by_source["stage_events"], 10.0)
        self.assertEqual(out["median_days"], 7.0)
        self.assertEqual(out["diagnostics"]["pairs_out_of_range"], 1)
        self.assertEqual(out["diagnostics"]["stage_projects_seen"], 3)

    def test_published_before_received_makes_incomplete_pair(self):
        stage_rows = [
            {"project_id": 7, "to_stage": "published", "effective_at": "2026-05-01T00:00:00Z"},
            {"project_id": 7, "to_stage": "received", "effective_at": "2026-05-05T00:00:00Z"},
        ]
        conn = FakeConn([
            ("FROM vkpi_kol_pool", FakeCursor(row={"id": 3})),
            ("FROM vkpi_project_stage_events", FakeCursor(rows=stage_rows)),
        ])
        self._use_conn(conn)
        out = lc.production_leadtime(3)
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["diagnostics"]["pairs_out_of_range"], 0)


class MentionEntriesTests(unittest.TestCase):
    def test_non_list_and_json_string(self):
        self.assertEqual(lc._mention_entries({"brand": "x"}), [])
        self.assertEqual(lc._mention_entries(None), [])
        out = lc._mention_entries('[{"brand": "Sony", "scene": "b-roll", "risk": "low"}]')
        self.assertEqual(out, [{"brand": "Sony", "scene": "b-roll", "risk": "low"}])

    def test_mixed_shapes_and_blank_brands(self):
        out = lc._mention_entries([
            {"brand": "  Sigma "}, {"brand": ""}, "Canon", "", 0,
        ])
        self.assertEqual(
            out,
            [
                {"brand": "Sigma", "scene": "", "risk": ""},
                {"brand": "Canon", "scene": "", "risk": ""},
            ],
        )


class BrandHelperFallbackTests(unittest.TestCase):
    def test_fallback_when_exposure_helper_broken(self):
        from app.domains.kol import competitor_exposure as ce

        original = ce._known_brand_lookup

        def boom():
            raise RuntimeError("lookup broken")

        ce._known_brand_lookup = boom
        try:
            canon, display = lc._brand_helpers()
        finally:
            ce._known_brand_lookup = original
        self.assertEqual(canon("  SoNy "), "sony")
        self.assertEqual(canon(None), "")
        self.assertEqual(display("sony"), "Sony")
        self.assertEqual(display(""), "")


class CompetingActivityTests(PatchDbMixin, unittest.TestCase):
    def setUp(self):
        self._orig_helpers = lc._brand_helpers
        lc._brand_helpers = lambda: (  # type: ignore[assignment]
            lambda raw: str(raw or "").strip().lower(),
            lambda key: key.title() if key else key,
        )
        self.addCleanup(lambda: setattr(lc, "_brand_helpers", self._orig_helpers))

    def _recent(self, days_ago: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_unknown_pool_raises(self):
        conn = FakeConn([("FROM vkpi_kol_pool", FakeCursor(row=None))])
        self._use_conn(conn)
        with self.assertRaises(LookupError):
            lc.competing_activity(3)

    def test_window_days_clamped(self):
        conn = FakeConn([("FROM vkpi_kol_pool", FakeCursor(row={"id": 3}))])
        self._use_conn(conn)
        self.assertEqual(lc.competing_activity(3, window_days=1)["window_days"], 7)
        self.assertEqual(lc.competing_activity(3, window_days=9999)["window_days"], 365)
        self.assertEqual(lc.competing_activity(3, window_days=None)["window_days"], 90)

    def test_empty_scan_is_empty_status(self):
        conn = FakeConn([("FROM vkpi_kol_pool", FakeCursor(row={"id": 3}))])
        self._use_conn(conn, tables_exist=False)
        out = lc.competing_activity(3)
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["items"], [])
        self.assertEqual(out["brand_signal_rows"], 0)

    def test_mentions_dedupe_viltrox_excluded_and_undated_bucket(self):
        rows = [
            {
                "evidence_id": 1, "content_url": "u1", "video_title": "t1", "platform": "yt",
                "posted_at": self._recent(5),
                # mentions 与 presence 同品牌重复 → 视频×品牌只记一次;viltrox 剔除
                "competitor_mentions": json.dumps([
                    {"brand": "Sony", "scene": "s", "risk": "r"},
                    {"brand": "Viltrox"},
                ]),
                "competitor_presence": json.dumps([{"brand": "sony"}, {"brand": "Sigma"}]),
            },
            {
                "evidence_id": 2, "content_url": "u2", "video_title": "t2", "platform": "yt",
                "posted_at": None,  # undated 桶
                "competitor_mentions": json.dumps([{"brand": "Canon"}]),
                "competitor_presence": None,
            },
            {
                "evidence_id": 3, "content_url": "u3", "video_title": "t3", "platform": "yt",
                "posted_at": self._recent(200),  # DATE 边界后精确 cutoff 再滤一遍
                "competitor_mentions": json.dumps([{"brand": "Nikon"}]),
                "competitor_presence": None,
            },
        ]
        conn = FakeConn([
            ("FROM vkpi_kol_pool", FakeCursor(row={"id": 3})),
            ("FROM vkpi_analysis_cache", FakeCursor(rows=rows)),
        ])
        self._use_conn(conn, tables_exist=False)
        out = lc.competing_activity(3, window_days=90)
        self.assertEqual(out["status"], "ok")
        dated_brands = sorted(i["brand"] for i in out["items"])
        self.assertEqual(dated_brands, ["Sigma", "Sony"])
        self.assertEqual([i["brand"] for i in out["undated_items"]], ["Canon"])
        counts = {b["brand"]: b["count"] for b in out["brands"]}
        self.assertEqual(counts, {"Sony": 1, "Sigma": 1, "Canon": 1})
        self.assertNotIn("Nikon", counts)
        self.assertEqual(out["scanned_videos"], 3)

    def test_brand_signal_secondary_source_merged(self):
        signal_rows = [
            {"brand_name": "Sony", "published_at": self._recent(3), "post_url": "u1",
             "platform": "yt", "signal_type": "mention", "brand_role": "sponsor"},
            {"brand_name": "viltrox", "published_at": self._recent(3), "post_url": "u2",
             "platform": "yt", "signal_type": "mention", "brand_role": ""},
            {"brand_name": "Canon", "published_at": self._recent(400), "post_url": "u3",
             "platform": "yt", "signal_type": "mention", "brand_role": ""},
        ]
        conn = FakeConn([
            ("FROM vkpi_kol_pool", FakeCursor(row={"id": 3})),
            ("FROM vkpi_analysis_cache", FakeCursor(rows=[])),
            ("FROM vkpi_brand_signal", FakeCursor(rows=signal_rows)),
        ])
        self._use_conn(conn, tables_exist=True)
        out = lc.competing_activity(3)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["brand_signal_rows"], 3)
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["brand"], "Sony")
        self.assertEqual(out["items"][0]["source"], "brand_signal")
        self.assertEqual(out["items"][0]["scene"], "mention")


if __name__ == "__main__":
    unittest.main()
