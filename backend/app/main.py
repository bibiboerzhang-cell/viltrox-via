from __future__ import annotations

import asyncio
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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import activities, auth, admin, audit, creator, jobs, leaderboard, media, ops, platform_ingest, sse, student_identity, uploads, verify, via, vkpi, vkpi_attribution_metrics, vkpi_comment_intelligence, vkpi_audit, vkpi_comments, vkpi_costs, vkpi_dashboard_staff, vkpi_data_quality, vkpi_evidence_assets, vkpi_firewall, vkpi_industry_automation, vkpi_kol_links, vkpi_kol_pool, vkpi_operations, vkpi_pillars, vkpi_product_analysis, vkpi_projects, vkpi_reconciliation, vkpi_reports, vkpi_settings, vkpi_sentiment, vkpi_sync, vkpi_weekly_reports, vkpi_workflow_assets
from app.api.routers import commerce, deepsight, insights, intelligence, intelligence_admin, system_admin
from app.core.config import (
    APP_ROLE,
    CORS_ORIGINS,
    DATABASE_URL,
    DB_RUNTIME_BACKEND,
    DB_TARGET_BACKEND,
    ENABLE_BROWSER,
    ENABLE_LOCAL_ORCHESTRATOR,
    ENABLE_SCHEDULER,
    ENABLE_UPLOAD_CLEANUP,
    IS_PRODUCTION,
    PLATFORM_INGEST_SOURCES,
    RESPONSE_GZIP_MIN_SIZE,
    UPLOAD_DIR,
    USE_REDIS_JOBS,
    WORKER_ASYNC_CONSUMERS,
    WORKER_CLUSTER_TIER,
    WORKER_CONFIGURED_CONCURRENCY,
    WORKER_SERVICE_PROCESSES,
)
from app.db.connection import (
    close_standalone_conn,
    close_db_runtime,
    db_connection_scope,
    get_conn,
    get_db_actor_stats,
    init_db_runtime,
    open_standalone_conn,
    probe_postgres_connectivity,
)
from app.services.jobs.queue import build_job_queue
from app.services.monitoring.runtime import record_request_metric
from app.services.security.admin_access import apply_admin_security_headers, get_admin_security_response
from app.services.via import build_via_event_bus
from app.core.logging import get_logger
from app.core.security import AUTH_COOKIE_NAME, get_current_user
from app.core.permissions import check_system_permission, check_tab_permission, staff_context_for_user

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
FRONTEND_DIST_DIR = FRONTEND_ROOT / "dist"
FRONTEND_INDEX = FRONTEND_DIST_DIR / "index.html"
FRONTEND_ASSETS_DIR = FRONTEND_DIST_DIR / "assets"
PUBLIC_REWARD_UPLOAD_DIR = UPLOAD_DIR / "reward_images"
PUBLIC_STUDENT_CARD_DIR = UPLOAD_DIR / "student_cards"
PUBLIC_STAFF_AVATAR_DIR = UPLOAD_DIR / "staff_avatars"
PUBLIC_VKPI_EVIDENCE_DIR = UPLOAD_DIR / "vkpi_evidence"

PUBLIC_APP_ROLES = {"all", "web", "public-web"}
ADMIN_APP_ROLES = {"all", "web", "admin-web"}
IS_PUBLIC_APP = APP_ROLE in PUBLIC_APP_ROLES
IS_ADMIN_APP = APP_ROLE in ADMIN_APP_ROLES
logger = get_logger(__name__)


def _read_git_value(*args: str) -> str:
    try:
        return (
            subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, stderr=subprocess.DEVNULL, text=True)
            .strip()
        )
    except Exception:
        return ""


APP_VERSION = "2.0.0"
APP_GIT_SHA = os.getenv("APP_GIT_SHA", "").strip() or _read_git_value("rev-parse", "HEAD")
APP_GIT_SHORT_SHA = (APP_GIT_SHA[:8] if APP_GIT_SHA else "unknown")
APP_GIT_BRANCH = os.getenv("APP_GIT_BRANCH", "").strip() or _read_git_value("rev-parse", "--abbrev-ref", "HEAD")
APP_BUILD_TIME = (
    os.getenv("APP_BUILD_TIME", "").strip()
    or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
)


def _build_info(client_build: str = "") -> dict[str, str | bool]:
    client = str(client_build or "").strip()
    return {
        "version": APP_VERSION,
        "git_sha": APP_GIT_SHA or "unknown",
        "git_short_sha": APP_GIT_SHORT_SHA,
        "git_branch": APP_GIT_BRANCH or "unknown",
        "build_time": APP_BUILD_TIME,
        "client_build": client,
        "client_matches_server": bool(client and APP_GIT_SHA and client == APP_GIT_SHA),
    }


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


def _admin_permission_for_request(path: str, method: str) -> tuple[str, str, bool] | None:
    if method.upper() == "OPTIONS":
        return None
    if path == "/api/admin/staff/accept-invite":
        return None
    mutating = method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
    level = "write" if mutating else "read"
    if path.startswith("/api/admin/system/keys"):
        return ("system.api_keys", "write", True)
    if path.startswith("/api/admin/system/restart"):
        return ("system.restart", "write", True)
    if path.startswith("/api/admin/system/providers"):
        return ("system.api_keys", "read", True)
    if path.startswith("/api/admin/system/models"):
        return ("system.models", level, True)
    if path.startswith("/api/admin/staff/api-tokens"):
        return ("system.api_keys", level, True)
    if path.startswith("/api/admin/staff"):
        if mutating:
            return ("system.members", "write", True)
        return ("system", "read", False)
    if path.startswith("/api/admin/runtime") or path.startswith("/api/admin/integrations"):
        return ("runtime", level, False)
    if path.startswith("/api/admin/trust"):
        return ("command", level, False)
    if path.startswith("/api/admin/kol"):
        return ("kol_ops", level, False)
    if path.startswith("/api/admin/deepsight"):
        return ("deepsight", level, False)
    if path.startswith("/api/admin/activities") or path.startswith("/api/public/event"):
        return ("activities", level, False)
    if path.startswith("/api/admin/vkpi") or path.startswith("/api/marketing"):
        return ("vkpi", level, False)
    if path.startswith("/api/admin/insights/"):
        return ("insights", level, False)
    if path.startswith("/api/admin/intel/student"):
        return ("student", level, False)
    if path.startswith("/api/admin/intel/via"):
        return ("via", level, False)
    if path.startswith("/api/admin/intel/system"):
        return ("runtime", level, False)
    if path.startswith("/api/intelligence/market") or path.startswith("/api/intelligence/brand"):
        return ("analytics", level, False)
    if path.startswith("/api/admin/intel") or path.startswith("/api/intelligence"):
        return ("intelligence", level, False)
    if path.startswith("/api/admin/analytics") or path.startswith("/api/admin/benchmarks") or path.startswith("/api/admin/learning"):
        return ("analytics", level, False)
    if path.startswith("/api/admin/orders") or path.startswith("/api/admin/payouts") or path.startswith("/api/admin/attribution") or path.startswith("/api/admin/webhook-events") or path.startswith("/api/admin/affiliate"):
        return ("operations", level, False)
    if path.startswith("/api/admin/rewards") or path.startswith("/api/admin/product_catalog") or path.startswith("/api/admin/creator-public/shop-heroes") or path.startswith("/api/admin/upload/reward-image"):
        return ("products", level, False)
    if path.startswith("/api/admin/creator") or path.startswith("/api/admin/creators"):
        return ("creators", level, False)
    if path.startswith("/api/admin/users/") and (
        path.endswith("/block")
        or path.endswith("/unblock")
        or path.endswith("/flag")
        or path.endswith("/clear-flag")
        or path.endswith("/adjust-score")
    ):
        return ("command", level, False)
    if path.startswith("/api/admin/users") or path.startswith("/api/admin/social-accounts") or path.startswith("/api/admin/verifications") or path.startswith("/api/admin/submissions") or path.startswith("/api/admin/approve") or path.startswith("/api/admin/reject") or path.startswith("/api/admin/reanalyze") or path.startswith("/api/admin/redemptions") or path.startswith("/api/verify/queue") or path.startswith("/api/verify/admin") or path.endswith("/scan") or path.endswith("/approve") or path.endswith("/reject"):
        return ("operations", level, False)
    if path.startswith("/api/admin/student") or path.startswith("/api/student/admin"):
        return ("student", level, False)
    if path.startswith("/api/vios"):
        return ("analytics", level, False)
    if path.startswith("/api/admin"):
        return ("overview", level, False)
    return None


def _admin_rbac_allowed(request) -> bool:
    path = str(request.url.path)
    if not (path.startswith("/api/admin") or path.startswith("/api/marketing") or path.startswith("/api/intelligence") or path.startswith("/api/vios") or path.startswith("/api/verify/")):
        return True
    requirement = _admin_permission_for_request(path, request.method)
    if requirement is None:
        return True
    permission_key, level, is_system = requirement
    user = get_current_user(request)
    if not user:
        return False
    staff = staff_context_for_user(user)
    if is_system:
        return check_system_permission(staff, permission_key, level)
    return check_tab_permission(staff, permission_key, level)


def _build_csp_value() -> str:
    connect_src = ["'self'"]
    if not IS_PRODUCTION:
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
            f"connect-src {' '.join(connect_src)}",
        ]
    )


GLOBAL_CSP_VALUE = _build_csp_value()

def _serve_frontend():
    if FRONTEND_INDEX.exists():
        response = FileResponse(FRONTEND_INDEX)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-VKPI-Build-SHA"] = APP_GIT_SHORT_SHA
        return response
    raise HTTPException(status_code=404, detail="Frontend build not found")


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


app = FastAPI(title="Viltrox Marketing", version="2.0.0", lifespan=lifespan)

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
    allow_origins=CORS_ORIGINS,
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
    if not _admin_rbac_allowed(request):
        logger.warning(
            "admin_rbac.blocked",
            extra={
                "path": str(request.url.path),
                "method": str(request.method),
                "ip": str(getattr(request.client, "host", "") or ""),
            },
        )
        return JSONResponse({"detail": "当前账号没有 Viltrox Marketing 权限"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def db_scope_middleware(request, call_next):
    async with db_connection_scope():
        response = await call_next(request)
        try:
            from app.services.vkpi import audit as vkpi_audit

            user = get_current_user(request)
            staff = staff_context_for_user(user) if user else None
            vkpi_audit.log_request_if_sensitive(request, response.status_code, staff=staff)
        except Exception:
            pass
        return response


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
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), browsing-topics=()",
    )
    response.headers.setdefault("Content-Security-Policy", GLOBAL_CSP_VALUE)
    if IS_PRODUCTION and _is_https_request(request):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")
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
    app.include_router(vkpi.router)
    app.include_router(vkpi_dashboard_staff.router)
    app.include_router(vkpi_attribution_metrics.router)
    app.include_router(vkpi_audit.router)
    app.include_router(vkpi_comments.router)
    app.include_router(vkpi_comment_intelligence.router)
    app.include_router(vkpi_costs.router)
    app.include_router(vkpi_data_quality.router)
    app.include_router(vkpi_evidence_assets.router)
    app.include_router(vkpi_firewall.router)
    app.include_router(vkpi_industry_automation.router)
    app.include_router(vkpi_kol_links.router)
    app.include_router(vkpi_kol_pool.router)
    app.include_router(vkpi_operations.router)
    app.include_router(vkpi_pillars.router)
    app.include_router(vkpi_product_analysis.router)
    app.include_router(vkpi_projects.router)
    app.include_router(vkpi_reconciliation.router)
    app.include_router(vkpi_reports.router)
    app.include_router(vkpi_settings.router)
    app.include_router(vkpi_sentiment.router)
    app.include_router(vkpi_sync.router)
    app.include_router(vkpi_weekly_reports.router)
    app.include_router(ops.router)
    app.include_router(system_admin.router)


@app.get("/health")
async def health_check(request: Request, deep: bool = False):
    build = _build_info(str(request.query_params.get("client_build", "") or ""))
    if not deep:
        return {"status": "ok", "service": APP_ROLE, "version": APP_VERSION, "build": build}
    if not _can_read_deep_health(request):
        raise HTTPException(status_code=403, detail="Deep health requires admin or ops token")
    queue_backend = getattr(getattr(app.state, "job_queue", None), "backend_name", "none")
    via_backend = getattr(getattr(app.state, "via_event_bus", None), "backend_name", "none")
    queue = getattr(app.state, "job_queue", None)
    via_event_bus = getattr(app.state, "via_event_bus", None)
    db_runtime = get_db_actor_stats()
    postgres = {
        "configured": bool(DATABASE_URL),
        "runtime_selected": DB_RUNTIME_BACKEND == "postgres",
        "pool_open": bool(db_runtime.get("running")),
        "ok": bool(db_runtime.get("running")) if DB_RUNTIME_BACKEND == "postgres" else True,
    }
    queue_stats = {
        "backend": queue_backend,
        "configured_concurrency": WORKER_CONFIGURED_CONCURRENCY,
        "worker_processes": WORKER_SERVICE_PROCESSES,
        "worker_async_consumers": WORKER_ASYNC_CONSUMERS,
    }
    via_stats = {"backend": via_backend}
    if deep:
        try:
            queue_stats = await queue.runtime_stats() if queue is not None else {"backend": "none"}
        except Exception as exc:
            queue_stats = {"backend": queue_backend, "ok": False, "error": str(exc)[:160]}
        try:
            via_stats = await via_event_bus.runtime_stats() if via_event_bus is not None else {"backend": "none"}
        except Exception as exc:
            via_stats = {"backend": via_backend, "ok": False, "error": str(exc)[:160]}
        postgres = await asyncio.to_thread(probe_postgres_connectivity)
    return {
        "status": "ok",
        "version": APP_VERSION,
        "build": build,
        "deep": deep,
        "app_role": APP_ROLE,
        "production_mode": IS_PRODUCTION,
        "surfaces": {
            "public_web": IS_PUBLIC_APP,
            "admin_web": IS_ADMIN_APP,
        },
        "database": {
            "database_backend": DB_RUNTIME_BACKEND,
            "target_backend": DB_TARGET_BACKEND,
            "postgres_configured": bool(DATABASE_URL),
            "pool_health": postgres,
            "runtime": db_runtime,
        },
        "ingestion_sources": PLATFORM_INGEST_SOURCES,
        "queue_backend": queue_backend,
        "queue": queue_stats,
        "via_event_backend": via_backend,
        "via": via_stats,
        "redis_jobs": USE_REDIS_JOBS,
        "local_orchestrator": ENABLE_LOCAL_ORCHESTRATOR,
        "worker_role": {
            "identity": APP_ROLE,
            "separated": APP_ROLE == "worker",
            "cluster_tier": WORKER_CLUSTER_TIER,
            "processes": WORKER_SERVICE_PROCESSES,
            "async_consumers": WORKER_ASYNC_CONSUMERS,
            "configured_concurrency": WORKER_CONFIGURED_CONCURRENCY,
        },
    }


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
