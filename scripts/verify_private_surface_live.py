#!/usr/bin/env python3
"""Validate a deployed private workspace without sending credentials."""

from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


SAFE_TITLE = "Authorized Workspace"
SAFE_DESCRIPTION = "Private workspace. Sign-in required."
REQUIRED_ROBOT_TOKENS = {"noindex", "nofollow", "noarchive", "nosnippet", "noimageindex"}
CONFLICTING_ROBOT_TOKENS = {"all", "archive", "follow", "imageindex", "index", "snippet"}
SAFE_DENIAL_STATUSES = {401, 403, 404}
DEFAULT_PROTECTED_API_PATHS = (
    "/api/admin/vkpi/kols?limit=1",
    "/api/admin/vkpi/projects?limit=1",
)
FORBIDDEN_SHELL_TEXT = {
    "viltrox marketing",
    "kol pool",
    "my kol",
    "internal marketing tool",
    "internal marketing management",
    "Viltrox Marketing 内部红人、项目、短链、归因和报表管理系统",
    "内部红人、项目、短链、归因和报表管理系统",
}
DENIAL_LEAK_MARKERS = {
    "api_key",
    "campaign_name",
    "contact_email",
    "creator_id",
    "database_url",
    "jwt_secret",
    "kol_pool_id",
    "owner_username",
    "postgresql://",
    "project_id",
    "redis://",
    "staff_id",
    "traceback (most recent call last)",
}


@dataclass(frozen=True)
class HttpResult:
    status: int
    url: str
    headers: Mapping[str, str]
    body: str


class HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.asset_urls: list[str] = []
        self.root_has_data_nosnippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        values = {str(key).lower(): value or "" for key, value in attrs}
        if tag_name == "title":
            self.in_title = True
        elif tag_name == "meta":
            name = values.get("name", "").strip().lower()
            if name:
                self.meta[name] = values.get("content", "").strip()
        elif values.get("id") == "root" and "data-nosnippet" in values:
            self.root_has_data_nosnippet = True

        asset_url = values.get("src", "") if tag_name == "script" else values.get("href", "")
        if asset_url:
            self.asset_urls.append(asset_url.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return "".join(self.title_parts).strip()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _normalized_headers(headers) -> dict[str, str]:  # noqa: ANN001
    normalized: dict[str, str] = {}
    if headers is None:
        return normalized
    for key in headers.keys():
        values = headers.get_all(key) or [headers.get(key, "")]
        normalized[str(key).lower()] = ", ".join(str(value) for value in values if value is not None)
    return normalized


def fetch(url: str, *, timeout: float, follow_redirects: bool = True) -> HttpResult:
    request = Request(url, headers={"User-Agent": "Private-Surface-Release-Gate/2.0"})
    open_request = urlopen if follow_redirects else build_opener(_NoRedirect()).open
    try:
        with open_request(request, timeout=timeout) as response:  # noqa: S310 - explicit release URL
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return HttpResult(
                status=int(response.status),
                url=str(response.url),
                headers=_normalized_headers(response.headers),
                body=raw.decode(charset, errors="replace"),
            )
    except HTTPError as exc:
        raw = exc.read()
        charset = exc.headers.get_content_charset() if exc.headers is not None else None
        return HttpResult(
            status=int(exc.code),
            url=str(exc.url),
            headers=_normalized_headers(exc.headers),
            body=raw.decode(charset or "utf-8", errors="replace"),
        )
    except URLError as exc:
        raise RuntimeError(f"request failed for {url}: {exc.reason}") from exc


def _robot_tokens(value: str) -> set[str]:
    return {token.strip().lower() for token in value.split(",") if token.strip()}


def _robots_groups(text: str) -> list[tuple[set[str], list[tuple[str, str]]]]:
    groups: list[tuple[set[str], list[tuple[str, str]]]] = []
    agents: set[str] = set()
    directives: list[tuple[str, str]] = []

    def commit() -> None:
        nonlocal agents, directives
        if agents:
            groups.append((set(agents), list(directives)))
        agents = set()
        directives = []

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            commit()
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip().lower()
        value = value.strip().lower()
        if key == "user-agent":
            if directives:
                commit()
            agents.add(value)
        elif agents:
            directives.append((key, value))
    commit()
    return groups


def _deny_all_robots_failures(text: str) -> list[str]:
    failures: list[str] = []
    groups = _robots_groups(text)
    if not groups:
        return ["robots.txt has no user-agent group"]
    if not any("*" in agents for agents, _directives in groups):
        failures.append("robots.txt has no wildcard user-agent group")
    for agents, directives in groups:
        label = ", ".join(sorted(agents)) or "<empty>"
        if ("disallow", "/") not in directives:
            failures.append(f"robots.txt group {label} does not deny all paths")
        if any(key == "allow" and value for key, value in directives):
            failures.append(f"robots.txt group {label} contains an Allow directive")
        if any(key == "sitemap" for key, _value in directives):
            failures.append(f"robots.txt group {label} advertises a sitemap")
    return failures


def _require_robots_header(result: HttpResult, *, label: str, failures: list[str]) -> None:
    tokens = _robot_tokens(result.headers.get("x-robots-tag", ""))
    missing = sorted(REQUIRED_ROBOT_TOKENS - tokens)
    if missing:
        failures.append(f"{label} X-Robots-Tag missing: {', '.join(missing)}")
    conflicts = sorted(CONFLICTING_ROBOT_TOKENS & tokens)
    if conflicts:
        failures.append(f"{label} X-Robots-Tag conflicts: {', '.join(conflicts)}")


def _shell_leaks(body: str) -> list[str]:
    lowered = body.lower()
    return sorted(text for text in FORBIDDEN_SHELL_TEXT if text.lower() in lowered)


def _denial_leaks(body: str) -> list[str]:
    lowered = body.lower()
    return sorted(marker for marker in DENIAL_LEAK_MARKERS if marker in lowered)


def _validate_html_shell(result: HttpResult, *, label: str, failures: list[str]) -> HeadParser:
    parser = HeadParser()
    parser.feed(result.body)
    if result.status != 200:
        failures.append(f"{label} returned HTTP {result.status}")
        return parser
    if "text/html" not in result.headers.get("content-type", "").lower():
        failures.append(f"{label} did not return HTML")
    if parser.title != SAFE_TITLE:
        failures.append(f"{label} title must be {SAFE_TITLE!r}")
    if parser.meta.get("description", "") != SAFE_DESCRIPTION:
        failures.append(f"{label} description must be the generic private-workspace description")
    for name in ("robots", "googlebot", "bingbot"):
        tokens = _robot_tokens(parser.meta.get(name, ""))
        missing = sorted(REQUIRED_ROBOT_TOKENS - tokens)
        if missing:
            failures.append(f"{label} meta {name} missing: {', '.join(missing)}")
        conflicts = sorted(CONFLICTING_ROBOT_TOKENS & tokens)
        if conflicts:
            failures.append(f"{label} meta {name} conflicts: {', '.join(conflicts)}")
    if not parser.root_has_data_nosnippet:
        failures.append(f"{label} SPA root is missing data-nosnippet")
    for leaked_text in _shell_leaks(result.body):
        failures.append(f"{label} contains forbidden public text: {leaked_text}")
    return parser


def _same_origin_url(base: str, path_or_url: str) -> str:
    candidate = urljoin(base, path_or_url)
    base_parts = urlsplit(base)
    candidate_parts = urlsplit(candidate)
    if candidate_parts.scheme not in {"http", "https"} or candidate_parts.netloc != base_parts.netloc:
        raise ValueError(f"probe path must stay on origin {base_parts.netloc}: {path_or_url}")
    return candidate


def validate_base_url(
    base_url: str,
    *,
    timeout: float,
    protected_api_paths: Sequence[str] = DEFAULT_PROTECTED_API_PATHS,
) -> dict[str, object]:
    base = base_url.rstrip("/") + "/"
    base_parts = urlsplit(base)
    if base_parts.scheme not in {"http", "https"} or not base_parts.netloc:
        raise ValueError(f"base URL must be an absolute HTTP(S) origin: {base_url}")

    failures: list[str] = []
    root = fetch(base, timeout=timeout)
    if urlsplit(root.url).netloc != base_parts.netloc:
        failures.append(f"root redirected off-origin to {root.url}")
    _require_robots_header(root, label="root", failures=failures)
    shell = _validate_html_shell(root, label="root", failures=failures)

    robots = fetch(_same_origin_url(base, "/robots.txt"), timeout=timeout, follow_redirects=False)
    _require_robots_header(robots, label="robots.txt", failures=failures)
    if robots.status != 200:
        failures.append(f"robots.txt returned HTTP {robots.status}")
    if "text/plain" not in robots.headers.get("content-type", "").lower():
        failures.append("robots.txt did not return text/plain")
    failures.extend(_deny_all_robots_failures(robots.body))

    asset_path = next(
        (
            candidate
            for candidate in shell.asset_urls
            if urlsplit(urljoin(base, candidate)).path.startswith("/assets/")
        ),
        "/assets/__private_surface_probe_missing__.js",
    )
    asset_is_declared = asset_path != "/assets/__private_surface_probe_missing__.js"
    asset = fetch(_same_origin_url(base, asset_path), timeout=timeout, follow_redirects=False)
    _require_robots_header(asset, label="static asset", failures=failures)
    expected_asset_statuses = {200} if asset_is_declared else {404}
    if asset.status not in expected_asset_statuses:
        expected = ", ".join(str(status) for status in sorted(expected_asset_statuses))
        failures.append(f"static asset probe returned HTTP {asset.status}; expected {expected}")
    for marker in _denial_leaks(asset.body) if asset.status >= 400 else []:
        failures.append(f"static asset error leaked internal marker: {marker}")

    docs_results: dict[str, HttpResult] = {}
    for path in ("/docs", "/redoc", "/openapi.json"):
        result = fetch(_same_origin_url(base, path), timeout=timeout, follow_redirects=False)
        docs_results[path] = result
        _require_robots_header(result, label=path, failures=failures)
        if result.status not in SAFE_DENIAL_STATUSES:
            failures.append(f"{path} is not closed; returned HTTP {result.status}")
        for marker in _denial_leaks(result.body):
            failures.append(f"{path} error leaked internal marker: {marker}")

    api_results: dict[str, HttpResult] = {}
    for path in protected_api_paths:
        result = fetch(_same_origin_url(base, path), timeout=timeout, follow_redirects=False)
        api_results[path] = result
        _require_robots_header(result, label=path, failures=failures)
        if result.status not in SAFE_DENIAL_STATUSES:
            failures.append(f"anonymous protected API {path} returned HTTP {result.status}")
        if len(result.body.encode("utf-8")) > 16_384:
            failures.append(f"anonymous protected API {path} returned an oversized denial body")
        for marker in _denial_leaks(result.body):
            failures.append(f"anonymous protected API {path} leaked internal marker: {marker}")

    return {
        "base_url": base,
        "resolved_url": root.url,
        "status": root.status,
        "title": shell.title,
        "description": shell.meta.get("description", ""),
        "x_robots_tag": root.headers.get("x-robots-tag", ""),
        "robots_status": robots.status,
        "static_probe": {"path": asset_path, "status": asset.status},
        "docs_status": docs_results["/docs"].status,
        "redoc_status": docs_results["/redoc"].status,
        "openapi_status": docs_results["/openapi.json"].status,
        "protected_api_statuses": {path: result.status for path, result in api_results.items()},
        "ok": not failures,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_urls", nargs="+", help="Public origins to validate after deployment.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--protected-api-path",
        action="append",
        dest="protected_api_paths",
        help="Origin-relative internal API path to probe anonymously; repeat as needed.",
    )
    args = parser.parse_args()
    protected_api_paths = tuple(args.protected_api_paths or DEFAULT_PROTECTED_API_PATHS)

    results: list[dict[str, object]] = []
    for base_url in args.base_urls:
        try:
            results.append(
                validate_base_url(
                    base_url,
                    timeout=args.timeout,
                    protected_api_paths=protected_api_paths,
                )
            )
        except Exception as exc:  # release gate must report every requested origin
            results.append({"base_url": base_url, "ok": False, "failures": [str(exc)]})

    stdout_out(json.dumps({"gate": "private_surface_live", "results": results}, ensure_ascii=False, indent=2))
    return 0 if all(bool(result.get("ok")) for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
