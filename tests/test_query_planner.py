"""N6 数据问数 · query_planner 单测(白名单 / 防注入 / 结构化结果)。

覆盖:
  - 意图匹配:中英文关键词命中正确意图;噪声问题不误命中。
  - 白名单不可注入:用户问题文本绝不进 SQL;非法 intent_key 不命中;
    assert_safe_sql 拒绝非 SELECT / 多语句 / 非白名单表 / 写关键词。
  - 至少 3 个意图返回 {intent, columns, rows, sql_explain} 结构(用内存 sqlite 真跑 SQL)。

用内存 sqlite(? 占位与本仓 compat 同风格)真执行,免活 DB,CI 友好。
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.analytics import query_planner as qp  # noqa: E402


def _make_conn() -> sqlite3.Connection:
    """建一个最小白名单表的内存库并塞少量行,使 3+ 意图能真跑出结构。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE kols (id INTEGER PRIMARY KEY, channel_name TEXT, country TEXT);
        CREATE TABLE vkpi_sample_assets (
            id INTEGER PRIMARY KEY, project_id INTEGER, kol_id INTEGER,
            product_sku TEXT, sample_cost_cents INTEGER, currency TEXT,
            received_at TEXT, created_at TEXT
        );
        CREATE TABLE vkpi_content_posts (
            id INTEGER PRIMARY KEY, project_id INTEGER, kol_id INTEGER,
            views INTEGER, likes INTEGER, comments INTEGER, published_at TEXT
        );
        CREATE TABLE vkpi_kol_claims (
            id INTEGER PRIMARY KEY, staff_id INTEGER, status TEXT
        );
        CREATE TABLE vkpi_channel_metrics (
            id INTEGER PRIMARY KEY, channel_id INTEGER,
            followers_delta INTEGER, views_delta_24h INTEGER, captured_at TEXT
        );
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY, platform TEXT, followers INTEGER,
            duplicate_of_id INTEGER,
            updated_at TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY, kol_pool_id INTEGER, title TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    cur.execute("INSERT INTO kols (id, channel_name, country) VALUES (1, 'Alice', 'US')")
    cur.execute("INSERT INTO kols (id, channel_name, country) VALUES (2, 'Bob', 'DE')")
    cur.execute(
        "INSERT INTO vkpi_sample_assets "
        "(id, project_id, kol_id, product_sku, sample_cost_cents, currency, received_at, created_at) "
        "VALUES (1, 10, 1, 'SKU-A', 5000, 'USD', '2099-01-01T00:00:00+00:00', '2099-01-01T00:00:00+00:00')"
    )
    cur.execute(
        "INSERT INTO vkpi_content_posts "
        "(id, project_id, kol_id, views, likes, comments, published_at) "
        "VALUES (1, 10, 1, 1000, 50, 5, '2099-01-01T00:00:00+00:00')"
    )
    cur.execute("INSERT INTO vkpi_kol_claims (id, staff_id, status) VALUES (1, 7, 'active')")
    cur.execute(
        "INSERT INTO vkpi_channel_metrics "
        "(id, channel_id, followers_delta, views_delta_24h, captured_at) "
        "VALUES (1, 1, 120, 3000, '2099-01-01T00:00:00+00:00')"
    )
    cur.execute(
        "INSERT INTO vkpi_kol_pool "
        "(id, platform, followers, updated_at) "
        "VALUES (1, 'youtube', 1000, '2026-08-01T00:00:00+00:00')"
    )
    cur.execute(
        "INSERT INTO vkpi_kol_pool (id, platform, followers, updated_at) "
        "VALUES (2, 'instagram', 2500, '2026-08-02T00:00:00+00:00')"
    )
    cur.execute(
        "INSERT INTO vkpi_kol_pool "
        "(id, platform, followers, duplicate_of_id, updated_at) "
        "VALUES (3, 'tiktok', 9999, 1, '2026-08-03T00:00:00+00:00')"
    )
    cur.executemany(
        "INSERT INTO vkpi_kol_video_evidence (id, kol_pool_id, title, is_active) VALUES (?, ?, ?, ?)",
        [
            (1, 1, "First", 1),
            (2, 1, "Second", 1),
            (3, 3, "Merged duplicate", 1),
            (4, 2, "Inactive evidence", 0),
        ],
    )
    conn.commit()
    return conn


class IntentMatchTest(unittest.TestCase):
    def test_chinese_keywords_match_expected_intent(self):
        self.assertEqual(qp.match_intent("近30天送样价值是多少").key, "sample_value_30d")
        self.assertEqual(qp.match_intent("KOL内容ROI排名").key, "kol_content_roi")
        self.assertEqual(qp.match_intent("KOL Pool 现在有多少人？").key, "kol_pool_overview")
        self.assertEqual(qp.match_intent("KOL池有多少账号").key, "kol_pool_overview")
        self.assertEqual(qp.match_intent("已签收未发内容有哪些").key, "received_no_content")

    def test_bare_kol_does_not_misclassify_as_roi(self):
        self.assertIsNone(qp.match_intent("KOL"))

    def test_english_keywords_match(self):
        self.assertEqual(qp.match_intent("sample cost by sku").key, "sample_value_30d")
        self.assertEqual(qp.match_intent("country growth ranking").key, "country_growth")

    def test_noise_question_no_match(self):
        self.assertIsNone(qp.match_intent("天气怎么样 hello world"))
        self.assertIsNone(qp.match_intent(""))

    def test_resolve_intent_prefers_explicit_key(self):
        self.assertEqual(qp.resolve_intent("送样价值", "active_claims_by_staff").key, "active_claims_by_staff")

    def test_resolve_intent_rejects_unknown_key(self):
        # 未知 intent_key 不命中(返回 None),绝不回退到拼接。
        self.assertIsNone(qp.resolve_intent(None, "drop_tables; --"))


class WhitelistInjectionTest(unittest.TestCase):
    def test_user_text_never_enters_sql(self):
        evil = "送样价值'; DROP TABLE kols;-- and 1=1"
        plan = qp.build_plan(qp._INTENT_BY_KEY["sample_value_30d"], 30, None)
        # 用户原文不得出现在 SQL 中(只用于匹配)。
        self.assertNotIn("DROP", plan.sql.upper())
        self.assertNotIn(evil, plan.sql)
        # 整个 run 路径同样不把问题文本带入 SQL。
        conn = _make_conn()
        result = qp.run(conn, question=evil, range_days=30)
        self.assertEqual(result["intent"], "sample_value_30d")

    def test_assert_safe_sql_rejects_non_select(self):
        for bad in (
            "UPDATE kols SET country='X'",
            "DELETE FROM kols",
            "SELECT 1; DROP TABLE kols",
            "SELECT * FROM secret_table",
            "INSERT INTO kols VALUES (1)",
        ):
            with self.assertRaises(ValueError):
                qp.assert_safe_sql(bad)

    def test_assert_safe_sql_accepts_whitelisted_select(self):
        # 不应抛。
        qp.assert_safe_sql("SELECT id FROM kols WHERE id = ?")

    def test_malicious_source_param_is_dropped(self):
        # source 含注入字符 -> _norm_country 丢弃 -> country=None(无过滤),不入 SQL。
        plan = qp.build_plan(qp._INTENT_BY_KEY["country_growth"], 30, "US'; DROP--")
        self.assertNotIn("DROP", plan.sql.upper())
        self.assertNotIn("US'", str(plan.params))

    def test_range_days_clamped(self):
        self.assertEqual(qp._clamp_range_days(99999), qp.MAX_RANGE_DAYS)
        self.assertEqual(qp._clamp_range_days(-5), qp.MIN_RANGE_DAYS)
        self.assertEqual(qp._clamp_range_days("abc"), qp.DEFAULT_RANGE_DAYS)

    def test_all_intents_build_safe_sql(self):
        for intent in qp.INTENTS:
            plan = qp.build_plan(intent, 30, "US")
            qp.assert_safe_sql(plan.sql)  # 不抛即通过


class StructuredResultTest(unittest.TestCase):
    def _assert_shape(self, result: dict):
        for key in ("intent", "columns", "rows", "sql_explain", "source_status"):
            self.assertIn(key, result)
        self.assertIsInstance(result["columns"], list)
        self.assertIsInstance(result["rows"], list)

    def test_three_intents_return_structured_rows(self):
        conn = _make_conn()
        r1 = qp.run(conn, question="近30天送样价值", range_days=3650)
        self._assert_shape(r1)
        self.assertEqual(r1["intent"], "sample_value_30d")
        self.assertTrue(len(r1["rows"]) >= 1)
        self.assertEqual(set(r1["columns"]), set(r1["rows"][0].keys()))

        r2 = qp.run(conn, question="KOL内容ROI排名", range_days=3650)
        self._assert_shape(r2)
        self.assertEqual(r2["intent"], "kol_content_roi")
        self.assertTrue(len(r2["rows"]) >= 1)

        r3 = qp.run(conn, question="按员工统计活跃认领")
        self._assert_shape(r3)
        self.assertEqual(r3["intent"], "active_claims_by_staff")
        self.assertTrue(len(r3["rows"]) >= 1)
        self.assertEqual(r3["rows"][0]["active_claims"], 1)

    def test_kol_pool_overview_returns_current_totals(self):
        conn = _make_conn()
        result = qp.run(conn, question="KOL 池现在共有多少账号？")
        self._assert_shape(result)
        self.assertEqual(result["intent"], "kol_pool_overview")
        metadata = next(item for item in qp.list_intents() if item["intent"] == "kol_pool_overview")
        self.assertEqual(metadata["columns"], result["columns"])
        self.assertEqual(
            result["rows"],
            [
                {
                    "total_kols": 2,
                    "platforms": 2,
                    "total_followers": 3500,
                    "raw_records": 3,
                    "duplicate_records": 1,
                    "video_evidence_kols": 1,
                    "video_coverage_pct": 50.0,
                    "data_updated_at": "2026-08-02T00:00:00+00:00",
                }
            ],
        )

    def test_country_growth_fails_closed_without_verified_country_dimension(self):
        conn = _make_conn()
        result = qp.run(
            conn,
            question="美国市场涨粉",
            range_days=30,
            source="US",
        )

        self.assertEqual(result["intent"], "country_growth")
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["source_status"], "unavailable")
        self.assertEqual(result["source_reason"], "verified_country_dimension_missing")
        plan = qp.build_plan(qp._INTENT_BY_KEY["country_growth"], 30, "US")
        self.assertNotIn("JOIN kols", plan.sql)
        self.assertNotIn("m.channel_id = k.id", plan.sql)

    def test_legacy_roi_key_is_truthfully_named_content_performance(self):
        intent = qp._INTENT_BY_KEY["kol_content_roi"]

        self.assertEqual(intent.title, "KOL内容表现排名")
        self.assertIn("不代表 ROI", intent.description)

    def test_received_no_content_finds_pending(self):
        conn = _make_conn()
        # 样品 received 但 project 10 已有 content_post -> 不应出现。
        r = qp.run(conn, question="已签收未发内容")
        self.assertEqual(r["intent"], "received_no_content")
        self.assertEqual(len(r["rows"]), 0)
        # 加一条已签收但无内容的样品(project 20)-> 应出现。
        conn.execute(
            "INSERT INTO vkpi_sample_assets "
            "(id, project_id, kol_id, product_sku, sample_cost_cents, currency, received_at, created_at) "
            "VALUES (2, 20, 2, 'SKU-B', 1000, 'USD', '2099-02-02T00:00:00+00:00', '2099-02-02T00:00:00+00:00')"
        )
        conn.commit()
        r2 = qp.run(conn, question="已签收未发内容")
        self.assertEqual(len(r2["rows"]), 1)
        self.assertEqual(r2["rows"][0]["project_id"], 20)

    def test_unmatched_question_returns_available_intents(self):
        conn = _make_conn()
        r = qp.run(conn, question="完全无关的问题 xyz")
        self.assertIsNone(r["intent"])
        self.assertIn("available_intents", r)
        self.assertTrue(len(r["available_intents"]) >= 5)

    def test_list_intents_shape(self):
        intents = qp.list_intents()
        self.assertTrue(len(intents) >= 5)
        for item in intents:
            for key in ("intent", "title", "description", "examples", "columns"):
                self.assertIn(key, item)


if __name__ == "__main__":
    unittest.main()
