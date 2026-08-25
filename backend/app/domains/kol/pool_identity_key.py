"""KOL 池身份归一键(2026-08-25 发现墙缺陷①「同一个人反复出现」车道)。

**纯函数模块,零库、零网络。** 现有两个消费点:

- `discovery_candidate_key` —— 单次发现结果内的候选去重键(`discovery_filters._candidate_key`)。
  旧口径按「首个非空原始字段」成键,同一个人一次带 handle、一次只带 channel_url 就成两把键;
- `handle_is_identity_signal` —— 「这个 handle 能不能当身份用」的单一真源
  (`brand_official_gate` 判要不要拿 handle 去比品牌词时问它)。

**刻意不做的事**(2026-08-25 并入主线时裁掉,主线已有更强的同名能力,不再开第二个口):

- 池行的存量重复折叠 → 主线 `pool_read_projection.build_pool_read_selection`
  (证据式 union-find + 冲突留人工复核 + SQL 层排除,读端计数与 has_more 自洽);
- 落库前按身份反查既有行 → 主线 `profile_basics._canonical_existing_pool_id`
  (走 `identity.canonical_creator_aliases`,命中多行直接抛错而不是随便挑一行)。
  本模块的 handle 等值反查比它松,留在树里迟早被人误用,故不留。

红线:零写库、零触 `viltrox_fit_score` / rule_v0。
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from app.core.coerce import _text
from app.core.logging import get_logger

logger = get_logger("viltrox.domains.kol.pool_identity_key")

# 池内平台值域(prod 实测):youtube / tiktok / instagram / media / x / facebook。
# 发现侧/短名写法统一归到长名,避免 yt 行与 youtube 行各成一族。
_PLATFORM_CANONICAL = {
    "yt": "youtube",
    "youtube": "youtube",
    "youtube.com": "youtube",
    "ig": "instagram",
    "insta": "instagram",
    "instagram": "instagram",
    "instagram.com": "instagram",
    "tt": "tiktok",
    "tiktok": "tiktok",
    "tiktok.com": "tiktok",
    "fb": "facebook",
    "facebook": "facebook",
    "twitter": "x",
    "x": "x",
}
# handle 归一:去 @ / 去 URL 装饰 / 小写 / 去杂字符。
# **杂字符只删不认字的那些**(空格、斜杠、括号、emoji、全角标点):非 ASCII 的**字母与数字**
# (中日韩、西里尔、带音标拉丁…)原样留在键里。`\w` = 字母 + 数字 + 下划线(Python3 默认 Unicode 语义),
# 再补 `.` 与 `-` 两个 handle 合法字符;输入已在上游 .lower(),故与旧口径对纯 ASCII 逐字等价。
#
# 2026-08-25 修(对抗审查缺陷③):旧口径 `[^a-z0-9._-]+` 把非 ASCII **删掉**,于是
# 'sony_中国' 与 'sony_日本' 同归一为 'sony_'、'张三_01' 与 '李四_01' 同归为 '_01' ——
# 两个不同的人撞成同一把身份键,且 handle_is_identity_signal 双双为 True。
# 同族病在主线仍活着(本刀不改,另案裁决):`identity.HANDLE_RE = [^a-z0-9._-]+` 喂
# `normalize_handle` / `dedup_key`,实测 '映像プロデューサーlit' -> 'lit'、'sony_日本' -> 'sony_'。
# 主线的规范身份链走 `identity._identity_handle`(`str.isalnum()`,Unicode 安全),不受影响。
# 保留原字符 = 两把键从此分家(宁可少并,绝不错并);纯 CJK handle 现在也能成键,
# 而 _is_garbage_handle 的 LLM 失败短语(「未提供」等)也终于能作用到中文 handle 上。
_HANDLE_JUNK_RE = re.compile(r"[^\w.\-]+")
# 主机名判据:至少一个点、只含域名合法字符。挡住 profile_url 列里那批「不是 URL 的脏值」。
_HOSTNAME_RE = re.compile(r"[a-z0-9¡-￿][a-z0-9._\-¡-￿]*\.[a-z¡-￿]{2,}")
# 站点行的作者判别位:同一家媒体站的不同作者是不同的人,不能只按站点 URL 并成一行。
_SITE_AUTHOR_JUNK_RE = re.compile(r"[^0-9a-z一-鿿]+")
_SITE_AUTHOR_MARKERS = ("【media】", "[media]", "(media)")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
# 这些「平台」的 handle 不是身份:media/website/blog 行的 handle 是从文章路径捏出来的
# (opticallimits-fe / nikon / cameras),同名不等于同一家 → 只认归一主页 URL。
# 平台缺失("")不进本表:那只是「不知道」,不能当「handle 不算身份」的证据。
_URL_ONLY_PLATFORMS = frozenset({
    "media", "website", "blog", "forum", "community", "newsletter", "other",
})
# 平台主页域名(只有域名对得上才敢从 URL 里取 handle)。
_PLATFORM_URL_HOSTS = {
    "youtube": ("youtube.com", "m.youtube.com", "youtu.be"),
    "instagram": ("instagram.com", "m.instagram.com"),
    "tiktok": ("tiktok.com", "m.tiktok.com", "vm.tiktok.com"),
    "x": ("x.com", "twitter.com", "mobile.twitter.com"),
    "facebook": ("facebook.com", "m.facebook.com", "fb.com"),
}
# 主页路径前缀(youtube.com/channel/UCxxx、/c/Name、/user/Name 与 /@handle 同为主页形态)。
_PROFILE_PATH_PREFIXES = frozenset({"channel", "c", "user", "@"})
# 懒 import 失败只告警一次(见 _is_garbage_handle):每行刷一条栈会把日志淹掉。
_GARBAGE_RULE_WARNED: set[str] = set()


def canonical_platform(value: Any) -> str:
    """平台归一:短名/域名写法统一到池内长名;未知值原样小写(不杜撰)。"""
    text = _text(value).lower().replace(" ", "_")
    return _PLATFORM_CANONICAL.get(text, text)


def normalize_handle_key(handle: Any, platform: Any = "") -> str:
    """handle 归一键:URL 取尾段 → 去 @ → 去 query/fragment → 小写 → 去杂字符。

    YT 频道 ID(UCxxxx)与 @handle 是两套身份,本函数不做互转(需要 raw 里的真映射,
    属另一刀);此处只保证同一写法的大小写/装饰差异不再分裂成两行。
    """
    raw = _text(handle)
    if not raw:
        return ""
    candidate = raw
    if _SCHEME_RE.match(raw) or raw.startswith("//"):
        parts = [part for part in urlsplit(raw if "://" in raw else f"https:{raw}").path.split("/") if part]
        candidate = parts[-1] if parts else ""
    candidate = candidate.strip().lstrip("@").split("?", 1)[0].split("#", 1)[0].strip("/").lower()
    if canonical_platform(platform) == "youtube" and candidate.startswith("channel/"):
        candidate = candidate.rsplit("/", 1)[-1]
    return _HANDLE_JUNK_RE.sub("", candidate)


def normalize_profile_url_key(url: Any) -> str:
    """主页 URL 归一键:去 scheme / 去 www. / 去尾斜杠 / 去 #fragment / 去 ?query,全小写。

    ?query 只在路径不是脚本页(.php 等)时才丢——facebook 的 `profile.php?id=123`
    里 query **就是**身份,丢了会把不同的人并成一行(宁可少并,绝不错并)。
    """
    raw = _text(url)
    if not raw:
        return ""
    if not _SCHEME_RE.match(raw):
        raw = f"https://{raw}"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    host = parts.netloc.lower().split("@")[-1].split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    # 主机名必须真长得像域名。profile_url 列里有一批根本不是 URL 的脏值(实测两行
    # 真达人 _aguywithacamera / badiu.photography 的 profile_url 都是抓来的页面标题
    # 「(2) Instagram」)——不设这道闸,它们会被归一成同一把 `u:` 键、在墙上并成一行,
    # 等于把一个真人整个抹掉。认不出主机名就当没有 URL 身份(宁可少并,绝不错并)。
    if not _HOSTNAME_RE.fullmatch(host):
        return ""
    path = parts.path.rstrip("/").lower()
    query = parts.query.lower()
    keep_query = bool(query) and path.endswith((".php", ".asp", ".aspx", ".jsp", ".cgi"))
    return f"{host}{path}" + (f"?{query}" if keep_query else "")


def handle_is_identity_signal(handle: Any, platform: Any = "") -> bool:
    """该 handle 能不能当身份用(单一真源:折叠 / 反查 / 品牌闸都问这一个口)。

    不能用的两类:① media/website/blog 等站点行——handle 是从文章路径捏的
    (opticallimits 的行 handle 叫 nikon),同名 ≠ 同一家;② URL 保留段/泛词垃圾
    handle('p' / 'reel' / 'watch' / 'cameras')——两个不同的人会撞成同一把键。
    """
    key = normalize_handle_key(handle, platform)
    if not key:
        return False
    if canonical_platform(platform) in _URL_ONLY_PLATFORMS:
        return False
    return not _is_garbage_handle(key)


def url_is_identity_signal(url: Any, platform: Any = "") -> bool:
    """该主页 URL 能不能当身份用(与 handle_is_identity_signal 同层的第二道守卫)。

    平台原生站(YouTube/IG/TikTok/X/Facebook)的自家域名 URL,只有**长得像主页**才算身份:
    `instagram.com/p`、`instagram.com/reel`、`youtube.com/@https://www.amazon.com/dp/...`
    这类贴文/保留段/被塞了外链的路径,是从文章链接捏出来的,两个不同的人会撞成同一把键
    (实测 9.5M 粉的 alessandroz1 会被并进 handle='reel' 的垃圾行)。外站链接(linktree 等)
    与站点类平台(media/website)不归本函数管,照旧放行——站点行靠 identity_keys 里的
    作者判别位再分家。
    """
    key = normalize_profile_url_key(url)
    if not key:
        return False
    plat = canonical_platform(platform)
    hosts = _PLATFORM_URL_HOSTS.get(plat)
    if not hosts:
        return True
    host = key.split("/", 1)[0].split("?", 1)[0]
    if host not in hosts:
        return True  # 外站链接:本函数不判,维持既有口径
    if "?" in key:
        return True  # facebook 的 profile.php?id=… —— query 就是身份
    return handle_is_identity_signal(handle_from_profile_url(url, plat), plat)


def site_author_key(row: Any) -> str:
    """站点行的作者判别位(只用于 media/website 等「URL 即身份」的平台)。

    35mmc 上的 Hamish Gill 与 Mike Brooks 是两个人,只按站点 URL 成键会把他们并成一行
    (被并掉的那个作者在墙上整个消失)。作者名归一后进键:名字取不到就退回纯站点键
    (与旧口径一致,宁可少并)。
    """
    if not isinstance(row, dict):
        return ""
    name = _text(row.get("display_name") or row.get("channel_name") or row.get("name")).lower()
    for marker in _SITE_AUTHOR_MARKERS:
        name = name.replace(marker, " ")
    return _SITE_AUTHOR_JUNK_RE.sub("", name)


def _is_garbage_handle(handle: str) -> bool:
    """复用入池卫生闸的同一份垃圾 handle 判据(懒 import 防循环)。

    **判不了就当垃圾(fail-closed)**:返回 True → handle 不当身份 → 不并、不反查。
    2026-08-25 修(对抗审查缺陷⑨):旧码 `except: return False` 朝不安全方向兜底——
    判据一取不到,'reel' / 'watch' / 'p' 这类 URL 保留段残片就重新变成身份信号,
    恰好复活 :168-177 想防的那类错并(9.5M 粉的真达人被并进 handle='reel' 的垃圾行)。
    失败必留痕:import 失败只在首次告警(每行都刷栈会淹掉日志),判据本身抛错逐次告警。
    """
    try:
        from app.domains.kol.pool_common import _garbage_handle_rule
    except Exception:
        if "import" not in _GARBAGE_RULE_WARNED:
            _GARBAGE_RULE_WARNED.add("import")
            logger.warning(
                "garbage-handle rule unavailable; handles treated as non-identity(fail-closed)",
                exc_info=True,
            )
        return True
    try:
        return _garbage_handle_rule(handle) is not None
    except Exception:
        logger.warning(
            "garbage-handle rule raised on %r; treated as non-identity(fail-closed)",
            handle[:60],
            exc_info=True,
        )
        return True


def handle_from_profile_url(url: Any, platform: Any) -> str:
    """从平台主页 URL 里取 handle(只在域名与本行平台对得上、路径形态是主页时才取)。

    youtube.com/@abc、instagram.com/abc/、tiktok.com/@abc、youtube.com/channel/UCxxx 认;
    多层路径(文章页/视频页)与非本平台域名一律返回空——宁可不取,绝不把文章路径当身份。
    """
    plat = canonical_platform(platform)
    hosts = _PLATFORM_URL_HOSTS.get(plat)
    raw = _text(url)
    if not hosts or not raw:
        return ""
    if not _SCHEME_RE.match(raw):
        raw = f"https://{raw}"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    host = parts.netloc.lower().split("@")[-1]
    if host.startswith("www."):
        host = host[4:]
    if host not in hosts:
        return ""
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) == 2 and segments[0].lower() in _PROFILE_PATH_PREFIXES:
        segments = segments[1:]
    if len(segments) != 1:
        return ""
    return normalize_handle_key(segments[0], plat)


def identity_keys(row: Any, *, platform: Any = "", default_platform: str = "") -> list[str]:
    """一行/一项的身份键表(可能多把:handle 一把、主页 URL 一把)。

    键前缀 `h:` / `u:` 区分来源;平台永远进键——跨平台同名 handle 是两个人,绝不并。
    """
    if not isinstance(row, dict):
        return []
    plat = canonical_platform(
        platform or row.get("platform") or row.get("kol_platform") or default_platform
    )
    keys: list[str] = []
    raw_url = row.get("profile_url") or row.get("channel_url") or row.get("url")
    handle = normalize_handle_key(
        row.get("handle") or row.get("username") or row.get("channel_handle"), plat
    )
    if not handle:
        # 只带主页 URL 的候选(同一个人一次带 handle、一次只带 URL)也要落到同一把 handle 键。
        handle = handle_from_profile_url(raw_url, plat)
    if handle and not handle_is_identity_signal(handle, plat):
        handle = ""  # 站点行的路径式 handle / URL 保留段垃圾 handle 都不当身份
    if handle:
        keys.append(f"h:{plat}:{handle}")
    url_key = normalize_profile_url_key(raw_url) if url_is_identity_signal(raw_url, plat) else ""
    if url_key:
        if plat in _URL_ONLY_PLATFORMS:
            # 站点行:站点 URL + 作者名才是一个人(同站不同作者绝不并成一行)。
            author = site_author_key(row)
            url_key = f"{url_key}#{author}" if author else url_key
        keys.append(f"u:{plat}:{url_key}")
    return keys


def discovery_candidate_key(item: dict[str, Any], platform: str) -> str:
    """发现结果单次运行内的候选去重键(归一身份优先,退回旧字段口径)。

    旧口径按「首个非空原始字段」成键:同一个人一次带 handle、一次只带 channel_url
    就成两把键 → 单次结果内也重复。归一后 handle / URL 任一命中即同一人。
    """
    keys = identity_keys(item, default_platform=platform)
    if keys:
        return keys[0]
    for key in ("handle", "channel_url", "source_url", "channel_name"):
        value = _text(item.get(key)).lower()
        if value:
            return f"{canonical_platform(platform)}:{value}"
    return f"{canonical_platform(platform)}:unknown:{len(str(item))}"



__all__ = [
    "canonical_platform",
    "discovery_candidate_key",
    "handle_from_profile_url",
    "handle_is_identity_signal",
    "identity_keys",
    "normalize_handle_key",
    "normalize_profile_url_key",
    "site_author_key",
    "url_is_identity_signal",
]
