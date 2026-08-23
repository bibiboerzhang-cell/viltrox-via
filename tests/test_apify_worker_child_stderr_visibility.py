"""优化波 B·C1:分析器子进程失败时,stderr 尾巴(≤500 字、脱敏)进 diagnostics.child_stderr_tail 并 warning 一行。

跨车道契约 V→O:子进程失败时 diagnostics 必带 child_stderr_tail。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from app.workers import apify_jobs_worker_media as media


SECRET = "AIzaSyD-FAKEFAKEFAKEFAKEFAKEFAKEFAKE1234"


def _child(monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    monkeypatch.setattr(media, "_gemini_analyzer_child_code", lambda: script)
    monkeypatch.setattr(media, "GEMINI_CALL_TIMEOUT_SECONDS", 30)


def _run(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return media._run_gemini_analyzer_with_timeout(
        payload or {"mode": "youtube", "url": "https://www.youtube.com/watch?v=abcdefghijk"},
        job_id=77,
        target_id="701",
        platform="youtube",
    )


def test_nonzero_exit_without_json_surfaces_redacted_stderr_tail(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _child(
        monkeypatch,
        "import sys\n"
        f"sys.stderr.write('Traceback ... proxy=http://user:pw@gate.decodo.com:10001 key={SECRET} token=abc123 ' + 'x' * 900 + ' END\\n')\n"
        "sys.exit(3)\n",
    )
    with caplog.at_level(logging.WARNING):
        raw = _run()
    assert raw["analyzed"] is False
    assert raw["error"].startswith("gemini_child_no_json")
    diag = raw["diagnostics"]
    tail = diag["child_stderr_tail"]
    assert len(tail) <= 500
    assert tail.rstrip().endswith("END")
    assert diag["child_returncode"] == 3
    assert diag["child_failure_reason"] == "no_json"
    assert SECRET not in raw["error"] and "pw@" not in raw["error"]
    warnings = [rec for rec in caplog.records if "gemini analyzer child failed" in rec.getMessage()]
    assert len(warnings) == 1
    assert SECRET not in warnings[0].getMessage()


def test_child_redaction_covers_google_keys_and_userinfo() -> None:
    text = f"GET https://generativelanguage.googleapis.com/v1?key={SECRET} via http://u:p@proxy:1 Authorization: Bearer abc.def"
    tail = media.child_stderr_tail(text)
    assert SECRET not in tail
    assert "u:p@" not in tail
    assert "abc.def" not in tail
    assert media.child_stderr_tail("") == ""
    assert len(media.child_stderr_tail("y" * 5000)) == 500


def test_json_result_with_error_and_zero_exit_still_attaches_stderr(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    body = json.dumps({"ok": True, "raw": {"analyzed": False, "method": "gemini_youtube", "error": "yt-dlp video download failed for Gemini analysis"}})
    _child(
        monkeypatch,
        "import sys\n"
        "sys.stderr.write('WARNING gemini_fileapi_download_failed stderr_tail=Sign in to confirm you are not a bot\\n')\n"
        f"print({body!r})\n",
    )
    with caplog.at_level(logging.WARNING):
        raw = _run()
    assert raw["analyzed"] is False
    assert raw["diagnostics"]["child_failure_reason"] == "result_error"
    assert "not a bot" in raw["diagnostics"]["child_stderr_tail"]
    assert raw["provider_subprocess"]["returncode"] == 0
    assert any("gemini analyzer child failed" in rec.getMessage() for rec in caplog.records)


def test_json_result_with_nonzero_exit_gets_exit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"ok": False, "raw": {"analyzed": False, "method": "gemini_worker_child"}})
    _child(monkeypatch, f"import sys\nsys.stderr.write('boom\\n')\nprint({body!r})\nsys.exit(1)\n")
    raw = _run()
    assert raw["error"].startswith("gemini_child_exit:1")
    assert raw["diagnostics"]["child_failure_reason"] == "nonzero_exit"
    assert raw["diagnostics"]["child_stderr_tail"].strip() == "boom"


def test_successful_child_keeps_diagnostics_from_analyzer_untouched(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    body = json.dumps({"ok": True, "raw": {"analyzed": True, "method": "gemini_direct_gemini-3.6-flash", "model": "gemini-3.6-flash", "error": None, "diagnostics": {"truncation": {"hit": False, "recovered": False}}}})
    _child(monkeypatch, f"print({body!r})\n")
    with caplog.at_level(logging.WARNING):
        raw = _run()
    assert raw["analyzed"] is True
    assert "child_stderr_tail" not in raw["diagnostics"]
    assert raw["diagnostics"]["truncation"] == {"hit": False, "recovered": False}
    assert not any("gemini analyzer child failed" in rec.getMessage() for rec in caplog.records)


def test_failure_diagnostics_persist_child_tail_and_analyzer_keys() -> None:
    from app.workers.apify_jobs_worker_gemini_stages import analyzer_failure_diagnostics

    raw = {
        "method": "gemini_worker_subprocess",
        "diagnostics": {
            "child_stderr_tail": "tail text",
            "truncation": {"hit": True, "recovered": False},
            "retries": {"count": 2, "backoff_ms": 2800, "calls": 1, "errors": ["ProxyError: 522"]},
            "method": "must-not-override",
        },
        "youtube_direct": {"attempted": True},
    }
    out = analyzer_failure_diagnostics(raw, platform="youtube", error="Gemini video analysis failed: x")
    assert out["child_stderr_tail"] == "tail text"
    assert out["truncation"] == {"hit": True, "recovered": False}
    assert out["retries"]["count"] == 2
    assert out["method"] == "gemini_worker_subprocess"  # 固定键优先
    assert out["platform"] == "youtube"
