"""Terminal video blockers keep canonical reasons and honest video scope."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from app.domains.kol.video_analysis_enqueue import list_kol_all_evidence_ids
from app.workers import apify_jobs_worker


class _Cursor:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.connection.calls.append((sql, params))


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> "_Connection":
        return self

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def cursor(self) -> _Cursor:
        return _Cursor(self)


def test_block_job_keeps_canonical_reason_and_preserves_resolver_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection()
    monkeypatch.setattr(apify_jobs_worker, "_sync_search_session_job", lambda *_args, **_kwargs: None)

    apify_jobs_worker._block_job(
        connection, 9, "image_post_no_video",  # type: ignore[arg-type]
        {"reason": "media_resolve_failed:instagram:image_post_no_video_confirmed", "method": "ytdlp_fallback"},
    )

    payload = json.loads(connection.calls[0][1][0])
    assert payload["reason"] == "image_post_no_video"
    assert payload["reason_detail"] == "media_resolve_failed:instagram:image_post_no_video_confirmed"
    assert payload["method"] == "ytdlp_fallback"


def test_all_video_scope_excludes_known_non_video_but_retains_unknown() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE vkpi_kol_video_evidence (
          id INTEGER PRIMARY KEY,
          kol_pool_id INTEGER NOT NULL,
          is_active BOOLEAN,
          evidence_type TEXT,
          media_kind TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO vkpi_kol_video_evidence (id, kol_pool_id, is_active, evidence_type, media_kind) VALUES (?, 88, ?, ?, ?)",
        [
            (7, True, "media_article", None),
            (6, True, "video", "video"),
            (5, True, "video", "image"),
            (4, True, "video", "CAROUSEL"),
            (3, True, "", None),
            (2, True, None, ""),
            (1, False, "video", "video"),
        ],
    )

    assert list_kol_all_evidence_ids(connection, 88) == [6, 3, 2]


def test_all_video_scope_tolerates_legacy_missing_media_kind() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE vkpi_kol_video_evidence (
          id INTEGER PRIMARY KEY,
          kol_pool_id INTEGER NOT NULL,
          is_active BOOLEAN,
          evidence_type TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO vkpi_kol_video_evidence VALUES (?, 88, TRUE, ?)",
        [(3, "video"), (2, "media_article"), (1, None)],
    )

    assert list_kol_all_evidence_ids(connection, 88) == [3, 1]
