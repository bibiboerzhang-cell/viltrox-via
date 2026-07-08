"""Real-Postgres smoke test: every GET endpoint must never return HTTP 500.

The bulk of this suite fakes the database with in-memory sqlite, so the
sqlite->postgres compat/dialect layer only crashes for real when a handler's
SQL runs against a live Postgres. This test enumerates *every* GET route on the
production FastAPI app (``app.main.app``), authenticates as the owner (uid=1),
and requests each one against the real database. The invariant:

    an unhandled exception -- including the ``strftime``/``datetime``/``ROUND``/
    ``INTERVAL`` dialect crashes and missing-column errors this file exists to
    catch -- surfaces as an HTTP 500 via Starlette's ``ServerErrorMiddleware``
    (``raise_server_exceptions=False`` turns the re-raise into a 500 response).

Deliberate non-2xx responses are allowed and are NOT failures: 400/422 for
missing query params, 401/403 for auth gates, 404 for unresolved ids, and
501 for feature-gated endpoints (e.g. Apple Wallet signing not configured).
Only a 500 fails a parametrised case, and the failing case's id is the exact
endpoint path so the culprit is unambiguous.

Marked ``pg``: every case is skipped (never failed) when no database is
reachable, so a missing local database does not turn CI red.

Read-only: only GET endpoints are exercised, so no test rows are created and
no cleanup is required; the smoke leans on rows already present in the target
database and asserts nothing about their contents.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Optional

import pytest

pytestmark = pytest.mark.pg

# App import + route enumeration need no database, so this is safe at collection
# time even when Postgres is down (the individual cases still skip on the marker).
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.core.security import make_token  # noqa: E402


# ---------------------------------------------------------------------------
# Route enumeration + denylist.
#
# These endpoints never return a prompt, ordinary response body and so cannot be
# smoke-asserted with a blocking ``client.get`` -- they are shown as *skipped*
# (never silently dropped) so their non-coverage is visible:
#   * 4 Server-Sent-Events streams  -> infinite generators; a full-body GET
#     blocks forever and even wedges TestClient's lifespan shutdown.
#   * 1 external video proxy        -> streams a third-party upstream over the
#     network (no bearing on the dialect layer).
#   * competitor dashboard          -> a ~36s O(rows*brands) synchronous scan;
#     excluded here so the smoke stays bounded, and covered instead by the
#     dedicated bounded perf test at the bottom of this file.
# ---------------------------------------------------------------------------
_NON_ASSERTABLE_ROUTE_NAMES = {
    "stream_activity",
    "stream_progress_center",
    "stream_audit_status",
    "stream_via_events",
    "proxy_vkpi_external_video",
    "get_pool_competitor_dashboard",
}


def _enumerate_get_routes() -> dict[str, str]:
    """Return ``{path: route_name}`` for every GET APIRoute (first name wins)."""
    routes: dict[str, str] = {}
    for route in app.routes:
        if isinstance(route, APIRoute) and "GET" in route.methods:
            routes.setdefault(route.path, route.name)
    return routes


_GET_ROUTES = _enumerate_get_routes()
_EXCLUDED_PATHS = {
    path for path, name in _GET_ROUTES.items() if name in _NON_ASSERTABLE_ROUTE_NAMES
}
_ALL_GET_PATHS = sorted(_GET_ROUTES)


def _smoke_params() -> list[Any]:
    params: list[Any] = []
    for path in _ALL_GET_PATHS:
        if path in _EXCLUDED_PATHS:
            params.append(
                pytest.param(
                    path,
                    marks=pytest.mark.skip(
                        reason="streaming/unbounded endpoint — not smoke-assertable "
                        "(see module docstring)"
                    ),
                )
            )
        else:
            params.append(pytest.param(path))
    return params


# ---------------------------------------------------------------------------
# Path-parameter resolution: fill ``{id}`` slots with a real row id from the
# live database where a clean mapping exists, else the neutral fallback "1"
# (which satisfies both int- and str-typed params so the handler's lookup SQL
# still executes -- a 404 "not found" still proves the query ran without a
# dialect crash).
# ---------------------------------------------------------------------------
_PARAM_ID_SQL: dict[str, str] = {
    "kol_pool_id": "SELECT id FROM vkpi_kol_pool ORDER BY id LIMIT 1",
    "kol_id": "SELECT id FROM vkpi_kol_pool ORDER BY id LIMIT 1",
    "project_id": "SELECT id FROM vkpi_industry_projects ORDER BY id LIMIT 1",
    "event_id": "SELECT id FROM vkpi_events ORDER BY id LIMIT 1",
    "channel_id": "SELECT channel_id FROM vkpi_channel_metrics "
    "WHERE channel_id IS NOT NULL ORDER BY channel_id LIMIT 1",
    "user_id": "SELECT id FROM users ORDER BY id LIMIT 1",
    "staff_id": "SELECT id FROM staff ORDER BY id LIMIT 1",
    "report_id": "SELECT id FROM vkpi_industry_reports ORDER BY id LIMIT 1",
    "run_id": "SELECT id FROM vkpi_report_runs ORDER BY id LIMIT 1",
    "account_id": "SELECT id FROM vkpi_industry_accounts ORDER BY id LIMIT 1",
    "submission_id": "SELECT id FROM submissions ORDER BY id LIMIT 1",
    "campaign_id": "SELECT id FROM vkpi_campaigns ORDER BY id LIMIT 1",
    "content_id": "SELECT id FROM vkpi_content_posts ORDER BY id LIMIT 1",
    "post_id": "SELECT id FROM vkpi_industry_posts ORDER BY id LIMIT 1",
    "shipment_id": "SELECT id FROM vkpi_shipments ORDER BY id LIMIT 1",
    "activity_id": "SELECT id FROM activities ORDER BY id LIMIT 1",
    "integration_id": "SELECT id FROM integrations ORDER BY id LIMIT 1",
    "message_id": "SELECT id FROM vkpi_messages ORDER BY id LIMIT 1",
    "session_id": "SELECT id FROM vkpi_kol_search_sessions ORDER BY id LIMIT 1",
    "recommendation_id": "SELECT id FROM vkpi_kol_recommendations ORDER BY id LIMIT 1",
    "sku": "SELECT sku FROM vkpi_monitored_products "
    "WHERE sku IS NOT NULL ORDER BY sku LIMIT 1",
}

_PARAM_FALLBACK = "1"


def _fill_path(path: str, values: dict[str, str]) -> str:
    def repl(match: "re.Match[str]") -> str:
        name = match.group(1).split(":")[0]  # strip a ':path' converter suffix
        return values.get(name, _PARAM_FALLBACK)

    return re.sub(r"\{([^}]+)\}", repl, path)


def _call_with_deadline(fn, timeout: float) -> tuple[Optional[Any], Optional[Any]]:
    """Run ``fn()`` in a daemon thread, returning ``(result, error)``.

    ``error`` is the string ``"timeout"`` if ``fn`` did not finish within
    ``timeout`` seconds (the daemon thread is abandoned but cannot block the
    interpreter from exiting), otherwise any exception raised, otherwise None.
    This keeps one accidentally-hanging endpoint from freezing the whole suite.
    """
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["result"] = fn()
        except Exception as exc:  # noqa: BLE001 - reported to the test as a failure
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None, "timeout"
    if "error" in box:
        return None, box["error"]
    return box.get("result"), None


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def owner_headers() -> dict[str, str]:
    """Bearer header for the owner account (uid=1) via the real signing path."""
    return {"Authorization": f"Bearer {make_token(1, 'admin')}"}


@pytest.fixture(scope="module")
def smoke_client(pg_dsn: str):  # pg_dsn gates on DB availability + skips cleanly
    """A single TestClient whose lifespan (DB pool init) is entered once.

    ``raise_server_exceptions=False`` makes an unhandled exception come back as
    a real 500 *response* instead of propagating into the test, so the smoke
    can assert on ``status_code`` uniformly.
    """
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture(scope="module")
def path_param_values(pg_dsn: str) -> dict[str, str]:
    """Resolve one real row id per known path-param name (read-only)."""
    import psycopg

    resolved: dict[str, str] = {}
    conn = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        for name, sql in _PARAM_ID_SQL.items():
            try:
                cur = conn.cursor()
                cur.execute(sql)
                row = cur.fetchone()
                if row and row[0] is not None:
                    resolved[name] = str(row[0])
            except Exception:
                conn.rollback()  # missing table/column -> fall back to "1"
    finally:
        conn.close()
    return resolved


# ---------------------------------------------------------------------------
# Sanity guard: if the owner token is silently rejected, the smoke would still
# pass (401/403 != 500) but cover only anonymous code paths. Fail loudly so an
# auth/JWT_SECRET/uid mismatch cannot masquerade as a green run.
# ---------------------------------------------------------------------------
def test_owner_token_is_accepted(smoke_client, owner_headers) -> None:
    resp = smoke_client.get("/api/admin/insights/overview", headers=owner_headers)
    assert resp.status_code not in (401, 403), (
        f"owner token rejected ({resp.status_code}) on an owner-gated endpoint; "
        "uid=1 must be the owner and JWT_SECRET must match, otherwise this smoke "
        "silently under-covers every authenticated endpoint"
    )
    assert resp.status_code != 500, resp.text[:500]


def test_route_table_is_populated() -> None:
    # Guards against a refactor that accidentally stops registering GET routes,
    # which would make the parametrised smoke vacuously green.
    assert len(_ALL_GET_PATHS) > 400, (
        f"only {len(_ALL_GET_PATHS)} GET routes enumerated — route registration "
        "looks broken"
    )


# ---------------------------------------------------------------------------
# The smoke: one parametrised case per GET endpoint. The case id is the path,
# so a failure reads e.g.
#   test_get_endpoint_never_500[/api/admin/intel/student/detail/{user_id}]
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", _smoke_params())
def test_get_endpoint_never_500(
    path: str, smoke_client, owner_headers, path_param_values
) -> None:
    url = _fill_path(path, path_param_values)
    resp, error = _call_with_deadline(
        lambda: smoke_client.get(url, headers=owner_headers), timeout=20.0
    )
    if error == "timeout":
        pytest.fail(
            f"GET {url} did not respond within 20s — possible hang / deadlock / "
            "unbounded query"
        )
    assert error is None, f"GET {url} raised {type(error).__name__}: {error}"
    assert resp.status_code != 500, (
        f"GET {url} returned HTTP 500 (unhandled server error):\n"
        f"{resp.text[:600]}"
    )


# ---------------------------------------------------------------------------
# Bounded perf regression for the endpoint excluded above. The competitor
# dashboard GET evaluates rows*brands synchronously with a per-relation DB
# query and, with no persisted snapshot, does ~18k evaluations in ~36s on the
# live pool — past any gateway timeout. Assert the underlying evaluation
# finishes within a generous bound; today it does not, exposing the defect
# without waiting the full ~36s.
# ---------------------------------------------------------------------------
def test_competitor_dashboard_evaluation_is_bounded(pg_dsn: str) -> None:
    from app.db.connection import db_connection_sync_scope
    from app.domains.kol import competitor_detector as detector

    def run() -> Any:
        with db_connection_sync_scope():
            return detector.batch_evaluate_kol_pool(
                brand="",
                limit=1200,
                source_type="legacy_excel_p2d",
                prefer_persisted=True,
            )

    started = time.time()
    result, error = _call_with_deadline(run, timeout=15.0)
    elapsed = time.time() - started
    if error == "timeout":
        pytest.fail(
            "GET /api/admin/vkpi/kol-pool/competitors/dashboard evaluation did not "
            f"finish within {elapsed:.0f}s — O(rows*brands) synchronous per-relation "
            "DB scan with no persisted snapshot (measured ~36s / ~18k evaluations "
            "directly); the endpoint will time out behind any gateway"
        )
    assert error is None, f"evaluation raised {type(error).__name__}: {error}"
    assert isinstance(result, dict)
