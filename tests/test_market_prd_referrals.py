"""市场之声「转产品部」PRD 转交账本(prd_referrals v1)契约测试(零真 DB,mock conn)。

断言面(施工单验收口径):
  1. 幂等:同 (source_table, source_id) 已转交 → 返回已有行(already_referred=True),
     绝不再发 INSERT;新转交 → INSERT 带 ON CONFLICT DO NOTHING(并发双击安全);
  2. 诚实失败:账本表缺失 / 来源枚举非法 / source_id、title 缺失 / 评论不存在
     各回真实 reason,绝不假装成功;
  3. 契约形状:item 键白名单齐全;lexicon_reco 稳定 key 幂等不回查评论;
     文本截断(title 300 / detail 2000 / category 80);
  4. 显示层宪法:行里混入 author_* / raw_data_json 内部列也绝不进 item;
  5. LIMIT 双层封顶 50 + status 枚举(非法 → invalid_status);
  6. SQL 卫生:全 ? 参数化零字面 percent、零 LIKE(compat 红线);
  7. 路由层映射:comment_not_found → 404,其余失败 → 400,invalid_status → 400,
     成功透传(直接调 api_*,monkeypatch domain,零 FastAPI TestClient / 零真库);
  8. voice_report_ext.prd_referrals 组:表在 → 窗口计数 + 日序列 0 填齐 + 环比;
     表未建 → 诚实 empty(count=null)。
红线:mock conn 全程,不触真库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.market import prd_referrals, voice_report_ext  # noqa: E402


# ── mock conn(仿 test_reply_queue_enqueue_comment._FakeConn,按 SQL 内容路由)──


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
        tables=("vkpi_market_prd_referrals", "vkpi_comments"),
        comment_ids=(1042,),
        existing_row=None,
        list_rows=(),
    ):
        self.tables = set(tables)
        self.comment_ids = set(comment_ids)
        self.existing_row = existing_row  # 已转交的现有行(None = 未转交)
        self.list_rows = list(list_rows)
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
        if flat.startswith("insert into vkpi_market_prd_referrals"):
            # 转交成功 → 物化账本行,后续 SELECT 读回同一行
            self.existing_row = {
                "id": 7001,
                "source_table": params[0],
                "source_id": params[1],
                "title": params[2],
                "detail": params[3],
                "category": params[4],
                "status": "referred",
                "created_by_staff_id": params[5],
                "created_at": params[6],
                "updated_at": params[7],
            }
            return _FakeResult([], rowcount=1)
        if "from vkpi_comments" in flat:
            wanted = int(params[0]) if params else 0
            return _FakeResult([{"id": wanted}] if wanted in self.comment_ids else [])
        if "from vkpi_market_prd_referrals" in flat and "where source_table" in flat:
            return _FakeResult([self.existing_row] if self.existing_row else [])
        if "from vkpi_market_prd_referrals" in flat:
            return _FakeResult(self.list_rows)
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self):
        self.commits += 1


ITEM_KEYS = {
    "id", "source_table", "source_id", "title", "detail", "category",
    "status", "created_by_staff_id", "created_at", "updated_at",
}


@pytest.fixture()
def fake_conn(monkeypatch):
    def _install(conn):
        monkeypatch.setattr(prd_referrals, "get_conn", lambda: conn)
        return conn

    return _install


# ── 1. 诚实失败面 ──


def test_missing_table_is_honest(fake_conn):
    conn = fake_conn(_FakeConn(tables=("vkpi_comments",)))
    result = prd_referrals.create_referral("vkpi_comments", "1042", "标题")
    assert result == {"ok": False, "reason": "prd_referrals_table_missing"}
    assert conn.insert_calls == []


def test_invalid_source_table_rejected(fake_conn):
    conn = fake_conn(_FakeConn())
    result = prd_referrals.create_referral("vkpi_bh_reviews", "1", "标题")
    assert result["ok"] is False
    assert result["reason"] == "invalid_source_table"
    assert conn.calls == []  # 枚举挡在 SQL 之前


def test_missing_source_id_and_title_rejected(fake_conn):
    conn = fake_conn(_FakeConn())
    assert prd_referrals.create_referral("vkpi_comments", "", "标题")["reason"] == "source_id_required"
    assert prd_referrals.create_referral("vkpi_comments", "1042", "  ")["reason"] == "title_required"
    assert conn.calls == []


def test_comment_not_found(fake_conn):
    conn = fake_conn(_FakeConn(comment_ids=()))
    result = prd_referrals.create_referral("vkpi_comments", "999", "标题")
    assert result["ok"] is False
    assert result["reason"] == "comment_not_found"
    assert conn.insert_calls == []
    # 非数字评论 id 同样按未找到处理(不硬塞脏 key)
    assert prd_referrals.create_referral("vkpi_comments", "abc", "标题")["reason"] == "comment_not_found"


# ── 2. 新转交:契约形状 + ON CONFLICT + 截断 ──


def test_refer_comment_happy_path_contract(fake_conn):
    conn = fake_conn(_FakeConn())
    result = prd_referrals.create_referral(
        "vkpi_comments", "1042", "对焦很稳,呼吸效应控制超预期",
        detail="反馈流单条转交", category="af",
    )
    assert result["ok"] is True
    assert result["already_referred"] is False
    assert set(result["item"].keys()) == ITEM_KEYS
    assert result["item"]["status"] == "referred"
    assert result["item"]["source_table"] == "vkpi_comments"
    assert result["item"]["source_id"] == "1042"
    # 恰一次 INSERT,带并发幂等护栏 ON CONFLICT DO NOTHING
    assert len(conn.insert_calls) == 1
    insert_sql, _params = conn.insert_calls[0]
    assert "ON CONFLICT (source_table, source_id) DO NOTHING" in insert_sql
    assert conn.commits >= 1


def test_refer_lexicon_reco_skips_comment_probe(fake_conn):
    conn = fake_conn(_FakeConn())
    result = prd_referrals.create_referral(
        "lexicon_reco", "improvement:改进关注:对焦", "改进关注:对焦", category="improvement",
    )
    assert result["ok"] is True and result["already_referred"] is False
    # lexicon_reco 无库可回查 → 绝不查 vkpi_comments
    assert all("vkpi_comments" not in sql for sql, _p in conn.calls)


def test_refer_truncates_long_text(fake_conn):
    conn = fake_conn(_FakeConn())
    result = prd_referrals.create_referral(
        "lexicon_reco", "k" * 500, "t" * 500, detail="d" * 5000, category="c" * 200,
    )
    assert result["ok"] is True
    _sql, params = conn.insert_calls[0]
    assert len(params[1]) == prd_referrals.SOURCE_ID_MAX_CHARS
    assert len(params[2]) == prd_referrals.TITLE_MAX_CHARS
    assert len(params[3]) == prd_referrals.DETAIL_MAX_CHARS
    assert len(params[4]) == prd_referrals.CATEGORY_MAX_CHARS


# ── 3. 幂等:已转交 → 返回已有行,零 INSERT ──


def test_refer_idempotent_returns_existing_row(fake_conn):
    existing = {
        "id": 77,
        "source_table": "vkpi_comments",
        "source_id": "1042",
        "title": "旧标题",
        "detail": "",
        "category": "af",
        "status": "accepted",
        "created_by_staff_id": 3,
        "created_at": "2026-07-10T00:00:00Z",
        "updated_at": "2026-07-10T00:00:00Z",
    }
    conn = fake_conn(_FakeConn(existing_row=existing))
    result = prd_referrals.create_referral("vkpi_comments", "1042", "新标题")
    assert result["ok"] is True
    assert result["already_referred"] is True
    assert result["item"]["id"] == 77
    assert result["item"]["status"] == "accepted"
    assert conn.insert_calls == []  # 幂等断言:绝不重复转交


# ── 4. 显示层宪法:内部列混入行里也不出 item ──


def test_item_never_leaks_private_fields(fake_conn):
    dirty = {
        "id": 5, "source_table": "vkpi_comments", "source_id": "1042",
        "title": "t", "detail": "", "category": "", "status": "referred",
        "created_by_staff_id": None, "created_at": "2026-07-10T00:00:00Z",
        "updated_at": "2026-07-10T00:00:00Z",
        # 混入内部/个人列也绝不外泄
        "author_handle": "should_never_leak", "author_id": "u-999", "raw_data_json": "{}",
    }
    conn = fake_conn(_FakeConn(existing_row=dirty, list_rows=[dirty]))
    referred = prd_referrals.create_referral("vkpi_comments", "1042", "t")
    listed = prd_referrals.list_referrals()
    flat = repr(referred) + repr(listed)
    assert "should_never_leak" not in flat
    assert "u-999" not in flat
    assert "raw_data_json" not in flat
    assert set(listed["items"][0].keys()) == ITEM_KEYS


# ── 5. 列表:LIMIT 双层封顶 + status 枚举 ──


def test_list_limit_double_capped_and_status_pushdown(fake_conn):
    over_rows = [
        {
            "id": i, "source_table": "lexicon_reco", "source_id": f"k{i}", "title": "t",
            "detail": "", "category": "", "status": "referred",
            "created_by_staff_id": None, "created_at": "2026-07-10T00:00:00Z",
            "updated_at": "2026-07-10T00:00:00Z",
        }
        for i in range(80)  # 超量喂 80(mock SQL 放水)
    ]
    conn = fake_conn(_FakeConn(list_rows=over_rows))
    body = prd_referrals.list_referrals(status="referred", limit=999)
    # Python 层二次封顶(哪怕 SQL 层被 mock 放水)
    assert body["count"] == len(body["items"]) == prd_referrals.MAX_LIMIT == 50
    list_sql, list_params = conn.calls[-1]
    assert "LIMIT ?" in list_sql
    assert list_params == ("referred", 50)  # status 参数化下推 + LIMIT=封顶值


def test_list_invalid_status_rejected(fake_conn):
    conn = fake_conn(_FakeConn())
    body = prd_referrals.list_referrals(status="alien")
    assert body["ok"] is False
    assert body["reason"] == "invalid_status"
    assert body["allowed"] == ["referred", "accepted", "rejected"]
    assert conn.calls == []  # 枚举挡在 SQL 之前


def test_list_missing_table_is_honest_empty(fake_conn):
    fake_conn(_FakeConn(tables=("vkpi_comments",)))
    body = prd_referrals.list_referrals()
    assert body["ok"] is True
    assert body["status"] == "absent"
    assert body["items"] == [] and body["count"] == 0


# ── 6. SQL 卫生:全 ? 参数化,零字面 percent、零 LIKE(compat 红线)──


def test_sql_hygiene_all_placeholders_no_percent(fake_conn):
    conn = fake_conn(_FakeConn())
    prd_referrals.create_referral("vkpi_comments", "1042", "标题")
    prd_referrals.list_referrals(status="referred", limit=10)
    assert conn.calls
    for sql, _params in conn.calls:
        assert "%" not in sql, f"SQL 出现字面 percent(compat 红线): {sql}"
        assert " LIKE " not in f" {sql.upper()} ".replace("\n", " ")


# ── 7. 路由层映射:404 / 400 / 成功透传(monkeypatch domain,零真库)──


def test_router_maps_reasons_to_http(monkeypatch):
    from fastapi import HTTPException

    from app.api.routers import vkpi_market_voice as router_mod

    body = router_mod.PrdReferralBody(source_table="vkpi_comments", source_id="1042", title="t")

    def _fake_create(reason=None, ok=False):
        def _inner(*_args, **_kwargs):
            if ok:
                return {"ok": True, "already_referred": False, "item": {"id": 1}}
            return {"ok": False, "reason": reason}

        return _inner

    monkeypatch.setattr(prd_referrals, "create_referral", _fake_create(reason="comment_not_found"))
    with pytest.raises(HTTPException) as exc404:
        router_mod.api_create_prd_referral(body, staff=None)
    assert exc404.value.status_code == 404

    monkeypatch.setattr(prd_referrals, "create_referral", _fake_create(reason="invalid_source_table"))
    with pytest.raises(HTTPException) as exc400:
        router_mod.api_create_prd_referral(body, staff=None)
    assert exc400.value.status_code == 400

    monkeypatch.setattr(prd_referrals, "create_referral", _fake_create(ok=True))
    result = router_mod.api_create_prd_referral(body, staff=None)
    assert result["ok"] is True and result["item"]["id"] == 1

    monkeypatch.setattr(
        prd_referrals, "list_referrals",
        lambda **_kw: {"ok": False, "reason": "invalid_status"},
    )
    with pytest.raises(HTTPException) as exc_list:
        router_mod.api_list_prd_referrals(status="alien", limit=50, staff=None)
    assert exc_list.value.status_code == 400

    monkeypatch.setattr(
        prd_referrals, "list_referrals",
        lambda **_kw: {"ok": True, "status": "ready", "items": [], "count": 0},
    )
    listed = router_mod.api_list_prd_referrals(status="", limit=50, staff=None)
    assert listed["status"] == "ready"


# ── 8. voice_report_ext.prd_referrals 组(KPI 真计数,窗口敏感)──


class _ExtFakeConn:
    """routes: {SQL 常量: rows 或 callable(params)->rows};没配的 SQL 返回空。"""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        for known_sql, rows in self.routes.items():
            if sql == known_sql:
                return _FakeResult(rows(tuple(params)) if callable(rows) else rows)
        return _FakeResult([])


def test_ext_prd_referrals_counts_series_and_delta():
    from app.domains.market.market_voice import _window

    since_0606, _until, _label = _window("2026-06")

    def prd_count(params):
        # 本窗 3,上窗 2 → +50.0%
        return [{"n": 3}] if params[0] == since_0606 else [{"n": 2}]

    conn = _ExtFakeConn(routes={
        voice_report_ext.PRD_TABLE_PROBE_SQL: [{"table_name": voice_report_ext.PRD_TABLE}],
        voice_report_ext.PRD_COUNT_SQL: prd_count,
        voice_report_ext.PRD_DAY_SQL: [{"day": "2026-06-03", "n": 2}, {"day": "2026-06-10", "n": 1}],
    })
    group = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)["prd_referrals"]
    assert group["status"] == "ready"
    assert group["count"] == 3
    assert group["previous"] == 2
    assert group["delta_pct"] == 50.0
    assert group["table"] == "vkpi_market_prd_referrals"
    # 日序列 0 填齐:2026-06 整月 30 天连续日轴
    series = group["series"]
    assert len(series) == 30
    assert series[0] == {"date": "2026-06-01", "count": 0}
    assert series[2] == {"date": "2026-06-03", "count": 2}
    assert series[9] == {"date": "2026-06-10", "count": 1}
    assert sum(p["count"] for p in series) == 3


def test_ext_prd_referrals_zero_is_honest_zero_not_pending():
    conn = _ExtFakeConn(routes={
        voice_report_ext.PRD_TABLE_PROBE_SQL: [{"table_name": voice_report_ext.PRD_TABLE}],
        voice_report_ext.PRD_COUNT_SQL: [{"n": 0}],
    })
    group = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)["prd_referrals"]
    # 表在 + 零转交 → status=ready、count=0(0 也如实 0,前端不再摆 pending 卡)
    assert group["status"] == "ready"
    assert group["count"] == 0
    assert group["delta_pct"] is None  # 上窗 0 → 诚实省略环比


def test_ext_prd_referrals_missing_table_is_honest_empty():
    conn = _ExtFakeConn()  # 探针不配 → 表未建
    group = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)["prd_referrals"]
    assert group["status"] == "empty"
    assert group["count"] is None
    assert group["series"] == []
    assert "reason" in group
