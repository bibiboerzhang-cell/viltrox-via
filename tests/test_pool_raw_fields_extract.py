"""raw 字段提列解析器(迁移 291 / pool_enrich.extract_raw_fields):fixture 按隔离库真实 raw 结构抽样缩写。

真实键名(2026-08-22 隔离库 vkpi_closeout 各平台抽样 40 行):
  TT  profile.items[] / videos[]: authorMeta.{verified, ttSeller, commerceUserInfo.{commerceUser, category}, signature}
      + mentions["@Nick"] + detailedMentions[{id, name, nickName, profileUrl}]
  IG  profile.items[0]: verified / isBusinessAccount / businessCategoryName / biography
      + latestPosts[].{mentions[str], taggedUsers[{username, full_name, is_verified}], productType, childPosts[]}
  YT  profile.items[0]: brandingSettings.channel.keywords(字符串,多词用引号)+ videos[].snippet.categoryId;
      raw 里零 topicDetails(解析器兼容,但当前靠 keywords 兜底)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import app.domains.kol.pool  # noqa: E402,F401 — pool_enrich 单独先导会触发既有循环导入
from app.domains.kol import pool_enrich  # noqa: E402


def _tt_video(**author: object) -> dict:
    meta = {
        "verified": False, "ttSeller": False, "signature": "📷 Sony shooter | business: hello@studio.example",
        "commerceUserInfo": {"commerceUser": False}, "bioLink": "https://tr.ee/x",
    }
    meta.update(author)
    return {"authorMeta": meta, "mentions": [], "detailedMentions": []}


TT_RAW = {
    "source": "tiktok_url_deep_crawl_profile",
    "profile": {"items": [
        _tt_video(verified=True, ttSeller=True, commerceUserInfo={"commerceUser": True, "category": "Electronics"})
        | {"mentions": ["@Levi’s", "@sidemen"], "detailedMentions": [{"id": "1", "name": "levis", "nickName": "Levi’s", "profileUrl": "https://www.tiktok.com/@1"}]},
        _tt_video() | {"mentions": ["@sidemen", "@Sidemen"]},
    ]},
    "videos": [
        _tt_video() | {"id": "v3", "mentions": ["@sidemen"]},
        _tt_video() | {"id": "v3", "mentions": ["@sidemen"]},  # 与上一条同 id:profile.items/videos 双存的同一视频,只计一次
    ],
}

IG_RAW = {
    "source": "instagram_url_deep_crawl_profile",
    "profile": {"items": [{
        "username": "shooter", "verified": True, "isBusinessAccount": False, "businessCategoryName": "Photographer",
        "biography": "Portraits | DM for collabs",
        "latestPosts": [
            {"productType": "clips", "mentions": ["sonyalpha", "nanlite_global"],
             "taggedUsers": [{"username": "sonyalpha", "full_name": "Sony | Alpha", "is_verified": True},
                             {"username": "friend_01", "full_name": "Friend", "is_verified": False}]},
            {"productType": None, "mentions": [], "taggedUsers": [],
             "childPosts": [{"mentions": ["sonyalpha"], "taggedUsers": [{"username": "nanlite_global", "is_verified": True}]}]},
        ],
    }]},
    "videos": [],
}

YT_RAW = {
    "source": "youtube_api",
    "profile": {"items": [{
        "kind": "youtube#channel", "id": "UCabc",
        "snippet": {"title": "Cam Reviews", "description": "Lens reviews"},
        "statistics": {"subscriberCount": "1000"},
        "brandingSettings": {"channel": {"keywords": '"camera review" photography "sony alpha" viltrox'}},
    }]},
    "videos": [{"snippet": {"categoryId": "28"}}, {"snippet": {"categoryId": "28"}}, {"snippet": {"categoryId": "26"}}],
}


def test_tiktok_flags_category_and_mentions() -> None:
    fields = pool_enrich.extract_raw_fields(json.dumps(TT_RAW), platform="tiktok")
    assert fields["is_verified"] is True
    assert fields["is_tt_seller"] is True
    assert fields["is_commerce_user"] is True
    assert fields["topic_details_json"]["source"] == "tiktok_profile_category"
    assert fields["topic_details_json"]["commerce_category"] == "Electronics"
    tagged = {row["handle"]: row for row in fields["tagged_brands_json"]}
    # detailedMentions 覆盖同帖 mentions 字符串(不双计);其余帖子的 @sidemen 逐帖计数,大小写归一,
    # 同 id 视频(profile.items 与 videos 双存)只计一次
    assert tagged["levis"]["mentioned"] == 1 and tagged["levis"]["name"] == "Levi’s"
    assert tagged["sidemen"]["mentioned"] == 3
    assert "levi’s" not in tagged


def test_instagram_business_flag_tagged_users_and_content_types() -> None:
    fields = pool_enrich.extract_raw_fields(IG_RAW, platform="instagram")
    assert fields["is_verified"] is True
    assert fields["is_tt_seller"] is None  # IG 没有 ttSeller 信号 -> 保持 NULL
    assert fields["is_commerce_user"] is False  # isBusinessAccount=False 是明确否,不是 NULL
    topic = fields["topic_details_json"]
    assert topic["business_category"] == "Photographer"
    assert topic["content_types"] == {"clips": 1}
    tagged = {row["handle"]: row for row in fields["tagged_brands_json"]}
    assert tagged["sonyalpha"] == {"handle": "sonyalpha", "name": "Sony | Alpha", "verified": True, "tagged": 1, "mentioned": 2, "count": 3}
    assert tagged["nanlite_global"]["tagged"] == 1 and tagged["nanlite_global"]["verified"] is True
    assert tagged["friend_01"]["verified"] is False
    assert fields["tagged_brands_json"][0]["handle"] == "sonyalpha"  # 按 count 降序


def test_youtube_keywords_and_category_histogram_without_topic_details() -> None:
    fields = pool_enrich.extract_raw_fields(YT_RAW, platform="youtube")
    assert fields["is_verified"] is None and fields["is_tt_seller"] is None and fields["is_commerce_user"] is None
    topic = fields["topic_details_json"]
    assert topic["source"] == "youtube_branding_keywords"
    assert topic["keywords"] == ["camera review", "photography", "sony alpha", "viltrox"]
    assert topic["video_category_ids"] == {"28": 2, "26": 1}
    assert topic["topic_categories"] == [] and topic["topic_ids"] == []
    assert fields["tagged_brands_json"] is None


def test_youtube_topic_details_preferred_when_present() -> None:
    raw = json.loads(json.dumps(YT_RAW))
    raw["profile"]["items"][0]["topicDetails"] = {
        "topicCategories": ["https://en.wikipedia.org/wiki/Photography"], "topicIds": ["/m/05wkw"],
    }
    topic = pool_enrich.extract_raw_fields(raw, platform="youtube")["topic_details_json"]
    assert topic["source"] == "youtube_topic_details"
    assert topic["topic_categories"] == ["https://en.wikipedia.org/wiki/Photography"]
    assert topic["topic_ids"] == ["/m/05wkw"]


def test_empty_or_garbage_raw_yields_all_none() -> None:
    for raw in ("", "{}", "not json", None, [], {"profile": {}}):
        fields = pool_enrich.extract_raw_fields(raw, platform="tiktok")
        assert all(value is None for value in fields.values()), raw


def _pool_conn(with_291: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    extra = ", topic_details_json TEXT, tagged_brands_json TEXT, raw_fields_extracted_at TEXT, raw_fields_extractor_version TEXT" if with_291 else ""
    conn.executescript(
        f"CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, platform TEXT, viltrox_fit_score REAL, "
        f"is_verified INTEGER, is_tt_seller INTEGER, is_commerce_user INTEGER{extra});"
        "INSERT INTO vkpi_kol_pool (id, platform, viltrox_fit_score) VALUES (7, 'tiktok', 61.5);"
    )
    return conn


def test_apply_writes_only_existing_columns_and_never_touches_fit(monkeypatch) -> None:
    monkeypatch.setattr(pool_enrich, "_table_columns", lambda conn, table: {
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    })
    conn = _pool_conn(with_291=True)
    result = pool_enrich.apply_raw_fields(conn, 7, TT_RAW, platform="tiktok")
    assert result["written"] == 7
    row = dict(conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=7").fetchone())
    assert row["is_verified"] == 1 and row["is_tt_seller"] == 1 and row["is_commerce_user"] == 1
    assert json.loads(row["topic_details_json"])["commerce_category"] == "Electronics"
    assert json.loads(row["tagged_brands_json"])[0]["handle"] == "sidemen"
    assert row["raw_fields_extractor_version"] == pool_enrich.RAW_FIELDS_EXTRACTOR_VERSION
    assert row["raw_fields_extracted_at"]
    assert row["viltrox_fit_score"] == 61.5

    # 旧布局(只有 208 三列):291 列静默跳过,三标记照写
    legacy = _pool_conn(with_291=False)
    result = pool_enrich.apply_raw_fields(legacy, 7, TT_RAW, platform="tiktok")
    assert result["written"] == 3
    row = dict(legacy.execute("SELECT * FROM vkpi_kol_pool WHERE id=7").fetchone())
    assert row["is_commerce_user"] == 1 and "topic_details_json" not in row


def test_apply_keeps_null_when_signal_absent(monkeypatch) -> None:
    monkeypatch.setattr(pool_enrich, "_table_columns", lambda conn, table: {
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    })
    conn = _pool_conn(with_291=True)
    conn.execute("UPDATE vkpi_kol_pool SET platform='youtube' WHERE id=7")
    pool_enrich.apply_raw_fields(conn, 7, YT_RAW, platform="youtube")
    row = dict(conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=7").fetchone())
    assert row["is_verified"] is None and row["is_tt_seller"] is None and row["is_commerce_user"] is None
    assert row["tagged_brands_json"] is None
    assert json.loads(row["topic_details_json"])["keywords"][0] == "camera review"


def test_migration_291_pair_is_additive_and_question_mark_free() -> None:
    forward = (ROOT / "migrations/291_vkpi_kol_pool_raw_fields.sql").read_text(encoding="utf-8")
    down = (ROOT / "migrations/291_vkpi_kol_pool_raw_fields_down.sql").read_text(encoding="utf-8")
    assert "?" not in forward and "?" not in down
    assert "BEGIN" not in forward.upper().split("--")[0]
    for column in ("topic_details_json", "tagged_brands_json", "raw_fields_extracted_at", "raw_fields_extractor_version"):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in forward
        assert f"DROP COLUMN IF EXISTS {column}" in down
    # 208 已建三列不重建、不回滚
    assert "is_verified" not in forward.split("ALTER")[1:] and "DROP COLUMN IF EXISTS is_verified" not in down
    assert "version_key='291_vkpi_kol_pool_raw_fields.sql'" in down
