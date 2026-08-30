"""分支覆盖冲刺·comments/fine_emotion.py — 六类情绪词表/诚实语言闸/渴望密度 KPI。

覆盖:classify_text 优先级与 unclassified 闸、classify_comments 幂等与写路径、
desire_density 置信分层与空态、desire_vs_conversion 统计闸(insufficient_data)。
DB 全部经 monkeypatch get_conn 的 fake conn,不触真库。
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

import app.db.connection as db_connection  # noqa: E402
from app.domains.comments import fine_emotion as fe  # noqa: E402


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
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()):  # noqa: ANN001
        self.executed.append((sql, params))
        for fragment, resp in self.responders:
            if fragment in sql:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return FakeCursor()

    def commit(self):
        self.commits += 1


class PatchConnMixin:
    def _use_conn(self, conn: FakeConn):
        self._orig_get_conn = db_connection.get_conn
        db_connection.get_conn = lambda: conn  # type: ignore[assignment]
        self.addCleanup(self._restore)

    def _restore(self):
        db_connection.get_conn = self._orig_get_conn  # type: ignore[assignment]


class LangSupportTests(unittest.TestCase):
    def test_hint_decides_first(self):
        self.assertTrue(fe._lang_supported("whatever", "en"))
        self.assertTrue(fe._lang_supported("whatever", "zh-CN"))
        self.assertFalse(fe._lang_supported("whatever", "ar"))

    def test_cjk_text_supported_without_hint(self):
        self.assertTrue(fe._lang_supported("太贵了", None))

    def test_no_letters_unsupported(self):
        self.assertFalse(fe._lang_supported("12345 !!!", None))

    def test_latin_ratio_gate(self):
        self.assertTrue(fe._lang_supported("plain english words", None))
        self.assertFalse(fe._lang_supported("это русский текст", None))

    def test_is_wordy_ascii_vs_emoji(self):
        self.assertTrue(fe._is_wordy("take my money"))
        self.assertFalse(fe._is_wordy("🔥"))
        self.assertFalse(fe._is_wordy("太贵"))
        self.assertFalse(fe._is_wordy(""))


class ClassifyTextTests(unittest.TestCase):
    def test_empty_text_unclassified(self):
        out = fe.classify_text("")
        self.assertEqual(out["primary"], "unclassified")
        self.assertEqual(out["labels"], [])
        self.assertFalse(out["lang_supported"])

    def test_supported_language_without_hits_is_none(self):
        out = fe.classify_text("interesting video about lenses")
        self.assertEqual(out["primary"], "none")
        self.assertTrue(out["lang_supported"])

    def test_unsupported_language_without_hits_is_unclassified(self):
        out = fe.classify_text("هذا فيديو جميل", "ar")
        self.assertEqual(out["primary"], "unclassified")
        self.assertFalse(out["lang_supported"])

    def test_embedded_english_desire_wins_despite_unsupported_lang(self):
        out = fe.classify_text("رائع i need this", "ar")
        self.assertEqual(out["primary"], "desire")
        self.assertFalse(out["lang_supported"])

    def test_priority_desire_beats_excitement(self):
        out = fe.classify_text("so cool, i need this!")
        self.assertEqual(out["labels"], ["desire", "excitement"])
        self.assertEqual(out["primary"], "desire")

    def test_word_boundary_prevents_partial_hits(self):
        # "wow" 词条不吃 "wowzers"
        out = fe.classify_text("wowzers that render")
        self.assertNotIn("awe", out["labels"])
        out2 = fe.classify_text("wow that render")
        self.assertIn("awe", out2["labels"])

    def test_emoji_substring_matches(self):
        out = fe.classify_text("🔥🔥🔥")
        self.assertIn("excitement", out["labels"])
        # 纯 emoji 无字母 → 语言不支持,但命中即真信号
        self.assertEqual(out["primary"], "excitement")
        self.assertFalse(out["lang_supported"])

    def test_chinese_price_complaint(self):
        out = fe.classify_text("这镜头也太贵了吧")
        self.assertEqual(out["primary"], "price_complaint")

    def test_matched_capped_at_five(self):
        text = "i need this need one i want this want one take my money must have gonna buy"
        out = fe.classify_text(text)
        self.assertEqual(len(out["matched"]["desire"]), 5)


class TagEqualTests(unittest.TestCase):
    def test_stored_tag_requires_dict_shapes(self):
        self.assertIsNone(fe._stored_tag(None))
        self.assertIsNone(fe._stored_tag({"fine_emotion_v1": "str"}))
        self.assertEqual(fe._stored_tag({"fine_emotion_v1": {"a": 1}}), {"a": 1})

    def test_tag_equal_ignores_timestamp(self):
        new = fe.classify_text("i need this")
        old = dict(new)
        old["classified_at"] = "2020-01-01T00:00:00Z"
        self.assertTrue(fe._tag_equal(old, new))
        old["primary"] = "none"
        self.assertFalse(fe._tag_equal(old, new))
        self.assertFalse(fe._tag_equal(None, new))


class ClassifyCommentsTests(PatchConnMixin, unittest.TestCase):
    def _rows(self):
        current = fe.classify_text("i need this")
        return [
            {"id": 1, "comment_text": "i need this", "language_detected": "en",
             "raw_data_json": json.dumps({fe.FINE_EMOTION_KEY: {**current, "classified_at": "x"}})},
            {"id": 2, "comment_text": "so cool", "language_detected": "en", "raw_data_json": "{}"},
            {"id": 3, "comment_text": "قصيدة", "language_detected": "ar", "raw_data_json": "{}"},
            {"id": 4, "comment_text": "plain neutral words", "language_detected": "en", "raw_data_json": "not json"},
            {"id": 5, "comment_text": "太贵了", "language_detected": None, "raw_data_json": "[]"},
        ]

    def test_dry_run_distribution_and_idempotent_skip(self):
        conn = FakeConn([("FROM vkpi_comments", FakeCursor(rows=self._rows()))])
        self._use_conn(conn)
        out = fe.classify_comments(dry_run=True)
        self.assertEqual(out["scanned"], 5)
        self.assertEqual(out["by_primary"]["desire"], 1)
        self.assertEqual(out["by_primary"]["excitement"], 1)
        self.assertEqual(out["by_primary"]["unclassified"], 1)
        self.assertEqual(out["by_primary"]["none"], 1)
        self.assertEqual(out["by_primary"]["price_complaint"], 1)
        self.assertEqual(out["labeled"], 3)
        self.assertEqual(out["none"], 1)
        self.assertEqual(out["unclassified"], 1)
        self.assertEqual(out["skipped_unchanged"], 1)  # id=1 标签未变
        self.assertEqual(out["raw_not_dict"], 2)       # id=4 烂 JSON + id=5 list
        self.assertEqual(out["written"], 2)            # would-write: id=2, id=3
        self.assertIn("dry_run=true", out["note"])
        self.assertEqual([s for s, _ in conn.executed if "UPDATE" in s], [])
        self.assertEqual(conn.commits, 1)

    def test_write_path_updates_only_changed_rows(self):
        conn = FakeConn([("FROM vkpi_comments", FakeCursor(rows=self._rows()))])
        self._use_conn(conn)
        out = fe.classify_comments(dry_run=False)
        self.assertEqual(out["written"], 2)
        updates = [(s, p) for s, p in conn.executed if "UPDATE vkpi_comments" in s]
        self.assertEqual(len(updates), 2)
        payload, cid = updates[0][1]
        stored = json.loads(payload)
        self.assertEqual(cid, 2)
        self.assertEqual(stored[fe.FINE_EMOTION_KEY]["primary"], "excitement")
        self.assertIn("classified_at", stored[fe.FINE_EMOTION_KEY])

    def test_force_rewrites_unchanged(self):
        conn = FakeConn([("FROM vkpi_comments", FakeCursor(rows=self._rows()))])
        self._use_conn(conn)
        out = fe.classify_comments(dry_run=False, force=True)
        self.assertEqual(out["written"], 3)  # 幂等跳过的 id=1 也被重写
        self.assertEqual(out["skipped_unchanged"], 0)

    def test_account_filter_and_limit_shape_sql(self):
        conn = FakeConn([("FROM vkpi_comments", FakeCursor(rows=[]))])
        self._use_conn(conn)
        fe.classify_comments(dry_run=True, account_id=9, limit=50)
        sql, params = conn.executed[0]
        self.assertIn("WHERE account_id = ?", sql)
        self.assertIn("LIMIT ?", sql)
        self.assertEqual(params, (9, 50))


class DensityAggTests(unittest.TestCase):
    def _comment(self, cid, text, tag=None, lang="en"):
        raw = {fe.FINE_EMOTION_KEY: tag} if tag else {}
        return {
            "id": cid, "comment_text": text, "language_detected": lang,
            "raw_data_json": json.dumps(raw),
        }

    def test_stored_tags_preferred_stale_recomputed(self):
        fresh = fe.classify_text("i need this")
        stale = dict(fresh, lexicon_version="lex_v0")
        comments = [
            self._comment(1, "i need this", tag=fresh),
            self._comment(2, "i need this", tag=stale),
            self._comment(3, "nice video"),
        ]
        out = fe._density_from_comments(comments)
        self.assertEqual(out["sample_size"], 3)
        self.assertEqual(out["computed_on_the_fly"], 2)  # 陈旧词表 + 无标签
        self.assertEqual(out["desire_comments"], 2)
        self.assertEqual(out["desire_density_per_1000"], 666.7)
        self.assertEqual(len(out["desire_samples"]), 2)

    def test_unclassified_denominator_split(self):
        comments = [
            self._comment(1, "i need this"),
            self._comment(2, "قصيدة جميلة", lang="ar"),
        ]
        out = fe._density_from_comments(comments)
        self.assertEqual(out["unclassified"], 1)
        self.assertEqual(out["lang_supported"], 1)
        self.assertEqual(out["desire_density_per_1000"], 500.0)
        self.assertEqual(out["desire_density_per_1000_lang_supported"], 1000.0)

    def test_empty_comments_zero_density(self):
        out = fe._density_from_comments([])
        self.assertEqual(out["sample_size"], 0)
        self.assertEqual(out["desire_density_per_1000"], 0.0)


class DesireDensityTests(PatchConnMixin, unittest.TestCase):
    def _scope_rows(self, n, text="i need this"):
        return [
            {"id": i, "comment_text": text, "language_detected": "en",
             "raw_data_json": "{}", "kol_pool_id": 3}
            for i in range(n)
        ]

    def test_requires_scope_argument(self):
        with self.assertRaises(ValueError):
            fe.desire_density()

    def test_unknown_kol_pool_raises(self):
        conn = FakeConn([("FROM vkpi_kol_pool", FakeCursor(row=None))])
        self._use_conn(conn)
        with self.assertRaises(LookupError):
            fe.desire_density(kol_pool_id=3)
        self.assertEqual(conn.commits, 1)

    def test_empty_scope_reports_reason(self):
        conn = FakeConn([
            ("FROM vkpi_kol_pool", FakeCursor(row={"id": 3})),
            ("JOIN vkpi_kol_video_evidence", FakeCursor(rows=[])),
        ])
        self._use_conn(conn)
        out = fe.desire_density(kol_pool_id=3)
        self.assertEqual(out["status"], "empty")
        self.assertIn("reason", out)
        self.assertEqual(out["confidence"], "low")

    def test_small_sample_low_confidence_with_note(self):
        conn = FakeConn([
            ("FROM vkpi_kol_pool", FakeCursor(row={"id": 3})),
            ("JOIN vkpi_kol_video_evidence", FakeCursor(rows=self._scope_rows(5))),
        ])
        self._use_conn(conn)
        out = fe.desire_density(kol_pool_id=3)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["confidence"], "low")
        self.assertIn("confidence_note", out)
        self.assertEqual(out["desire_density_per_1000"], 1000.0)

    def test_large_sample_caps_at_medium_never_high(self):
        conn = FakeConn([
            ("FROM vkpi_kol_pool", FakeCursor(row={"id": 3})),
            ("JOIN vkpi_kol_video_evidence", FakeCursor(rows=self._scope_rows(25))),
        ])
        self._use_conn(conn)
        out = fe.desire_density(kol_pool_id=3)
        self.assertEqual(out["confidence"], "medium")
        self.assertNotIn("confidence_note", out)
        cats = {c["key"]: c["count"] for c in out["categories"]}
        self.assertEqual(cats["desire"], 25)
        self.assertEqual(cats["awe"], 0)

    def test_sku_scope_resolution_with_missing_tables(self):
        conn = FakeConn([
            ("FROM vkpi_projects", RuntimeError("no table")),
            ("FROM vkpi_links", RuntimeError("no table")),
            ("JOIN vkpi_kol_video_evidence", FakeCursor(rows=[])),
        ])
        self._use_conn(conn)
        out = fe.desire_density(sku="AF-85")
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["scope"]["resolved_kol_pool_ids"], [])


class DesireVsConversionTests(PatchConnMixin, unittest.TestCase):
    def test_local_empty_data_is_honest_pending(self):
        conn = FakeConn([
            ("SELECT DISTINCT e.kol_pool_id", FakeCursor(rows=[])),
            ("FROM vkpi_links", RuntimeError("no table")),
            ("FROM vkpi_shopify_orders", RuntimeError("no table")),
            ("FROM vkpi_goaffpro_sales", RuntimeError("no table")),
        ])
        self._use_conn(conn)
        out = fe.desire_vs_conversion()
        self.assertEqual(out["status"], "pending")
        self.assertEqual(out["verdict"], "insufficient_data")
        self.assertEqual(out["pairs"], [])
        self.assertEqual(out["pairs_judgeable"], 0)
        self.assertEqual(
            out["conversion_overall"]["sources"]["vkpi_links"]["status"],
            "table_missing_or_error",
        )

    def test_unknown_kol_raises(self):
        conn = FakeConn([("FROM vkpi_kol_pool", FakeCursor(row=None))])
        self._use_conn(conn)
        with self.assertRaises(LookupError):
            fe.desire_vs_conversion(kol_pool_id=3)

    def test_enough_judgeable_pairs_flip_to_ok(self):
        kids = [{"kid": i} for i in range(1, 6)]
        scope_rows = [
            {"id": i, "comment_text": "i need this", "language_detected": "en",
             "raw_data_json": "{}", "kol_pool_id": 1}
            for i in range(20)
        ]
        conn = FakeConn([
            ("SELECT DISTINCT e.kol_pool_id", FakeCursor(rows=kids)),
            ("SELECT c.id", FakeCursor(rows=scope_rows)),
            ("FROM vkpi_links", FakeCursor(row={"links": 1, "clicks": 3, "valid_clicks": 2})),
            ("FROM vkpi_shopify_orders", FakeCursor(row={"orders": 0, "total_cents": 0})),
            ("FROM vkpi_goaffpro_sales", FakeCursor(row={"sales": 0, "total_cents": 0})),
        ])
        self._use_conn(conn)
        out = fe.desire_vs_conversion()
        self.assertEqual(out["pairs_total"], 5)
        self.assertEqual(out["pairs_judgeable"], 5)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["verdict"], "correlation_pending")
        self.assertEqual(out["pairs"][0]["valid_clicks"], 2)
        self.assertEqual(out["pairs"][0]["desire_density_per_1000"], 1000.0)


if __name__ == "__main__":
    unittest.main()
