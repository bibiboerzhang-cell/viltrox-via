"""头像落地(车道 2)验收:落地不阻断建档 / 幂等 / 批量闸 / 两类链接 / 诚实标记。

背景实测(prod a05e48dd3,2026-08-25):池表 2034 行头像里 927 行是签名型 CDN 外链,
717 行超 14 天,抽样 12 条探活 12/12 全死。本测试守的就是「头像必须落进我们自己的
存储,而且落不成也不许连累建档」这条底线。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.domains.kol import avatar_landing, profile_basics


PUBLIC_PREFIX = "/api/vkpi-media/image-cache"
SIGNED_SECRET = "never-leak-this-signature"
# 稳定直连(YouTube):用户明确要求它也要落地。
STABLE_AVATAR = "https://yt3.ggpht.com/ytc/stable-channel-avatar=s176-c-k-c0x00ffffff-no-rj"
# 签名型 CDN(Instagram),oe 是十六进制到期戳:FFFFFFFF ≈ 2106 年,尚未过期。
SIGNED_AVATAR = (
    "https://scontent-lax3-1.cdninstagram.com/v/t51.2885-19/avatar.jpg"
    f"?_nc_ht=scontent-lax3-1.cdninstagram.com&oe=FFFFFFFF&sig={SIGNED_SECRET}"
)
# 同一张图换了签名(IG/TikTok 每天都在换)。
ROTATED_AVATAR = (
    "https://scontent-lax3-1.cdninstagram.com/v/t51.2885-19/avatar.jpg"
    f"?_nc_ht=scontent-lax3-1.cdninstagram.com&oe=FFFFFFFE&sig={SIGNED_SECRET}-rotated"
)
# oe=1 → 1970 年到期,实测这类链接已经 12/12 全死。
EXPIRED_AVATAR = (
    "https://scontent-lax3-1.cdninstagram.com/v/t51.2885-19/dead.jpg"
    "?_nc_ht=scontent-lax3-1.cdninstagram.com&oe=1"
)
TIKTOK_AVATAR = "https://p16-sign-va.tiktokcdn.com/avatar/creator.jpeg?x-expires=4102444800&x-signature=abc"

LEDGER_DDL = """
CREATE TABLE vkpi_media_cache_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_uid TEXT NOT NULL UNIQUE,
    media_kind TEXT NOT NULL DEFAULT 'video',
    platform TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    source_url_hash TEXT NOT NULL DEFAULT '',
    digest TEXT NOT NULL DEFAULT '',
    checksum TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    storage_backend TEXT NOT NULL DEFAULT 'local',
    local_path TEXT NOT NULL DEFAULT '',
    r2_key TEXT NOT NULL DEFAULT '',
    cache_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'cached',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

POOL_DDL = """
CREATE TABLE vkpi_kol_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_uid TEXT,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    display_name TEXT DEFAULT '',
    profile_url TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',
    bio TEXT DEFAULT '',
    followers INTEGER,
    posts_count INTEGER,
    last_video_at TEXT,
    raw_platform_data TEXT NOT NULL DEFAULT '{}',
    profile_backfilled_at TEXT,
    duplicate_of_id INTEGER,
    viltrox_fit_score REAL,
    viltrox_fit_reason TEXT,
    UNIQUE(platform, handle)
);
CREATE TABLE vkpi_kol_pool_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kol_pool_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    profile_url TEXT DEFAULT '',
    confidence REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT,
    UNIQUE(platform, handle)
);
"""


def _digest(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class FakeMediaCache:
    """替身媒体缓存:磁盘是真的(校验和要算),网络与 R2 是假的。"""

    def __init__(
        self,
        root: Path,
        conn: sqlite3.Connection,
        *,
        r2_enabled: bool = True,
        payloads: dict[str, bytes] | None = None,
        failures: set[str] | None = None,
    ) -> None:
        self.root = root
        self.conn = conn
        self.r2_enabled = r2_enabled
        self.payloads = dict(payloads or {})
        self.failures = set(failures or ())
        self.fetches: list[str] = []
        self.uploads: list[str] = []
        self.records: list[dict[str, Any]] = []

    # --- 被 monkeypatch 顶替的媒体边界 ---------------------------------
    def cache_image(self, url: str, *, timeout: int = 8) -> dict[str, Any]:
        digest = _digest(url)
        path = self.root / digest
        if path.exists():
            return {"status": "cached", "url": f"{PUBLIC_PREFIX}/{digest}"}
        self.fetches.append(url)
        if url in self.failures:
            return {"status": "failed", "reason": "HTTPError"}
        path.write_bytes(self.payloads.get(url, f"bytes-of-{digest[:8]}".encode()))
        (self.root / f"{digest}.content-type").write_text("image/jpeg")
        return {"status": "cached", "url": f"{PUBLIC_PREFIX}/{digest}"}

    def cached_image_url(self, url: str) -> str:
        digest = _digest(url)
        return f"{PUBLIC_PREFIX}/{digest}" if (self.root / digest).exists() else ""

    def cached_image_file(self, digest: str) -> tuple[Path, str] | None:
        path = self.root / digest
        return (path, "image/jpeg") if path.exists() else None

    def r2_enabled_probe(self) -> bool:
        return self.r2_enabled

    def upload(
        self,
        *,
        digest: str,
        cache_path: Path,
        content_type: str,
        source_url: str,
        platform: str,
        external_id: str,
    ) -> dict[str, Any]:
        self.uploads.append(digest)
        r2_key = f"vkpi/media-cache/images/{digest}.jpg"
        cache_url = f"https://cdn.example.test/{r2_key}"
        # 真实 _upload_to_r2_if_enabled 上传成功后自己登记台账,替身照做。
        self.record(
            {
                "media_kind": "image",
                "platform": platform,
                "external_id": external_id,
                "source_url": source_url,
                "digest": digest,
                "checksum": hashlib.sha256(cache_path.read_bytes()).hexdigest(),
                "content_type": content_type,
                "size_bytes": cache_path.stat().st_size,
                "storage_backend": "r2",
                "local_path": str(cache_path),
                "r2_key": r2_key,
                "cache_url": cache_url,
                "status": "cached",
            }
        )
        return {"storage_backend": "r2", "r2_key": r2_key, "cache_url": cache_url}

    def record(self, payload: dict[str, Any]) -> None:
        self.records.append(dict(payload))
        source_url = str(payload.get("source_url") or "")
        seed = "|".join(
            [
                str(payload.get("media_kind") or "image"),
                str(payload.get("platform") or ""),
                str(payload.get("external_id") or ""),
                source_url,
                str(payload.get("digest") or ""),
            ]
        )
        self.conn.execute(
            """
            INSERT INTO vkpi_media_cache_assets (
                asset_uid, media_kind, platform, external_id, source_url, source_url_hash,
                digest, checksum, content_type, size_bytes, storage_backend, local_path,
                r2_key, cache_url, status, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_uid) DO UPDATE SET
                storage_backend=excluded.storage_backend,
                checksum=excluded.checksum,
                r2_key=excluded.r2_key,
                cache_url=excluded.cache_url,
                status=excluded.status
            """,
            (
                hashlib.sha256(seed.encode("utf-8")).hexdigest(),
                str(payload.get("media_kind") or "image"),
                str(payload.get("platform") or ""),
                str(payload.get("external_id") or ""),
                source_url,
                hashlib.sha256(source_url.encode("utf-8")).hexdigest() if source_url else "",
                str(payload.get("digest") or ""),
                str(payload.get("checksum") or ""),
                str(payload.get("content_type") or ""),
                int(payload.get("size_bytes") or 0),
                str(payload.get("storage_backend") or "local"),
                str(payload.get("local_path") or ""),
                str(payload.get("r2_key") or ""),
                str(payload.get("cache_url") or ""),
                str(payload.get("status") or "cached"),
                json.dumps(payload.get("metadata") or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()


@pytest.fixture
def ledger_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(LEDGER_DDL)
    conn.commit()
    return conn


@pytest.fixture
def install_media(monkeypatch: pytest.MonkeyPatch):
    def _install(fake: FakeMediaCache) -> FakeMediaCache:
        monkeypatch.setattr(avatar_landing, "_media_cache_image", fake.cache_image)
        monkeypatch.setattr(avatar_landing, "_media_cached_image_url", fake.cached_image_url)
        monkeypatch.setattr(avatar_landing, "_media_cached_image_file", fake.cached_image_file)
        monkeypatch.setattr(avatar_landing, "_media_r2_enabled", fake.r2_enabled_probe)
        monkeypatch.setattr(avatar_landing, "_media_upload_to_r2", fake.upload)
        monkeypatch.setattr(avatar_landing, "_media_record_asset", fake.record)
        monkeypatch.setattr(avatar_landing, "_release_validation_fenced", lambda: False)
        return fake

    return _install


# ---------------------------------------------------------------------------
# 两类链接都落地 + 诚实标记
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected_upstream"),
    [
        (STABLE_AVATAR, "durable"),
        (SIGNED_AVATAR, "ephemeral"),
        (TIKTOK_AVATAR, "ephemeral"),
    ],
)
def test_stable_and_signed_avatars_all_land(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media, url: str, expected_upstream: str
) -> None:
    fake = install_media(FakeMediaCache(tmp_path, ledger_conn))

    marker = avatar_landing.land_avatar_url(url, platform="instagram", external_id="creator", conn=ledger_conn)

    assert marker["status"] == "durable"
    assert marker["stored"] is True
    assert marker["outcome"] == "landed"
    assert marker["upstream_status"] == expected_upstream
    assert marker["storage_backend"] == "r2"
    assert marker["offbox_backup"] is True
    assert marker["cache_url"] == f"{PUBLIC_PREFIX}/{_digest(url)}"
    assert fake.fetches == [url]
    assert fake.uploads == [_digest(url)]


def test_marker_is_honest_about_external_only_rows(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media
) -> None:
    """抓不下来时绝不许自称 durable —— 稳定直连也只是「别人家的稳定」。"""
    fake = install_media(FakeMediaCache(tmp_path, ledger_conn, failures={STABLE_AVATAR}))

    marker = avatar_landing.land_avatar_url(STABLE_AVATAR, conn=ledger_conn)

    assert marker["stored"] is False
    assert marker["status"] == "external"
    assert marker["upstream_status"] == "durable"
    assert marker["outcome"] == "fetch_failed"
    assert marker["reason"] == "HTTPError"
    assert marker["cache_url"] == ""
    assert fake.uploads == []


def test_marker_never_carries_the_signed_url(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media
) -> None:
    install_media(FakeMediaCache(tmp_path, ledger_conn))

    marker = avatar_landing.land_avatar_url(SIGNED_AVATAR, conn=ledger_conn)

    serialized = json.dumps(marker, ensure_ascii=False)
    assert SIGNED_SECRET not in serialized
    assert "?" not in serialized
    assert marker["source_host"] == "scontent-lax3-1.cdninstagram.com"


def test_expired_upstream_is_not_refetched(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media
) -> None:
    """过期签名链实测 12/12 全死:不空转烧带宽,诚实标 expired 等重抓档案。"""
    fake = install_media(FakeMediaCache(tmp_path, ledger_conn))

    marker = avatar_landing.land_avatar_url(EXPIRED_AVATAR, conn=ledger_conn)

    assert marker["status"] == "expired"
    assert marker["stored"] is False
    assert marker["outcome"] == "upstream_expired"
    assert fake.fetches == []


def test_local_only_storage_still_counts_as_landed(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media
) -> None:
    """R2 未开时本地副本仍是我们自己的存储,但必须诚实说没有异地备份。"""
    fake = install_media(FakeMediaCache(tmp_path, ledger_conn, r2_enabled=False))

    marker = avatar_landing.land_avatar_url(STABLE_AVATAR, conn=ledger_conn)

    assert marker["status"] == "durable"
    assert marker["stored"] is True
    assert marker["storage_backend"] == "local"
    assert marker["offbox_backup"] is False
    assert fake.uploads == []
    row = ledger_conn.execute(
        "SELECT storage_backend, media_kind FROM vkpi_media_cache_assets WHERE digest=?",
        (_digest(STABLE_AVATAR),),
    ).fetchone()
    assert dict(row) == {"storage_backend": "local", "media_kind": "image"}


def test_already_local_avatar_url_is_free(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media
) -> None:
    fake = install_media(FakeMediaCache(tmp_path, ledger_conn))
    local_url = f"{PUBLIC_PREFIX}/{'a' * 64}"

    marker = avatar_landing.land_avatar_url(local_url, conn=ledger_conn)

    assert marker["status"] == "durable"
    assert marker["outcome"] == "already_local_url"
    assert fake.fetches == []
    assert fake.uploads == []


def test_release_validation_fence_blocks_landing(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install_media(FakeMediaCache(tmp_path, ledger_conn))
    monkeypatch.setattr(avatar_landing, "_release_validation_fenced", lambda: True)

    marker = avatar_landing.land_avatar_url(STABLE_AVATAR, conn=ledger_conn)

    assert marker["outcome"] == "release_validation_fenced"
    assert marker["stored"] is False
    assert fake.fetches == []


def test_landing_switch_off_reports_external(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install_media(FakeMediaCache(tmp_path, ledger_conn))
    monkeypatch.setenv("VKPI_AVATAR_LANDING_ENABLED", "0")

    marker = avatar_landing.land_avatar_url(STABLE_AVATAR, conn=ledger_conn)

    assert marker["outcome"] == "landing_disabled"
    assert marker["status"] == "external"
    assert fake.fetches == []


def test_non_allowlisted_host_costs_no_budget(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media
) -> None:
    fake = install_media(FakeMediaCache(tmp_path, ledger_conn))
    budget = avatar_landing.AvatarLandingBudget(2)

    marker = avatar_landing.land_avatar_url(
        "https://avatars.example.com/not-allowlisted.jpg", budget=budget, conn=ledger_conn
    )

    assert marker["outcome"] == "not_allowlisted"
    assert marker["stored"] is False
    assert budget.snapshot() == {"limit": 2, "used": 0, "remaining": 2}
    assert fake.fetches == []


# ---------------------------------------------------------------------------
# 幂等
# ---------------------------------------------------------------------------


def test_repeat_landing_never_refetches_or_reuploads(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media
) -> None:
    fake = install_media(FakeMediaCache(tmp_path, ledger_conn))

    first = avatar_landing.land_avatar_url(SIGNED_AVATAR, conn=ledger_conn)
    second = avatar_landing.land_avatar_url(SIGNED_AVATAR, conn=ledger_conn)

    assert first["outcome"] == "landed"
    assert second["outcome"] == "already_cached"
    assert second["status"] == "durable"
    assert second["r2_key"] == first["r2_key"]
    assert fake.fetches == [SIGNED_AVATAR]
    assert fake.uploads == [_digest(SIGNED_AVATAR)]


def test_rotated_signature_reuses_the_uploaded_object(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media
) -> None:
    """同一张图换签名 URL:必须按内容校验和复用 R2 对象,不许重复上传。"""
    same_bytes = b"identical-avatar-image-bytes"
    fake = install_media(
        FakeMediaCache(
            tmp_path,
            ledger_conn,
            payloads={SIGNED_AVATAR: same_bytes, ROTATED_AVATAR: same_bytes},
        )
    )

    first = avatar_landing.land_avatar_url(SIGNED_AVATAR, conn=ledger_conn)
    rotated = avatar_landing.land_avatar_url(ROTATED_AVATAR, conn=ledger_conn)

    assert rotated["status"] == "durable"
    assert rotated["storage_backend"] == "r2"
    assert rotated["reason"] == "reused_identical_object"
    assert rotated["r2_key"] == first["r2_key"]
    assert fake.fetches == [SIGNED_AVATAR, ROTATED_AVATAR]
    assert fake.uploads == [_digest(SIGNED_AVATAR)]


# ---------------------------------------------------------------------------
# 批量闸
# ---------------------------------------------------------------------------


def test_batch_budget_caps_landings(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media
) -> None:
    fake = install_media(FakeMediaCache(tmp_path, ledger_conn))
    urls = [f"https://yt3.ggpht.com/ytc/avatar-{index}=s176" for index in range(4)]
    budget = avatar_landing.AvatarLandingBudget(2)

    markers = [avatar_landing.land_avatar_url(url, budget=budget, conn=ledger_conn) for url in urls]

    assert [marker["outcome"] for marker in markers] == [
        "landed",
        "landed",
        "budget_exhausted",
        "budget_exhausted",
    ]
    assert [marker["stored"] for marker in markers] == [True, True, False, False]
    assert markers[2]["status"] == "external"
    assert fake.fetches == urls[:2]
    assert fake.uploads == [_digest(url) for url in urls[:2]]
    assert budget.snapshot() == {"limit": 2, "used": 2, "remaining": 0}


def test_already_landed_rows_do_not_eat_budget(
    tmp_path: Path, ledger_conn: sqlite3.Connection, install_media
) -> None:
    """重复跑同一批不会被自己的历史成绩挤爆闸门。"""
    fake = install_media(FakeMediaCache(tmp_path, ledger_conn))
    avatar_landing.land_avatar_url(STABLE_AVATAR, conn=ledger_conn)

    budget = avatar_landing.AvatarLandingBudget(1)
    repeat = avatar_landing.land_avatar_url(STABLE_AVATAR, budget=budget, conn=ledger_conn)
    fresh = avatar_landing.land_avatar_url(SIGNED_AVATAR, budget=budget, conn=ledger_conn)

    assert repeat["outcome"] == "already_cached"
    assert fresh["outcome"] == "landed"
    assert budget.snapshot() == {"limit": 1, "used": 1, "remaining": 0}
    assert fake.fetches == [STABLE_AVATAR, SIGNED_AVATAR]


def test_batch_limit_is_env_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_AVATAR_LANDING_MAX_PER_BATCH", "9999")
    assert avatar_landing.avatar_landing_batch_limit() == avatar_landing.MAX_BATCH_LANDING_LIMIT
    monkeypatch.setenv("VKPI_AVATAR_LANDING_MAX_PER_BATCH", "-5")
    assert avatar_landing.avatar_landing_batch_limit() == 0
    monkeypatch.setenv("VKPI_AVATAR_LANDING_MAX_PER_BATCH", "not-a-number")
    assert avatar_landing.avatar_landing_batch_limit() == avatar_landing.DEFAULT_BATCH_LANDING_LIMIT


# ---------------------------------------------------------------------------
# 与建档主链的耦合:标记落库 + 失败不阻断
# ---------------------------------------------------------------------------


def _pool_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(POOL_DDL)
    conn.executescript(LEDGER_DDL)
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool (id, pool_uid, platform, handle, display_name, raw_platform_data)
        VALUES (7, 'pool-7', 'instagram', 'dsipictures', 'DSI Pictures', '{"existing_key": 1}')
        """
    )
    conn.commit()
    return conn


def test_profile_write_stamps_the_honest_marker(tmp_path: Path, install_media) -> None:
    conn = _pool_conn()
    fake = install_media(FakeMediaCache(tmp_path, conn))

    result = profile_basics.write_kol_profile_basics(
        7, {"avatar_url": SIGNED_AVATAR}, dry_run=False, conn=conn
    )

    assert result["ok"] is True
    assert "avatar_url" in result["fields_written"]
    assert result["avatar_landing"]["status"] == "durable"
    assert result["avatar_landing"]["stamped"] is True

    row = dict(conn.execute("SELECT avatar_url, raw_platform_data FROM vkpi_kol_pool WHERE id=7").fetchone())
    # 上游原链保持不动 —— 它是将来重抓的唯一线索。
    assert row["avatar_url"] == SIGNED_AVATAR
    payload = json.loads(row["raw_platform_data"])
    assert payload["existing_key"] == 1
    marker = payload[avatar_landing.AVATAR_MEDIA_MARKER_KEY]
    assert marker["stored"] is True
    assert marker["cache_url"] == f"{PUBLIC_PREFIX}/{_digest(SIGNED_AVATAR)}"
    assert fake.uploads == [_digest(SIGNED_AVATAR)]


def test_profile_write_survives_landing_fetch_failure(tmp_path: Path, install_media) -> None:
    conn = _pool_conn()
    install_media(FakeMediaCache(tmp_path, conn, failures={SIGNED_AVATAR}))

    result = profile_basics.write_kol_profile_basics(
        7, {"avatar_url": SIGNED_AVATAR}, dry_run=False, conn=conn
    )

    assert result["ok"] is True
    row = dict(conn.execute("SELECT avatar_url, raw_platform_data FROM vkpi_kol_pool WHERE id=7").fetchone())
    assert row["avatar_url"] == SIGNED_AVATAR
    marker = json.loads(row["raw_platform_data"])[avatar_landing.AVATAR_MEDIA_MARKER_KEY]
    assert marker["stored"] is False
    assert marker["status"] == "ephemeral"
    assert marker["outcome"] == "fetch_failed"


def test_profile_write_survives_lander_exception(
    tmp_path: Path, install_media, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连落地器本身炸了,建档也必须成功 —— 这是本车道最硬的一条底线。"""
    conn = _pool_conn()
    install_media(FakeMediaCache(tmp_path, conn))

    def _explode(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("media cache exploded")

    monkeypatch.setattr(avatar_landing, "land_avatar_url", _explode)

    result = profile_basics.write_kol_profile_basics(
        7, {"avatar_url": STABLE_AVATAR}, dry_run=False, conn=conn
    )

    assert result["ok"] is True
    assert result["avatar_landing"] == {}
    assert result["viltrox_fit_score_untouched"] is True
    row = dict(conn.execute("SELECT avatar_url, viltrox_fit_score FROM vkpi_kol_pool WHERE id=7").fetchone())
    assert row["avatar_url"] == STABLE_AVATAR
    assert row["viltrox_fit_score"] is None


def test_uncommitted_write_never_commits_the_media_ledger(tmp_path: Path, install_media) -> None:
    """commit_write=False 的调用方靠 rollback 去重,落地绝不许替它提前 commit。"""
    conn = _pool_conn()
    fake = install_media(FakeMediaCache(tmp_path, conn))

    result = profile_basics.write_kol_profile_basics(
        7, {"avatar_url": STABLE_AVATAR}, dry_run=False, conn=conn, commit_write=False
    )
    conn.rollback()

    assert result["ok"] is True
    marker = result["avatar_landing"]
    assert marker["stored"] is True
    assert marker["storage_backend"] == "local"
    assert marker["offbox_backup"] is False
    assert marker["reason"] == "ledger_write_deferred"
    # 台账零写入、R2 零上传 → 调用方的 rollback 依然真的回滚得掉。
    assert fake.uploads == []
    assert fake.records == []
    assert int(dict(conn.execute("SELECT COUNT(*) AS n FROM vkpi_media_cache_assets").fetchone())["n"]) == 0
    assert dict(conn.execute("SELECT avatar_url FROM vkpi_kol_pool WHERE id=7").fetchone())["avatar_url"] == ""


def test_dry_run_never_touches_the_network(tmp_path: Path, install_media) -> None:
    conn = _pool_conn()
    fake = install_media(FakeMediaCache(tmp_path, conn))

    result = profile_basics.write_kol_profile_basics(7, {"avatar_url": SIGNED_AVATAR}, dry_run=True, conn=conn)

    assert result["dry_run"] is True
    assert fake.fetches == []
    assert fake.uploads == []
