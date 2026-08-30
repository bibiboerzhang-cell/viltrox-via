from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import main_release_validation
from app.api.routers import (
    auth,
    media,
    vkpi_goaffpro,
    vkpi_kol_pool_helpers,
    vkpi_kol_pool_intel,
)
from app.core import permissions, release_validation, security
from app.db import connection
from app.domains.integrations import goaffpro_connect
from app.platform import apify_budget
from app.services.cache import memory_cache
from app.services.scheduler import fleet_guard
from app.workers import apify_jobs_worker, worker_main


ROOT = Path(__file__).resolve().parents[1]


def _install_local_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    marker = tmp_path / "release-validation.fence"
    marker.write_text(release_validation.FENCE_PAYLOAD, encoding="utf-8")
    marker.chmod(0o444)
    monkeypatch.setattr(release_validation, "IS_PRODUCTION", False)
    monkeypatch.setenv("VKPI_RELEASE_VALIDATION_FENCE_PATH", str(marker))
    return marker


def test_verified_local_marker_is_active_and_absence_is_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = _install_local_fence(tmp_path, monkeypatch)
    assert release_validation.release_validation_status() == {
        "active": True,
        "valid": True,
        "source": "verified_marker",
    }
    marker.chmod(0o644)
    marker.unlink()
    assert release_validation.release_validation_status() == {
        "active": False,
        "valid": True,
        "source": "absent",
    }


def test_malformed_or_symlink_marker_stays_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = _install_local_fence(tmp_path, monkeypatch)
    marker.chmod(0o600)
    assert release_validation.release_validation_status()["active"] is True
    assert release_validation.release_validation_status()["valid"] is False

    marker.unlink()
    target = tmp_path / "target"
    target.write_text(release_validation.FENCE_PAYLOAD, encoding="utf-8")
    target.chmod(0o444)
    marker.symlink_to(target)
    status = release_validation.release_validation_status()
    assert status["active"] is True
    assert status["valid"] is False


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_read_only_methods_remain_available(method: str) -> None:
    assert release_validation.release_validation_request_allowed(
        method, "/health"
    )


def test_options_remains_available_for_cors_preflight() -> None:
    assert release_validation.release_validation_request_allowed(
        "OPTIONS", "/api/admin/vkpi/unknown"
    )


def test_only_reviewed_ask_post_is_available() -> None:
    assert release_validation.release_validation_request_allowed(
        "POST", "/api/admin/vkpi/intelligent/query"
    )
    assert release_validation.release_validation_request_allowed(
        "POST", "/api/marketing/intelligent/query"
    )
    assert release_validation.release_validation_request_allowed(
        "POST", "/api/admin/vkpi/event-radar/refresh-preview"
    )
    for method, path in (
        ("POST", "/api/admin/vkpi/jobs"),
        ("PUT", "/api/admin/vkpi/kols/1"),
        ("PATCH", "/api/admin/vkpi/projects/1"),
        ("DELETE", "/api/admin/vkpi/kols/1"),
    ):
        assert not release_validation.release_validation_request_allowed(method, path)


@pytest.mark.parametrize(
    ("path", "query"),
    [
        ("/go/campaign-1", None),
        ("/api/admin/vkpi/goaffpro", None),
        ("/api/admin/vkpi/kol-pool/7/content-fit", {"analyze": "true"}),
        ("/api/admin/vkpi/kol-pool/7/content-fit", b"force=1"),
    ],
)
def test_get_shaped_side_effects_are_blocked(path: str, query: object) -> None:
    assert not release_validation.release_validation_request_allowed("GET", path, query)


def test_unknown_get_is_fail_closed() -> None:
    assert not release_validation.release_validation_request_allowed(
        "GET", "/api/admin/vkpi/not-reviewed-yet"
    )


def test_browser_capture_read_paths_are_explicitly_available() -> None:
    captured_exact_paths = {
        "/api/admin/runtime/metrics",
        "/api/admin/vkpi/actions/inbox",
        "/api/admin/vkpi/agents/loop/trace",
        "/api/admin/vkpi/alerts",
        "/api/admin/vkpi/attribution",
        "/api/admin/vkpi/attribution/unmatched",
        "/api/admin/vkpi/brand-signals",
        "/api/admin/vkpi/channels/assignments",
        "/api/admin/vkpi/channels/official-matrix",
        "/api/admin/vkpi/costs",
        "/api/admin/vkpi/dashboard/ai-today-hot",
        "/api/admin/vkpi/dashboard/competitor-radar",
        "/api/admin/vkpi/dashboard/copilot-brief",
        "/api/admin/vkpi/dashboard/fit-movers",
        "/api/admin/vkpi/dashboard/kol-distribution-pack",
        "/api/admin/vkpi/dashboard/product-performance",
        "/api/admin/vkpi/dashboard/recent-content",
        "/api/admin/vkpi/dashboard/revenue-trend",
        "/api/admin/vkpi/dashboard/tasks",
        "/api/admin/vkpi/dealers/rankings",
        "/api/admin/vkpi/event-radar/summary",
        "/api/admin/vkpi/events/upcoming",
        "/api/admin/vkpi/goaffpro/creds",
        "/api/admin/vkpi/goaffpro/summary",
        "/api/admin/vkpi/gtm/northstar",
        "/api/admin/vkpi/industry-data/hashtag-trends/v0",
        "/api/admin/vkpi/industry-data/market-intelligence/cards/v0",
        "/api/admin/vkpi/kol-pool/needs-analysis",
        "/api/admin/vkpi/kols",
        "/api/admin/vkpi/kpi-ledger",
        "/api/admin/vkpi/learning/weekly-scorecard",
        "/api/admin/vkpi/links",
        "/api/admin/vkpi/market/prd-referrals",
        "/api/admin/vkpi/market/voice-feed",
        "/api/admin/vkpi/market/voice-report",
        "/api/admin/vkpi/marketing-advisor/threads",
        "/api/admin/vkpi/media/image-proxy",
        "/api/admin/vkpi/morning-brief",
        "/api/admin/vkpi/my-kol/aggregate",
        "/api/admin/vkpi/my-kol/contribution-rollup",
        "/api/admin/vkpi/my-kol/daily-digest",
        "/api/admin/vkpi/my-kol/risk-index",
        "/api/admin/vkpi/ops/cost-ledger",
        "/api/admin/vkpi/prediction-ledger/summary",
        "/api/admin/vkpi/product-analysis/launches",
        "/api/admin/vkpi/product-costs",
        "/api/admin/vkpi/progress/center",
        "/api/admin/vkpi/projects/content-posts",
        "/api/admin/vkpi/projects/due-list",
        "/api/admin/vkpi/projects/observation-windows",
        "/api/admin/vkpi/reply-queue/kpi-series",
        "/api/admin/vkpi/settings/preferences",
        "/api/admin/vkpi/shopify/status",
        "/api/admin/vkpi/skills/runs",
        "/api/admin/vkpi/staff-directory",
        "/api/admin/vkpi/staff-groups",
        "/api/admin/vkpi/staff-kpi",
        "/api/admin/vkpi/task-queue/compact",
        "/api/admin/vkpi/tasks",
        "/api/admin/vkpi/tasks/realtime-status",
    }
    assert captured_exact_paths <= release_validation._CAPTURED_READ_ONLY_GET_PATHS
    for path in captured_exact_paths:
        assert release_validation.release_validation_request_allowed("GET", path), path

    for path in (
        "/api/marketing/channels/111/posts",
        "/api/admin/vkpi/kol-pool/4234/signature",
        "/api/admin/vkpi/kol-pool/4234/videos",
        "/api/admin/vkpi/marketing-advisor/threads/advthr_0123456789abcdef/messages",
        "/api/marketing/marketing-advisor/threads/advthr_0123456789abcdef/messages",
        "/api/vkpi-media/image-cache/" + "a" * 64,
    ):
        assert release_validation.release_validation_request_allowed("GET", path), path


def test_advisor_history_read_does_not_open_stream_or_mutation_while_fenced() -> None:
    messages = (
        "/api/admin/vkpi/marketing-advisor/threads/"
        "advthr_0123456789abcdef/messages"
    )
    assert release_validation.release_validation_request_allowed("GET", messages)
    assert release_validation.release_validation_request_allowed("HEAD", messages)
    assert not release_validation.release_validation_request_allowed(
        "GET", f"{messages}/stream"
    )
    assert not release_validation.release_validation_request_allowed("POST", messages)


def test_event_refresh_preview_is_post_only_while_fenced() -> None:
    path = "/api/admin/vkpi/event-radar/refresh-preview"
    assert release_validation.release_validation_request_allowed("POST", path)
    assert not release_validation.release_validation_request_allowed("GET", path)


@pytest.mark.parametrize(
    "path",
    (
        "/api/admin/vkpi/goaffpro/affiliates",
        "/api/admin/vkpi/goaffpro/orders",
    ),
)
def test_goaffpro_external_gets_remain_blocked(path: str) -> None:
    assert not release_validation.release_validation_request_allowed("GET", path)


def test_goaffpro_persisted_link_read_is_available_while_fenced() -> None:
    path = "/api/admin/vkpi/goaffpro/kol/7/link"
    assert release_validation.release_validation_request_allowed("GET", path)
    assert release_validation.release_validation_request_allowed(
        "GET", path, {"product": "AF 35mm"}
    )
    assert not release_validation.release_validation_request_allowed("POST", path)


def test_unknown_get_receives_503_while_fenced(monkeypatch) -> None:
    app = FastAPI()

    @app.get("/api/admin/vkpi/not-reviewed-yet")
    def unknown_get() -> dict:
        return {"unsafe": True}

    app.add_middleware(main_release_validation.ReleaseValidationFenceMiddleware)
    monkeypatch.setattr(
        main_release_validation,
        "release_validation_active",
        lambda: True,
    )

    with TestClient(app) as client:
        response = client.get("/api/admin/vkpi/not-reviewed-yet")

    assert response.status_code == 503
    assert response.json()["code"] == "release_validation_fenced"


def test_cached_content_fit_read_remains_available() -> None:
    assert release_validation.release_validation_request_allowed(
        "GET",
        "/api/admin/vkpi/kol-pool/7/content-fit",
        {"analyze": "false", "force": "0"},
    )


def test_auth_me_is_pure_read_while_fenced(monkeypatch) -> None:
    user = {"id": 17, "email": "release@example.com"}
    monkeypatch.setattr(auth, "get_current_user", lambda _request: user)
    monkeypatch.setattr(auth, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        auth,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("auth/me wrote while fenced")),
    )

    assert auth.auth_me(object()) == {"status": "success", "user": user}


def test_kol_detail_refresh_is_pure_read_while_fenced(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_kol_pool_helpers, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        vkpi_kol_pool_helpers.refresh_tier,
        "record_kol_search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("KOL search marker wrote while fenced")
        ),
    )
    monkeypatch.setattr(
        vkpi_kol_pool_helpers.refresh_tier,
        "freshness_for_kol",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("freshness schema guard ran while fenced")
        ),
    )

    result = asyncio.run(
        vkpi_kol_pool_helpers._maybe_enqueue_refresh(
            object(),
            7,
            staff={"id": 1},
            enabled=True,
        )
    )

    assert result == {
        "triggered": False,
        "reason": "release_validation_fenced",
        "freshness": None,
        "search_marker": None,
        "provider_calls_enabled": False,
    }


def test_content_fit_enqueue_has_cross_layer_fence(monkeypatch) -> None:
    monkeypatch.setattr(release_validation, "release_validation_active", lambda: True)

    with pytest.raises(RuntimeError, match="release validation fence"):
        vkpi_kol_pool_intel._enqueue_content_fit_on_demand(
            7,
            "AF 16mm",
            force=True,
            staff={"id": 1},
        )


def test_fenced_media_proxy_never_fetches_or_creates_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "not-created" / "cache"
    monkeypatch.setattr(media, "VKPI_IMAGE_PROXY_CACHE_DIR", cache_dir)
    monkeypatch.setattr(media, "get_current_user", lambda _request: {"id": 1})
    monkeypatch.setattr(media, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        media,
        "_fetch_external_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("external image provider called while fenced")
        ),
    )

    response = media.serve_vkpi_external_image(
        object(),
        "https://i.ytimg.com/vi/release-test/default.jpg",
    )

    assert response.body == media._TRANSPARENT_IMAGE_SVG
    assert response.headers["x-vkpi-media-fallback"] == "release_validation_fenced"
    assert response.headers["cache-control"] == "no-store"
    assert not cache_dir.exists()


def test_fenced_media_proxy_can_serve_a_healthy_existing_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(media, "VKPI_IMAGE_PROXY_CACHE_DIR", cache_dir)
    monkeypatch.setattr(media, "get_current_user", lambda _request: {"id": 1})
    monkeypatch.setattr(media, "release_validation_active", lambda: True)
    normalized_url = "https://i.ytimg.com/vi/release-test/default.jpg"
    cache_path, content_type_path = media._cached_external_image_path(normalized_url)
    cache_path.write_bytes(b"real-image-bytes")
    content_type_path.write_text("image/jpeg", encoding="utf-8")
    monkeypatch.setattr(
        media,
        "_fetch_external_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("healthy cache hit called external provider")
        ),
    )

    response = media.serve_vkpi_external_image(object(), normalized_url)

    assert Path(response.path) == cache_path
    assert response.headers["cache-control"] == "private, max-age=86400"


def test_fenced_goaffpro_creds_skips_schema_write_when_table_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(goaffpro_connect, "release_validation_active", lambda: True)
    monkeypatch.setattr(goaffpro_connect, "table_exists", lambda _name: False)
    monkeypatch.setattr(
        goaffpro_connect,
        "ensure_goaffpro_creds_schema",
        lambda: (_ for _ in ()).throw(AssertionError("GOAFFPRO creds schema write ran")),
    )
    monkeypatch.setattr(
        goaffpro_connect,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("missing GOAFFPRO table was queried")),
    )
    for key in (
        "GOAFFPRO_ACCESS_TOKEN",
        "GOAFFPRO_API_ACCESS_TOKEN",
        "GOAFFPRO_PUBLIC_TOKEN",
        "GOAFFPRO_PRIVATE_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    status = goaffpro_connect.connection_status()

    assert status["status"] == "not_configured"
    assert status["source"] == "none"


def test_fenced_goaffpro_summary_skips_schema_write_when_tables_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vkpi_goaffpro, "release_validation_active", lambda: True)
    monkeypatch.setattr(vkpi_goaffpro, "table_exists", lambda _name: False)
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "ensure_goaffpro_links_schema",
        lambda: (_ for _ in ()).throw(AssertionError("GOAFFPRO links schema write ran")),
    )
    monkeypatch.setattr(
        vkpi_goaffpro,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("missing GOAFFPRO tables were queried")),
    )

    result = vkpi_goaffpro.goaffpro_summary(
        limit=200,
        project_id=None,
        search=None,
        staff={"id": 1},
    )

    assert result == {
        "ok": True,
        "items": [],
        "count": 0,
        "totals": {
            "kol_count": 0,
            "clicks": 0,
            "orders": 0,
            "gmv_usd": 0.0,
            "commission_usd": 0.0,
        },
        "note": "release validation: cached GOAFFPRO tables unavailable",
    }


def test_fenced_web_postgres_checkout_is_read_only_and_resets_after_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRawCursor:
        def __init__(self, raw) -> None:
            self.connection = raw
            self.description = None
            self.rowcount = 0
            self._row = None

        def execute(self, sql, _params) -> None:
            statement = str(sql).strip().upper()
            if self.connection.read_only and statement.startswith(
                ("CREATE", "DELETE", "INSERT", "UPDATE")
            ):
                raise RuntimeError("read-only transaction")
            if statement.startswith("SELECT"):
                self.description = [("value",)]
                self._row = (1,)

        def fetchone(self):
            return self._row

        def fetchall(self):
            return [] if self._row is None else [self._row]

        def close(self) -> None:
            return None

    class FakeRawConnection:
        def __init__(self) -> None:
            self.read_only = False

        def cursor(self):
            return FakeRawCursor(self)

        def rollback(self) -> None:
            return None

    class FakePool:
        def __init__(self) -> None:
            self.raw = FakeRawConnection()
            self.returned = 0

        def getconn(self):
            return self.raw

        def putconn(self, raw) -> None:
            assert raw is self.raw
            self.returned += 1

    pool = FakePool()
    monkeypatch.setattr(connection, "_get_pg_pool", lambda: pool)
    monkeypatch.setattr(release_validation, "release_validation_active", lambda: True)

    fenced = connection._build_postgres_conn(release_validation_guard=True)
    assert fenced.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(RuntimeError, match="read-only transaction"):
        fenced.execute("INSERT INTO audit_log(value) VALUES (?)", (1,))
    fenced.close()

    monkeypatch.setattr(release_validation, "release_validation_active", lambda: False)
    activated = connection._build_postgres_conn(release_validation_guard=True)
    activated.execute("INSERT INTO audit_log(value) VALUES (?)", (1,))
    assert pool.raw.read_only is False
    activated.close()
    assert pool.returned == 2


class _ReadOnlyCacheRedis:
    def __init__(self, values: dict[str, bytes] | None = None) -> None:
        self.values = dict(values or {})
        self.get_calls = 0
        self.setex_calls = 0
        self.delete_calls = 0
        self.lock_calls = 0

    def get(self, key: str):
        self.get_calls += 1
        return self.values.get(key)

    def setex(self, *_args, **_kwargs) -> None:
        self.setex_calls += 1
        raise AssertionError("Redis SETEX ran while fenced")

    def delete(self, *_args, **_kwargs) -> None:
        self.delete_calls += 1
        raise AssertionError("Redis DELETE ran while fenced")

    def scan_iter(self, **_kwargs):
        yield b"vkpi:test"

    def lock(self, *_args, **_kwargs):
        self.lock_calls += 1
        raise AssertionError("Redis lock write ran while fenced")


def test_cache_set_fails_closed_without_redis_or_memory_write(monkeypatch) -> None:
    monkeypatch.setattr(memory_cache, "_cache", {})
    monkeypatch.setattr(memory_cache, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        memory_cache,
        "_get_redis",
        lambda: (_ for _ in ()).throw(AssertionError("fenced cache_set opened Redis")),
    )

    memory_cache.cache_set("release:test", {"value": 1}, ttl=60)

    assert memory_cache._cache == {}


def test_cache_guard_status_error_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(memory_cache, "_cache", {})
    monkeypatch.setattr(
        memory_cache,
        "release_validation_active",
        lambda: (_ for _ in ()).throw(RuntimeError("marker unreadable")),
    )
    monkeypatch.setattr(
        memory_cache,
        "_get_redis",
        lambda: (_ for _ in ()).throw(AssertionError("failed guard opened Redis")),
    )

    memory_cache.cache_set("release:error", {"value": 1}, ttl=60)

    assert memory_cache._cache == {}


def test_fenced_cold_cache_build_reads_but_never_sets_or_locks(monkeypatch) -> None:
    redis = _ReadOnlyCacheRedis()
    monkeypatch.setattr(memory_cache, "_cache", {})
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: redis)
    monkeypatch.setattr(memory_cache, "release_validation_active", lambda: True)
    builds: list[str] = []

    result = memory_cache.cache_get_or_build(
        "release:cold",
        lambda: builds.append("built") or {"fresh": True},
        ttl=60,
    )

    assert result == {"fresh": True}
    assert builds == ["built"]
    assert redis.get_calls == 1
    assert redis.setex_calls == 0
    assert redis.lock_calls == 0
    assert memory_cache._cache == {}


def test_fence_activation_after_distributed_lock_leaves_lease_to_expire(
    monkeypatch,
) -> None:
    class Lease:
        acquired = 0
        released = 0

        def acquire(self, *, blocking: bool) -> bool:
            assert blocking is True
            self.acquired += 1
            return True

        def release(self) -> None:
            self.released += 1

    class RedisWithLease:
        def __init__(self) -> None:
            self.lease = Lease()

        def lock(self, *_args, **_kwargs):
            return self.lease

    redis = RedisWithLease()
    states = iter((False, False, True))
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: redis)
    monkeypatch.setattr(
        memory_cache,
        "release_validation_active",
        lambda: next(states, True),
    )

    with memory_cache._distributed_build_lock("release:race") as acquired:
        assert acquired is True

    assert redis.lease.acquired == 1
    assert redis.lease.released == 0


def test_cache_delete_and_clear_recheck_before_each_mutation(monkeypatch) -> None:
    redis = _ReadOnlyCacheRedis()
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: redis)
    monkeypatch.setattr(memory_cache, "_cache", {"release:key": {"value": 1}})

    delete_states = iter((False, True, True))
    monkeypatch.setattr(
        memory_cache,
        "release_validation_active",
        lambda: next(delete_states, True),
    )
    assert memory_cache.cache_delete("release:key") is False
    assert redis.delete_calls == 0
    assert "release:key" in memory_cache._cache

    clear_states = iter((False, True, True))
    monkeypatch.setattr(
        memory_cache,
        "release_validation_active",
        lambda: next(clear_states, True),
    )
    assert memory_cache.cache_clear(prefix="release:") == 0
    assert redis.delete_calls == 0
    assert "release:key" in memory_cache._cache


def test_cache_set_lock_and_delete_resume_after_fence_is_removed(monkeypatch) -> None:
    class Lease:
        def __init__(self) -> None:
            self.acquired = 0
            self.released = 0

        def acquire(self, *, blocking: bool) -> bool:
            assert blocking is True
            self.acquired += 1
            return True

        def release(self) -> None:
            self.released += 1

    class WritableRedis:
        def __init__(self) -> None:
            self.setex_calls = 0
            self.delete_calls = 0
            self.lock_calls = 0
            self.lease = Lease()

        def setex(self, *_args, **_kwargs) -> None:
            self.setex_calls += 1

        def delete(self, *_args, **_kwargs) -> int:
            self.delete_calls += 1
            return 1

        def lock(self, *_args, **_kwargs):
            self.lock_calls += 1
            return self.lease

    redis = WritableRedis()
    active = {"value": True}
    monkeypatch.setattr(memory_cache, "_cache", {})
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: redis)
    monkeypatch.setattr(
        memory_cache,
        "release_validation_active",
        lambda: active["value"],
    )

    memory_cache.cache_set("release:resume", {"value": 1}, ttl=60)
    assert memory_cache.cache_delete("release:resume") is False
    with memory_cache._distributed_build_lock("release:resume") as acquired:
        assert acquired is False
    assert (redis.setex_calls, redis.delete_calls, redis.lock_calls) == (0, 0, 0)

    active["value"] = False
    memory_cache.cache_set("release:resume", {"value": 2}, ttl=60)
    with memory_cache._distributed_build_lock("release:resume") as acquired:
        assert acquired is True
    assert memory_cache.cache_delete("release:resume") is True
    assert (redis.setex_calls, redis.delete_calls, redis.lock_calls) == (1, 1, 1)
    assert (redis.lease.acquired, redis.lease.released) == (1, 1)


def test_fenced_cold_auth_and_whitelisted_get_make_zero_cache_writes(
    monkeypatch,
) -> None:
    redis = _ReadOnlyCacheRedis()

    class UserResult:
        def fetchone(self):
            return {
                "id": 7,
                "email": "release@example.com",
                "name": "Release Reviewer",
                "creator_code": "release-reviewer",
                "status": "approved",
                "role": "admin",
                "points_balance": 0,
                "points_pending": 0,
                "points_total": 0,
                "avatar_url": "",
                "bio": "",
                "signature": "",
                "tier_status": "active",
                "trust_score": 100,
                "trust_updated_at": "",
            }

    class UserConnection:
        def execute(self, *_args, **_kwargs):
            return UserResult()

    monkeypatch.setattr(memory_cache, "_cache", {})
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: redis)
    monkeypatch.setattr(memory_cache, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        main_release_validation,
        "release_validation_active",
        lambda: True,
    )
    monkeypatch.setattr(
        security,
        "db_connection_sync_reusing_scope",
        lambda: nullcontext(),
    )
    monkeypatch.setattr(security, "get_conn", lambda: UserConnection())
    monkeypatch.setattr(
        security,
        "verify_token",
        lambda token: {"uid": 7} if token == "cold-token" else None,
    )
    monkeypatch.setattr(
        permissions,
        "staff_context_for_user",
        lambda _user: {
            "id": 17,
            "role": "admin",
            "permissions": {"vkpi": "admin"},
            "is_owner": True,
        },
    )
    monkeypatch.setattr(permissions, "staff_context_is_inactive", lambda _staff: False)

    app = FastAPI()

    @app.get("/api/admin/vkpi/strategy/category-tracks")
    def reviewed_cold_get(request: Request) -> dict:
        user = security.get_current_user(request)
        payload = memory_cache.cache_get_or_build(
            "release:reviewed-get",
            lambda: {"count": 30},
            ttl=60,
        )
        return {"user_id": user["id"], "payload": payload}

    app.add_middleware(main_release_validation.ReleaseValidationFenceMiddleware)
    with TestClient(app) as client:
        response = client.get(
            "/api/admin/vkpi/strategy/category-tracks",
            headers={"Authorization": "Bearer cold-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"user_id": 7, "payload": {"count": 30}}
    assert redis.get_calls == 2
    assert redis.setex_calls == 0
    assert redis.delete_calls == 0
    assert redis.lock_calls == 0
    assert memory_cache._cache == {}


def test_apify_worker_cannot_reach_database_claim_while_fenced(monkeypatch) -> None:
    monkeypatch.setattr(apify_jobs_worker, "release_validation_active", lambda: True)

    class ExplodingConnection:
        def __getattribute__(self, name: str):
            raise AssertionError(f"database claim touched while fenced: {name}")

    assert apify_jobs_worker._claim_job(ExplodingConnection()) is None


def test_scheduler_does_not_create_fire_claim_while_fenced(monkeypatch) -> None:
    monkeypatch.setattr(fleet_guard, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        fleet_guard,
        "claim_scheduled_fire",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scheduler claim touched while fenced")
        ),
    )
    called: list[str] = []
    guarded = fleet_guard.guard_scheduled_callable(
        "release-test",
        lambda: called.append("ran"),
        owner_id="release-test-owner",
    )
    assert guarded() == {
        "status": "release_validation_fenced",
        "task_key": "release-test",
    }
    assert called == []


def test_async_scheduler_does_not_run_while_fenced(monkeypatch) -> None:
    monkeypatch.setattr(fleet_guard, "release_validation_active", lambda: True)

    async def callback() -> None:
        raise AssertionError("scheduled callback ran while fenced")

    guarded = fleet_guard.guard_scheduled_callable(
        "release-async-test",
        callback,
        owner_id="release-test-owner",
    )
    assert asyncio.run(guarded()) == {
        "status": "release_validation_fenced",
        "task_key": "release-async-test",
    }


def test_redis_worker_never_pops_stream_while_fenced(monkeypatch) -> None:
    class Queue:
        pop_count = 0

        async def pop_job(self, **_kwargs):
            self.pop_count += 1
            raise AssertionError("Redis stream read while fenced")

    queue = Queue()
    monkeypatch.setattr(worker_main, "release_validation_active", lambda: True)

    async def cancel_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(worker_main.asyncio, "sleep", cancel_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))
    assert queue.pop_count == 0


def test_redis_worker_rechecks_fence_after_blocking_pop(monkeypatch) -> None:
    class Queue:
        pop_count = 0

        async def pop_job(self, **_kwargs):
            self.pop_count += 1
            return {"task_id": "race-job", "job_type": "official_sync"}

    states = iter((False, True, True))
    queue = Queue()
    monkeypatch.setattr(
        worker_main,
        "release_validation_active",
        lambda: next(states, True),
    )
    monkeypatch.setattr(
        worker_main,
        "acquire_provider_execution_claim",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider claim ran after fence activation")
        ),
    )

    async def cancel_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(worker_main.asyncio, "sleep", cancel_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))
    assert queue.pop_count == 1


def test_apify_worker_rechecks_fence_after_database_claim(monkeypatch) -> None:
    requeued: list[tuple[int, str]] = []
    monkeypatch.setattr(apify_jobs_worker, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        apify_jobs_worker,
        "_requeue_job",
        lambda _conn, job_id, reason, **_kwargs: requeued.append((job_id, reason)),
    )
    monkeypatch.setattr(
        apify_jobs_worker,
        "acquire_provider_execution_claim",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider claim ran after fence activation")
        ),
    )

    assert apify_jobs_worker._execute_claimed_job(
        object(),
        {"id": 23, "lease_owner": "worker-a", "job_type": "kol_refresh"},
    ) == "queued"
    assert requeued == [
        (23, "release validation fence activated after database claim")
    ]


def test_provider_claim_boundary_closes_the_post_recheck_race(monkeypatch) -> None:
    monkeypatch.setattr(release_validation, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        apify_budget,
        "_ensure_reservation_schema",
        lambda: (_ for _ in ()).throw(AssertionError("provider claim touched the DB")),
    )

    with pytest.raises(apify_budget.ApifyExecutionClaimBlocked, match="release validation"):
        apify_budget.acquire_provider_execution_claim("task-1", "worker-1")


def test_provider_start_boundary_closes_the_post_claim_race(monkeypatch) -> None:
    monkeypatch.setattr(release_validation, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        apify_budget,
        "require_apify_budget",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("budget reservation wrote")),
    )

    with pytest.raises(apify_budget.ApifyExecutionClaimBlocked, match="release validation"):
        apify_budget.call_apify_actor(object(), "actor/test")
