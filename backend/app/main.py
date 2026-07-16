from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import ADMIN_ROUTER_MODULES
from app.api.routers import activities, auth, admin, audit, creator, jobs, leaderboard, media, ops, platform_ingest, sse, student_identity, uploads, verify, via, vkpi
# vkpi_kol_portal 在 admin 挂载区之外还有 portal_public_router(公开门户),需显式引用;
# 其余 vkpi_* 模块统一由 ADMIN_ROUTER_MODULES 注册表(routers/__init__.py)循环挂载。
from app.api.routers import vkpi_kol_portal
from app.api.routers import commerce, deepsight, insights, intelligence, intelligence_admin, system_admin
from app.core.config import (
    APP_ROLE,
    CORS_ORIGINS,
    ENABLE_BROWSER,
    ENABLE_LOCAL_ORCHESTRATOR,
    ENABLE_SCHEDULER,
    ENABLE_UPLOAD_CLEANUP,
    IS_PRODUCTION,
    MIGRATION_RUNNER_APP_ROLE,
    RESPONSE_GZIP_MIN_SIZE,
    UPLOAD_DIR,
)
from app.db.connection import (
    close_standalone_conn,
    close_db_runtime,
    db_connection_scope,
    db_connection_sync_scope,
    get_conn,
    get_db_startup_status,
    init_db_runtime,
    is_postgres_runtime,
    open_standalone_conn,
)
from app.services.jobs.queue import build_job_queue
from app.services.monitoring.runtime import record_request_metric
from app.services.security.admin_access import apply_admin_security_headers, get_admin_security_response
from app.services.via import build_via_event_bus
from app.core.logging import get_logger
from app.core.security import AUTH_COOKIE_NAME, get_current_user
from app.core.permissions import check_system_permission, check_tab_permission, staff_context_for_user
from app.main_health import build_deep_health_payload
from app.main_request_guards import (
    DB_REQUEST_ADMISSION_TIMEOUT_SEC as _DB_REQUEST_ADMISSION_TIMEOUT_SEC,
    PRIVATE_INTERNAL_UPLOAD_PREFIXES as _PRIVATE_INTERNAL_UPLOAD_PREFIXES,
    admin_permission_for_request as _admin_permission_for_request,
    db_admission_unavailable_response,
    db_request_admission_limiter as _db_request_admission_limiter,
    request_path_requires_db_admission,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
FRONTEND_DIST_DIR = FRONTEND_ROOT / "dist"
FRONTEND_INDEX = FRONTEND_DIST_DIR / "index.html"
FRONTEND_ASSETS_DIR = FRONTEND_DIST_DIR / "assets"
FRONTEND_BUILD_INFO = FRONTEND_DIST_DIR / "build-info.json"
PUBLIC_REWARD_UPLOAD_DIR = UPLOAD_DIR / "reward_images"
PUBLIC_STUDENT_CARD_DIR = UPLOAD_DIR / "student_cards"
PUBLIC_STAFF_AVATAR_DIR = UPLOAD_DIR / "staff_avatars"
PUBLIC_VKPI_EVIDENCE_DIR = UPLOAD_DIR / "vkpi_evidence"

PUBLIC_APP_ROLES = {"all", "web", "public-web"}
ADMIN_APP_ROLES = {"all", "web", "admin-web"}
IS_PUBLIC_APP = APP_ROLE in PUBLIC_APP_ROLES
IS_ADMIN_APP = APP_ROLE in ADMIN_APP_ROLES
logger = get_logger(__name__)

def _request_requires_db_admission(request) -> bool:
    return request_path_requires_db_admission(
        request,
        postgres_runtime=is_postgres_runtime(),
    )


def _db_admission_unavailable_response() -> JSONResponse:
    return db_admission_unavailable_response(
        _DB_REQUEST_ADMISSION_TIMEOUT_SEC,
    )


def _read_git_value(*args: str) -> str:
    try:
        return (
            subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, stderr=subprocess.DEVNULL, text=True)
            .strip()
        )
    except Exception:
        return ""


def _read_build_file(name: str) -> str:
    try:
        path = PROJECT_ROOT / name
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return ""


APP_VERSION = "2.0.0"
APP_GIT_SHA = os.getenv("APP_GIT_SHA", "").strip() or _read_build_file("BUILD_GIT_SHA") or _read_git_value("rev-parse", "HEAD")
APP_GIT_SHORT_SHA = (APP_GIT_SHA[:8] if APP_GIT_SHA else "unknown")
APP_GIT_BRANCH = os.getenv("APP_GIT_BRANCH", "").strip() or _read_build_file("BUILD_GIT_BRANCH") or _read_git_value("rev-parse", "--abbrev-ref", "HEAD")
APP_BUILD_TIME = (
    os.getenv("APP_BUILD_TIME", "").strip()
    or _read_build_file("BUILD_TIME")
    or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
)


def _read_frontend_build_sha() -> str:
    try:
        parsed = json.loads(FRONTEND_BUILD_INFO.read_text(encoding="utf-8"))
    except Exception:
        return ""
    value = parsed.get("gitSha") or parsed.get("git_sha") or ""
    return str(value or "").strip()


def _build_info(client_build: str = "") -> dict[str, str | bool]:
    explicit_client = str(client_build or "").strip()
    client = explicit_client or _read_frontend_build_sha()
    return {
        "version": APP_VERSION,
        "git_sha": APP_GIT_SHA or "unknown",
        "git_short_sha": APP_GIT_SHORT_SHA,
        "git_branch": APP_GIT_BRANCH or "unknown",
        "build_time": APP_BUILD_TIME,
        "client_build": client,
        "client_build_source": "query" if explicit_client else ("frontend_dist" if client else ""),
        "client_matches_server": bool(client and APP_GIT_SHA and client == APP_GIT_SHA),
    }


_WORKER_ONLINE_WINDOW_MIN = 10


def _worker_lane_from_name(name: str) -> str:
    normalized = str(name or "").strip().lower()
    if "-interactive-" in normalized or normalized.endswith("-interactive"):
        return "interactive"
    if "-bulk" in normalized:
        return "batch"
    return "all"


def _trust_db_migration_max() -> str | None:
    """Highest migration version_key ACTUALLY APPLIED on the DB (schema_migrations).

    Fail closed when the database cannot be queried.  Returning the code
    manifest tail after a DB failure would let deployment acceptance mistake
    "declared in source" for "applied to this database".
    """
    try:
        from app.db.connection import get_conn

        rows = get_conn().execute("SELECT version_key FROM schema_migrations").fetchall()
        applied = [str(dict(r).get("version_key") or "") for r in rows]
        applied = [a for a in applied if a]
        if applied:
            return max(applied)
    except Exception:
        logger.debug("health: applied migration read failed", exc_info=True)
    return None


def _trust_worker() -> dict[str, object | None]:
    """Return the live worker fleet trust contract used by ``/health``."""

    # Release identity contract fields remain source-visible for the migration
    # alignment gate: worker_git_sha, boot_nonce_sha256, started_at.
    from app.main_worker_trust import trust_worker_impl

    return trust_worker_impl(globals())


def _trust_scheduler() -> dict[str, int] | str:
    """Scheduler task counts if the (optional) scheduler_tasks table exists."""
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists("scheduler_tasks"):
            return "not_configured"
        conn = get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN enabled THEN 1 ELSE 0 END) AS enabled FROM scheduler_tasks"
        ).fetchone()
        if row is None:
            return "not_configured"
        total = int(row["total"] or 0)
        enabled = int(row["enabled"] or 0)
        return {"total": total, "enabled": enabled}
    except Exception:
        logger.debug("health: scheduler_tasks read failed", exc_info=True)
        return "not_configured"


def _trust_worker_sha() -> dict[str, str | None]:
    """Legacy test hook; production worker identity is DB-heartbeat bound.

    A filesystem stamp or the server build cannot prove which worker process is
    alive, so this compatibility helper deliberately fails closed.
    """
    return {
        "worker_sha": None,
        "worker_sha_source": "unavailable",
    }


def _runtime_trust() -> dict[str, object]:
    """Additive runtime-trust block for /health. Never raises; each field guarded."""
    trust: dict[str, object] = {}
    try:
        trust["db_startup"] = get_db_startup_status()
    except Exception:
        trust["db_startup"] = {"state": "unknown"}
    try:
        migration_max = _trust_db_migration_max()
        trust["db_migration_max"] = migration_max
        trust["db_migration_source"] = (
            "schema_migrations" if migration_max else "unavailable"
        )
    except Exception:
        trust["db_migration_max"] = None
        trust["db_migration_source"] = "unavailable"
    try:
        worker_trust = _trust_worker()
        trust.update(worker_trust)
    except Exception:
        trust["worker_heartbeat"] = None
        trust["worker_online"] = None
        trust["worker_sha"] = None
        trust["worker_sha_source"] = "unavailable"
        trust["worker_heartbeat_source"] = "unavailable"
    try:
        from app.workers.redis_worker_health import redis_worker_fleet_health

        trust["redis_worker_fleet"] = redis_worker_fleet_health(APP_GIT_SHA)
    except Exception:
        logger.debug("health: redis worker heartbeat read failed", exc_info=True)
        trust["redis_worker_fleet"] = {
            "online": False,
            "online_count": 0,
            "expected_count": None,
            "workers": [],
        }
    try:
        trust["scheduler_status"] = _trust_scheduler()
    except Exception:
        trust["scheduler_status"] = "not_configured"
    # Compatibility hook for isolated legacy tests only.  Production
    # _trust_worker always returns an explicit worker_sha key and therefore can
    # never fall back to the server build or a stale filesystem assumption.
    if "worker_sha" not in trust:
        try:
            trust.update(_trust_worker_sha())
        except Exception:
            trust["worker_sha"] = None
            trust["worker_sha_source"] = "unavailable"
    try:
        _server_sha = (APP_GIT_SHA or None)
        _client_sha = (_read_frontend_build_sha() or None)
        trust["server_git_sha"] = _server_sha
        trust["client_git_sha"] = _client_sha
        if _server_sha is None or _client_sha is None:
            trust["sha_aligned"] = None
        else:
            trust["sha_aligned"] = bool(_server_sha == _client_sha)
    except Exception:
        trust["server_git_sha"] = (APP_GIT_SHA or None)
        trust["client_git_sha"] = None
        trust["sha_aligned"] = None
    return trust


def _is_https_request(request) -> bool:
    if str(request.url.scheme).lower() == "https":
        return True
    return str(request.headers.get("x-forwarded-proto", "")).lower() == "https"


def _origin_from_url(raw: str) -> str:
    parsed = urlparse(str(raw or ""))
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _request_origin(request) -> str:
    host = str(request.headers.get("host") or "").strip()
    if not host:
        return ""
    scheme = "https" if _is_https_request(request) else str(request.url.scheme or "http")
    return f"{scheme}://{host}".rstrip("/")


def _is_allowed_csrf_origin(candidate: str, request) -> bool:
    origin = _origin_from_url(candidate)
    if not origin:
        return False
    if origin == _request_origin(request):
        return True
    normalized_allowed = {_origin_from_url(item) for item in CORS_ORIGINS if item and item != "*"}
    return origin in normalized_allowed


def _csrf_request_allowed(request) -> bool:
    path = str(request.url.path)
    if path.startswith("/api/platform-ingest/") or path.startswith("/api/vkpi/webhooks/"):
        return True
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    has_cookie = bool(request.cookies.get(AUTH_COOKIE_NAME))
    auth_header = str(request.headers.get("authorization") or "").strip().lower()
    if not has_cookie and auth_header.startswith("bearer "):
        return True
    origin = str(request.headers.get("origin") or "").strip()
    referer = str(request.headers.get("referer") or "").strip()
    if origin and _is_allowed_csrf_origin(origin, request):
        return True
    if referer and _is_allowed_csrf_origin(referer, request):
        return True
    if not origin and not referer and str(request.headers.get("x-requested-with") or "").lower() == "xmlhttprequest":
        return True
    logger.warning(
        "csrf.blocked",
        extra={
            "origin": origin,
            "referer": referer,
            "path": str(request.url.path),
            "ip": str(getattr(request.client, "host", "") or ""),
        },
    )
    return False


def _can_read_deep_health(request) -> bool:
    ops_token = str(os.getenv("OPS_HEALTH_TOKEN", "") or "").strip()
    if ops_token and str(request.headers.get("x-ops-token") or "").strip() == ops_token:
        return True
    try:
        user = get_current_user(request)
        return bool(user and str(user.get("role") or "").lower() == "admin")
    except Exception:
        return False


def _admin_rbac_allowed(request) -> bool:
    path = str(request.url.path)
    if not (
        path.startswith("/api/admin")
        or path.startswith("/api/marketing")
        or path.startswith("/api/intelligence")
        or path.startswith("/api/vios")
        or path.startswith("/api/verify/")
        or path.startswith(_PRIVATE_INTERNAL_UPLOAD_PREFIXES)
    ):
        return True
    requirement = _admin_permission_for_request(path, request.method)
    if requirement is None:
        return True
    permission_key, level, is_system = requirement
    # Browser EventSource subscriptions are GET requests and consume a
    # short-lived path-bound ticket from an HttpOnly cookie.  A POST endpoint
    # may also return ``text/event-stream`` (for example the Marketing Advisor
    # staged response) while still using the normal Authorization header.  Do
    # not classify those mutation requests as EventSource subscriptions merely
    # because their path ends in ``/stream``; doing so rejected a valid bearer
    # request before routing and left the UI waiting for an SSE response that
    # could never arrive.
    if request.method.upper() == "GET" and path.endswith("/stream"):
        from app.core.security import get_current_user_stream

        user = get_current_user_stream(request)
    else:
        user = get_current_user(request)
    if not user:
        return False
    staff = staff_context_for_user(user)
    # The route permission dependency and post-response audit need the exact
    # same staff projection.  Reusing it avoids two more synchronous Postgres
    # lookups per request and, critically, prevents an async dependency from
    # waiting for the fifth pool lease on the event-loop thread.
    request.state.vkpi_authorized_staff = staff
    if is_system:
        return check_system_permission(staff, permission_key, level)
    return check_tab_permission(staff, permission_key, level)


def _admin_rbac_allowed_bounded(request) -> bool:
    """Authorize without retaining a pool lease for the whole request.

    ``db_scope_middleware`` is outside this middleware in Starlette's runtime
    stack.  If RBAC uses that outer handle, a cold dashboard fan-out lets the
    first requests keep every pool connection while the remaining RBAC worker
    threads wait for one.  Give authentication its own lazy, bounded scope so
    the lease is returned before routing continues.
    """
    with db_connection_sync_scope():
        return _admin_rbac_allowed(request)


def _audit_sensitive_request(request, status_code: int) -> None:
    """Run the existing sensitive-request audit outside the event loop."""
    from app.domains import audit as vkpi_audit

    user = get_current_user(request)
    staff = getattr(request.state, "vkpi_authorized_staff", None)
    if staff is None and user:
        staff = staff_context_for_user(user)
    vkpi_audit.log_request_if_sensitive(request, status_code, staff=staff)


def _host_without_port(raw: str) -> str:
    host = str(raw or "").strip().lower()
    if not host:
        return ""
    if host.startswith("[") and "]" in host:
        return host[1:host.index("]")]
    return host.split(":", 1)[0]


def _is_local_host(raw: str) -> bool:
    host = _host_without_port(raw)
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")


def _request_uses_public_host(request) -> bool:
    return bool(request.headers.get("host")) and not _is_local_host(str(request.headers.get("host") or ""))


def _build_csp_value(*, include_dev_connect: bool | None = None) -> str:
    connect_src = ["'self'"]
    if include_dev_connect is None:
        include_dev_connect = not IS_PRODUCTION
    if include_dev_connect:
        connect_src.extend(
            [
                "http://127.0.0.1:5173",
                "http://localhost:5173",
                "http://127.0.0.1:8000",
                "http://localhost:8000",
                "ws://127.0.0.1:5173",
                "ws://localhost:5173",
            ]
        )
    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "frame-ancestors 'none'",
            "object-src 'none'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob: https:",
            "font-src 'self' data: https:",
            "media-src 'self' data: blob: https:",
            "frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com",
            "child-src 'self' https://www.youtube.com https://www.youtube-nocookie.com",
            f"connect-src {' '.join(connect_src)}",
        ]
    )


GLOBAL_CSP_VALUE = _build_csp_value()


def _csp_value_for_request(request) -> str:
    # Test deployments may intentionally keep ENVIRONMENT=local while still
    # serving a public hostname. Never expose local dev origins on public hosts.
    return _build_csp_value(include_dev_connect=(not IS_PRODUCTION and not _request_uses_public_host(request)))


def _should_send_hsts(request) -> bool:
    return _request_uses_public_host(request)

def _serve_frontend():
    if FRONTEND_INDEX.exists():
        response = FileResponse(FRONTEND_INDEX)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-VKPI-Build-SHA"] = APP_GIT_SHORT_SHA
        return response
    raise HTTPException(status_code=404, detail="Frontend build not found")


def _serve_frontend_public_file(filename: str):
    path = FRONTEND_DIST_DIR / filename
    if path.is_file():
        response = FileResponse(path)
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response
    raise HTTPException(status_code=404, detail="Frontend public file not found")


def _collect_referenced_uploads() -> set[str]:
    refs: set[str] = set()
    conn = None
    try:
        conn = open_standalone_conn()
        rows = conn.execute(
            "SELECT video_path FROM submissions WHERE video_path IS NOT NULL AND video_path != ''",
        ).fetchall()
        for row in rows:
            path = str(row[0] or "").strip()
            if path:
                refs.add(path)
                refs.add(Path(path).name)
    except Exception:
        return refs
    finally:
        close_standalone_conn(conn)
    return refs


def _cleanup_uploads(max_age_hours: int = 48):
    cutoff = time.time() - (max_age_hours * 3600)
    referenced = _collect_referenced_uploads()
    for upload in UPLOAD_DIR.iterdir():
        if not upload.is_file():
            continue
        try:
            if upload.stat().st_mtime >= cutoff:
                continue
            if str(upload.resolve()) in referenced or upload.name in referenced:
                continue
            upload.unlink()
        except Exception:
            continue


async def _cleanup_loop():
    while True:
        try:
            await asyncio.sleep(24 * 3600)
            await asyncio.to_thread(_cleanup_uploads)
        except asyncio.CancelledError:
            break
        except Exception:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    if APP_ROLE == MIGRATION_RUNNER_APP_ROLE:
        raise RuntimeError(
            "APP_ROLE='migration-runner' is a one-shot database role and cannot serve web traffic"
        )
    await init_db_runtime()
    app.state.orchestrator = None
    app.state.job_queue = None
    app.state.via_event_bus = build_via_event_bus()
    cleanup_task = None

    if ENABLE_LOCAL_ORCHESTRATOR:
        from app.services.ai.analyzers.claude_text import analyze_text_content
        from app.services.ai.analyzers.gemini_video import analyze_youtube_with_gemini
        from app.services.ai.analyzers.gpt_prefilter import gpt_prefilter_caption
        from app.services.ai.orchestrator import build_orchestrator
        from app.services.scoring.benchmark import update_genre_benchmark
        from app.services.scoring.core import compute_weighted_scores, get_vertical
        from app.services.scoring.verticals import apply_learned_weights

        if ENABLE_BROWSER:
            from app.services.scraping.playwright_scraper import _start_browser

            await _start_browser()

        app.state.orchestrator = build_orchestrator(
            gemini_fn=analyze_youtube_with_gemini,
            gpt_fn=gpt_prefilter_caption,
            claude_fn=analyze_text_content,
            get_conn_fn=get_conn,
            compute_weighted_fn=compute_weighted_scores,
            update_benchmark_fn=update_genre_benchmark,
            get_vertical_fn=get_vertical,
            apply_learned_weights_fn=apply_learned_weights,
        )
        await app.state.orchestrator.start()

    app.state.job_queue = build_job_queue(app.state.orchestrator)

    if ENABLE_SCHEDULER:
        from app.services.scheduler import start_scheduler

        await start_scheduler()

    if ENABLE_UPLOAD_CLEANUP:
        cleanup_task = asyncio.create_task(_cleanup_loop())

    yield

    if cleanup_task is not None:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)

    if ENABLE_SCHEDULER:
        from app.services.scheduler import stop_scheduler

        await stop_scheduler()

    if app.state.job_queue is not None:
        await app.state.job_queue.close()
    if getattr(app.state, "via_event_bus", None) is not None:
        await app.state.via_event_bus.close()
    if app.state.orchestrator is not None:
        await app.state.orchestrator.stop()
    if ENABLE_LOCAL_ORCHESTRATOR and ENABLE_BROWSER:
        from app.services.scraping.playwright_scraper import _stop_browser

        await _stop_browser()
    await close_db_runtime()


_ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

app = FastAPI(
    title="Viltrox Workspace",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _ENABLE_API_DOCS else None,
    redoc_url="/redoc" if _ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if _ENABLE_API_DOCS else None,
)

sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
if sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
        environment=os.getenv("ENVIRONMENT", "local"),
        release="viltrox-2.0",
    )

app.add_middleware(
    CORSMiddleware,
    # T6 安全:allow_credentials=True 下 '*' 既违规又是凭据泄漏面;剥掉任何混入的通配,
    # 强制显式 origin(与 _is_allowed_csrf_origin 的 '!= "*"' 口径一致)。
    allow_origins=[o for o in CORS_ORIGINS if o and o != "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-CSRF-Token"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Bucket"],
)
app.add_middleware(GZipMiddleware, minimum_size=RESPONSE_GZIP_MIN_SIZE)


@app.middleware("http")
async def csrf_origin_middleware(request, call_next):
    if not _csrf_request_allowed(request):
        return JSONResponse({"detail": "CSRF check failed"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def admin_rbac_middleware(request, call_next):
    # RBAC performs synchronous Postgres reads.  A request waiting for the next
    # pool lease must not block the event loop: under a burst larger than the
    # pool, that prevented completed requests from resuming and releasing their
    # own leases (pool-size + 1 became a deterministic deadlock).
    try:
        allowed = await asyncio.to_thread(_admin_rbac_allowed_bounded, request)
    except Exception as exc:
        # Fail closed, but surface pool/database admission pressure as a
        # retryable service condition instead of leaking an ASGI 500.
        logger.warning("db.rbac_admission_failed: %s", exc)
        return _db_admission_unavailable_response()
    if not allowed:
        logger.warning(
            "admin_rbac.blocked",
            extra={
                "path": str(request.url.path),
                "method": str(request.method),
                "ip": str(getattr(request.client, "host", "") or ""),
            },
        )
        return JSONResponse({"detail": "当前账号没有 Viltrox Marketing 权限"}, status_code=403)
    if _request_requires_db_admission(request):
        try:
            # RBAC's bounded lease has already been returned.  Prime the outer
            # request handle off-loop before entering sync-heavy route code, so
            # a fifth get_conn() can never block the sole event-loop thread.
            await asyncio.to_thread(get_conn)
        except Exception as exc:
            logger.warning("db.request_prime_failed: %s", exc)
            return _db_admission_unavailable_response()
    return await call_next(request)


@app.middleware("http")
async def db_scope_middleware(request, call_next):
    limiter = None
    acquired = False
    if _request_requires_db_admission(request):
        limiter = _db_request_admission_limiter()
        try:
            await asyncio.wait_for(
                limiter.acquire(),
                timeout=_DB_REQUEST_ADMISSION_TIMEOUT_SEC,
            )
            acquired = True
        except TimeoutError:
            return _db_admission_unavailable_response()

    try:
        async with db_connection_scope():
            response = await call_next(request)
            try:
                # Staff projection is synchronous Postgres work.  Running it on the
                # event loop can freeze even DB-free endpoints such as /health when
                # a cold dashboard burst is already using every pool connection.
                await asyncio.to_thread(_audit_sensitive_request, request, response.status_code)
            except Exception as exc:
                logger.debug("vkpi sensitive request audit skipped: %s", exc)
            return response
    finally:
        # Covers normal completion, route exceptions and client cancellation.
        if acquired and limiter is not None:
            limiter.release()


@app.middleware("http")
async def admin_security_middleware(request, call_next):
    blocked = get_admin_security_response(request)
    if blocked is not None:
        return apply_admin_security_headers(request.url.path, blocked)
    response = await call_next(request)
    return apply_admin_security_headers(request.url.path, response)


@app.middleware("http")
async def request_metrics_middleware(request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - started) * 1000
        record_request_metric(request.url.path, request.method, 500, duration_ms)
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    record_request_metric(request.url.path, request.method, response.status_code, duration_ms)
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
    rate_limit_headers = getattr(request.state, "rate_limit_headers", None)
    if isinstance(rate_limit_headers, dict):
        for key, value in rate_limit_headers.items():
            response.headers[str(key)] = str(value)
    return response


@app.middleware("http")
async def security_headers_middleware(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault(
        "X-Robots-Tag",
        "noindex, nofollow, noarchive, nosnippet, noimageindex",
    )
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), browsing-topics=()",
    )
    response.headers.setdefault("Content-Security-Policy", _csp_value_for_request(request))
    if _should_send_hsts(request):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.middleware("http")
async def marketing_api_alias_middleware(request, call_next):
    path = str(request.scope.get("path") or "")
    if path == "/api/marketing" or path.startswith("/api/marketing/"):
        request.scope["path"] = "/api/admin/vkpi" + path.removeprefix("/api/marketing")
    return await call_next(request)


PUBLIC_REWARD_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_STUDENT_CARD_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_STAFF_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_VKPI_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads/reward_images", StaticFiles(directory=PUBLIC_REWARD_UPLOAD_DIR), name="reward-images")
app.mount("/uploads/student_cards", StaticFiles(directory=PUBLIC_STUDENT_CARD_DIR), name="student-cards")
app.mount("/uploads/staff_avatars", StaticFiles(directory=PUBLIC_STAFF_AVATAR_DIR), name="staff-avatars")
app.mount("/uploads/vkpi_evidence", StaticFiles(directory=PUBLIC_VKPI_EVIDENCE_DIR), name="vkpi-evidence")
if FRONTEND_ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS_DIR), name="frontend-assets")

app.include_router(auth.router)
app.include_router(activities.public_router)
app.include_router(vkpi.public_router)
app.include_router(vkpi.webhook_router)
app.include_router(vkpi_kol_portal.portal_public_router)
app.include_router(student_identity.router)
app.include_router(uploads.router)
app.include_router(platform_ingest.router)
app.include_router(media.router)
app.include_router(via.router)
app.include_router(verify.router)
app.include_router(jobs.router)
if IS_PUBLIC_APP:
    app.include_router(creator.router)
    app.include_router(audit.router)
    app.include_router(sse.router)
    app.include_router(leaderboard.router)
if IS_ADMIN_APP:
    from app.api.routers import account_scanner, brand_analysis, kol_ops

    app.include_router(admin.router)
    app.include_router(commerce.router)
    app.include_router(insights.router)
    app.include_router(activities.router)
    app.include_router(intelligence.router)
    app.include_router(intelligence_admin.router)
    app.include_router(deepsight.router)
    app.include_router(brand_analysis.router)
    app.include_router(account_scanner.router)
    app.include_router(kol_ops.router)
    # P2:vkpi.router 为零路由空壳(vkpi.py 仅 public_router/webhook_router 挂路由),不再 include。
    # F2 工程税①:vkpi_* 挂载区收敛为注册表循环。顺序=ADMIN_ROUTER_MODULES
    # (app/api/routers/__init__.py)即历史手写顺序;改造验收=前后 app.routes
    # (type, path, methods, name) 有序全量对比逐字一致。
    for _module_name in ADMIN_ROUTER_MODULES:
        _module = importlib.import_module(f"app.api.routers.{_module_name}")
        app.include_router(_module.router)
    app.include_router(ops.router)
    app.include_router(system_admin.router)


@app.get("/health")
async def health_check(request: Request, deep: bool = False):
    build = _build_info(str(request.query_params.get("client_build", "") or ""))
    try:
        # Runtime trust performs synchronous Postgres/Redis heartbeat reads.
        # A saturated small DB pool must never make that work block the sole
        # asyncio event loop, otherwise in-flight requests cannot finish and
        # return the very leases /health is waiting for.
        trust = await asyncio.to_thread(_runtime_trust)
    except Exception:
        logger.debug("health: runtime_trust block failed", exc_info=True)
        trust = {}
    if not deep and (not IS_PRODUCTION or _can_read_deep_health(request)):
        return {
            "status": "ok",
            "service": APP_ROLE,
            "version": APP_VERSION,
            "build": build,
            "trust": trust,
        }
    if not deep:
        return {"status": "ok", "service": APP_ROLE, "version": APP_VERSION}
    if not _can_read_deep_health(request):
        raise HTTPException(status_code=403, detail="Deep health requires admin or ops token")
    return await build_deep_health_payload(
        app,
        app_version=APP_VERSION,
        build=build,
        is_production=IS_PRODUCTION,
        is_public_app=IS_PUBLIC_APP,
        is_admin_app=IS_ADMIN_APP,
    )


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    response = PlainTextResponse(
        "User-agent: *\n"
        "Disallow: /\n"
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.get("/favicon.svg", include_in_schema=False)
def frontend_favicon_svg():
    return _serve_frontend_public_file("favicon.svg")


@app.get("/favicon.ico", include_in_schema=False)
def frontend_favicon_ico():
    return _serve_frontend_public_file("favicon.ico")


@app.get("/")
def read_index():
    if IS_ADMIN_APP and not IS_PUBLIC_APP:
        return RedirectResponse("/admin", status_code=307)
    if not IS_PUBLIC_APP:
        raise HTTPException(status_code=404, detail="Public surface is disabled on this instance")
    return _serve_frontend()


@app.get("/admin")
def read_admin():
    if not IS_ADMIN_APP:
        raise HTTPException(status_code=404, detail="Admin surface is disabled on this instance")
    return _serve_frontend()


@app.get("/admin/{admin_path:path}")
def read_admin_nested(admin_path: str):
    if not IS_ADMIN_APP:
        raise HTTPException(status_code=404, detail="Admin surface is disabled on this instance")
    return _serve_frontend()


@app.get("/account")
def read_account():
    if not IS_PUBLIC_APP:
        raise HTTPException(status_code=404, detail="Account surface is disabled on this instance")
    return _serve_frontend()


@app.get("/account/{account_path:path}")
def read_account_nested(account_path: str):
    if not IS_PUBLIC_APP:
        raise HTTPException(status_code=404, detail="Account surface is disabled on this instance")
    return _serve_frontend()


@app.get("/redeem")
def read_redeem():
    if not IS_PUBLIC_APP:
        raise HTTPException(status_code=404, detail="Rewards surface is disabled on this instance")
    return _serve_frontend()


@app.get("/redeem/{redeem_path:path}")
def read_redeem_nested(redeem_path: str):
    if not IS_PUBLIC_APP:
        raise HTTPException(status_code=404, detail="Rewards surface is disabled on this instance")
    return _serve_frontend()


@app.get("/login")
def read_login():
    if not IS_PUBLIC_APP:
        raise HTTPException(status_code=404, detail="Public surface is disabled on this instance")
    return _serve_frontend()


@app.get("/activate")
def read_activate():
    if not IS_PUBLIC_APP:
        raise HTTPException(status_code=404, detail="Public surface is disabled on this instance")
    return _serve_frontend()


@app.get("/student-signup")
def read_student_signup():
    if not IS_PUBLIC_APP:
        raise HTTPException(status_code=404, detail="Public surface is disabled on this instance")
    return _serve_frontend()


@app.get("/vid/{vid_path:path}")
def read_public_vid(vid_path: str):
    if not IS_PUBLIC_APP:
        raise HTTPException(status_code=404, detail="Public VID surface is disabled on this instance")
    return _serve_frontend()


@app.get("/react")
def read_react_redirect():
    return RedirectResponse("/", status_code=307)


@app.get("/react/{react_path:path}")
def read_react_path(react_path: str):
    return RedirectResponse("/", status_code=307)
