"""发现墙缺陷合并波(2026-08-25):归一身份键 / 品牌官号建档闸 / 翻页确定性。

守的是三条用户可见的事实:

① 同一个人不该在**单次发现结果**里反复出现——候选去重键按归一身份成键,
   而不是按「首个非空原始字段」(同一个人一次带 handle、一次只带 channel_url 就成两把键);
② 品牌官方账号不当外部达人——**新建档**时拦下并记诚实原因,存量行照常刷新、绝不删行;
③ 翻页必须确定——排序键并列时 LIMIT/OFFSET 的行序由执行计划自由决定,
   同一个人可能一页都翻不到(隔离库实测 2009 条取回、去重只剩 1999,10 个人取不到)。

同时守「保守」:判据宁可漏拦也不误吃真达人(sonyalpharumors / sonya_official 必须放行),
判据取不到时的兜底方向必须朝安全的一边倒,且必须留痕(失败绝不静默)。

**本文件不覆盖的东西**(合并进主线时裁掉,主线已有更强的同名能力,见
``pool_identity_key`` 模块头):池行存量重复折叠、落库前按身份反查既有行、读端头像闸。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import connection as db_connection
from app.db.connection import get_conn
import app.platform.db.schema_product_industry as product_industry_schema
from app.domains.kol.brand_official_gate import (
    BRAND_OFFICIAL_SKIP_REASON,
    brand_official_match,
    configured_brand_tokens,
    is_brand_official_row,
)
from app.domains.kol.pool_identity_key import (
    discovery_candidate_key,
    handle_is_identity_signal,
    identity_keys,
    normalize_handle_key,
    normalize_profile_url_key,
    site_author_key,
    url_is_identity_signal,
)
from app.domains.kol import profile_discovery_provider as _provider
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema

# 本文件测的是真实现,故按属性现取现用:门面壳 `profile_discovery` 只在调用期间把自己的值
# 装进本模块、退出即还原(见 _CompatBinding),所以这里读到的必须一直是真函数。若哪天门面
# 又开始往真实现里写死,先跑 tests/test_kol_search_quality_guardrails.py 再跑本文件就会炸——
# 这正是我们要的报警,别再改成收集期抓函数对象把它盖回去。


def _keys(platform: str, **row: Any) -> list[str]:
    return identity_keys({"platform": platform, **row})


def _shares_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """两行是否会被判成同一个人(= 身份键有交集)。"""
    return bool(set(identity_keys(left)).intersection(identity_keys(right)))


# ───────────────────────── 归一身份键(候选去重的地基) ─────────────────────────


def test_handle_key_folds_case_at_and_url_decorations() -> None:
    """大小写 / @ / 主页 URL 三种写法必须落到同一把 handle 键(逐字比对只认其中一种)。"""
    keys = {
        normalize_handle_key("BrandonLi", "youtube"),
        normalize_handle_key("@brandonli", "yt"),
        normalize_handle_key("https://www.youtube.com/@BrandonLi", "youtube"),
        normalize_handle_key(" brandonli ", "youtube"),
    }
    assert keys == {"brandonli"}


def test_profile_url_key_strips_fragment_and_search_junk() -> None:
    """prod 实证的两类脏 URL(#google_vignette / ?s=viltrox)必须归到干净的同一把键。"""
    assert normalize_profile_url_key("https://sonyalpha.blog/#google_vignette") == "sonyalpha.blog"
    assert normalize_profile_url_key("https://35mmc.com/?s=viltrox") == "35mmc.com"
    assert normalize_profile_url_key("http://www.opticallimits.com/review/") == "opticallimits.com/review"


def test_profile_url_key_keeps_query_when_query_is_the_identity() -> None:
    """facebook 的 profile.php?id=… 里 query 就是身份——丢了会把不同的人并成一个。"""
    left = normalize_profile_url_key("https://facebook.com/profile.php?id=123")
    right = normalize_profile_url_key("https://facebook.com/profile.php?id=456")
    assert left != right
    assert "id=123" in left


def test_identity_key_never_merges_across_platforms() -> None:
    """跨平台同名 handle 是两个人(prod 33 组几乎全是这种),绝不能并。"""
    youtube = _keys("youtube", handle="brandonli")
    instagram = _keys("instagram", handle="brandonli")
    assert youtube and instagram and youtube[0] != instagram[0]


def test_discovery_candidate_key_matches_handle_only_and_url_only_items() -> None:
    """同一个人一次带 handle、一次只带 channel_url,单次结果内必须是同一把键。"""
    by_handle = discovery_candidate_key({"handle": "@ABC"}, "youtube")
    by_url = discovery_candidate_key({"channel_url": "https://youtube.com/@abc"}, "youtube")
    assert by_handle == by_url


def test_discovery_candidate_key_falls_back_to_the_old_field_order() -> None:
    """两把归一键都取不出来时,逐字退回旧口径(首个非空原始字段),行为不变。"""
    key = discovery_candidate_key({"channel_name": "Some Media Desk"}, "media")
    assert key == "media:some media desk"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("sony_中国", "sony_日本"),          # 旧口径双双归一为 'sony_'
        ("张三_01", "李四_01"),              # 旧口径双双归一为 '_01'
        ("映像プロデューサーlit", "lit"),      # 隔离库真行 id=4232:旧口径被削成 'lit'
    ],
)
def test_non_ascii_handles_never_collide_into_one_identity(left: str, right: str) -> None:
    """非 ASCII handle 绝不能被削成同一把键——那不只是显示错,是「把两个人当成一个人」。

    旧口径 ``[^a-z0-9._-]+`` 把非 ASCII 字符**删掉**;身份键一撞,凡是拿这把键做
    「是不是同一个人」判定的地方(候选去重、品牌闸的 handle 认不认)就全判错。
    """
    left_key = normalize_handle_key(left, "instagram")
    right_key = normalize_handle_key(right, "instagram")
    assert left_key and right_key and left_key != right_key
    assert not _shares_identity(
        {"platform": "instagram", "handle": left}, {"platform": "instagram", "handle": right}
    )


def test_pure_ascii_handle_keys_are_unchanged() -> None:
    """非 ASCII 修法不许动纯 ASCII 的既有口径(大小写/@/URL/合法符号全部照旧)。"""
    assert normalize_handle_key("BrandonLi", "youtube") == "brandonli"
    assert normalize_handle_key("@BrandonLi", "youtube") == "brandonli"
    assert normalize_handle_key("https://www.youtube.com/@BrandonLi/", "youtube") == "brandonli"
    assert normalize_handle_key("brandon.li_-", "youtube") == "brandon.li_-"
    assert normalize_handle_key("a b/c(d)", "youtube") == "abcd"
    assert normalize_handle_key("", "youtube") == ""


def test_cjk_only_handle_now_carries_identity_but_llm_junk_still_does_not() -> None:
    """纯中日韩 handle 现在能成键;LLM 失败短语仍必须出局。"""
    assert handle_is_identity_signal("映像プロデューサーlit", "youtube") is True
    # 隔离库真行 id=3326/3328:整段中文都是 LLM 失败话术,留了键就会把两条脏行并成一个人。
    assert handle_is_identity_signal("由于未提供<|产品型号|>的具体内容", "youtube") is False
    assert handle_is_identity_signal("方子聪-无（由于<|红人/媒体|>字段为空", "youtube") is False


def test_garbage_handle_rule_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """垃圾 handle 判据取不到时必须朝「不当身份」兜底,且必须留痕(不许静默)。"""
    import app.domains.kol.pool_identity_key as identity_module

    class _Boom:
        def __getattr__(self, name: str) -> Any:
            raise ImportError("pool_common unavailable")

    warned: list[str] = []
    monkeypatch.setitem(sys.modules, "app.domains.kol.pool_common", _Boom())
    monkeypatch.setattr(identity_module, "_GARBAGE_RULE_WARNED", set())
    monkeypatch.setattr(
        identity_module.logger, "warning", lambda msg, *a, **kw: warned.append(str(msg))
    )
    # 'reel' 是 URL 保留段残片:判据一失灵就 fail-open 的话,它会重新变成身份信号,
    # 把两个不同的人并成一行(9.5M 粉的真达人被并进 handle='reel' 的垃圾行)。
    assert identity_module._is_garbage_handle("reel") is True
    assert identity_module.handle_is_identity_signal("reel", "instagram") is False
    assert identity_module.identity_keys({"platform": "instagram", "handle": "reel"}) == []
    assert warned, "判不了必须告警,失败绝不静默"


def test_website_row_handles_are_not_identity() -> None:
    """站点行的 handle 是从文章路径捏的(opticallimits 的行 handle 叫 nikon):
    同名不同站绝不能并。"""
    assert handle_is_identity_signal("nikon", "media") is False
    assert not _shares_identity(
        {"platform": "media", "handle": "nikon", "profile_url": "https://opticallimits.com/"},
        {"platform": "media", "handle": "nikon", "profile_url": "https://photographylife.com/"},
    )


def test_url_reserved_segment_handles_never_merge_two_people() -> None:
    """instagram.com/p/xxx 这类贴文链接会把 handle 捏成 'p' —— 两个不同的人不能因此并成一行。"""
    assert handle_is_identity_signal("p", "instagram") is False
    assert not _shares_identity(
        {"platform": "instagram", "handle": "p", "profile_url": "https://instagram.com/p/aaa"},
        {"platform": "instagram", "handle": "p", "profile_url": "https://instagram.com/p/bbb"},
    )


def test_scraped_page_title_in_profile_url_is_never_an_identity() -> None:
    """真库 3643 / 3671 两行的 profile_url 抓成了页面标题「(2) Instagram」——
    两个真达人绝不能因为共用一句垃圾文本而被判成同一个人。"""
    assert normalize_profile_url_key("(2) Instagram") == ""
    assert not _shares_identity(
        {"id": 3643, "platform": "instagram", "handle": "_aguywithacamera",
         "display_name": "A Guy With A Camera", "profile_url": "(2) Instagram"},
        {"id": 3671, "platform": "instagram", "handle": "badiu.photography",
         "display_name": "Badiu Photography", "profile_url": "(2) Instagram"},
    )


def test_post_url_on_the_platforms_own_domain_is_never_an_identity() -> None:
    """真库 3307(alessandroz1,9.5M 粉)与 3309(handle='reel' 的垃圾行)
    共用 profile_url `instagram.com/reel` —— 判成同一个人就等于把一个真达人折没。"""
    assert url_is_identity_signal("https://www.instagram.com/reel", "instagram") is False
    assert url_is_identity_signal("https://www.instagram.com/p", "instagram") is False
    assert url_is_identity_signal("https://www.instagram.com/alessandroz1", "instagram") is True
    assert url_is_identity_signal(
        "https://www.youtube.com/channel/UC9C-IwKhChgKJkoaYQKhJoQ", "youtube"
    ) is True
    assert not _shares_identity(
        {"id": 3307, "platform": "instagram", "handle": "alessandroz1",
         "display_name": "reel", "profile_url": "https://www.instagram.com/reel"},
        {"id": 3309, "platform": "instagram", "handle": "reel",
         "display_name": "reel", "profile_url": "https://www.instagram.com/reel"},
    )


def test_two_authors_on_one_media_site_are_two_people() -> None:
    """真库 35mmc.com 上的 Hamish Gill 与 Mike Brooks 各有两行:
    同作者的两行是同一个人,不同作者绝不是(只按站点 URL 成键会把两人判成一个)。"""
    hamish_old = {"id": 1533, "platform": "media", "handle": "20",
                  "display_name": "35mmc-Hamish Gill - 【MEDIA】", "profile_url": "https://www.35mmc.com"}
    hamish_new = {"id": 3841, "platform": "media", "handle": "hamishgill",
                  "display_name": "35mmc-Hamish Gill", "profile_url": "https://www.35mmc.com"}
    mike = {"id": 3310, "platform": "media", "handle": "35mmc",
            "display_name": "35mmc-Mike Brooks - 【MEDIA】",
            "profile_url": "https://www.35mmc.com/?s=viltrox"}
    assert _shares_identity(hamish_old, hamish_new)
    assert not _shares_identity(hamish_old, mike)
    assert site_author_key(hamish_old) == "35mmchamishgill"
    assert site_author_key(mike) == "35mmcmikebrooks"


def test_same_media_site_same_author_is_one_person() -> None:
    """真库 opticallimits 的行(作者名同一个、handle 全是从文章路径捏的)仍判成同一个人。"""
    assert _shares_identity(
        {"id": 1534, "platform": "media", "handle": "nikon",
         "display_name": "opticallimits - 【MEDIA】", "profile_url": "https://opticallimits.com/"},
        {"id": 3318, "platform": "media", "handle": "opticallimits-fe",
         "display_name": "opticallimits - 【MEDIA】", "profile_url": "https://opticallimits.com/"},
    )


# ───────────────────────── 品牌官号闸(正例 + 保守反例) ─────────────────────────


@pytest.mark.parametrize(
    "handle,display_name",
    [
        ("sonyalpha", "Sony Alpha"),
        ("tamron_europe", "Tamron Europe"),
        ("tamronmalaysia", "Tamron Malaysia"),
        ("tamron_south_africa", "Tamron South Africa"),
        ("nikon", ""),
        ("gvmled", "GVM LED"),
        ("viltrox.cee", "Viltrox CEE"),
        ("", "Canon Official"),
        ("UCcqb9fX4rSo03Re4uORKx3Q", "Canon USA"),
    ],
)
def test_brand_official_gate_catches_clear_official_shapes(handle: str, display_name: str) -> None:
    match = brand_official_match(handle=handle, display_name=display_name, platform="youtube")
    assert match.get("reason") == BRAND_OFFICIAL_SKIP_REASON, (handle, display_name)


@pytest.mark.parametrize(
    "handle,display_name",
    [
        ("sonyalpharumors", "Sony Alpha Rumors"),   # 粉丝/爆料号
        ("canonrumors", "Canon Rumors"),
        ("sonya_official", "Sonya Official"),        # 真人 Sonya
        ("nikonlover", "Nikon Lover"),
        ("markus", "Markus"),
        ("sigma_male", "Sigma Male"),
        ("lensreviewhq", "Sony Canon Nikon Lens Reviews"),  # 标题提品牌 ≠ 官号
        ("", ""),
    ],
)
def test_brand_official_gate_stays_conservative(handle: str, display_name: str) -> None:
    assert brand_official_match(handle=handle, display_name=display_name) == {}, (handle, display_name)


def test_brand_badge_ignores_fabricated_website_handles() -> None:
    """真库里有一行 platform=media / handle=nikon,其实是 opticallimits 的页面——
    站点行只认 display_name,不能凭捏出来的 handle 给人扣「官号」帽子。"""
    assert is_brand_official_row(
        {"platform": "media", "handle": "nikon", "display_name": "opticallimits - 【MEDIA】"}
    ) is False
    assert is_brand_official_row({"platform": "media", "handle": "x", "display_name": "Nikon"}) is True


def test_brand_gate_documented_conservatism_is_literally_true() -> None:
    """文档举的例子必须与代码事实一致(profile_basics.write_kol_profile_basics 的 docstring)。

    拦得住的只有「品牌词 + 表内后缀」这一档;sirui.cine / viltrox_id 是「品牌词 + 表外后缀」,
    按现口径**放行**——把它们当拦得住的例子举就是过度声称。
    """
    assert brand_official_match(handle="tamron_europe", platform="instagram").get("brand") == "tamron"
    assert brand_official_match(handle="tamron_south_africa", platform="instagram").get("brand") == "tamron"
    assert brand_official_match(handle="sirui.cine", platform="instagram") == {}
    assert brand_official_match(handle="viltrox_id", platform="instagram") == {}


def test_brand_gate_handle_identity_failure_does_not_gate_a_real_creator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """身份判据取不到时,本闸只看 display_name —— 宁可漏拦官号,绝不误吃真达人。

    下游朝「拦人」方向放大后果(建档闸直接不建行),所以这里的安全方向是
    「不拿 handle 硬判」,不是「硬判」。
    """
    import app.domains.kol.brand_official_gate as gate_module

    class _Boom:
        def __getattr__(self, name: str) -> Any:
            raise ImportError("pool_identity_key unavailable")

    warned: list[str] = []
    monkeypatch.setitem(sys.modules, "app.domains.kol.pool_identity_key", _Boom())
    monkeypatch.setattr(gate_module, "_IDENTITY_RULE_WARNED", set())
    monkeypatch.setattr(gate_module.logger, "warning", lambda msg, *a, **kw: warned.append(str(msg)))
    assert gate_module._handle_is_identity("tamron_europe", "instagram") is False
    assert gate_module.brand_official_match(
        handle="tamron_europe", display_name="Real Person", platform="instagram"
    ) == {}
    assert warned, "判不了必须告警,失败绝不静默"


def test_brand_gate_is_config_driven(monkeypatch: pytest.MonkeyPatch) -> None:
    """加词/减词/整闸关都在 env 上(镜像地区排除的形态),代码不改一行。"""
    monkeypatch.setenv("VKPI_BRAND_OFFICIAL_TOKENS", "twnz, someotherbrand")
    assert "twnz" in configured_brand_tokens()
    assert brand_official_match(handle="twnz.official").get("brand") == "twnz"
    monkeypatch.setenv("VKPI_BRAND_OFFICIAL_TOKENS_EXCLUDE", "sigma")
    assert brand_official_match(handle="sigma") == {}
    monkeypatch.setenv("VKPI_BRAND_OFFICIAL_GATE", "0")
    assert brand_official_match(handle="nikon") == {}
    assert is_brand_official_row({"handle": "nikon"}) is False


# ───────────────────────── 写端建档闸(真库真写路径) ─────────────────────────


@pytest.fixture(scope="module", autouse=True)
def _wall_defect_test_db(tmp_path_factory: pytest.TempPathFactory):
    """私有 SQLite 库(绝不碰仓库 submissions.db),用真 schema 跑写端建档闸。"""
    db_path = (tmp_path_factory.mktemp("wall-defects") / "wall-defects.db").resolve()
    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != repository_db

    old_db_path = db_connection.DB_PATH
    old_backend = db_connection.DB_RUNTIME_BACKEND
    old_url = db_connection.DB_RUNTIME_URL
    old_ready = product_industry_schema._SCHEMA_READY

    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    product_industry_schema._SCHEMA_READY = False
    try:
        ensure_vkpi_product_industry_schema()
        conn = get_conn()
        actual_path = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
        assert actual_path == db_path
        # 让 hermetic sqlite 与线上 PG 的写入契约对齐,才能跑真实写路径:
        # ① 写端 ON CONFLICT 分支带 `updated_at=NOW()`(PG 函数)→ 注册同名函数;
        # ② 建档 INSERT 不写 created_at/updated_at(线上列有默认值)→ 给本地表补默认值。
        conn.create_function("NOW", 0, lambda: "2026-08-25T00:00:00Z")
        ddl = str(conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='vkpi_kol_pool'"
        ).fetchone()[0])
        conn.execute("DROP TABLE vkpi_kol_pool")
        conn.execute(
            ddl.replace("created_at TEXT NOT NULL,", "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,")
            .replace("updated_at TEXT NOT NULL,", "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,")
        )
        conn.execute(
            "INSERT INTO vkpi_kol_pool (pool_uid, platform, handle, display_name, profile_url, source_type, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("wall-dup-1", "youtube", "BrandonLi", "Brandon Li",
             "https://www.youtube.com/@BrandonLi", "manual", "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        )
        conn.commit()
        yield db_path
    finally:
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH = old_db_path
        db_connection.DB_RUNTIME_BACKEND = old_backend
        db_connection.DB_RUNTIME_URL = old_url
        product_industry_schema._SCHEMA_READY = old_ready


def test_enroll_writer_refuses_to_create_a_new_brand_official_row() -> None:
    from app.domains.kol.profile_basics import write_kol_profile_basics

    conn = get_conn()
    before = int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()["n"])
    result = write_kol_profile_basics(
        None,
        {"platform": "tiktok", "handle": "tamron_europe", "display_name": "Tamron Europe"},
        dry_run=False,
        conn=conn,
    )
    assert result["skipped"] is True
    assert result["skip_reason"] == BRAND_OFFICIAL_SKIP_REASON
    assert result["kol_pool_id"] is None
    after = int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()["n"])
    assert after == before  # 一行都没新建


def test_enroll_writer_still_admits_a_real_creator() -> None:
    from app.domains.kol.profile_basics import write_kol_profile_basics

    conn = get_conn()
    result = write_kol_profile_basics(
        None,
        {"platform": "tiktok", "handle": "sonyalpharumors", "display_name": "Sony Alpha Rumors"},
        dry_run=False,
        conn=conn,
    )
    assert not result.get("skipped")
    assert int(result["kol_pool_id"]) > 0
    assert result["viltrox_fit_score_untouched"] is True


def test_gate_can_be_opened_for_an_explicit_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """闸只拦「自动/默认」路径;显式放行参数与整闸关都必须真的能建档。"""
    from app.domains.kol.profile_basics import write_kol_profile_basics

    conn = get_conn()
    allowed = write_kol_profile_basics(
        None,
        {"platform": "instagram", "handle": "godox_global", "display_name": "Godox Global"},
        dry_run=False,
        conn=conn,
        allow_brand_official=True,
    )
    assert not allowed.get("skipped") and int(allowed["kol_pool_id"]) > 0
    monkeypatch.setenv("VKPI_BRAND_OFFICIAL_GATE", "0")
    off = write_kol_profile_basics(
        None,
        {"platform": "instagram", "handle": "smallrig_official", "display_name": "SmallRig Official"},
        dry_run=False,
        conn=conn,
    )
    assert not off.get("skipped") and int(off["kol_pool_id"]) > 0


def test_existing_brand_official_row_can_still_be_refreshed() -> None:
    """存量官号行照常刷新(只拦新建)——绝不删行、绝不改评分。"""
    from app.domains.kol.profile_basics import write_kol_profile_basics

    conn = get_conn()
    conn.execute(
        "INSERT INTO vkpi_kol_pool (pool_uid, platform, handle, display_name, source_type, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("wall-brand-1", "tiktok", "sonyalpha", "Sony Alpha", "manual",
         "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z"),
    )
    conn.commit()
    refreshed = write_kol_profile_basics(
        None,
        {"platform": "tiktok", "handle": "sonyalpha", "display_name": "Sony Alpha", "followers": 40500},
        dry_run=False,
        conn=conn,
    )
    assert not refreshed.get("skipped")
    row = conn.execute(
        "SELECT followers FROM vkpi_kol_pool WHERE platform=? AND handle=?", ("tiktok", "sonyalpha")
    ).fetchone()
    assert int(row["followers"]) == 40500
    assert is_brand_official_row({"handle": "sonyalpha", "platform": "tiktok"}) is True


def test_auto_enroll_marks_brand_official_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """自动入库:被写端任一道闸拦下的项不计入库数,原因就地留标(调用方据此诚实计数)。

    建档入口有两道闸:主线既有的 ``discovery_account_gate_verdict``(**抛错**拦人,
    竞品词表命中时先手)与本波补的 ``brand_official_gate``(返回 skip)。
    竞品词表是库/配置驱动的,同一条候选在不同环境可能被不同的一道拦下——
    本用例锁的是「无论哪道拦,都必须留下诚实原因标,绝不当成功数、也绝不静默」。
    """
    monkeypatch.setenv("KOL_AUTO_DEDUP_ENROLL", "0")
    monkeypatch.setenv("KOL_AUTO_ENROLL_DISCOVERY", "1")
    conn = get_conn()
    before = int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()["n"])
    creators = [{"platform": "tiktok", "handle": "tamron_south_africa", "display_name": "Tamron South Africa"}]
    enrolled = _provider._auto_enroll_discoveries(creators)
    after = int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()["n"])
    assert enrolled == 0
    assert after == before
    assert creators[0]["auto_enroll_skipped"] in {BRAND_OFFICIAL_SKIP_REASON, "brand_official", "own_brand"}


def test_auto_enroll_marks_the_pre_existing_gate_rejection_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """主线那道闸抛 ValueError 拦人时,同样要留原因标——以前它和「网络挂了」混在一起只进日志。"""
    from app.domains.kol import profile_basics

    monkeypatch.setenv("KOL_AUTO_DEDUP_ENROLL", "0")
    monkeypatch.setenv("KOL_AUTO_ENROLL_DISCOVERY", "1")
    monkeypatch.setattr(
        profile_basics, "write_kol_profile_basics",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("discovery_account_rejected:own_brand")),
    )
    creators = [{"platform": "tiktok", "handle": "a_real_looking_handle", "display_name": "Someone"}]
    assert _provider._auto_enroll_discoveries(creators) == 0
    assert creators[0]["auto_enroll_skipped"] == "own_brand"


# ───────────────────────── 翻页确定性(并列行不重不漏) ─────────────────────────


def test_every_sort_clause_ends_with_a_unique_tiebreaker() -> None:
    """每一档排序键末位都必须落到唯一键 id 上。

    原因不是洁癖:排序键并列时,LIMIT/OFFSET 的行序由执行计划自由决定,同一行可能在
    两页各出现一次、另一行则一页都翻不到。隔离库实测(2020 行池、读端投影后可见 2009,
    按 fit 每页 500 连翻 6 页)共取回 2009 条,去重只剩 1999 —— 10 个人怎么翻都取不到。
    """
    from app.domains.kol.pool_common import _sort_clause

    for sort_key in ("fit", "followers", "avg_views", "views", "engagement", "engagement_rate",
                     "updated", "recent", "oldest", "updated_oldest", "missing", "gaps", "", "不认识的键"):
        clause = " ".join(_sort_clause(sort_key).split())
        assert clause.endswith("id DESC"), (sort_key, clause)


def test_paged_reads_return_every_row_exactly_once() -> None:
    """按 fit 逐页翻完整张池表:取回条数 = 表行数,且一行不重。"""
    conn = get_conn()
    total = int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()["n"])
    from app.domains.kol.pool_common import _sort_clause

    order_clause = _sort_clause("fit")
    page_size = 3
    seen: list[int] = []
    offset = 0
    while offset < total + page_size:
        rows = conn.execute(
            f"SELECT id FROM vkpi_kol_pool ORDER BY {order_clause} LIMIT ? OFFSET ?",
            (page_size, offset),
        ).fetchall()
        if not rows:
            break
        seen.extend(int(row["id"]) for row in rows)
        offset += page_size
    assert len(seen) == total
    assert len(set(seen)) == total
