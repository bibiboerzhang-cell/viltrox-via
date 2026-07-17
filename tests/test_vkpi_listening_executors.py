"""开闸刀契约测试:listening_executors —— 幂等落表 + 限速 + 解析 + 预算被闸透传。

全部用假 fetch/假 crawler/假 conn,零真实网络(socket 级卫兵坐实)、零真实库。
合规红线:执行体只认注入的 subreddit 列表(Reddit 公开 JSON 通道),
DPReview 论坛 / FredMiranda 没有任何代码路径。
"""
from __future__ import annotations

import socket

import pytest

from app.domains.comments import listening_executors as le


@pytest.fixture()
def no_network(monkeypatch):
    def _boom(*_args, **_kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("network call attempted in listening executor test")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)


@pytest.fixture()
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(le.time, "sleep", lambda seconds: slept.append(float(seconds)))
    return slept


@pytest.fixture(autouse=True)
def _fallback_gate_defaults_off(monkeypatch):
    """本文件所有用例默认付费兜底闸关(不吃开发机 operator env 的开闸);要开的用例自己 setenv。"""
    monkeypatch.delenv(le.REDDIT_APIFY_FALLBACK_GATE, raising=False)


@pytest.fixture()
def no_fence(monkeypatch):
    """durable 供应商围栏换成透传(单测不碰真库);围栏语义另有真库路径坐实。"""
    from contextlib import contextmanager

    fenced: list[str] = []

    @contextmanager
    def _noop(task_id, *, job_type=""):
        fenced.append(f"{task_id}|{job_type}")
        yield

    monkeypatch.setattr(le, "_provider_execution_fence", _noop)
    return fenced


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """最小 compat 形状:execute/fetch + commit/rollback,内存台账可断言。"""

    def __init__(self):
        self.sources: dict[tuple[str, str], int] = {}
        self.mentions: list[tuple] = []
        self.commits = 0
        self.rollbacks = 0
        self._next_id = 1

    def execute(self, query, params=()):
        q = " ".join(str(query).split())
        if q.startswith("SELECT source_ref FROM vkpi_market_sources"):
            platform = params[0]
            wanted = set(params[1:])
            rows = [
                {"source_ref": ref}
                for (p, ref) in self.sources
                if p == platform and ref in wanted
            ]
            return _Cursor(rows)
        if q.startswith("INSERT INTO vkpi_market_sources"):
            platform, ref = params[2], params[3]
            row_id = self._next_id
            self._next_id += 1
            self.sources[(platform, ref)] = row_id
            return _Cursor([{"id": row_id}])
        if q.startswith("INSERT INTO vkpi_market_mentions"):
            self.mentions.append(tuple(params))
            return _Cursor([])
        raise AssertionError(q)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _reddit_post(ref: str, *, title: str, score: int = 10, comments: int = 3):
    return {
        "id": ref,
        "title": title,
        "body": "selftext excerpt",
        "author": "shooter",
        "permalink": f"https://www.reddit.com/r/photography/comments/{ref}/",
        "score": score,
        "numberOfComments": comments,
        "createdAt": "2026-07-15T09:00:00Z",
    }


def test_reddit_run_persists_posts_idempotently(no_network, no_sleep):
    conn = _FakeConn()
    posts = {
        "photography": [_reddit_post("t3_aaa", title="Viltrox 85mm long term"), _reddit_post("t3_bbb", title="Which tripod")],
        "SonyAlpha": [_reddit_post("t3_ccc", title="Sigma vs Sony 50mm")],
    }

    def fetch(sub, limit):
        return posts[sub][:limit]

    first = le.run_reddit_listening(["photography", "SonyAlpha"], conn=conn, fetch=fetch)
    assert first["status"] == "ok"
    assert first["fetched"] == 3
    assert first["new_sources"] == 3 and first["new_mentions"] == 3
    assert first["skipped_existing"] == 0
    assert first["network_calls"] == 2
    assert conn.commits == 1
    # mention 行:platform=reddit,text=标题+摘要
    platforms = {row[2] for row in conn.mentions}
    assert platforms == {"reddit"}
    assert any("Viltrox 85mm long term" in str(row[4]) for row in conn.mentions)

    second = le.run_reddit_listening(["photography", "SonyAlpha"], conn=conn, fetch=fetch)
    assert second["new_sources"] == 0 and second["new_mentions"] == 0
    assert second["skipped_existing"] == 3


def test_reddit_run_rate_limit_floor_is_two_seconds(no_network, no_sleep):
    conn = _FakeConn()

    def fetch(sub, limit):
        del sub, limit
        return []

    result = le.run_reddit_listening(
        ["photography", "SonyAlpha", "fujifilm"], conn=conn, fetch=fetch, sleep_seconds=0
    )
    # 3 个 sub → 请求间隔 2 次,即使调用方传 0 也压到 >= 2 秒下限
    assert no_sleep == [2.0, 2.0]
    assert result["status"] == "empty"
    assert result["fetched"] == 0


def test_reddit_run_reports_per_sub_errors_honestly(no_network, no_sleep):
    conn = _FakeConn()

    def fetch(sub, limit):
        del limit
        raise RuntimeError(f"boom {sub}")

    result = le.run_reddit_listening(["photography"], conn=conn, fetch=fetch)
    assert result["status"] == "error"
    assert result["fetched"] == 0
    assert any("photography" in err for err in result["errors"])
    # 闸关时兜底跳过的说明排最前(不被逐 sub 报错挤出截断窗)
    assert result["errors"][0].startswith("apify_fallback_skipped")


class _FakeXCrawler:
    def __init__(self, result):
        self.api_token = "token"
        self.result = result
        self.run_inputs: list[dict] = []

    def _apify_run(self, run_input):
        self.run_inputs.append(run_input)
        return self.result


def test_x_run_parses_items_and_persists(no_network, no_fence):
    conn = _FakeConn()
    crawler = _FakeXCrawler(
        {
            "provider_status": "ok",
            "items": [
                {
                    "id": "1878880000000000001",
                    "fullText": "Viltrox AF 135mm LAB first impressions",
                    "author": {"userName": "lensrumors"},
                    "likeCount": 12,
                    "retweetCount": 3,
                    "replyCount": 1,
                    "createdAt": "Tue Jul 14 08:00:00 +0000 2026",
                    "url": "https://x.com/lensrumors/status/1878880000000000001",
                },
                # 无 id,靠 url 提取
                {
                    "text": "sigma 35mm deal",
                    "url": "https://x.com/deals/status/1878880000000000002",
                },
                # 既无 id 也无可解析 url → 诚实丢弃
                {"text": "no id here"},
            ],
            "raw": {"actor_id": "apidojo~twitter-scraper-lite"},
        }
    )
    result = le.run_x_listening(["viltrox"], max_items=50, conn=conn, crawler=crawler)
    assert result["status"] == "ok"
    assert result["fetched"] == 2
    assert result["new_sources"] == 2 and result["new_mentions"] == 2
    assert crawler.run_inputs[0]["searchTerms"] == ["viltrox"]
    assert crawler.run_inputs[0]["maxItems"] == 50
    assert ("x", "1878880000000000001") in conn.sources
    assert ("x", "1878880000000000002") in conn.sources
    # Twitter 原生时间格式转 ISO UTC
    assert le._parse_x_created_at("Tue Jul 14 08:00:00 +0000 2026") == "2026-07-14T08:00:00Z"


def test_x_run_budget_blocked_passthrough_writes_nothing(no_network, no_fence):
    conn = _FakeConn()
    crawler = _FakeXCrawler(
        {"provider_status": "budget_blocked", "blocked": True, "items": [], "reason": "hard_stop"}
    )
    result = le.run_x_listening(["viltrox"], max_items=10, conn=conn, crawler=crawler)
    assert result["status"] == "blocked"
    assert result["new_sources"] == 0 and result["new_mentions"] == 0
    assert conn.sources == {} and conn.mentions == []


def test_x_run_without_token_is_honest_not_configured(no_network):
    class _NoToken:
        api_token = ""

    result = le.run_x_listening(["viltrox"], conn=_FakeConn(), crawler=_NoToken())
    assert result["status"] == "not_configured"
    assert result["network_calls"] == 0


def test_reddit_apify_fallback_requires_explicit_gate_and_batches_once(no_network, no_sleep, monkeypatch):
    """公开 JSON 被挡:兜底闸关 → 如实报错零烧钱;闸开 → 失败 sub 合并成一次批量兜底。"""
    batch_calls: list[tuple[tuple[str, ...], int]] = []

    def failing_fetch(sub, limit):
        del limit
        raise RuntimeError(f"HTTP Error 403: Blocked ({sub})")

    def batch_fetch(failed, total):
        batch_calls.append((tuple(failed), total))
        return [
            {"dataType": "community", "id": "t5_xyz", "title": "should be filtered"},
            {
                "dataType": "post",
                "id": "t3_apify1",
                "title": "Tamron 35-150 on sale",
                "body": "",
                "username": "dealbot",
                "communityName": "photography",
                "url": "https://www.reddit.com/r/photography/comments/apify1/",
                "upVotes": 9,
                "numberOfComments": 4,
                "createdAt": "2026-07-15T12:00:00Z",
            },
        ]

    monkeypatch.delenv(le.REDDIT_APIFY_FALLBACK_GATE, raising=False)
    conn = _FakeConn()
    blocked = le.run_reddit_listening(
        ["photography", "SonyAlpha"], conn=conn, fetch=failing_fetch, batch_fetch=batch_fetch
    )
    assert blocked["status"] == "error"
    assert batch_calls == []  # 闸关绝不烧钱
    assert conn.sources == {}
    assert any("apify_fallback_skipped" in err for err in blocked["errors"])

    monkeypatch.setenv(le.REDDIT_APIFY_FALLBACK_GATE, "1")
    conn2 = _FakeConn()
    ok = le.run_reddit_listening(
        ["photography", "SonyAlpha"], conn=conn2, fetch=failing_fetch, batch_fetch=batch_fetch
    )
    assert ok["status"] == "ok"
    assert ok["fetched"] == 1  # community 行被 _filter_apify_posts 过滤,只落真帖
    assert ("reddit", "t3_apify1") in conn2.sources
    # 两个失败 sub 合并成一次批量调用(50 = 2 sub x per_sub_limit 25)
    assert batch_calls == [(("photography", "SonyAlpha"), 50)]
    assert "apify_reddit_scraper" in ok["provider"]


def test_filter_apify_posts_drops_non_post_rows(no_network):
    items = [
        {"dataType": "community", "id": "t5_a"},
        {"dataType": "post", "id": "t3_b", "title": "ok"},
        {"id": "t3_c", "title": "no dataType still post-like"},
        {"dataType": "post", "title": "missing id -> dropped"},
        "not a dict",
    ]
    kept = le._filter_apify_posts(items)
    assert [item.get("id") for item in kept] == ["t3_b", "t3_c"]


def test_default_reddit_watchlist_is_camera_circle_and_deduped(no_network):
    subs = le.default_reddit_watchlist()
    assert 6 <= len(subs) <= 12
    assert "photography" in subs
    assert "cameras" in subs and "canon" in subs
    assert len({s.lower() for s in subs}) == len(subs)
