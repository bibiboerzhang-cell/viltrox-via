"""单品播放「逐条重新实测」的只读投影(sqlite 假库,零 provider、零入队)。

界面在点之前必须能回答三件事,本组测试就钉这三件 + 一个静默截断陷阱:
  1. 能不能点   —— 付费动作围栏:自己收藏的可测,同事共享的只能看;
  2. 什么状态   —— 排队 / 被拦下 如实回显;任务态与数据新鲜度分层,
                  且失败任务**不会**改实测时刻,只盯时刻就会永远显示「还没回来」;
  3. 上次多久前 —— 从未成功实测一律 never,绝不当「刚测过」。
另加:一页最多 800 行而单次 IN 查询只吃 200,不分片会让第 201 行起
静默变成「未发起」——这本身就是违反诚实进度红线的假象。

后半段(2026-08-25)钉服务端硬闸:单次上限 / 每日上限 / 冷却全部由服务端判,
报价 = 真实取数次数,派活必须带报价指纹,超上限 / 额度耗尽 / 冷却期内一条都不派。

夹具与种子复用 test_my_kol_sku_play(同一张假库),本文件只补重新实测那一层。
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.kol import (  # noqa: E402
    sku_play_overview,
    sku_play_refresh_dispatch,
    sku_play_refresh_plan,
    sku_play_refresh_state,
    video_metric_refresh,
    video_tracking,
)
from test_my_kol_sku_play import (  # noqa: E402
    MANAGER,
    NOW,
    STAFF_A,
    _conn,
    _fake_active_enqueue,
    _iso,
    _job_count,
    _seed_link,
    _seed_overview,
)


STAFF_A_W = {**STAFF_A, "permissions_json": '{"vkpi":"write"}'}


@pytest.fixture()
def conn(monkeypatch):
    db = _conn()
    monkeypatch.setattr(video_metric_refresh, "enqueue_active_apify_job", _refuse_enqueue)
    yield db
    db.close()


def _refuse_enqueue(*_args, **_kwargs):
    raise AssertionError("只读投影绝不允许入队")

def _seed_refresh_job(conn, evidence_id: int, status: str, **extra) -> int:
    payload = {
        "target_type": "kol_video_evidence",
        "target_id": str(evidence_id),
        "evidence_id": evidence_id,
        "derive_method": "content_metric_refresh_v1",
    }
    return int(
        conn.execute(
            """
            INSERT INTO apify_jobs (
                job_type, payload, idempotency_key, status, attempts,
                next_retry_at, last_error, last_error_category, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_metric_refresh.VIDEO_METRIC_REFRESH_JOB_TYPE,
                json.dumps(payload),
                f"seed:{evidence_id}:{status}:{extra.get('key', '')}",
                status,
                int(extra.get("attempts", 0)),
                extra.get("next_retry_at"),
                extra.get("last_error"),
                extra.get("last_error_category"),
                _iso(extra.get("at") or NOW),
                _iso(extra.get("at") or NOW),
            ),
        ).lastrowid
    )


def _items_by_evidence(body) -> dict:
    return {
        item["evidence_id"]: item
        for group in body["groups"]
        for item in group["items"]
    }


def test_row_refresh_state_reports_queued_job_and_write_fence(conn):
    _seed_overview(conn)
    # KOL 3 是同事(B)收藏、共享给 A 看的:A 只能查看,不能从这里重新实测。
    conn.execute("INSERT INTO vkpi_kol_pool_members (kol_pool_id, staff_id) VALUES (3, 10)")
    # 任务比快照新 → 旧读数仍可见但要标「重测中」。
    job_id = _seed_refresh_job(conn, 101, "queued", at=NOW + timedelta(minutes=1))
    conn.commit()

    body = sku_play_overview.build_sku_play_overview(conn, staff=STAFF_A_W, now=NOW)
    items = _items_by_evidence(body)

    # 自己收藏的行:可发起,任务态如实回显排队中(不是「已完成」)。
    assert items[101]["can_refresh"] is True
    assert items[101]["refresh_forbidden_reason"] is None
    assert items[101]["refresh"]["status"] == "queued"
    assert items[101]["refresh"]["job_id"] == job_id
    # 数据层与任务层分离:排队不代表读数已更新,旧读数仍可见。
    assert items[101]["refresh"]["data"]["superseded_by_job"] is True
    assert items[101]["view_count"] == 1600

    # 共享进来的行:能看不能测,原因是稳定机器码,由门面另做人话。
    assert items[301]["can_refresh"] is False
    assert items[301]["refresh_forbidden_reason"] == "my_kol_paid_action_write_forbidden"

    # 单品行按钮的条数由服务端实算(可测 2 条、在路上 1 条),前端不再猜。
    group_a = next(g for g in body["groups"] if g["sku_code"] == "SKU-A")
    assert group_a["refreshable_videos"] == 2
    assert group_a["in_flight_videos"] == 1


def test_row_refresh_state_never_fakes_freshness_or_success(conn):
    _seed_overview(conn)
    conn.commit()
    items = _items_by_evidence(
        sku_play_overview.build_sku_play_overview(conn, staff=STAFF_A_W, now=NOW)
    )
    # 从未成功实测过的行:never + none,绝不当「刚测过」。
    assert items[102]["measured_at"] is None
    assert items[102]["refresh"]["data"]["freshness"] == "never"
    assert items[102]["refresh"]["data"]["status"] == "none"
    assert items[102]["refresh"]["status"] == "not_requested"
    assert items[102]["recently_measured"] is False
    # 刚在 NOW 实测过的行:fresh(界面据此提示「刚测过」,后端不因此拒绝)。
    assert items[101]["recently_measured"] is True
    # 采样档位来自既有 TIER_CADENCES,不另发明数字(101 发布 10 天前 = warm 24h)。
    assert items[101]["refresh_cadence_hours"] == 24


def test_failed_refresh_is_visible_even_though_measured_at_unchanged(conn):
    """失败任务不改实测时刻:只盯实测时刻就会永远显示「还没回来」。"""

    _seed_overview(conn)
    _seed_refresh_job(
        conn, 101, "blocked", at=NOW + timedelta(minutes=1),
        last_error="budget_guard_blocked", last_error_category="budget",
    )
    conn.commit()
    item = _items_by_evidence(
        sku_play_overview.build_sku_play_overview(conn, staff=STAFF_A_W, now=NOW)
    )[101]
    assert item["measured_at"] == _iso(NOW)          # 读数没变
    assert item["refresh"]["status"] == "blocked"    # 但任务被拦下,不许装作还在路上
    assert item["refresh"]["reason_class"] == "budget"
    assert item["refresh"]["failure_reason_human"]   # 中文一句,门面直接可用
    group_a = next(
        g for g in sku_play_overview.build_sku_play_overview(conn, staff=STAFF_A_W, now=NOW)["groups"]
        if g["sku_code"] == "SKU-A"
    )
    assert group_a["in_flight_videos"] == 0


def test_refresh_job_lookup_shards_beyond_single_query_cap(conn):
    """一页最多 800 行而单次 IN 查询只吃 200:不分片就会静默变成「未发起」。"""

    cap = sku_play_refresh_state.JOB_LOOKUP_CHUNK
    ids = list(range(9000, 9000 + cap + 50))
    for evidence_id in ids:
        _seed_refresh_job(conn, evidence_id, "queued")
    conn.commit()
    found = sku_play_refresh_state.latest_refresh_jobs(conn, ids)
    assert len(found) == len(ids)
    assert all(found[evidence_id]["status"] == "queued" for evidence_id in ids)


def test_refresh_annotation_is_read_only(conn):
    """报价/状态投影绝不入队:跑一遍不应该多出任何任务行。"""

    _seed_overview(conn)
    conn.commit()
    before = _job_count(conn)
    sku_play_overview.build_sku_play_overview(conn, staff=STAFF_A_W, now=NOW)
    sku_play_overview.build_sku_play_overview(conn, staff=MANAGER, now=NOW)
    assert _job_count(conn) == before == 0


# ── 服务端硬闸:单次上限 / 每日上限 / 冷却(2026-08-25 HIGH 1)─────────────────
#
# 为什么必须有:单品行的「重新实测 N 条」原本没有任何上限,二次确认只报数字不封顶,
# 绕开前端就是无上限的批量花钱按钮。三道闸的口径照抄内容墙侧,且**全部在服务端判**。


@pytest.fixture()
def wconn(monkeypatch):
    """可写夹具:入队器换成记账假件(仍然零 provider、零真实花费)。"""

    db = _conn()
    monkeypatch.setattr(video_metric_refresh, "enqueue_active_apify_job", _fake_active_enqueue)
    for name in (
        "VKPI_SKU_PLAY_REFRESH_PER_CLICK",
        "VKPI_SKU_PLAY_REFRESH_DAILY_MAX",
        "VKPI_SKU_PLAY_REFRESH_COOLDOWN_HOURS",
    ):
        monkeypatch.delenv(name, raising=False)
    yield db
    db.close()


def _seed_lane_job(conn, evidence_id: int, *, at, status: str = "done", source: str | None = None) -> int:
    """本车道自己派过的一次取数(source 是闸的记账依据)。"""

    payload = {
        "target_type": "kol_video_evidence",
        "target_id": str(evidence_id),
        "evidence_id": evidence_id,
        "derive_method": "content_metric_refresh_v1",
        "source": sku_play_refresh_plan.SKU_PLAY_REFRESH_SOURCE if source is None else source,
    }
    return int(
        conn.execute(
            """
            INSERT INTO apify_jobs (
                job_type, payload, idempotency_key, status, attempts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (
                sku_play_refresh_plan.REFRESH_JOB_TYPE,
                json.dumps(payload),
                f"lane:{evidence_id}:{_iso(at)}:{status}",
                status,
                _iso(at),
                _iso(at),
            ),
        ).lastrowid
    )


def _plan(conn, **kwargs):
    params = {"sku_code": "SKU-A", "staff": STAFF_A_W, "staff_scope_id": 10, "now": NOW}
    params.update(kwargs)
    return sku_play_refresh_plan.plan_sku_play_refresh(conn, **params)


def test_plan_counts_real_fetch_calls_and_books_every_skip(wconn):
    """报价 = 真实取数次数(一条视频一次),被闸挡下的一条也不许静默消失。"""

    _seed_overview(wconn)
    wconn.execute("INSERT INTO vkpi_kol_pool_members (kol_pool_id, staff_id) VALUES (3, 10)")
    wconn.commit()

    plan = _plan(wconn)
    # 候选 3 条:101 刚成功实测过(冷却)、102 只有失败快照(可取)、301 是共享的。
    assert plan["candidates_total"] == 3
    assert plan["planned_count"] == 1
    assert [item["evidence_id"] for item in plan["planned"]] == [102]
    assert plan["fetch_per_video"] == 1
    assert plan["fetch_calls_total"] == 1
    assert plan["skipped_counts"]["recently_measured"] == 1
    assert plan["skipped_counts"]["shared_readonly"] == 1
    assert plan["skipped"]["shared_readonly"][0]["reason"] == "my_kol_paid_action_write_forbidden"
    assert plan["requires_confirmation"] is True
    assert plan["limits"] == {
        "per_click": 12,
        "daily": 40,
        "daily_used": 0,
        "daily_left": 40,
        "cooldown_hours": 6,
    }


def test_plan_never_charges_for_rows_already_in_flight(wconn):
    """已经在队列里的行并入既有任务:不重复排、不计入报价,但要如实报出来。"""

    _seed_overview(wconn)
    _seed_refresh_job(wconn, 102, "queued")
    wconn.commit()

    plan = _plan(wconn)
    assert plan["planned_count"] == 0
    assert plan["skipped_counts"]["already_in_flight"] == 1
    assert plan["skipped"]["already_in_flight"][0]["evidence_id"] == 102


def test_per_click_cap_is_enforced_server_side(wconn, monkeypatch):
    monkeypatch.setenv("VKPI_SKU_PLAY_REFRESH_PER_CLICK", "1")
    _seed_overview(wconn)
    _seed_link(wconn, 103, "SKU-A")       # 103 从未实测,与 102 同为可取
    wconn.commit()

    plan = _plan(wconn)
    assert plan["planned_count"] == 1
    assert plan["skipped_counts"]["per_click_cap"] == 1
    assert plan["limits"]["per_click"] == 1


def test_daily_cap_exhausted_blocks_everything(wconn, monkeypatch):
    monkeypatch.setenv("VKPI_SKU_PLAY_REFRESH_DAILY_MAX", "2")
    _seed_overview(wconn)
    _seed_link(wconn, 103, "SKU-A")
    for offset in (1, 2):
        _seed_lane_job(wconn, 900 + offset, at=NOW - timedelta(hours=offset))
    wconn.commit()

    plan = _plan(wconn)
    assert plan["limits"]["daily_used"] == 2
    assert plan["limits"]["daily_left"] == 0
    assert plan["planned_count"] == 0
    assert plan["skipped_counts"]["daily_cap"] == 2


def test_daily_counter_only_books_this_lane_and_last_24h(wconn):
    """别的入口的活不算本车道的账;24 小时以前的也不算。"""

    _seed_overview(wconn)
    _seed_lane_job(wconn, 901, at=NOW - timedelta(hours=1), source="my_kol_video_tracking")
    _seed_lane_job(wconn, 902, at=NOW - timedelta(hours=30))
    _seed_lane_job(wconn, 903, at=NOW - timedelta(hours=2))
    wconn.commit()

    assert _plan(wconn)["limits"]["daily_used"] == 1


def test_cooldown_covers_recent_success_and_this_lane_recent_dispatch(wconn):
    """冷却两个真源各自独立生效;冷却窗外就该放行。"""

    _seed_overview(wconn)
    _seed_lane_job(wconn, 102, at=NOW - timedelta(hours=1))   # 刚派过,结果还没回来
    wconn.commit()
    plan = _plan(wconn)
    assert plan["planned_count"] == 0
    assert plan["skipped_counts"]["recently_measured"] == 2

    # 冷却窗外(101 上次成功在 8 小时前,本车道那次也退到 8 小时前)→ 两条都放行。
    later = NOW + timedelta(hours=7)
    plan_later = _plan(wconn, now=later)
    assert [item["evidence_id"] for item in plan_later["planned"]] == [101, 102]


def test_failed_snapshot_never_counts_as_recently_measured(wconn):
    """失败快照不是实测:拿它当冷却依据 = 用户永远测不了这条。"""

    _seed_overview(wconn)
    wconn.commit()
    plan = _plan(wconn)
    # 102 只有一条 NOW-1d 的 failed 快照,照样可取。
    assert [item["evidence_id"] for item in plan["planned"]] == [102]


def test_env_caps_cannot_exceed_the_hard_ceilings(monkeypatch):
    monkeypatch.setenv("VKPI_SKU_PLAY_REFRESH_PER_CLICK", "9999")
    monkeypatch.setenv("VKPI_SKU_PLAY_REFRESH_DAILY_MAX", "9999")
    monkeypatch.setenv("VKPI_SKU_PLAY_REFRESH_COOLDOWN_HOURS", "99999")
    assert sku_play_refresh_plan.per_click_cap() == 30
    assert sku_play_refresh_plan.daily_cap() == 120
    assert sku_play_refresh_plan.cooldown_hours() == 168


def test_plan_itself_never_spends(wconn):
    """报价必须是纯 SELECT:跑几遍都不应该多出任何任务行。"""

    _seed_overview(wconn)
    wconn.commit()
    before = _job_count(wconn)
    _plan(wconn)
    _plan(wconn, evidence_id=102)
    assert _job_count(wconn) == before == 0


# ── 派活:报价指纹绑定 + 闸的裁决落到实处 ────────────────────────────────────


def _dispatch(conn, plan, **kwargs):
    params = {
        "staff": STAFF_A_W,
        "staff_scope_id": 10,
        "sku_code": "SKU-A",
        "plan_hash": plan["plan_hash"],
        "expected_count": plan["planned_count"],
        "now": NOW,
    }
    params.update(kwargs)
    return sku_play_refresh_dispatch.run_sku_play_refresh(conn, **params)


def test_dispatch_without_plan_hash_is_refused(wconn):
    _seed_overview(wconn)
    wconn.commit()
    plan = _plan(wconn)
    with pytest.raises(sku_play_refresh_dispatch.SkuPlayRefreshError) as excinfo:
        _dispatch(wconn, plan, plan_hash="")
    assert excinfo.value.code == "sku_play_refresh_plan_required"
    assert _job_count(wconn) == 0


def test_dispatch_refuses_when_the_confirmed_number_does_not_match(wconn):
    """确认框写 3 条、实际要派 30 条——这道闸就是专门挡这个的。"""

    _seed_overview(wconn)
    wconn.commit()
    plan = _plan(wconn)
    with pytest.raises(sku_play_refresh_dispatch.SkuPlayRefreshError) as excinfo:
        _dispatch(wconn, plan, expected_count=30)
    assert excinfo.value.code == "sku_play_refresh_plan_drifted"
    assert excinfo.value.status_code == 409
    assert _job_count(wconn) == 0


def test_dispatch_only_enqueues_the_planned_rows_even_when_more_are_asked(wconn, monkeypatch):
    """超上限被拦:候选 2 条、单次上限 1 条 → 真正派出去的只有 1 条。"""

    monkeypatch.setenv("VKPI_SKU_PLAY_REFRESH_PER_CLICK", "1")
    _seed_overview(wconn)
    _seed_link(wconn, 103, "SKU-A")
    wconn.commit()

    plan = _plan(wconn)
    result = _dispatch(wconn, plan)
    assert result["status"] == "dispatched"
    assert result["counts"] == {"planned": 1, "queued": 1, "already_queued": 0, "failed": 0}
    assert result["provider_calls_performed"] is False
    assert _job_count(wconn) == 1
    payload = json.loads(wconn.execute("SELECT payload FROM apify_jobs").fetchone()[0])
    assert payload["source"] == sku_play_refresh_plan.SKU_PLAY_REFRESH_SOURCE
    assert payload["evidence_id"] == plan["planned"][0]["evidence_id"]


def test_dispatch_when_the_cap_leaves_nothing_says_so_instead_of_pretending(wconn, monkeypatch):
    monkeypatch.setenv("VKPI_SKU_PLAY_REFRESH_DAILY_MAX", "1")
    _seed_overview(wconn)
    _seed_lane_job(wconn, 901, at=NOW - timedelta(hours=1))
    wconn.commit()

    plan = _plan(wconn)
    result = _dispatch(wconn, plan)
    assert result["status"] == "nothing_to_fetch"
    assert result["counts"]["queued"] == 0
    assert result["plan"]["skipped_counts"]["daily_cap"] == 1
    assert _job_count(wconn) == 1  # 只有种子那一条,没有新派


def test_dispatch_never_bypasses_the_paid_action_fence(wconn):
    """共享进来的红人:报价里就归入只读,派活自然一条都不派(围栏不绕)。"""

    _seed_overview(wconn)
    wconn.execute("INSERT INTO vkpi_kol_pool_members (kol_pool_id, staff_id) VALUES (3, 10)")
    wconn.commit()

    plan = _plan(wconn, evidence_id=301)
    assert plan["planned_count"] == 0
    assert plan["skipped_counts"]["shared_readonly"] == 1
    assert plan["requires_confirmation"] is False  # 单条路径不弹框,但也没得派
    result = _dispatch(wconn, plan, evidence_id=301)
    assert result["status"] == "nothing_to_fetch"
    assert _job_count(wconn) == 0


def test_dispatch_reports_per_row_failures_without_inventing_success(wconn, monkeypatch):
    """单条入队失败不阻断整批,但必须如实计入 failed(带稳定原因码)。"""

    _seed_overview(wconn)
    wconn.commit()
    plan = _plan(wconn)

    def _boom(*_args, **_kwargs):
        raise video_tracking.VideoTrackingError("video_evidence_not_found", 404)

    monkeypatch.setattr(video_tracking, "queue_evidence_refresh", _boom)
    result = _dispatch(wconn, plan)
    assert result["counts"] == {"planned": 1, "queued": 0, "already_queued": 0, "failed": 1}
    assert result["failed"][0]["reason"] == "video_evidence_not_found"
    assert _job_count(wconn) == 0


class _SqlProbeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _SqlProbeConn:
    """非 sqlite 假库(刻意不给 executescript):用来看 PG 那一支真发了什么 SQL。"""

    def __init__(self):
        self.seen: list[str] = []

    def execute(self, sql, params=()):
        self.seen.append(sql)
        if "FROM vkpi_kol_video_product_links" in sql:
            return _SqlProbeCursor([
                {"evidence_id": 101, "kol_pool_id": 1, "platform": "youtube",
                 "video_title": "t", "kol_name": "Alice"},
            ])
        return _SqlProbeCursor([])


def test_plan_sql_stays_pg_compatible(monkeypatch):
    """PG 那一支:时间窗走 make_interval,占位符全 ?,禁 LIKE / 字面 %,聚合带 AS。"""

    monkeypatch.setattr(
        sku_play_refresh_plan._state,
        "writable_by_kol",
        lambda conn, ids, *, staff: {1: {"can_run_paid_actions": True, "reason": "owned_favorite"}},
    )
    conn = _SqlProbeConn()
    plan = sku_play_refresh_plan.plan_sku_play_refresh(
        conn, staff=STAFF_A_W, staff_scope_id=10, sku_code="SKU-A", now=NOW
    )
    assert plan["planned_count"] == 1

    joined = "\n".join(conn.seen)
    assert "make_interval" in joined
    assert " LIKE " not in joined.upper()
    assert "%" not in joined
    assert "MAX(fetched_at) AS last_success_at" in joined
    # 冷却读两个真源:成功快照 + 本车道自己的近期入队。
    assert "vkpi_content_metric_snapshots" in joined
    assert "payload ->> 'source'" in joined
