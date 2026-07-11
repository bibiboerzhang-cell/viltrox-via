"""ReplyQueue enqueue_comment(市场之声 V0e「转回复队列」)契约测试(零 DB 依赖)。

断言面:
  1. 幂等:同 (platform, comment_external_id) 已在队 → 返回已有行(already_queued=True),
     绝不再发 INSERT;新入队 → INSERT 带 ON CONFLICT DO NOTHING(并发双击安全);
  2. 诚实失败:队列表缺失 / 评论不存在 / 空正文 / 缺 external_comment_id 各回真实 reason,
     绝不假装成功;
  3. 契约形状:item 键齐全;intent 规则不命中落 'manual';SQL 全 ? 参数化零字面 percent;
  4. 缺陷⑤口径:account_id 不在 vkpi_kol_pool → kol_pool_id 落 NULL;
  5. 路由层映射:comment_not_found → 404,其余失败 → 400(直接调 api_enqueue_comment,
     monkeypatch domain,零 FastAPI TestClient / 零真库)。
红线:mock conn 全程,不触真库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.comments import reply_queue  # noqa: E402


# ── mock conn(仿 tests 现有 _FakeConn 风格,按 SQL 内容路由 + 记录调用)──


class _FakeResult:
    def __init__(self, rows, rowcount=0):
        self._rows = rows
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(
        self,
        *,
        tables=("vkpi_reply_queue", "vkpi_kol_pool"),
        comment=None,
        queue_row=None,
        pool_ids=(),
    ):
        self.tables = set(tables)
        self.comment = comment
        self.queue_row = queue_row  # 已在队的现有行(None = 未入队)
        self.pool_ids = set(pool_ids)
        self.calls: list[tuple[str, tuple]] = []
        self.commits = 0

    @property
    def insert_calls(self):
        return [c for c in self.calls if c[0].lstrip().upper().startswith("INSERT")]

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        flat = " ".join(sql.split()).lower()
        if "information_schema.tables" in flat:
            name = params[0] if params else ""
            return _FakeResult([{"table_name": name}] if name in self.tables else [])
        if flat.startswith("insert into vkpi_reply_queue"):
            # 入队成功 → 物化队列行,后续 SELECT 读回同一行(仿真 RETURNING 之外的读回路径)
            self.queue_row = {
                "id": 501,
                "platform": params[0],
                "comment_external_id": params[2],
                "intent_tag": params[4],
                "lang": params[5],
                "status": "pending",
                "created_at": params[6],
            }
            return _FakeResult([], rowcount=1)
        if "from vkpi_comments" in flat:
            return _FakeResult([self.comment] if self.comment else [])
        if "from vkpi_reply_queue" in flat:
            return _FakeResult([self.queue_row] if self.queue_row else [])
        if "from vkpi_kol_pool" in flat:
            wanted = {int(p) for p in params}
            return _FakeResult([{"id": i} for i in sorted(self.pool_ids & wanted)])
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self):
        self.commits += 1


def _comment_row(**overrides):
    row = {
        "id": 1042,
        "external_comment_id": "yt-c-777",
        "comment_text": "How much is this lens and where can I buy it?",
        "platform": "youtube",
        "account_id": 9001,
        "language_detected": "en",
    }
    row.update(overrides)
    return row


ITEM_KEYS = {"id", "platform", "comment_external_id", "intent_tag", "lang", "status", "created_at"}


@pytest.fixture()
def fake_conn(monkeypatch):
    def _install(conn):
        monkeypatch.setattr(reply_queue, "get_conn", lambda: conn)
        return conn

    return _install


# ── 1. 诚实失败面 ──


def test_missing_queue_table_is_honest(fake_conn):
    conn = fake_conn(_FakeConn(tables=("vkpi_kol_pool",), comment=_comment_row()))
    result = reply_queue.enqueue_comment(1042)
    assert result == {"ok": False, "reason": "reply_queue_table_missing", "comment_id": 1042}
    assert conn.insert_calls == []


def test_comment_not_found(fake_conn):
    conn = fake_conn(_FakeConn(comment=None))
    result = reply_queue.enqueue_comment(999)
    assert result["ok"] is False
    assert result["reason"] == "comment_not_found"
    assert conn.insert_calls == []


def test_empty_text_and_missing_external_id_rejected(fake_conn):
    conn = fake_conn(_FakeConn(comment=_comment_row(comment_text="")))
    assert reply_queue.enqueue_comment(1042)["reason"] == "comment_text_empty"
    conn2 = fake_conn(_FakeConn(comment=_comment_row(external_comment_id="")))
    assert reply_queue.enqueue_comment(1042)["reason"] == "comment_missing_external_id"
    assert conn.insert_calls == [] and conn2.insert_calls == []


# ── 2. 新入队:契约形状 + ON CONFLICT + 缺陷⑤口径 ──


def test_enqueue_happy_path_contract(fake_conn):
    conn = fake_conn(_FakeConn(comment=_comment_row()))
    result = reply_queue.enqueue_comment(1042)
    assert result["ok"] is True
    assert result["already_queued"] is False
    assert result["comment_id"] == 1042
    assert set(result["item"].keys()) == ITEM_KEYS
    assert result["item"]["status"] == "pending"
    # intent 规则命中 price(how much / buy)
    assert result["item"]["intent_tag"] == "price"
    # 恰一次 INSERT,且带并发幂等护栏 ON CONFLICT DO NOTHING
    assert len(conn.insert_calls) == 1
    insert_sql, insert_params = conn.insert_calls[0]
    assert "ON CONFLICT (platform, comment_external_id) DO NOTHING" in insert_sql
    # 缺陷⑤:account_id=9001 不在 vkpi_kol_pool(pool_ids 空)→ kol_pool_id 落 NULL
    assert insert_params[1] is None
    assert conn.commits >= 1


def test_enqueue_intent_fallback_manual(fake_conn):
    fake_conn(_FakeConn(comment=_comment_row(comment_text="Nice colors on this footage")))
    result = reply_queue.enqueue_comment(1042)
    assert result["ok"] is True
    assert result["item"]["intent_tag"] == "manual"


def test_enqueue_keeps_valid_kol_pool_id(fake_conn):
    conn = fake_conn(_FakeConn(comment=_comment_row(), pool_ids={9001}))
    result = reply_queue.enqueue_comment(1042)
    assert result["ok"] is True
    assert conn.insert_calls[0][1][1] == 9001


# ── 3. 幂等:已在队 → 返回已有行,零 INSERT ──


def test_enqueue_idempotent_returns_existing_row(fake_conn):
    existing = {
        "id": 77,
        "platform": "youtube",
        "comment_external_id": "yt-c-777",
        "intent_tag": "price",
        "lang": "en",
        "status": "drafted",
        "created_at": "2026-07-10T00:00:00Z",
    }
    conn = fake_conn(_FakeConn(comment=_comment_row(), queue_row=existing))
    result = reply_queue.enqueue_comment(1042)
    assert result["ok"] is True
    assert result["already_queued"] is True
    assert result["item"]["id"] == 77
    assert result["item"]["status"] == "drafted"
    assert conn.insert_calls == []  # 幂等断言:绝不重复入队


# ── 4. SQL 卫生:全 ? 参数化,零字面 percent(compat 红线)──


def test_sql_hygiene_all_placeholders_no_percent(fake_conn):
    conn = fake_conn(_FakeConn(comment=_comment_row()))
    reply_queue.enqueue_comment(1042)
    for sql, _params in conn.calls:
        assert "%" not in sql, f"SQL 出现字面 percent(compat 红线): {sql}"


# ── 5. 路由层映射:comment_not_found → 404,其余失败 → 400,成功透传 ──


def test_router_maps_reasons_to_http(monkeypatch):
    from fastapi import HTTPException

    from app.api.routers import vkpi_reply_queue as router_mod

    def _fake_enqueue(reason=None, ok=False, **extra):
        def _inner(_comment_id):
            if ok:
                return {"ok": True, "already_queued": False, "comment_id": _comment_id, "item": {}, **extra}
            return {"ok": False, "reason": reason, "comment_id": _comment_id}

        return _inner

    body = router_mod.EnqueueCommentBody(comment_id=1042)

    monkeypatch.setattr(reply_queue, "enqueue_comment", _fake_enqueue(reason="comment_not_found"))
    with pytest.raises(HTTPException) as exc404:
        router_mod.api_enqueue_comment(body, staff=None)
    assert exc404.value.status_code == 404

    monkeypatch.setattr(reply_queue, "enqueue_comment", _fake_enqueue(reason="comment_text_empty"))
    with pytest.raises(HTTPException) as exc400:
        router_mod.api_enqueue_comment(body, staff=None)
    assert exc400.value.status_code == 400

    monkeypatch.setattr(reply_queue, "enqueue_comment", _fake_enqueue(ok=True))
    result = router_mod.api_enqueue_comment(body, staff=None)
    assert result["ok"] is True and result["comment_id"] == 1042
