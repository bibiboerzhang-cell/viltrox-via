"""失败池治理单测:triage_report 分桶口径 + recycle dry_run 绝不改库。

用一个内存 FakeConn 模拟 compat 层(? 占位符、execute().fetchall()、UPDATE 计数),
不依赖活 DB,CI 友好。覆盖:
  - 分桶按 job_type × 有效类别,且 disposition 正确(可回收/永久/unknown);
  - 真实 last_error 重派生纠正持久化脏标(unknown→content_unavailable/code_error);
  - recycle dry_run=True 只返清单、UPDATE 计数恒为 0(不改库);
  - recycle dry_run=False 只动可回收且有预算的行,UPDATE 真的发生;
  - attempts 耗尽(>=max_attempts)的行不进回收候选。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.kol import failed_pool_triage as triage  # noqa: E402


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """最小 compat 连接:SELECT failed 行 + 计 UPDATE 次数。? 占位符。"""

    def __init__(self, jobs: list[dict]):
        # jobs: 每行 {id, job_type, last_error_category, last_error, attempts, status}
        self.jobs = jobs
        self.update_count = 0
        self.commit_count = 0

    def execute(self, sql: str, params=None):
        s = " ".join(sql.split()).lower()
        params = params or ()
        if s.startswith("update apify_jobs"):
            self.update_count += 1
            # 模拟 WHERE id=? AND status='failed':把对应行置 queued
            job_id = params[-1]
            for j in self.jobs:
                if j["id"] == job_id and j.get("status") == "failed":
                    j["status"] = "queued"
            return _FakeCursor([])
        if s.startswith("select"):
            failed = [j for j in self.jobs if j.get("status") == "failed"]
            # recycle 的 SELECT 带 "attempts < ?" 过滤
            if "attempts < ?" in s:
                max_attempts = params[0]
                failed = [j for j in failed if int(j.get("attempts") or 0) < max_attempts]
            out = []
            for j in failed:
                out.append(
                    {
                        "id": j["id"],
                        "job_type": j.get("job_type") or "",
                        "stored_category": j.get("last_error_category") or "",
                        "last_error": j.get("last_error"),
                        "attempts": j.get("attempts", 0),
                    }
                )
            return _FakeCursor(out)
        return _FakeCursor([])

    def commit(self):
        self.commit_count += 1


def _make_jobs() -> list[dict]:
    return [
        # 可回收:provider_pressure(82-ish)
        {"id": 1, "job_type": "video", "last_error_category": "provider_pressure",
         "last_error": "429 RESOURCE_EXHAUSTED quota exceeded", "attempts": 1, "status": "failed"},
        # 可回收:download(yt-dlp 超时)
        {"id": 2, "job_type": "video", "last_error_category": "download",
         "last_error": "direct_video_download_failed: timed out after 90s", "attempts": 1, "status": "failed"},
        # 可回收:media_resolve
        {"id": 3, "job_type": "video", "last_error_category": "media_resolve",
         "last_error": "media_resolve_failed: media_resolve_failed", "attempts": 1, "status": "failed"},
        # 永久:content_unavailable(404)
        {"id": 4, "job_type": "video", "last_error_category": "content_unavailable",
         "last_error": "precheck: youtube oEmbed 404/410 (deleted_or_private)", "attempts": 1, "status": "failed"},
        # 脏标:DB 标 unknown 但其实是 code_error(payload ValueError)→ 永久,不回收
        {"id": 5, "job_type": "kol_auto_poll", "last_error_category": "unknown",
         "last_error": "ValueError: payload must include target_type and target_id", "attempts": 1, "status": "failed"},
        # 可回收类别但 attempts 耗尽(>=2)→ 不进回收候选
        {"id": 6, "job_type": "video", "last_error_category": "download",
         "last_error": "yt-dlp exited 1", "attempts": 2, "status": "failed"},
        # 真 unknown(无法派生)→ disposition unknown,不回收
        {"id": 7, "job_type": "project_contract_extract", "last_error_category": "unknown",
         "last_error": "something weird happened with no signal", "attempts": 1, "status": "failed"},
        # done 行不该被 SELECT 命中
        {"id": 8, "job_type": "video", "last_error_category": None,
         "last_error": None, "attempts": 1, "status": "done"},
    ]


class TriageReportTests(unittest.TestCase):
    def _run_report(self, jobs):
        fake = FakeConn(jobs)
        with patch.object(triage, "get_conn", return_value=fake), \
                patch.dict("os.environ", {"APIFY_WORKER_MAX_ATTEMPTS": "2"}):
            return triage.triage_report(), fake

    def test_buckets_count_and_disposition(self) -> None:
        report, _ = self._run_report(_make_jobs())
        # 7 failed(id8 是 done,不计)
        self.assertEqual(report["total_failed"], 7)
        by_key = {(b["job_type"], b["category"]): b for b in report["buckets"]}

        # provider_pressure / download / media_resolve → recyclable
        self.assertEqual(by_key[("video", "provider_pressure")]["disposition"], "recyclable")
        self.assertTrue(by_key[("video", "provider_pressure")]["recyclable"])
        self.assertEqual(by_key[("video", "media_resolve")]["disposition"], "recyclable")

        # download 桶含 id2(attempts1) 与 id6(attempts2),count=2 但 with_retry_budget=1
        dl = by_key[("video", "download")]
        self.assertEqual(dl["count"], 2)
        self.assertEqual(dl["with_retry_budget"], 1)
        self.assertEqual(dl["disposition"], "recyclable")

        # content_unavailable → permanent
        self.assertEqual(by_key[("video", "content_unavailable")]["disposition"], "permanent")

    def test_dirty_unknown_redrived_to_code_error_permanent(self) -> None:
        # GAP 取证:_error_category 的 code_error 词表**漏了 ValueError**(只有
        # TypeError/KeyError/... 没有 ValueError),所以 kol_auto_poll 的
        # "ValueError: payload must include..." 重派生后**仍是 unknown**。
        # 关键安全性:unknown 默认**不可回收** → 这 50 条永久 payload 错不会被误回收。
        report, _ = self._run_report(_make_jobs())
        by_key = {(b["job_type"], b["category"]): b for b in report["buckets"]}
        bucket = by_key[("kol_auto_poll", "unknown")]
        self.assertEqual(bucket["disposition"], "unknown")
        self.assertFalse(bucket["recyclable"])

        # 反证:换成 TypeError(在词表里)就能正确重派生为 code_error/permanent。
        jobs = _make_jobs()
        for j in jobs:
            if j["id"] == 5:
                j["last_error"] = "TypeError: payload must include target_type and target_id"
        report2, _ = self._run_report(jobs)
        by_key2 = {(b["job_type"], b["category"]): b for b in report2["buckets"]}
        self.assertIn(("kol_auto_poll", "code_error"), by_key2)
        self.assertEqual(by_key2[("kol_auto_poll", "code_error")]["disposition"], "permanent")

    def test_true_unknown_stays_unknown_and_not_recyclable(self) -> None:
        report, _ = self._run_report(_make_jobs())
        by_key = {(b["job_type"], b["category"]): b for b in report["buckets"]}
        bucket = by_key[("project_contract_extract", "unknown")]
        self.assertEqual(bucket["disposition"], "unknown")
        self.assertFalse(bucket["recyclable"])

    def test_totals_partition_cleanly(self) -> None:
        report, _ = self._run_report(_make_jobs())
        total = report["recyclable_total"] + report["permanent_total"] + report["unknown_total"]
        self.assertEqual(total, report["total_failed"])


class RecycleDryRunTests(unittest.TestCase):
    def _run_recycle(self, jobs, dry_run):
        fake = FakeConn(jobs)
        with patch.object(triage, "get_conn", return_value=fake), \
                patch.dict("os.environ", {"APIFY_WORKER_MAX_ATTEMPTS": "2"}):
            return triage.recycle_recyclable(dry_run=dry_run), fake

    def test_dry_run_does_not_mutate_db(self) -> None:
        jobs = _make_jobs()
        result, fake = self._run_recycle(jobs, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["recycled_count"], 0)
        # 绝不写库:零 UPDATE
        self.assertEqual(fake.update_count, 0)
        # 所有 failed 行原封不动
        self.assertTrue(all(j["status"] == "failed" for j in jobs if j["id"] != 8))

    def test_dry_run_candidates_are_only_recyclable_with_budget(self) -> None:
        result, _ = self._run_recycle(_make_jobs(), dry_run=True)
        ids = {c["id"] for c in result["candidates"]}
        # id1(pp), id2(download), id3(media_resolve) 可回收且有预算
        self.assertEqual(ids, {1, 2, 3})
        self.assertEqual(result["candidate_count"], 3)
        # 永久(id4,id5)、attempts耗尽(id6)、真unknown(id7)都不在
        for bad in (4, 5, 6, 7, 8):
            self.assertNotIn(bad, ids)

    def test_wet_run_recycles_only_candidates(self) -> None:
        jobs = _make_jobs()
        result, fake = self._run_recycle(jobs, dry_run=False)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["recycled_count"], 3)
        self.assertEqual(fake.update_count, 3)
        status_by_id = {j["id"]: j["status"] for j in jobs}
        # 候选被重置回 queued
        self.assertEqual(status_by_id[1], "queued")
        self.assertEqual(status_by_id[2], "queued")
        self.assertEqual(status_by_id[3], "queued")
        # 非候选仍 failed
        for bad in (4, 5, 6, 7):
            self.assertEqual(status_by_id[bad], "failed")


if __name__ == "__main__":
    unittest.main()
