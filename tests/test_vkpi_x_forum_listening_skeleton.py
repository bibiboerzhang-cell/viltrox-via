"""迸发⑤ 契约测试:X / 摄影论坛监听骨架 —— 闸默认 OFF 零网络 + 字段映射。

红线契约:
  1) 闸三态(absent/off/on)任何一态都零网络零写库(socket 级卫兵坐实);
  2) on 也只到 not_wired(执行体属开闸刀),绝不静默变成真实抓取;
  3) 字段映射(x / forum)对 mock payload 产出 vkpi_comments 标准字段,0 值保留;
  4) 监听覆盖两行读真闸态:not_connected / scaffold / empty / ready;
  5) market_voice 扩源降级兜底:骨架模块炸 → 覆盖卡仍出「未接入·盲区」两行。
"""
from __future__ import annotations

import socket

import pytest

# 模块 import 放卫兵之前(重依赖在 import 期完成;卫兵只稽查调用期)
from app.domains.comments import market_listening as ml
from app.domains.comments.collector import PLATFORM_DEFAULTS, _standardize_comment
from app.domains.market import market_voice
from app.platform.industry_crawlers import get_crawler


@pytest.fixture()
def no_network(monkeypatch):
    """socket 级卫兵:测试体内任何真实网络连接立刻炸。"""

    def _boom(*_args, **_kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("network call attempted in zero-network skeleton test")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)


@pytest.fixture()
def gates_absent(monkeypatch):
    monkeypatch.delenv(ml.GATE_X, raising=False)
    monkeypatch.delenv(ml.GATE_FORUM, raising=False)


# ── 闸三态 × 零网络 ──────────────────────────────────────────────────────


def test_gate_absent_returns_disabled_with_zero_network(no_network, gates_absent):
    for collect, gate in ((ml.collect_x_listening, ml.GATE_X), (ml.collect_forum_listening, ml.GATE_FORUM)):
        result = collect()
        assert result["status"] == "disabled"
        assert result["gate"] == gate
        assert result["gate_state"] == "absent"
        assert result["items"] == []
        assert result["network_calls"] == 0


def test_gate_off_returns_disabled_with_zero_network(no_network, monkeypatch):
    monkeypatch.setenv(ml.GATE_X, "0")
    monkeypatch.setenv(ml.GATE_FORUM, "false")
    for collect in (ml.collect_x_listening, ml.collect_forum_listening):
        result = collect()
        assert result["status"] == "disabled"
        assert result["gate_state"] == "off"
        assert result["network_calls"] == 0


def test_gate_on_stops_at_not_wired_still_zero_network(no_network, monkeypatch):
    """开闸 ≠ 抓取:骨架阶段执行体未接线,on 也必须零网络(误开闸零烧钱)。"""
    monkeypatch.setenv(ml.GATE_X, "1")
    monkeypatch.setenv(ml.GATE_FORUM, "1")

    x = ml.collect_x_listening(max_items=5000)
    assert x["status"] == "not_wired"
    assert x["items"] == [] and x["network_calls"] == 0
    # 词族复用 lexicon:品牌词必在;max_items 被日上限封顶
    assert "viltrox" in x["planned"]["queries"]
    assert x["planned"]["max_items"] <= ml.X_LISTEN_DAILY_CAP

    forum = ml.collect_forum_listening()
    assert forum["status"] == "not_wired"
    assert forum["items"] == [] and forum["network_calls"] == 0
    # 合规红线:自动化只允许 reddit;DPReview 论坛 / FredMiranda 永在 no_scrape
    assert forum["planned"]["allowed_sources"] == ["reddit"]
    assert set(forum["planned"]["no_scrape_sources"]) == {"dpreview_forums", "fredmiranda"}
    assert forum["planned"]["watchlist"], "watchlist 不得为空(reddit 策略或兜底)"


def test_forum_platform_registered_but_has_no_crawler():
    """forum 进 PLATFORM_DEFAULTS(映射契约)但无 crawler → collector 路径必然 not_configured,零网络。"""
    assert "forum" in PLATFORM_DEFAULTS
    assert "x" in PLATFORM_DEFAULTS
    assert get_crawler("forum") is None


# ── 字段映射(mock payload → vkpi_comments 标准字段)─────────────────────


def test_x_field_mapping_from_apidojo_like_payload(no_network):
    raw = {
        "id": "1878881234567890123",
        "fullText": "The Viltrox AF 135mm f/1.8 LAB is stunning wide open",
        "author": {
            "userName": "lensrumors",
            "id": "44196397",
            "profilePicture": "https://pbs.twimg.com/profile_images/demo.jpg",
        },
        "likeCount": 0,  # 0 值必须保留为已知 0,不得落 None
        "replyCount": 3,
        "createdAt": "2026-07-10T08:00:00.000Z",
    }
    std = ml.standardize_x_listen_item(raw, post_id=7, external_post_id="1878881234567890123")
    assert std["platform"] == "x"
    assert std["post_table"] == ml.LISTEN_POST_TABLE == "vkpi_market_mentions"
    assert std["external_comment_id"] == "1878881234567890123"
    assert std["comment_text"].startswith("The Viltrox AF 135mm")
    assert std["author_handle"] == "lensrumors"
    assert std["author_id"] == "44196397"
    assert std["likes_count"] == 0
    assert std["reply_count"] == 3
    assert std["created_at"] == "2026-07-10T08:00:00.000Z"
    assert std["author_avatar_url"] == "https://pbs.twimg.com/profile_images/demo.jpg"


def test_forum_field_mapping_from_xenforo_like_payload(no_network):
    raw = {
        "post_id": 987654,
        "message": "The 27mm f/1.2 has zero AF issues on my X-T5",
        "username": "apsc_shooter",
        "user": {"id": 3345, "avatar_urls": {"s": "https://forum.example.com/avatars/s/3345.jpg"}},
        "reaction_score": 0,
        "post_date": 1783850000,  # epoch 秒 → ISO UTC
        "is_first_post": False,
        "position_in_thread": 4,
    }
    std = ml.standardize_forum_listen_item(raw, post_id=11, external_post_id="thread-42")
    assert std["platform"] == "forum"
    assert std["post_table"] == "vkpi_market_mentions"
    assert std["external_comment_id"] == "987654"
    assert std["comment_text"] == "The 27mm f/1.2 has zero AF issues on my X-T5"
    assert std["author_handle"] == "apsc_shooter"
    assert std["author_id"] == "3345"
    assert std["likes_count"] == 0
    assert std["is_op"] is False
    assert std["depth"] == 4
    assert std["created_at"].startswith("2026-")  # epoch 转 ISO,不落原始整数
    assert std["author_avatar_url"] == "https://forum.example.com/avatars/s/3345.jpg"


def test_forum_mapping_registered_in_collector_switch(no_network):
    """collector._standardize_comment 直接吃 forum 平台(映射本刀新增,枚举契约)。"""
    std = _standardize_comment(
        {"id": "p1", "body": "hello", "author": "u1"},
        platform="forum",
        post_id=1,
        account_id=None,
        external_post_id="t1",
        post_table="vkpi_market_mentions",
    )
    assert std["external_comment_id"] == "p1"
    assert std["comment_text"] == "hello"
    assert std["author_handle"] == "u1"


# ── 监听覆盖两行(读真闸态,禁装)────────────────────────────────────────


class _FakeRow(dict):
    pass


class _FakeCursor:
    def __init__(self, n):
        self._n = n

    def fetchone(self):
        return _FakeRow({"n": self._n})


class _FakeConn:
    def __init__(self, n):
        self._n = n

    def execute(self, *_args, **_kwargs):
        return _FakeCursor(self._n)


def test_cover_sources_absent_is_not_connected(no_network, gates_absent):
    cover = ml.listening_cover_sources(None)
    assert set(cover) == {"x_listen", "forum_listen"}
    for entry in cover.values():
        assert entry["status"] == "not_connected"
        assert entry["count"] == 0
        assert "盲区" in entry["note"]


def test_cover_sources_off_is_scaffold(no_network, monkeypatch):
    monkeypatch.setenv(ml.GATE_X, "0")
    monkeypatch.setenv(ml.GATE_FORUM, "0")
    cover = ml.listening_cover_sources(None)
    for entry in cover.values():
        assert entry["status"] == "scaffold"
        assert entry["count"] == 0
        assert "待开闸" in entry["note"]


def test_cover_sources_on_reports_real_counts(no_network, monkeypatch):
    monkeypatch.setenv(ml.GATE_X, "1")
    monkeypatch.setenv(ml.GATE_FORUM, "1")
    ready = ml.listening_cover_sources(_FakeConn(38))
    assert ready["x_listen"]["status"] == "ready"
    assert ready["x_listen"]["count"] == 38
    empty = ml.listening_cover_sources(_FakeConn(0))
    assert empty["forum_listen"]["status"] == "empty"
    assert empty["forum_listen"]["count"] == 0


def test_market_voice_listening_fallback_is_honest_blindspot(no_network, monkeypatch):
    """骨架模块炸 → 覆盖卡两行仍在,状态回落「未接入·盲区」,绝不整卡失败/绝不装。"""

    def _boom(_conn):
        raise RuntimeError("skeleton unavailable")

    monkeypatch.setattr(
        "app.domains.comments.market_listening.listening_cover_sources", _boom
    )
    sources = market_voice._listening_sources(None)
    assert set(sources) == {"x_listen", "forum_listen"}
    for entry in sources.values():
        assert entry["status"] == "not_connected"
        assert "盲区" in entry["note"]


def test_market_voice_listening_sources_merge_real_gate_state(no_network, monkeypatch, gates_absent):
    """正常通路:market_voice 包装层原样透出骨架真闸态(absent → not_connected)。"""
    sources = market_voice._listening_sources(None)
    assert sources["x_listen"]["gate"] == ml.GATE_X
    assert sources["forum_listen"]["gate_state"] == "absent"
    assert sources["forum_listen"]["status"] == "not_connected"
