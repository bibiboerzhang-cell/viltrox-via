from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
VERIFIER_PATH = SCRIPTS_ROOT / "verify_private_surface_live.py"
VERIFIER_SPEC = importlib.util.spec_from_file_location("vkpi_private_surface_verifier", VERIFIER_PATH)
assert VERIFIER_SPEC is not None and VERIFIER_SPEC.loader is not None
verifier = importlib.util.module_from_spec(VERIFIER_SPEC)
sys.modules[VERIFIER_SPEC.name] = verifier
VERIFIER_SPEC.loader.exec_module(verifier)

BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.main as main  # noqa: E402


ROBOTS_POLICY = "noindex, nofollow, noarchive, nosnippet, noimageindex"
ROBOTS_HEADER_DIRECTIVE = f'add_header X-Robots-Tag "{ROBOTS_POLICY}" always;'
PRIVATE_HTML = f"""<!doctype html>
<html>
  <head>
    <title>{verifier.SAFE_TITLE}</title>
    <meta name="description" content="{verifier.SAFE_DESCRIPTION}" />
    <meta name="robots" content="{ROBOTS_POLICY}" />
    <meta name="googlebot" content="{ROBOTS_POLICY}" />
    <meta name="bingbot" content="{ROBOTS_POLICY}" />
    <script type="module" src="/assets/app.js"></script>
  </head>
  <body><div id="root" data-nosnippet></div></body>
</html>
"""


def _assert_full_robots_header(value: str) -> None:
    assert verifier.REQUIRED_ROBOT_TOKENS <= verifier._robot_tokens(value)


def _nginx_nodes(text: str) -> list[dict[str, object]]:
    roots: list[dict[str, object]] = []
    stack: list[dict[str, object]] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith("{"):
            node: dict[str, object] = {
                "header": line[:-1].strip(),
                "directives": [],
                "children": [],
            }
            if stack:
                stack[-1]["children"].append(node)  # type: ignore[union-attr]
            else:
                roots.append(node)
            stack.append(node)
        elif line == "}":
            assert stack, "unbalanced Nginx closing brace"
            stack.pop()
        else:
            if stack:
                stack[-1]["directives"].append(line)  # type: ignore[union-attr]
    assert not stack, "unbalanced Nginx opening brace"

    flattened: list[dict[str, object]] = []

    def visit(node: dict[str, object]) -> None:
        flattened.append(node)
        for child in node["children"]:  # type: ignore[union-attr]
            visit(child)

    for root in roots:
        visit(root)
    return flattened


def _http_result(
    url: str,
    status: int,
    body: str,
    *,
    content_type: str,
    x_robots_tag: str = ROBOTS_POLICY,
) -> verifier.HttpResult:
    headers = {"content-type": content_type}
    if x_robots_tag:
        headers["x-robots-tag"] = x_robots_tag
    return verifier.HttpResult(status=status, url=url, headers=headers, body=body)


def _passing_live_responses() -> dict[str, verifier.HttpResult]:
    base = "https://private.example/"
    responses = {
        base: _http_result(base, 200, PRIVATE_HTML, content_type="text/html; charset=utf-8"),
        f"{base}robots.txt": _http_result(
            f"{base}robots.txt",
            200,
            "User-agent: *\nDisallow: /\n",
            content_type="text/plain; charset=utf-8",
        ),
        f"{base}assets/app.js": _http_result(
            f"{base}assets/app.js",
            200,
            "console.log('loaded')",
            content_type="application/javascript",
        ),
    }
    for path in ("docs", "redoc", "openapi.json"):
        url = f"{base}{path}"
        responses[url] = _http_result(url, 404, '{"detail":"Not Found"}', content_type="application/json")
    for path in verifier.DEFAULT_PROTECTED_API_PATHS:
        url = f"{base}{path.lstrip('/')}"
        responses[url] = _http_result(url, 403, '{"detail":"Forbidden"}', content_type="application/json")
    return responses


def _access_gated_responses() -> dict[str, verifier.HttpResult]:
    base = "https://private.example/"
    access_location = (
        "https://team.cloudflareaccess.com/cdn-cgi/access/login/private.example"
    )
    responses = {
        base: verifier.HttpResult(
            status=302,
            url=base,
            headers={"location": access_location},
            body="",
        )
    }
    for probe_path in verifier.ACCESS_GATE_STATIC_PROBE_PATHS:
        url = f"{base}{probe_path.lstrip('/')}"
        responses[url] = verifier.HttpResult(
            status=302,
            url=url,
            headers={"location": access_location},
            body="",
        )
    return responses


def test_application_sends_noindex_on_success_and_error_responses() -> None:
    client = TestClient(main.app, raise_server_exceptions=False)
    for path in ("/", "/assets/not-present.js", "/definitely-not-present"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in {200, 307, 404}
        _assert_full_robots_header(response.headers.get("x-robots-tag", ""))


def test_admin_root_serves_spa_without_dropping_cockpit_query() -> None:
    client = TestClient(main.app, raise_server_exceptions=False)
    with mock.patch.object(main, "IS_ADMIN_APP", True), mock.patch.object(
        main, "IS_PUBLIC_APP", False
    ):
        response = client.get("/?cockpit=dealers", follow_redirects=False)
    assert response.status_code == 200
    assert "location" not in response.headers
    _assert_full_robots_header(response.headers.get("x-robots-tag", ""))


def test_frontend_favicons_are_served_without_console_404s() -> None:
    client = TestClient(main.app, raise_server_exceptions=False)
    for path, content_type in (
        ("/favicon.svg", "image/svg+xml"),
        ("/favicon.ico", "image/x-icon"),
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith(content_type)
        assert response.headers.get("cache-control") == "public, max-age=3600"
        _assert_full_robots_header(response.headers.get("x-robots-tag", ""))


def test_application_closes_api_schema_and_denies_all_crawlers_by_default() -> None:
    client = TestClient(main.app, raise_server_exceptions=False)
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404
        _assert_full_robots_header(response.headers.get("x-robots-tag", ""))

    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert robots.text == "User-agent: *\nDisallow: /\n"
    assert verifier._deny_all_robots_failures(robots.text) == []


def test_unauthenticated_internal_apis_do_not_return_data() -> None:
    client = TestClient(main.app, raise_server_exceptions=False)
    for path in ("/api/admin/vkpi/kols?limit=1", "/api/admin/vkpi/projects?limit=1"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in verifier.SAFE_DENIAL_STATUSES
        _assert_full_robots_header(response.headers.get("x-robots-tag", ""))
        assert not verifier._denial_leaks(response.text)


def test_production_public_health_omits_runtime_topology() -> None:
    client = TestClient(main.app, raise_server_exceptions=False)
    with mock.patch.object(main, "IS_PRODUCTION", True), mock.patch.object(
        main, "_can_read_deep_health", return_value=False
    ):
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "service": main.APP_ROLE, "version": main.APP_VERSION}
    assert "build" not in body
    assert "trust" not in body


def test_frontend_metadata_is_generic_and_non_indexable() -> None:
    index = (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    parser = verifier.HeadParser()
    parser.feed(index)
    assert parser.title == verifier.SAFE_TITLE
    assert parser.meta["description"] == verifier.SAFE_DESCRIPTION
    for name in ("robots", "googlebot", "bingbot"):
        _assert_full_robots_header(parser.meta[name])
    assert parser.root_has_data_nosnippet
    assert not verifier._shell_leaks(index)


def test_static_robots_policy_denies_every_crawler_and_path() -> None:
    robots = (REPO_ROOT / "frontend" / "public" / "robots.txt").read_text(encoding="utf-8")
    assert robots == "User-agent: *\nDisallow: /\n"
    assert verifier._deny_all_robots_failures(robots) == []


@pytest.mark.parametrize(
    "relative_path",
    ("deploy/nginx/viltrox-2.0.conf", "deploy/nginx/viltrox-2.0.local.conf"),
)
def test_nginx_makes_noindex_authoritative_for_every_response_class(relative_path: str) -> None:
    config = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    nodes = _nginx_nodes(config)
    servers = [node for node in nodes if node["header"] == "server"]
    locations = [node for node in nodes if str(node["header"]).startswith("location ")]

    assert servers
    for server in servers:
        directives = server["directives"]
        assert ROBOTS_HEADER_DIRECTIVE in directives
        if any(" ssl" in directive for directive in directives):  # type: ignore[union-attr]
            assert "proxy_hide_header X-Robots-Tag;" in directives

    for location in locations:
        directives = location["directives"]
        if any(directive.startswith("add_header ") for directive in directives):  # type: ignore[union-attr]
            assert ROBOTS_HEADER_DIRECTIVE in directives, location["header"]

    ssl_server_count = sum(
        any(" ssl" in directive for directive in server["directives"])  # type: ignore[union-attr]
        for server in servers
    )
    robots_locations = [node for node in locations if node["header"] == "location = /robots.txt"]
    assert len(robots_locations) == ssl_server_count
    for location in robots_locations:
        directives = location["directives"]
        assert 'add_header Cache-Control "no-store, max-age=0" always;' in directives
        assert 'return 200 "User-agent: *\\nDisallow: /\\n";' in directives


def test_live_gate_accepts_complete_private_surface_contract() -> None:
    responses = _passing_live_responses()
    with mock.patch.object(verifier, "fetch", side_effect=lambda url, **_kwargs: responses[url]):
        result = verifier.validate_base_url("https://private.example", timeout=1)
    assert result["ok"] is True
    assert result["failures"] == []


def test_live_gate_accepts_cloudflare_access_interception_when_explicitly_requested() -> None:
    responses = _access_gated_responses()
    with mock.patch.object(verifier, "fetch", side_effect=lambda url, **_kwargs: responses[url]):
        result = verifier.validate_base_url(
            "https://private.example",
            timeout=1,
            expect_access_gated=True,
        )
    assert result["ok"] is True
    assert result["status"] == 302
    assert [probe["status"] for probe in result["static_probes"]] == [302, 302]
    assert result["failures"] == []


def test_live_gate_accepts_direct_access_denials_when_explicitly_requested() -> None:
    responses = _access_gated_responses()
    for url in tuple(responses):
        responses[url] = verifier.HttpResult(
            status=403,
            url=url,
            headers={"content-type": "text/html"},
            body="Access denied",
        )
    with mock.patch.object(verifier, "fetch", side_effect=lambda url, **_kwargs: responses[url]):
        result = verifier.validate_base_url(
            "https://private.example",
            timeout=1,
            expect_access_gated=True,
        )
    assert result["ok"] is True
    assert result["status"] == 403
    assert [probe["status"] for probe in result["static_probes"]] == [403, 403]


def test_access_gate_mode_rejects_an_anonymously_downloadable_root() -> None:
    responses = _access_gated_responses()
    base = "https://private.example/"
    responses[base] = _http_result(
        base,
        200,
        PRIVATE_HTML,
        content_type="text/html; charset=utf-8",
    )
    with mock.patch.object(verifier, "fetch", side_effect=lambda url, **_kwargs: responses[url]):
        result = verifier.validate_base_url(
            "https://private.example",
            timeout=1,
            expect_access_gated=True,
        )
    assert result["ok"] is False
    assert any("root is anonymously downloadable" in failure for failure in result["failures"])


def test_access_gate_mode_rejects_an_anonymously_downloadable_static_asset() -> None:
    responses = _access_gated_responses()
    probe_path = verifier.ACCESS_GATE_STATIC_PROBE_PATHS[0]
    asset_url = f"https://private.example/{probe_path.lstrip('/')}"
    responses[asset_url] = _http_result(
        asset_url,
        200,
        "<svg></svg>",
        content_type="image/svg+xml",
    )
    with mock.patch.object(verifier, "fetch", side_effect=lambda url, **_kwargs: responses[url]):
        result = verifier.validate_base_url(
            "https://private.example",
            timeout=1,
            expect_access_gated=True,
        )
    assert result["ok"] is False
    assert any(
        f"static asset {probe_path} is anonymously downloadable" in failure
        for failure in result["failures"]
    )


def test_access_gate_mode_rejects_a_non_access_redirect() -> None:
    responses = _access_gated_responses()
    base = "https://private.example/"
    responses[base] = verifier.HttpResult(
        status=302,
        url=base,
        headers={"location": "https://private.example/login"},
        body="",
    )
    with mock.patch.object(verifier, "fetch", side_effect=lambda url, **_kwargs: responses[url]):
        result = verifier.validate_base_url(
            "https://private.example",
            timeout=1,
            expect_access_gated=True,
        )
    assert result["ok"] is False
    assert any("outside the Cloudflare Access flow" in failure for failure in result["failures"])


def test_live_gate_rejects_robots_rules_that_reopen_a_named_bot() -> None:
    responses = _passing_live_responses()
    robots_url = "https://private.example/robots.txt"
    responses[robots_url] = _http_result(
        robots_url,
        200,
        "User-agent: *\nDisallow: /\n\nUser-agent: ExampleBot\nAllow: /\n",
        content_type="text/plain",
    )
    with mock.patch.object(verifier, "fetch", side_effect=lambda url, **_kwargs: responses[url]):
        result = verifier.validate_base_url("https://private.example", timeout=1)
    assert result["ok"] is False
    assert any("ExampleBot" in failure or "examplebot" in failure for failure in result["failures"])


def test_live_gate_rejects_anonymous_internal_api_data() -> None:
    responses = _passing_live_responses()
    path = verifier.DEFAULT_PROTECTED_API_PATHS[0]
    url = f"https://private.example/{path.lstrip('/')}"
    responses[url] = _http_result(
        url,
        200,
        '{"items":[{"kol_pool_id":1,"project_id":2}]}',
        content_type="application/json",
    )
    with mock.patch.object(verifier, "fetch", side_effect=lambda request_url, **_kwargs: responses[request_url]):
        result = verifier.validate_base_url("https://private.example", timeout=1)
    assert result["ok"] is False
    assert any("anonymous protected API" in failure for failure in result["failures"])


def test_live_gate_requires_noindex_on_static_and_error_responses() -> None:
    responses = _passing_live_responses()
    asset_url = "https://private.example/assets/app.js"
    responses[asset_url] = _http_result(
        asset_url,
        200,
        "console.log('loaded')",
        content_type="application/javascript",
        x_robots_tag="",
    )
    with mock.patch.object(verifier, "fetch", side_effect=lambda url, **_kwargs: responses[url]):
        result = verifier.validate_base_url("https://private.example", timeout=1)
    assert result["ok"] is False
    assert any("static asset X-Robots-Tag missing" in failure for failure in result["failures"])


def test_live_gate_rejects_conflicting_robot_headers() -> None:
    responses = _passing_live_responses()
    base = "https://private.example/"
    responses[base] = _http_result(
        base,
        200,
        PRIVATE_HTML,
        content_type="text/html",
        x_robots_tag=f"{ROBOTS_POLICY}, index, follow",
    )
    with mock.patch.object(verifier, "fetch", side_effect=lambda url, **_kwargs: responses[url]):
        result = verifier.validate_base_url("https://private.example", timeout=1)
    assert result["ok"] is False
    assert any("root X-Robots-Tag conflicts: follow, index" in failure for failure in result["failures"])


def test_cloud_deploy_runs_external_private_surface_gate() -> None:
    deploy = (REPO_ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh").read_text(encoding="utf-8")
    assert "verify_private_surface_live.py" in deploy
    assert "https://www.viltroxtest.com" in deploy
    assert 'VKPI_EXPECT_ACCESS_GATED="${VKPI_EXPECT_ACCESS_GATED:-0}"' in deploy
    assert "VKPI_EXPECT_ACCESS_GATED must be exactly 0 or 1." in deploy
    assert 'if [ "${VKPI_EXPECT_ACCESS_GATED}" = "1" ]; then' in deploy
    assert '--expect-access-gated "${PRIVATE_SURFACE_URL_LIST[@]}"' in deploy
    assert '"${PRIVATE_SURFACE_URL_LIST[@]}"' in deploy
    assert "PRIVATE_SURFACE_GATE_ARGS" not in deploy


def test_internal_uploads_are_not_anonymous_static_files() -> None:
    client = TestClient(main.app, raise_server_exceptions=False)
    evidence = client.get("/uploads/vkpi_evidence/not-present.pdf")
    avatar = client.get("/uploads/staff_avatars/not-present.png")
    assert evidence.status_code == 403
    assert avatar.status_code == 403
