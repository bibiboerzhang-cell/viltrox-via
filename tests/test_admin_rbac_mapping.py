from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import (  # noqa: E402
    _admin_permission_for_request,
    _db_request_admission_limiter,
    _request_requires_db_admission,
    admin_rbac_middleware,
    db_scope_middleware,
    health_check,
)


class AdminRbacMappingTests(unittest.TestCase):
    def test_rbac_database_check_runs_off_event_loop(self) -> None:
        event_loop_thread = threading.get_ident()
        check_threads: list[int] = []
        sentinel = object()

        def allowed(_request) -> bool:
            check_threads.append(threading.get_ident())
            return True

        async def call_next(_request):
            return sentinel

        async def run():
            with patch("app.main._admin_rbac_allowed", side_effect=allowed):
                return await admin_rbac_middleware(object(), call_next)

        self.assertIs(asyncio.run(run()), sentinel)
        self.assertEqual(len(check_threads), 1)
        self.assertNotEqual(check_threads[0], event_loop_thread)

    def test_rbac_releases_its_bounded_scope_before_routing(self) -> None:
        events: list[str] = []
        sentinel = object()

        @contextmanager
        def scope():
            events.append("scope-enter")
            try:
                yield
            finally:
                events.append("scope-exit")

        def allowed(_request) -> bool:
            events.append("allowed")
            return True

        async def call_next(_request):
            events.append("call-next")
            return sentinel

        async def run():
            with (
                patch("app.main.db_connection_sync_scope", side_effect=scope),
                patch("app.main._admin_rbac_allowed", side_effect=allowed),
            ):
                return await admin_rbac_middleware(object(), call_next)

        self.assertIs(asyncio.run(run()), sentinel)
        self.assertEqual(events, ["scope-enter", "allowed", "scope-exit", "call-next"])

    def test_rbac_primes_request_connection_off_loop_after_bounded_check(self) -> None:
        event_loop_thread = threading.get_ident()
        prime_threads: list[int] = []
        events: list[str] = []
        sentinel = object()

        def allowed(_request) -> bool:
            events.append("allowed")
            return True

        def prime():
            events.append("prime")
            prime_threads.append(threading.get_ident())

        async def call_next(_request):
            events.append("call-next")
            return sentinel

        async def run():
            with (
                patch("app.main._admin_rbac_allowed_bounded", side_effect=allowed),
                patch("app.main._request_requires_db_admission", return_value=True),
                patch("app.main.get_conn", side_effect=prime),
            ):
                return await admin_rbac_middleware(object(), call_next)

        self.assertIs(asyncio.run(run()), sentinel)
        self.assertEqual(events, ["allowed", "prime", "call-next"])
        self.assertEqual(len(prime_threads), 1)
        self.assertNotEqual(prime_threads[0], event_loop_thread)

    def test_rbac_database_pressure_returns_retryable_503(self) -> None:
        async def call_next(_request):
            raise AssertionError("failed RBAC admission must not reach routing")

        async def run():
            with patch(
                "app.main._admin_rbac_allowed_bounded",
                side_effect=RuntimeError("pool unavailable"),
            ):
                return await admin_rbac_middleware(object(), call_next)

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "5")
        self.assertIn(b"db_request_admission_timeout", response.body)

    def test_sensitive_request_audit_runs_off_event_loop(self) -> None:
        event_loop_thread = threading.get_ident()
        audit_threads: list[int] = []
        response = SimpleNamespace(status_code=200)

        @asynccontextmanager
        async def scope():
            yield

        async def call_next(_request):
            return response

        def audit(_request, status_code: int) -> None:
            self.assertEqual(status_code, 200)
            audit_threads.append(threading.get_ident())

        async def run():
            with (
                patch("app.main.db_connection_scope", side_effect=scope),
                patch("app.main._audit_sensitive_request", side_effect=audit),
            ):
                return await db_scope_middleware(object(), call_next)

        self.assertIs(asyncio.run(run()), response)
        self.assertEqual(len(audit_threads), 1)
        self.assertNotEqual(audit_threads[0], event_loop_thread)

    def test_db_admission_timeout_returns_explicit_retryable_503(self) -> None:
        class NeverAvailable:
            async def acquire(self):
                await asyncio.Event().wait()

            def release(self):
                raise AssertionError("unacquired limiter must not be released")

        async def call_next(_request):
            raise AssertionError("timed-out request must not reach routing")

        async def run():
            with (
                patch("app.main._request_requires_db_admission", return_value=True),
                patch("app.main._db_request_admission_limiter", return_value=NeverAvailable()),
                patch("app.main._DB_REQUEST_ADMISSION_TIMEOUT_SEC", 0.01),
            ):
                return await db_scope_middleware(object(), call_next)

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "1")
        self.assertIn(b"db_request_admission_timeout", response.body)

    def test_db_admission_releases_capacity_on_cancellation(self) -> None:
        limiter = asyncio.BoundedSemaphore(1)

        @asynccontextmanager
        async def scope():
            yield

        async def call_next(_request):
            raise asyncio.CancelledError()

        async def run():
            with (
                patch("app.main._request_requires_db_admission", return_value=True),
                patch("app.main._db_request_admission_limiter", return_value=limiter),
                patch("app.main.db_connection_scope", side_effect=scope),
            ):
                await db_scope_middleware(object(), call_next)

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(run())
        self.assertEqual(limiter._value, 1)

    def test_db_admission_is_per_event_loop(self) -> None:
        async def limiter_for_running_loop():
            return _db_request_admission_limiter()

        first = asyncio.run(limiter_for_running_loop())
        second = asyncio.run(limiter_for_running_loop())
        self.assertIsNot(first, second)

    def test_db_admission_bypasses_frontend_and_health_routes(self) -> None:
        for path in ("/", "/robots.txt", "/assets/app.js", "/favicon.svg", "/health"):
            request = SimpleNamespace(scope={"path": path})
            with patch("app.main.is_postgres_runtime", return_value=True):
                self.assertFalse(_request_requires_db_admission(request), path)

        request = SimpleNamespace(scope={"path": "/api/admin/vkpi/dashboard/summary"})
        with patch("app.main.is_postgres_runtime", return_value=True):
            self.assertTrue(_request_requires_db_admission(request))

    def test_health_runtime_trust_runs_off_event_loop(self) -> None:
        event_loop_thread = threading.get_ident()
        trust_threads: list[int] = []

        def trust():
            trust_threads.append(threading.get_ident())
            return {"worker_online": None}

        request = SimpleNamespace(query_params={})
        with (
            patch("app.main._runtime_trust", side_effect=trust),
            patch("app.main.IS_PRODUCTION", False),
        ):
            response = asyncio.run(health_check(request, deep=False))

        self.assertEqual(response["status"], "ok")
        self.assertEqual(len(trust_threads), 1)
        self.assertNotEqual(trust_threads[0], event_loop_thread)

    def test_staff_invite_public_routes_skip_admin_rbac(self) -> None:
        self.assertIsNone(_admin_permission_for_request("/api/admin/staff/accept-invite", "POST"))
        self.assertIsNone(_admin_permission_for_request("/api/admin/staff/invite/status", "GET"))

    def test_intelligent_query_post_is_mapped_to_read_permission(self) -> None:
        self.assertEqual(
            _admin_permission_for_request(
                "/api/admin/vkpi/intelligent/query",
                "POST",
            ),
            ("vkpi", "read", False),
        )

    def test_intel_nested_student_routes_use_student_tab(self) -> None:
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/student/overview", "GET"),
            ("student", "read", False),
        )
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/student/schools", "POST"),
            ("student", "write", False),
        )

    def test_intel_nested_via_routes_use_via_tab(self) -> None:
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/via/control-overview", "GET"),
            ("via", "read", False),
        )
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/via/proposals/key/apply", "POST"),
            ("via", "write", False),
        )

    def test_intel_nested_system_routes_use_runtime_tab(self) -> None:
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/system/cache", "GET"),
            ("runtime", "read", False),
        )
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/system/cache/clear", "POST"),
            ("runtime", "write", False),
        )

    def test_market_and_brand_intelligence_routes_use_analytics_tab(self) -> None:
        self.assertEqual(
            _admin_permission_for_request("/api/intelligence/market/gaps", "GET"),
            ("analytics", "read", False),
        )
        self.assertEqual(
            _admin_permission_for_request("/api/intelligence/brand/insights/generate", "POST"),
            ("analytics", "write", False),
        )

    def test_trust_user_moderation_routes_use_command_tab(self) -> None:
        for suffix in ("block", "unblock", "flag", "clear-flag", "adjust-score"):
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    _admin_permission_for_request(f"/api/admin/users/42/{suffix}", "POST"),
                    ("command", "write", False),
                )


if __name__ == "__main__":
    unittest.main()
