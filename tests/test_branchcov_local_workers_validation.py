"""分支覆盖冲刺·local_workers/validation.py — 深校验分支/错误路径/落库桥降级语义。

真行为断言:每条断言检查具体返回值/落库副作用,不做「不抛即过」空转。
只新建测试,不动生产代码。DB 一律 fake conn(dict 行天然支持 keys()/[key])。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.local_workers import validation as v  # noqa: E402


class FakeCursor:
    def __init__(self, row: Any = None, rowcount: int = 0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class FakeDb:
    """按 SQL 片段路由回包的 fake 连接;记录全部 execute 供副作用断言。"""

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
                if isinstance(resp, FakeCursor):
                    return resp
                return FakeCursor(row=resp)
        return FakeCursor()

    def commit(self):
        self.commits += 1


class SmallHelpersTests(unittest.TestCase):
    def test_loads_none_empty_and_bytes_return_default(self):
        self.assertEqual(v._loads(None, {"d": 1}), {"d": 1})
        self.assertEqual(v._loads("", []), [])
        self.assertEqual(v._loads(b"", 7), 7)

    def test_loads_passthrough_dict_and_list(self):
        payload = {"a": 1}
        self.assertIs(v._loads(payload, None), payload)
        items = [1, 2]
        self.assertIs(v._loads(items, None), items)

    def test_loads_parses_json_and_falls_back_on_garbage_and_null(self):
        self.assertEqual(v._loads('{"k": 3}', {}), {"k": 3})
        self.assertEqual(v._loads("not-json", {"fb": True}), {"fb": True})
        self.assertEqual(v._loads("null", {"fb": True}), {"fb": True})

    def test_int_or_none_rejects_bool_and_fractional_float(self):
        self.assertIsNone(v._int_or_none(True))
        self.assertIsNone(v._int_or_none(False))
        self.assertIsNone(v._int_or_none(3.5))
        self.assertEqual(v._int_or_none(3.0), 3)
        self.assertEqual(v._int_or_none(42), 42)

    def test_int_or_none_string_paths(self):
        self.assertEqual(v._int_or_none(" 12 "), 12)
        self.assertEqual(v._int_or_none("-7"), -7)
        self.assertIsNone(v._int_or_none("12.5"))
        self.assertIsNone(v._int_or_none("abc"))
        self.assertIsNone(v._int_or_none(None))

    def test_boolish_accepts_bool_int_and_string_forms(self):
        self.assertTrue(v._boolish(True))
        self.assertFalse(v._boolish(False))
        self.assertTrue(v._boolish(1))
        self.assertFalse(v._boolish(0))
        self.assertIsNone(v._boolish(2))
        for s in ("true", "T", " 1 ", "YES"):
            self.assertTrue(v._boolish(s), s)
        for s in ("false", "f", "0", "no"):
            self.assertFalse(v._boolish(s), s)
        self.assertIsNone(v._boolish("maybe"))
        self.assertIsNone(v._boolish(3.3))

    def test_norm_url_defaults_scheme_lowers_and_strips_trailing_slash(self):
        # 无 scheme 时整串按 path 解析:host 不会被小写,只有 scheme/netloc 小写
        self.assertEqual(v._norm_url("YouTube.com/Watch/"), "https://YouTube.com/Watch")
        self.assertEqual(
            v._norm_url("HTTP://Host.com/a/?v=1"), "http://host.com/a?v=1"
        )
        self.assertEqual(v._norm_url(""), "")
        self.assertEqual(v._norm_url(None), "")

    def test_pick_url_respects_key_priority_and_empty(self):
        self.assertEqual(
            v._pick_url({"video_url": "b", "url": "a"}), "a"
        )
        self.assertEqual(v._pick_url({"source_url": "s"}), "s")
        self.assertEqual(v._pick_url({}), "")


class VideoPrecheckProblemsTests(unittest.TestCase):
    def _good(self) -> dict:
        return {"http_status": 200, "playable": True, "reason": "ok"}

    def test_valid_result_has_no_problems(self):
        self.assertEqual(v._problems_video_precheck(self._good()), [])

    def test_missing_or_out_of_range_http_status(self):
        probs = v._problems_video_precheck({"playable": 1, "reason": "r"})
        self.assertTrue(any("http_status" in p for p in probs))
        probs = v._problems_video_precheck({"http_status": 1000, "playable": 1, "reason": "r"})
        self.assertTrue(any("http_status" in p for p in probs))

    def test_playable_and_reason_missing(self):
        probs = v._problems_video_precheck({"http_status": 200})
        self.assertTrue(any("playable" in p for p in probs))
        self.assertTrue(any("reason" in p for p in probs))

    def test_playable_true_conflicts_with_terminal_status(self):
        probs = v._problems_video_precheck({"http_status": 404, "playable": True, "reason": "gone"})
        self.assertEqual(probs, ["precheck.playable=true conflicts with terminal http_status=404"])

    def test_playable_false_with_terminal_status_is_fine(self):
        self.assertEqual(
            v._problems_video_precheck({"http_status": 404, "playable": False, "reason": "gone"}),
            [],
        )


class MetadataExtractProblemsTests(unittest.TestCase):
    def _payload(self) -> dict:
        return {"url": "https://youtube.com/watch?v=1"}

    def _good(self) -> dict:
        return {
            "title": "t",
            "duration_seconds": 10,
            "view_count": 5,
            "url": "https://youtube.com/watch?v=1",
        }

    def test_valid_metadata_has_no_problems(self):
        self.assertEqual(v._problems_metadata_extract(self._payload(), self._good()), [])

    def test_duration_fallback_key_accepted(self):
        result = self._good()
        del result["duration_seconds"]
        result["duration"] = 33
        self.assertEqual(v._problems_metadata_extract(self._payload(), result), [])

    def test_title_duration_viewcount_problems(self):
        probs = v._problems_metadata_extract(
            self._payload(), {"duration_seconds": -1, "view_count": -2, "url": "https://youtube.com/watch?v=1"}
        )
        self.assertTrue(any("title" in p for p in probs))
        self.assertTrue(any("duration" in p for p in probs))
        self.assertTrue(any("view_count" in p for p in probs))

    def test_payload_without_url_refuses_to_trust(self):
        probs = v._problems_metadata_extract({}, self._good())
        self.assertEqual(
            probs,
            ["job payload has no url field - result url cannot be verified, refusing to trust"],
        )

    def test_result_missing_url_and_mismatch(self):
        result = self._good()
        del result["url"]
        probs = v._problems_metadata_extract(self._payload(), result)
        self.assertEqual(probs, ["metadata.url missing - must echo the leased job url"])
        result["url"] = "https://youtube.com/watch?v=OTHER"
        probs = v._problems_metadata_extract(self._payload(), result)
        self.assertEqual(probs, ["metadata url does not match leased job payload url"])

    def test_url_normalization_tolerates_case_and_trailing_slash(self):
        payload = {"url": "https://YouTube.com/watch/"}
        result = self._good()
        result["url"] = "https://youtube.com/watch"
        self.assertEqual(v._problems_metadata_extract(payload, result), [])


class DownloadFramesProblemsTests(unittest.TestCase):
    def _meta(self, **over) -> dict:
        base = {"name": "f1.jpg", "sha256": "a" * 64, "bytes": 100}
        base.update(over)
        return base

    def test_valid_frames_submission(self):
        self.assertEqual(
            v._problems_download_frames({"frame_count": 2}, [self._meta(), self._meta(name="f2.jpg")]),
            [],
        )

    def test_frame_count_derived_from_frames_list(self):
        self.assertEqual(
            v._problems_download_frames({"frames": ["a"]}, [self._meta()]),
            [],
        )

    def test_missing_count_and_empty_files_meta(self):
        probs = v._problems_download_frames({}, [])
        self.assertIn("frames.frame_count missing or below 1", probs)
        self.assertIn("files_meta is empty - frames submission must declare files", probs)

    def test_files_meta_item_shape_problems(self):
        probs = v._problems_download_frames(
            {"frame_count": 3},
            ["not-a-dict", self._meta(name="", sha256="xyz", bytes=0), self._meta(bytes=True)],
        )
        self.assertIn("files_meta[0] is not an object", probs)
        self.assertIn("files_meta[1].name missing", probs)
        self.assertIn("files_meta[1].sha256 is not 64-hex", probs)
        self.assertIn("files_meta[1].bytes must be an int above 0", probs)
        self.assertIn("files_meta[2].bytes must be an int above 0", probs)

    def test_count_mismatch_detected(self):
        probs = v._problems_download_frames({"frame_count": 3}, [self._meta()])
        self.assertIn("frame_count=3 does not match files_meta count=1", probs)

    def test_non_list_files_meta_treated_as_empty(self):
        probs = v._problems_download_frames({"frame_count": 1}, "nope")  # type: ignore[arg-type]
        self.assertIn("files_meta is empty - frames submission must declare files", probs)


class CommentCleanProblemsTests(unittest.TestCase):
    def test_comments_must_be_array(self):
        self.assertEqual(
            v._problems_comment_clean({"comments": "x"}),
            ["comments must be a JSON array under result.comments"],
        )

    def test_valid_comments_pass(self):
        good = {"comments": [{"text": "hi", "lang": "en"}, {"content": "ok", "language": "ja"}]}
        self.assertEqual(v._problems_comment_clean(good), [])

    def test_item_shape_problems(self):
        probs = v._problems_comment_clean(
            {"comments": ["str", {"lang": "en"}, {"text": "hey"}]}
        )
        self.assertIn("comments[0] is not an object", probs)
        self.assertIn("comments[1] has no non-empty text field", probs)
        self.assertIn("comments[2] has no language field (lang / language)", probs)

    def test_problem_truncation_after_five(self):
        probs = v._problems_comment_clean({"comments": [{} for _ in range(10)]})
        self.assertEqual(probs[-1], "more problems truncated")
        # 5 上限 + 截断哨兵,不再逐条罗列 10 条 x2 问题
        self.assertLessEqual(len(probs), 7)


class DeepProblemsDispatchTests(unittest.TestCase):
    def test_unsafe_task_type_fails_closed(self):
        self.assertEqual(
            v.deep_problems("drop_tables", {}, {}, []),
            ["task_type not in safe whitelist: drop_tables"],
        )
        self.assertEqual(
            v.deep_problems("", {}, {}, []),
            ["task_type not in safe whitelist: (empty)"],
        )

    def test_result_must_be_object(self):
        self.assertEqual(
            v.deep_problems("video_precheck", {}, "not-a-dict", []),
            ["result must be a JSON object"],
        )

    def test_dispatch_reaches_each_task_branch(self):
        self.assertTrue(v.deep_problems("video_precheck", {}, {}, []))
        self.assertTrue(v.deep_problems("metadata_extract", None, {}, []))  # type: ignore[arg-type]
        self.assertTrue(v.deep_problems("download_frames", {}, {}, None))
        self.assertEqual(
            v.deep_problems("comment_clean", {}, {"comments": []}, []), []
        )


class ValidateSubmissionDualFormTests(unittest.TestCase):
    def test_contract_form_pass_returns_dict(self):
        lease_view = {
            "task_type": "comment_clean",
            "payload": '{"whatever": 1}',
        }
        out = v.validate_submission(lease_view, {"comments": []}, [])
        self.assertEqual(
            out,
            {
                "ok": True,
                "task_type": "comment_clean",
                "problems": [],
                "notes": "deep checks passed",
            },
        )

    def test_contract_form_failure_notes_join_problems(self):
        out = v.validate_submission({"task_type": "video_precheck", "payload": {}}, {}, [])
        self.assertFalse(out["ok"])
        self.assertIn("precheck.http_status", out["notes"])
        self.assertGreaterEqual(len(out["problems"]), 3)

    def test_hook_form_without_marker_skips_honestly(self):
        ok, notes = v.validate_submission({"no_marker": True}, {"anything": 1})
        self.assertTrue(ok)
        self.assertEqual(len(notes), 1)
        self.assertIn("deep_validation_skipped", notes[0])
        self.assertIn("lease_id=0", notes[0])

    def test_hook_form_non_dict_first_arg_skips(self):
        ok, notes = v.validate_submission("garbage", {})  # type: ignore[arg-type]
        self.assertTrue(ok)
        self.assertIn("deep_validation_skipped", notes[0])

    def test_hook_form_resolves_task_type_via_marker(self):
        fake = FakeDb([("SELECT task_type", {"task_type": "video_precheck"})])
        original = v.get_conn
        v.get_conn = lambda: fake  # type: ignore[assignment]
        try:
            ok, problems = v.validate_submission(
                {"local_lease_id": 7}, {"http_status": 200, "playable": "yes", "reason": "fine"}
            )
        finally:
            v.get_conn = original
        self.assertTrue(ok)
        self.assertEqual(problems, [])

    def test_hook_form_marker_lookup_error_degrades_to_skip(self):
        fake = FakeDb([("SELECT task_type", RuntimeError("db down"))])
        original = v.get_conn
        v.get_conn = lambda: fake  # type: ignore[assignment]
        try:
            ok, notes = v.validate_submission({"local_lease_id": 9}, {"x": 1})
        finally:
            v.get_conn = original
        self.assertTrue(ok)
        self.assertIn("lease_id=9", notes[0])

    def test_hook_form_marker_row_missing_skips(self):
        fake = FakeDb([("SELECT task_type", None)])
        original = v.get_conn
        v.get_conn = lambda: fake  # type: ignore[assignment]
        try:
            ok, notes = v.validate_submission({"local_lease_id": 4}, {"x": 1})
        finally:
            v.get_conn = original
        self.assertTrue(ok)
        self.assertIn("deep_validation_skipped", notes[0])


class IngestValidatedTests(unittest.TestCase):
    def test_metadata_without_kol_pool_id_stays_pending(self):
        out = v._ingest_validated(FakeDb(), "metadata_extract", {}, {"url": "https://a.b/c"}, dry_run=False)
        self.assertEqual(out["status"], "pending_ingest:no_kol_pool_id_in_job_payload")
        self.assertFalse(out["final"])

    def _with_writer(self, writer):
        import app.domains.kol.video_evidence as ve

        original = ve.ensure_video_evidence_from_url
        ve.ensure_video_evidence_from_url = writer
        return ve, original

    def test_metadata_ingest_success_marks_final(self):
        ve, original = self._with_writer(
            lambda *a, **k: {"ok": True, "status": "created", "evidence_id": 88}
        )
        try:
            out = v._ingest_validated(
                FakeDb(), "metadata_extract", {"kol_pool_id": 5}, {"url": "https://a.b/c"}, dry_run=False
            )
        finally:
            ve.ensure_video_evidence_from_url = original
        self.assertEqual(out, {"status": "ingested:created", "final": True, "evidence_id": 88})

    def test_metadata_ingest_dry_run_never_final(self):
        ve, original = self._with_writer(
            lambda *a, **k: {"ok": True, "status": "would_create", "evidence_id": None}
        )
        try:
            out = v._ingest_validated(
                FakeDb(), "metadata_extract", {"kol_id": "6"}, {"url": "https://a.b/c"}, dry_run=True
            )
        finally:
            ve.ensure_video_evidence_from_url = original
        self.assertEqual(out["status"], "ingest_dry_run:would_create")
        self.assertFalse(out["final"])

    def test_metadata_ingest_refused_by_conflict_guard(self):
        ve, original = self._with_writer(
            lambda *a, **k: {"ok": False, "status": "conflict_existing_other_kol"}
        )
        try:
            out = v._ingest_validated(
                FakeDb(), "metadata_extract", {"pool_id": 3}, {"url": "https://a.b/c"}, dry_run=False
            )
        finally:
            ve.ensure_video_evidence_from_url = original
        self.assertEqual(out["status"], "ingest_refused:conflict_existing_other_kol")
        self.assertFalse(out["final"])

    def test_metadata_ingest_exception_degrades_to_pending(self):
        def boom(*a, **k):
            raise ValueError("writer exploded")

        ve, original = self._with_writer(boom)
        try:
            out = v._ingest_validated(
                FakeDb(), "metadata_extract", {"kol_pool_id": 1}, {"url": "https://a.b/c"}, dry_run=False
            )
        finally:
            ve.ensure_video_evidence_from_url = original
        self.assertEqual(out["status"], "pending_ingest:ingest_error:ValueError")
        self.assertFalse(out["final"])
        self.assertEqual(out["error"], "writer exploded")

    def test_non_metadata_types_are_honest_pending(self):
        for task_type, expect in (
            ("video_precheck", "pending_ingest:no_reusable_precheck_writer"),
            ("download_frames", "pending_ingest:frames_meta_only"),
            ("comment_clean", "pending_ingest:no_direct_comment_table_write"),
        ):
            out = v._ingest_validated(FakeDb(), task_type, {}, {}, dry_run=False)
            self.assertEqual(out["status"], expect)
            self.assertFalse(out["final"])


class MarkJobDoneTests(unittest.TestCase):
    def test_missing_job_short_circuits(self):
        self.assertEqual(v._mark_job_done_after_ingest(FakeDb(), 0, 1), "job_missing")

    def test_marked_done_releases_marker(self):
        db = FakeDb([("UPDATE apify_jobs", FakeCursor(rowcount=1))])
        released: list[tuple] = []
        original = v._release_job_marker
        v._release_job_marker = lambda conn, job_id, lease_id: released.append((job_id, lease_id))  # type: ignore[assignment]
        try:
            out = v._mark_job_done_after_ingest(db, 12, 34)
        finally:
            v._release_job_marker = original
        self.assertEqual(out, "job_marked_done")
        self.assertEqual(released, [(12, 34)])

    def test_status_moved_or_marker_mismatch(self):
        db = FakeDb([("UPDATE apify_jobs", FakeCursor(rowcount=0))])
        self.assertEqual(
            v._mark_job_done_after_ingest(db, 12, 34),
            "job_not_marked:status_moved_or_marker_mismatch",
        )


class ApplyValidationBridgeTests(unittest.TestCase):
    def setUp(self):
        self._orig_table_exists = v.table_exists
        self._orig_release = v._release_job_marker
        v.table_exists = lambda name: True  # type: ignore[assignment]
        self.released: list[tuple] = []
        v._release_job_marker = lambda conn, job_id, lease_id: self.released.append((job_id, lease_id))  # type: ignore[assignment]

    def tearDown(self):
        v.table_exists = self._orig_table_exists  # type: ignore[assignment]
        v._release_job_marker = self._orig_release  # type: ignore[assignment]

    def _lease_row(self, **over) -> dict:
        base = {
            "id": 1,
            "job_id": 10,
            "device_id": "dev",
            "task_type": "video_precheck",
            "status": "submitted",
            "result_json": '{"result": {"http_status": 200, "playable": 1, "reason": "ok"}}',
            "result_validated": 0,
        }
        base.update(over)
        return base

    def test_missing_table_fails_closed(self):
        v.table_exists = lambda name: False  # type: ignore[assignment]
        with self.assertRaises(RuntimeError) as ctx:
            v.apply_validation(1, conn=FakeDb())
        self.assertIn("migration 213", str(ctx.exception))

    def test_unknown_lease_raises_lookup(self):
        db = FakeDb([("FROM vkpi_local_task_leases", None)])
        with self.assertRaises(LookupError):
            v.apply_validation(99, conn=db)

    def test_non_submitted_lease_is_skipped_not_revived(self):
        db = FakeDb([("FROM vkpi_local_task_leases", self._lease_row(status="rejected"))])
        out = v.apply_validation(1, conn=db)
        self.assertEqual(out["ok"], False)
        self.assertTrue(out["skipped"])
        self.assertEqual(out["reason"], "lease_status_not_submitted:rejected")
        self.assertEqual(db.commits, 0)

    def test_staging_without_result_object_rejects_and_releases(self):
        db = FakeDb([("FROM vkpi_local_task_leases", self._lease_row(result_json='{"files_meta": []}'))])
        out = v.apply_validation(1, conn=db)
        self.assertFalse(out["ok"])
        self.assertEqual(out["validated"], 0)
        self.assertIn("no result object", out["notes"])
        self.assertEqual(self.released, [(10, 1)])
        self.assertEqual(db.commits, 1)
        reject_sql = [sql for sql, _ in db.executed if "status = 'rejected'" in sql]
        self.assertEqual(len(reject_sql), 1)

    def test_deep_problem_rejects_with_notes(self):
        row = self._lease_row(result_json='{"result": {"http_status": 200}}')
        db = FakeDb([("FROM vkpi_local_task_leases", row)])
        out = v.apply_validation(1, conn=db)
        self.assertFalse(out["ok"])
        self.assertEqual(out["validated"], 0)
        self.assertTrue(any("playable" in p for p in out["problems"]))
        self.assertEqual(self.released, [(10, 1)])

    def test_pass_path_marks_validated_but_precheck_stays_pending(self):
        db = FakeDb([
            ("FROM vkpi_local_task_leases", self._lease_row()),
            ("FROM apify_jobs", {"payload": {"url": "https://a.b/c"}}),
        ])
        out = v.apply_validation(1, conn=db)
        self.assertTrue(out["ok"])
        self.assertEqual(out["validated"], 1)
        self.assertEqual(out["ingest"]["status"], "pending_ingest:no_reusable_precheck_writer")
        self.assertNotIn("job_action", out["ingest"])  # final=False 不动 apify_jobs
        self.assertEqual(self.released, [])
        validated_sql = [sql for sql, _ in db.executed if "result_validated = 1" in sql]
        self.assertEqual(len(validated_sql), 1)

    def test_full_metadata_final_path_marks_job_done(self):
        import app.domains.kol.video_evidence as ve

        row = self._lease_row(
            task_type="metadata_extract",
            result_json=(
                '{"result": {"title": "t", "duration_seconds": 5, "view_count": 2,'
                ' "url": "https://a.b/c"}}'
            ),
        )
        db = FakeDb([
            ("FROM vkpi_local_task_leases", row),
            ("FROM apify_jobs", {"payload": '{"url": "https://a.b/c", "kol_pool_id": 3}'}),
            ("UPDATE apify_jobs", FakeCursor(rowcount=1)),
        ])
        original = ve.ensure_video_evidence_from_url
        ve.ensure_video_evidence_from_url = lambda *a, **k: {"ok": True, "status": "created", "evidence_id": 5}
        try:
            out = v.apply_validation(1, conn=db)
        finally:
            ve.ensure_video_evidence_from_url = original
        self.assertTrue(out["ok"])
        self.assertEqual(out["ingest"]["status"], "ingested:created")
        self.assertEqual(out["ingest"]["job_action"], "job_marked_done")
        self.assertEqual(self.released, [(10, 1)])


class LoadJobPayloadTests(unittest.TestCase):
    def test_zero_job_id_and_missing_row(self):
        self.assertEqual(v._load_job_payload(FakeDb(), 0), {})
        db = FakeDb([("FROM apify_jobs", None)])
        self.assertEqual(v._load_job_payload(db, 3), {})

    def test_payload_json_string_and_dict_forms(self):
        db = FakeDb([("FROM apify_jobs", {"payload": '{"a": 1}'})])
        self.assertEqual(v._load_job_payload(db, 3), {"a": 1})
        db = FakeDb([("FROM apify_jobs", {"payload": {"b": 2}})])
        self.assertEqual(v._load_job_payload(db, 3), {"b": 2})


class MetadataMappingTests(unittest.TestCase):
    def test_metadata_from_result_types_and_fallbacks(self):
        out = v._metadata_from_result(
            {
                "title": " T ",
                "view_count": "10",
                "duration": 7,
                "platform": "youtube",
                "posted_at": "2026-01-01",
            },
            "https://a.b/c",
        )
        self.assertEqual(out["content_url"], "https://a.b/c")
        self.assertEqual(out["title"], "T")
        self.assertEqual(out["view_count"], 10)
        self.assertEqual(out["duration_seconds"], 7)
        self.assertIsNone(out["like_count"])
        self.assertEqual(out["posted_at"], "2026-01-01")


if __name__ == "__main__":
    unittest.main()
