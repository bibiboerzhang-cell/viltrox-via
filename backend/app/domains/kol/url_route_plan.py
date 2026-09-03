"""贴任意链接的去向决策(纯函数,零网络、零数据库、零写库)。

用户贴进来的链接只有四条去向:

* ``profile`` —— 平台账号主页,交给既有的账号分析通道(行为与今天逐字一致);
* ``video``  —— 单条内容,交给既有的内容分析通道;
* ``website`` —— 不是平台账号,但是一个能打开的公开站点:走网页抓取腿,
  只取站点公开的联系方式与站点资料;
* ``unsupported`` —— 诚实拒绝,并给一句用户看得懂的原因,**不留下一条卡住的任务**。

判据只有两样:**主机名** 与 **路径形状**。主机名判定一律委托既有识别器
(``_platform_from_host`` / ``_cn_platform_from_host`` / ``detect_platform_from_profile_url``),
不在这里另抄一份;路径形状只回答「这是不是一个能读的网页」。

刻意不收任何具体站点清单:摄影媒体站、个人博客、论坛在本模块里没有名字,
它们只是「不是平台账号的公开站点」。收清单等于每来一个新站就要改代码,
而历史上正是这类链接被一路送进账号抓取通道,最后堆成 202 条卡住的活。

关于 Facebook(2026-09-03 核过):``detect_platform_from_profile_url`` 认得
facebook.com,但账号抓取通道的 ``SUPPORTED_PLATFORMS`` 只有 youtube/instagram/tiktok,
``_platform_from_host`` 与 ``_video_id`` 也都没有 facebook 分支,网页抓取腿的域名
黑名单同样把 facebook.com 排除在外 —— 三处一致,所以它是**真的不支持**,
归 ``unsupported`` 并明说,不假装能读。

红线:本模块不抓取、不写库、不判断产品证据;网页抓取腿拿到的正文只允许落
联系方式与站点资料,不得进召回证据链。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.domains.kol.business_contact_extract import _SOCIAL_HOSTS, _host_matches
from app.domains.kol.url_deep_crawl_helpers import (
    _cn_platform_from_host,
    _normalize_input_url,
    _platform_from_host,
)

ROUTE_PROFILE = "profile"
ROUTE_VIDEO = "video"
ROUTE_WEBSITE = "website"
ROUTE_UNSUPPORTED = "unsupported"

# 平台展示名:只在拒绝文案里出现,让用户知道我们认出了是哪家,只是读不了。
_PLATFORM_LABELS = {
    "bilibili": "哔哩哔哩",
    "discord": "Discord",
    "douyin": "抖音",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "pinterest": "Pinterest",
    "telegram": "Telegram",
    "tiktok": "TikTok",
    "twitch": "Twitch",
    "twitter": "X(原 Twitter)",
    "whatsapp": "WhatsApp",
    "xiaohongshu": "小红书",
    "youtube": "YouTube",
}

# 保留 / 内网用途的顶级后缀:这些地址不是公开站点,不去打。
_RESERVED_TLDS = frozenset({"local", "localhost", "internal", "intranet", "invalid", "test", "onion", "home", "lan"})

# 指向文件而不是网页的后缀:读它拿不到联系方式,也不该下载。
_FILE_SUFFIXES = (
    ".7z", ".apk", ".avi", ".bin", ".bmp", ".css", ".csv", ".dmg", ".doc", ".docx",
    ".exe", ".gif", ".gz", ".ico", ".jpeg", ".jpg", ".js", ".json", ".mov", ".mp3",
    ".mp4", ".pdf", ".png", ".ppt", ".pptx", ".rar", ".svg", ".tar", ".wav", ".webp",
    ".xls", ".xlsx", ".xml", ".zip",
)

_SUPPORTED_ACCOUNT_PLATFORMS = "YouTube、Instagram 或 TikTok"

# 站点根地址在 contacts 结构里的行类型(``site_contact_rows`` 用)。
_SITE_CONTACT_TYPE = "website"


@dataclass(frozen=True)
class UrlRoutePlan:
    """一条链接的去向 + 一句给用户看的原因。``target_url`` 是补好协议头的规范地址。"""

    route: str
    reason_code: str
    reason_human: str
    target_url: str
    host: str

    @property
    def handled_by_account_crawler(self) -> bool:
        """True = 沿用既有账号分析通道,调用方什么都不用改。"""
        return self.route in (ROUTE_PROFILE, ROUTE_VIDEO)

    def receipt(self) -> dict[str, Any]:
        """分流回执骨架:``job_id`` 恒为 None —— 这条链接没有、也不该有一条卡住的活。"""
        return {
            "status": "not_supported",
            "job_id": None,
            "route": self.route,
            "reason": self.reason_code,
            "message": self.reason_human,
            "url": self.target_url,
        }


def _plan(route: str, code: str, human: str, url: str, host: str) -> UrlRoutePlan:
    return UrlRoutePlan(route=route, reason_code=code, reason_human=human, target_url=url, host=host)


def host_of(url: str) -> str:
    """规范地址的主机名(小写、去 www.、去端口);取不到返回空串。"""
    try:
        netloc = urlparse(str(url or "")).netloc
    except ValueError:
        return ""
    host = netloc.rsplit("@", 1)[-1].strip().lower()
    if not host or host.startswith("["):  # IPv6 字面量不是站点,交给上游按「打不开」处理
        return ""
    return host.split(":", 1)[0].removeprefix("www.").strip(".")


def site_base(url: str) -> str:
    """站点根地址 ``https://<host>``:网页抓取腿按站点记账,同一站点只认一次。"""
    host = host_of(url)
    return f"https://{host}" if host else ""


def site_contact_rows(base: str, found: list[dict[str, Any]]) -> list[tuple[str, str, str, float, str]]:
    """网页抓取腿的产出整形成待写行 ``(类型, 值, 来源页, 置信, 佐证)``。

    第一行永远是站点根地址本身 —— 它既是站点资料,也是「这个站点读过了」的记号。
    只整形联系方式与站点资料,正文一律丢弃(红线:不得进召回证据链)。
    """
    rows = [(_SITE_CONTACT_TYPE, base, base, 0.5, "site")]
    rows += [
        (
            str(item.get("contact_type") or ""),
            str(item.get("contact_value") or ""),
            str(item.get("source_url") or base),
            float(item.get("confidence") or 0.7),
            str(item.get("evidence_text") or "")[:200],
        )
        for item in found or []
        if item.get("contact_type") and item.get("contact_value")
    ]
    return rows


def is_public_site_host(host: str) -> bool:
    """像不像一个公开站点的主机名:有点、后缀是字母、不是保留后缀、不是 IP 字面量。

    这不是安全判定 —— 真正的私网/回环拦截在 ``app.platform.safe_fetch``;
    这里只负责把明显不该当站点抓的地址提前挡在门外,少发一次没意义的请求。
    """
    clean = str(host or "").strip().lower()
    if not clean or " " in clean or "." not in clean:
        return False
    labels = clean.split(".")
    if not all(labels):
        return False
    suffix = labels[-1]
    return suffix.isalpha() and len(suffix) >= 2 and suffix not in _RESERVED_TLDS


def file_suffix_of(url: str) -> str:
    """路径末尾的文件后缀(小写);不是文件返回空串。"""
    try:
        path = urlparse(str(url or "")).path
    except ValueError:
        return ""
    lowered = path.rstrip("/").lower()
    for suffix in _FILE_SUFFIXES:
        if lowered.endswith(suffix):
            return suffix
    return ""


def _platform_label(platform: str) -> str:
    key = str(platform or "").strip().lower()
    return _PLATFORM_LABELS.get(key, key or "这个平台")


def _refuse_platform(platform: str, url: str, host: str) -> UrlRoutePlan:
    return _plan(
        ROUTE_UNSUPPORTED,
        "platform_not_supported",
        f"暂时读不了 {_platform_label(platform)} 的链接。"
        f"请改贴 {_SUPPORTED_ACCOUNT_PLATFORMS} 的账号主页,或者贴一个能打开的网站。",
        url,
        host,
    )


def _known_platform_host(host: str) -> str:
    """认得但账号抓取通道打不开的平台(Facebook / X / LinkedIn / Telegram …)。

    判定复用 L0 抽取腿那份**精确**主机名表(``_SOCIAL_HOSTS`` + ``_host_matches``):
    它的注释里就写着为什么不能退回子串匹配 —— ``jurjax.com`` 含 "x.com",
    子串匹配会把一个正经独立站误判成 X 然后拒掉。
    """
    for needle, tag in _SOCIAL_HOSTS.items():
        if _host_matches(host, needle):
            return str(tag)
    return ""


def _plan_off_platform(url: str, host: str) -> UrlRoutePlan:
    """主机名不属于账号抓取通道时的去向:公开站点走网页抓取,其余诚实拒绝。"""
    other_platform = _known_platform_host(host)
    if other_platform:
        return _refuse_platform(other_platform, url, host)
    if not is_public_site_host(host):
        return _plan(
            ROUTE_UNSUPPORTED,
            "site_not_public",
            "这个地址不是一个公开网站,已跳过。",
            url,
            host,
        )
    suffix = file_suffix_of(url)
    if suffix:
        return _plan(
            ROUTE_UNSUPPORTED,
            "site_is_a_file",
            f"这个链接指向一个 {suffix.lstrip('.')} 文件,不是能读的网页。",
            url,
            host,
        )
    return _plan(
        ROUTE_WEBSITE,
        "site_page",
        "这不是平台账号主页,已按公开网站处理:只读站点公开的联系方式与站点资料。",
        url,
        host,
    )


def _empty_url_plan(url: str) -> UrlRoutePlan:
    return _plan(
        ROUTE_UNSUPPORTED,
        "link_not_a_web_address" if url else "link_missing",
        "这不是一个能打开的网址。" if url else "没有收到链接。",
        url,
        "",
    )


def plan_url_route_from_url(raw_url: str) -> UrlRoutePlan:
    """只看链接本身决定去向 —— 入队口用这一支(那里还没做过账号识别)。

    保守口径:凡是账号抓取通道**认得的主机名**(youtube / instagram / tiktok
    与三家中国平台)一律回 ``profile``,原样交给既有通道去分辨主页/单条内容,
    今天能跑通的链接因此一条都不受影响;只有通道从来就不认的主机名才分流。
    """
    normalized = _normalize_input_url(str(raw_url or "").strip())
    host = host_of(normalized)
    if not normalized or not host:
        return _empty_url_plan(normalized)
    known = _platform_from_host(host) or _cn_platform_from_host(host)
    if known:
        return _plan(
            ROUTE_PROFILE,
            "handled_by_account_crawler",
            "识别为平台链接,交给账号分析。",
            normalized,
            host,
        )
    return _plan_off_platform(normalized, host)


def plan_url_route(classified: Any, *, raw_url: str = "") -> UrlRoutePlan:
    """拿着账号识别结果决定去向 —— 已经调过 ``classify_url`` 的地方用这一支。

    只读 ``classified`` 的字段,不改它、也不要求它是某个具体类型
    (``classify_url`` 的返回形状有特征化测试钉着,本模块一个字都不动它)。
    """
    url_type = str(getattr(classified, "url_type", "") or "")
    normalized = str(getattr(classified, "normalized_url", "") or "") or _normalize_input_url(raw_url)
    host = host_of(normalized)
    if url_type == ROUTE_VIDEO:
        return _plan(ROUTE_VIDEO, "single_content", "识别为单条内容,交给内容分析。", normalized, host)
    if url_type == ROUTE_PROFILE:
        return _plan(ROUTE_PROFILE, "account_profile", "识别为账号主页,交给账号分析。", normalized, host)
    if not normalized or not host:
        return _empty_url_plan(normalized)
    cn_platform = _cn_platform_from_host(host)
    if cn_platform:
        return _plan(
            ROUTE_UNSUPPORTED,
            "platform_single_content_only",
            f"{_platform_label(cn_platform)} 目前只支持单条内容的链接,账号主页还读不了。",
            normalized,
            host,
        )
    if _platform_from_host(host):
        return _plan(
            ROUTE_UNSUPPORTED,
            "platform_link_without_account",
            "这个链接里没有账号信息,请改贴账号主页或单条内容的链接。",
            normalized,
            host,
        )
    return _plan_off_platform(normalized, host)


__all__ = [
    "ROUTE_PROFILE",
    "ROUTE_UNSUPPORTED",
    "ROUTE_VIDEO",
    "ROUTE_WEBSITE",
    "UrlRoutePlan",
    "file_suffix_of",
    "host_of",
    "is_public_site_host",
    "plan_url_route",
    "plan_url_route_from_url",
    "site_base",
    "site_contact_rows",
]
