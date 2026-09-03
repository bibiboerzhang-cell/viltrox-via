"""S-03 / S-04 SSRF 修复验收:app.platform.safe_fetch + image-proxy / video-proxy + 联系方式抓取腿。

覆盖(全部离线,DNS / 传输层走模块级测试缝,零真实网络):
- 只许 https:http:// / file:// / data: / 带 userinfo 一律拒,且在 DNS 之前就拒;
- DNS 解析后任一地址落私网 / 回环 / 链路本地 / 云元数据 / IPv6 过渡段即拒,连接根本不建;
- 连接钉在校验过的地址,连上后核对端,对端私网立刻关掉;
- 任何 3xx 第一跳截停(白名单 CDN 上的开放重定向打不到内网),第二个请求不会发出;
- 大小 / 超时上限;正常 https 200 走通;
- image-proxy:http 400、重定向 -> 透明占位且不写缓存;
- video-proxy:重定向 502、非视频体 502、视频体 200 流回;
- contact_website_scrape._fetch:http 升级 https、私网 / 重定向进错误台账、超 500KB 截断。
"""
from __future__ import annotations

import asyncio
import email.message
import io
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.api.routers import media as media_router  # noqa: E402
from app.domains.kol import contact_website_scrape as cws  # noqa: E402
from app.platform import safe_fetch  # noqa: E402

PUBLIC_IP = "93.184.216.34"
PUBLIC_IP6 = "2606:4700::1111"


# ----------------------------------------------------------------- 假传输层(HTTPConnection API)


class _FakeResponse:
    """够 urllib OpenerDirector / HTTPErrorProcessor 用的最小响应对象。"""

    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.code = status
        self.reason = {200: "OK", 206: "Partial Content", 302: "Found", 404: "Not Found"}.get(status, "X")
        self.msg = self.reason
        self.headers = email.message.Message()
        for key, value in headers.items():
            self.headers[key] = value
        self._body = io.BytesIO(body)
        self.url = ""
        self.closed = False

    def info(self) -> Any:
        return self.headers

    def geturl(self) -> str:
        return self.url

    def getheader(self, name: str, default: Any = None) -> Any:
        return self.headers.get(name, default)

    def read(self, amt: int = -1) -> bytes:
        return self._body.read(amt)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


class _FakeConnection:
    """替代 _PinnedHTTPSConnection:记录请求、返回预置响应,不碰 socket。"""

    calls: list[dict[str, Any]] = []

    def __init__(self, host: str, response: _FakeResponse, **kwargs: Any) -> None:
        self.host = host
        self.kwargs = kwargs
        self.sock = None
        self._response = response

    def set_debuglevel(self, _level: int) -> None:
        return None

    def request(self, method: str, selector: str, body: Any, headers: dict[str, str], **_kw: Any) -> None:
        _FakeConnection.calls.append({"host": self.host, "method": method, "selector": selector, "headers": dict(headers)})

    def getresponse(self) -> _FakeResponse:
        return self._response

    def close(self) -> None:
        return None


@pytest.fixture
def offline(monkeypatch):
    """把 DNS / 代理 / 传输层全部换成离线缝;返回一个 `arm(status, headers, body)` 装弹函数。"""
    _FakeConnection.calls = []
    monkeypatch.setattr(safe_fetch, "_proxy_for", lambda _host: "")
    monkeypatch.setattr(safe_fetch, "_resolve_addresses", lambda host, port: (PUBLIC_IP,))
    state: dict[str, Any] = {}

    def factory(host: str, target: safe_fetch.SafeTarget, **kwargs: Any) -> _FakeConnection:
        state["target"] = target
        state["kwargs"] = kwargs
        return _FakeConnection(host, state["response"], **kwargs)

    monkeypatch.setattr(safe_fetch, "_connection_factory", factory)

    def arm(status: int, headers: dict[str, str], body: bytes = b"") -> _FakeResponse:
        state["response"] = _FakeResponse(status, headers, body)
        return state["response"]

    state["arm"] = arm
    return state


# ----------------------------------------------------------------- 地址策略


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "127.8.8.8", "10.0.0.5", "172.16.31.9", "192.168.1.1", "169.254.169.254",
        "100.64.1.1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "::1", "::", "fc00::1", "fd12::1",
        "fe80::1", "fe80::1%en0", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "2002:7f00:1::",
        "64:ff9b::7f00:1", "2001::1", "ff02::1", "localhost", "", "not-an-ip",
    ],
)
def test_non_public_addresses_are_rejected(address: str) -> None:
    assert safe_fetch.is_public_address(address) is False


@pytest.mark.parametrize("address", [PUBLIC_IP, "8.8.8.8", "1.1.1.1", PUBLIC_IP6, "2a03:2880:f10c:83:face:b00c::25de", "::ffff:8.8.8.8"])
def test_public_addresses_are_allowed(address: str) -> None:
    assert safe_fetch.is_public_address(address) is True


# ----------------------------------------------------------------- validate_url


def _no_dns(monkeypatch) -> None:
    def _must_not_resolve(host: str, port: int) -> tuple[str, ...]:
        raise AssertionError(f"DNS must not be consulted for {host}")

    monkeypatch.setattr(safe_fetch, "_resolve_addresses", _must_not_resolve)


@pytest.mark.parametrize(
    "url, reason",
    [
        ("http://i.ytimg.com/vi/x/hq.jpg", "scheme_not_https"),
        ("HTTP://i.ytimg.com/vi/x/hq.jpg", "scheme_not_https"),
        ("file:///etc/passwd", "scheme_not_https"),
        ("ftp://example.com/x", "scheme_not_https"),
        ("data:text/plain,hi", "scheme_not_https"),
        ("//example.com/x", "scheme_not_https"),
        ("", "scheme_not_https"),
        ("https://user:pw@example.com/x", "userinfo_forbidden"),
        ("https://user@example.com/x", "userinfo_forbidden"),
        ("https:///x", "host_missing"),
        ("https://example.com:99999/x", "port_invalid"),
        ("https://[::1/x", "url_invalid"),
    ],
)
def test_validate_url_rejects_before_dns(monkeypatch, url: str, reason: str) -> None:
    _no_dns(monkeypatch)
    with pytest.raises(safe_fetch.SafeFetchBlocked) as info:
        safe_fetch.validate_url(url)
    assert info.value.reason == reason


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8102/health",
        "https://[::1]:8101/",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.5/",
        "https://[::ffff:10.0.0.5]/",
        "https://[fc00::1]/",
        "https://[fe80::1]/",
    ],
)
def test_validate_url_rejects_private_ip_literals_without_dns(monkeypatch, url: str) -> None:
    _no_dns(monkeypatch)  # 字面量 IP 不查 DNS,直接按地址策略拒
    with pytest.raises(safe_fetch.SafeFetchBlocked) as info:
        safe_fetch.validate_url(url)
    assert info.value.reason == "private_address"


@pytest.mark.parametrize(
    "resolved",
    [("127.0.0.1",), ("10.9.9.9",), ("169.254.169.254",), ("::1",), (PUBLIC_IP, "10.0.0.1"), ("fd00::1", PUBLIC_IP6)],
)
def test_validate_url_rejects_hostnames_resolving_to_private_space(monkeypatch, resolved: tuple[str, ...]) -> None:
    # 任一地址私网即拒(混合应答也拒),攻击者控制的域名 / localhost / 内网别名全在这一刀。
    monkeypatch.setattr(safe_fetch, "_resolve_addresses", lambda host, port: resolved)
    with pytest.raises(safe_fetch.SafeFetchBlocked) as info:
        safe_fetch.validate_url("https://cdn.attacker.example/redirect")
    assert info.value.reason == "private_address"


def test_validate_url_accepts_public_https_and_pins_addresses(monkeypatch) -> None:
    monkeypatch.setattr(safe_fetch, "_resolve_addresses", lambda host, port: (PUBLIC_IP, PUBLIC_IP6))
    target = safe_fetch.validate_url("https://scontent.cdninstagram.com:443/x.jpg?sig=abc")
    assert target.host == "scontent.cdninstagram.com"
    assert target.port == 443
    assert target.addresses == (PUBLIC_IP, PUBLIC_IP6)
    assert target.url == "https://scontent.cdninstagram.com:443/x.jpg?sig=abc"


def test_dns_failure_is_reported_as_safe_fetch_error(monkeypatch) -> None:
    def _fail(host: str, port: int) -> tuple[str, ...]:
        raise safe_fetch.SafeFetchError("dns_failed", host)

    monkeypatch.setattr(safe_fetch, "_resolve_addresses", _fail)
    with pytest.raises(safe_fetch.SafeFetchError) as info:
        safe_fetch.validate_url("https://nxdomain.example/")
    assert info.value.reason == "dns_failed"
    assert not isinstance(info.value, safe_fetch.SafeFetchBlocked)


def test_timeout_is_clamped_to_bounds() -> None:
    assert safe_fetch.clamp_timeout(0) == safe_fetch.MIN_TIMEOUT_SEC
    assert safe_fetch.clamp_timeout(-5) == safe_fetch.MIN_TIMEOUT_SEC
    assert safe_fetch.clamp_timeout(6) == 6.0
    assert safe_fetch.clamp_timeout(10_000) == safe_fetch.MAX_TIMEOUT_SEC
    assert safe_fetch.clamp_timeout("garbage") == float(safe_fetch.DEFAULT_TIMEOUT_SEC)


# ----------------------------------------------------------------- 钉地址 + 对端核验


class _FakeSocket:
    def __init__(self, peer: str) -> None:
        self._peer = peer
        self.closed = False

    def getpeername(self) -> tuple[str, int]:
        return (self._peer, 443)

    def close(self) -> None:
        self.closed = True


def test_pinned_connection_connects_to_validated_address_not_hostname(monkeypatch) -> None:
    dialed: list[tuple[str, int]] = []

    def fake_create_connection(address: tuple[str, int], timeout: Any = None, source_address: Any = None) -> _FakeSocket:
        dialed.append(address)
        return _FakeSocket(address[0])

    monkeypatch.setattr(safe_fetch.socket, "create_connection", fake_create_connection)
    conn = safe_fetch._PinnedHTTPSConnection("cdn.example", addresses=(PUBLIC_IP,), timeout=3)
    sock = conn._pinned_create_connection(("cdn.example", 443), 3, None)
    assert dialed == [(PUBLIC_IP, 443)]  # 用的是校验过的地址,不是 hostname(无第二次 DNS)
    assert isinstance(sock, _FakeSocket) and not sock.closed


def test_pinned_connection_closes_socket_when_peer_is_private(monkeypatch) -> None:
    created: list[_FakeSocket] = []

    def fake_create_connection(address: tuple[str, int], timeout: Any = None, source_address: Any = None) -> _FakeSocket:
        sock = _FakeSocket("10.0.0.7")  # 模拟对端实际落在私网(rebinding / 劫持)
        created.append(sock)
        return sock

    monkeypatch.setattr(safe_fetch.socket, "create_connection", fake_create_connection)
    conn = safe_fetch._PinnedHTTPSConnection("cdn.example", addresses=(PUBLIC_IP,), timeout=3)
    with pytest.raises(safe_fetch.SafeFetchBlocked) as info:
        conn._pinned_create_connection(("cdn.example", 443), 3, None)
    assert info.value.reason == "private_address"
    assert created and created[0].closed is True


def test_pinned_connection_falls_back_across_validated_addresses(monkeypatch) -> None:
    attempts: list[str] = []

    def fake_create_connection(address: tuple[str, int], timeout: Any = None, source_address: Any = None) -> _FakeSocket:
        attempts.append(address[0])
        if address[0] == PUBLIC_IP:
            raise OSError("first address down")
        return _FakeSocket(address[0])

    monkeypatch.setattr(safe_fetch.socket, "create_connection", fake_create_connection)
    conn = safe_fetch._PinnedHTTPSConnection("cdn.example", addresses=(PUBLIC_IP, "8.8.8.8"), timeout=3)
    conn._pinned_create_connection(("cdn.example", 443), 3, None)
    assert attempts == [PUBLIC_IP, "8.8.8.8"]


# ----------------------------------------------------------------- opener 装配 + 重定向截停


def test_opener_has_no_redirect_follower_and_no_non_https_handlers(monkeypatch) -> None:
    monkeypatch.setattr(safe_fetch, "_proxy_for", lambda _host: "")
    target = safe_fetch.SafeTarget(url="https://cdn.example/x", host="cdn.example", port=443, addresses=(PUBLIC_IP,))
    handlers = safe_fetch._build_opener(target).handlers
    redirecters = [h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert redirecters and all(isinstance(h, safe_fetch._NoRedirectHandler) for h in redirecters)
    banned = (urllib.request.HTTPHandler, urllib.request.FileHandler, urllib.request.FTPHandler, urllib.request.DataHandler)
    assert not any(isinstance(h, banned) for h in handlers)
    assert not any(isinstance(h, urllib.request.ProxyHandler) for h in handlers)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_to_internal_host_is_blocked_at_first_hop(offline, status: int) -> None:
    # 白名单 CDN 上的开放重定向 -> 云元数据地址:第一跳就截停,第二个请求不会发出。
    response = offline["arm"](status, {"Location": "https://169.254.169.254/latest/meta-data/"})
    response.reason = "Moved"
    with pytest.raises(safe_fetch.SafeFetchBlocked) as info:
        safe_fetch.open_url("https://scontent.cdninstagram.com/open-redirect?u=x", timeout=3)
    assert info.value.reason == "redirect_blocked"
    assert "169.254.169.254" in info.value.detail
    assert len(_FakeConnection.calls) == 1  # 只发出了对白名单 CDN 的第一跳
    assert response.closed is True


def test_redirect_to_http_downgrade_is_blocked_too(offline) -> None:
    offline["arm"](302, {"Location": "http://scontent.cdninstagram.com/plain"})
    with pytest.raises(safe_fetch.SafeFetchBlocked) as info:
        safe_fetch.open_url("https://scontent.cdninstagram.com/x", timeout=3)
    assert info.value.reason == "redirect_blocked"
    assert len(_FakeConnection.calls) == 1


def test_redirect_request_never_builds_a_follow_up_request() -> None:
    handler = safe_fetch._NoRedirectHandler()
    req = urllib.request.Request("https://cdn.example/x")
    with pytest.raises(safe_fetch.SafeFetchBlocked):
        handler.redirect_request(req, None, 302, "Found", {}, "https://127.0.0.1/")


def test_upstream_4xx_surfaces_as_http_error_with_code(offline) -> None:
    offline["arm"](404, {"Content-Type": "text/plain"}, b"nope")
    with pytest.raises(urllib.error.HTTPError) as info:
        safe_fetch.open_url("https://scontent.cdninstagram.com/missing.jpg", timeout=3)
    assert info.value.code == 404


# ----------------------------------------------------------------- 正常路径 + 大小上限


def test_plain_https_fetch_passes_through(offline) -> None:
    offline["arm"](200, {"Content-Type": "image/jpeg; charset=binary", "X-Upstream": "1"}, b"\xff\xd8jpegbytes")
    result = safe_fetch.fetch_bytes(
        "https://scontent.cdninstagram.com/x.jpg", headers={"Accept": "image/*"}, timeout=4, max_bytes=1024
    )
    assert result.status == 200
    assert result.content_type == "image/jpeg"
    assert result.data == b"\xff\xd8jpegbytes"
    assert result.truncated is False
    assert result.headers["x-upstream"] == "1"
    call = _FakeConnection.calls[0]
    assert call["method"] == "GET" and call["selector"] == "/x.jpg"
    assert call["headers"]["Accept"] == "image/*"
    assert call["headers"]["Connection"] == "close"
    assert offline["target"].addresses == (PUBLIC_IP,)
    assert offline["kwargs"]["timeout"] == 4.0


def test_fetch_bytes_rejects_oversized_body(offline) -> None:
    offline["arm"](200, {"Content-Type": "image/png"}, b"x" * 5000)
    with pytest.raises(safe_fetch.SafeFetchTooLarge):
        safe_fetch.fetch_bytes("https://scontent.cdninstagram.com/big.png", timeout=3, max_bytes=4096)


def test_fetch_bytes_can_truncate_instead_of_failing(offline) -> None:
    offline["arm"](200, {"Content-Type": "text/html"}, b"y" * 5000)
    result = safe_fetch.fetch_bytes("https://kol.example/", timeout=3, max_bytes=4096, truncate=True)
    assert len(result.data) == 4096
    assert result.truncated is True


def test_read_capped_exact_limit_is_not_truncated() -> None:
    response = _FakeResponse(200, {}, b"z" * 4096)
    data, truncated = safe_fetch.read_capped(response, 4096)
    assert len(data) == 4096 and truncated is False


def test_open_url_never_connects_when_policy_rejects(monkeypatch) -> None:
    def _must_not_connect(host: str, target: Any, **kwargs: Any) -> Any:
        raise AssertionError("transport must not be touched")

    monkeypatch.setattr(safe_fetch, "_connection_factory", _must_not_connect)
    monkeypatch.setattr(safe_fetch, "_proxy_for", lambda _host: "")
    monkeypatch.setattr(safe_fetch, "_resolve_addresses", lambda host, port: ("127.0.0.1",))
    with pytest.raises(safe_fetch.SafeFetchBlocked):
        safe_fetch.open_url("https://evil.example/", timeout=3)
    with pytest.raises(safe_fetch.SafeFetchBlocked):
        safe_fetch.open_url("http://scontent.cdninstagram.com/x.jpg", timeout=3)


# ----------------------------------------------------------------- image-proxy


@pytest.mark.parametrize(
    "url",
    ["http://i.ytimg.com/vi/abc/hq.jpg", "http://scontent.cdninstagram.com/x.jpg", "ftp://i.ytimg.com/x"],
)
def test_image_proxy_rejects_non_https_urls(url: str) -> None:
    with pytest.raises(HTTPException) as info:
        media_router._allowed_external_image_url(url)
    assert info.value.status_code == 400


def test_image_proxy_still_accepts_https_allowlisted_urls() -> None:
    normalized, host = media_router._allowed_external_image_url("https://i.ytimg.com/vi/abc/hq.jpg")
    assert normalized == "https://i.ytimg.com/vi/abc/hq.jpg" and host == "i.ytimg.com"


def test_image_proxy_redirect_to_internal_yields_placeholder_and_no_cache(offline, tmp_path, monkeypatch) -> None:
    offline["arm"](302, {"Location": "https://127.0.0.1:8102/health"})
    monkeypatch.setattr(media_router, "VKPI_IMAGE_PROXY_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(media_router, "get_current_user", lambda _request: {"id": 1})
    monkeypatch.setattr(media_router, "release_validation_active", lambda: False)

    response = media_router.serve_vkpi_external_image(object(), "https://scontent.cdninstagram.com/open-redirect")

    assert response.headers["x-vkpi-media-fallback"] == "upstream_unavailable"
    assert response.headers["cache-control"] == "no-store"
    assert response.body == media_router._TRANSPARENT_IMAGE_SVG
    assert not any((tmp_path / "cache").glob("*")) if (tmp_path / "cache").exists() else True
    assert len(_FakeConnection.calls) == 1  # 没有重试、更没有第二跳


def test_image_proxy_private_target_is_blocked_without_retry(offline, monkeypatch) -> None:
    monkeypatch.setattr(safe_fetch, "_resolve_addresses", lambda host, port: ("10.0.0.9",))
    data, content_type, ok = media_router._fetch_external_image("https://scontent.cdninstagram.com/x.jpg", "scontent.cdninstagram.com")
    assert ok is False and content_type == "image/svg+xml" and data == media_router._TRANSPARENT_IMAGE_SVG
    assert _FakeConnection.calls == []


def test_image_proxy_plain_https_image_passes(offline) -> None:
    offline["arm"](200, {"Content-Type": "image/webp"}, b"RIFFwebp")
    data, content_type, ok = media_router._fetch_external_image("https://scontent.cdninstagram.com/x.webp", "scontent.cdninstagram.com")
    assert ok is True and content_type == "image/webp" and data == b"RIFFwebp"


def test_image_proxy_non_image_body_is_502(offline) -> None:
    offline["arm"](200, {"Content-Type": "text/html"}, b"<html>")
    with pytest.raises(HTTPException) as info:
        media_router._fetch_external_image("https://scontent.cdninstagram.com/x.jpg", "scontent.cdninstagram.com")
    assert info.value.status_code == 502


# ----------------------------------------------------------------- video-proxy


class _Req:
    def __init__(self, range_header: str = "") -> None:
        self.headers = {"range": range_header} if range_header else {}


def test_video_proxy_rejects_http_url() -> None:
    with pytest.raises(HTTPException) as info:
        media_router._allowed_external_video_url("http://scontent.cdninstagram.com/v.mp4")
    assert info.value.status_code == 400


def test_video_proxy_redirect_is_502_not_followed(offline, monkeypatch) -> None:
    offline["arm"](302, {"Location": "https://169.254.169.254/"})
    monkeypatch.setattr(media_router, "get_current_user", lambda _request: {"id": 1})
    with pytest.raises(HTTPException) as info:
        media_router.proxy_vkpi_external_video(_Req(), "https://scontent.cdninstagram.com/v.mp4")
    assert info.value.status_code == 502
    assert len(_FakeConnection.calls) == 1


def test_video_proxy_refuses_non_video_body(offline, monkeypatch) -> None:
    response = offline["arm"](200, {"Content-Type": "text/html; charset=utf-8"}, b"<html>internal</html>")
    monkeypatch.setattr(media_router, "get_current_user", lambda _request: {"id": 1})
    with pytest.raises(HTTPException) as info:
        media_router.proxy_vkpi_external_video(_Req(), "https://scontent.cdninstagram.com/v.mp4")
    assert info.value.status_code == 502
    assert response.closed is True


async def _drain(body_iterator: Any) -> bytes:
    """StreamingResponse 把同步生成器包成线程池异步迭代器,只能异步消费。"""
    return b"".join([chunk async for chunk in body_iterator])


def test_video_proxy_streams_video_body(offline, monkeypatch) -> None:
    response = offline["arm"](206, {"Content-Type": "video/mp4", "Content-Range": "bytes 0-3/4", "Content-Length": "4"}, b"mp4!")
    monkeypatch.setattr(media_router, "get_current_user", lambda _request: {"id": 1})
    streamed = media_router.proxy_vkpi_external_video(_Req("bytes=0-3"), "https://scontent.cdninstagram.com/v.mp4")
    assert streamed.status_code == 206
    assert streamed.media_type == "video/mp4"
    assert streamed.headers["content-range"] == "bytes 0-3/4"
    assert asyncio.run(_drain(streamed.body_iterator)) == b"mp4!"
    assert response.closed is True  # 流完关上游
    assert _FakeConnection.calls[0]["headers"]["Range"] == "bytes=0-3"


# ----------------------------------------------------------------- 联系方式抓取腿


def test_scrape_fetch_upgrades_http_to_https(offline) -> None:
    offline["arm"](200, {"Content-Type": "text/html"}, b"<a href='mailto:hi@kol.example'>mail</a>")
    cws.pop_fetch_errors()
    html = cws._fetch("http://kol.example/contact", timeout=3)
    assert "mailto:hi@kol.example" in html
    assert offline["target"].url == "https://kol.example/contact"
    assert cws.pop_fetch_errors() == []


def test_scrape_fetch_private_target_goes_to_error_ledger(monkeypatch) -> None:
    monkeypatch.setattr(safe_fetch, "_proxy_for", lambda _host: "")
    monkeypatch.setattr(safe_fetch, "_resolve_addresses", lambda host, port: ("127.0.0.1",))
    monkeypatch.setattr(safe_fetch, "_connection_factory", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no connect")))
    cws.pop_fetch_errors()
    assert cws._fetch("https://bio-link.attacker.example/", timeout=3) == ""
    errors = cws.pop_fetch_errors()
    assert len(errors) == 1
    assert errors[0].startswith("SafeFetchBlocked: private_address") and "bio-link.attacker.example" in errors[0]


def test_scrape_fetch_redirect_is_blocked_and_recorded(offline) -> None:
    offline["arm"](302, {"Location": "https://10.0.0.1/admin"})
    cws.pop_fetch_errors()
    assert cws._fetch("https://linktr.ee/someone", timeout=3) == ""
    errors = cws.pop_fetch_errors()
    assert len(errors) == 1 and "redirect_blocked" in errors[0]
    assert len(_FakeConnection.calls) == 1


def test_scrape_fetch_truncates_at_500kb_and_skips_non_html(offline) -> None:
    offline["arm"](200, {"Content-Type": "text/html"}, b"<p>" + b"a" * 600_000)
    assert len(cws._fetch("https://kol.example/", timeout=3)) == cws._MAX_PAGE_BYTES
    offline["arm"](200, {"Content-Type": "application/pdf"}, b"%PDF")
    assert cws._fetch("https://kol.example/media-kit.pdf", timeout=3) == ""
    cws.pop_fetch_errors()
