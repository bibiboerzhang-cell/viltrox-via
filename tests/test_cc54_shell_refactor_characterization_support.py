"""共享层(千行卫兵拆分)。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


# ── 通用 fake conn(按 SQL 片段路由) ────────────────────────────


class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _RouteConn:
    def __init__(self, routes: dict[str, list[Any]]):
        self.routes = routes
        self.calls: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()):  # noqa: D401
        self.calls.append((sql, tuple(params)))
        for key, rows in self.routes.items():
            if key in sql:
                return _Result(rows)
        return _Result([])

    def commit(self) -> None:
        self.commits += 1


# ════════════════════════════════════════════════════════════════
# 1) refresh_audience_stats
# ════════════════════════════════════════════════════════════════

from app.domains.kol import audience_stats  # noqa: E402


def _pool_row(platform: str) -> dict[str, Any]:
    return {
        "id": 7,
        "platform": platform,
        "handle": "tester",
        "profile_url": "https://youtube.com/@tester",
        "raw_platform_data": "{}",
    }


def _patch_common(monkeypatch: pytest.MonkeyPatch, conn: _RouteConn) -> None:
    from app.db import connection

    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(audience_stats, "_utcnow_iso", lambda: "2026-08-30T00:00:00Z")


def _happy_youtube_patches(
    monkeypatch: pytest.MonkeyPatch,
    conn: _RouteConn,
    *,
    sample_extra: dict[str, Any] | None = None,
    age_raises: bool = False,
) -> dict[str, Any]:
    _patch_common(monkeypatch, conn)
    sample = {
        "status": "ok",
        "comments_scanned": 40,
        "channel_id": "UCabc",
        "commenters": [{"author_key": "u1"}, {"author_key": "u2"}],
        "comments": [{"text": "nice"}],
        "reply_total": 5,
        **(sample_extra or {}),
    }
    monkeypatch.setattr(audience_stats, "_youtube_channel_ref", lambda rec: "@tester")
    monkeypatch.setattr(audience_stats, "sample_youtube_commenters", lambda ref, max_comments: dict(sample))
    monkeypatch.setattr(
        audience_stats,
        "_infer_with_cache",
        lambda conn, platform, commenters: (
            [{"author_key": "u1"}, {"author_key": "u2"}],
            {"cache_hits": 1, "inferred_fresh": 1, "cache_written": 1},
        ),
    )
    if age_raises:
        def _age_boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("age blew up")

        monkeypatch.setattr(audience_stats, "_age_ensemble", _age_boom)
    else:
        monkeypatch.setattr(
            audience_stats,
            "_age_ensemble",
            lambda conn, platform, inferred, llm_max_batches, allow_avatar_provider: {
                "llm": {"status": "ok", "calls": 1, "people_in": 2},
                "m3": "unavailable",
                "counts": {"llm": 2},
            },
        )
    monkeypatch.setattr(
        audience_stats,
        "aggregate_audience",
        lambda kol_pool_id, inferred, conn, platform: {"sample_size": 2, "confidence": "low"},
    )
    from app.domains.kol import comment_intel as ci

    monkeypatch.setattr(
        ci,
        "analyze_comments",
        lambda comments: {"sample_size": 8, "engagement": {"like_pct": 1.0}},
    )
    monkeypatch.setattr(ci, "compute_audience_overlap", lambda kol_pool_id, conn: {"items": [], "self_commenters": 9})
    monkeypatch.setattr(
        audience_stats,
        "_yt_audience_affinity",
        lambda cids, channel_id: {"items": [{"channel_id": "UCother"}], "checked": len(cids)},
    )
    return sample


# ════════════════════════════════════════════════════════════════
# 2) get_ai_today_hot
# ════════════════════════════════════════════════════════════════

from app.domains.market import ai_today  # noqa: E402


def _hot_row(snapshot_date: str, content: dict[str, Any] | str, model: str = "test-model", created_at: str = "") -> dict[str, Any]:
    return {
        "snapshot_date": snapshot_date,
        "content_json": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
        "model": model,
        "created_at": created_at or f"{snapshot_date}T08:00:00Z",
    }


def _ready_content(generated_at: str, *, pipeline: bool = True) -> dict[str, Any]:
    content: dict[str, Any] = {
        "headline": "今日热点标题",
        "shooting_plans": ["拍摄计划一"],
        "hot_topics": ["话题一"],
        "sources": [
            {"relation_type": "grounding", "url": "https://example.com/a", "title": "A"},
        ],
        "generated_at": generated_at,
    }
    if pipeline:
        content["provenance"] = {"pipeline": "ai_today_evidence_strategy_v1"}
    return content


def _patch_ai_today_read(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    *,
    attempt: dict[str, Any] | None = None,
    market_sources: list[dict[str, Any]] | None = None,
) -> None:
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: _RouteConn({"FROM vkpi_ai_today_hot": rows}))
    monkeypatch.setattr(ai_today, "_latest_scheduler_attempt", lambda conn: dict(attempt or {}))
    monkeypatch.setattr(ai_today, "_market_sources", lambda *_args, **_kwargs: list(market_sources or []))
    monkeypatch.setattr(ai_today, "_recommended_video_rows", lambda: [])


def _fresh_iso() -> str:
    return (
        datetime.now(tz=timezone.utc) - timedelta(hours=1)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")


# ════════════════════════════════════════════════════════════════
# 3) get_brand_pulse
# ════════════════════════════════════════════════════════════════

from app.domains.market import brand_pulse as bp  # noqa: E402


def _bp_evidence(eid: int, title: str, day: str, *, kol_id: int | None = 1, views: int = 100) -> dict[str, Any]:
    return {
        "evidence_id": eid,
        "kol_pool_id": kol_id,
        "platform": "youtube",
        "content_url": f"https://youtu.be/v{eid}",
        "view_count": views,
        "video_title": title,
        "title_alt": "",
        "pub_day": day,
        "kol_name": f"kol-{kol_id}",
    }


def _patch_brand_pulse(
    monkeypatch: pytest.MonkeyPatch,
    evidence: list[dict[str, Any]],
    deep_rows: list[dict[str, Any]] | None = None,
) -> None:
    conn = _RouteConn(
        {
            "FROM vkpi_kol_video_evidence e": evidence,
            "FROM vkpi_analysis_cache ac": deep_rows or [],
        }
    )
    monkeypatch.setattr(bp, "get_conn", lambda: conn)
    monkeypatch.setattr(
        bp,
        "_competitor_vocab",
        lambda: {
            "sony": {"keywords": ["sony"], "priority": "p1", "category": "camera", "brand_type": "competitor"},
            "sigma": {"keywords": ["sigma"], "priority": "p2", "category": "lens", "brand_type": "competitor"},
        },
    )
    monkeypatch.setattr(bp, "_viltrox_terms", lambda: ["viltrox"])
    monkeypatch.setattr(bp, "_matcher", lambda: (lambda text, kw: str(kw).lower() in str(text or "").lower()))


def _day(offset: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=offset)).isoformat()


# ════════════════════════════════════════════════════════════════
# 4) run_recommendations(hermetic sqlite 全链落库)
# ════════════════════════════════════════════════════════════════

from app.db.connection import get_conn as _real_get_conn  # noqa: E402
from app.domains.kol import pool as kol_pool  # noqa: E402
from app.domains.kol.competitor_detector import ensure_competitor_relation_schema  # noqa: E402
from app.domains.recommendations import product_analysis  # noqa: E402
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema  # noqa: E402

RUN_MARKER = "cc54-runrec-characterization"


def _run_cleanup() -> None:
    conn = _real_get_conn()
    rec_rows = conn.execute(
        "SELECT id, run_id FROM vkpi_kol_recommendations WHERE handle LIKE ?",
        (f"{RUN_MARKER}%",),
    ).fetchall()
    rec_ids = [int(row["id"]) for row in rec_rows]
    run_ids = sorted({int(row["run_id"]) for row in rec_rows if row["run_id"] is not None})
    for rec_id in rec_ids:
        conn.execute("DELETE FROM vkpi_recommendation_feedback WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_explanations WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_kol_recommendations WHERE id=?", (rec_id,))
    for run_id in run_ids:
        conn.execute("DELETE FROM vkpi_kol_recommendation_runs WHERE id=?", (run_id,))
    pool_rows = conn.execute("SELECT id FROM vkpi_kol_pool WHERE source_ref=?", (RUN_MARKER,)).fetchall()
    for row in pool_rows:
        conn.execute("DELETE FROM vkpi_competitor_relation WHERE kol_pool_id=?", (int(row["id"]),))
    conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=?", (RUN_MARKER,))
    conn.execute("DELETE FROM vkpi_product_launches WHERE name LIKE ?", (f"{RUN_MARKER}%",))
    conn.commit()
    kol_pool._clear_kol_pool_read_cache()


def _run_insert_pool_row(handle: str, *, fit_score: int, platform: str = "youtube") -> int:
    conn = _real_get_conn()
    now = "2026-08-20T10:00:00Z"
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool
          (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
           followers, following, posts_count, avg_views, avg_likes, avg_comments,
           engagement_rate, viltrox_fit_score, source_type, source_ref, raw_platform_data,
           created_by_staff_id, last_seen_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            f"{handle}-uid",
            platform,
            handle,
            f"https://youtube.com/@{handle}",
            handle,
            "",
            f"{RUN_MARKER} camera lens review",
            "",
            250000,
            None,
            12,
            50000,
            1200,
            80,
            0.035,
            fit_score,
            "unit",
            RUN_MARKER,
            json.dumps({"videos": []}),
            None,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM vkpi_kol_pool WHERE handle=?", (handle,)).fetchone()["id"])
