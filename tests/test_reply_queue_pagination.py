"""ReplyQueue list_queue 服务端分页契约测试(零 DB 依赖)。

断言面:
  1. 旧调用零破坏:缺省 offset=0 → SQL 尾参 (limit, 0);items/count 键原样,
     total/offset/limit 纯增量;count=本页行数;
  2. 双层封顶:limit>500 钳 500、limit=0 回落 100;offset<0 钳 0、offset 天文数字
     钳 MAX_LIST_OFFSET(router 层另有 Query ge/le 校验,domain 层为第二重);
  3. total 口径:同一份 WHERE(过滤 + 可见性)先 COUNT,总数与页无关(前端
     「已显 X/Y」真分母);过滤参数同时进 COUNT 与 SELECT;
  4. 可见性收敛进两条 SQL:非管理层 → COUNT/SELECT 都带 claimed_by 收敛;
  5. 稳定定序:ORDER BY 末位 id DESC(created_at 撞值跨页不重不漏)+ LIMIT ? OFFSET ?;
  6. 表未建 → 诚实空({items:[], count:0, total:0});
  7. 路由层:api_list 把 offset 透传 domain(monkeypatch,零 FastAPI TestClient);
  8. SQL 卫生:全 ? 参数化零字面 percent(compat 红线)。
红线:mock conn 全程,不触真库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402

from app.domains.comments import reply_queue  # noqa: E402

OWNER = {"id": 1, "is_owner": 1}
MEMBER = {"id": 7, "role": "member", "is_owner": 0}


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _queue_row(i: int) -> dict:
    return {
        "id": i,
        "platform": "youtube",
        "kol_pool_id": None,
        "comment_external_id": f"ext-{i}",
        "comment_text": f"row {i}",
        "intent_tag": "price",
        "lang": "en",
        "draft_reply": "",
        "status": "pending",
        "claimed_by": None,
        "claimed_at": "",
        "created_at": "2026-07-10T00:00:00Z",
        "updated_at": "2026-07-10T00:00:00Z",
    }


class _FakeConn:
    """按 SQL 内容路由:information_schema 探针 / COUNT / SELECT 分页页。"""

    def __init__(self, *, has_table=True, total=1234, page_rows=2):
        self.has_table = has_table
        self.total = total
        self.page_rows = page_rows
        self.calls: list[tuple[str, tuple]] = []

    @property
    def count_calls(self):
        return [c for c in self.calls if "count(*)" in " ".join(c[0].split()).lower()]

    @property
    def select_calls(self):
        return [
            c
            for c in self.calls
            if "select id, platform" in " ".join(c[0].split()).lower()
        ]

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        flat = " ".join(sql.split()).lower()
        if "information_schema.tables" in flat:
            return _FakeResult([{"table_name": "vkpi_reply_queue"}] if self.has_table else [])
        if "count(*)" in flat:
            return _FakeResult([{"n": self.total}])
        if flat.startswith("select id, platform"):
            return _FakeResult([_queue_row(i) for i in range(1, self.page_rows + 1)])
        raise AssertionError(f"unexpected SQL: {sql}")


@pytest.fixture()
def fake_conn(monkeypatch):
    def _install(conn):
        monkeypatch.setattr(reply_queue, "get_conn", lambda: conn)
        return conn

    return _install


# ── 1. 旧调用零破坏 + 增量键 ──


def test_default_call_zero_breakage(fake_conn):
    conn = fake_conn(_FakeConn(total=1234, page_rows=2))
    result = reply_queue.list_queue(staff=OWNER)
    # 旧键原样:items 列表 + count=本页行数
    assert isinstance(result["items"], list)
    assert result["count"] == 2
    # 增量键:total=COUNT 真分母(与页无关),offset/limit 回显钳后值
    assert result["total"] == 1234
    assert result["offset"] == 0
    assert result["limit"] == 100
    # SQL 尾参:LIMIT ? OFFSET ? = (100, 0)
    sql, params = conn.select_calls[0]
    assert "LIMIT ? OFFSET ?" in sql
    assert params[-2:] == (100, 0)


def test_offset_passes_through_and_total_page_independent(fake_conn):
    conn = fake_conn(_FakeConn(total=1234, page_rows=2))
    result = reply_queue.list_queue(offset=500, limit=500, staff=OWNER)
    assert result["total"] == 1234  # 总数与页无关
    assert result["count"] == 2  # 本页行数
    assert result["offset"] == 500
    _sql, params = conn.select_calls[0]
    assert params[-2:] == (500, 500)


# ── 2. 双层封顶(domain 第二重;router Query ge/le 第一重)──


def test_limit_and_offset_clamped(fake_conn):
    conn = fake_conn(_FakeConn())
    result = reply_queue.list_queue(limit=99999, offset=-5, staff=OWNER)
    assert result["limit"] == 500 and result["offset"] == 0
    assert conn.select_calls[0][1][-2:] == (500, 0)

    conn2 = fake_conn(_FakeConn())
    result2 = reply_queue.list_queue(limit=0, offset=10**9, staff=OWNER)
    assert result2["limit"] == 100  # limit=0 回落缺省
    assert result2["offset"] == reply_queue.MAX_LIST_OFFSET
    assert conn2.select_calls[0][1][-2:] == (100, reply_queue.MAX_LIST_OFFSET)


# ── 3. 过滤 + 可见性:COUNT 与 SELECT 同一份 WHERE ──


def test_filters_apply_to_both_count_and_select(fake_conn):
    conn = fake_conn(_FakeConn())
    reply_queue.list_queue(status="pending", platform="YouTube", offset=10, limit=50, staff=OWNER)
    count_sql, count_params = conn.count_calls[0]
    select_sql, select_params = conn.select_calls[0]
    for sql in (count_sql, select_sql):
        assert "status = ?" in sql
        assert "LOWER(COALESCE(platform,'')) = ?" in sql
    assert count_params == ("pending", "youtube")
    # SELECT = 同过滤参数 + 尾参 (limit, offset)
    assert select_params == ("pending", "youtube", 50, 10)


def test_member_visibility_clause_in_both_queries(fake_conn):
    conn = fake_conn(_FakeConn())
    reply_queue.list_queue(staff=MEMBER)
    count_sql, count_params = conn.count_calls[0]
    select_sql, select_params = conn.select_calls[0]
    for sql in (count_sql, select_sql):
        assert "claimed_by IS NULL OR claimed_by = ?" in sql
    assert count_params == (7,)
    assert select_params == (7, 100, 0)


# ── 4. 稳定定序:末位 id DESC(OFFSET 分页跨页不重不漏的前提)──


def test_stable_order_with_id_tiebreaker(fake_conn):
    conn = fake_conn(_FakeConn())
    reply_queue.list_queue(staff=OWNER)
    sql = " ".join(conn.select_calls[0][0].split())
    assert "created_at DESC, id DESC LIMIT ? OFFSET ?" in sql


# ── 5. 表未建 → 诚实空 ──


def test_missing_table_honest_empty(fake_conn):
    fake_conn(_FakeConn(has_table=False))
    result = reply_queue.list_queue(offset=50, staff=OWNER)
    assert result == {"items": [], "count": 0, "total": 0, "offset": 50, "limit": 100}


# ── 6. SQL 卫生:全 ? 参数化零字面 percent(compat 红线)──


def test_sql_hygiene_no_percent(fake_conn):
    conn = fake_conn(_FakeConn())
    reply_queue.list_queue(status="pending", offset=5, staff=MEMBER)
    for sql, _params in conn.calls:
        assert "%" not in sql, f"SQL 出现字面 percent(compat 红线): {sql}"


# ── 7. 路由层:offset 透传 domain(缺省 0 = 旧行为)──


def test_router_passes_offset(monkeypatch):
    from app.api.routers import vkpi_reply_queue as router_mod

    seen: dict = {}

    def _fake_list(**kwargs):
        seen.update(kwargs)
        return {"items": [], "count": 0, "total": 0, "offset": kwargs.get("offset"), "limit": kwargs.get("limit")}

    monkeypatch.setattr(reply_queue, "list_queue", _fake_list)
    result = router_mod.api_list(status="", platform="", limit=500, offset=1000, staff=OWNER)
    assert seen["offset"] == 1000 and seen["limit"] == 500
    assert result["offset"] == 1000
