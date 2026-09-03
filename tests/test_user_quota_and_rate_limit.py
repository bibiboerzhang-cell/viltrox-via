"""Pin the per-staff daily quota + per-user burst gate, the sentry import guard,
the alert egress check script and the advisory pip-audit CI job (beta lane B/O)."""
from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.platform import rate_limit_store, user_quota  # noqa: E402

PREFIX = "/api/admin/vkpi"
SEARCH = f"{PREFIX}/kol-smart-search"
ADVANCE_JOB = f"{PREFIX}/kol-smart-search/profile-advance-job"
VIDEO = f"{PREFIX}/kol-pool/42/enqueue-video-analysis"
CRAWL = f"{PREFIX}/kol-url-deep-crawl"
OUTREACH = f"{PREFIX}/kol-search-sessions/7/generate-outreach"
CHEAP = f"{PREFIX}/kol-pool/42/cooperation"


@pytest.fixture(autouse=True)
def _hermetic_counters(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rate_limit_store, "_redis_client", None)
    monkeypatch.setattr(rate_limit_store, "Redis", None)
    monkeypatch.setattr(rate_limit_store, "_memory_windows", {})
    user_quota.reset_state_for_tests()
    for action in user_quota.ACTIONS.values():
        monkeypatch.delenv(action.env, raising=False)
    monkeypatch.delenv(user_quota.ENV_ENABLED, raising=False)
    monkeypatch.delenv(user_quota.ENV_BURST, raising=False)
    yield
    user_quota.reset_state_for_tests()


def _build_app() -> FastAPI:
    app = FastAPI()
    user_quota.install(app)

    # Registered after install → runs *before* the quota middleware, like admin_rbac_middleware.
    @app.middleware("http")
    async def _fake_rbac(request: Request, call_next):
        staff_id = request.headers.get("x-test-staff")
        if staff_id:
            request.state.vkpi_authorized_staff = {"id": int(staff_id), "user_id": 900 + int(staff_id)}
        return await call_next(request)

    async def ok(request: Request) -> dict[str, Any]:
        return {"ok": True, "path": request.url.path}

    async def fail(request: Request) -> dict[str, Any]:
        raise HTTPException(status_code=400, detail="input is required")

    for path in (SEARCH, ADVANCE_JOB, VIDEO, CRAWL, OUTREACH, CHEAP):
        app.add_api_route(path, ok, methods=["POST"])
        app.add_api_route(path, ok, methods=["GET"])
    app.add_api_route(f"{PREFIX}/kol-pool/43/enqueue-video-analysis", fail, methods=["POST"])
    return app


def _client() -> TestClient:
    return TestClient(_build_app(), raise_server_exceptions=False)


def _post(client: TestClient, path: str, staff: int | None = 1, body: dict | None = None):
    headers = {"x-test-staff": str(staff)} if staff is not None else {}
    return client.post(path, json=body if body is not None else {"input": "x"}, headers=headers)


# ── route classification ──


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", SEARCH, "smart_search_online"),
        ("POST", ADVANCE_JOB, "smart_search_online"),
        ("POST", f"{PREFIX}/kol-pool/discovery/federated-search/refresh", "smart_search_online"),
        ("POST", VIDEO, "video_deep_analysis"),
        ("POST", f"{PREFIX}/kol-pool/enqueue-video-analysis-batch", "video_deep_analysis"),
        ("POST", f"{PREFIX}/kol-memory/5/video-fullscan-enqueue", "video_deep_analysis"),
        ("POST", CRAWL, "deep_crawl"),
        ("POST", f"{PREFIX}/kol-search-sessions/1/items/2/profile-crawl", "deep_crawl"),
        ("POST", f"{PREFIX}/kol-search-sessions/1/advance-job", "deep_crawl"),
        ("POST", f"{PREFIX}/kols/9/analyze-account", "deep_crawl"),
        ("POST", OUTREACH, "outreach_send"),
        ("POST", f"{PREFIX}/kol-pool/outreach-draft/enqueue", "outreach_send"),
        ("POST", f"{PREFIX}/kol-pool/12/outreach-pack", "outreach_send"),
        ("POST", "/api/marketing/kol-url-deep-crawl", "deep_crawl"),
        ("GET", SEARCH, None),
        ("POST", CHEAP, None),
        ("POST", f"{PREFIX}/kol-search-sessions/1/advance-job/cancel", None),
        ("POST", "/api/auth/login", None),
    ],
)
def test_match_route_covers_the_expensive_families_only(method: str, path: str, expected: str | None) -> None:
    matched = user_quota.match_route(method, path)
    assert (matched[0] if matched else None) == expected


def test_every_action_has_env_default_and_facade_label() -> None:
    for key, action in user_quota.ACTIONS.items():
        assert action.key == key
        assert action.env.startswith("VKPI_USER_DAILY_QUOTA_")
        assert action.default > 0
        for banned in ("LLM", "lexicon", "rule_v0", "词表"):
            assert banned not in action.label


# ── daily quota ──


def test_daily_quota_blocks_with_honest_429_after_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_USER_DAILY_QUOTA_VIDEO_DEEP_ANALYSIS", "2")
    client = _client()
    first = _post(client, VIDEO)
    assert first.status_code == 200
    assert first.headers["X-Quota-Action"] == "video_deep_analysis"
    assert first.headers["X-Quota-Limit"] == "2"
    assert first.headers["X-Quota-Remaining"] == "1"
    second = _post(client, VIDEO)
    assert second.status_code == 200
    assert second.headers["X-Quota-Remaining"] == "0"
    third = _post(client, VIDEO)
    assert third.status_code == 429
    payload = third.json()
    assert payload["code"] == "user_daily_quota_exceeded"
    assert payload["scope"] == "per_user"
    assert payload["used"] == 2 and payload["limit"] == 2
    assert "视频深度分析" in payload["detail"] and "重置" in payload["detail"]
    assert payload["resets_at"].endswith("+00:00")
    assert int(third.headers["Retry-After"]) >= 1
    assert third.headers["X-Quota-Used"] == "2"


def test_failed_requests_do_not_consume_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_USER_DAILY_QUOTA_VIDEO_DEEP_ANALYSIS", "1")
    client = _client()
    for _ in range(3):
        assert _post(client, f"{PREFIX}/kol-pool/43/enqueue-video-analysis").status_code == 400
    assert user_quota.used_today("video_deep_analysis", 1) == 0
    assert _post(client, VIDEO).status_code == 200
    assert _post(client, VIDEO).status_code == 429


def test_quota_is_isolated_per_staff_and_per_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_USER_DAILY_QUOTA_DEEP_CRAWL", "1")
    monkeypatch.setenv("VKPI_USER_DAILY_QUOTA_OUTREACH_SEND", "1")
    client = _client()
    assert _post(client, CRAWL, staff=1).status_code == 200
    assert _post(client, CRAWL, staff=1).status_code == 429
    assert _post(client, CRAWL, staff=2).status_code == 200  # another staff: own counter
    assert _post(client, OUTREACH, staff=1).status_code == 200  # another family: own counter
    assert _post(client, OUTREACH, staff=1).status_code == 429


def test_smart_search_counts_only_online_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_USER_DAILY_QUOTA_SMART_SEARCH_ONLINE", "1")
    monkeypatch.setenv(user_quota.ENV_BURST, "100/60")
    client = _client()
    for _ in range(3):  # provider-free recall is not quota-bound
        response = _post(client, SEARCH, body={"input": "sony fe lens"})
        assert response.status_code == 200
        assert "X-Quota-Action" not in response.headers
    assert _post(client, SEARCH, body={"input": "x", "include_new_discovery": True}).status_code == 200
    blocked = _post(client, SEARCH, body={"input": "x", "execute_new_discovery": True})
    assert blocked.status_code == 429
    assert blocked.json()["action"] == "smart_search_online"
    assert _post(client, ADVANCE_JOB).status_code == 429  # same family, always counted


def test_unauthenticated_and_disabled_paths_bypass_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_USER_DAILY_QUOTA_DEEP_CRAWL", "1")
    client = _client()
    assert _post(client, CRAWL, staff=None).status_code == 200  # RBAC upstream owns this case
    assert _post(client, CRAWL, staff=None).status_code == 200
    assert user_quota.used_today("deep_crawl", 0) == 0
    monkeypatch.setenv(user_quota.ENV_ENABLED, "0")
    for _ in range(3):
        assert _post(client, CRAWL, staff=1).status_code == 200


def test_zero_or_negative_limit_means_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_USER_DAILY_QUOTA_DEEP_CRAWL", "0")
    client = _client()
    for _ in range(5):
        response = _post(client, CRAWL)
        assert response.status_code == 200
        assert "X-Quota-Action" not in response.headers
    assert user_quota.snapshot(1)["actions"]["deep_crawl"]["unlimited"] is True


def test_defaults_apply_when_env_is_unset_or_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    assert user_quota.daily_limit("video_deep_analysis") == 20
    monkeypatch.setenv("VKPI_USER_DAILY_QUOTA_VIDEO_DEEP_ANALYSIS", "lots")
    assert user_quota.daily_limit("video_deep_analysis") == 20
    assert user_quota.daily_limit("unknown_action") == 0
    assert user_quota.burst_limit() == user_quota.DEFAULT_BURST
    monkeypatch.setenv(user_quota.ENV_BURST, "5/30")
    assert user_quota.burst_limit() == (5, 30)
    monkeypatch.setenv(user_quota.ENV_BURST, "5/0")
    assert user_quota.burst_limit() == user_quota.DEFAULT_BURST


def test_counter_rolls_over_at_utc_midnight() -> None:
    day_one = 1_760_000_000.0  # 2025-10-09T08:53:20Z
    user_quota.consume("deep_crawl", 5, now=day_one)
    assert user_quota.used_today("deep_crawl", 5, now=day_one) == 1
    resets = user_quota.reset_at(day_one)
    assert (resets.hour, resets.minute, resets.second) == (0, 0, 0)
    assert user_quota.used_today("deep_crawl", 5, now=resets.timestamp() + 1) == 0


def test_snapshot_reports_used_and_remaining(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_USER_DAILY_QUOTA_OUTREACH_SEND", "3")
    user_quota.consume("outreach_send", 8)
    view = user_quota.snapshot(8)
    assert view["enabled"] is True
    assert view["actions"]["outreach_send"] == {
        "label": "外联生成/发送", "limit": 3, "used": 1, "remaining": 2, "unlimited": False,
    }


# ── per-user burst limit ──


def test_burst_limit_is_keyed_by_user_not_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(user_quota.ENV_BURST, "2/60")
    client = _client()
    assert _post(client, CRAWL, staff=1).status_code == 200
    ok = _post(client, CRAWL, staff=1)
    assert ok.status_code == 200
    assert ok.headers["X-RateLimit-Bucket"] == user_quota.BURST_BUCKET
    assert ok.headers["X-RateLimit-Scope"] == "per_user"
    blocked = _post(client, CRAWL, staff=1)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "user_rate_limited"
    assert blocked.headers["Retry-After"] == "60"
    assert blocked.headers["X-RateLimit-Remaining"] == "0"
    # Same client/IP, different staff → its own window.
    assert _post(client, CRAWL, staff=2).status_code == 200
    # Burst-blocked requests never touch the daily counter.
    assert user_quota.used_today("deep_crawl", 1) == 2


def test_burst_limit_also_covers_cheap_recall_on_expensive_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(user_quota.ENV_BURST, "1/60")
    client = _client()
    assert _post(client, SEARCH, body={"input": "plain recall"}).status_code == 200
    assert _post(client, SEARCH, body={"input": "plain recall"}).status_code == 429
    assert _post(client, CHEAP).status_code == 200  # non-expensive route untouched


def test_middleware_is_wired_into_main_before_csrf() -> None:
    source = (BACKEND_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    install_at = source.index('importlib.import_module("app.platform.user_quota").install(app)')
    csrf_at = source.index("async def csrf_origin_middleware")
    rbac_at = source.index("async def admin_rbac_middleware")
    assert install_at < csrf_at < rbac_at  # earlier registration = inner = runs after RBAC


# ── sentry import guard ──


def _sentry_block_source() -> str:
    path = BACKEND_ROOT / "app" / "main.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "sentry_dsn":
            return "sentry_dsn = 'https://public@sentry.example/1'\n" + ast.get_source_segment(source, node)
    raise AssertionError("sentry guard block not found in main.py")


def test_sentry_missing_sdk_degrades_to_one_log_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # import → ImportError
    logger = MagicMock()
    namespace: dict[str, Any] = {"os": __import__("os"), "logger": logger}
    exec(compile(_sentry_block_source(), "main_sentry_block", "exec"), namespace)  # noqa: S102 - test harness
    logger.warning.assert_called_once()
    assert "sentry_sdk is not installed" in logger.warning.call_args.args[0]


def test_sentry_present_sdk_is_initialised(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("sentry_sdk")
    fake.init = MagicMock()
    integrations = types.ModuleType("sentry_sdk.integrations")
    fastapi_mod = types.ModuleType("sentry_sdk.integrations.fastapi")
    fastapi_mod.FastApiIntegration = lambda: "fastapi"
    starlette_mod = types.ModuleType("sentry_sdk.integrations.starlette")
    starlette_mod.StarletteIntegration = lambda: "starlette"
    for name, module in (
        ("sentry_sdk", fake),
        ("sentry_sdk.integrations", integrations),
        ("sentry_sdk.integrations.fastapi", fastapi_mod),
        ("sentry_sdk.integrations.starlette", starlette_mod),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    logger = MagicMock()
    namespace: dict[str, Any] = {"os": __import__("os"), "logger": logger}
    exec(compile(_sentry_block_source(), "main_sentry_block", "exec"), namespace)  # noqa: S102 - test harness
    logger.warning.assert_not_called()
    kwargs = fake.init.call_args.kwargs
    assert kwargs["dsn"] == "https://public@sentry.example/1"
    assert kwargs["send_default_pii"] is False
    assert kwargs["integrations"] == ["starlette", "fastapi"]


# ── alert egress check script ──


def _load_egress_script():
    path = REPO / "scripts" / "ops" / "alert_egress_check.py"
    spec = importlib.util.spec_from_file_location("alert_egress_check", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_egress_check_reports_not_configured_without_leaking(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("VKPI_ALERT_WEBHOOK_URL", raising=False)
    script = _load_egress_script()
    assert script.run(["--json"]) == script.EXIT_NOT_CONFIGURED
    payload = yaml.safe_load(capsys.readouterr().out)
    assert payload["configured"] is False and payload["sent"] is False
    assert payload["reason"] == "not_configured"


def test_egress_check_sends_and_verifies_2xx(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    secret_url = "https://hooks.example.test/services/T000/B000/secret-token"
    monkeypatch.setenv("VKPI_ALERT_WEBHOOK_URL", secret_url)
    monkeypatch.setenv("VKPI_ALERT_WEBHOOK_KIND", "generic")
    monkeypatch.delenv("VKPI_ALERT_SILENCE_KEYS", raising=False)
    script = _load_egress_script()
    seen: list[dict[str, Any]] = []

    def transport(payload: dict[str, Any], timeout_s: float) -> tuple[int, str]:
        seen.append(payload)
        return 200, "ok"

    assert script.run([], transport=transport) == script.EXIT_SENT
    text = capsys.readouterr().out
    assert "SENT (2xx)" in text and "http_status=200" in text
    assert secret_url not in text and "secret-token" not in text and "hooks.example.test" not in text
    assert seen and seen[0]["key"] == script.ALERT_KEY and seen[0]["severity"] == "info"

    assert script.run(["--json"], transport=lambda payload, timeout_s: (503, "upstream down")) == script.EXIT_FAILED
    failed = yaml.safe_load(capsys.readouterr().out)
    assert failed["sent"] is False and failed["status"] == 503 and secret_url not in str(failed)

    monkeypatch.setenv("VKPI_ALERT_SILENCE_KEYS", "other,egress-check")
    assert script.run(["--json"], transport=transport) == script.EXIT_SILENCED
    assert script.run(["--dry-run"], transport=transport) == script.EXIT_SENT
    assert len(seen) == 1  # dry-run and silenced runs never hit the transport


# ── CI: advisory pip-audit job ──


def test_verify_workflow_has_advisory_pip_audit_job() -> None:
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert {"verify", "postgres-integration", "pip-audit"} <= set(jobs)
    job = jobs["pip-audit"]
    assert job["continue-on-error"] is True
    assert "needs" not in job  # runs in parallel; never delays the real gate
    audit_steps = [s for s in job["steps"] if "pip-audit -r requirements.txt" in str(s.get("run", ""))]
    assert len(audit_steps) == 1 and audit_steps[0]["continue-on-error"] is True
    assert "pip-audit==2.10.1" in "".join(str(s.get("run", "")) for s in job["steps"])


class _SearchIn(BaseModel):  # module-level: `from __future__ import annotations` keeps FastAPI resolving it
    input: str
    include_new_discovery: bool = False


def test_body_flag_sniffing_does_not_starve_the_route_of_its_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The middleware reads the JSON body to classify kol-smart-search; the route must still receive it."""
    monkeypatch.setenv("VKPI_USER_DAILY_QUOTA_SMART_SEARCH_ONLINE", "1")
    app = FastAPI()
    user_quota.install(app)

    @app.middleware("http")
    async def _fake_rbac(request: Request, call_next):
        request.state.vkpi_authorized_staff = {"id": 5}
        return await call_next(request)

    async def echo(payload: _SearchIn) -> dict[str, Any]:
        return {"echo": payload.input, "online": payload.include_new_discovery}

    app.add_api_route(SEARCH, echo, methods=["POST"])
    client = TestClient(app, raise_server_exceptions=False)
    response = _post(client, SEARCH, body={"input": "sony", "include_new_discovery": True})
    assert response.status_code == 200, response.text
    assert response.json() == {"echo": "sony", "online": True}
    assert response.headers["X-Quota-Action"] == "smart_search_online"
    assert _post(client, SEARCH, body={"input": "sony", "include_new_discovery": True}).status_code == 429
    recall = _post(client, SEARCH, body={"input": "sony"})  # no discovery flag: body still reaches the route
    assert recall.status_code == 200 and recall.json() == {"echo": "sony", "online": False}
