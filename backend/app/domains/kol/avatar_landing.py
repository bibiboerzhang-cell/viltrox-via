"""头像落地(durable avatar landing):写池表时同步把图片存进我们自己的媒体缓存。

病根(2026-08-25 实测,prod a05e48dd3):全池 2034 行头像里,905 行是 YouTube 稳定
直连,**927 行是签名型 CDN 外链**(cdninstagram / tiktokcdn),其中 717 行 updated_at
超 14 天;对过期链接抽样 12 条做 HEAD 探活,**12/12 全部失效**。也就是说我们把
「别人家的临时链接」当成了持久数据,链接一过期,头像就没了,原链救不回来。

本模块的职责只有一件:**在头像写进池表的同一条路径上,把图片复制进我们自己的
媒体缓存**(本地 image-cache 目录 + 已开启 R2 时的异地备份),并在
``raw_platform_data.avatar_media_v1`` 落一个诚实标记,让下游与运维一眼分清
「我们自己存着」和「只有一根随时会断的外链」。

设计红线:

* **落地失败绝不阻断建档**:所有入口全 best-effort,只告警 + 记账,永不抛给写事务;
* **只调用既有媒体缓存能力**,不改 ``backend/app/domains/media/`` 任何文件;
* **幂等**:池表里已是本地地址、或本地缓存已命中、或同一张图已在 R2(按内容
  校验和比对)时,一律不再抓、不再传;
* **有闸**:``AvatarLandingBudget`` 限制单次批量入库真正花带宽的落地张数;
* 稳定直连(ggpht / googleusercontent)同样落地——别人家的「稳定」不是我们的保证;
* 零触 ``viltrox_fit_score`` / ``rule_v0``。

``avatar_url`` 列**保持上游原值不动**:它是重抓的唯一线索。读侧投影
(``pool_read_projection.project_pool_avatar``)已经会优先解析本地缓存副本,
所以落地之后前端自然走本地,无需改读路径。

运维一句话看风险面(PostgreSQL)::

    SELECT COALESCE(raw_platform_data::jsonb -> 'avatar_media_v1' ->> 'status',
                    'unstamped') AS avatar_state,
           COUNT(*) AS row_count
    FROM vkpi_kol_pool
    WHERE duplicate_of_id IS NULL
    GROUP BY 1
    ORDER BY 2 DESC;
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.coerce import _text
from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger("viltrox.domains.kol.avatar_landing")

AVATAR_MEDIA_MARKER_KEY = "avatar_media_v1"
AVATAR_LANDING_METHOD = "kol_avatar_landing_v1"
PUBLIC_IMAGE_CACHE_PREFIX = "/api/vkpi-media/image-cache"
# 读侧投影认这两个前缀为「我们自己的地址」,此处保持同口径。
_LOCAL_CACHE_PREFIXES = (
    "/api/vkpi-media/image-cache/",
    "/api/admin/vkpi/media/image-cache/",
)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)

DEFAULT_BATCH_LANDING_LIMIT = 50
MAX_BATCH_LANDING_LIMIT = 500
SINGLE_WRITE_LANDING_LIMIT = 1
DEFAULT_FETCH_TIMEOUT = 8

# 诚实词表:与 pool_read_projection 的 avatar_url_status 同口径,避免两套说法。
STATUS_DURABLE = "durable"      # 我们自己存着
STATUS_EXTERNAL = "external"    # 只有第三方外链(哪怕上游号称稳定)
STATUS_UNKNOWN = "unknown"      # 判定过程本身出错,不敢下结论

# 白名单预检不可用时的哨兵:必须与「明确不在白名单」的 None 区分开。
_NORMALIZER_UNAVAILABLE = object()


class AvatarLandingBudget:
    """单次批量入库的落地闸。

    只计「真正花带宽/成本的一次落地」:命中本地缓存或已在 R2 的重复入库不占额度,
    所以同一批重复跑不会被自己的历史成绩挤爆闸门。
    """

    __slots__ = ("_limit", "_used")

    def __init__(self, limit: int) -> None:
        try:
            parsed = int(limit)
        except (TypeError, ValueError):
            parsed = 0
        self._limit = max(0, min(MAX_BATCH_LANDING_LIMIT, parsed))
        self._used = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self._limit - self._used)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def consume(self, count: int = 1) -> None:
        self._used += max(0, int(count))

    def refund(self, count: int = 1) -> None:
        self._used = max(0, self._used - max(0, int(count)))

    def snapshot(self) -> dict[str, int]:
        return {"limit": self._limit, "used": self._used, "remaining": self.remaining}


class _Reservation:
    """一次 ``land_avatar_url`` 调用最多占用一格额度(抓图 + 上传合计一格)。"""

    __slots__ = ("_budget", "_held")

    def __init__(self, budget: AvatarLandingBudget | None) -> None:
        self._budget = budget
        self._held = False

    def claim(self) -> bool:
        if self._held:
            return True
        if self._budget is None:
            self._held = True
            return True
        if self._budget.exhausted:
            return False
        self._budget.consume()
        self._held = True
        return True

    def refund(self) -> None:
        if self._held and self._budget is not None:
            self._budget.refund()
        self._held = False


def avatar_landing_enabled() -> bool:
    return _env_flag("VKPI_AVATAR_LANDING_ENABLED", default=True)


def avatar_landing_batch_limit() -> int:
    raw = os.getenv("VKPI_AVATAR_LANDING_MAX_PER_BATCH", "").strip()
    if not raw:
        return DEFAULT_BATCH_LANDING_LIMIT
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        logger.warning("VKPI_AVATAR_LANDING_MAX_PER_BATCH 非整数,回落默认值")
        return DEFAULT_BATCH_LANDING_LIMIT
    return max(0, min(MAX_BATCH_LANDING_LIMIT, parsed))


def new_landing_budget(limit: int | None = None) -> AvatarLandingBudget:
    """批量入口用它开一把闸;单条写入默认只给 1 格。"""
    return AvatarLandingBudget(avatar_landing_batch_limit() if limit is None else limit)


def land_avatar_url(
    raw_url: Any,
    *,
    platform: str = "",
    external_id: str = "",
    budget: AvatarLandingBudget | None = None,
    timeout: int = DEFAULT_FETCH_TIMEOUT,
    conn: Any | None = None,
    allow_ledger_writes: bool = True,
) -> dict[str, Any]:
    """把一个头像 URL 落进我们自己的缓存,返回诚实标记。此函数**永不抛异常**。

    ``allow_ledger_writes=False`` 用于「调用方的事务还开着、稍后可能 rollback」的
    场景:媒体台账登记与 R2 上传都会在共享连接上 commit,会把调用方还没定稿的
    事务提前定死(``profile_online_inventory`` 就靠 rollback 去重)。这种场景只落
    本地副本,异地备份留给后续独立通道补,标记如实写 ``ledger_write_deferred``。
    """
    try:
        return _land_avatar_url(
            raw_url,
            platform=platform,
            external_id=external_id,
            budget=budget,
            timeout=timeout,
            conn=conn,
            allow_ledger_writes=allow_ledger_writes,
        )
    except Exception:
        # 失败方向安全:建档继续,标记诚实地说「不知道」,不假装 durable。
        logger.warning("avatar landing failed; profile write continues", exc_info=True)
        return _marker(status=STATUS_UNKNOWN, stored=False, outcome="lander_error", upstream=STATUS_UNKNOWN)


def stamp_avatar_landing(
    conn: Any,
    kol_pool_id: int | None,
    marker: dict[str, Any],
    *,
    commit: bool = True,
) -> bool:
    """把标记合并进该行 ``raw_platform_data``(独立读改写,零触评分列)。"""
    if not kol_pool_id or not isinstance(marker, dict):
        return False
    db = conn or get_conn()
    try:
        row = db.execute(
            "SELECT raw_platform_data FROM vkpi_kol_pool WHERE id=?",
            (int(kol_pool_id),),
        ).fetchone()
        if row is None:
            return False
        payload = _json_obj(dict(row).get("raw_platform_data"))
        payload[AVATAR_MEDIA_MARKER_KEY] = dict(marker)
        db.execute(
            "UPDATE vkpi_kol_pool SET raw_platform_data=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False, default=str), int(kol_pool_id)),
        )
        if commit:
            db.commit()
        return True
    except Exception:
        logger.warning("avatar landing marker stamp skipped kol=%s", kol_pool_id, exc_info=True)
        return False


def land_and_stamp_avatar(
    conn: Any,
    kol_pool_id: int | None,
    raw_url: Any,
    *,
    platform: str = "",
    external_id: str = "",
    budget: AvatarLandingBudget | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """建档/刷新落库后的收尾:落地 + 打标记,全程 best-effort。"""
    marker = land_avatar_url(
        raw_url,
        platform=platform,
        external_id=external_id,
        budget=budget,
        conn=conn,
        # 调用方还没提交 → 台账/R2 的 commit 会把它的事务提前定死,这里只落本地。
        allow_ledger_writes=commit,
    )
    marker["stamped"] = stamp_avatar_landing(conn, kol_pool_id, marker, commit=commit)
    return marker


# --------------------------------------------------------------------------
# 内部实现
# --------------------------------------------------------------------------


def _land_avatar_url(
    raw_url: Any,
    *,
    platform: str,
    external_id: str,
    budget: AvatarLandingBudget | None,
    timeout: int,
    conn: Any | None,
    allow_ledger_writes: bool,
) -> dict[str, Any]:
    text = _text(raw_url).strip()
    if not text:
        return _marker(status="missing", stored=False, outcome="no_avatar", upstream="missing")

    local_digest = local_cache_digest(text)
    if local_digest:
        # 池表里存的已经是我们自己的地址 —— 天然幂等,零网络零上传。
        return _marker(
            status=STATUS_DURABLE,
            stored=True,
            outcome="already_local_url",
            upstream=STATUS_DURABLE,
            digest=local_digest,
            storage_backend="local",
        )

    usable_url, upstream = _upstream_policy(text)
    host = _host(text)

    if _release_validation_fenced():
        return _marker(status=_visible_status(False, upstream), stored=False,
                       outcome="release_validation_fenced", upstream=upstream, source_host=host)
    if not avatar_landing_enabled():
        return _marker(status=_visible_status(False, upstream), stored=False,
                       outcome="landing_disabled", upstream=upstream, source_host=host)
    if upstream in {"invalid", "missing"}:
        return _marker(status=_visible_status(False, upstream), stored=False,
                       outcome="unusable_upstream", upstream=upstream, source_host=host)

    normalized = _media_normalize_image_url(text)
    if normalized is _NORMALIZER_UNAVAILABLE:
        # 预检不可用(媒体域改名/搬家):退回让 cache_image 自己把白名单的关,
        # 下面 status == "skipped" 分支仍会把额度退回。
        target_url = usable_url or text
    elif normalized is None:
        return _marker(status=_visible_status(False, upstream), stored=False,
                       outcome="not_allowlisted", upstream=upstream,
                       reason="host_not_allowlisted", source_host=host)
    else:
        target_url, host = normalized

    reservation = _Reservation(budget)
    fetched = False
    cache_url = _media_cached_image_url(target_url)
    if not cache_url:
        if upstream == "expired":
            # 抽样实测:过期签名链 12/12 全死。此处不空转烧带宽,真正的补法是重抓档案
            # ——这正是「陆续不齐全的数据要继续补齐」那条机制该接手的地方。
            return _marker(status=_visible_status(False, upstream), stored=False,
                           outcome="upstream_expired", upstream=upstream, source_host=host)
        if not reservation.claim():
            return _marker(status=_visible_status(False, upstream), stored=False,
                           outcome="budget_exhausted", upstream=upstream, source_host=host)
        result = _media_cache_image(target_url, timeout=timeout)
        status = _text(result.get("status"))
        if status == "skipped":
            # 域名不在既有白名单里:没花任何带宽,额度退回。
            reservation.refund()
            return _marker(status=_visible_status(False, upstream), stored=False,
                           outcome="not_allowlisted", upstream=upstream,
                           reason=_text(result.get("reason")), source_host=host)
        if status != "cached":
            return _marker(status=_visible_status(False, upstream), stored=False,
                           outcome="fetch_failed", upstream=upstream,
                           reason=_text(result.get("reason")), source_host=host)
        fetched = True
        cache_url = _text(result.get("url"))

    digest = local_cache_digest(cache_url)
    if not digest:
        return _marker(status=_visible_status(False, upstream), stored=False,
                       outcome="cache_url_unrecognized", upstream=upstream, source_host=host)

    backup = _ensure_offbox_backup(
        digest=digest,
        source_url=target_url,
        platform=platform,
        external_id=external_id,
        reservation=reservation,
        conn=conn,
        allow_ledger_writes=allow_ledger_writes,
    )
    return _marker(
        status=STATUS_DURABLE,
        stored=True,
        outcome="landed" if fetched else "already_cached",
        upstream=upstream,
        digest=digest,
        storage_backend=_text(backup.get("storage_backend")) or "local",
        r2_key=_text(backup.get("r2_key")),
        reason=_text(backup.get("reason")),
        source_host=host,
    )


def _ensure_offbox_backup(
    *,
    digest: str,
    source_url: str,
    platform: str,
    external_id: str,
    reservation: _Reservation,
    conn: Any | None,
    allow_ledger_writes: bool,
) -> dict[str, Any]:
    """本地副本已在手,再尽力放一份异地备份,并把这份资产登记进媒体台账。"""
    entry = _media_cached_image_file(digest)
    if not entry:
        return {"storage_backend": "", "r2_key": "", "reason": "cache_file_missing"}
    if not allow_ledger_writes:
        # 台账登记与 R2 上传都会在共享连接上 commit,调用方事务还开着时一律不碰。
        return {"storage_backend": "local", "r2_key": "", "reason": "ledger_write_deferred"}
    cache_path, content_type = entry
    ledger = {
        "digest": digest,
        "cache_path": cache_path,
        "content_type": content_type,
        "source_url": source_url,
        "platform": platform,
        "external_id": external_id,
    }
    if not _media_r2_enabled():
        _record_asset(**ledger, storage_backend="local", r2_key="", cache_url=_public_cache_url(digest))
        return {"storage_backend": "local", "r2_key": "", "reason": "r2_disabled"}

    existing = _ledger_r2_row(conn, digest=digest)
    if existing:
        return {"storage_backend": "r2", "r2_key": _text(existing.get("r2_key")), "reason": "already_uploaded"}

    checksum = _file_checksum(cache_path)
    twin = _ledger_r2_row(conn, checksum=checksum) if checksum else None
    if twin:
        # 同一张图换了签名 URL(IG/TikTok 天天换)→ 内容校验和相同,直接复用已在
        # R2 的对象,只补一条 URL→对象的映射,绝不重复上传。
        _record_asset(**ledger, storage_backend="r2", r2_key=_text(twin.get("r2_key")),
                      cache_url=_text(twin.get("cache_url")), checksum=checksum)
        return {"storage_backend": "r2", "r2_key": _text(twin.get("r2_key")), "reason": "reused_identical_object"}

    if not reservation.claim():
        _record_asset(**ledger, storage_backend="local", r2_key="", cache_url=_public_cache_url(digest), checksum=checksum)
        return {"storage_backend": "local", "r2_key": "", "reason": "budget_exhausted"}

    result = _media_upload_to_r2(
        digest=digest,
        cache_path=cache_path,
        content_type=content_type,
        source_url=source_url,
        platform=platform,
        external_id=external_id,
    )
    if _text(result.get("storage_backend")) == "r2":
        return {"storage_backend": "r2", "r2_key": _text(result.get("r2_key")), "reason": "uploaded"}
    _record_asset(**ledger, storage_backend="local", r2_key="", cache_url=_public_cache_url(digest), checksum=checksum)
    return {
        "storage_backend": "local",
        "r2_key": "",
        "reason": _text(result.get("r2_error")) or "r2_upload_failed",
    }


def _record_asset(
    *,
    digest: str,
    cache_path: Path,
    content_type: str,
    source_url: str,
    platform: str,
    external_id: str,
    storage_backend: str,
    r2_key: str,
    cache_url: str,
    checksum: str = "",
) -> None:
    """登记进既有媒体台账 ``vkpi_media_cache_assets``(同 asset_uid 幂等 upsert)。"""
    try:
        size_bytes = cache_path.stat().st_size
    except OSError:
        size_bytes = 0
    _media_record_asset(
        {
            "media_kind": "image",
            "platform": _text(platform).lower(),
            "external_id": _text(external_id),
            "source_url": source_url,
            "digest": digest,
            "checksum": checksum or _file_checksum(cache_path),
            "content_type": content_type,
            "size_bytes": size_bytes,
            "storage_backend": storage_backend,
            "local_path": str(cache_path),
            "r2_key": r2_key,
            "cache_url": cache_url,
            "status": "cached",
            "metadata": {"method": AVATAR_LANDING_METHOD},
        }
    )


def _ledger_r2_row(conn: Any | None, *, digest: str = "", checksum: str = "") -> dict[str, Any] | None:
    """查媒体台账里已在 R2 的同一份资产;台账不可读时按「没有」处理(方向安全:最多重传一次)。"""
    column = "digest" if digest else "checksum"
    value = digest or checksum
    if not value:
        return None
    try:
        db = conn or get_conn()
        row = db.execute(
            f"""
            SELECT r2_key, cache_url, storage_backend
            FROM vkpi_media_cache_assets
            WHERE media_kind=? AND {column}=? AND storage_backend=? AND status=?
            ORDER BY updated_at DESC
            LIMIT 1
            """,  # noqa: S608 — column 是本函数内两个字面量之一,无外部输入

            ("image", value, "r2", "cached"),
        ).fetchone()
    except Exception:
        logger.warning("media cache ledger lookup unavailable; may re-upload", exc_info=True)
        return None
    if not row:
        return None
    data = dict(row)
    return data if _text(data.get("r2_key")) else None


def _marker(
    *,
    status: str,
    stored: bool,
    outcome: str,
    upstream: str,
    digest: str = "",
    storage_backend: str = "",
    r2_key: str = "",
    reason: str = "",
    source_host: str = "",
) -> dict[str, Any]:
    """诚实标记:只记结论与主机名,绝不把带签名的原链再抄一份进 raw。"""
    return {
        "version": AVATAR_MEDIA_MARKER_KEY,
        "method": AVATAR_LANDING_METHOD,
        "status": status,
        "stored": bool(stored),
        "offbox_backup": storage_backend == "r2",
        "storage_backend": storage_backend,
        "cache_url": _public_cache_url(digest) if digest and stored else "",
        "digest": digest,
        "r2_key": r2_key,
        "upstream_status": upstream,
        "source_host": source_host,
        "outcome": outcome,
        "reason": reason,
        "checked_at": _utcnow(),
    }


def _visible_status(stored: bool, upstream: str) -> str:
    if stored:
        return STATUS_DURABLE
    # 上游「稳定直连」也只是别人家的稳定:我们没存,对外就叫 external。
    return STATUS_EXTERNAL if upstream == STATUS_DURABLE else (upstream or STATUS_UNKNOWN)


def local_cache_digest(value: Any) -> str:
    text = _text(value).strip()
    for prefix in _LOCAL_CACHE_PREFIXES:
        if text.startswith(prefix):
            digest = text[len(prefix):]
            if _DIGEST_RE.fullmatch(digest):
                return digest.lower()
    return ""


def _public_cache_url(digest: str) -> str:
    return f"{PUBLIC_IMAGE_CACHE_PREFIX}/{digest}" if digest else ""


def _upstream_policy(url: str) -> tuple[str, str]:
    from app.services.intelligence.account_scan_helpers import _avatar_url_policy

    return _avatar_url_policy(url)


def _host(url: str) -> str:
    try:
        return (urlparse(_text(url)).hostname or "").lower()
    except ValueError:
        return ""


def _file_checksum(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        logger.warning("avatar cache file checksum unavailable", exc_info=True)
        return ""


def _release_validation_fenced() -> bool:
    try:
        from app.core.release_validation import release_validation_active

        return bool(release_validation_active())
    except Exception:
        logger.warning("release validation probe unavailable; landing fails closed", exc_info=True)
        return True


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (str, bytes)) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- 媒体缓存边界:全部懒加载 -------------------------------------------------
# 媒体域由另一条车道维护。这里一律函数内懒 import,任何改名/搬家都只会让「落地」
# 降级,不会在应用启动期炸掉 profile_basics 的导入链,更不会阻断建档。


def _media_cache_image(url: str, *, timeout: int) -> dict[str, Any]:
    from app.domains.media.cache import cache_image

    result = cache_image(url, timeout=timeout)
    return result if isinstance(result, dict) else {}


def _media_cached_image_url(url: str) -> str:
    from app.domains.media.cache import cached_image_url

    return _text(cached_image_url(url))


def _media_cached_image_file(digest: str) -> tuple[Path, str] | None:
    from app.domains.media.cache import cached_image_file

    return cached_image_file(digest)


def _media_normalize_image_url(url: str) -> Any:
    """白名单预检(与 scripts/ops/prewarm_kol_pool_avatars.py 同一口径)。

    ``None`` = 明确不在白名单;``_NORMALIZER_UNAVAILABLE`` = 预检本身没法做,
    两者必须分开,否则「媒体域改名」会被当成「域名不合法」而悄悄停掉全部落地。
    """
    try:
        from app.domains.media.cache_core import _normalize_image_url
    except ImportError:
        logger.warning("media image-url normalizer unavailable; falling back to cache_image gate")
        return _NORMALIZER_UNAVAILABLE
    return _normalize_image_url(url)


def _media_r2_enabled() -> bool:
    try:
        from app.domains.media.cache_core import _media_cache_r2_enabled
    except ImportError:
        logger.warning("media R2 probe unavailable; treating off-box backup as disabled")
        return False
    return bool(_media_cache_r2_enabled())


def _media_upload_to_r2(
    *,
    digest: str,
    cache_path: Path,
    content_type: str,
    source_url: str,
    platform: str,
    external_id: str,
) -> dict[str, Any]:
    from app.domains.media.cache_core import _upload_to_r2_if_enabled

    result = _upload_to_r2_if_enabled(
        media_kind="image",
        digest=digest,
        cache_path=cache_path,
        content_type=content_type,
        source_url=source_url,
        platform=_text(platform).lower(),
        external_id=_text(external_id),
    )
    return result if isinstance(result, dict) else {}


def _media_record_asset(payload: dict[str, Any]) -> None:
    from app.domains.media.cache_core import _record_media_cache_asset

    _record_media_cache_asset(payload)
