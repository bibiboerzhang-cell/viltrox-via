"""safe_fetch — 出站 HTTP 抓取的统一安全门(S-03 / S-04 SSRF 修复,2026-09-02)。

背景:image-proxy / video-proxy 只校验 host 后缀、允许 http://、不解析 IP,且 urllib 默认
跟随 3xx —— 白名单 CDN 上任意一个开放重定向就能打到本机 8101/8102 或云厂商元数据地址;
联系方式抓取腿对 KOL bio 里的任意 URL 直接 urlopen,连后缀白名单都没有。

本模块把"能连到哪"收成一条硬规则,纯标准库、零业务依赖,api / domains / services 都可以调:

1. 只许 ``https://``(http / file / ftp / data 一律拒);URL 不许带 ``user:pass@``。
2. 先解析 DNS,任一地址落在私网 / 回环 / 链路本地 / 保留段(RFC1918、127/8、169.254/16、
   ::1、fc00::/7、fe80::/10、IPv4-mapped 与 6to4/NAT64 过渡段等)即拒。直连时把 TCP 连接
   **钉在已校验的地址上**,连上后再核一次对端地址 —— 校验与连接用的是同一份地址,
   DNS rebinding 的 TOCTOU 窗口不存在。TLS 仍按原 host 做 SNI 与证书校验。
3. **禁跟随任何 3xx**:重定向在第一跳截停并抛 ``SafeFetchBlocked(redirect_blocked)``,
   第二个请求永远不会被构造、更不会发出。
4. 超时夹在 [1, 60] 秒;``fetch_bytes`` / ``read_capped`` 带字节上限(超限抛
   ``SafeFetchTooLarge``,或按调用方要求截断)。
5. 环境代理(HTTPS_PROXY / no_proxy)照旧尊重:走代理时由代理去连目标,本机仍先按自己
   视角预校验地址(代理是可信基础设施,不在本机网络内)。

测试缝(都是模块级函数,monkeypatch 即可,生产码不感知测试):
``_resolve_addresses``(DNS)、``_connection_factory``(传输层)、``_proxy_for``(代理判定)。
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

DEFAULT_TIMEOUT_SEC = 10
MIN_TIMEOUT_SEC = 1
MAX_TIMEOUT_SEC = 60
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
READ_CHUNK_BYTES = 128 * 1024
ALLOWED_SCHEMES = ("https",)

# 显式列出的禁连网段(IANA 特殊用途登记 + 过渡机制段),不依赖各 Python 版本 is_private 口径漂移。
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",  # "this" network
        "10.0.0.0/8",  # RFC1918
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local / 云元数据 169.254.169.254
        "172.16.0.0/12",  # RFC1918
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # TEST-NET-1
        "192.88.99.0/24",  # 6to4 relay anycast
        "192.168.0.0/16",  # RFC1918
        "198.18.0.0/15",  # benchmarking
        "198.51.100.0/24",  # TEST-NET-2
        "203.0.113.0/24",  # TEST-NET-3
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved + broadcast
        "::/128",  # unspecified
        "::1/128",  # loopback
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",  # local-use NAT64
        "100::/64",  # discard-only
        "2001::/32",  # Teredo
        "2001:db8::/32",  # documentation
        "2002::/16",  # 6to4
        "fc00::/7",  # unique local
        "fe80::/10",  # link-local
        "ff00::/8",  # multicast
    )
)


class SafeFetchError(Exception):
    """safe_fetch 基类。``reason`` 是稳定的机器可读码,``detail`` 是给日志的短说明(不含密钥/令牌)。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason or "safe_fetch_error")
        self.detail = str(detail or "")
        super().__init__(f"{self.reason}: {self.detail}" if self.detail else self.reason)


class SafeFetchBlocked(SafeFetchError):
    """策略拒绝:scheme 非 https / 带 userinfo / 缺 host / 私网地址 / 重定向。"""


class SafeFetchTooLarge(SafeFetchError):
    """响应体超过调用方给的 max_bytes。"""


@dataclass(frozen=True)
class SafeTarget:
    """validate_url 的产物:已通过策略校验的目标 + 解析出的地址(直连时钉住这些地址)。"""

    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class FetchResult:
    data: bytes
    content_type: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    truncated: bool = False


# ---------------------------------------------------------------- 地址策略


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """字符串 -> IP 对象;IPv4-mapped IPv6(::ffff:a.b.c.d)拆成内嵌的 IPv4 再判。非 IP 返回 None。"""
    text = str(value or "").strip().strip("[]").split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped if mapped is not None else ip


def is_public_address(value: str) -> bool:
    """地址是否允许出站连接:不是 IP、回环、链路本地、组播、未指定、保留段、显式禁连网段一律 False。"""
    ip = _parse_ip(value)
    if ip is None:
        return False
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return False
    return not any(ip in net for net in _BLOCKED_NETWORKS if net.version == ip.version)


def _resolve_addresses(host: str, port: int) -> tuple[str, ...]:
    """DNS 解析(测试缝)。只收主机名——IP 字面量在 validate_url 里先于本函数按地址策略裁决,
    不进这里;解析失败 / 无结果抛 SafeFetchError(dns_failed)。"""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise SafeFetchError("dns_failed", f"{host}: {type(exc).__name__}") from exc
    addresses = tuple(dict.fromkeys(str(info[4][0]).split("%", 1)[0] for info in infos))
    if not addresses:
        raise SafeFetchError("dns_failed", f"{host}: no address")
    return addresses


def _split_url(url: str) -> urllib.parse.SplitResult:
    text = str(url or "").strip()
    try:
        return urllib.parse.urlsplit(text)
    except ValueError as exc:
        raise SafeFetchBlocked("url_invalid", type(exc).__name__) from exc


def _port_of(parsed: urllib.parse.SplitResult) -> int:
    try:
        port = parsed.port
    except ValueError as exc:
        raise SafeFetchBlocked("port_invalid") from exc
    return int(port or 443)


def validate_url(url: str) -> SafeTarget:
    """策略校验 + DNS 解析 + 地址校验。任何一条不过都抛 SafeFetchBlocked / SafeFetchError,不发任何请求。"""
    parsed = _split_url(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise SafeFetchBlocked("scheme_not_https", parsed.scheme or "(none)")
    if parsed.username is not None or parsed.password is not None:
        raise SafeFetchBlocked("userinfo_forbidden")
    host = str(parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise SafeFetchBlocked("host_missing")
    port = _port_of(parsed)
    # IP 字面量不查 DNS:先于解析按地址策略裁决(127.0.0.1 / [::1] / 169.254.x / ::ffff:10.x 在这一步就拒)。
    addresses = (host,) if _parse_ip(host) is not None else _resolve_addresses(host, port)
    blocked = [address for address in addresses if not is_public_address(address)]
    if blocked:
        raise SafeFetchBlocked("private_address", f"{host} -> {blocked[0]}")
    return SafeTarget(url=parsed.geturl(), host=host, port=port, addresses=addresses)


def clamp_timeout(value: Any) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = float(DEFAULT_TIMEOUT_SEC)
    return min(float(MAX_TIMEOUT_SEC), max(float(MIN_TIMEOUT_SEC), seconds))


# ---------------------------------------------------------------- 传输层


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """任何 3xx 都在第一跳截停:不构造第二个 Request、不发出、把响应体关掉。"""

    def _blocked(self, req: Any, fp: Any, code: int, msg: str, headers: Any) -> None:
        location = str(headers.get("Location") or headers.get("URI") or "") if headers is not None else ""
        target_host = str(urllib.parse.urlsplit(location).hostname or "?") if location else "?"
        close = getattr(fp, "close", None)
        if callable(close):
            close()
        raise SafeFetchBlocked("redirect_blocked", f"{code} -> {target_host}")

    http_error_301 = _blocked
    http_error_302 = _blocked
    http_error_303 = _blocked
    http_error_307 = _blocked
    http_error_308 = _blocked

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        # 保险丝:即便有人把 http_error_3xx 接回父类,也绝不产出可跟随的新请求。
        raise SafeFetchBlocked("redirect_blocked", str(code))


def _checked_peer(sock: socket.socket) -> socket.socket:
    """连上之后核对端:对端不是公网地址就立刻关掉,不发一个字节。"""
    peer = str(sock.getpeername()[0])
    if not is_public_address(peer):
        sock.close()
        raise SafeFetchBlocked("private_address", f"peer {peer}")
    return sock


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TCP 连接钉在 validate_url 已校验的地址上;TLS 仍按原 host 做 SNI / 证书校验。"""

    def __init__(self, host: str, *, addresses: tuple[str, ...], **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._pinned_addresses = tuple(addresses)
        self._create_connection = self._pinned_create_connection  # http.client 的连接工厂钩子

    def _pinned_create_connection(
        self,
        address: tuple[str, int],
        timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,  # noqa: SLF001 - http.client 同款默认哨兵
        source_address: Any = None,
    ) -> socket.socket:
        _host, port = address
        last_error: OSError | None = None
        for candidate in self._pinned_addresses:
            try:
                sock = socket.create_connection((candidate, int(port)), timeout, source_address)
            except OSError as exc:
                last_error = exc
                continue
            return _checked_peer(sock)
        raise last_error or OSError("no pinned address reachable")


def _connection_factory(host: str, target: SafeTarget, **kwargs: Any) -> http.client.HTTPConnection:
    """传输层工厂(测试缝):do_open 会带 timeout / context 调它。"""
    return _PinnedHTTPSConnection(host, addresses=target.addresses, **kwargs)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, target: SafeTarget, context: ssl.SSLContext) -> None:
        super().__init__(context=context)
        self._target = target

    def https_open(self, req: Any) -> Any:
        return self.do_open(self._make_connection, req, context=self._context)

    def _make_connection(self, host: str, **kwargs: Any) -> http.client.HTTPConnection:
        return _connection_factory(host, self._target, **kwargs)


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def _proxy_for(host: str) -> str:
    """环境里给 https 配了代理且 host 不在 no_proxy 内 -> 代理地址;否则空串(直连并钉地址)。"""
    proxy = str(urllib.request.getproxies().get("https") or "").strip()
    if not proxy or urllib.request.proxy_bypass(host):
        return ""
    return proxy


def _build_opener(target: SafeTarget) -> urllib.request.OpenerDirector:
    """只装 https 一种协议处理器 + 错误处理 + 禁重定向;file/ftp/data/http 处理器一概不装。"""
    opener = urllib.request.OpenerDirector()
    proxy = _proxy_for(target.host)
    if proxy:
        opener.add_handler(urllib.request.ProxyHandler({"https": proxy}))
        opener.add_handler(urllib.request.HTTPSHandler(context=_ssl_context()))
    else:
        opener.add_handler(_PinnedHTTPSHandler(target, _ssl_context()))
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    opener.add_handler(_NoRedirectHandler())
    return opener


def _assert_landed_on_target(response: Any, target: SafeTarget) -> None:
    """保险丝:响应最终 URL 的 host 必须还是校验过的 host(重定向被截停后这里永远成立)。"""
    landed = str(urllib.parse.urlsplit(str(response.geturl() or "")).hostname or "").rstrip(".").lower()
    if landed and landed != target.host:
        response.close()
        raise SafeFetchBlocked("redirect_blocked", f"landed on {landed}")


# ---------------------------------------------------------------- 公开入口


def open_url(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: Any = DEFAULT_TIMEOUT_SEC,
    method: str = "GET",
) -> Any:
    """校验 + 打开一个 https 响应(调用方负责 close / 用作上下文管理器)。

    抛:SafeFetchBlocked(策略拒绝 / 重定向)、SafeFetchError(dns_failed)、
    urllib.error.HTTPError(上游 4xx/5xx,code 保留给调用方)、urllib.error.URLError / OSError(网络)。
    """
    target = validate_url(url)
    request = urllib.request.Request(target.url, headers=dict(headers or {}), method=method)
    response = _build_opener(target).open(request, timeout=clamp_timeout(timeout))
    _assert_landed_on_target(response, target)
    return response


def content_type_of(response: Any) -> str:
    """响应的主 content-type(小写、去参数);缺失返回空串,由调用方决定默认值。"""
    raw = response.headers.get("content-type") if getattr(response, "headers", None) is not None else ""
    return str(raw or "").split(";", 1)[0].strip().lower()


def read_capped(response: Any, max_bytes: int, *, truncate: bool = False) -> tuple[bytes, bool]:
    """按上限读响应体。超限:truncate=True 返回前 max_bytes 字节并标 truncated,否则抛 SafeFetchTooLarge。"""
    limit = max(1, int(max_bytes))
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(READ_CHUNK_BYTES, limit - total + 1))
        if not chunk:
            return b"".join(chunks), False
        total += len(chunk)
        if total <= limit:
            chunks.append(chunk)
            continue
        if not truncate:
            raise SafeFetchTooLarge("too_large", f">{limit} bytes")
        chunks.append(chunk[: len(chunk) - (total - limit)])
        return b"".join(chunks), True


def fetch_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: Any = DEFAULT_TIMEOUT_SEC,
    max_bytes: int = DEFAULT_MAX_BYTES,
    truncate: bool = False,
) -> FetchResult:
    """一把梭:open_url + read_capped,返回 FetchResult。"""
    with open_url(url, headers=headers, timeout=timeout) as response:
        data, truncated = read_capped(response, max_bytes, truncate=truncate)
        return FetchResult(
            data=data,
            content_type=content_type_of(response),
            status=int(getattr(response, "status", 200) or 200),
            headers={str(k).lower(): str(v) for k, v in response.headers.items()},
            truncated=truncated,
        )


__all__ = [
    "ALLOWED_SCHEMES",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_TIMEOUT_SEC",
    "FetchResult",
    "MAX_TIMEOUT_SEC",
    "SafeFetchBlocked",
    "SafeFetchError",
    "SafeFetchTooLarge",
    "SafeTarget",
    "clamp_timeout",
    "content_type_of",
    "fetch_bytes",
    "is_public_address",
    "open_url",
    "read_capped",
    "validate_url",
]
